"""Offline MCP reference checks; no Google, model, HTTP or ADC calls."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import ipaddress
import json
from pathlib import Path

import pytest
import yaml

from agent import context_evidence as evidence
from agent.foodkeeper import FoodKeeperService

TRACT = {
    "tract_id": "53033000100", "tract_lat": 47.6, "tract_lon": -122.3,
    "total_pop": 3000, "median_income": 45000, "poverty_rate": .2,
    "no_vehicle_rate": .1, "assistance_rate": None, "need_score": .15,
}
REFERENCE = FoodKeeperService()
SECRET = "RAW_PROVIDER_SECRET_MUST_NOT_LEAK"


class FakeTool:
    def __init__(self, name, *, payload=None, error=None, gate=None):
        self.name, self.payload, self.error, self.gate = name, payload, error, gate
        self.calls = []
        self.started = asyncio.Event()
        self.cancelled = 0

    async def run_async(self, args, tool_context):
        assert tool_context is None
        self.calls.append(deepcopy(args))
        self.started.set()
        try:
            if self.gate:
                await self.gate.wait()
            if self.error:
                raise self.error
            if self.payload is not None:
                return deepcopy(self.payload)
            if self.name == "lookup_shelf_life":
                return REFERENCE.search(args["query"])
            return [{**TRACT, "tract_id": args["tract_id"]}]
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class FakeToolset:
    def __init__(self, tools=None, *, discovery_gate=None, close_gate=None):
        self.tools = tools if tools is not None else [FakeTool(name) for name in evidence.TOOLS]
        self.discovery_gate, self.close_gate = discovery_gate, close_gate
        self.discovery_started = asyncio.Event()
        self.discovery_cancelled = False
        self.closed = False
        self.close_cancelled = False

    async def get_tools(self):
        self.discovery_started.set()
        try:
            if self.discovery_gate:
                await self.discovery_gate.wait()
            return self.tools
        except asyncio.CancelledError:
            self.discovery_cancelled = True
            raise

    async def close(self):
        self.closed = True
        try:
            if self.close_gate:
                await self.close_gate.wait()
        except asyncio.CancelledError:
            self.close_cancelled = True
            raise


@pytest.mark.asyncio
async def test_successful_reads_are_actual_mcp_results_with_pinned_reference_and_unknown_observation():
    tools = FakeToolset()
    terms, ids = ["bread"], [TRACT["tract_id"]]
    before = deepcopy((terms, ids))
    started = datetime.now(timezone.utc)
    result = await evidence.ContextEvidenceService(lambda: tools).collect(
        food_terms=terms, candidate_tract_ids=ids)
    assert result["status"] == "ready" and result["deadline_modified"] is False
    assert (terms, ids) == before and tools.closed
    assert tools.tools[0].calls == [{"query": "bread"}]
    assert tools.tools[1].calls == [{"tract_id": TRACT["tract_id"]}]
    food, tract = result["foodkeeper"][0], result["tract_equity"][0]
    assert food["source"]["sha256"] == hashlib.sha256(
        Path("data/foodkeeper.json").read_bytes()).hexdigest() == evidence.FOODKEEPER_SHA256
    assert food["data"]["products"] == REFERENCE.search("bread")["products"]
    assert food["data"]["remaining_freshness_hours"] is None
    assert food["data"]["donor_deadline_modified"] is False
    assert tract["data"]["need_score"] == .15
    assert tract["data"]["assistance_rate"] is None
    assert tract["data"]["observation_year"] is None
    assert tract["data"]["service_catchment_verified"] is False
    for item in (food, tract):
        assert item["source"]["transport"] == "mcp" and item["source"]["source_observed_at"] is None
        assert item["requires_confirmation"] and not item["operational_data_verified"]
        assert datetime.fromisoformat(item["retrieved_at"]) >= started
    calls = [row for row in result["execution_trace"] if row["stage"] == "context_mcp_call"]
    assert {row["tool"] for row in calls} == set(evidence.TOOLS)
    assert all(len(row["request_sha256"]) == 64 for row in calls)
    assert not any(row["stage"] == "mcp_call" for row in result["execution_trace"])
    assert not ({"winner", "deadline_at", "donor", "match_brief"} & result.keys())
    assert not ({"deadline_at", "offer", "ranking", "dispatch"} &
                set(inspect.signature(evidence.ContextEvidenceService.collect).parameters))


@pytest.mark.asyncio
async def test_caps_deduplicates_and_preserves_literal_query_without_model_url_parameters():
    tools = FakeToolset()
    terms = ["bread", " BREAD ", "milk", "bread &url=http://169.254.169.254/", "apples"]
    ids = ["53033000100", "53033000100", "53033000200", "53033000300", "53033000400"]
    result = await evidence.ContextEvidenceService(lambda: tools).collect(
        food_terms=terms, candidate_tract_ids=ids)
    assert len(result["foodkeeper"]) == len(result["tract_equity"]) == 3
    assert tools.tools[0].calls == [{"query": term} for term in [terms[0], terms[2], terms[3]]]
    assert tools.tools[1].calls == [{"tract_id": value} for value in dict.fromkeys(ids[:4])]
    assert set(result["warnings"]) == {"food_terms_capped", "tract_ids_capped"}
    assert result["status"] == "partial"
    assert result["foodkeeper"][-1]["status"] == "no_match"


@pytest.mark.asyncio
async def test_invalid_inputs_do_not_invoke_discovery_or_echo_invalid_payloads():
    def forbidden_factory():
        pytest.fail("No tools needed for invalid or empty inputs")
    service = evidence.ContextEvidenceService(forbidden_factory)
    result = await service.collect(food_terms=[SECRET * 20, None, "x", "milk\nhttp"],
                                   candidate_tract_ids=["../anything", "17033000100", True])
    assert result["status"] == "unknown"
    assert result["foodkeeper"] == result["tract_equity"] == result["execution_trace"] == []
    assert SECRET not in json.dumps(result)
    assert (await service.collect())["status"] == "not_requested"
    assert (await service.collect(food_terms="bread"))["status"] == "unknown"


@pytest.mark.parametrize("wrapper", [
    lambda value: value,
    json.dumps,
    lambda value: {"result": value},
    lambda value: {"content": [{"type": "text", "text": json.dumps(value)}]},
])
def test_http_toolbox_envelopes_retain_actual_pinned_foodkeeper_response(wrapper):
    status, data = evidence.normalize_foodkeeper(wrapper(REFERENCE.search("yogurt")), "yogurt")
    assert status == "ready" and len(data["products"]) == 1
    assert data["products"][0]["applicability"] == "unconfirmed_product_state_and_history"


@pytest.mark.parametrize("mutate", [
    lambda b: b["source"].update(sha256="untrusted"),
    lambda b: b.update(remaining_freshness_hours=72),
    lambda b: b.update(donor_deadline_modified=True),
    lambda b: b.update(selection_required=False),
    lambda b: b.update(query="milk"),
    lambda b: b["products"][0].update(remaining_freshness_hours=24),
    lambda b: b["products"][0]["storage_advice"][0].update(raw_value="Safe for 30 more days"),
    lambda b: b["products"][0].update(phone=SECRET),
])
def test_foodkeeper_rejects_hash_or_guidance_substitution(mutate):
    body = REFERENCE.search("bread")
    mutate(body)
    with pytest.raises(evidence.InvalidEvidence):
        evidence.normalize_foodkeeper(body, "bread")


def test_foodkeeper_no_match_is_reference_absence_not_freshness_or_safety():
    status, data = evidence.normalize_foodkeeper(REFERENCE.search("zznotfound"), "zznotfound")
    assert status == "no_match" and data["products"] == []
    assert data["remaining_freshness_hours"] is None and data["selection_required"]


@pytest.mark.parametrize("payload", [
    {"isError": True, "content": [{"type": "text", "text": SECRET}]},
    json.dumps({"error": SECRET}),
])
@pytest.mark.asyncio
async def test_provider_tool_errors_never_fall_back_to_local_reference_or_leak(payload):
    tools = FakeToolset([FakeTool("lookup_shelf_life", payload=payload)])
    result = await evidence.ContextEvidenceService(lambda: tools).collect(food_terms=["bread"])
    assert result["status"] == "unavailable"
    assert result["foodkeeper"][0]["data"] is None
    assert result["foodkeeper"][0]["code"] == "context_lookup_unavailable"
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("wrapper", [
    lambda rows: rows, json.dumps, lambda rows: {"rows": rows},
    lambda rows: {"result": {"rows": rows}},
    lambda rows: {"content": [{"type": "text", "text": json.dumps(rows)}]},
])
def test_tract_envelopes_exact_id_and_null_rates(wrapper):
    row = {**TRACT, "poverty_rate": None, "need_score": None, "contact_email": SECRET, "embedding": [1]}
    status, data = evidence.normalize_tract(wrapper([row]), TRACT["tract_id"])
    assert status == "ready" and data["poverty_rate"] is None and data["need_score"] is None
    assert "poverty_rate" in data["unknown_fields"]
    assert "contact_email" not in data and "embedding" not in data


@pytest.mark.parametrize("rows,status", [([], "no_match"), ([TRACT, TRACT], "unknown")])
def test_tract_absence_and_duplicates_are_never_substituted(rows, status):
    observed, data = evidence.normalize_tract(rows, TRACT["tract_id"])
    assert observed == status and data is None


@pytest.mark.parametrize("mutation", [
    {"tract_id": "53033000200"}, {"poverty_rate": 20}, {"no_vehicle_rate": -1},
    {"total_pop": True}, {"median_income": -100}, {"tract_lon": -200},
    {"need_score": .99}, {"poverty_rate": None, "need_score": .15},
    {"no_vehicle_rate": float("nan")},
])
def test_invalid_tract_rates_ids_and_calculated_need_are_unknown_not_zeroed(mutation):
    with pytest.raises(evidence.InvalidEvidence):
        evidence.normalize_tract([{**TRACT, **mutation}], TRACT["tract_id"])


@pytest.mark.parametrize("payload", [
    "not-json", "x" * (evidence.MAX_PAYLOAD_BYTES + 1),
    {"result": {"result": {"result": {"result": {"result": []}}}}},
    {"content": [{"type": "image", "text": SECRET}]},
    [TRACT] * 3,
])
def test_invalid_or_unbounded_tool_response_is_refused(payload):
    with pytest.raises(evidence.InvalidEvidence):
        evidence.normalize_tract(payload, TRACT["tract_id"])


@pytest.mark.asyncio
async def test_one_failed_tool_cannot_delete_other_evidence_or_invent_a_match():
    tools = FakeToolset([FakeTool("lookup_shelf_life", error=RuntimeError(SECRET)),
                         FakeTool("get_tract_equity")])
    result = await evidence.ContextEvidenceService(lambda: tools).collect(
        food_terms=["bread"], candidate_tract_ids=[TRACT["tract_id"]])
    assert result["status"] == "partial"
    assert result["foodkeeper"][0]["status"] == "unavailable"
    assert result["tract_equity"][0]["status"] == "ready"
    assert result["deadline_modified"] is False and "winner" not in result
    assert SECRET not in json.dumps(result) and tools.closed


@pytest.mark.asyncio
async def test_missing_or_duplicate_discovered_tool_is_unavailable_without_calling_it():
    one = FakeTool("lookup_shelf_life")
    tools = FakeToolset([one, one])
    result = await evidence.ContextEvidenceService(lambda: tools).collect(
        food_terms=["bread"], candidate_tract_ids=[TRACT["tract_id"]])
    assert result["status"] == "unavailable"
    assert all(row["code"] == "context_tool_unavailable" for row in result["foodkeeper"] + result["tract_equity"])
    assert one.calls == [] and tools.closed


@pytest.mark.asyncio
async def test_lookup_deadline_cancels_waiter_preserves_completed_sibling_and_closes_toolset():
    slow = FakeTool("get_tract_equity", gate=asyncio.Event())
    tools = FakeToolset([FakeTool("lookup_shelf_life"), slow])
    result = await asyncio.wait_for(evidence.ContextEvidenceService(
        lambda: tools, lookup_timeout_seconds=.02).collect(
            food_terms=["bread"], candidate_tract_ids=[TRACT["tract_id"]]), 1)
    assert result["status"] == "partial" and result["foodkeeper"][0]["status"] == "ready"
    assert result["tract_equity"][0]["code"] == "context_lookup_timeout"
    assert slow.cancelled == 1 and tools.closed


@pytest.mark.asyncio
async def test_one_total_budget_bounds_all_six_lookups_and_cancels_them():
    tools = FakeToolset([FakeTool(name, gate=asyncio.Event()) for name in evidence.TOOLS])
    result = await asyncio.wait_for(evidence.ContextEvidenceService(
        lambda: tools, timeout_seconds=.03, lookup_timeout_seconds=5).collect(
            food_terms=["bread", "milk", "yogurt"],
            candidate_tract_ids=["53033000100", "53033000200", "53033000300"]), .5)
    assert result["status"] == "unavailable" and tools.closed
    assert all(tool.cancelled == 3 for tool in tools.tools)
    assert len(result["foodkeeper"]) == len(result["tract_equity"]) == 3


@pytest.mark.asyncio
async def test_discovery_timeout_still_closes_and_returns_per_request_unavailable():
    tools = FakeToolset(discovery_gate=asyncio.Event())
    result = await asyncio.wait_for(evidence.ContextEvidenceService(
        lambda: tools, timeout_seconds=.02).collect(food_terms=["bread"]), .5)
    assert tools.discovery_cancelled and tools.closed
    assert result["foodkeeper"][0]["code"] == "context_operation_timeout"


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_cleans_children_without_partial_success_return():
    slow = FakeTool("lookup_shelf_life", gate=asyncio.Event())
    tools = FakeToolset([slow])
    task = asyncio.create_task(evidence.ContextEvidenceService(lambda: tools).collect(food_terms=["bread"]))
    await slow.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, .5)
    assert slow.cancelled == 1 and tools.closed


@pytest.mark.asyncio
async def test_stalled_cleanup_is_bounded_and_retains_read_receipt():
    tools = FakeToolset(close_gate=asyncio.Event())
    result = await asyncio.wait_for(evidence.ContextEvidenceService(
        lambda: tools, cleanup_seconds=.02).collect(food_terms=["bread"]), .5)
    assert result["foodkeeper"][0]["status"] == "ready"
    assert result["status"] == "partial" and result["warnings"] == ["context_cleanup_incomplete"]
    await asyncio.sleep(0)
    assert tools.closed and tools.close_cancelled


def test_toolbox_config_is_fixed_loopback_get_and_bounded_exact_read_only_sql():
    docs = {doc["name"]: doc for doc in yaml.safe_load_all(Path("agent/tools.yaml").read_text())}
    source, tool = docs["foodkeeper_loopback"], docs["lookup_shelf_life"]
    assert source["baseUrl"] == "http://127.0.0.1:8080"
    assert source["timeout"] == "5s" and source["returnFullError"] is False
    assert source["allowPrivateNetworks"] is False
    assert source["allowedIpRanges"] == ["127.0.0.1/32"]
    # v1.14.0's SSRFGuard checks explicit allowance before the custom block list.
    allows = [ipaddress.ip_network(value) for value in source["allowedIpRanges"]]
    blocks = [ipaddress.ip_network(value) for value in source["customBlockedIpRanges"]]
    def allowed(address):
        address = ipaddress.ip_address(address)
        return any(address in network for network in allows) or not any(address in network for network in blocks)
    assert allowed("127.0.0.1")
    assert all(not allowed(ip) for ip in ("127.0.0.2", "::1", "169.254.169.254", "10.0.0.1", "8.8.8.8", "2001:4860:4860::8888"))
    assert (tool["type"], tool["method"], tool["path"]) == ("http", "GET", "/api/foodkeeper")
    assert tool["queryParams"] == [{"name": "query", "type": "string",
                                  "description": "One literal product term of 2–120 characters, not a mixed-food posting."}]
    assert not ({"pathParams", "headerParams", "bodyParams", "requestBody", "headers"} & tool.keys())
    tract = docs["get_tract_equity"]
    assert tract["source"] == "seattle_food_db" and tract["type"] == "bigquery-sql"
    assert "WHERE geo_id = @tract_id" in tract["statement"] and "LIMIT 2" in tract["statement"]
    assert ".a4i_food.tract_demographics`" in tract["statement"]
    assert "embedding" not in tract["statement"] and "SELECT *" not in tract["statement"]
    assert not ({"templateParameters", "template"} & tract.keys())
    for item in (tool, tract):
        assert item["annotations"] == {"readOnlyHint": True, "destructiveHint": False}
    assert docs["seattle_food_db"]["readOnly"] is True and docs["seattle_food_db"]["writeMode"] == "blocked"
    assert "deadline" not in tract["statement"].lower()
