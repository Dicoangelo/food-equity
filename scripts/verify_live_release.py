"""Authenticated, bounded Cloud Run smoke checks; run in student Cloud Shell.

Example: python scripts/verify_live_release.py --output /tmp/live-release.json
Use --skip-agent for health/data checks only (not proof of the ADK runtime).
No credentials are written, no auth setup is attempted, and HTTP is not retried.
The full run makes two real broker requests and one exact-ID evidence read,
consuming model/query quota. The evidence read does not invoke a model.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import httpx

PROJECT = "qwiklabs-gcp-04-b992b9561e32"
REGION = "us-east1"
SERVICE = "seattle-food-broker"
CI_DEPLOYER = f"food-equity-deploy@{PROJECT}.iam.gserviceaccount.com"
FOODKEEPER_SHA256 = "e84caab9d4b603209e31d21d5027c41d29adfecbca7051ff7322e9fb0226c01d"
BAKERY_TEXT = "roughly 200 loaves of day-old bread and pastries from three store locations"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_STREAM_EVENTS = 256
EVIDENCE_TIMEOUT_SECONDS = 35  # Server query budget 25s + cleanup 2s + network margin.
MAX_EVIDENCE_BYTES = 131_072
STREAM_PATH = "/api/match/stream"
WORKFLOW_PATH = "/ui/offer-workflow.js"
WORKFLOW_SOURCE = Path(__file__).resolve().parents[1] / "agent/ui/offer-workflow.js"
PROGRESS_STAGES = (
    "accepted", "connecting", "retrieving", "checking_constraints", "preparing_brief", "finalizing",
)
COUNT_FIELDS = {"candidate_count", "retrieved_count", "eligible_count", "rejected_count", "pending_count"}


class VerificationError(Exception):
    """Only fixed public error codes, never provider bodies or credentials."""


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.utcoffset() is not None, "timezone_missing")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise VerificationError("invalid_timestamp") from None


def recipient_evidence_path(identifier):
    if type(identifier) is int:
        identifier = str(identifier)
    require(isinstance(identifier, str) and bool(re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", identifier)), "invalid_evidence_recipient_id")
    return f"/api/recipients/{quote(identifier, safe='')}/evidence"


def gcloud(*args):
    try:
        result = subprocess.run(["gcloud", *args, "--quiet"], capture_output=True,
                                text=True, timeout=45)
        require(result.returncode == 0, "gcloud_read_failed")
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        raise VerificationError("gcloud_unavailable_or_timeout") from None


def resolve_service(service=SERVICE, explicit_url=None, expected_revision=None,
                    candidate_tag=None, expected_deployer=None):
    # Identity values are read only into memory and never included in the report.
    account = gcloud("auth", "list", "--filter=status:ACTIVE", "--format=value(account)")
    if expected_deployer is not None:
        require(expected_deployer == CI_DEPLOYER and account == CI_DEPLOYER, "wrong_ci_identity")
    else:
        require(bool(re.fullmatch(r"student-[^@\s]+@qwiklabs\.net", account)), "wrong_active_identity")
    require(gcloud("config", "get-value", "project") == PROJECT, "wrong_active_project")
    require(bool(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", service)), "invalid_service")
    try:
        metadata = json.loads(gcloud(
            "run", "services", "describe", service, "--project", PROJECT, "--region", REGION,
            "--format=json(status.url,status.latestReadyRevisionName,status.latestCreatedRevisionName,status.traffic)",
        ))["status"]
    except (ValueError, KeyError, TypeError):
        raise VerificationError("invalid_service_metadata") from None
    traffic = metadata.get("traffic", [])
    if candidate_tag is not None:
        require(bool(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", candidate_tag))
                and bool(expected_revision), "candidate_requires_tag_and_revision")
        targets = [item for item in traffic if item.get("tag") == candidate_tag]
        require(len(targets) == 1 and targets[0].get("revisionName") == expected_revision,
                "candidate_revision_mismatch")
        url = targets[0].get("url", "")
        revision = expected_revision
        try:
            ready = json.loads(gcloud(
                "run", "revisions", "describe", revision, "--project", PROJECT, "--region", REGION,
                "--format=json(status.conditions)"))["status"]["conditions"]
            require(any(row.get("type") == "Ready" and row.get("status") in ("True", True)
                        for row in ready), "candidate_revision_not_ready")
        except (ValueError, KeyError, TypeError):
            raise VerificationError("invalid_revision_metadata") from None
    else:
        url = metadata.get("url", "")
        revision = metadata.get("latestReadyRevisionName")
        require(bool(revision) and revision == metadata.get("latestCreatedRevisionName"),
                "latest_revision_not_ready")
        require(expected_revision is None or expected_revision == revision, "revision_mismatch")
        require(sum(t.get("percent", 0) for t in traffic if t.get("revisionName") == revision) == 100
                and not any(t.get("percent", 0) > 0 and t.get("revisionName") != revision for t in traffic),
                "traffic_not_all_latest_revision")
    parts = urlsplit(url)
    require(parts.scheme == "https" and bool(parts.hostname)
            and parts.hostname.endswith(".run.app") and not parts.username
            and not parts.password and not parts.port and parts.path in ("", "/")
            and not parts.query and not parts.fragment, "untrusted_service_url")
    url = url.rstrip("/")
    require(explicit_url is None or explicit_url.rstrip("/") == url, "service_url_mismatch")
    return {"project": PROJECT, "region": REGION, "service": service, "url": url,
            "revision": revision, "candidate_tag": candidate_tag,
            "student_identity_verified": expected_deployer is None,
            "ci_identity_verified": expected_deployer == CI_DEPLOYER}


def identity_token():
    token = gcloud("auth", "print-identity-token")
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token)),
            "invalid_identity_token")
    return token


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LiveClient:
    def __init__(self, url, token, timeout=210):
        self.url, self._token, self.timeout = url, token, timeout
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, payload=None):
        require(path.startswith("/api/") or path == WORKFLOW_PATH, "invalid_endpoint")
        asset = path == WORKFLOW_PATH
        headers = {"Content-Type": "application/json",
                   "Accept": "text/javascript" if asset else "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        request = Request(self.url + path, method=method,
                          data=None if payload is None else json.dumps(payload, allow_nan=False).encode(),
                          headers=headers)
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.status
                if asset and status == 200:
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    require(content_type in {"text/javascript", "application/javascript"}, "workflow_content_type")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            # Do not persist error bodies, Location headers, or provider messages.
            status, raw = exc.code, b""
            exc.close()
        except (URLError, OSError, TimeoutError):
            raise VerificationError("http_unavailable_or_timeout") from None
        receipt = {"http_status": status, "latency_ms": round((time.monotonic() - started) * 1000),
                   "response_bytes": len(raw), "response_sha256": hashlib.sha256(raw).hexdigest()}
        if status != 200:
            return receipt, None
        require(len(raw) <= MAX_RESPONSE_BYTES, "response_too_large")
        if asset:
            return receipt, raw
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise VerificationError("invalid_response_json") from None
        require(isinstance(body, dict), "response_not_object")
        return receipt, body

    def request_recipient_evidence(self, identifier):
        """One bounded GET, with no retries, model calls or match payload."""
        return asyncio.run(self._request_recipient_evidence(identifier))

    async def _request_recipient_evidence(self, identifier):
        path = recipient_evidence_path(identifier)
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        started = time.monotonic()
        raw = bytearray()
        try:
            async with asyncio.timeout(min(self.timeout, EVIDENCE_TIMEOUT_SECONDS)):
                async with httpx.AsyncClient(
                    timeout=min(self.timeout, EVIDENCE_TIMEOUT_SECONDS), follow_redirects=False,
                ) as client:
                    async with client.stream("GET", self.url + path, headers=headers) as response:
                        status = response.status_code
                        receipt = {"http_status": status}
                        if status != 200:
                            # Error bodies may contain provider details; never consume them.
                            receipt["latency_ms"] = round((time.monotonic() - started) * 1000)
                            return receipt, None
                        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        require(content_type == "application/json", "evidence_content_type")
                        directives = {part.strip().lower() for part in
                                      response.headers.get("Cache-Control", "").split(",")}
                        require("no-store" in directives, "evidence_cache_contract")
                        async for chunk in response.aiter_bytes():
                            require(len(raw) + len(chunk) <= MAX_EVIDENCE_BYTES, "evidence_response_too_large")
                            raw.extend(chunk)
        except (TimeoutError, httpx.TimeoutException):
            raise VerificationError("evidence_http_timeout") from None
        except httpx.HTTPError:
            raise VerificationError("http_unavailable_or_timeout") from None
        try:
            body = json.loads(raw)
            json.dumps(body, allow_nan=False)
        except (ValueError, UnicodeDecodeError):
            raise VerificationError("invalid_evidence_json") from None
        require(isinstance(body, dict), "evidence_response_not_object")
        receipt.update(latency_ms=round((time.monotonic() - started) * 1000),
                       response_bytes=len(raw), response_sha256=hashlib.sha256(raw).hexdigest(),
                       cache_control_no_store=True)
        return receipt, body

    def request_stream(self, payload):
        """Read NDJSON incrementally with a total deadline, including final EOF.

        HTTPX is already used by the installed Google SDKs. Its async transport
        lets the total deadline cancel an in-flight read, not just check the
        clock between chunks. No extra broker request or retry is made.
        """
        return asyncio.run(self._request_stream(payload))

    async def _request_stream(self, payload):
        headers = {"Content-Type": "application/json", "Accept": "application/x-ndjson"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        started = time.monotonic()
        digest = hashlib.sha256()
        size = events = 0
        pending = b""
        run_id = None
        stages = []
        result = None
        first_event_ms = None

        def read_event(raw):
            nonlocal events, run_id, result, first_event_ms
            require(events < MAX_STREAM_EVENTS, "stream_too_many_events")
            try:
                event = json.loads(raw)
                json.dumps(event, allow_nan=False)
            except (ValueError, UnicodeDecodeError):
                raise VerificationError("stream_invalid_json") from None
            require(isinstance(event, dict), "stream_event_not_object")
            require(result is None, "stream_after_terminal")
            observed_id = event.get("run_id")
            require(isinstance(observed_id, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", observed_id)),
                    "stream_invalid_run_id")
            if run_id is None:
                run_id = observed_id
            require(observed_id == run_id, "stream_run_id_changed")
            require(type(event.get("sequence")) is int and event["sequence"] == events + 1,
                    "stream_sequence")
            kind = event.get("type")
            if events == 0:
                require(kind == "progress" and event.get("stage") == "accepted", "stream_accepted_first")
                first_event_ms = round((time.monotonic() - started) * 1000)
            events += 1
            common = {"type", "run_id", "sequence"}
            if kind == "progress":
                stage = event.get("stage")
                require(stage in PROGRESS_STAGES and set(event) <= common | {"stage"} | COUNT_FIELDS,
                        "stream_progress_contract")
                require(not stages or PROGRESS_STAGES.index(stage) > PROGRESS_STAGES.index(stages[-1]),
                        "stream_stage_order")
                require(all(type(event[key]) is int and 0 <= event[key] <= 1_000_000
                            for key in COUNT_FIELDS & set(event)), "stream_progress_counts")
                stages.append(stage)
            elif kind == "heartbeat":
                require(set(event) == common, "stream_heartbeat_contract")
            elif kind == "result":
                require(set(event) == common | {"data"} and isinstance(event.get("data"), dict),
                        "stream_result_contract")
                result = event["data"]
            elif kind == "error":
                raise VerificationError("stream_terminal_error")
            else:
                raise VerificationError("stream_event_type")

        try:
            async with asyncio.timeout(min(self.timeout, 210)):
                async with httpx.AsyncClient(timeout=min(self.timeout, 30), follow_redirects=False) as client:
                    async with client.stream("POST", self.url + STREAM_PATH, json=payload,
                                             headers=headers) as response:
                        status = response.status_code
                        if status != 200:
                            return {"http_status": status, "latency_ms": round(
                                (time.monotonic() - started) * 1000)}, None
                        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        require(content_type == "application/x-ndjson", "stream_content_type")
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            require(size <= MAX_RESPONSE_BYTES, "response_too_large")
                            digest.update(chunk)
                            pending += chunk
                            while b"\n" in pending:
                                raw, pending = pending.split(b"\n", 1)
                                read_event(raw)
                        require(not pending, "stream_incomplete_line")
                        require(result is not None, "stream_missing_result")
                        require(all(stage in stages for stage in (
                            "accepted", "connecting", "retrieving", "checking_constraints", "finalizing",
                        )), "stream_missing_progress")
        except (TimeoutError, httpx.TimeoutException):
            raise VerificationError("stream_timeout") from None
        except httpx.HTTPError:
            raise VerificationError("http_unavailable_or_timeout") from None
        return {
            "http_status": status, "latency_ms": round((time.monotonic() - started) * 1000),
            "response_bytes": size, "response_sha256": digest.hexdigest(),
            "stream_events": events, "progress_stages": stages,
            "first_event_latency_ms": first_event_ms, "terminal_result_received": True,
        }, result


def check_workflow_asset(body):
    require(isinstance(body, bytes), "workflow_asset_missing")
    try:
        expected = WORKFLOW_SOURCE.read_bytes()
    except OSError:
        raise VerificationError("workflow_source_unavailable") from None
    require(bool(expected) and len(expected) <= MAX_RESPONSE_BYTES and body == expected, "workflow_asset_mismatch")
    return {"source_sha256": hashlib.sha256(expected).hexdigest(), "bytes": len(expected)}


def check_health(body):
    require(body == {"status": "ok", "cloud_dependencies": "not_checked"}, "health_contract")
    return {"status": "ok", "cloud_dependencies": "not_checked"}


def check_city(body):
    require(body.get("status") == "ready" and body.get("city") == "Seattle", "city_not_ready")
    provenance = body.get("provenance", {})
    require(provenance.get("project_id") == PROJECT and provenance.get("dataset_id") == "a4i_food"
            and provenance.get("rate_units") == "fraction_0_to_1", "city_provenance")
    for layer, cap, identifier in (("tracts", 1000, "tract_id"), ("recipients", 500, "recipient_id")):
        rows = body.get(layer)
        require(isinstance(rows, list) and 0 < len(rows) <= cap, "city_layer_empty_or_oversized")
        require(len({str(row.get(identifier)) for row in rows}) == len(rows), "city_duplicate_ids")
        for row in rows:
            require(number(row.get("lat")) and -90 <= row["lat"] <= 90
                    and number(row.get("lon")) and -180 <= row["lon"] <= 180, "city_coordinates")
            require("embedding" not in row, "city_exposes_vectors")
            if layer == "tracts":
                for field in ("poverty_rate", "no_vehicle_rate", "assistance_rate", "need_score"):
                    value = row.get(field)
                    require(value is None or (number(value) and 0 <= value <= 1), "city_rate_units")
            else:
                require(row.get("operational_data_verified") is False
                        and row.get("requires_confirmation") is True
                        and bool(row.get("profile_source")), "city_profile_provenance")
    return {"status": body["status"], "tracts": len(body["tracts"]), "recipients": len(body["recipients"]),
            "profile_sources": dict(Counter(r["profile_source"] for r in body["recipients"])),
            "rate_units": provenance["rate_units"],
            "routing_verified": body.get("routing", {}).get("travel_time_verified") is True,
            "scope": "City endpoint viewport; not proof of full metro coverage"}


def check_recipient_evidence(body, city_recipient, *, started_at, finished_at=None):
    """Verify dataset provenance without promoting it to operational evidence."""
    require(isinstance(body, dict) and body.get("status") == "ready", "recipient_evidence_not_ready")
    identifier = str(city_recipient["recipient_id"])
    recipient_evidence_path(identifier)
    profile, source = body.get("profile"), body.get("source")
    require(isinstance(profile, dict) and body.get("recipient_id") == identifier
            and profile.get("recipient_id") == identifier, "recipient_evidence_id_mismatch")
    require(isinstance(source, dict)
            and source.get("kind") == "bigquery_recipient_snapshot"
            and source.get("table") == "a4i_food.recipient_embeddings"
            and source.get("transport") == "mcp"
            and source.get("tool") == "get_recipient_evidence", "recipient_evidence_source")
    require(all(obj.get("requires_confirmation") is True
                and obj.get("operational_data_verified") is False for obj in (body, profile)),
            "recipient_evidence_operational_claim")
    provenance = profile.get("profile_source")
    require(provenance in {"generated", "planted", "unknown"}
            and provenance == city_recipient.get("profile_source")
            and profile.get("synthetic_organization") is (provenance == "planted"),
            "recipient_evidence_provenance")
    require("observation_date" in body and body["observation_date"] is None
            and all(key in source and source[key] is None
                    for key in ("source_observed_at", "operational_confirmed_at")),
            "recipient_evidence_observation_claim")
    retrieved = timestamp(body.get("retrieved_at"))
    require(timestamp(body.get("fetched_at")) == retrieved, "recipient_evidence_timestamp_mismatch")
    finished_at = finished_at or datetime.now(timezone.utc)
    # The two clocks are on different hosts; allow five seconds of skew, not stale cached data.
    require(started_at - timedelta(seconds=5) <= retrieved <= finished_at + timedelta(seconds=5),
            "recipient_evidence_stale_timestamp")
    require(not ({"winner", "runners_up", "donor", "deadline_at", "match_brief", "execution_trace"} & body.keys())
            and not ({"embedding", "embeddings", "email", "phone", "contact", "street_address",
                      "score", "composite_score", "constraint_status", "deadline_at"} & profile.keys()),
            "recipient_evidence_unexpected_match_or_private_fields")
    require(isinstance(profile.get("name"), str) and bool(profile["name"].strip())
            and body.get("code") is None
            and body.get("unknown_fields") == sorted(k for k, value in profile.items() if value is None),
            "recipient_evidence_profile_contract")
    return {
        "status": "ready", "recipient_id": identifier, "profile_source": provenance,
        "retrieved_at": retrieved.isoformat(), "source": {
            key: source[key] for key in ("kind", "table", "transport", "tool",
                                        "source_observed_at", "operational_confirmed_at")},
        "requires_confirmation": True, "operational_data_verified": False,
        "observation_date": None, "match_result_fields_absent": True,
    }


def check_foodkeeper(body):
    products = body.get("products")
    require(body.get("status") == "options" and isinstance(products, list) and 0 < len(products) <= 5,
            "foodkeeper_lookup")
    require(body.get("source", {}).get("sha256") == FOODKEEPER_SHA256, "foodkeeper_source_changed")
    require(body.get("selection_required") is True and body.get("selected_product_id") is None
            and body.get("remaining_freshness_hours") is None
            and body.get("donor_deadline_modified") is False, "foodkeeper_advisory_contract")
    require(bool(body.get("advisory_notice")) and bool(body.get("unknowns_required")), "foodkeeper_caveats")
    for product in products:
        require(product.get("remaining_freshness_hours") is None
                and product.get("applicability") == "unconfirmed_product_state_and_history",
                "foodkeeper_freshness_inferred")
    return {"status": "options", "source_sha256": FOODKEEPER_SHA256,
            "product_ids": [p.get("product_id") for p in products],
            "selection_required": True, "donor_deadline_modified": False}


def make_offer(stress=False, now=None):
    now = now or datetime.now(timezone.utc)
    hours = 1.0 if stress else 24.0
    return {
        "posting_text": ("Synthetic capacity stress: 10000 pallets of chilled milk and yogurt; "
                         "refrigerated storage required, pickup within one hour."
                         if stress else BAKERY_TEXT),
        "required_storage": "cold" if stress else "ambient",
        "pallet_count": 10000.0 if stress else 1.0, "hours_remaining": hours,
        "donor_name": "Synthetic capacity stress test" if stress else "Capitol Hill Bakery Hub",
        "donor_lat": 47.6180, "donor_lon": -122.3160,
        "deadline_at": (now + timedelta(hours=hours)).isoformat(),
    }


def check_broker(body, offer, stress=False):
    require(body.get("status") in {"success", "no_viable_match"}, "agent_incomplete_or_fallback")
    require(body.get("requires_confirmation") is True and bool(body.get("provenance_warning")),
            "confirmation_or_provenance_missing")
    donor = body.get("donor", {})
    deadline = timestamp(offer["deadline_at"])
    require(timestamp(donor.get("deadline_at")) == deadline, "deadline_changed")
    evaluated = timestamp(body.get("evaluated_at"))
    require(number(donor.get("hours_remaining"))
            and abs(donor["hours_remaining"] - max(0, (deadline - evaluated).total_seconds() / 3600)) < 1 / 3600,
            "remaining_clock_inconsistent")
    require(all(donor.get(k) == offer[k] for k in ("posting_text", "required_storage", "pallet_count")),
            "donor_offer_changed")
    trace = body.get("execution_trace", [])
    require(isinstance(trace, list) and all(isinstance(t, dict) for t in trace), "trace_invalid")
    stages = [t.get("stage") for t in trace]
    required = ("mcp_discovery", "mcp_call", "mcp_result", "deterministic_constraints",
                "adk_function_call", "adk_function_result", "adk_complete", "dispatch_clock_recheck")
    require(all(stage in stages for stage in required) and "execution_error" not in stages, "trace_incomplete")
    require(all(any(t.get("stage") == stage and t.get("name") == "broker_match" for t in trace)
                for stage in ("adk_function_call", "adk_function_result")), "adk_function_mismatch")
    require(stages.index("mcp_call") < stages.index("mcp_result")
            < stages.index("deterministic_constraints") < stages.index("dispatch_clock_recheck"), "trace_order")
    calls = [t for t in trace if t.get("stage") == "mcp_call"]
    results = [t for t in trace if t.get("stage") == "mcp_result"]
    expected_hash = hashlib.sha256(offer["posting_text"].encode()).hexdigest()
    require(len(calls) == len(results) == 1 and calls[0].get("tool") == "vector_search_recipients"
            and calls[0].get("query_sha256") == expected_hash, "retrieval_query_mismatch")
    ids = results[0].get("recipient_ids", [])
    require(isinstance(ids, list) and 0 < len(ids) <= 12
            and results[0].get("candidate_count") == len(ids)
            and results[0].get("tool") == "vector_search_recipients", "retrieval_candidates_missing")
    ids = {str(value) for value in ids}
    winner = body.get("winner")
    runners = body.get("runners_up", [])
    rejected = body.get("eliminated_decoys", [])
    pending = body.get("pending_confirmation", [])
    require(all(isinstance(rows, list) for rows in (runners, rejected, pending)), "ranking_invalid")
    proposed = ([winner] if winner is not None else []) + runners
    require(len(runners) <= 2 and (winner is not None or not runners), "ranking_structure")
    require(len({str(row.get("recipient_id")) for row in proposed}) == len(proposed), "duplicate_proposal")
    require(all(row.get("constraint_status") == "rejected" for row in rejected)
            and all(row.get("constraint_status") == "needs_confirmation" for row in pending),
            "ranking_status_conflict")
    for row in proposed + rejected + pending:
        require(isinstance(row, dict) and str(row.get("recipient_id")) in ids, "ungrounded_recipient")
        require(row.get("requires_confirmation") is True
                and row.get("operational_data_verified") is False
                and bool(row.get("profile_source")), "candidate_provenance")
        require(timestamp(row.get("deadline_at")) == deadline
                and row.get("clock_basis") == "fixed_deadline", "candidate_deadline_changed")
    for row in proposed:
        require(row.get("constraint_status") == "eligible" and number(row.get("max_pallets"))
                and row["max_pallets"] >= offer["pallet_count"]
                and number(row.get("time_margin_hours")) and row["time_margin_hours"] > 0,
                "ineligible_proposal")
    if winner is None:
        require(body["status"] == "no_viable_match" and body.get("generation_mode") == "constraints"
                and not body.get("match_brief"), "no_match_claim")
    else:
        require(body["status"] == "success" and body.get("generation_mode") == "adk_gemini"
                and body.get("brief_generation") == "deterministic_facts"
                and body.get("outreach_mode") == "gemini_plan", "brief_generation_contract")
        brief = body.get("match_brief", {})
        require(isinstance(brief, dict) and all(
            isinstance(brief.get(field), str) and donor["deadline_at"] in brief[field]
            for field in ("clock_action_plan", "drafted_sms_outreach", "drafted_email_outreach")
        ), "brief_deadline_missing")
        require("brief_render" in stages and any(t.get("stage") == "brief_render"
                and t.get("facts_source") == "final_deterministic_ranking" for t in trace),
                "brief_not_grounded")
    reason_codes = sorted({code for row in rejected + pending for code in row.get("reason_codes", [])})
    if stress:
        require(winner is None and not runners and "capacity_exceeded" in reason_codes,
                "capacity_stress_not_blocked")
    # Persist bounded evidence only; not provider messages, model prose or full profiles.
    return {"status": body["status"], "generation_mode": body.get("generation_mode"),
            "query_sha256": expected_hash, "candidate_count": len(ids),
            "trace_stages": stages, "deadline_at": donor["deadline_at"],
            "evaluated_at": body["evaluated_at"], "remaining_hours": donor["hours_remaining"],
            "proposed_count": len(proposed), "rejected_count": len(rejected), "pending_count": len(pending),
            "proposals": [{"recipient_id": row["recipient_id"], "profile_source": row["profile_source"],
                           "time_margin_hours": row["time_margin_hours"]} for row in proposed],
            "reason_codes": reason_codes, "requires_confirmation": True}


def run_checks(client, *, skip_agent=False, expected_commit=None, expected_revision=None):
    checks = []

    def execute(name, path, validator, payload=None, fetch=None):
        record = {"name": name, "endpoint": path, "passed": False}
        checks.append(record)
        started = time.monotonic()
        try:
            if fetch is not None:
                receipt, body = fetch()
            elif path == STREAM_PATH:
                receipt, body = client.request_stream(payload)
            else:
                receipt, body = client.request("GET" if payload is None else "POST", path, payload)
            record.update(receipt)
            require(receipt["http_status"] == 200, "unexpected_http_status")
            if isinstance(body, dict):
                known_statuses = {"ok", "ready", "partial", "unavailable", "options", "no_match",
                                  "blocked", "draft", "success", "no_viable_match"}
                record["response_status"] = body.get("status") if body.get("status") in known_statuses else "unknown"
                if payload is not None:
                    # Retain observed counts/trace even when strict validation fails.
                    # These observations do not turn into verified matches.
                    rows = body.get("runners_up")
                    record["observed_proposed_count"] = int(isinstance(body.get("winner"), dict)) + (
                        len(rows) if isinstance(rows, list) else 0)
                    trace = body.get("execution_trace")
                    record["trace_stages"] = [
                        row["stage"] for row in (trace if isinstance(trace, list) else [])[:40]
                        if isinstance(row, dict) and isinstance(row.get("stage"), str)
                        and re.fullmatch(r"[a-z_]{1,50}", row["stage"])
                    ]
            record["evidence"] = validator(body)
            record["passed"] = True
        except VerificationError as exc:
            record["error_code"] = str(exc)
        except Exception:
            record["error_code"] = "unexpected_contract_shape"
        record.setdefault("latency_ms", round((time.monotonic() - started) * 1000))
        return record["passed"]

    if expected_commit is not None:
        def check_version(body):
            require(isinstance(body, dict) and body.get("commit_sha") == expected_commit
                    and body.get("revision") == expected_revision
                    and body.get("service") == SERVICE, "deployed_source_mismatch")
            return {"commit_sha": expected_commit, "revision": expected_revision, "service": SERVICE}
        if not execute("version", "/api/version", check_version):
            return checks
    if not execute("health", "/api/health", check_health):
        return checks
    if not execute("offer_workflow_asset", WORKFLOW_PATH, check_workflow_asset):
        return checks
    selected_recipient = {}

    def select_city_recipient(body):
        summary = check_city(body)
        selected_recipient.update(body["recipients"][0])
        recipient_evidence_path(selected_recipient.get("recipient_id"))
        return summary

    if not execute("city", "/api/city", select_city_recipient):
        return checks
    execute("foodkeeper", "/api/foodkeeper?query=bread", check_foodkeeper)
    evidence_started_at = datetime.now(timezone.utc)
    if not execute(
        "recipient_evidence", recipient_evidence_path(selected_recipient["recipient_id"]),
        lambda body: check_recipient_evidence(body, selected_recipient, started_at=evidence_started_at),
        fetch=lambda: client.request_recipient_evidence(selected_recipient["recipient_id"]),
    ):
        return checks
    if not skip_agent:
        for stress in (False, True):
            offer = make_offer(stress)
            execute("cold_capacity_stress" if stress else "bakery_source_3",
                    "/api/match" if stress else STREAM_PATH,
                    lambda body: check_broker(body, offer, stress), offer)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New evidence JSON file; never overwrite.")
    parser.add_argument("--service", default=SERVICE)
    parser.add_argument("--url", help="Optional exact URL; must equal the verified service URL.")
    parser.add_argument("--expected-revision", help="Optional exact latest ready revision name.")
    parser.add_argument("--candidate-tag", help="Verify this exact tagged revision before promotion; requires --expected-revision.")
    parser.add_argument("--expected-deployer", choices=[CI_DEPLOYER], help="Explicit dedicated CI identity; default remains student-only.")
    parser.add_argument("--public", action="store_true", help="Verify the already-public demo without minting an identity token; changes no IAM.")
    parser.add_argument("--expected-commit", help="Verify /api/version reports this exact 40-character Git commit and resolved revision.")
    parser.add_argument("--skip-agent", action="store_true")
    args = parser.parse_args(argv)
    require(args.expected_commit is None or bool(re.fullmatch(r"[0-9a-f]{40}", args.expected_commit)),
            "invalid_expected_commit")
    require(not args.output.exists() and args.output.parent.is_dir(), "output_must_be_new_in_existing_directory")
    report = {"report_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
              "mode": "cheap_checks" if args.skip_agent else "full_agent_smoke", "status": "failed",
              "runtime_passed": False, "proposed_count": 0, "checks": [],
              "assumptions": [
                  "Bakery uses exact starter posting 3 with a hypothetical 1-pallet capacity test, not a quantity conversion.",
                  "The 10000-pallet cold offer is a synthetic rejection stress case, not a real donation.",
                  "No-match can pass runtime verification; proposals never mean recipient acceptance or delivery.",
                  "FoodKeeper guidance never extends the donor deadline. No outreach is sent.",
                  "Bakery verifies streaming; capacity stress verifies JSON. Only two broker requests are made.",
                  "One exact-ID MCP read uses an ID from the existing city response; it adds no model or match request.",
                  "A fresh retrieval timestamp is not a source observation or operational confirmation.",
                  "Live cancellation is not probed; disconnect cleanup is covered by offline ASGI tests.",
              ]}
    token = None
    try:
        report["target"] = resolve_service(args.service, args.url, args.expected_revision,
                                          args.candidate_tag, args.expected_deployer)
        token = None if args.public else identity_token()
        report["checks"] = run_checks(LiveClient(report["target"]["url"], token), skip_agent=args.skip_agent,
                                     expected_commit=args.expected_commit,
                                     expected_revision=report["target"]["revision"])
        expected_checks = (5 if args.skip_agent else 7) + int(args.expected_commit is not None)
        passed = len(report["checks"]) == expected_checks and all(c["passed"] for c in report["checks"])
        report["status"] = "passed" if passed else "failed"
        report["runtime_passed"] = passed and not args.skip_agent
        report["proposed_count"] = sum(c.get("observed_proposed_count", 0) for c in report["checks"])
    except VerificationError as exc:
        report["error_code"] = str(exc)
    except Exception:
        report["error_code"] = "verification_setup_failed"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    rendered = json.dumps(report, indent=2, allow_nan=False)
    if token:
        rendered = rendered.replace(token, "[redacted]")
    with args.output.open("x") as stream:
        stream.write(rendered + "\n")
    print(json.dumps({"status": report["status"], "mode": report["mode"],
                      "runtime_passed": report["runtime_passed"], "proposed_count": report["proposed_count"],
                      "checks": [{"name": c["name"], "passed": c["passed"], "http_status": c.get("http_status"),
                                  "error_code": c.get("error_code")} for c in report["checks"]],
                      "error_code": report.get("error_code")}, separators=(",", ":")))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(json.dumps({"status": "failed", "error_code": str(exc)}))
        raise SystemExit(1)
