"""Public-demo typed Live smoke. No mic, credentials, transcript logs or broker calls."""
import argparse
import base64
import binascii
import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

MODEL = "gemini-3.8-live"
LOCATION = "us-central1"
UPDATE_TEXT = (
    "For this synthetic test, I have two pallets of sealed shelf-stable bread. "
    "Correct the pallet count to two. Keep the existing pickup deadline and location unchanged. "
    "Use draft_offer to propose this edit for review."
)


class SmokeFailure(Exception):
    """Stable failure code; never include provider output."""


def endpoints(base_url):
    try:
        parsed = urlsplit(base_url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or
                parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError
        if parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError
        _ = parsed.port
        origin = f"{parsed.scheme}://{parsed.netloc}"
        uri = f"{'wss' if parsed.scheme == 'https' else 'ws'}://{parsed.netloc}/api/live/intake"
        return origin, uri
    except ValueError:
        raise argparse.ArgumentTypeError("Use an HTTPS service origin, or HTTP localhost, without credentials/path/query")


def timestamp(value):
    if not isinstance(value, str):
        raise SmokeFailure("invalid_deadline")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise SmokeFailure("invalid_deadline")


def assert_draft(event, deadline, quantity, previous=None):
    fields = event.get("fields")
    if not isinstance(fields, dict) or event.get("ready_for_review") is not True:
        raise SmokeFailure("draft_not_ready")
    actual = fields.get("pallet_count")
    if (isinstance(actual, bool) or not isinstance(actual, (int, float)) or
            not math.isfinite(actual) or actual != quantity):
        raise SmokeFailure("quantity_not_updated")
    if timestamp(fields.get("deadline_at")) != deadline:
        raise SmokeFailure("deadline_changed")
    if fields.get("donor_lat") != 47.6062 or fields.get("donor_lon") != -122.3321:
        raise SmokeFailure("location_changed")
    version, identity = event.get("draft_version"), event.get("draft_id")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1 or not isinstance(identity, str) or not identity:
        raise SmokeFailure("invalid_draft_version")
    if previous and (identity != previous["draft_id"] or version <= previous["draft_version"]):
        raise SmokeFailure("stale_draft")


async def probe(base_url, *, timeout=90, connector=None, now=None):
    """Return a sanitized receipt. connector injection supports offline protocol tests."""
    import websockets
    origin, uri = endpoints(base_url)
    now = now or datetime.now(timezone.utc)
    deadline = now + timedelta(hours=2)
    context = {
        "posting_text": "Synthetic sealed shelf-stable bread; quantity is declared in the pallet field.",
        "donor_name": "Synthetic Live smoke test", "required_storage": "ambient",
        "pallet_count": 1, "hours_remaining": 2, "deadline_at": deadline.isoformat(),
        "donor_lat": 47.6062, "donor_lon": -122.3321,
    }
    report = {
        "probe": "typed_live_draft_v1", "service_origin": origin, "started_at": now.isoformat(),
        "model": MODEL, "location": LOCATION, "passed": False, "ready": False,
        "draft_updated": False, "deadline_preserved": False, "response_complete": False,
        "action_ownership_verified": False,
        "audio_bytes_received": 0, "transcript_events": 0, "match_request_sent": False,
    }
    connector = connector or websockets.connect
    owner, correlated, provider_epoch = None, False, None

    async def receive(ws):
        for _ in range(10000):
            raw = await ws.recv()
            if isinstance(raw, bytes):
                if correlated:
                    raise SmokeFailure("unowned_audio")
                report["audio_bytes_received"] += len(raw)
                if report["audio_bytes_received"] > 10_000_000:
                    raise SmokeFailure("response_audio_limit")
                continue
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                raise SmokeFailure("invalid_server_json")
            if not isinstance(event, dict):
                raise SmokeFailure("invalid_server_event")
            if event.get("type") == "error":
                # Known server codes only; never echo message/provider text.
                known = {"context_invalid", "session_timeout", "live_unavailable", "tool_limit",
                         "invalid_audio", "invalid_message", "message_too_large", "invalid_text"}
                code = event.get("code")
                raise SmokeFailure(code if code in known else "server_error")
            if correlated and event.get("type") in {"audio", "transcript"}:
                if (not owner or any(event.get(k) != v for k, v in owner.items())):
                    raise SmokeFailure("output_owner_mismatch")
            if event.get("type") == "audio":
                encoded = event.get("pcm")
                if not isinstance(encoded, str) or len(encoded) > 48000:
                    raise SmokeFailure("invalid_owned_audio")
                try:
                    pcm = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    raise SmokeFailure("invalid_owned_audio") from None
                if not pcm or len(pcm) % 2:
                    raise SmokeFailure("invalid_owned_audio")
                report["audio_bytes_received"] += len(pcm)
                if report["audio_bytes_received"] > 10_000_000:
                    raise SmokeFailure("response_audio_limit")
                continue
            if event.get("type") == "transcript":
                report["transcript_events"] += 1
            return event
        raise SmokeFailure("response_event_limit")

    try:
        async with asyncio.timeout(timeout):
            async with connector(uri, origin=origin, open_timeout=15, close_timeout=5,
                                 max_size=1_000_000, max_queue=32) as ws:
                try:
                    event = await receive(ws)
                    if (event.get("type") != "ready" or event.get("model") != MODEL or
                            event.get("location") != LOCATION):
                        raise SmokeFailure("unexpected_ready")
                    report["ready"] = True
                    correlated = event.get("input_correlation") is True
                    provider_epoch = event.get("provider_epoch")
                    if correlated and (type(provider_epoch) is not int or provider_epoch != 1):
                        raise SmokeFailure("unexpected_ready")
                    await ws.send(json.dumps({"type": "context", "fields": context}))
                    baseline = None
                    while True:
                        event = await receive(ws)
                        if event.get("type") == "draft":
                            assert_draft(event, deadline, 1)
                            baseline = event
                        if event.get("type") == "turn_complete" and baseline:
                            break
                    await ws.send(json.dumps({"type": "text", "text": UPDATE_TEXT,
                                              **({"request_id": 1} if correlated else {})}))
                    owner = None
                    while True:
                        event = await receive(ws)
                        if event.get("type") == "turn_started":
                            if correlated and (event.get("request_id") != 1 or event.get("provider_epoch") != provider_epoch):
                                raise SmokeFailure("input_owner_mismatch")
                            owner = {key: event.get(key) for key in (
                                "turn_id", "expected_offer_revision", "expected_view_revision")}
                            if correlated:
                                owner["provider_epoch"] = provider_epoch
                        if event.get("type") == "action_result" and event.get("effect") == "draft_proposed":
                            if (event.get("outcome") != "applied" or not owner
                                    or not owner.get("turn_id")
                                    or any(event.get(key) != value for key, value in owner.items())):
                                raise SmokeFailure("action_owner_mismatch")
                            event = event.get("payload", {}).get("draft", {})
                            report["action_ownership_verified"] = True
                        if event.get("type") == "draft":
                            assert_draft(event, deadline, 2, previous=baseline)
                            report["draft_updated"] = True
                            report["deadline_preserved"] = True
                        if event.get("type") == "turn_complete":
                            if correlated and (not owner or any(event.get(k) != v for k, v in owner.items())):
                                raise SmokeFailure("completion_owner_mismatch")
                            if not report["draft_updated"]:
                                raise SmokeFailure("model_did_not_update_draft")
                            report["response_complete"] = True
                            break
                    report["passed"] = True
                finally:
                    try:
                        await ws.send(json.dumps({"type": "stop"}))
                    except Exception:
                        pass
    except SmokeFailure as exc:
        report["error_code"] = str(exc)
    except TimeoutError:
        report["error_code"] = "probe_timeout"
    except Exception as exc:
        report["error_code"] = type(exc).__name__
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            report["http_status"] = status
    return report


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", type=int, default=90, help="Overall seconds, 5–150 (default 90)")
    args = parser.parse_args(argv)
    try:
        endpoints(args.base_url)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if not 5 <= args.timeout <= 150:
        parser.error("--timeout must be 5–150 seconds")
    return args


def main(argv=None):
    args = arguments(argv)
    report = asyncio.run(probe(args.base_url, timeout=args.timeout))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
