"""Audit existing postings through MCP + deterministic matching, without Gemini.

First write/review the scenario manifest, then run inside the authorized lab with
the same Toolbox server available. This creates read-only BigQuery query jobs;
it does not load data, change IAM, deploy anything, or send outreach.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.matcher import SurplusMatcher

PROJECT = "qwiklabs-gcp-04-b992b9561e32"
DATASET = "a4i_food"
MAX_TRACTS = 5000
EXPECTED = {
    1: ("RETAIL", "refrigerated", 6, 1),
    2: ("RETAIL", "refrigerated", 8, 1),
    3: ("RETAIL", "pantry", 24, 1),
    4: ("RETAIL", "refrigerated", 4, .5),
    5: ("FARM", "pantry", 72, 10),
    6: ("FARM", "refrigerated", 12, 8),
    7: ("FARM", "pantry", 120, 6),
    8: ("ESSENTIALS", "pantry", 168, 1),
    9: ("ESSENTIALS", "pantry", 720, 1),
    10: ("ESSENTIALS", "refrigerated", 8, .5),
}
STORAGE = {"refrigerated": "cold", "pantry": "ambient", "frozen": "frozen"}


def number(value, low=0, high=None):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= low and (high is None or value <= high) else None


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("Evaluation time must be an ISO timestamp with timezone.") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Evaluation time must include a timezone.")
    return result.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def manifest_template():
    return {
        "version": 1,
        "purpose": "Hypothetical capacity scenarios, not verified quantity conversions.",
        "deadline_policy": "Use each existing source hours_remaining unchanged from the fixed evaluation timestamp.",
        "cases": [
            {"posting_id": identifier, "expected_track": track,
             "expected_storage_state": storage, "expected_hours_available": hours,
             "required_storage": STORAGE[storage], "pallet_count": pallets,
             "quantity_basis": (
                 f"Hypothetical {pallets:g}-pallet capacity scenario. NOT a conversion "
                 "of the posting's crates, gallons, loaves, tons, truckload, bins or cases."
             ),
             "deadline_basis": "Existing surplus_postings.hours_remaining; no freshness inferred."}
            for identifier, (track, storage, hours, pallets) in EXPECTED.items()
        ],
    }


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or type(manifest.get("version")) is not int or manifest.get("version") != 1:
        raise ValueError("Manifest version must be 1.")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != 10:
        raise ValueError("Manifest must contain exactly ten cases.")
    by_id = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each manifest case must be an object.")
        identifier = case.get("posting_id")
        if type(identifier) is not int or identifier not in EXPECTED or identifier in by_id:
            raise ValueError("Manifest posting IDs must be unique integers 1–10.")
        pallets = number(case.get("pallet_count"), high=10000)
        if pallets is None or pallets <= 0:
            raise ValueError("Every case needs an explicit positive finite pallet scenario.")
        if case.get("required_storage") not in {"cold", "ambient", "frozen"}:
            raise ValueError("Every case needs a declared storage requirement.")
        for field in ("quantity_basis", "deadline_basis", "expected_track", "expected_storage_state"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(f"Manifest requires {field}.")
        if number(case.get("expected_hours_available")) is None:
            raise ValueError("Manifest requires expected_hours_available.")
        by_id[identifier] = case
    return by_id


def validate_sources(postings, tracts, manifest):
    cases = validate_manifest(manifest)
    if len(postings) != 10 or {row.get("posting_id") for row in postings} != set(EXPECTED):
        raise ValueError("Expected exactly the ten existing unique starter postings, IDs 1–10.")
    if not tracts or len(tracts) > MAX_TRACTS:
        raise ValueError("Metro tract inventory is empty or exceeds the audit bound.")
    seen = set()
    for row in tracts:
        identifier = str(row.get("geo_id", ""))
        lat, lon = number(row.get("tract_lat"), -90, 90), number(row.get("tract_lon"), -180, 180)
        if not re.fullmatch(r"53\d{9}", identifier) or identifier in seen or lat is None or lon is None:
            raise ValueError("Tract inventory must contain unique Washington tract IDs and valid coordinates.")
        seen.add(identifier)
    for row in postings:
        case = cases[row["posting_id"]]
        if (row.get("track") != case["expected_track"]
                or row.get("storage_state") != case["expected_storage_state"]
                or number(row.get("hours_available")) != number(case["expected_hours_available"])):
            raise ValueError("Posting differs from reviewed manifest; update assumptions explicitly.")
        if STORAGE.get(row["storage_state"]) != case["required_storage"]:
            raise ValueError("Manifest storage conflicts with source storage.")
        remaining = number(row.get("hours_remaining"))
        available = number(row.get("hours_available"))
        if remaining is None or remaining <= 0 or remaining > available:
            raise ValueError("Source hours_remaining must be positive and within hours_available.")
        if not isinstance(row.get("posting_text"), str) or not 5 <= len(row["posting_text"]) <= 4000:
            raise ValueError("Posting text must contain 5–4000 characters.")
    return cases


def metrics(tracts):
    """Separate equal-tract statistics from explicitly approximate population weighting."""
    result = {"rows": len(tracts), "metrics": {}}
    for field in ("poverty_rate", "no_vehicle_rate", "assistance_rate"):
        values = [(number(row.get(field), 0, 1), number(row.get("total_pop")))
                  for row in tracts]
        valid = [v for v, _ in values if v is not None]
        weighted = [(v, p) for v, p in values if v is not None and p is not None and p > 0]
        weight = sum(p for _, p in weighted)
        result["metrics"][field] = {
            "equal_row_mean": sum(valid) / len(valid) if valid else None,
            "valid_rows": len(valid), "missing_rows": len(values) - len(valid),
            "population_weighted_context": sum(v * p for v, p in weighted) / weight if weight else None,
            "population_weighted_rows": len(weighted),
            "population_weight_sum": weight,
        }
    return result


def compact_candidate(row):
    if row is None:
        return None
    return {key: row.get(key) for key in (
        "recipient_id", "name", "profile_source", "synthetic_organization", "tract_id",
        "semantic_distance", "composite_score", "constraint_status",
        "time_margin_hours", "timing_basis", "reason_codes", "elimination_reasons",
        "requires_confirmation",
    )}


def summarize(results, tract_by_id, baseline):
    winners = [r["winner"] for r in results if r.get("winner")]
    joined = [tract_by_id[str(w["tract_id"])] for w in winners if str(w.get("tract_id")) in tract_by_id]
    unique = {str(w["recipient_id"]): w for w in winners}
    unique_joined = [tract_by_id[str(w["tract_id"])] for w in unique.values()
                     if str(w.get("tract_id")) in tract_by_id]
    posting_stats, recipient_stats = metrics(joined), metrics(unique_joined)
    deltas = {}
    for field, value in posting_stats["metrics"].items():
        reference = baseline["metrics"][field]["equal_row_mean"]
        matched = value["equal_row_mean"]
        deltas[field] = (matched - reference) * 100 if matched is not None and reference is not None else None
    return {
        "postings": len(results), "matched_proposals": len(winners),
        "no_match": sum(r["status"] == "no_match" for r in results),
        "retrieval_or_evaluation_errors": sum(r["status"] == "error" for r in results),
        "match_coverage": len(winners) / len(results) if results else None,
        "unique_recipients": len(unique), "winners_without_current_tract": len(winners) - len(joined),
        "winner_profile_sources": dict(Counter(w["profile_source"] or "unknown" for w in winners)),
        "posting_weighted_tract_context": posting_stats,
        "unique_recipient_tract_context": recipient_stats,
        "posting_minus_metro_equal_tract_percentage_points": deltas,
        "no_match_reasons": dict(Counter(reason for r in results if r["status"] == "no_match"
                                        for reason in r["reason_codes"])),
    }


def make_toolset():
    """Same toolset/protocol/auth choices as broker; no model is constructed."""
    from toolbox_adk import CredentialStrategy, ToolboxToolset
    from toolbox_core.protocol import Protocol
    url = os.getenv("TOOLBOX_URL", "http://127.0.0.1:5000")
    credentials = None
    if url.startswith("https://"):
        credentials = CredentialStrategy.workload_identity(target_audience=url)
    elif url not in {"http://127.0.0.1:5000", "http://localhost:5000"}:
        raise ValueError("Unencrypted Toolbox must be loopback-only.")
    return ToolboxToolset(server_url=url, tool_names=["vector_search_recipients"],
                          credentials=credentials, protocol=Protocol.MCP_v20250326)


async def audit(postings, tracts, manifest, evaluated_at, *, toolset_factory=make_toolset,
                matcher=None, project=PROJECT, dataset=DATASET):
    cases = validate_sources(postings, tracts, manifest)
    when = timestamp(evaluated_at)
    matcher = matcher or SurplusMatcher(project_id=project, dataset_id=dataset)
    baseline = metrics(tracts)
    tract_by_id = {str(t["geo_id"]): t for t in tracts}
    results, toolset, tool, connection_error = [], None, None, False
    try:
        try:
            toolset = toolset_factory()
            discovered = await asyncio.wait_for(toolset.get_tools(), 30)
            tool = next((t for t in discovered if t.name == "vector_search_recipients"), None)
            if tool is None:
                raise ValueError("Vector tool missing.")
        except Exception:
            connection_error = True
        for posting in sorted(postings, key=lambda row: row["posting_id"]):
            case = cases[posting["posting_id"]]
            hours = float(posting["hours_remaining"])
            deadline = when + timedelta(hours=hours)
            result = {
                "posting_id": posting["posting_id"], "track": posting["track"],
                "posting_text": posting["posting_text"], "status": "error", "winner": None,
                "scenario": {**case, "hours_remaining": hours, "deadline_at": deadline.isoformat()},
                "source_clock": {key: posting.get(key) for key in (
                    "hours_available", "shelf_life_hours", "foodkeeper_product", "storage_state")},
                "query_sha256": hashlib.sha256(posting["posting_text"].encode()).hexdigest(),
                "candidate_count": 0, "alternatives": [], "rejected": [], "pending_confirmation": [],
                "reason_codes": [], "mcp_tool": "vector_search_recipients", "mcp_succeeded": False,
            }
            results.append(result)
            if connection_error:
                result["reason_codes"] = ["mcp_discovery_failed"]
                continue
            try:
                from agent.broker import parse_candidates
                # SDK1.4 permits no ADK context for this non-interactive server-auth tool.
                raw = await asyncio.wait_for(tool.run_async(
                    args={"query_text": posting["posting_text"]}, tool_context=None), 75)
                candidates = parse_candidates(raw)
                result["mcp_succeeded"] = True
                result["candidate_count"] = len(candidates)
                result["candidate_ids"] = [str(c["recipient_id"]) for c in candidates]
                result["candidate_sha256"] = digest(candidates)
                ranking = matcher.score_and_rank(
                    candidates, required_storage=case["required_storage"],
                    pallet_count=case["pallet_count"], hours_remaining=hours,
                    evaluated_at=when, deadline_at=deadline,
                )
                result["winner"] = compact_candidate(ranking["winner"])
                result["alternatives"] = [compact_candidate(c) for c in ranking["viable_runners_up"]]
                result["rejected"] = [compact_candidate(c) for c in ranking["eliminated_decoys"]]
                result["pending_confirmation"] = [compact_candidate(c) for c in ranking["pending_confirmation"]]
                result["status"] = "matched_proposal" if ranking["winner"] else "no_match"
                if not ranking["winner"]:
                    result["reason_codes"] = sorted({
                        code for c in result["rejected"] + result["pending_confirmation"]
                        for code in c.get("reason_codes") or []
                    })
                    if result["pending_confirmation"]:
                        result["reason_codes"].append("needs_confirmation")
                    if not candidates:
                        result["reason_codes"].append("no_candidates")
            except Exception:
                result["reason_codes"] = ["evaluation_failed" if result["mcp_succeeded"] else "mcp_call_or_payload_failed"]
    finally:
        if toolset is not None:
            try:
                await asyncio.wait_for(toolset.close(), 10)
            except Exception:
                pass
    groups = {
        "all_ten": results, "retail": [r for r in results if r["track"] == "RETAIL"],
        "cross_track": [r for r in results if r["track"] != "RETAIL"],
    }
    errors = sum(r["status"] == "error" for r in results)
    return {
        "report_version": 1, "status": "complete" if errors == 0 else ("blocked" if errors == 10 else "partial"),
        "generated_at": datetime.now(timezone.utc).isoformat(), "evaluated_at": when.isoformat(),
        "mode": "actual_mcp_retrieval_fixed_time_deterministic_audit_no_gemini",
        "source": {"project": project, "dataset": dataset, "bigquery_location": "US",
                   "postings": len(postings), "tracts": len(tracts),
                   "postings_sha256": digest(postings), "tracts_sha256": digest(tracts)},
        "manifest": manifest, "manifest_sha256": digest(manifest),
        "baseline_scope": "Every supplied tract_demographics row; region coverage is inherited from starter dataset metadata, not the map display bounding box.",
        "metro_baseline": baseline, "results": results,
        "summary": {name: summarize(rows, tract_by_id, baseline) for name, rows in groups.items()},
        "caveats": [
            "Quantity scenarios are explicit assumptions, not conversions or donor-confirmed capacities.",
            "All source windows are replayed from the fixed audit timestamp, including 720h; no API clamp.",
            "FoodKeeper-derived source clocks are starter guidance, not verified remaining freshness.",
            "Fixed-time replay does not consume runtime latency; this is evaluation, not live dispatch.",
            "Equal-tract mean and population weighting differ. Population-weighted vehicle/poverty rates are context approximations, not exact aggregate rates; household and poverty-status denominators are absent.",
            "Missing rates are excluded per metric and reported; they are never zeros.",
            "The full supplied tract dataset defines this baseline; the city-map viewport does not filter it.",
            "Recipient-to-tract association uses approximate ZIP centroids, not catchment boundaries.",
            "Generated operating profiles and planted teaching cases are unverified; matches are proposals, not delivered food or causal fairness proof.",
            "No race or ethnicity is used for ranking. This dataset does not permit a race/ethnicity outcome audit.",
            "The existing MCP query embeds postings and may consume Vertex/BigQuery quota; no synthesis model is called.",
        ],
    }


def read_sources(project, dataset):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", project) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dataset):
        raise ValueError("Invalid project or dataset.")
    from google.cloud import bigquery
    client = bigquery.Client(project=project)
    prefix = f"{project}.{dataset}"
    queries = [
        (f"""SELECT posting_id, track, posting_text, foodkeeper_product, storage_state,
                    hours_available, shelf_life_hours, hours_remaining, COUNT(*) OVER() AS source_row_count
             FROM `{prefix}.surplus_postings` ORDER BY posting_id LIMIT 11""", 10),
        (f"""SELECT geo_id, tract_lat, tract_lon, total_pop, median_income,
                    poverty_rate, no_vehicle_rate, assistance_rate, COUNT(*) OVER() AS source_row_count
             FROM `{prefix}.tract_demographics` ORDER BY geo_id LIMIT {MAX_TRACTS + 1}""", MAX_TRACTS),
    ]
    output = []
    for sql, cap in queries:
        job = client.query(sql, location="US", job_config=bigquery.QueryJobConfig(
            maximum_bytes_billed=100_000_000, use_query_cache=True,
            labels={"application": "seattle-food-broker", "path": "equity-audit"}))
        rows = [dict(row) for row in job.result(timeout=30)]
        if len(rows) > cap or (rows and rows[0]["source_row_count"] != len(rows)):
            raise ValueError("Audit source was truncated; cannot claim a full baseline.")
        for row in rows:
            row.pop("source_row_count", None)
        output.append(rows)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifest", type=Path, help="Write a new reviewable template and exit, without cloud calls.")
    parser.add_argument("--manifest", type=Path, help="Reviewed scenario manifest JSON.")
    parser.add_argument("--evaluated-at", help="Required fixed ISO timestamp including timezone.")
    parser.add_argument("--output", type=Path, help="Create a new report file; otherwise print sanitized report JSON.")
    parser.add_argument("--compact", action="store_true", help="Emit one JSON line for structured Cloud Run logging.")
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT", PROJECT))
    parser.add_argument("--dataset", default=os.getenv("BIGQUERY_DATASET", DATASET))
    args = parser.parse_args(argv)
    try:
        if args.write_manifest:
            with args.write_manifest.open("x") as file:
                json.dump(manifest_template(), file, indent=2, allow_nan=False)
            return 0
        if not args.manifest or not args.evaluated_at:
            parser.error("--manifest and --evaluated-at are required for an audit.")
        timestamp(args.evaluated_at)
        manifest = json.loads(args.manifest.read_text())
        validate_manifest(manifest)
        if args.output and args.output.exists():
            raise FileExistsError("Report already exists.")
        postings, tracts = read_sources(args.project, args.dataset)
        report = asyncio.run(audit(postings, tracts, manifest, args.evaluated_at,
                                   project=args.project, dataset=args.dataset))
        encoded = json.dumps(report, indent=None if args.compact else 2, allow_nan=False)
        if args.output:
            with args.output.open("x") as file:
                file.write(encoded + "\n")
        else:
            print(encoded)
        return 0 if report["status"] == "complete" else 1
    except Exception as exc:
        # Never log raw cloud/credential error text or response bodies.
        print(json.dumps({"status": "blocked", "error_code": type(exc).__name__,
                          "message": "Audit failed. Check reviewed inputs, source schemas and authorized lab access."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
