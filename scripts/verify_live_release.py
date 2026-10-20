"""Authenticated, bounded Cloud Run smoke checks; run in student Cloud Shell.

Example: python scripts/verify_live_release.py --output /tmp/live-release.json
Use --skip-agent for health/data checks only (not proof of the ADK runtime).
No credentials are written, no auth setup is attempted, and HTTP is not retried.
The full run makes two real broker requests and consumes model/query quota.
"""
import argparse
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
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROJECT = "qwiklabs-gcp-04-b992b9561e32"
REGION = "us-east1"
SERVICE = "seattle-food-broker"
CI_DEPLOYER = f"food-equity-deploy@{PROJECT}.iam.gserviceaccount.com"
FOODKEEPER_SHA256 = "e84caab9d4b603209e31d21d5027c41d29adfecbca7051ff7322e9fb0226c01d"
BAKERY_TEXT = "roughly 200 loaves of day-old bread and pastries from three store locations"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


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
        require(path.startswith("/api/") and not path.startswith("//"), "invalid_endpoint")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        request = Request(self.url + path, method=method,
                          data=None if payload is None else json.dumps(payload, allow_nan=False).encode(),
                          headers=headers)
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.status
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
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise VerificationError("invalid_response_json") from None
        require(isinstance(body, dict), "response_not_object")
        return receipt, body


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

    def execute(name, path, validator, payload=None):
        record = {"name": name, "endpoint": path, "passed": False}
        checks.append(record)
        started = time.monotonic()
        try:
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
    execute("city", "/api/city", check_city)
    execute("foodkeeper", "/api/foodkeeper?query=bread", check_foodkeeper)
    if not skip_agent:
        for stress in (False, True):
            offer = make_offer(stress)
            execute("cold_capacity_stress" if stress else "bakery_source_3", "/api/match",
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
              ]}
    token = None
    try:
        report["target"] = resolve_service(args.service, args.url, args.expected_revision,
                                          args.candidate_tag, args.expected_deployer)
        token = None if args.public else identity_token()
        report["checks"] = run_checks(LiveClient(report["target"]["url"], token), skip_agent=args.skip_agent,
                                     expected_commit=args.expected_commit,
                                     expected_revision=report["target"]["revision"])
        expected_checks = (3 if args.skip_agent else 5) + int(args.expected_commit is not None)
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
