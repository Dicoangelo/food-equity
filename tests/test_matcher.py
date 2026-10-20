"""Behavior tests without cloud clients, credentials or model calls."""
import importlib.util
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("matcher_under_test", Path(__file__).parents[1] / "agent" / "matcher.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)  # Tuesday, 09:00 Seattle.


def candidate(**changes):
    return {
        "recipient_id": 1, "name": "Fixture pantry", "street": "Fixture address",
        "city": "Seattle", "zip_code": "00000", "profile_source": "generated",
        "profile_text": "Fixture profile with refrigerated storage.",
        "cold_storage": "walk-in", "frozen_storage": False, "max_pallets": 2,
        "pickup_window_hours": 1, "service_days": "Monday, Tuesday, Wednesday, Thursday, Friday",
        "refrigerated_transport": False, "loading_dock": False,
        "tract_poverty_rate": 0.2, "tract_no_vehicle_rate": 0.3, "distance": 0.2,
        **changes,
    }


class ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.matcher = module.SurplusMatcher()

    def check(self, row=None, **args):
        return self.matcher.evaluate_constraints(row or candidate(), evaluated_at=NOW, **args)

    def test_pure_scoring_never_creates_cloud_client(self):
        self.assertIsNone(self.matcher.client)
        self.assertTrue(self.check()["viable"])
        self.assertIsNone(self.matcher.client)

    def test_semantic_decoy_rejected_for_all_chilled_names(self):
        for storage in ("cold", "refrigerated", "chilled", "Refrigerated"):
            with self.subTest(storage=storage):
                result = self.check(candidate(cold_storage="none", distance=0.01), required_storage=storage)
                self.assertFalse(result["viable"])
                self.assertIn("storage_incompatible", result["reason_codes"])

    def test_unknown_and_vehicle_storage_require_confirmation(self):
        for storage in (None, "", "unknown", "unverified", "vehicle"):
            with self.subTest(storage=storage):
                self.assertEqual(self.check(candidate(cold_storage=storage))["status"], "needs_confirmation")

    def test_freezer_requires_boolean_confirmation(self):
        self.assertTrue(self.check(candidate(frozen_storage=True), required_storage="frozen")["viable"])
        self.assertFalse(self.check(candidate(frozen_storage=False), required_storage="frozen")["viable"])
        self.assertEqual(self.check(candidate(frozen_storage="true"), required_storage="frozen")["status"], "needs_confirmation")

    def test_planted_contradiction_requires_confirmation(self):
        row = candidate(frozen_storage=True, profile_source="planted",
                        profile_text="We operate a large walk-in freezer. We have no freezer at present.")
        result = self.check(row, required_storage="frozen")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertTrue(any("contradict" in reason for reason in result["reasons"]))

    def test_ambient_food_does_not_require_refrigeration(self):
        self.assertTrue(self.check(candidate(cold_storage="none"), required_storage="pantry")["viable"])

    def test_capacity_boundary(self):
        self.assertTrue(self.check(candidate(max_pallets=1), pallet_count=1)["viable"])
        self.assertFalse(self.check(candidate(max_pallets=1), pallet_count=2)["viable"])
        self.assertEqual(self.check(candidate(max_pallets=None))["status"], "needs_confirmation")

    def test_invalid_quantities_cannot_match(self):
        for quantity in (-1, 0, math.nan, math.inf, None, True):
            with self.subTest(quantity=quantity):
                result = self.check(pallet_count=quantity)
                self.assertFalse(result["viable"])
                self.assertIn("invalid_quantity", result["reason_codes"])

    def test_invalid_storage_cannot_bypass_validation(self):
        for storage in ("", "cooled-ish", None):
            self.assertFalse(self.check(required_storage=storage)["viable"])

    def test_missed_deadline_rejected_even_with_refrigerated_transport(self):
        for reefer in (False, True):
            with self.subTest(reefer=reefer):
                self.assertFalse(self.check(candidate(pickup_window_hours=6, refrigerated_transport=reefer),
                                            hours_remaining=4)["viable"])
                self.assertFalse(self.check(candidate(pickup_window_hours=100, refrigerated_transport=reefer),
                                            hours_remaining=0)["viable"])

    def test_margin_and_fixed_clock_do_not_reset(self):
        row = candidate(pickup_eta_hours=1.5)
        first = self.check(row, hours_remaining=2)
        self.assertTrue(first["viable"])
        self.assertEqual(first["time_margin_hours"], 0.5)
        later = self.matcher.evaluate_constraints(
            row, hours_remaining=2, deadline_at=first["deadline_at"],
            evaluated_at=NOW + timedelta(minutes=30),
        )
        self.assertFalse(later["viable"])
        self.assertEqual(later["time_margin_hours"], 0)
        self.assertEqual(later["deadline_at"], first["deadline_at"])

    def test_buffer_boundary(self):
        self.assertTrue(self.check(candidate(pickup_eta_hours=1.75), hours_remaining=2)["viable"])
        self.assertFalse(self.check(candidate(pickup_eta_hours=1.8), hours_remaining=2)["viable"])
        self.assertFalse(self.check(candidate(pickup_eta_hours=2), hours_remaining=2, min_buffer_hours=0)["viable"])

    def test_missing_pickup_is_not_invented_zero_is_immediate(self):
        self.assertEqual(self.check(candidate(pickup_window_hours=None), hours_remaining=24)["status"], "needs_confirmation")
        self.assertTrue(self.check(candidate(pickup_window_hours=0))["viable"])

    def test_invalid_expired_and_timezone_free_clocks(self):
        for hours in (-1, 0, math.inf, math.nan):
            self.assertFalse(self.check(hours_remaining=hours)["viable"])
        self.assertFalse(self.check(deadline_at=(NOW - timedelta(minutes=1)).isoformat())["viable"])
        self.assertFalse(self.check(deadline_at="2026-10-20T16:00:00")["viable"])

    def test_service_day_is_evaluated_in_seattle(self):
        self.assertFalse(self.check(candidate(service_days="Monday"))["viable"])
        self.assertTrue(self.check(candidate(service_days="Tuesday"))["viable"])
        self.assertEqual(self.check(candidate(service_days="by appointment"))["status"], "needs_confirmation")
        result = self.matcher.evaluate_constraints(candidate(service_days="Tuesday", pickup_eta_hours=0),
                                                  evaluated_at=datetime(2026, 10, 21, 1, tzinfo=timezone.utc))
        self.assertTrue(result["viable"])  # UTC Wednesday is still Seattle Tuesday.

    def test_ordinal_schedule_does_not_mean_every_wednesday(self):
        row = candidate(service_days="Second and fourth Wednesday of the month", pickup_eta_hours=0)
        third = self.matcher.evaluate_constraints(row, evaluated_at=datetime(2026, 10, 21, 16, tzinfo=timezone.utc))
        fourth = self.matcher.evaluate_constraints(row, evaluated_at=datetime(2026, 10, 28, 16, tzinfo=timezone.utc))
        self.assertFalse(third["viable"])
        self.assertTrue(fourth["viable"])


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.matcher = module.SurplusMatcher()

    def rank(self, rows):
        return self.matcher.score_and_rank(rows, evaluated_at=NOW)

    def test_decoy_cannot_beat_feasible_candidate(self):
        result = self.rank([candidate(recipient_id=1, cold_storage="none", distance=0.01),
                            candidate(recipient_id=2, distance=0.4)])
        self.assertEqual(result["winner"]["recipient_id"], 2)
        self.assertEqual(result["eliminated_decoys"][0]["recipient_id"], 1)

    def test_unknowns_remain_separate(self):
        result = self.rank([candidate(max_pallets=None)])
        self.assertIsNone(result["winner"])
        self.assertEqual(len(result["pending_confirmation"]), 1)
        self.assertEqual(result["eliminated_decoys"], [])

    def test_two_distinct_fallbacks_without_mutating_input(self):
        rows = [candidate(recipient_id=i, distance=i / 10) for i in range(1, 5)]
        result = self.rank(rows + [dict(rows[0])])
        self.assertEqual(len(result["viable_runners_up"]), 2)
        self.assertEqual(len({r["recipient_id"] for r in [result["winner"], *result["viable_runners_up"]]}), 3)
        self.assertEqual(result["total_candidates_evaluated"], 4)
        self.assertNotIn("composite_score", rows[0])

    def test_missing_demographics_are_not_claimed_zero(self):
        winner = self.rank([candidate(tract_poverty_rate=None, tract_no_vehicle_rate=None)])["winner"]
        self.assertIsNone(winner["tract_poverty_pct"])
        self.assertIsNone(winner["tract_no_vehicle_pct"])

    def test_provenance_does_not_certify_generated_capacity(self):
        winner = self.rank([candidate(profile_source="planted")])["winner"]
        self.assertEqual(winner["profile_source"], "planted")
        self.assertTrue(winner["synthetic_organization"])
        self.assertFalse(winner["operational_data_verified"])
        self.assertTrue(winner["requires_confirmation"])
        self.assertEqual(winner["geography_precision"], "zip_centroid")

    def test_bad_vector_distance_cannot_produce_winner(self):
        for distance in (None, math.nan, math.inf, -1, 3):
            self.assertIsNone(self.rank([candidate(distance=distance)])["winner"])


if __name__ == "__main__":
    unittest.main()
