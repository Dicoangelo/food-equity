"""Offline Live transport and draft boundaries; never use credentials or cloud APIs."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent import live_intake as live

NOW = datetime(2026, 10, 20, 16, 30, tzinfo=timezone.utc)


def complete_context():
    return dict(posting_text="One pallet of sealed bread", donor_name="Test grocer",
                required_storage="ambient", pallet_count=1, hours_remaining=6,
                donor_lat=47.6, donor_lon=-122.3)


def test_config_disabled_and_no_auth_discovery(monkeypatch):
    monkeypatch.delenv("FOOD_LIVE_ENABLED", raising=False)
    monkeypatch.setattr(live, "create_client", lambda *_: pytest.fail("No cloud client"))
    app = FastAPI()
    app.include_router(live.router)
    with TestClient(app) as client:
        config = client.get("/api/live/config").json()
        assert config["enabled"] is False
        assert config["max_session_seconds"] == 180
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/live/intake", headers={"origin": "http://testserver"}):
                pass


def test_origin_and_explicit_proxy_allowlist(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("FOOD_LIVE_ALLOWED_ORIGINS", raising=False)
    def ws(origin, host="testserver", scheme="ws"):
        return NS(headers={"origin": origin, "host": host}, url=NS(scheme=scheme))
    assert live.same_origin(ws("http://testserver"))
    for origin in ("", "null", "http://evil.test", "https://testserver", "http://testserver@evil.test"):
        assert not live.same_origin(ws(origin))
    monkeypatch.setenv("K_SERVICE", "broker")
    assert live.same_origin(ws("https://testserver"))
    monkeypatch.setenv("FOOD_LIVE_ALLOWED_ORIGINS", "https://8080.example.cloudshell.dev,https://*.example.com")
    assert live.same_origin(ws("https://8080.example.cloudshell.dev", "backend.run.app"))
    assert not live.same_origin(ws("https://evil.example.com", "backend.run.app"))
    assert not live.same_origin(ws("https://8080.example.cloudshell.dev.evil", "backend.run.app"))
    assert not live.same_origin(ws("http://8080.example.cloudshell.dev", "backend.run.app"))


def test_session_capacity_is_bounded_and_released():
    capacity = live.SessionSlots()
    assert capacity.acquire(2)
    assert capacity.acquire(2)
    assert not capacity.acquire(2)
    capacity.release()
    assert capacity.acquire(2)


def test_full_snapshot_and_fixed_context_clock():
    clock = [NOW]
    draft = live.Draft(now=lambda: clock[0])
    first = draft.context(complete_context())
    assert first["ready_for_review"] and first["draft_version"] == 1
    assert first["fields"]["deadline_at"] == (NOW + timedelta(hours=6)).isoformat()
    clock[0] += timedelta(hours=1)
    draft.text("We have six hours for pickup and one pallet of bread")
    update = draft.update(dict(evidence=draft.latest_text, hours_remaining=6, pallet_count=1))
    assert update["fields"]["deadline_at"] == first["fields"]["deadline_at"]
    assert update["fields"]["hours_remaining"] == 5
    assert update["draft_version"] == 1
    assert update["draft_id"] == first["draft_id"]


def test_initial_window_anchors_at_utterance_not_tool_latency():
    clock = [NOW]
    draft = live.Draft(now=lambda: clock[0])
    draft.text("Move one pallet within two hours")
    clock[0] += timedelta(minutes=7)
    result = draft.update(dict(evidence=draft.latest_text, pallet_count=1, hours_remaining=2))
    assert result["fields"]["deadline_at"] == (NOW + timedelta(hours=2)).isoformat()
    assert not result["ready_for_review"]


def test_explicit_deadline_correction_is_visible_versioned():
    draft = live.Draft(now=lambda: NOW)
    draft.context(complete_context())
    draft.text("Actually change pickup to within four hours")
    result = draft.update(dict(evidence=draft.latest_text, hours_remaining=4, replace_deadline=True))
    assert result["deadline_changed"] and result["draft_version"] == 2
    assert result["fields"]["deadline_at"] == (NOW + timedelta(hours=4)).isoformat()
    draft.text("We have six hours for pickup")
    with pytest.raises(ValueError):
        draft.update(dict(evidence=draft.latest_text, hours_remaining=6, replace_deadline=True))


def test_spoken_shorter_window_tightens_form_default_without_resetting_retries():
    clock = [NOW]
    draft = live.Draft(now=lambda: clock[0])
    draft.context(complete_context())
    draft.text("Pickup within two hours")
    first = draft.update(dict(evidence=draft.latest_text, hours_remaining=2))
    assert first["deadline_changed"] and first["fields"]["hours_remaining"] == 2
    clock[0] += timedelta(minutes=10)
    draft.text("Pickup within two hours")
    repeated = draft.update(dict(evidence=draft.latest_text, hours_remaining=2))
    assert repeated["fields"]["deadline_at"] == first["fields"]["deadline_at"]
    assert not repeated["deadline_changed"]


@pytest.mark.parametrize("text,patch", [
    ("200 loaves of bread", {"pallet_count": 1}),
    ("Not one pallet, just three cases", {"pallet_count": 1}),
    ("We do not have six hours for pickup", {"hours_remaining": 6}),
    ("We don't have two pallets", {"pallet_count": 2}),
    ("We have milk with six hours to expiry", {"hours_remaining": 6}),
    ("Three crates of spinach", {"hours_remaining": 6}),
    ("one pallet of bread", {"donor_lat": 47.6}),
    ("one pallet of bread", {"approved": True}),
    ("one pallet of bread", {"pallet_count": True}),
    ("one pallet of bread", {"pallet_count": float("nan")}),
])
def test_no_invented_units_coordinates_approval_or_clock(text, patch):
    draft = live.Draft(now=lambda: NOW)
    draft.text(text)
    with pytest.raises(ValueError):
        draft.update({"evidence": text, **patch})
    assert draft.values == {} and draft.deadline is None


def test_evidence_must_be_latest_input_and_context_is_one_time():
    draft = live.Draft(now=lambda: NOW)
    draft.context({"donor_lat": 47.6, "donor_lon": -122.3})
    with pytest.raises(ValueError):
        draft.context(complete_context())
    draft.text("Two pallets")
    draft.text("Correction: three crates")
    with pytest.raises(ValueError):
        draft.update({"evidence": "Two pallets", "pallet_count": 2})
    assert draft.values == {"donor_lat": 47.6, "donor_lon": -122.3}


def test_expired_snapshot_cannot_be_applied():
    clock = [NOW]
    draft = live.Draft(now=lambda: clock[0])
    draft.context(complete_context())
    clock[0] += timedelta(hours=7)
    snap = draft.snapshot()
    assert snap["fields"]["hours_remaining"] == 0
    assert not snap["ready_for_review"]
    assert "unexpired_deadline" in snap["missing_fields"]


def test_tool_config_exposes_only_draft_function():
    config = live.connect_config()
    tools = config.tools[0].function_declarations
    assert [t.name for t in tools] == ["draft_offer"]
    assert "donor_lat" not in tools[0].parameters_json_schema["properties"]
    assert config.response_modalities == ["AUDIO"]


class FakeWebSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.closed = False
    async def receive(self):
        return await self.incoming.get()
    async def send_json(self, value):
        await self.outgoing.put(value)
    async def send_bytes(self, value):
        await self.outgoing.put(value)
    async def close(self):
        self.closed = True
    async def send(self, payload):
        import json
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps(payload)})


class FakeSession:
    def __init__(self):
        self.events = asyncio.Queue()
        self.texts, self.audio, self.responses = [], [], []
        self.receiving = False
    async def send_client_content(self, **kwargs):
        self.texts.append(kwargs)
    async def send_realtime_input(self, **kwargs):
        self.audio.append(kwargs)
    async def send_tool_response(self, **kwargs):
        self.responses.append(kwargs)
    async def receive(self):
        self.receiving = True
        try:
            while True:
                event = await self.events.get()
                yield event
                if event.server_content and event.server_content.turn_complete:
                    return
        finally:
            self.receiving = False


class FakeClient:
    def __init__(self, fail=False):
        self.session = FakeSession()
        self.closed = False
        self.context_closed = False
        self.fail = fail
        self.aio = NS(live=NS(connect=self.connect), aclose=self.close)
    @asynccontextmanager
    async def connect(self, **kwargs):
        self.connect_args = kwargs
        if self.fail:
            raise RuntimeError("PRIVATE_PROVIDER_SECRET")
        try:
            yield self.session
        finally:
            self.context_closed = True
    async def close(self):
        self.closed = True


async def next_output(ws):
    return await asyncio.wait_for(ws.outgoing.get(), 1)


@pytest.mark.asyncio
async def test_transport_tool_draft_pcm_transcript_and_cleanup():
    from google.genai import types
    ws, client = FakeWebSocket(), FakeClient()
    task = asyncio.create_task(live.serve(ws, live.Settings(enabled=True, project="test"), lambda _: client))
    assert (await next_output(ws))["type"] == "ready"
    context = complete_context()
    await ws.send({"type": "context", "fields": context})
    assert (await next_output(ws))["ready_for_review"]
    await ws.send({"type": "text", "text": "Actually two pallets of bread"})
    # Wait for actual upstream processing, not a timing guess.
    for _ in range(50):
        if len(client.session.texts) == 2:
            break
        await asyncio.sleep(.001)
    await client.session.events.put(types.LiveServerMessage(tool_call=types.LiveServerToolCall(
        function_calls=[types.FunctionCall(id="draft-1", name="draft_offer", args={
            "pallet_count": 2, "evidence": "Actually two pallets of bread"})])))
    result = await next_output(ws)
    assert result["type"] == "draft" and result["fields"]["pallet_count"] == 2
    assert result["fields"]["donor_lat"] == context["donor_lat"]
    await ws.incoming.put({"type": "websocket.receive", "bytes": b"\0\0" * 100})
    await client.session.events.put(types.LiveServerMessage(server_content=types.LiveServerContent(
        output_transcription=types.Transcription(text="Please review the draft."),
        model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
            data=b"\0\0" * 100, mime_type="audio/pcm;rate=24000"))]),
        turn_complete=True)))
    assert (await next_output(ws))["type"] == "transcript"
    assert await next_output(ws) == b"\0\0" * 100
    assert (await next_output(ws))["type"] == "turn_complete"
    await ws.send({"type": "stop"})
    await asyncio.wait_for(task, 1)
    assert client.closed and client.context_closed and ws.closed
    assert not client.session.receiving
    assert client.session.audio[0]["audio"].mime_type == "audio/pcm;rate=16000"
    assert client.session.responses[0]["function_responses"][0].id == "draft-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"type": "websocket.receive", "bytes": b"x"},
    {"type": "websocket.receive", "bytes": b"\0" * (live.MAX_AUDIO_FRAME + 2)},
    {"type": "websocket.receive", "text": "x" * (live.MAX_JSON_BYTES + 1)},
    {"type": "websocket.receive", "text": '{"type":"dispatch"}'},
])
async def test_bad_wire_frames_close_without_provider_actions(payload):
    ws, client = FakeWebSocket(), FakeClient()
    task = asyncio.create_task(live.serve(ws, live.Settings(enabled=True), lambda _: client))
    await next_output(ws)
    await ws.incoming.put(payload)
    assert (await next_output(ws))["type"] == "error"
    await asyncio.wait_for(task, 1)
    assert not client.session.texts and not client.session.audio
    assert client.closed and ws.closed


@pytest.mark.asyncio
async def test_timeout_and_provider_error_are_safe_and_clean():
    ws, client = FakeWebSocket(), FakeClient()
    await live.serve(ws, live.Settings(enabled=True, max_seconds=.02), lambda _: client)
    assert (await next_output(ws))["type"] == "ready"
    assert (await next_output(ws))["code"] == "session_timeout"
    assert client.closed and not client.session.receiving
    ws, client = FakeWebSocket(), FakeClient(fail=True)
    await live.serve(ws, live.Settings(enabled=True), lambda _: client)
    result = await next_output(ws)
    assert result["code"] == "live_unavailable" and "SECRET" not in str(result)
    assert client.closed and ws.closed
