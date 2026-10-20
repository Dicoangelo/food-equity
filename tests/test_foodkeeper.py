"""Reference fidelity tests; no cloud calls or shelf-life arithmetic."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.foodkeeper import DEFAULT_DATA_PATH, FoodKeeperService, STORAGE_FIELDS


@pytest.fixture(scope="module")
def service():
    return FoodKeeperService()


def advice_by_field(product):
    return {item["raw_field"]: item for item in product["storage_advice"]}


def test_lettuce_keeps_ambiguous_subtypes_and_never_selects(service):
    result = service.search("Lettuce")
    products = {p["product_id"]: p for p in result["products"]}
    assert {"290", "291"} <= products.keys()
    assert products["290"]["subtitle"] == "iceberg, romaine"
    assert products["291"]["subtitle"] == "leaf, spinach"
    assert result["selection_required"] is True
    assert result["selected_product_id"] is None
    assert result["remaining_freshness_hours"] is None
    assert result["donor_deadline_modified"] is False


def test_spinach_id_and_original_purchase_and_freeze_guidance(service):
    result = service.search("spinach")
    assert result["selected_product_id"] is None
    product = next(p for p in result["products"] if p["product_id"] == "291")
    advice = advice_by_field(product)
    purchase = advice["from_date_of_purchase_refrigerate_output_display_only"]
    assert purchase == {
        "storage": "refrigerated", "basis": "from_purchase", "kind": "guidance",
        "raw_field": "from_date_of_purchase_refrigerate_output_display_only",
        "raw_value": "3 - 7 Days",
    }
    frozen = advice["freeze_output_display_only"]
    assert frozen["raw_value"] == "Not Recommended"
    assert frozen["storage"] == "frozen" and frozen["basis"] == "unspecified"
    assert product["raw_storage_fields"]["refrigerate_after_opening_output_display_only"] is None
    assert product["remaining_freshness_hours"] is None


def test_formula_after_opening_is_never_presented_as_sealed_pantry_duration(service):
    result = service.get_product("369")
    assert result["selected_product_id"] == "369"
    product = result["products"][0]
    assert product["name"] == "Liquid concentrate or ready-to-feed formula"
    assert product["subtitle"] is None
    advice = advice_by_field(product)
    assert advice["refrigerate_after_opening_output_display_only"] == {
        "storage": "refrigerated", "basis": "after_opening", "kind": "guidance",
        "raw_field": "refrigerate_after_opening_output_display_only", "raw_value": "2 Days",
    }
    assert advice["pantry_tips"]["raw_value"] == "Should be discarded after use-by date."
    assert advice["pantry_tips"]["basis"] == "unspecified"
    assert not any(item["storage"] == "pantry" and item["kind"] == "guidance" for item in advice.values())
    assert product["applicability"] == "unconfirmed_product_state_and_history"
    assert result["remaining_freshness_hours"] is None


def test_all_records_preserve_every_raw_storage_field_and_value(service):
    records = json.loads(DEFAULT_DATA_PATH.read_text())["product_data"]
    assert len(records) == 661
    for original in records:
        product = service.get_product(original["id"])["products"][0]
        assert product["product_id"] == original["id"]
        assert product["subtitle"] == original["name_subtitle"]
        assert product["raw_storage_fields"] == {key: original.get(key) for key in STORAGE_FIELDS}
        assert {key for key in original if key.endswith("_tips") or key.endswith("_output_display_only")} == set(STORAGE_FIELDS)
        expected = {key: value for key, value in product["raw_storage_fields"].items() if value not in (None, "")}
        actual = {item["raw_field"]: item["raw_value"] for item in product["storage_advice"]}
        assert actual == expected
        for item in product["storage_advice"]:
            key = item["raw_field"]
            expected_basis = (
                "from_purchase" if key.startswith("from_date_of_purchase_") else
                "after_opening" if "_after_opening_" in key else
                "after_thawing" if "_after_thawing_" in key else "unspecified"
            )
            assert item["basis"] == expected_basis


def test_no_fuzzy_guess_and_no_automatic_selection_for_single_result(service):
    assert service.search("spinnach")["products"] == []
    ambiguous = service.search("leaf spinach")
    assert {p["product_id"] for p in ambiguous["products"]} == {"291", "415"}
    result = service.search("iceberg romaine")
    assert [p["product_id"] for p in result["products"]] == ["290"]
    assert result["selection_required"] is True
    assert result["selected_product_id"] is None
    assert service.search("spinach milk")["status"] == "no_match"


def test_case_punctuation_and_bounds_are_deterministic(service):
    assert service.search("  LeAf, SPINACH  ")["products"] == service.search("leaf spinach")["products"]
    broad = service.search("milk", limit=1)
    assert len(broad["products"]) == 1
    assert broad["total_matches"] > 1 and broad["truncated"] is True
    assert len(service.search("milk", limit=10)["products"]) <= 10
    assert broad["match_method"] == "all_query_terms_literal"


@pytest.mark.parametrize("query", ["", " ", "a", "x" * 121, "!!!", None, 291])
def test_invalid_search_queries_rejected(service, query):
    with pytest.raises(ValueError):
        service.search(query)


@pytest.mark.parametrize("limit", [0, 11, -1, True, 1.5, "5"])
def test_invalid_limits_rejected(service, limit):
    with pytest.raises(ValueError):
        service.search("milk", limit=limit)


@pytest.mark.parametrize("product_id", ["../291", " 291", "291 ", "spinach", "1" * 7, 291, None])
def test_only_exact_product_ids_are_accepted(service, product_id):
    with pytest.raises(ValueError):
        service.get_product(product_id)


def test_unknown_id_is_not_substituted_and_unknown_inputs_remain_visible(service):
    result = service.get_product("999999")
    assert result["status"] == "not_found" and result["products"] == []
    assert result["selected_product_id"] is None
    unknowns = " ".join(result["unknowns_required"])
    for required in ["Product state", "timestamps", "Package label", "use-by", "Storage and temperature history"]:
        assert required in unknowns
    assert "not measured remaining freshness" in result["advisory_notice"]
    assert "donor's pickup deadline" in result["advisory_notice"]


def test_result_mutation_cannot_change_committed_reference(service):
    first = service.get_product("291")
    first["products"][0]["raw_storage_fields"]["freeze_output_display_only"] = "safe forever"
    first["products"][0]["storage_advice"][0]["raw_value"] = "invented"
    first["unknowns_required"].clear()
    first["source"]["sha256"] = "changed"
    again = service.get_product("291")
    assert again["products"][0]["raw_storage_fields"]["freeze_output_display_only"] == "Not Recommended"
    assert again["products"][0]["storage_advice"][0]["raw_value"] != "invented"
    assert len(again["unknowns_required"]) == 5
    assert len(again["source"]["sha256"]) == 64
