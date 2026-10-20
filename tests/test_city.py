"""Offline city-data contracts: no ADC or cloud calls."""
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

from agent.city import CityDataService, ROW_CAPS, MAXIMUM_BYTES_BILLED


TRACT = {
    "geo_id": "53033000100", "tract_lat": 47.61, "tract_lon": -122.33,
    "total_pop": 4000, "median_income": 65000, "poverty_rate": .2,
    "no_vehicle_rate": .3, "assistance_rate": .1, "source_row_count": 1,
}
RECIPIENT = {
    "recipient_id": "r1", "name": "Teaching food bank", "latitude": 47.60,
    "longitude": -122.32, "cold_storage": "vehicle", "frozen_storage": False,
    "max_pallets": 2, "profile_source": "planted", "source_row_count": 1,
}


class CityTests(unittest.TestCase):
    def service(self, tracts=None, recipients=None, **kwargs):
        service = CityDataService(**kwargs)
        service._query = Mock(side_effect=[
            [dict(TRACT)] if tracts is None else tracts,
            [dict(RECIPIENT)] if recipients is None else recipients,
        ])
        return service

    def test_constructor_does_not_load_adc_or_create_client(self):
        self.assertIsNone(CityDataService().client)

    def test_ready_provenance_and_heat_are_explicit(self):
        result = self.service().get_city_data()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["tracts"][0]["need_score"], .25)
        self.assertEqual(result["provenance"]["rate_units"], "fraction_0_to_1")
        recipient = result["recipients"][0]
        self.assertTrue(recipient["synthetic_organization"])
        self.assertFalse(recipient["operational_data_verified"])
        self.assertTrue(recipient["cold_storage_confirmation_required"])
        self.assertEqual(recipient["geography_precision"], "zip_centroid")
        self.assertFalse(result["routing"]["travel_time_verified"])

    def test_output_allowlist_excludes_vectors_and_unexpected_fields(self):
        row = dict(RECIPIENT, embedding=[1.0] * 3072, access_token="never-export")
        result = self.service(recipients=[row]).get_city_data()
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn("never-export", encoded)
        self.assertNotIn('"embedding"', encoded)

    def test_invalid_rates_stay_unknown_and_nonfinite_never_leaks(self):
        row = dict(TRACT, poverty_rate=float("nan"), no_vehicle_rate=40,
                   median_income=float("inf"), assistance_rate=-.2)
        result = self.service(tracts=[row]).get_city_data()
        for field in ("poverty_rate", "no_vehicle_rate", "need_score", "median_income", "assistance_rate"):
            self.assertIsNone(result["tracts"][0][field])
        json.dumps(result, allow_nan=False)

    def test_zero_rate_is_real_zero(self):
        row = dict(TRACT, poverty_rate=0, no_vehicle_rate=0)
        self.assertEqual(self.service(tracts=[row]).get_city_data()["tracts"][0]["need_score"], 0)

    def test_wrong_metro_bad_coordinates_and_duplicates_are_filtered(self):
        rows = [TRACT, dict(TRACT), dict(TRACT, geo_id="17033000100"),
                dict(TRACT, geo_id="53033000200", tract_lat=0),
                dict(TRACT, geo_id="53033000300", tract_lon=float("nan"))]
        result = self.service(tracts=rows).get_city_data()
        self.assertEqual(len(result["tracts"]), 1)
        self.assertEqual(result["limits"]["tracts"]["skipped_invalid_or_duplicate"], 4)

    def test_partial_cloud_error_is_sanitized(self):
        service = self.service()
        service._query.side_effect = [RuntimeError("secret-account-token"), [RECIPIENT]]
        result = service.get_city_data()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["errors"][0]["code"], "data_unavailable")
        self.assertNotIn("secret-account-token", json.dumps(result))

    def test_empty_is_unavailable_not_ready(self):
        result = self.service(tracts=[], recipients=[]).get_city_data()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(len(result["errors"]), 2)

    def test_cap_and_source_count_are_preserved(self):
        rows = [dict(RECIPIENT, recipient_id=f"r{i}", source_row_count=510)
                for i in range(ROW_CAPS["recipients"] + 1)]
        result = self.service(recipients=rows).get_city_data()
        self.assertEqual(len(result["recipients"]), ROW_CAPS["recipients"])
        self.assertTrue(result["limits"]["recipients"]["truncated"])
        self.assertEqual(result["limits"]["recipients"]["matching_source_rows"], 510)

    def test_cached_response_cannot_be_mutated_by_caller(self):
        service = self.service()
        first = service.get_city_data()
        first["recipients"][0]["name"] = "modified"
        second = service.get_city_data()
        self.assertTrue(second["cache"]["hit"])
        self.assertNotEqual(second["recipients"][0]["name"], "modified")
        self.assertEqual(service._query.call_count, 2)

    def test_expired_cache_queries_again(self):
        service = self.service(cache_ttl_seconds=0)
        service._query.side_effect = [[TRACT], [RECIPIENT], [TRACT], [RECIPIENT]]
        service.get_city_data()
        service.get_city_data()
        self.assertEqual(service._query.call_count, 4)

    def test_safe_query_has_budget_timeout_limits_and_no_vector_selection(self):
        fake = types.ModuleType("google.cloud.bigquery")
        fake.QueryJobConfig = lambda **kwargs: types.SimpleNamespace(**kwargs)
        fake.ScalarQueryParameter = lambda *args: args
        cloud = types.ModuleType("google.cloud")
        cloud.bigquery = fake
        google = types.ModuleType("google")
        google.cloud = cloud
        client = Mock()
        client.query.return_value.result.return_value = [RECIPIENT]
        service = CityDataService(client=client)
        with patch.dict(sys.modules, {"google": google, "google.cloud": cloud, "google.cloud.bigquery": fake}):
            service._query("recipients")
        args, kwargs = client.query.call_args
        self.assertNotIn("SELECT *", args[0])
        self.assertNotIn(" embedding,", args[0])
        self.assertEqual(kwargs["location"], "US")
        config = kwargs["job_config"]
        self.assertEqual(config.maximum_bytes_billed, MAXIMUM_BYTES_BILLED)
        self.assertEqual(config.query_parameters, [("row_limit", "INT64", 501)])
        client.query.return_value.result.assert_called_once_with(timeout=20)

    def test_invalid_identifier_and_cache_ttl_rejected(self):
        for kwargs in ({"project_id": "p`; DROP TABLE x"}, {"dataset_id": "d.x"},
                       {"cache_ttl_seconds": float("inf")}, {"cache_ttl_seconds": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CityDataService(**kwargs)


if __name__ == "__main__":
    unittest.main()
