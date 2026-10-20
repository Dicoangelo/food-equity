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


def test_sparse_quantity_patch_repairs_copied_context_hours_without_changing_deadline():
    clock = [NOW]
    draft = live.Draft(now=lambda: clock[0])
    before = draft.context(complete_context())
    clock[0] += timedelta(minutes=5)
    draft.text("I have two pallets. Keep the existing pickup deadline unchanged.")
    with pytest.raises(live.DraftRejected) as rejected:
        draft.update({"pallet_count": 2, "hours_remaining": 6, "evidence": draft.latest_text})
    assert rejected.value.code == "pickup_hours_not_explicit"
    assert rejected.value.field == "hours_remaining"
    assert draft.version == before["draft_version"]
    assert draft.snapshot()["fields"]["pallet_count"] == 1
    after = draft.update({"pallet_count": 2, "evidence": "I have two pallets"})
    assert after["fields"]["pallet_count"] == 2
    assert after["fields"]["deadline_at"] == before["fields"]["deadline_at"]
    assert after["draft_version"] == before["draft_version"] + 1


@pytest.mark.parametrize("text,patch,code,field", [
    ("Two pallets", {"evidence": "One pallet", "pallet_count": 1},
     "evidence_not_current", "evidence"),
    ("Two crates", {"evidence": "Two crates", "pallet_count": 2},
     "pallet_quantity_not_explicit", "pallet_count"),
    ("I have two pallets", {"evidence": "I have two pallets", "hours_remaining": 6},
     "pickup_hours_not_explicit", "hours_remaining"),
    ("Six hours to expiry", {"evidence": "Six hours to expiry", "hours_remaining": 6},
     "pickup_window_not_confirmed", "hours_remaining"),
    ("We have eight hours for pickup",
     {"evidence": "We have eight hours for pickup", "hours_remaining": 8, "replace_deadline": True},
     "deadline_correction_not_explicit", "replace_deadline"),
    ("Two pallets", {"evidence": "Two pallets", "pallet_count": "PRIVATE_VALIDATION_INPUT"},
     "invalid_patch", None),
])
def test_rejection_feedback_is_static_and_field_specific(text, patch, code, field):
    draft = live.Draft(now=lambda: NOW)
    before = draft.context(complete_context())
    draft.text(text)
    with pytest.raises(live.DraftRejected) as rejected:
        draft.update(patch)
    assert rejected.value.code == code and rejected.value.field == field
    assert str(rejected.value) == live.REJECTION_REASONS[code][1]
    assert "PRIVATE_VALIDATION_INPUT" not in str(rejected.value)
    assert draft.snapshot() == before


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
    assert [t.name for t in tools] == [
        "draft_offer", "read_current_match", "read_recipient_evidence", "show_alternatives"]
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
    assert (await next_output(ws))["context_only"]
    await ws.send({"type": "text", "text": "Actually two pallets of bread"})
    started = await next_output(ws)
    owner = {key: started[key] for key in live.Owner.model_fields}
    # Wait for actual upstream processing, not a timing guess.
    for _ in range(50):
        if len(client.session.texts) == 1:
            break
        await asyncio.sleep(.001)
    await client.session.events.put(types.LiveServerMessage(tool_call=types.LiveServerToolCall(
        function_calls=[types.FunctionCall(id="draft-1", name="draft_offer", args={
            "pallet_count": 2, "evidence": "Actually two pallets of bread", **owner})])))
    result = await next_output(ws)
    assert result["type"] == "action_result" and result["effect"] == "draft_proposed"
    assert result["payload"]["draft"]["fields"]["pallet_count"] == 2
    assert result["payload"]["draft"]["fields"]["donor_lat"] == context["donor_lat"]
    await client.session.events.put(types.LiveServerMessage(server_content=types.LiveServerContent(
        output_transcription=types.Transcription(text="Please review the draft."),
        model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
            data=b"\0\0" * 100, mime_type="audio/pcm;rate=24000"))]),
        turn_complete=True)))
    assert (await next_output(ws))["type"] == "transcript"
    assert await next_output(ws) == b"\0\0" * 100
    assert (await next_output(ws))["type"] == "turn_complete"
    await ws.incoming.put({"type": "websocket.receive", "bytes": b"\0\0" * 100})
    assert (await next_output(ws))["type"] == "turn_started"
    for _ in range(50):
        if client.session.audio:
            break
        await asyncio.sleep(.001)
    await ws.send({"type": "stop"})
    await asyncio.wait_for(task, 1)
    assert client.closed and client.context_closed and ws.closed
    assert not client.session.receiving
    assert client.session.audio[0]["audio"].mime_type == "audio/pcm;rate=16000"
    assert client.session.responses[0]["function_responses"][0].id == "draft-1"


@pytest.mark.asyncio
async def test_transport_returns_specific_repair_then_accepts_sparse_patch():
    from google.genai import types
    ws, client = FakeWebSocket(), FakeClient()
    task = asyncio.create_task(live.serve(ws, live.Settings(enabled=True), lambda _: client))
    await next_output(ws)
    await ws.send({"type": "context", "fields": complete_context()})
    before = await next_output(ws)
    assert (await next_output(ws))["context_only"]
    await ws.send({"type": "text", "text": "I have two pallets. Keep the existing pickup deadline."})
    started = await next_output(ws)
    owner = {key: started[key] for key in live.Owner.model_fields}
    for _ in range(50):
        if len(client.session.texts) == 1:
            break
        await asyncio.sleep(.001)
    async def tool(args, identity):
        await client.session.events.put(types.LiveServerMessage(tool_call=types.LiveServerToolCall(
            function_calls=[types.FunctionCall(id=identity, name="draft_offer", args={**args, **owner})])))
        return await next_output(ws)
    rejected = await tool({"pallet_count": 2, "hours_remaining": 6,
                           "evidence": "I have two pallets"}, "rejected")
    assert rejected["type"] == "action_result" and rejected["outcome"] == "refused"
    assert rejected["code"] == "pickup_hours_not_explicit"
    assert rejected["field"] == "hours_remaining"
    for _ in range(50):
        if client.session.responses:
            break
        await asyncio.sleep(.001)
    response = client.session.responses[0]["function_responses"][0].response
    assert response["accepted"] is False
    assert response["code"] == rejected["code"]
    assert response["message"] == rejected["message"]
    repaired = await tool({"pallet_count": 2, "evidence": "I have two pallets"}, "repaired")
    assert repaired["payload"]["draft"]["fields"]["pallet_count"] == 2
    assert repaired["payload"]["draft"]["fields"]["deadline_at"] == before["fields"]["deadline_at"]
    assert repaired["payload"]["draft"]["draft_version"] == before["draft_version"] + 1
    await ws.send({"type": "stop"})
    await asyncio.wait_for(task, 1)
    assert client.closed and client.context_closed and ws.closed


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


class BackpressuredWebSocket(FakeWebSocket):
    """ASGI sends wait on a client that never reads; cancellation still works."""
    def __init__(self):
        super().__init__()
        self.error_started = asyncio.Event()
        self.close_started = asyncio.Event()
        self.error_cancelled = False
        self.close_cancelled = False

    async def accept(self):
        pass

    async def send_json(self, value):
        if value["type"] != "error":
            return await super().send_json(value)
        self.error_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.error_cancelled = True

    async def close(self, **kwargs):
        self.close_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.close_cancelled = True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "protocol", "provider"])
async def test_stalled_terminal_error_and_close_release_route_slot(monkeypatch, failure):
    ws, client = BackpressuredWebSocket(), FakeClient(fail=failure == "provider")
    capacity = live.SessionSlots()
    monkeypatch.setattr(live, "slots", capacity)
    monkeypatch.setattr(live, "same_origin", lambda _: True)
    monkeypatch.setattr(live, "create_client", lambda _: client)
    monkeypatch.setattr(live, "TERMINAL_IO_TIMEOUT_SECONDS", .01)
    monkeypatch.setattr(live, "settings", lambda: live.Settings(
        enabled=True, project="test", max_seconds=.03))
    if failure == "protocol":
        await ws.incoming.put({"type": "websocket.receive", "text": '{"type":"dispatch"}'})
    task = asyncio.create_task(live.live_intake(ws))
    await asyncio.wait_for(ws.error_started.wait(), 1)
    assert capacity.active == 1
    await asyncio.wait_for(task, 1)
    assert ws.error_cancelled and ws.close_cancelled
    assert client.closed and not client.session.receiving
    assert capacity.active == 0
    if failure != "provider":
        assert client.context_closed


@pytest.mark.asyncio
async def test_rejected_connection_close_is_also_bounded(monkeypatch):
    ws = BackpressuredWebSocket()
    monkeypatch.setattr(live, "settings", lambda: live.Settings(enabled=False))
    monkeypatch.setattr(live, "TERMINAL_IO_TIMEOUT_SECONDS", .01)
    monkeypatch.setattr(live, "create_client", lambda _: pytest.fail("No client for disabled route"))
    await asyncio.wait_for(live.live_intake(ws), 1)
    assert ws.close_cancelled and not ws.error_started.is_set()


async def owned_session():
    ws, client = FakeWebSocket(), FakeClient()
    task = asyncio.create_task(live.serve(ws, live.Settings(enabled=True), lambda _: client))
    assert (await next_output(ws))['type'] == 'ready'
    await ws.send({'type':'context','fields':complete_context()})
    before = await next_output(ws)
    assert (await next_output(ws))['context_only']
    await ws.send({'type':'text','text':'I have two pallets. Keep the pickup deadline unchanged.'})
    started = await next_output(ws)
    return ws,client,task,before,{k:started[k] for k in live.Owner.model_fields}


def draft_event(owner, identity='owned-call', **content):
    from google.genai import types
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(**content) if content else None,
        tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(
            id=identity,name='draft_offer',args={**owner,'pallet_count':2,'evidence':'I have two pallets'})]))


@pytest.mark.asyncio
async def test_interruption_and_tool_same_envelope_cancel_before_effect_or_audio():
    from google.genai import types
    ws,client,task,before,owner = await owned_session()
    await client.session.events.put(draft_event(owner, interrupted=True,
        output_transcription=types.Transcription(text='I applied the old change'),
        model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=b'\0\0',mime_type='audio/pcm;rate=24000'))])))
    assert (await next_output(ws))['type'] == 'interrupted'
    result = await next_output(ws)
    assert result['type'] == 'action_result' and result['outcome'] == 'cancelled'
    assert result['accepted'] is False and result['effect'] == 'none'
    assert (await next_output(ws))['code'] == 'turn_interrupted'
    await asyncio.wait_for(task,1)
    assert ws.outgoing.empty()  # No false transcript, audio, or mutated draft after cancellation.
    assert client.closed and client.context_closed and ws.closed


@pytest.mark.asyncio
async def test_view_change_cancels_pending_turn_and_late_provider_callback():
    ws,client,task,before,owner = await owned_session()
    await ws.send({'type':'view_context','context':{'schema_version':1,'offer_revision':0,'view_revision':1}})
    assert (await next_output(ws))['status'] == 'accepted'
    assert (await next_output(ws))['code'] == 'view_changed_restart'
    await asyncio.wait_for(task,1)
    await client.session.events.put(draft_event(owner))
    assert ws.outgoing.empty() and not client.session.receiving
    assert client.closed and client.context_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("offer_revision", [0, 1])
async def test_idle_view_update_cannot_rebind_a_captured_offer(offer_revision):
    ws, client = FakeWebSocket(), FakeClient()
    task = asyncio.create_task(live.serve(ws, live.Settings(enabled=True), lambda _: client))
    await next_output(ws)
    await ws.send({'type':'view_context','context':{'offer_revision':0,'view_revision':1}})
    assert (await next_output(ws))['status']=='accepted'
    await ws.send({'type':'context','fields':complete_context()})
    captured = await next_output(ws)
    assert captured['ready_for_review'] and (await next_output(ws))['context_only']
    await ws.send({'type':'view_context','context':{
        'offer_revision':offer_revision,'view_revision':2,'selected_recipient_id':'2'}})
    assert (await next_output(ws))['status']=='accepted'
    if offer_revision:
        assert (await next_output(ws))['code']=='offer_changed_restart'
    else:
        await ws.send({'type':'stop'})
    await asyncio.wait_for(task,1)
    assert client.closed and client.context_closed and not client.session.texts
    assert ws.outgoing.empty()


@pytest.mark.asyncio
async def test_duplicate_provider_call_has_one_ui_receipt_and_one_draft_increment():
    from google.genai import types
    ws,client,task,before,owner = await owned_session()
    await client.session.events.put(draft_event(owner))
    first = await next_output(ws)
    assert first['payload']['draft']['draft_version'] == before['draft_version']+1
    assert first['payload']['draft']['fields']['deadline_at'] == before['fields']['deadline_at']
    await client.session.events.put(draft_event(owner))
    await client.session.events.put(types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True)))
    assert (await next_output(ws))['type'] == 'turn_complete'  # No duplicate UI acknowledgement.
    assert len(client.session.responses) == 2
    assert client.session.responses[1]['function_responses'][0].response['replayed'] is True
    await ws.send({'type':'stop'});await asyncio.wait_for(task,1)


@pytest.mark.asyncio
async def test_read_tools_return_context_only_and_do_not_touch_draft_or_external_provider():
    from google.genai import types
    from tests.test_view_context import view_fixture
    ws,client = FakeWebSocket(),FakeClient()
    task=asyncio.create_task(live.serve(ws,live.Settings(enabled=True),lambda _:client))
    await next_output(ws)
    await ws.send({'type':'view_context','context':view_fixture()})
    assert (await next_output(ws))['status']=='accepted'
    await ws.send({'type':'text','text':'Why was recipient 3 rejected? Can they accept food now?'})
    started=await next_output(ws);owner={k:started[k] for k in live.Owner.model_fields}
    await client.session.events.put(types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
        types.FunctionCall(id='read-1',name='read_recipient_evidence',args={**owner,'recipient_id':'3'})])))
    result=await next_output(ws)
    assert result['effect']=='read_only'
    assert result['payload']['reported_recipient']['constraint_status']=='rejected'
    assert result['payload']['operational_availability']=='unconfirmed'
    assert result['payload']['authority']=='untrusted_browser_projection'
    assert not client.session.audio and len(client.session.texts)==1
    await ws.send({'type':'stop'});await asyncio.wait_for(task,1)
