"""Offline audit tests; real matcher and payload parser, fake MCP/BigQuery boundary."""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.audit_postings import (
    EXPECTED, audit, main, manifest_template, metrics, read_sources,
    timestamp, validate_manifest, validate_sources,
)

WHEN = "2026-10-20T16:00:00-07:00"
TRACTS = [
    {"geo_id": "53033000100", "tract_lat": 47.61, "tract_lon": -122.33,
     "total_pop": 100, "poverty_rate": .2, "no_vehicle_rate": .3, "assistance_rate": .1},
    {"geo_id": "53033000200", "tract_lat": 47.62, "tract_lon": -122.32,
     "total_pop": 900, "poverty_rate": .8, "no_vehicle_rate": .1, "assistance_rate": None},
]
CANDIDATE = {
    "recipient_id": "r1", "name": "Offline fixture", "cold_storage": "commercial",
    "frozen_storage": False, "max_pallets": 2, "pickup_window_hours": 1,
    "distance": .2, "service_days": "daily", "profile_source": "generated",
    "tract_id": TRACTS[0]["geo_id"], "tract_poverty_rate": .2,
    "tract_no_vehicle_rate": .3,
}


def postings():
    return [
        {"posting_id": identifier, "track": track, "storage_state": storage,
         "posting_text": f"Offline test offer number {identifier}",
         "hours_available": hours, "hours_remaining": hours,
         "shelf_life_hours": None, "foodkeeper_product": None}
        for identifier, (track, storage, hours, _) in EXPECTED.items()
    ]


class FakeTool:
    name = "vector_search_recipients"

    def __init__(self, rows=None, error_at=None):
        self.rows = [CANDIDATE] if rows is None else rows
        self.error_at, self.calls = error_at, []

    async def run_async(self, args, tool_context):
        assert tool_context is None
        self.calls.append(args)
        if self.error_at == len(self.calls):
            raise RuntimeError("secret-token-provider-body")
        return json.dumps(self.rows)


class FakeToolset:
    def __init__(self, tool=None):
        self.tool, self.closed = tool or FakeTool(), False

    async def get_tools(self):
        return [self.tool]

    async def close(self):
        self.closed = True


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, tool=None, source=None, manifest=None):
        toolset = FakeToolset(tool)
        with patch("agent.broker.Gemini") as model:
            result = await audit(source or postings(), TRACTS, manifest or manifest_template(),
                                 WHEN, toolset_factory=lambda: toolset)
            model.assert_not_called()
        self.assertTrue(toolset.closed)
        return result, toolset

    async def test_ten_actual_tool_invocations_no_gemini_and_track_coverage(self):
        result, tools = await self.execute()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(tools.tool.calls), 10)
        self.assertEqual(tools.tool.calls, [{"query_text": p["posting_text"]} for p in postings()])
        self.assertEqual(result["summary"]["retail"]["postings"], 4)
        self.assertEqual(result["summary"]["cross_track"]["postings"], 6)
        self.assertEqual(result["summary"]["all_ten"]["matched_proposals"], 7)
        self.assertEqual(result["summary"]["all_ten"]["no_match"], 3)
        self.assertTrue(all(r["mcp_succeeded"] for r in result["results"]))
        self.assertNotIn('"embedding"', json.dumps(result))

    async def test_original_posting_contents_reach_acceptance_gates(self):
        source = postings()
        source[0]["posting_text"] = "Chilled pork for donation"
        restricted = dict(CANDIDATE, profile_text="We cannot accept pork or uncertified meat.")
        result, _ = await self.execute(FakeTool(rows=[restricted]), source=source)
        first = result["results"][0]
        self.assertEqual(first["status"], "no_match")
        self.assertIn("recipient_food_restriction", first["reason_codes"])
        self.assertEqual(first["rejected"][0]["recipient_id"], CANDIDATE["recipient_id"])

    async def test_720_hour_source_clock_is_not_api_clamped(self):
        result, _ = await self.execute()
        scenario = result["results"][8]["scenario"]
        self.assertEqual(scenario["hours_remaining"], 720)
        deadline = datetime.fromisoformat(scenario["deadline_at"])
        self.assertEqual((deadline - timestamp(WHEN)).total_seconds() / 3600, 720)
        self.assertTrue(all(r["scenario"]["deadline_basis"] for r in result["results"]))

    async def test_no_match_retains_constraint_reason_and_generated_provenance(self):
        result, _ = await self.execute()
        farm = result["results"][4]
        self.assertEqual(farm["status"], "no_match")
        self.assertIn("capacity_exceeded", farm["reason_codes"])
        self.assertEqual(farm["rejected"][0]["profile_source"], "generated")
        self.assertTrue(farm["rejected"][0]["elimination_reasons"])

    async def test_baseline_join_posting_and_unique_recipient_denominators(self):
        result, _ = await self.execute()
        summary = result["summary"]["all_ten"]
        self.assertEqual(summary["posting_weighted_tract_context"]["rows"], 7)
        self.assertEqual(summary["unique_recipient_tract_context"]["rows"], 1)
        self.assertEqual(summary["winner_profile_sources"], {"generated": 7})
        self.assertAlmostEqual(summary["posting_minus_metro_equal_tract_percentage_points"]["poverty_rate"], -30)

    async def test_planted_cases_are_disclosed(self):
        result, _ = await self.execute(FakeTool(rows=[dict(CANDIDATE, profile_source="planted")]))
        self.assertEqual(result["summary"]["all_ten"]["winner_profile_sources"], {"planted": 7})
        self.assertTrue(result["results"][0]["winner"]["synthetic_organization"])

    async def test_missing_tract_not_silently_replaced_by_embedding_demographics(self):
        result, _ = await self.execute(FakeTool(rows=[dict(CANDIDATE, tract_id="53033999999")]))
        summary = result["summary"]["all_ten"]
        self.assertEqual(summary["winners_without_current_tract"], 7)
        self.assertIsNone(summary["posting_minus_metro_equal_tract_percentage_points"]["poverty_rate"])

    async def test_retrieval_error_is_not_no_match_and_does_not_leak_provider_text(self):
        result, _ = await self.execute(FakeTool(error_at=2))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["results"][1]["status"], "error")
        self.assertEqual(result["results"][1]["reason_codes"], ["mcp_call_or_payload_failed"])
        self.assertNotIn("secret-token", json.dumps(result))
        self.assertEqual(result["summary"]["all_ten"]["retrieval_or_evaluation_errors"], 1)

    async def test_empty_retrieval_is_honest_no_match_complete_audit(self):
        result, _ = await self.execute(FakeTool(rows=[]))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["summary"]["all_ten"]["no_match"], 10)
        self.assertEqual(result["summary"]["all_ten"]["match_coverage"], 0)
        self.assertEqual(result["results"][0]["reason_codes"], ["no_candidates"])

    async def test_discovery_failure_records_all_ten_and_closes(self):
        toolset = FakeToolset()
        async def fail():
            raise RuntimeError("secret-discovery-response")
        toolset.get_tools = fail
        result = await audit(postings(), TRACTS, manifest_template(), WHEN,
                             toolset_factory=lambda: toolset)
        self.assertTrue(toolset.closed)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["results"]), 10)
        self.assertEqual(result["summary"]["all_ten"]["no_match"], 0)
        self.assertNotIn("secret-discovery", json.dumps(result))

    async def test_inputs_validated_before_any_mcp_connection(self):
        factory = Mock()
        source = postings()
        source[0]["hours_remaining"] = 2000
        with self.assertRaises(ValueError):
            await audit(source, TRACTS, manifest_template(), WHEN, toolset_factory=factory)
        factory.assert_not_called()

    async def test_source_and_manifest_fingerprints_are_stable(self):
        first, _ = await self.execute()
        second, _ = await self.execute()
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertEqual(first["source"], second["source"])
        self.assertEqual(first["results"], second["results"])


class InputTests(unittest.TestCase):
    def test_all_pallets_require_explicit_basis_and_positive_values(self):
        for value in (None, 0, -1, float("nan"), True):
            manifest = manifest_template()
            manifest["cases"][0]["pallet_count"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_manifest(manifest)
        manifest = manifest_template()
        manifest["cases"][0]["quantity_basis"] = ""
        with self.assertRaises(ValueError):
            validate_manifest(manifest)

    def test_missing_duplicate_or_changed_postings_rejected(self):
        for source in (postings()[:-1], postings()[:-1] + [postings()[0]]):
            with self.assertRaises(ValueError):
                validate_sources(source, TRACTS, manifest_template())
        source = postings()
        source[0]["storage_state"] = "pantry"
        with self.assertRaises(ValueError):
            validate_sources(source, TRACTS, manifest_template())

    def test_manifest_requires_integer_ids_and_version(self):
        for field, value in (("posting_id", 1.0), ("posting_id", True), ("version", True)):
            manifest = manifest_template()
            (manifest if field == "version" else manifest["cases"][0])[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_manifest(manifest)

    def test_geographic_drift_and_duplicate_tracts_block_full_metro_claim(self):
        for rows in ([dict(TRACTS[0], geo_id="17033000100")],
                     [dict(TRACTS[0], tract_lat=91)], [TRACTS[0], TRACTS[0]]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_sources(postings(), rows, manifest_template())

    def test_valid_washington_tract_outside_display_box_is_retained(self):
        wider = TRACTS + [dict(TRACTS[0], geo_id="53061000100", tract_lat=48.0,
                              tract_lon=-122.15, poverty_rate=.9)]
        validate_sources(postings(), wider, manifest_template())
        baseline = metrics(wider)
        self.assertEqual(baseline["rows"], 3)
        self.assertAlmostEqual(baseline["metrics"]["poverty_rate"]["equal_row_mean"], 1.9 / 3)

    def test_invalid_or_missing_timezone_rejected(self):
        for value in ("2026-10-20T16:00:00", "tomorrow", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                timestamp(value)

    def test_missing_rates_excluded_not_zero_and_weightings_distinct(self):
        result = metrics(TRACTS)
        self.assertAlmostEqual(result["metrics"]["poverty_rate"]["equal_row_mean"], .5)
        self.assertAlmostEqual(result["metrics"]["poverty_rate"]["population_weighted_context"], .74)
        self.assertEqual(result["metrics"]["assistance_rate"]["missing_rows"], 1)
        self.assertEqual(result["metrics"]["assistance_rate"]["equal_row_mean"], .1)
        malformed = metrics([dict(TRACTS[0], poverty_rate=float("nan"), no_vehicle_rate=2)])
        self.assertIsNone(malformed["metrics"]["poverty_rate"]["equal_row_mean"])
        json.dumps(malformed, allow_nan=False)

    def test_manifest_creation_offline_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, patch("scripts.audit_postings.read_sources") as cloud:
            path = Path(directory) / "manifest.json"
            self.assertEqual(main(["--write-manifest", str(path)]), 0)
            self.assertEqual(len(json.loads(path.read_text())["cases"]), 10)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--write-manifest", str(path)]), 1)
            self.assertEqual(json.loads(output.getvalue())["error_code"], "FileExistsError")
            cloud.assert_not_called()

    def test_cloud_query_contract_reads_existing_columns_with_budget_and_bound(self):
        client = Mock()
        client.query.return_value.result.side_effect = [
            [{**row, "source_row_count": 10} for row in postings()],
            [{**row, "source_row_count": 2} for row in TRACTS],
        ]
        with patch("google.cloud.bigquery.Client", return_value=client):
            output = read_sources("test-project", "a4i_food")
        self.assertEqual(len(output[0]), 10)
        self.assertNotIn("source_row_count", output[0][0])
        for call in client.query.call_args_list:
            sql = call.args[0]
            self.assertTrue(sql.strip().startswith("SELECT "))
            self.assertIn("LIMIT", sql)
            self.assertNotIn("SELECT *", sql)
            self.assertEqual(call.kwargs["location"], "US")
            self.assertEqual(call.kwargs["job_config"].maximum_bytes_billed, 100_000_000)

    def test_truncated_baseline_cannot_pass(self):
        client = Mock()
        client.query.return_value.result.return_value = [
            {**postings()[0], "source_row_count": 30}]
        with patch("google.cloud.bigquery.Client", return_value=client), self.assertRaises(ValueError):
            read_sources("test-project", "a4i_food")


if __name__ == "__main__":
    unittest.main()
