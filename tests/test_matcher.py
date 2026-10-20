"""Behavior tests without cloud clients, credentials or model calls."""
import importlib.util
import json
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

    def test_schedule_qualifiers_require_confirmation_instead_of_guessing_open_or_closed(self):
        schedules = (
            "Tuesday by appointment only",
            "Not Tuesday",
            "Last Tuesday of the month",
            "First and last Tuesday of the month",
            "Tuesday every other week",
            "Tuesday 9am to 5pm",
            "Tuesday except holidays",
            "Third Tuesday of the month by appointment",
            "Third Tuesday of the month except holidays",
            "Tuesday or Wednesday",
            "Monday by appointment only",
            "Monday-Saturday",
        )
        for schedule in schedules:
            with self.subTest(schedule=schedule):
                result = self.check(candidate(service_days=schedule))
                self.assertEqual(result["status"], "needs_confirmation")
                self.assertFalse(result["viable"])
                self.assertIn("Receiving schedule requires confirmation.", result["confirmations_required"])
                self.assertNotIn("service_day_closed", result["reason_codes"])

    def test_recognized_weekly_lists_and_aliases_preserve_open_and_closed_days(self):
        schedules = (
            ("Tuesday", True, False),
            ("Tuesday and Thursday", True, False),
            ("Monday, Tuesday, and Friday", True, False),
            (" every  Tuesday, Thursday ", True, False),
            ("Sunday, Monday", False, True),
            ("weekdays", True, False),
            ("Monday-Friday", True, False),
            ("Monday through Friday", True, False),
            ("daily", True, True),
            ("every day", True, True),
            ("everyday", True, True),
            ("7 days", True, True),
            ("seven days", True, True),
        )
        for schedule, tuesday_open, sunday_open in schedules:
            for when, expected in ((NOW, tuesday_open),
                                   (datetime(2026, 10, 25, 16, tzinfo=timezone.utc), sunday_open)):
                with self.subTest(schedule=schedule, when=when):
                    result = self.matcher.evaluate_constraints(
                        candidate(service_days=schedule, pickup_eta_hours=0), evaluated_at=when,
                    )
                    self.assertEqual(result["status"], "eligible" if expected else "rejected")
                    self.assertEqual("service_day_closed" in result["reason_codes"], not expected)

    def test_every_supplied_schedule_retains_its_documented_weekly_or_monthly_pattern(self):
        data = json.loads((Path(__file__).parents[1] / "data/profile_components.json").read_text())
        expected_open_dates = {
            "Monday": {5, 12, 19, 26},
            "Tuesday": {6, 13, 20, 27},
            "Wednesday": {7, 14, 21, 28},
            "Thursday": {1, 8, 15, 22, 29},
            "Friday": {2, 9, 16, 23, 30},
            "Saturday": {3, 10, 17, 24, 31},
            "Monday, Wednesday, Friday": {2, 5, 7, 9, 12, 14, 16, 19, 21, 23, 26, 28, 30},
            "Tuesday, Thursday": {1, 6, 8, 13, 15, 20, 22, 27, 29},
            "Monday, Tuesday, Wednesday, Thursday, Friday": set(range(1, 32)) - {3, 4, 10, 11, 17, 18, 24, 25, 31},
            "Sunday, Monday, Tuesday, Wednesday, Thursday, Friday, Saturday": set(range(1, 32)),
            "Second and fourth Wednesday of the month": {14, 28},
            "First Saturday of the month": {3},
        }
        self.assertEqual(set(data["service_days"]), set(expected_open_dates))
        for schedule in data["service_days"]:
            for day in range(1, 32):
                with self.subTest(schedule=schedule, october_day=day):
                    result = self.matcher.evaluate_constraints(
                        candidate(service_days=schedule, pickup_eta_hours=0),
                        evaluated_at=datetime(2026, 10, day, 16, tzinfo=timezone.utc),
                    )
                    self.assertEqual(result["status"],
                                     "eligible" if day in expected_open_dates[schedule] else "rejected")


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

    def test_qualified_schedule_is_pending_and_cannot_be_winner_or_backup(self):
        result = self.rank([
            candidate(recipient_id=1, service_days="Tuesday by appointment only", distance=0.01),
            candidate(recipient_id=2, service_days="Tuesday", distance=0.4),
        ])
        self.assertEqual(result["winner"]["recipient_id"], 2)
        self.assertEqual(result["viable_runners_up"], [])
        self.assertEqual([row["recipient_id"] for row in result["pending_confirmation"]], [1])
        self.assertEqual(result["pending_confirmation"][0]["constraint_status"], "needs_confirmation")
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


class AcceptanceTests(unittest.TestCase):
    """Use literal supplied profile needs, including negation and conditional cases."""
    @classmethod
    def setUpClass(cls):
        data = json.loads((Path(__file__).parents[1] / "data/profile_components.json").read_text())
        cls.archetypes = data["archetypes"]

    def check(self, profile, offer=None, storage="ambient"):
        return module.SurplusMatcher().evaluate_constraints(
            candidate(profile_text=profile), required_storage=storage,
            posting_text=offer, evaluated_at=NOW,
        )

    def test_supplied_pork_refusal_blocks_even_semantically_close_offer(self):
        profile = self.archetypes["halal_kosher"]["needs"][0]
        result = self.check(profile, "1 pallet of chilled pork", "cold")
        self.assertEqual(result["status"], "rejected")
        self.assertIn("recipient_food_restriction", result["reason_codes"])
        self.assertIn("cannot accept pork", result["acceptance_evidence"])

    def test_explicit_uncertified_meat_refused_unknown_or_claimed_certification_pending(self):
        profile = self.archetypes["halal_kosher"]["needs"][1]
        for offer in ("uncertified beef", "not certified chicken"):
            with self.subTest(offer=offer):
                self.assertEqual(self.check(profile, offer, "cold")["status"], "rejected")
        for offer in ("chilled chicken", "certified halal beef", "organic beef"):
            with self.subTest(offer=offer):
                result = self.check(profile, offer, "cold")
                self.assertEqual(result["status"], "needs_confirmation")
                self.assertIn("acceptance_confirmation_required", result["reason_codes"])

    def test_plant_food_and_negated_pork_are_not_refused_as_meat(self):
        profile = self.archetypes["halal_kosher"]["needs"][0]
        for offer in ("fresh spinach", "bread with no pork", "pork-free bread", "vegan pork with tofu",
                      "plant-based beef and lentils", "canned chickpeas", "shelf-stable graham crackers and bread"):
            with self.subTest(offer=offer):
                self.assertEqual(self.check(profile, offer)["status"], "eligible")
        # A substitute in one item cannot hide actual pork in a different item.
        self.assertEqual(self.check(profile, "vegan bread and pork")["status"], "rejected")

    def test_supplied_infant_only_refuses_regular_food_not_diapers(self):
        profile = self.archetypes["diaper_bank"]["needs"][1]
        for offer in ("fresh spinach", "shelf-stable bread", "canned beans and diapers"):
            with self.subTest(offer=offer):
                self.assertEqual(self.check(profile, offer)["status"], "rejected")
        self.assertEqual(self.check(profile, "sealed diapers and wipes")["status"], "eligible")
        self.assertEqual(self.check(profile, "mixed donations")["status"], "needs_confirmation")

    def test_no_food_profile_does_not_certify_formula_exception(self):
        profile = self.archetypes["diaper_bank"]["needs"][7]
        self.assertEqual(self.check(profile, "shelf-stable bread")["status"], "rejected")
        self.assertEqual(self.check(profile, "sealed infant formula")["status"], "needs_confirmation")
        self.assertEqual(self.check(profile, "diapers and wipes")["status"], "eligible")

    def test_formula_packaging_rule_keeps_donor_claims_unverified(self):
        profile = self.archetypes["diaper_bank"]["needs"][2]
        for offer in ("opened infant formula", "expired formula", "unsealed formula"):
            with self.subTest(offer=offer):
                self.assertEqual(self.check(profile, offer)["status"], "rejected")
        self.assertEqual(self.check(profile, "sealed in-date formula")["status"], "needs_confirmation")

    def test_nonperishable_refusal_and_unknown_preservation(self):
        profile = self.archetypes["disaster_staging"]["needs"][0]
        self.assertEqual(self.check(profile, "fresh spinach", "cold")["status"], "rejected")
        self.assertEqual(self.check(profile, "fresh spinach", "ambient")["status"], "rejected")
        self.assertEqual(self.check(profile, "canned spinach", "ambient")["status"], "eligible")
        self.assertEqual(self.check(profile, "canned beans and fresh spinach", "ambient")["status"], "rejected")
        self.assertEqual(self.check(profile, "mixed load of canned beans and assorted items", "ambient")["status"], "needs_confirmation")

    def test_preferences_and_demographics_are_not_prohibitions(self):
        for profile in (self.archetypes["halal_kosher"]["needs"][7],
                        "Community pantry serving families who keep halal.",
                        "Fresh produce preferred, meat welcome.",
                        "We accept uncertified meat."):
            with self.subTest(profile=profile):
                self.assertEqual(self.check(profile, "chicken", "cold")["status"], "eligible")

    def test_ambiguous_conditional_or_unsupported_restrictions_need_review(self):
        for profile in ("We cannot accept pork unless the coordinator approves it.",
                        "We cannot accept food without a clear cold chain.",
                        "We cannot accept sesame."):
            with self.subTest(profile=profile):
                self.assertEqual(self.check(profile, "pork", "cold")["status"], "needs_confirmation")

    def test_recognized_refusal_prefix_cannot_hide_an_unsupported_remainder(self):
        result = self.check("We cannot accept pork or nuts.", "bread with walnuts")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertNotIn("recipient_food_restriction", result["reason_codes"])
        self.assertTrue(any("pork or nuts" in reason for reason in result["reasons"]))

    def test_qualified_food_refusal_is_not_a_blanket_no_food_ban(self):
        result = self.check("We cannot accept food containing nuts.", "plain rice")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertNotIn("recipient_food_restriction", result["reason_codes"])

    def test_legacy_missing_offer_cannot_pass_a_restricted_profile(self):
        profile = self.archetypes["halal_kosher"]["needs"][0]
        self.assertEqual(self.check(profile)["status"], "needs_confirmation")
        self.assertEqual(self.check("General community pantry.")["status"], "eligible")

    def test_mixed_load_requires_certification_without_excluding_produce_alone(self):
        profile = self.archetypes["halal_kosher"]["needs"][1]
        self.assertEqual(self.check(profile, "spinach and chicken", "cold")["status"], "needs_confirmation")
        self.assertEqual(self.check(profile, "spinach", "cold")["status"], "eligible")
        self.assertEqual(self.check(profile, "mixed load of spinach and protein", "cold")["status"], "needs_confirmation")

    def test_refused_candidate_cannot_win_or_be_an_alternative(self):
        rows = [
            candidate(recipient_id=1, distance=.01, profile_text=self.archetypes["halal_kosher"]["needs"][0]),
            candidate(recipient_id=2, distance=.4, profile_text="General food bank."),
        ]
        result = module.SurplusMatcher().score_and_rank(
            rows, posting_text="chilled pork", evaluated_at=NOW,
        )
        self.assertEqual(result["winner"]["recipient_id"], 2)
        self.assertEqual(result["viable_runners_up"], [])
        self.assertEqual(result["eliminated_decoys"][0]["recipient_id"], 1)


if __name__ == "__main__":
    unittest.main()
