"""Bounded, read-only Seattle map data; never fetches vectors or calls a model.

The heat layer is a tract-centroid demographic proxy, not measured hunger.
Recipient locations are ZIP centroids and operating profiles need confirmation.
"""
from copy import deepcopy
from datetime import datetime, timezone
from itertools import islice
import math
import os
import re
import threading
import time

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-04-b992b9561e32")
DATASET_ID = os.getenv("BIGQUERY_DATASET", "a4i_food")
BOUNDS = {"south": 47.20, "north": 47.86, "west": -122.55, "east": -121.98}
ROW_CAPS = {"tracts": 1000, "recipients": 500}
MAXIMUM_BYTES_BILLED = 50 * 1024 * 1024


def _number(value, minimum=None, maximum=None):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        return None
    return None if maximum is not None and value > maximum else value


def _text(value, limit=160):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()[:limit] or None


def _boolean(value):
    return value if isinstance(value, bool) else None


def _point(row, latitude, longitude):
    lat = _number(row.get(latitude), BOUNDS["south"], BOUNDS["north"])
    lon = _number(row.get(longitude), BOUNDS["west"], BOUNDS["east"])
    return (lat, lon) if lat is not None and lon is not None else None


def _tract(row):
    point, tract_id = _point(row, "tract_lat", "tract_lon"), _text(row.get("geo_id"), 11)
    if point is None or tract_id is None or not re.fullmatch(r"53\d{9}", tract_id):
        return None
    poverty = _number(row.get("poverty_rate"), 0, 1)
    vehicle = _number(row.get("no_vehicle_rate"), 0, 1)
    return {
        "tract_id": tract_id, "lat": point[0], "lon": point[1],
        "population": _number(row.get("total_pop"), 0),
        "median_income": _number(row.get("median_income"), 0),
        "poverty_rate": poverty, "no_vehicle_rate": vehicle,
        "assistance_rate": _number(row.get("assistance_rate"), 0, 1),
        "need_score": round((poverty + vehicle) / 2, 6)
        if poverty is not None and vehicle is not None else None,
        "geography_precision": "tract_centroid",
    }


def _recipient(row):
    point, identifier = _point(row, "latitude", "longitude"), _text(row.get("recipient_id"))
    if point is None or identifier is None:
        return None
    source = _text(row.get("profile_source"), 40) or "unknown"
    cold = _text(row.get("cold_storage"), 40)
    cold = cold.lower() if cold else None
    result = {key: _text(row.get(key)) for key in (
        "name", "city", "zip_code", "archetype", "service_days",
    )}
    result.update({
        "recipient_id": identifier, "lat": point[0], "lon": point[1],
        "cold_storage": cold, "frozen_storage": _boolean(row.get("frozen_storage")),
        "loading_dock": _boolean(row.get("loading_dock")),
        "refrigerated_transport": _boolean(row.get("refrigerated_transport")),
        "max_pallets": _number(row.get("max_pallets"), 0),
        "pickup_window_hours": _number(row.get("pickup_window_hours"), 0),
        "weekly_households": _number(row.get("weekly_households"), 0),
        "profile_source": source, "synthetic_organization": source == "planted",
        "geography_precision": "zip_centroid", "operational_data_verified": False,
        "requires_confirmation": True,
        "cold_storage_confirmation_required": cold in (None, "vehicle"),
    })
    return result


class CityDataService:
    """Synchronous service. Reuse one instance; API may call it in a worker thread."""

    def __init__(self, project_id=PROJECT_ID, dataset_id=DATASET_ID, *, client=None,
                 cache_ttl_seconds=300):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", project_id):
            raise ValueError("Invalid project identifier.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dataset_id):
            raise ValueError("Invalid dataset identifier.")
        ttl = _number(cache_ttl_seconds, 0, 3600)
        if ttl is None:
            raise ValueError("Cache TTL must be from 0 to 3600 seconds.")
        self.project_id, self.dataset_id, self.client = project_id, dataset_id, client
        self.cache_ttl_seconds, self._cached, self._expires = ttl, None, 0
        self._lock = threading.Lock()

    def _sql(self, layer):
        prefix = f"{self.project_id}.{self.dataset_id}"
        if layer == "tracts":
            return f"""
            SELECT geo_id, tract_lat, tract_lon, total_pop, median_income,
                   poverty_rate, no_vehicle_rate, assistance_rate,
                   COUNT(*) OVER() AS source_row_count
            FROM `{prefix}.tract_demographics`
            WHERE STARTS_WITH(geo_id, '53')
              AND tract_lat BETWEEN 47.20 AND 47.86
              AND tract_lon BETWEEN -122.55 AND -121.98
            ORDER BY geo_id LIMIT @row_limit
            """
        if layer == "recipients":
            return f"""
            SELECT recipient_id, name, city, zip_code, archetype, service_days,
                   latitude, longitude, cold_storage, frozen_storage, loading_dock,
                   refrigerated_transport, max_pallets, pickup_window_hours,
                   weekly_households, profile_source,
                   COUNT(*) OVER() AS source_row_count
            FROM `{prefix}.recipient_embeddings`
            WHERE latitude BETWEEN 47.20 AND 47.86
              AND longitude BETWEEN -122.55 AND -121.98
            ORDER BY recipient_id LIMIT @row_limit
            """
        raise ValueError("Unknown city layer.")

    def _query(self, layer):
        from google.cloud import bigquery
        if self.client is None:
            self.client = bigquery.Client(project=self.project_id)
        config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter(
                "row_limit", "INT64", ROW_CAPS[layer] + 1)],
            maximum_bytes_billed=MAXIMUM_BYTES_BILLED, use_query_cache=True,
            labels={"application": "seattle-food-broker", "path": "city-map"},
        )
        job = self.client.query(self._sql(layer), job_config=config,
                                location=os.getenv("BIGQUERY_LOCATION", "US"))
        return [dict(row) for row in islice(job.result(timeout=20), ROW_CAPS[layer] + 1)]

    def get_city_data(self):
        with self._lock:
            if self._cached is not None and time.monotonic() < self._expires:
                result = deepcopy(self._cached)
                result["cache"]["hit"] = True
                return result
            result = self._load()
            self._cached = deepcopy(result)
            # Failed requests may be retried soon without hammering cloud access.
            ttl = self.cache_ttl_seconds if result["status"] == "ready" else min(15, self.cache_ttl_seconds)
            self._expires = time.monotonic() + ttl
            return result

    def _load(self):
        result = {
            "city": "Seattle", "status": "unavailable",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "viewport": dict(BOUNDS), "tracts": [], "recipients": [],
            "limits": {}, "errors": [],
            "cache": {"hit": False, "ttl_seconds": self.cache_ttl_seconds},
            "provenance": {
                "project_id": self.project_id, "dataset_id": self.dataset_id,
                "location": os.getenv("BIGQUERY_LOCATION", "US"),
                "tracts": "Existing ACS-derived tract_demographics starter snapshot.",
                "recipients": "Existing recipient_embeddings; selected capability fields only.",
                "rate_units": "fraction_0_to_1",
                "need_score": "0.5 * poverty_rate + 0.5 * no_vehicle_rate; null if either is missing.",
                "geography": "Seattle metro starter bounding box, not a city boundary.",
                "observation_date": None,
            },
            "routing": {
                "status": "unavailable", "travel_time_verified": False,
                "reason": "No verified road-routing response. ZIP centroids are not facility entrances.",
            },
            "warnings": [
                "Heat intensity is a tract-centroid demographic need proxy, not measured hunger or household demand.",
                "Missing rates are unknown, not zero; ACS observation dates are not supplied by these tables.",
                "Recipient markers are ZIP centroids; generated/planted operations and current capacity require confirmation.",
                "Vehicle refrigeration does not establish receiving-site cold storage.",
            ],
        }
        for layer, convert in (("tracts", _tract), ("recipients", _recipient)):
            cap = ROW_CAPS[layer]
            limits = result["limits"][layer] = {
                "row_cap": cap, "returned": 0, "matching_source_rows": None,
                "truncated": False, "skipped_invalid_or_duplicate": 0,
            }
            try:
                rows = self._query(layer)
            except Exception:
                # Never return raw SDK exceptions: they can include account/SQL details.
                result["errors"].append({
                    "layer": layer, "code": "data_unavailable",
                    "message": "City layer could not be read; verify service identity, dataset access and query budget.",
                })
                continue
            total = _number(rows[0].get("source_row_count"), 0) if rows else 0
            limits["matching_source_rows"] = int(total) if total is not None else None
            limits["truncated"] = len(rows) > cap or (total is not None and total > cap)
            seen = set()
            for row in rows[:cap]:
                item = convert(row)
                identifier = item.get("tract_id" if layer == "tracts" else "recipient_id") if item else None
                if item is None or identifier in seen:
                    limits["skipped_invalid_or_duplicate"] += 1
                    continue
                seen.add(identifier)
                result[layer].append(item)
            limits["returned"] = len(result[layer])
            if not result[layer]:
                result["errors"].append({
                    "layer": layer, "code": "empty_layer",
                    "message": "No valid Seattle points were returned for this layer.",
                })
            if limits["truncated"]:
                result["warnings"].append(f"{layer.capitalize()} layer reached its row cap; map coverage is incomplete.")
        layers_ready = sum(bool(result[layer]) for layer in ("tracts", "recipients"))
        result["status"] = "ready" if layers_ready == 2 else ("partial" if layers_ready else "unavailable")
        if any(item["need_score"] is None for item in result["tracts"]):
            result["warnings"].append("Some tract points have incomplete rates and are omitted from heat intensity.")
        return result
