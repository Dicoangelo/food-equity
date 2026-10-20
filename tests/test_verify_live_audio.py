"""Offline adversarial checks for the synthetic audio probe; no provider or mic."""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import struct
import wave

import pytest

from scripts.verify_live_audio import MODEL, LOCATION, SmokeFailure, load_wav, probe

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
PCM = struct.pack("<h", 2000) * 16000  # Protocol fixture, not the spoken integration-test WAV.


class Socket:
    def __init__(self, mode="success"):
        self.mode, self.sent, self.audio, self.closed = mode, [], [], False
        self.accepted = False
        self.events = asyncio.Queue()
        self.owner = {"turn_id": "turn-audio", "expected_offer_revision": 0,
                      "expected_view_revision": 0, "provider_epoch": 1}
        self.put({"type": "ready", "model": MODEL, "location": LOCATION, "provider_epoch": 1,
                  "audio_activity": True, "input_correlation": mode != "old_protocol"})

    def put(self, event):
        self.events.put_nowait(event if isinstance(event, bytes) else json.dumps(event))

    def owned(self, **event):
        return {**self.owner, **event}

    async def recv(self):
        result = await self.events.get()
        if isinstance(result, str) and json.loads(result).get("type") == "audio_ready":
            self.accepted = True
        return result

    def draft(self):
        return {"type": "draft", "draft_id": "synthetic-session", "draft_version": 1,
                "fields": dict(self.fields), "ready_for_review": True}

    async def send(self, raw):
        if isinstance(raw, bytes):
            assert self.accepted, "PCM must wait until audio_ready is consumed"
            self.audio.append(raw)
            return
        event = json.loads(raw)
        self.sent.append(event)
        if event["type"] == "context":
            self.fields = dict(event["fields"])
            self.put({**self.draft(), "context_only": True})
            self.put({"type": "turn_complete", "context_only": True})
        elif event["type"] == "audio_start":
            self.put(self.owned(type="turn_started", activity_id=2 if self.mode == "wrong_activity" else 1))
            self.put(self.owned(type="audio_ready", activity_id=1,
                                provider_epoch=2 if self.mode == "wrong_accept_epoch" else 1))
        elif event["type"] == "audio_end":
            if self.mode == "timeout":
                return
            if self.mode == "error":
                self.put({"type": "error", "code": "PRIVATE_UNKNOWN_CODE", "message": "PRIVATE_PROVIDER_SECRET"})
                return
            if self.mode == "recovery":
                self.put({"type": "recovering", "provider_epoch": 2})
                return
            if self.mode != "no_transcription":
                self.put(self.owned(type="transcript", role="user", text="PRIVATE_INPUT_SPEECH"))
            self.put(self.owned(type="transcript", role="model", text="PRIVATE_MODEL_SPEECH"))
            output = (b"\0\0" if self.mode == "silent_output" else b"\x00\x10") * 2400
            if self.mode == "binary_output":
                self.put(output)
            elif self.mode != "no_audio":
                encoded = "NOT BASE64!" if self.mode == "bad_base64" else base64.b64encode(output).decode()
                for _ in range(301 if self.mode == "output_limit" else 1):
                    self.put(self.owned(type="audio", pcm=encoded,
                                        provider_epoch=2 if self.mode == "stale_audio" else 1))
            if self.mode in {"mutated_offer", "same_draft"}:
                self.fields["hours_remaining"] = 1.999
                if self.mode == "mutated_offer":
                    self.fields["donor_name"] = "PRIVATE_UNEXPECTED_DONOR"
                self.put(self.owned(type="action_result", effect="draft_proposed", outcome="applied",
                                    payload={"draft": self.draft()}))
            self.put(self.owned(type="turn_complete", turn_id="old" if self.mode == "stale_complete" else "turn-audio"))


def connection(socket, seen):
    @asynccontextmanager
    async def connect(uri, **kwargs):
        seen.update(uri=uri, kwargs=kwargs)
        try:
            yield socket
        finally:
            socket.closed = True
    return connect


async def no_delay(seconds):
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_real_audio_protocol_requires_transcription_and_owned_nonzero_pcm():
    socket, seen, delays = Socket(), {}, []

    async def pacer(seconds):
        delays.append(seconds)
        await asyncio.sleep(0)

    report = await probe("https://demo.run.app", PCM, connector=connection(socket, seen), now=NOW, sleep=pacer)
    assert report["passed"] and report["input_audio_sent"] and report["response_complete"]
    assert report["input_transcript_events"] == 1 and report["output_transcript_events"] == 1
    assert report["audio_bytes_received"] == 4800 and report["output_seconds"] == .1
    assert report["non_silent_output"] and report["no_draft_mutation_observed"]
    assert report["action_ownership_verified"] and report["audio_ready"]
    assert not report["physical_microphone_verified"] and not report["browser_playback_verified"]
    assert not report["match_request_sent"] and "PRIVATE" not in json.dumps(report)
    assert [event["type"] for event in socket.sent] == ["context", "audio_start", "audio_end", "stop"]
    assert socket.closed and report["input_frames_sent"] == len(socket.audio) == 50
    assert all(struct.unpack("!II", frame[:8]) == (1, 1) and len(frame) == 648 for frame in socket.audio)
    assert b"".join(frame[8:] for frame in socket.audio) == PCM
    assert all(delay == .02 for delay in delays) and sum(delays) == pytest.approx(1)
    assert seen["uri"] == "wss://demo.run.app/api/live/intake"
    assert seen["kwargs"]["origin"] == "https://demo.run.app" and "additional_headers" not in seen["kwargs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [
    ("old_protocol", "audio_protocol_not_ready"),
    ("wrong_activity", "invalid_audio_owner"),
    ("wrong_accept_epoch", "output_owner_mismatch"),
    ("stale_audio", "output_owner_mismatch"),
    ("stale_complete", "output_owner_mismatch"),
    ("binary_output", "unowned_audio"),
    ("bad_base64", "invalid_owned_audio"),
    ("output_limit", "response_audio_limit"),
    ("no_audio", "no_response_speech"),
    ("silent_output", "no_response_speech"),
    ("no_transcription", "no_input_transcription"),
    ("mutated_offer", "unexpected_draft_mutation"),
    ("recovery", "unexpected_recovery"),
    ("error", "server_error"),
    ("timeout", "probe_timeout"),
])
async def test_failure_is_bounded_stops_and_never_reports_provider_text(mode, code):
    socket = Socket(mode)
    result = await probe("https://demo.run.app", PCM, timeout=.05,
                         connector=connection(socket, {}), now=NOW, sleep=no_delay)
    assert not result["passed"] and result["error_code"] == code
    assert socket.closed and socket.sent[-1] == {"type": "stop"}
    assert "PRIVATE" not in json.dumps(result)
    if mode in {"old_protocol", "wrong_activity", "wrong_accept_epoch"}:
        assert socket.audio == []


@pytest.mark.asyncio
async def test_noop_proposal_with_elapsed_clock_is_not_a_mutation():
    socket = Socket("same_draft")
    result = await probe("https://demo.run.app", PCM, connector=connection(socket, {}), now=NOW, sleep=no_delay)
    assert result["passed"]


@pytest.mark.asyncio
async def test_timeout_during_upload_cancels_the_sender_before_stop():
    socket = Socket()
    result = await probe("https://demo.run.app", PCM, timeout=.025,
                         connector=connection(socket, {}), now=NOW)
    assert result["error_code"] == "probe_timeout"
    assert socket.closed and not result["input_audio_sent"]
    assert 0 < result["input_frames_sent"] < 50
    assert socket.sent[-1] == {"type": "stop"}
    count = len(socket.audio)
    await asyncio.sleep(.025)
    assert len(socket.audio) == count  # No background streaming after the bounded probe exits.


@pytest.mark.asyncio
async def test_short_last_frame_retains_exact_pcm_and_real_time_pacing():
    socket, delays = Socket(), []

    async def pacer(seconds):
        delays.append(seconds)
        await asyncio.sleep(0)

    result = await probe("https://demo.run.app", PCM + b"\x01\0", connector=connection(socket, {}),
                         now=NOW, sleep=pacer)
    assert result["passed"] and result["input_frames_sent"] == 51
    assert socket.audio[-1] == struct.pack("!II", 1, 1) + b"\x01\0"
    assert delays[-1] == 1 / 16000


def write_wav(path, *, rate=16000, channels=1, width=2, pcm=PCM):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(pcm)


def test_wav_reader_only_accepts_bounded_mono_pcm16_at_16khz(tmp_path):
    path = tmp_path / "synthetic.wav"
    write_wav(path)
    assert load_wav(path) == PCM
    for options in ({"rate": 24000}, {"channels": 2}, {"width": 1}):
        write_wav(path, **options)
        with pytest.raises(SmokeFailure, match="^invalid_wav_format$"):
            load_wav(path)
    for pcm, code in ((b"\x01\0" * 100, "invalid_input_duration"),
                      (b"\0" * 32000, "silent_input"),
                      (b"\x01\0" * 320001, "invalid_input_duration")):
        write_wav(path, pcm=pcm)
        with pytest.raises(SmokeFailure, match=f"^{code}$"):
            load_wav(path)


def test_truncated_or_missing_wav_fails_without_path_details(tmp_path):
    path = tmp_path / "PRIVATE.wav"
    with pytest.raises(SmokeFailure, match="^unreadable_wav$"):
        load_wav(path)
    write_wav(path)
    path.write_bytes(path.read_bytes()[:-2])
    with pytest.raises(SmokeFailure, match="^truncated_wav$"):
        load_wav(path)
