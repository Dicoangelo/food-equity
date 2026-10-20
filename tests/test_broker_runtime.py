"""Actual ADK Runner tests with a fake model/MCP boundary; never call cloud."""
import json
import sys
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.broker import FoodBrokerAgent, parse_candidates


BRIEF = {
    "match_justification": "Profile-based proposal.",
    "operational_verification": "Confirm current capacity before dispatch.",
    "clock_action_plan": "Confirm ETA.",
    "equity_rationale": "Dataset indicators are approximate.",
    "drafted_sms_outreach": "Can you accept this surplus?",
    "drafted_email_outreach": "Please confirm storage and pickup.",
}
CANDIDATE = {
    "recipient_id": 1, "name": "Fixture Food Bank", "cold_storage": "commercial",
    "max_pallets": 2, "pickup_window_hours": 1, "distance": 0.2,
    "service_days": "Monday Tuesday Wednesday Thursday Friday Saturday Sunday",
    "profile_source": "planted", "latitude": 47.6, "longitude": -122.3,
}


class ScriptedModel(BaseLlm):
    model: str = "offline-test-model"
    calls: int = 0
    fail_after_tool: bool = False
    skip_tool: bool = False
    invalid_json: bool = False

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        if self.calls == 1 and not self.skip_tool:
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(name="broker_match", args={}))
            ]))
        else:
            if self.fail_after_tool:
                raise RuntimeError("mock provider unavailable")
            text = "not json" if self.invalid_json else json.dumps(BRIEF)
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


async def execute(model=None, tool=None):
    tool = tool or FakeVectorTool()
    toolset = FakeToolset(tool)
    broker = FoodBrokerAgent(model=model or ScriptedModel(), toolset_factory=lambda: toolset)
    result = await broker.broker_posting_async("A pallet of chilled spinach", hours_remaining=6)
    return result, tool, toolset


@pytest.mark.asyncio
async def test_real_adk_function_call_invokes_mcp_boundary_and_gates():
    result, tool, toolset = await execute()
    assert result["status"] == "success", result
    assert result["generation_mode"] == "adk_gemini"
    assert tool.calls == [{"query_text": "A pallet of chilled spinach"}]
    assert result["winner"]["profile_source"] == "planted"
    assert result["requires_confirmation"] is True
    assert toolset.closed
    stages = [e["stage"] for e in result["execution_trace"]]
    assert "adk_function_call" in stages
    assert "adk_function_result" in stages
    assert stages.index("mcp_call") < stages.index("deterministic_constraints")


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
