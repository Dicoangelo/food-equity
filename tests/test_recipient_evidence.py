"""Exact-ID MCP evidence reads, offline only; no cloud, model, broker or matcher."""
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

from agent import recipient_evidence as evidence

ROW = {
    "recipient_id": 91, "name": "Fixture food bank", "city": "Seattle",
    "latitude": 47.6, "longitude": -122.3, "profile_source": "generated",
    "cold_storage": "commercial", "frozen_storage": False, "max_pallets": 2,
    "pickup_window_hours": 1, "profile_text": "Can accept sealed ambient bakery goods.",
    "service_days": "Tuesday", "tract_poverty_rate": 0.2,
}


class FakeTool:
    name = evidence.TOOL_NAME

    def __init__(self, payload=None, error=None, gate=None):
        self.payload = [dict(ROW)] if payload is None else payload
        self.error, self.gate = error, gate
        self.calls = []
        self.started = asyncio.Event()
        self.cancelled = False

    async def run_async(self, args, tool_context):
        assert tool_context is None  # Official SDK's server-auth path.
        self.calls.append(args)
        self.started.set()
        if self.error:
            raise self.error
        try:
            if self.gate:
                await self.gate.wait()
            return self.payload
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FakeToolset:
    def __init__(self, tool=None, close_gate=None):
        self.tool = tool or FakeTool()
        self.closed = False
        self.close_gate = close_gate
        self.close_cancelled = False

    async def get_tools(self):
        return [self.tool]

    async def close(self):
        self.closed = True
        try:
            if self.close_gate:
                await self.close_gate.wait()
        except asyncio.CancelledError:
            self.close_cancelled = True
            raise


async def fetch(payload=None, **kwargs):
    tool = FakeTool(payload=payload, **kwargs)
    toolset = FakeToolset(tool)
    service = evidence.RecipientEvidenceService(lambda: toolset)
    return await service.get("91"), tool, toolset


@pytest.mark.asyncio
async def test_exact_read_has_fresh_retrieval_provenance_without_claiming_current_match():
    result, tool, toolset = await fetch()
    assert tool.calls == [{"recipient_id": "91"}]
    assert toolset.closed
    assert result["status"] == "ready" and result["recipient_id"] == "91"
    assert result["profile"]["recipient_id"] == "91"
    assert result["retrieved_at"] == result["fetched_at"]
    assert result["source"]["transport"] == "mcp"
    assert result["observation_date"] is None
    assert result["source"]["source_observed_at"] is None
    assert result["source"]["operational_confirmed_at"] is None
    assert result["requires_confirmation"] and not result["operational_data_verified"]
    assert "current match has not changed" in result["message"]
    assert "composite_score" not in result["profile"]


@pytest.mark.asyncio
async def test_each_refresh_performs_a_new_exact_read_without_cache_or_model():
    toolset = FakeToolset()
    service = evidence.RecipientEvidenceService(lambda: toolset)
    await service.get("91")
    await service.get("91")
    assert toolset.tool.calls == [{"recipient_id": "91"}, {"recipient_id": "91"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper", [
    lambda rows: rows,
    lambda rows: json.dumps(rows),
    lambda rows: {"result": {"rows": rows}},
    lambda rows: {"content": [{"type": "text", "text": json.dumps(rows)}]},
])
async def test_supported_actual_toolbox_row_envelopes(wrapper):
    result, _, _ = await fetch(wrapper([ROW]))
    assert result["status"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("rows,code", [
    ([ROW, ROW], "ambiguous_recipient_id"),
    ([dict(ROW, recipient_id="92")], "invalid_evidence"),
    ([dict(ROW, name=None)], "invalid_evidence"),
    ([ROW, ROW, ROW], "invalid_evidence"),
    ({"unexpected": "SECRET_RAW_VALUE"}, "invalid_evidence"),
])
async def test_ambiguous_or_invalid_evidence_is_unknown_never_silently_deduplicated(rows, code):
    result, _, toolset = await fetch(rows)
    assert result["status"] == "unknown" and result["code"] == code
    assert result["profile"] is None and toolset.closed
    assert "SECRET_RAW_VALUE" not in json.dumps(result)


@pytest.mark.asyncio
async def test_empty_result_is_not_found_not_unavailable_or_real_world_absence():
    result, _, _ = await fetch([])
    assert result["status"] == "not_found" and result["code"] == "recipient_not_found"
    assert result["retrieved_at"] is not None and result["profile"] is None


@pytest.mark.asyncio
async def test_unmapped_profile_is_retained_and_output_field_allowlist_drops_contact_vector_score():
    result, _, _ = await fetch([dict(ROW, latitude=None, longitude=None,
        contact_email="SECRET_CONTACT", phone="SECRET_PHONE", embedding=[1]*3072,
        composite_score=.99, operational_data_verified=True, profile_source="verified")])
    assert result["status"] == "ready"
    assert result["profile"]["lat"] is None and result["profile"]["lon"] is None
    assert result["profile"]["profile_source"] == "unknown"
    assert result["profile"]["operational_data_verified"] is False
    assert {"lat", "lon"}.issubset(result["unknown_fields"])
    encoded = json.dumps(result)
    assert "SECRET_" not in encoded and '"embedding"' not in encoded and "composite_score" not in encoded


def test_normalization_preserves_zero_unknowns_and_bounded_profile_text():
    result = evidence.normalize_profile(dict(ROW, max_pallets=0, tract_poverty_rate=0,
        tract_no_vehicle_rate=99, latitude=float("nan"), profile_text="x"*5000), "91")
    assert result["max_pallets"] == result["tract_poverty_rate"] == 0
    assert result["tract_no_vehicle_rate"] is None and result["lat"] is None
    assert len(result["profile_text"]) == 4000
    assert result["profile_text_truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    "x" * (evidence.MAX_PAYLOAD_BYTES + 1),
    {"result": {"result": {"result": {"result": {"result": [ROW]}}}}},
    [dict(ROW, max_pallets=float("nan"))],
])
async def test_oversized_nested_or_nonfinite_payload_is_unknown(payload):
    result, _, toolset = await fetch(payload)
    assert result["status"] == "unknown" and result["code"] == "invalid_evidence"
    assert toolset.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["", " ", "../91", "91 OR 1=1", "https://example.com", "x"*101, True])
async def test_invalid_identifiers_never_discover_or_call_mcp(identifier):
    service = evidence.RecipientEvidenceService(lambda: pytest.fail("No MCP call for invalid identifier"))
    with pytest.raises(ValueError):
        await service.get(identifier)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,error", [
    ({"isError": True, "content": "SECRET_PROVIDER_ERROR"}, None),
    (None, RuntimeError("SECRET_PROVIDER_ERROR")),
])
async def test_provider_errors_are_unavailable_sanitized_and_closed(payload, error):
    result, _, toolset = await fetch(payload, error=error)
    assert result["status"] == "unavailable" and result["retrieved_at"] is None
    assert "SECRET" not in json.dumps(result) and toolset.closed


@pytest.mark.asyncio
async def test_timeout_cancels_query_and_bounds_cleanup():
    tool = FakeTool(gate=asyncio.Event())
    toolset = FakeToolset(tool, close_gate=asyncio.Event())
    service = evidence.RecipientEvidenceService(lambda: toolset, timeout_seconds=.01, cleanup_seconds=.01)
    result = await asyncio.wait_for(service.get("91"), .2)
    await asyncio.sleep(0)
    assert result["status"] == "unavailable" and result["code"] == "evidence_timeout"
    assert tool.cancelled and toolset.closed and toolset.close_cancelled


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_after_closing_toolset():
    tool = FakeTool(gate=asyncio.Event())
    toolset = FakeToolset(tool)
    task = asyncio.create_task(evidence.RecipientEvidenceService(lambda: toolset).get("91"))
    await tool.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert tool.cancelled and toolset.closed


@pytest.mark.parametrize("rows,status", [([ROW], 200), ([ROW, ROW], 409), ([], 404)])
def test_router_status_identity_and_no_store(monkeypatch, rows, status):
    app = FastAPI()
    app.include_router(evidence.router)
    monkeypatch.setattr(evidence, "get_evidence_service", lambda:
        evidence.RecipientEvidenceService(lambda: FakeToolset(FakeTool(rows))))
    with TestClient(app) as client:
        result = client.get("/api/recipients/91/evidence")
    assert result.status_code == status
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["recipient_id"] == "91"


def test_router_bad_id_and_unavailable_are_distinct(monkeypatch):
    app = FastAPI()
    app.include_router(evidence.router)
    monkeypatch.setattr(evidence, "get_evidence_service", lambda:
        evidence.RecipientEvidenceService(lambda: FakeToolset(FakeTool(error=RuntimeError("SECRET")))))
    with TestClient(app) as client:
        bad = client.get("/api/recipients/bad%20id/evidence")
        failed = client.get("/api/recipients/91/evidence")
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_recipient_id"
    assert failed.status_code == 503 and failed.json()["recipient_id"] == "91"
    assert "SECRET" not in failed.text


def test_tool_definition_uses_fixed_table_and_exact_parameter_without_contact_or_vectors():
    documents = list(yaml.safe_load_all((Path(__file__).parents[1]/"agent/tools.yaml").read_text()))
    tool = next(item for item in documents if item.get("name") == evidence.TOOL_NAME)
    sql = tool["statement"]
    assert "WHERE CAST(recipient_id AS STRING) = @recipient_id" in sql and "LIMIT 2" in sql
    assert "SELECT *" not in sql and "embedding," not in sql
    assert all(field not in sql for field in ("contact_email", "phone", "street"))
    assert tool["parameters"] == [{"name":"recipient_id","type":"string",
        "description":"One canonical exact recipient ID, never a name, SQL, table or URL."}]
    source = documents[0]
    assert source["readOnly"] and source["writeMode"] == "blocked"
    assert source["allowedDatasets"] == ["a4i_food"] and source["maximumBytesBilled"] == 100000000
