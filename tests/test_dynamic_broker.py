"""No cloud: execute actual ADK orchestration across evidence and final clock."""
import json
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from google.genai import _transformers
from pydantic import ValidationError

from agent import free_text_intake as intake
from agent.brief_synthesis import evidence_cards, literal_food_terms, selected_cards, personalized_sms
from agent.broker import FoodBrokerAgent
from agent.foodkeeper import FoodKeeperService


NOW = datetime(2026, 10, 20, 19, tzinfo=timezone.utc)
POSTING = "Two pallets of spinach, cold storage required. Pickup within 3 hours before the compactor."
EXTRACTED = {
    "food_terms": ["spinach"], "required_storage": "cold", "storage_quote": "cold storage required",
    "pallet_count": 2, "quantity_quote": "Two pallets",
    "pickup_window_hours": 3, "deadline_quote": "Pickup within 3 hours before the compactor",
}


def test_exact_extraction_keeps_original_deadline_and_never_converts_units():
    existing = NOW + timedelta(hours=1)
    data = intake.validated_draft(EXTRACTED, POSTING, NOW, existing)
    assert data["fields"]["deadline_at"] == existing.isoformat()
    assert data["fields"]["pallet_count"] == 2
    assert data["food_terms"] == ["spinach"]
    assert data["requires_review"] and not data["sent"]
    forged = {**EXTRACTED, "pallet_count": 20, "quantity_quote": "20 crates"}
    data = intake.validated_draft(forged, POSTING + " and 20 crates", NOW)
    assert "pallet_count" not in data["fields"]
    assert "converted" in " ".join(data["questions"])


@pytest.mark.parametrize("quote", ["expires in 3 hours", "pickup within 3 hours; expires in 3 hours"])
def test_shelf_life_never_becomes_pickup_clock(quote):
    data = intake.validated_draft({**EXTRACTED, "deadline_quote": quote}, quote + " " + POSTING, NOW)
    assert "deadline_at" not in data["fields"]


def test_clock_time_is_reviewed_without_inventing_date_or_timezone():
    text = "Two pallets spinach. Compactor at 4pm."
    data = intake.validated_draft({
        "food_terms": ["spinach"], "deadline_quote": "Compactor at 4pm.",
    }, text, NOW)
    assert data["quoted_evidence"]["pickup_deadline"] == "Compactor at 4pm."
    assert "deadline_at" not in data["fields"]
    assert "required_storage" not in data["fields"]


def test_unquoted_products_and_storage_are_not_accepted():
    data = intake.validated_draft({
        "food_terms": ["milk", "spinach", "https://evil.example"],
        "required_storage": "cold", "storage_quote": "cold storage",
    }, "We have spinach.", NOW)
    assert data["food_terms"] == ["spinach"]
    assert "required_storage" not in data["fields"]
    assert literal_food_terms(["pin", "spinach", "SPINACH"], "spinach") == ["spinach"]


@pytest.mark.parametrize("posting,raw,field", [
    ("No refrigeration required.", {"required_storage":"cold","storage_quote":"refrigeration required"}, "required_storage"),
    ("We do not have 2 pallets.", {"pallet_count":2,"quantity_quote":"2 pallets"}, "pallet_count"),
    ("Pickup cannot happen within 2 hours.", {"pickup_window_hours":2,"deadline_quote":"within 2 hours"}, "deadline_at"),
])
def test_clipped_affirmative_quote_cannot_hide_negated_statement(posting, raw, field):
    assert field not in intake.validated_draft(raw, posting, NOW)["fields"]


@pytest.mark.parametrize("posting", ["2 pallets of bread; no milk or eggs.", "Bread contains milk.", "Milk-free bread."])
def test_product_mentions_do_not_assert_negated_goods_or_ingredients(posting):
    assert literal_food_terms(["milk"], posting) == []


class ExtractModel(BaseLlm):
    model: str = "offline"
    async def generate_content_async(self, llm_request, stream=False):
        # Exercise the actual installed SDK's Vertex schema conversion. A fake
        # model alone previously missed exclusiveMinimum failing before HTTP.
        schema = _transformers.t_schema(SimpleNamespace(vertexai=True), llm_request.config.response_schema)
        assert schema.properties["pallet_count"].minimum == 0
        assert schema.properties["pickup_window_hours"].minimum == 0
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(EXTRACTED))]))


@pytest.mark.parametrize("field", ["pallet_count", "pickup_window_hours"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), float("-inf"), "2", True])
def test_vertex_compatible_schema_keeps_strict_positive_amount_validation(field, value):
    with pytest.raises(ValidationError):
        intake.ExtractedAlert.model_validate({field: value})


@pytest.mark.parametrize("field", ["pallet_count", "pickup_window_hours"])
def test_positive_fraction_and_missing_amount_remain_valid(field):
    assert getattr(intake.ExtractedAlert.model_validate({field: 0.5}), field) == 0.5
    assert getattr(intake.ExtractedAlert.model_validate({field: None}), field) is None


@pytest.mark.asyncio
async def test_real_adk_extracts_using_structured_schema_without_any_tools():
    service = intake.FreeTextIntake(model=ExtractModel())
    result = await service.parse(intake.AlertRequest(posting_text=POSTING, deadline_at=NOW))
    assert result["status"] == "review_required"
    assert result["fields"]["deadline_at"] == NOW.isoformat()
    assert result["quoted_evidence"]["pallet_count"] == "Two pallets"


def test_parse_endpoint_reports_failure_without_disclosing_provider_details(monkeypatch, caplog):
    class Failed:
        async def parse(self, request):
            raise RuntimeError("private provider details")
    app = FastAPI()
    app.include_router(intake.router)
    monkeypatch.setattr(intake, "get_intake", lambda: Failed())
    response = TestClient(app).post("/api/intake/parse", json={"posting_text": POSTING})
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in response.text and response.json()["sent"] is False
    assert "surplus_intake_failed category=provider_or_runtime" in caplog.text
    assert "private provider details" not in caplog.text


ROWS = [
    {"recipient_id": 1, "name": "Recipient A", "cold_storage": "commercial", "max_pallets": 3,
     "pickup_window_hours": 1, "distance": .1, "service_days": "daily", "profile_source": "generated",
     "tract_id": "53033000100", "tract_poverty_rate": .2, "tract_no_vehicle_rate": .1},
    {"recipient_id": 2, "name": "Recipient B", "cold_storage": "commercial", "max_pallets": 5,
     "pickup_window_hours": 2, "distance": .6, "service_days": "daily", "profile_source": "generated",
     "tract_id": "53033000200", "tract_poverty_rate": .1, "tract_no_vehicle_rate": .1},
]


class Evidence:
    def __init__(self):
        self.calls = []
    async def collect(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "ready", "deadline_modified": False,
            "foodkeeper": [{"request": {"query": "spinach"}, "status": "ready",
                           "data": FoodKeeperService().search("spinach", limit=5)}],
            "tract_equity": [{"request": {"tract_id": ROWS[0]["tract_id"]}, "status": "ready",
                             "data": {"poverty_rate": .2, "no_vehicle_rate": .1}}],
            "execution_trace": [
                {"stage": "context_mcp_call", "tool": "get_tract_equity"},
                {"stage": "context_mcp_call", "tool": "lookup_shelf_life"},
            ],
        }


class Vector:
    name = "vector_search_recipients"
    async def run_async(self, args, tool_context):
        return ROWS


class Tools:
    async def get_tools(self):
        return [Vector()]
    async def close(self):
        pass


class ReasoningModel(BaseLlm):
    model: str = "offline"
    calls: int = 0
    evidence_seen: bool = False
    duplicate: bool = False
    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        if self.calls == 1:
            parts = [
                types.Part(function_call=types.FunctionCall(
                    name="broker_match", args={"food_terms": ["spinach", "invented milk"]}))
            ]
            yield LlmResponse(content=types.Content(role="model", parts=parts * (2 if self.duplicate else 1)))
        else:
            text = json.dumps([part.function_response.response for content in llm_request.contents
                               for part in content.parts or [] if part.function_response])
            self.evidence_seen = "FoodKeeper" in text and "tract_equity" in text and "alternative.0.capacity" in text
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps({
                "tone": "warm", "confirmation_focus": ["storage", "pickup_eta"],
                "evidence_order": ["alternative.0.capacity", "winner.clock", "shelf.0"],
            }))]))


@pytest.mark.asyncio
async def test_three_tool_pipeline_uses_real_adk_and_fresh_facts_for_personalized_handoff():
    evidence, model = Evidence(), ReasoningModel()
    deadline = datetime.now(timezone.utc) + timedelta(hours=3)
    broker = FoodBrokerAgent(model=model, toolset_factory=Tools, evidence_service=evidence)
    result = await broker.broker_posting_async(POSTING, pallet_count=2, hours_remaining=3, deadline_at=deadline)
    assert result["status"] == "success", result
    assert model.evidence_seen
    assert evidence.calls == [{"food_terms": ["spinach"], "candidate_tract_ids": ["53033000100", "53033000200"]}]
    assert result["donor"]["deadline_at"] == deadline.isoformat()
    assert result["donor"]["hours_remaining"] < 3
    assert "lower profile capacity" in result["match_brief"]["match_justification"]
    sms = result["match_brief"]["drafted_sms_outreach"]
    assert "spinach" in sms and "Can you maintain the required storage?" in sms
    assert "nothing booked" in sms and len(sms) <= 480
    assert len(result["synthesis_evidence"]) == 3
    assert {e.get("tool") for e in result["execution_trace"] if e["stage"].endswith("mcp_call")} == {
        "vector_search_recipients", "get_tract_equity", "lookup_shelf_life"}


@pytest.mark.asyncio
async def test_evidence_outage_cannot_erase_candidates_or_extend_deadline():
    class Failed(Evidence):
        async def collect(self, **kwargs):
            raise TimeoutError()
    deadline = datetime.now(timezone.utc) + timedelta(hours=3)
    broker = FoodBrokerAgent(model=ReasoningModel(), toolset_factory=Tools, evidence_service=Failed())
    result = await broker.broker_posting_async(POSTING, pallet_count=2, hours_remaining=3, deadline_at=deadline)
    assert result["winner"]["recipient_id"] == 1
    assert result["status"] == "draft"  # Requested shelf card cannot be fabricated.
    assert result["context_evidence"]["status"] == "unavailable"
    assert result["donor"]["deadline_at"] == deadline.isoformat()
    assert "FoodKeeper options" not in result["match_brief"]["match_justification"]


@pytest.mark.asyncio
async def test_duplicate_adk_calls_share_one_bounded_retrieval_and_evidence_pass():
    calls = []
    class SlowVector(Vector):
        async def run_async(self, args, tool_context):
            calls.append(args)
            await asyncio.sleep(.01)
            return ROWS
    class SlowTools(Tools):
        async def get_tools(self):
            return [SlowVector()]
    evidence = Evidence()
    result = await FoodBrokerAgent(model=ReasoningModel(duplicate=True), toolset_factory=SlowTools,
                                   evidence_service=evidence).broker_posting_async(POSTING, pallet_count=2)
    assert result["status"] == "success"
    assert len(calls) == len(evidence.calls) == 1
    assert len([e for e in result["execution_trace"] if e["stage"] == "mcp_call"]) == 1


def test_synthesis_rejects_nonexistent_evidence_and_never_uses_missing_as_zero():
    cards = evidence_cards({"winner": ROWS[0]}, {"tract_equity": [{
        "status": "ready", "request": {"tract_id": ROWS[0]["tract_id"]},
        "data": {"poverty_rate": None, "no_vehicle_rate": 0},
    }]})
    text = next(c["text"] for c in cards if c["id"] == "tract.0")
    assert "poverty rate unknown" in text and "no-vehicle rate 0.00" in text
    with pytest.raises(ValueError):
        selected_cards({"evidence_order": ["forged"]}, cards)
