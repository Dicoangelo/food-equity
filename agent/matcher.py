"""Deterministic Seattle matching. Eligible means a proposal awaiting confirmation.

Generated operational profiles and ZIP centroids cannot verify live capacity,
food safety or travel time. Pure scoring never creates a cloud client.
"""
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-04-b992b9561e32")
DATASET_ID = os.getenv("BIGQUERY_DATASET", "a4i_food")
SEATTLE = ZoneInfo("America/Los_Angeles")
STORAGE_ALIASES = {"cold": "cold", "refrigerated": "cold", "chilled": "cold", "frozen": "frozen", "freezer": "frozen", "ambient": "ambient", "dry": "ambient", "pantry": "ambient"}
COLD_CAPABILITIES = {"walk-in", "commercial", "domestic", "medical", "vehicle"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Clock timestamps must include a timezone.")
    return value.astimezone(timezone.utc)


def _open_on(service_days, when):
    """Parse the starter's weekly/ordinal schedules; unfamiliar text stays unknown."""
    if not isinstance(service_days, str) or not service_days.strip():
        return None
    text = service_days.lower().strip()
    if text in {"daily", "every day", "everyday", "7 days", "seven days"}:
        return True
    if text in {"weekdays", "monday-friday", "monday through friday"}:
        return when.weekday() < 5
    named = {i for i, day in enumerate(WEEKDAYS) if re.search(r"\b" + day + r"\b", text)}
    if not named:
        return None
    ordinals = {word: i for i, word in enumerate(("first", "second", "third", "fourth", "fifth"), 1)}
    occurrences = {i for word, i in ordinals.items() if re.search(r"\b" + word + r"\b", text)}
    if occurrences:
        return when.weekday() in named and (when.day - 1) // 7 + 1 in occurrences if len(named) == 1 else None
    if "-" in text or "through" in text or "except" in text:
        return None
    return when.weekday() in named


class SurplusMatcher:
    def __init__(self, project_id: str = PROJECT_ID, dataset_id: str = DATASET_ID):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", project_id):
            raise ValueError("Invalid project identifier.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dataset_id):
            raise ValueError("Invalid dataset identifier.")
        self.project_id, self.dataset_id, self.client = project_id, dataset_id, None

    def search_candidates(self, query_text: str, top_k: int = 15) -> List[Dict[str, Any]]:
        """Explicit SDK utility; production ADK retrieval should consume MCP."""
        if not isinstance(query_text, str) or not 1 <= len(query_text.strip()) <= 4000:
            raise ValueError("query_text must contain 1–4000 characters.")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise ValueError("top_k must be an integer from 1 to 20.")
        from google.cloud import bigquery
        if self.client is None:
            self.client = bigquery.Client(project=self.project_id)
        sql = f"""
        SELECT base.* EXCEPT (embedding), distance
        FROM VECTOR_SEARCH(
          TABLE `{self.project_id}.{self.dataset_id}.recipient_embeddings`, 'embedding',
          (SELECT embedded.result AS embedding
           FROM (SELECT AI.EMBED(@query_text, endpoint => 'gemini-embedding-2') AS embedded)
           WHERE embedded.status = ''),
          top_k => @top_k, distance_type => 'COSINE',
          options => '{{"use_brute_force":true}}'
        ) ORDER BY distance, base.recipient_id
        """
        config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("query_text", "STRING", query_text),
            bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
        ], labels={"application": "seattle-food-broker", "path": "explicit-sdk"})
        job = self.client.query(sql, job_config=config, location=os.getenv("BIGQUERY_LOCATION", "US"))
        return [dict(row) for row in job.result(timeout=60)]

    def evaluate_constraints(self, candidate: Dict[str, Any], required_storage: Optional[str] = "cold",
                             pallet_count: float = 1.0, hours_remaining: float = 6.0, *,
                             evaluated_at: Any = None, deadline_at: Any = None,
                             min_buffer_hours: float = 0.25) -> Dict[str, Any]:
        """The 15-minute buffer is a coordination policy, not a food-safety claim.

        Return deadline_at on subsequent requests to preserve the original clock.
        A profile pickup window is explicitly an estimate requiring confirmation.
        """
        reasons, missing, warnings, codes = [], [], [], []
        confirmations = ["Recipient must confirm current capacity and acceptance."]
        storage = STORAGE_ALIASES.get(str(required_storage).lower().strip())
        quantity, remaining, buffer = map(_number, (pallet_count, hours_remaining, min_buffer_hours))
        try:
            now = _timestamp(evaluated_at) if evaluated_at is not None else datetime.now(timezone.utc)
            deadline = _timestamp(deadline_at) if deadline_at is not None else None
        except (ValueError, TypeError, OverflowError):
            now, deadline = datetime.now(timezone.utc), None
            reasons.append("Invalid clock timestamp; include an explicit timezone.")
            codes.append("invalid_clock")
        if storage is None:
            reasons.append("Required storage must be cold/refrigerated, frozen, or ambient.")
            codes.append("invalid_storage")
        if quantity is None or quantity <= 0:
            reasons.append("Pallet quantity must be finite and positive; confirm unit conversions.")
            codes.append("invalid_quantity")
        if remaining is None or remaining <= 0:
            reasons.append("The offer has expired or its remaining time is invalid.")
            codes.append("expired_or_invalid_deadline")
        if buffer is None or buffer < 0:
            reasons.append("Coordination buffer must be finite and nonnegative.")
            codes.append("invalid_buffer")
        if remaining is not None and remaining > 0:
            try:
                request_deadline = now + timedelta(hours=remaining)
                deadline = min(deadline, request_deadline) if deadline else request_deadline
                remaining = (deadline - now).total_seconds() / 3600
            except OverflowError:
                reasons.append("Remaining time exceeds the supported clock range.")
                codes.append("invalid_clock")
            if remaining <= 0:
                reasons.append("The fixed pickup deadline has already passed.")
                codes.append("deadline_passed")
        cold = str(candidate.get("cold_storage") or "").strip().lower()
        frozen = candidate.get("frozen_storage")
        profile = str(candidate.get("profile_text") or "").lower()
        if storage == "cold":
            if cold in {"none", "dry", "ambient"}:
                reasons.append("Storage incompatible: chilled food requires refrigeration.")
                codes.append("storage_incompatible")
            elif cold not in COLD_CAPABILITIES:
                missing.append("Refrigeration capability is missing or unrecognized.")
            elif cold == "vehicle":
                missing.append("Vehicle refrigeration does not establish receiving-site cold storage.")
            if cold in COLD_CAPABILITIES and re.search(r"\bno refrigeration\b", profile):
                missing.append("Storage fields conflict with the profile's lack of refrigeration.")
        if storage == "frozen":
            if frozen is False:
                reasons.append("Storage incompatible: frozen food requires a freezer.")
                codes.append("storage_incompatible")
            elif frozen is not True:
                missing.append("Freezer capability is unconfirmed.")
            if frozen is True and re.search(r"\bno freezer\b", profile):
                missing.append("Freezer capability conflicts with the profile text.")
        if "walk-in freezer" in profile and "no freezer" in profile:
            missing.append("The profile contains contradictory freezer claims.")
        capacity = _number(candidate.get("max_pallets"))
        if capacity is None or capacity < 0:
            missing.append("Pallet capacity is missing or invalid.")
        elif quantity is not None and quantity > capacity:
            reasons.append(f"Capacity exceeded: {quantity:g} pallets offered, {capacity:g} documented.")
            codes.append("capacity_exceeded")
        supplied_eta = candidate.get("pickup_eta_hours")
        timing_basis = "coordinator_supplied_eta" if supplied_eta is not None else "profile_pickup_estimate"
        pickup = _number(supplied_eta if supplied_eta is not None else candidate.get("pickup_window_hours"))
        margin = None
        if pickup is None or pickup < 0:
            missing.append("Pickup timing is missing or invalid.")
        elif remaining is not None and buffer is not None and buffer >= 0:
            margin = remaining - pickup
            if remaining <= 0 or margin <= 0 or margin < buffer:
                reasons.append(f"Pickup misses deadline/buffer: {pickup:g}h pickup, {remaining:g}h remaining, {buffer:g}h required margin.")
                codes.append("insufficient_time_margin")
            if timing_basis == "profile_pickup_estimate":
                warnings.append("Pickup time is a profile-based estimate, not a verified ETA.")
                confirmations.append("Confirm pickup ETA, receiving hours and transport.")
            try:
                service_open = _open_on(candidate.get("service_days"), (now + timedelta(hours=pickup)).astimezone(SEATTLE))
            except OverflowError:
                service_open = None
            if service_open is False:
                reasons.append("Estimated pickup falls outside documented Seattle service days.")
                codes.append("service_day_closed")
            elif service_open is None:
                missing.append("Receiving schedule requires confirmation.")
        source = candidate.get("profile_source") or "unknown"
        if source == "planted":
            warnings.append("Invented teaching case: organization and operational profile are synthetic.")
        elif source == "generated":
            warnings.append("Operational profile is generated; organization identity comes from the starter.")
        elif source == "unknown":
            warnings.append("Profile provenance is unknown.")
        status = "rejected" if reasons else ("needs_confirmation" if missing else "eligible")
        return {"viable": status == "eligible", "status": status, "reasons": reasons + missing,
                "reason_codes": list(dict.fromkeys(codes)), "warnings": list(dict.fromkeys(warnings)),
                "requires_confirmation": True, "confirmations_required": list(dict.fromkeys(confirmations + missing)),
                "time_margin_hours": round(margin, 4) if margin is not None else None,
                "timing_basis": timing_basis, "evaluated_at": now.isoformat(),
                "deadline_at": deadline.isoformat() if deadline else None,
                "clock_basis": "fixed_deadline" if deadline_at is not None else "relative_offer",
                "min_buffer_hours": buffer, "profile_source": source}

    def score_and_rank(self, candidates: List[Dict[str, Any]], required_storage: str = "cold",
                       pallet_count: float = 1.0, hours_remaining: float = 6.0, *,
                       evaluated_at: Any = None, deadline_at: Any = None,
                       min_buffer_hours: float = 0.25) -> Dict[str, Any]:
        now = _timestamp(evaluated_at) if evaluated_at is not None else datetime.now(timezone.utc)
        viable, eliminated, pending, seen = [], [], [], set()
        for c in candidates:
            key = c.get("recipient_id")
            key = ("id", str(key)) if key is not None else ("record", c.get("name"), c.get("street"))
            if key in seen:
                continue
            seen.add(key)
            check = self.evaluate_constraints(c, required_storage, pallet_count, hours_remaining,
                evaluated_at=now, deadline_at=deadline_at, min_buffer_hours=min_buffer_hours)
            distance = _number(c.get("distance"))
            if distance is None or not 0 <= distance <= 2:
                check["viable"] = False
                if check["status"] != "rejected":
                    check["status"] = "needs_confirmation"
                check["reasons"].append("A valid cosine vector-search distance is missing.")
            semantic_score = max(0.0, 1.0 - distance) if distance is not None and 0 <= distance <= 2 else 0.0
            poverty, vehicle = _number(c.get("tract_poverty_rate")), _number(c.get("tract_no_vehicle_rate"))
            poverty = poverty if poverty is not None and 0 <= poverty <= 1 else None
            vehicle = vehicle if vehicle is not None and 0 <= vehicle <= 1 else None
            equity_score = 0.5 * (poverty or 0) + 0.5 * (vehicle or 0)
            logistics = 0.5 + (0.3 if c.get("refrigerated_transport") is True else 0) + (0.2 if c.get("loading_dock") is True else 0)
            warnings = list(check["warnings"])
            if poverty is None or vehicle is None:
                warnings.append("Neighborhood data are incomplete; missing values do not mean zero poverty.")
            info = {name: c.get(name) for name in (
                "recipient_id", "name", "street", "city", "zip_code", "archetype", "cold_storage",
                "frozen_storage", "max_pallets", "pickup_window_hours", "pickup_eta_hours", "weekly_households", "service_days",
                "profile_text", "latitude", "longitude", "tract_id", "refrigerated_transport", "loading_dock")}
            info.update({"profile_source": check["profile_source"],
                "synthetic_organization": check["profile_source"] == "planted", "operational_data_verified": False,
                "geography_precision": c.get("geography_precision") or "zip_centroid",
                "tract_poverty_pct": round(poverty * 100, 1) if poverty is not None else None,
                "tract_no_vehicle_pct": round(vehicle * 100, 1) if vehicle is not None else None,
                "semantic_distance": round(distance, 4) if distance is not None else None, "distance_type": "COSINE",
                "composite_score": round(0.4 * semantic_score + 0.35 * equity_score + 0.25 * logistics, 4),
                "constraint_status": check["status"], "warnings": warnings, "requires_confirmation": True,
                "confirmations_required": check["confirmations_required"], "time_margin_hours": check["time_margin_hours"],
                "timing_basis": check["timing_basis"], "evaluated_at": check["evaluated_at"],
                "clock_basis": check["clock_basis"],
                "deadline_at": check["deadline_at"], "reason_codes": check["reason_codes"]})
            if check["viable"]:
                viable.append(info)
            else:
                info["elimination_reasons"] = check["reasons"]
                (pending if check["status"] == "needs_confirmation" else eliminated).append(info)
        viable.sort(key=lambda x: (-x["composite_score"], x["semantic_distance"], str(x["recipient_id"])))
        eliminated.sort(key=lambda x: (x["semantic_distance"] is None, x["semantic_distance"] or 0))
        return {"winner": viable[0] if viable else None, "viable_runners_up": viable[1:3],
                "eliminated_decoys": eliminated, "pending_confirmation": pending,
                "total_candidates_evaluated": len(seen), "evaluated_at": now.isoformat(),
                "min_buffer_hours": min_buffer_hours}
