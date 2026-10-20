"""Actual ADK Runner tests with a fake model/MCP boundary; never call cloud."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.broker import FoodBrokerAgent, parse_candidates, parse_outreach_plan, render_brief


@pytest.fixture(autouse=True)
def offline_context_evidence(monkeypatch):
    class OfflineEvidence:
        async def collect(self, **kwargs):
            return {"status": "unavailable", "foodkeeper": [], "tract_equity": [],
                    "execution_trace": [], "deadline_modified": False}
    monkeypatch.setattr("agent.broker.ContextEvidenceService", OfflineEvidence)


BRIEF = {
    "tone": "concise",
    "confirmation_focus": ["pickup_eta", "storage"],
}
CANDIDATE = {
    "recipient_id": 1, "name": "Fixture Food Bank", "cold_storage": "commercial",
    "max_pallets": 2, "pickup_window_hours": 1, "distance": 0.2,
    "service_days": "daily",
    "profile_source": "planted", "latitude": 47.6, "longitude": -122.3,
}


class ScriptedModel(BaseLlm):
    model: str = "offline-test-model"
    calls: int = 0
    fail_after_tool: bool = False
    skip_tool: bool = False
    invalid_json: bool = False
    final_payload: dict | None = None

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        if self.calls == 1 and not self.skip_tool:
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(name="broker_match", args={}))
            ]))
        else:
            if self.fail_after_tool:
                raise RuntimeError("mock provider unavailable")
            text = "not json" if self.invalid_json else json.dumps(self.final_payload or BRIEF)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


class FakeVectorTool:
    name = "vector_search_recipients"

    def __init__(self, payload=None, error=False):
        self.payload = payload if payload is not None else json.dumps([CANDIDATE])
        self.error = error
        self.calls = []

    async def run_async(self, args, tool_context):
        self.calls.append(args)
        assert tool_context is not None  # It is a real ADK tool context.
        if self.error:
            raise PermissionError("mock denied")
        return self.payload


class FakeToolset:
    def __init__(self, tool):
        self.tool = tool
        self.closed = False

    async def get_tools(self):
        return [self.tool]

    async def close(self):
        self.closed = True


async def execute(model=None, tool=None, **kwargs):
    tool = tool or FakeVectorTool()
    toolset = FakeToolset(tool)
    broker = FoodBrokerAgent(model=model or ScriptedModel(), toolset_factory=lambda: toolset)
    result = await broker.broker_posting_async(
        "A pallet of chilled spinach", hours_remaining=6, **kwargs
    )
    return result, tool, toolset


@pytest.mark.asyncio
async def test_real_adk_function_call_invokes_mcp_boundary_and_gates():
    result, tool, toolset = await execute()
    assert result["status"] == "success", result
    assert result["generation_mode"] == "adk_gemini"
    assert result["brief_generation"] == "deterministic_facts"
    assert result["outreach_mode"] == "gemini_plan"
    assert tool.calls == [{"query_text": "A pallet of chilled spinach"}]
    assert result["winner"]["profile_source"] == "planted"
    assert result["requires_confirmation"] is True
    assert toolset.closed
    stages = [e["stage"] for e in result["execution_trace"]]
    assert "adk_function_call" in stages
    assert "adk_function_result" in stages
    assert stages.index("mcp_call") < stages.index("deterministic_constraints")


@pytest.mark.asyncio
async def test_progress_reports_actual_boundaries_without_offer_or_recipient_data():
    tool = FakeVectorTool()
    toolset = FakeToolset(tool)
    agent = FoodBrokerAgent(model=ScriptedModel(), toolset_factory=lambda: toolset)
    events = []

    async def progress(stage, details):
        if stage in {"connecting", "retrieving"}:
            assert not tool.calls
        if stage in {"checking_constraints", "preparing_brief", "finalizing"}:
            assert len(tool.calls) == 1
        if stage == "finalizing":
            assert toolset.closed
        events.append((stage, details))

    result = await agent.broker_posting_async(
        "A pallet of chilled spinach", progress_callback=progress
    )
    assert result["status"] == "success"
    assert events == [
        ("connecting", {}),
        ("retrieving", {}),
        ("checking_constraints", {"candidate_count": 1}),
        ("enriching_evidence", {}),
        ("preparing_brief", {}),
        ("finalizing", {}),
    ]


@pytest.mark.asyncio
async def test_no_match_progress_never_claims_a_brief_is_being_prepared():
    events = []

    async def progress(stage, details):
        events.append((stage, details))

    result, _, _ = await execute(
        tool=FakeVectorTool(payload="[]"), progress_callback=progress
    )
    assert result["status"] == "no_viable_match"
    assert events == [
        ("connecting", {}),
        ("retrieving", {}),
        ("checking_constraints", {"candidate_count": 0}),
        ("enriching_evidence", {}),
        ("finalizing", {}),
    ]


@pytest.mark.asyncio
async def test_observer_failures_do_not_corrupt_match_or_leak_exception_text(caplog):
    async def progress(stage, details):
        raise RuntimeError("private observer payload")

    result, _, toolset = await execute(progress_callback=progress)
    assert result["status"] == "success"
    assert toolset.closed
    assert "private observer payload" not in caplog.text


@pytest.mark.asyncio
async def test_stalled_observer_is_bounded_and_cancelled(monkeypatch):
    from agent import broker as broker_module
    monkeypatch.setattr(broker_module, "BROKER_PROGRESS_TIMEOUT_SECONDS", 0.01)
    cancelled = []

    async def progress(stage, details):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(stage)

    result, _, toolset = await asyncio.wait_for(
        execute(progress_callback=progress), timeout=1
    )
    await asyncio.sleep(0)
    assert result["status"] == "success"
    assert toolset.closed
    assert cancelled == [
        "connecting", "retrieving", "checking_constraints", "enriching_evidence", "preparing_brief", "finalizing"
    ]


@pytest.mark.asyncio
async def test_operation_budget_includes_tool_discovery(monkeypatch):
    from agent import broker as broker_module
    monkeypatch.setattr(broker_module, "BROKER_OPERATION_TIMEOUT_SECONDS", 0.02)

    class StalledDiscovery(FakeToolset):
        async def get_tools(self):
            await asyncio.Event().wait()

    toolset = StalledDiscovery(FakeVectorTool())
    agent = FoodBrokerAgent(model=ScriptedModel(), toolset_factory=lambda: toolset)
    result = await asyncio.wait_for(
        agent.broker_posting_async("Chilled spinach"), timeout=0.5
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "TimeoutError"
    assert not toolset.tool.calls
    assert toolset.closed


@pytest.mark.asyncio
async def test_stalled_synthesis_returns_clock_checked_draft_within_budget(monkeypatch):
    from agent import broker as broker_module
    monkeypatch.setattr(broker_module, "BROKER_OPERATION_TIMEOUT_SECONDS", 0.1)

    class StalledSynthesis(ScriptedModel):
        async def generate_content_async(self, llm_request, stream=False):
            if self.calls:
                await asyncio.Event().wait()
            async for response in super().generate_content_async(llm_request, stream):
                yield response

    result, tool, toolset = await asyncio.wait_for(
        execute(model=StalledSynthesis()), timeout=1
    )
    assert len(tool.calls) == 1
    assert result["status"] == "draft"
    assert result["outreach_mode"] == "template_fallback"
    assert any(e["stage"] == "dispatch_clock_recheck" for e in result["execution_trace"])
    assert toolset.closed


@pytest.mark.asyncio
async def test_stalled_toolset_cleanup_cannot_hold_completed_result(monkeypatch):
    from agent import broker as broker_module
    monkeypatch.setattr(broker_module, "BROKER_CLEANUP_TIMEOUT_SECONDS", 0.01)

    class StalledCleanup(FakeToolset):
        async def close(self):
            try:
                await asyncio.Event().wait()
            finally:
                self.closed = True

    toolset = StalledCleanup(FakeVectorTool())
    agent = FoodBrokerAgent(model=ScriptedModel(), toolset_factory=lambda: toolset)
    result = await asyncio.wait_for(
        agent.broker_posting_async("Chilled spinach"), timeout=1
    )
    await asyncio.sleep(0)
    assert result["status"] == "success"
    assert toolset.closed


@pytest.mark.asyncio
async def test_cleanup_that_defers_cancellation_cannot_hold_completed_result(monkeypatch):
    from agent import broker as broker_module
    monkeypatch.setattr(broker_module, "BROKER_CLEANUP_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()
    cancellation_received = asyncio.Event()
    closed = asyncio.Event()

    class DeferredCleanup(FakeToolset):
        async def close(self):
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellation_received.set()
                await release.wait()
            finally:
                closed.set()

    toolset = DeferredCleanup(FakeVectorTool())
    agent = FoodBrokerAgent(model=ScriptedModel(), toolset_factory=lambda: toolset)
    try:
        result = await asyncio.wait_for(
            agent.broker_posting_async("Chilled spinach"), timeout=1
        )
        await asyncio.wait_for(cancellation_received.wait(), timeout=0.5)
        assert result["status"] == "success"
        assert not closed.is_set()
    finally:
        release.set()
        await asyncio.wait_for(closed.wait(), timeout=0.5)


@pytest.mark.asyncio
async def test_caller_cancellation_stops_retrieval_and_cleans_up_without_result():
    started = asyncio.Event()
    cancelled = asyncio.Event()
    events = []

    class StalledRetrieval(FakeVectorTool):
        async def run_async(self, args, tool_context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    async def progress(stage, details):
        events.append(stage)

    toolset = FakeToolset(StalledRetrieval())
    agent = FoodBrokerAgent(model=ScriptedModel(), toolset_factory=lambda: toolset)
    task = asyncio.create_task(agent.broker_posting_async(
        "Chilled spinach", progress_callback=progress
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert cancelled.is_set()
    assert toolset.closed
    assert events == ["connecting", "retrieving"]


@pytest.mark.asyncio
async def test_cancellation_during_cleanup_propagates_and_cancels_cleanup():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class StalledCleanup(FakeToolset):
        async def close(self):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    toolset = StalledCleanup(FakeVectorTool())
    agent = FoodBrokerAgent(model=ScriptedModel(), toolset_factory=lambda: toolset)
    task = asyncio.create_task(agent.broker_posting_async("Chilled spinach"))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_denied_mcp_never_fabricates_brief():
    result, _, toolset = await execute(tool=FakeVectorTool(error=True))
    assert result["status"] == "blocked"
    assert "winner" not in result and "match_brief" not in result
    assert toolset.closed


@pytest.mark.asyncio
async def test_model_claim_without_tool_is_rejected():
    result, tool, _ = await execute(model=ScriptedModel(skip_tool=True))
    assert result["status"] == "blocked"
    assert result["error_code"] == "tool_not_invoked"
    assert not tool.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [
    ScriptedModel(fail_after_tool=True), ScriptedModel(invalid_json=True),
])
async def test_generation_failure_is_explicit_draft(model):
    result, _, _ = await execute(model=model)
    assert result["status"] == "draft"
    assert result["generation_mode"] == "deterministic_fallback"
    assert "not been confirmed" in result["match_brief"]["operational_verification"]
    assert "secured" not in result["match_brief"]["operational_verification"]


@pytest.mark.asyncio
async def test_empty_real_retrieval_has_no_winner():
    result, _, _ = await execute(tool=FakeVectorTool(payload="[]"))
    assert result["status"] == "no_viable_match"
    assert result["winner"] is None


@pytest.mark.asyncio
async def test_offer_restrictions_reach_initial_and_final_ranking():
    from agent.matcher import SurplusMatcher

    class RecordingMatcher(SurplusMatcher):
        def __init__(self):
            super().__init__()
            self.offers = []

        def score_and_rank(self, *args, **kwargs):
            self.offers.append(kwargs.get("posting_text"))
            return super().score_and_rank(*args, **kwargs)

    restricted = {**CANDIDATE, "profile_text": "We cannot accept pork or uncertified meat."}
    alternative = {**CANDIDATE, "recipient_id": 2, "name": "General recipient", "distance": .4}
    toolset = FakeToolset(FakeVectorTool(payload=json.dumps([restricted, alternative])))
    matcher = RecordingMatcher()
    agent = FoodBrokerAgent(model=ScriptedModel(), toolset_factory=lambda: toolset, matcher=matcher)
    result = await agent.broker_posting_async("A pallet of chilled pork", hours_remaining=6)
    assert matcher.offers == ["A pallet of chilled pork"] * 2
    assert result["winner"]["recipient_id"] == 2
    assert result["runners_up"] == []
    assert result["eliminated_decoys"][0]["acceptance_check"] == "explicit_conflict"
    assert "recipient_food_restriction" in result["eliminated_decoys"][0]["reason_codes"]


@pytest.mark.asyncio
async def test_certification_dependent_result_stays_pending_without_a_brief():
    restricted = {**CANDIDATE, "profile_text": "Meat must be certified or we cannot distribute it."}
    toolset = FakeToolset(FakeVectorTool(payload=json.dumps([restricted])))
    agent = FoodBrokerAgent(model=ScriptedModel(), toolset_factory=lambda: toolset)
    result = await agent.broker_posting_async("A pallet of certified halal chicken", hours_remaining=6)
    assert result["status"] == "no_viable_match"
    assert "match_brief" not in result
    assert result["winner"] is None
    assert result["pending_confirmation"][0]["acceptance_check"] == "confirmation_required"


@pytest.mark.parametrize("payload", [
    {"error": "denied"}, {"isError": True}, [{"name": "No id"}], "garbage",
    [{"recipient_id": i} for i in range(21)],
])
def test_invalid_mcp_output_is_not_a_candidate(payload):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_candidates(payload)


def test_one_row_and_empty_mcp_encodings():
    assert parse_candidates(json.dumps(CANDIDATE)) == [CANDIDATE]
    assert parse_candidates("null") == []
    assert len(parse_candidates([CANDIDATE, CANDIDATE])) == 1


def test_cloud_runtime_uses_vertex_project_and_region_without_discovery():
    broker = FoodBrokerAgent()
    assert broker.model.client_kwargs == {
        "vertexai": True, "project": "qwiklabs-gcp-04-b992b9561e32", "location": "us-east1",
    }


@pytest.mark.parametrize("row", [
    {"recipient_id": 1},
    {**CANDIDATE, "name": "  "},
    {**CANDIDATE, "max_pallets": float("nan")},
    {**CANDIDATE, "latitude": float("inf")},
])
def test_malformed_candidates_cannot_break_response_serialization(row):
    with pytest.raises(ValueError):
        parse_candidates([row])


def advancing_clock(monkeypatch, elapsed_hours):
    from datetime import datetime, timedelta, timezone
    import agent.broker as broker_module
    start = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    times = iter([start, start, start + timedelta(hours=elapsed_hours)])
    class Clock:
        @staticmethod
        def now(tz=None):
            return next(times)
    monkeypatch.setattr(broker_module, "datetime", Clock)


def test_absolute_deadline_survives_lazy_broker_initialization_delay(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from agent import api as api_module
    import agent.broker as broker_module

    start = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    fixed_deadline = start + timedelta(hours=6)
    clock = {"now": start}

    class Clock:
        @staticmethod
        def now(tz=None):
            return clock["now"]

    agent = FoodBrokerAgent(
        model=ScriptedModel(), toolset_factory=lambda: FakeToolset(FakeVectorTool()),
    )

    def initialize_after_delay():
        clock["now"] = start + timedelta(hours=1)
        return agent

    monkeypatch.setattr(api_module, "datetime", Clock)
    monkeypatch.setattr(broker_module, "datetime", Clock)
    monkeypatch.setattr(api_module, "get_broker_agent", initialize_after_delay)
    response = TestClient(api_module.app).post("/api/match", json={
        "posting_text": "Chilled spinach", "required_storage": "cold", "pallet_count": 1,
        "hours_remaining": 6, "deadline_at": fixed_deadline.isoformat(),
        "donor_name": "Fixture grocer", "donor_lat": 47.6, "donor_lon": -122.3,
    })
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "success"
    assert result["donor"]["deadline_at"] == fixed_deadline.isoformat()
    assert result["donor"]["hours_remaining"] == 5
    assert result["winner"]["time_margin_hours"] == 4
    assert result["winner"]["deadline_at"] == fixed_deadline.isoformat()


@pytest.mark.asyncio
async def test_offer_expiring_during_synthesis_is_no_longer_a_match(monkeypatch):
    advancing_clock(monkeypatch, elapsed_hours=7)
    result, _, _ = await execute()
    assert result["status"] == "no_viable_match"
    assert result["winner"] is None
    assert result["donor"]["hours_remaining"] == 0
    assert "match_brief" not in result
    assert result["execution_trace"][-1]["stage"] == "dispatch_clock_recheck"


@pytest.mark.asyncio
async def test_changed_winner_gets_fresh_labeled_draft_not_old_model_brief(monkeypatch):
    advancing_clock(monkeypatch, elapsed_hours=5)
    backup = {**CANDIDATE, "recipient_id": 2, "name": "Backup Food Bank", "distance": 0.5, "pickup_window_hours": 0.25}
    result, _, _ = await execute(tool=FakeVectorTool(payload=json.dumps([CANDIDATE, backup])))
    assert result["status"] == "draft"
    assert result["winner"]["recipient_id"] == 2
    assert result["generation_mode"] == "deterministic_fallback"
    assert "Backup Food Bank" in result["match_brief"]["match_justification"]
    assert "clock changed" in result["message"]


def render_fixture():
    winner = {
        **CANDIDATE, "name": "Lynnwood Food Bank", "pickup_window_hours": 5,
        "time_margin_hours": 7, "tract_id": "fixture-tract",
        "tract_poverty_pct": 12.4, "tract_no_vehicle_pct": 8.2,
    }
    ranking = {
        "winner": winner, "min_buffer_hours": 0.25,
        "viable_runners_up": [{"name": "Eligible Backup"}],
        "eliminated_decoys": [{"name": "Edmonds Food Bank", "elimination_reasons": ["Closed on the pickup day"]}],
    }
    donor = {
        "name": "Fixture Grocer", "posting_text": "Sealed shelf-stable bread",
        "pallet_count": 0.5, "required_storage": "ambient",
        "hours_remaining": 12, "deadline_at": "2026-10-21T03:00:00+00:00",
    }
    return ranking, donor


def test_brief_facts_use_final_candidates_and_exact_buffer_math():
    ranking, donor = render_fixture()
    brief = render_brief(ranking, donor, BRIEF)
    justification = brief["match_justification"]
    assert "Eligible alternatives, in order: Eligible Backup." in justification
    assert "Excluded: Edmonds Food Bank — Closed on the pickup day." in justification
    eligible_text = justification.split("Eligible alternatives, in order: ", 1)[1].split(".", 1)[0]
    assert "Edmonds" not in eligible_text
    clock = brief["clock_action_plan"]
    assert "Raw deadline margin before buffer: 7.00h" in clock
    assert "Required coordination buffer: 0.25h" in clock
    assert "margin after subtracting that buffer: 6.75h" in clock
    assert donor["deadline_at"] in clock
    assert "12.40% poverty" in brief["equity_rationale"]
    assert "8.20% households without a vehicle" in brief["equity_rationale"]


@pytest.mark.parametrize("tone", ["concise", "warm"])
def test_every_outreach_keeps_all_confirmations_and_bounded_sms(tone):
    from agent.broker import CONFIRMATION_QUESTIONS
    ranking, donor = render_fixture()
    brief = render_brief(ranking, donor, {"tone": tone, "confirmation_focus": ["pickup_eta"]})
    for question in CONFIRMATION_QUESTIONS.values():
        assert question in brief["drafted_email_outreach"]
    sms = brief["drafted_sms_outreach"]
    assert len(sms) <= 480
    assert ranking["winner"]["name"] in sms and donor["name"] in sms
    assert "0.50 pallets; ambient storage" in sms
    assert donor["deadline_at"] in sms
    for essential in ["acceptance", "storage", "capacity", "pickup ETA", "receiving hours", "transport"]:
        assert essential in sms
    assert "Full names and offer in email" in sms
    assert "multiple SMS segments" in brief["sms_delivery_note"]
    assert "has not been sent" in brief["drafted_email_outreach"]
    assert "Monday" not in " ".join(brief.values())
    assert "Tuesday" not in " ".join(brief.values())


def test_long_sms_names_are_explicitly_shortened_without_losing_offer_or_questions():
    ranking, donor = render_fixture()
    ranking["winner"]["name"] = "A very long recipient organization name " * 10
    donor["name"] = "A very long donor grocer name " * 6
    donor["pallet_count"] = 10000
    donor["deadline_at"] = "2026-10-21T03:00:00.123456+00:00"
    donor["posting_text"] = "Unverified free-form offer must remain in the email."
    brief = render_brief(ranking, donor, {"tone": "warm", "confirmation_focus": ["pickup_eta"]})
    sms = brief["drafted_sms_outreach"]
    assert len(sms) <= 480
    assert sms.count("…") == 2
    assert donor["deadline_at"] in sms
    assert "10000.00 pallets; ambient storage" in sms
    assert "receiving hours and transport" in sms
    assert donor["posting_text"] not in sms
    email = brief["drafted_email_outreach"]
    assert ranking["winner"]["name"] in email and donor["name"] in email
    assert donor["posting_text"] in email


def test_missing_alternatives_and_indicators_are_not_invented():
    ranking, donor = render_fixture()
    ranking["viable_runners_up"] = []
    ranking["winner"]["tract_poverty_pct"] = None
    ranking["winner"]["tract_no_vehicle_pct"] = None
    brief = render_brief(ranking, donor)
    assert "No other eligible alternatives" in brief["match_justification"]
    assert "No usable tract indicators" in brief["equity_rationale"]
    assert "0.00%" not in brief["equity_rationale"]


@pytest.mark.parametrize("payload", [
    {"tone": "warm on Monday October20", "confirmation_focus": ["acceptance"]},
    {"tone": "warm", "confirmation_focus": ["Edmonds Food Bank"]},
    {"tone": "concise", "confirmation_focus": ["pickup_eta"], "drafted_email_outreach": "Pickup secured tomorrow Tuesday"},
    {"tone": "warm", "confirmation_focus": []},
    {"tone": "warm", "confirmation_focus": ["storage"] * 7},
])
def test_unbounded_model_facts_cannot_cross_outreach_contract(payload):
    with pytest.raises(ValueError):
        parse_outreach_plan(json.dumps(payload))


@pytest.mark.asyncio
async def test_hallucinated_brief_is_replaced_with_safe_factual_renderer():
    hallucinated = {
        "match_justification": "Edmonds is a viable alternative",
        "operational_verification": "Verified capacity secured",
        "clock_action_plan": "Monday October20 2026; 7h margin includes buffer",
        "equity_rationale": "Invented 99% poverty",
        "drafted_sms_outreach": "Confirmed delivery tomorrow Tuesday",
        "drafted_email_outreach": "We booked Edmonds. No confirmation needed.",
    }
    result, _, _ = await execute(model=ScriptedModel(final_payload=hallucinated))
    assert result["status"] == "draft"
    assert result["outreach_mode"] == "template_fallback"
    text = " ".join(result["match_brief"].values())
    for unsafe in ["Edmonds", "Monday October20", "tomorrow Tuesday", "99%", "Verified capacity"]:
        assert unsafe not in text
    assert "Fixture Food Bank" in text
    assert result["execution_trace"][-1]["facts_source"] == "final_deterministic_ranking"
