"""One bounded synthetic-speech Live turn; never opens a mic or dispatches a match.

Supply a nonpersonal, locally synthesized WAV saying e.g. "Please say the audio
check is working. Do not change the food offer." This verifies the deployed
audio transport and provider response, not browser capture or speaker playback.
"""
import argparse
import asyncio
import base64
import binascii
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import struct
import wave

try:
    from .verify_live_intake import MODEL, LOCATION, SmokeFailure, assert_draft, endpoints
except ImportError:  # python scripts/verify_live_audio.py
    from verify_live_intake import MODEL, LOCATION, SmokeFailure, assert_draft, endpoints

INPUT_RATE = 16000
OUTPUT_RATE = 24000
FRAME_BYTES = 640  # 20 ms, matching the browser AudioWorklet.
MAX_INPUT_BYTES = INPUT_RATE * 2 * 20
MAX_OUTPUT_BYTES = OUTPUT_RATE * 2 * 30
MIN_OUTPUT_BYTES = OUTPUT_RATE * 2 // 10


def validate_pcm(pcm):
    if not isinstance(pcm, bytes) or not INPUT_RATE * 2 <= len(pcm) <= MAX_INPUT_BYTES or len(pcm) % 2:
        raise SmokeFailure("invalid_input_duration")
    if not any(pcm):
        raise SmokeFailure("silent_input")


def load_wav(path):
    """Read at most 20 seconds of uncompressed 16-bit mono 16 kHz test speech."""
    try:
        with wave.open(str(path), "rb") as source:
            if (source.getnchannels() != 1 or source.getsampwidth() != 2
                    or source.getframerate() != INPUT_RATE or source.getcomptype() != "NONE"):
                raise SmokeFailure("invalid_wav_format")
            frames = source.getnframes()
            if not INPUT_RATE <= frames <= MAX_INPUT_BYTES // 2:
                raise SmokeFailure("invalid_input_duration")
            pcm = source.readframes(frames)
            if len(pcm) != frames * 2:
                raise SmokeFailure("truncated_wav")
    except (OSError, EOFError, wave.Error):
        raise SmokeFailure("unreadable_wav") from None
    validate_pcm(pcm)
    return pcm


async def probe(base_url, pcm, *, timeout=90, connector=None, now=None, sleep=asyncio.sleep):
    """Return sanitized evidence; injected transport/pacer permit offline tests."""
    validate_pcm(pcm)
    origin, uri = endpoints(base_url)
    if connector is None:
        import websockets
        connector = websockets.connect
    now = now or datetime.now(timezone.utc)
    deadline = now + timedelta(hours=2)
    context = {
        "posting_text": "Synthetic sealed shelf-stable bread; quantity is declared in the pallet field.",
        "donor_name": "Synthetic Live audio check", "required_storage": "ambient",
        "pallet_count": 1, "hours_remaining": 2, "deadline_at": deadline.isoformat(),
        "donor_lat": 47.6062, "donor_lon": -122.3321,
    }
    report = {
        "probe": "synthetic_audio_live_v1", "service_origin": origin, "started_at": now.isoformat(),
        "model": MODEL, "location": LOCATION, "passed": False, "ready": False,
        "audio_ready": False, "input_audio_sent": False, "input_frames_sent": 0,
        "input_bytes_sent": 0, "input_seconds": len(pcm) / (INPUT_RATE * 2),
        "input_sha256": hashlib.sha256(pcm).hexdigest(), "input_transcript_events": 0,
        "output_transcript_events": 0, "audio_bytes_received": 0, "output_seconds": 0,
        "non_silent_output": False, "response_complete": False,
        "action_ownership_verified": False, "no_draft_mutation_observed": False,
        "match_request_sent": False, "physical_microphone_verified": False,
        "browser_playback_verified": False,
    }
    owner, baseline, upload = None, None, None
    event_count = 0

    def assert_owner(event):
        if owner is None or any(type(event.get(key)) is not type(value) or event[key] != value
                                for key, value in owner.items()):
            raise SmokeFailure("output_owner_mismatch")

    def unchanged(snapshot):
        assert_draft(snapshot, deadline, 1)
        # Remaining hours naturally decrease while the absolute deadline stays fixed.
        stable = lambda fields: {k: v for k, v in fields.items() if k not in {"hours_remaining", "deadline_at"}}
        hours = snapshot["fields"].get("hours_remaining")
        if (type(hours) not in {int, float} or not math.isfinite(hours) or hours < 0
                or hours > baseline["fields"]["hours_remaining"] + 1e-6
                or stable(snapshot["fields"]) != stable(baseline["fields"])
                or snapshot.get("draft_id") != baseline.get("draft_id")):
            raise SmokeFailure("unexpected_draft_mutation")

    async def receive(ws):
        nonlocal event_count
        raw = await ws.recv()
        event_count += 1
        if event_count > 3000:
            raise SmokeFailure("event_limit")
        if isinstance(raw, bytes):
            raise SmokeFailure("unowned_audio")
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            raise SmokeFailure("invalid_server_json") from None
        if not isinstance(event, dict):
            raise SmokeFailure("invalid_server_event")
        kind = event.get("type")
        if kind == "error":
            known = {"context_invalid", "session_timeout", "live_unavailable", "tool_limit",
                     "invalid_audio", "invalid_message", "message_too_large", "invalid_activity",
                     "provider_closed", "provider_connect_timeout", "provider_cleanup_timeout"}
            code = event.get("code")
            raise SmokeFailure(code if code in known else "server_error")
        if kind in {"recovering", "resumed", "interrupted"}:
            raise SmokeFailure("unexpected_recovery")
        if kind in {"audio", "transcript", "action_result", "audio_ready"}:
            assert_owner(event)
        return event

    async def send_audio(ws):
        for offset in range(0, len(pcm), FRAME_BYTES):
            chunk = pcm[offset:offset + FRAME_BYTES]
            await ws.send(struct.pack("!II", 1, 1) + chunk)
            report["input_frames_sent"] += 1
            report["input_bytes_sent"] += len(chunk)
            await sleep(len(chunk) / (INPUT_RATE * 2))
        await ws.send(json.dumps({"type": "audio_end", "provider_epoch": 1, "activity_id": 1}))
        report["input_audio_sent"] = True

    try:
        async with asyncio.timeout(timeout):
            async with connector(uri, origin=origin, open_timeout=15, close_timeout=3,
                                 max_size=100_000, max_queue=32) as ws:
                try:
                    ready = await receive(ws)
                    if (ready.get("type") != "ready" or ready.get("model") != MODEL
                            or ready.get("location") != LOCATION or type(ready.get("provider_epoch")) is not int
                            or ready["provider_epoch"] != 1 or ready.get("audio_activity") is not True
                            or ready.get("input_correlation") is not True):
                        raise SmokeFailure("audio_protocol_not_ready")
                    report["ready"] = True
                    await ws.send(json.dumps({"type": "context", "fields": context}))
                    baseline = await receive(ws)
                    if baseline.get("type") != "draft" or baseline.get("context_only") is not True:
                        raise SmokeFailure("missing_context_draft")
                    assert_draft(baseline, deadline, 1)
                    complete = await receive(ws)
                    if complete.get("type") != "turn_complete" or complete.get("context_only") is not True:
                        raise SmokeFailure("missing_context_complete")
                    await ws.send(json.dumps({"type": "audio_start", "provider_epoch": 1, "activity_id": 1}))
                    started = await receive(ws)
                    if (started.get("type") != "turn_started" or type(started.get("provider_epoch")) is not int
                            or started["provider_epoch"] != 1 or type(started.get("activity_id")) is not int
                            or started["activity_id"] != 1 or not isinstance(started.get("turn_id"), str)
                            or not started["turn_id"] or any(type(started.get(key)) is not int or started[key] != 0
                                for key in ("expected_offer_revision", "expected_view_revision"))):
                        raise SmokeFailure("invalid_audio_owner")
                    owner = {key: started[key] for key in (
                        "turn_id", "expected_offer_revision", "expected_view_revision", "provider_epoch")}
                    accepted = await receive(ws)
                    if accepted.get("type") != "audio_ready" or type(accepted.get("activity_id")) is not int or accepted["activity_id"] != 1:
                        raise SmokeFailure("audio_not_accepted")
                    report["audio_ready"] = report["action_ownership_verified"] = True
                    upload = asyncio.create_task(send_audio(ws))
                    while True:
                        event = await receive(ws)
                        if upload.done():
                            upload.result()
                        kind = event.get("type")
                        if kind == "audio":
                            encoded = event.get("pcm")
                            if not isinstance(encoded, str) or len(encoded) > 48000:
                                raise SmokeFailure("invalid_owned_audio")
                            try:
                                output = base64.b64decode(encoded, validate=True)
                            except (ValueError, binascii.Error):
                                raise SmokeFailure("invalid_owned_audio") from None
                            if not output or len(output) % 2:
                                raise SmokeFailure("invalid_owned_audio")
                            report["audio_bytes_received"] += len(output)
                            if report["audio_bytes_received"] > MAX_OUTPUT_BYTES:
                                raise SmokeFailure("response_audio_limit")
                            report["non_silent_output"] |= any(output)
                        elif kind == "transcript":
                            if isinstance(event.get("text"), str) and event["text"].strip():
                                if event.get("role") == "user":
                                    report["input_transcript_events"] += 1
                                elif event.get("role") == "model":
                                    report["output_transcript_events"] += 1
                        elif kind == "action_result":
                            payload = event.get("payload", {})
                            if isinstance(payload, dict) and isinstance(payload.get("draft"), dict):
                                unchanged(payload["draft"])
                        elif kind == "draft":
                            assert_owner(event)
                            unchanged(event)
                        elif kind == "turn_complete":
                            assert_owner(event)
                            if not report["input_audio_sent"]:
                                raise SmokeFailure("premature_audio_complete")
                            report["response_complete"] = True
                            break
                        elif kind in {"turn_started", "ready", "audio_ready"}:
                            raise SmokeFailure("unexpected_audio_turn")
                    if not report["input_transcript_events"]:
                        raise SmokeFailure("no_input_transcription")
                    if report["audio_bytes_received"] < MIN_OUTPUT_BYTES or not report["non_silent_output"]:
                        raise SmokeFailure("no_response_speech")
                    report["no_draft_mutation_observed"] = report["passed"] = True
                finally:
                    if upload is not None:
                        upload.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await upload
                    with suppress(Exception):
                        await asyncio.wait_for(ws.send(json.dumps({"type": "stop"})), timeout=2)
    except SmokeFailure as exc:
        report["error_code"] = str(exc)
    except TimeoutError:
        report["error_code"] = "probe_timeout"
    except Exception as exc:
        report["error_code"] = "transport_failure"
        report["exception_type"] = type(exc).__name__
    report["output_seconds"] = report["audio_bytes_received"] / (OUTPUT_RATE * 2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--wav", required=True, help="Nonpersonal synthesized mono PCM16 16 kHz WAV, 1–20 seconds")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if not 5 <= args.timeout <= 150:
        parser.error("timeout must be 5–150 seconds")
    try:
        endpoints(args.base_url)
        pcm = load_wav(args.wav)
        report = asyncio.run(probe(args.base_url, pcm, timeout=args.timeout))
    except (SmokeFailure, argparse.ArgumentTypeError) as exc:
        report = {"probe": "synthetic_audio_live_v1", "passed": False,
                  "error_code": str(exc) if isinstance(exc, SmokeFailure) else "invalid_service_origin"}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
