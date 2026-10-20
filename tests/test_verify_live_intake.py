"""Synthetic WebSocket protocol receipts; no network, microphone or broker access."""
import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from scripts.verify_live_intake import MODEL, LOCATION, endpoints, probe

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)


class Socket:
    def __init__(self, mode="success"):
        self.mode = mode
        self.events = asyncio.Queue()
        self.events.put_nowait(json.dumps({"type": "ready", "model": MODEL, "location": LOCATION}))
        self.sent = []
        self.closed = False

    async def recv(self):
        return await self.events.get()

    async def send(self, text):
        event = json.loads(text)
        self.sent.append(event)
        if event["type"] == "context":
            self.fields = dict(event["fields"])
            await self.events.put(json.dumps(self.snapshot(1)))
            await self.events.put(json.dumps({"type": "turn_complete"}))
        elif event["type"] == "text":
            if self.mode == "timeout":
                return
            if self.mode == "error":
                await self.events.put(json.dumps({
                    "type": "error", "code": "live_unavailable", "message": "PRIVATE_PROVIDER_SECRET"}))
                return
            await self.events.put(b"\0" * 240)
            await self.events.put(json.dumps({"type": "transcript", "text": "PRIVATE_TRANSCRIPT"}))
            if self.mode != "no_tool":
                self.fields["pallet_count"] = 2
                if self.mode == "bad_deadline":
                    self.fields["deadline_at"] = (NOW + timedelta(hours=3)).isoformat()
                await self.events.put(json.dumps(self.snapshot(1 if self.mode == "stale" else 2)))
            await self.events.put(json.dumps({"type": "turn_complete"}))

    def snapshot(self, version):
        return {"type": "draft", "draft_id": "test-session", "draft_version": version,
                "fields": dict(self.fields), "missing_fields": [], "ready_for_review": True}


def connection(socket, seen):
    @asynccontextmanager
    async def connect(uri, **kwargs):
        seen.update(uri=uri, kwargs=kwargs)
        try:
            yield socket
        finally:
            socket.closed = True
    return connect


@pytest.mark.asyncio
async def test_real_protocol_shape_updates_draft_preserves_clock_and_stops():
    socket, seen = Socket(), {}
    report = await probe("https://demo.run.app", connector=connection(socket, seen), now=NOW)
    assert report["passed"] and report["draft_updated"] and report["deadline_preserved"]
    assert report["response_complete"] and report["audio_bytes_received"] == 240
    assert report["transcript_events"] == 1 and report["match_request_sent"] is False
    assert [e["type"] for e in socket.sent] == ["context", "text", "stop"]
    assert seen["uri"] == "wss://demo.run.app/api/live/intake"
    assert seen["kwargs"]["origin"] == "https://demo.run.app"
    assert "additional_headers" not in seen["kwargs"]  # Public probe sends no credential.
    assert "PRIVATE" not in json.dumps(report)
    assert socket.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [
    ("bad_deadline", "deadline_changed"), ("stale", "stale_draft"),
    ("no_tool", "model_did_not_update_draft"), ("error", "live_unavailable"),
    ("timeout", "probe_timeout"),
])
async def test_failure_never_claims_success_or_exposes_server_text(mode, code):
    socket = Socket(mode)
    result = await probe("https://demo.run.app", timeout=.03,
                         connector=connection(socket, {}), now=NOW)
    assert not result["passed"] and result["error_code"] == code
    assert "PRIVATE" not in json.dumps(result)
    assert socket.sent[-1]["type"] == "stop"
    assert socket.closed


@pytest.mark.parametrize("value", [
    "http://public.example", "https://name:secret@demo.run.app",
    "https://demo.run.app/path", "https://demo.run.app?token=secret", "wss://demo.run.app",
])
def test_endpoint_rejects_cleartext_public_hosts_and_embedded_secrets(value):
    with pytest.raises(argparse.ArgumentTypeError):
        endpoints(value)


def test_localhost_probe_is_supported():
    assert endpoints("http://127.0.0.1:8080/") == (
        "http://127.0.0.1:8080", "ws://127.0.0.1:8080/api/live/intake")
