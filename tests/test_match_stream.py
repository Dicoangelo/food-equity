"""Offline behavior checks for bounded request-scoped matching progress."""
import asyncio
import importlib.util
import json
from pathlib import Path
from uuid import UUID

import pytest
from starlette.requests import ClientDisconnect


spec = importlib.util.spec_from_file_location(
    "food_match_stream", Path(__file__).parents[1] / "agent/match_stream.py",
)
stream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stream)


async def collect(run, **kwargs):
    return [json.loads(line) async for line in stream.stream_match(run, **kwargs)]


@pytest.mark.asyncio
async def test_accepted_and_real_progress_arrive_before_result():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def run(report):
        entered.set()
        await report("retrieving", {})
        await release.wait()
        return {"status": "draft", "requires_confirmation": True}

    iterator = stream.stream_match(run)
    first = json.loads(await anext(iterator))
    assert first["stage"] == "accepted"
    assert not entered.is_set()
    second = json.loads(await anext(iterator))
    assert entered.is_set()
    assert second["stage"] == "retrieving"
    assert not release.is_set()
    release.set()
    rest = [json.loads(line) async for line in iterator]
    assert rest[-1]["type"] == "result"
    assert rest[-1]["data"] == {"status": "draft", "requires_confirmation": True}
    records = [first, second, *rest]
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))
    assert len({r["run_id"] for r in records}) == 1
    assert str(UUID(first["run_id"])) == first["run_id"]


@pytest.mark.asyncio
async def test_progress_only_exposes_allowlisted_stages_and_bounded_counts():
    async def run(report):
        await report("retrieving", {
            "candidate_count": 12, "retrieved_count": 0,
            "eligible_count": True, "pending_count": -1,
            "rejected_count": 1_000_001,
            "message": "secret", "trace": ["private"], "run_id": "spoof",
            "sequence": 900, "data": {"secret": "private"},
        })
        await report("checking_constraints", {
            "candidate_count": 12.5, "eligible_count": float("nan"),
            "rejected_count": "2", "pending_count": 2,
        })
        await report("accepted", {"candidate_count": 99})
        await report("arbitrary_private_stage", {})
        await report("finalizing", None)
        return {"status": "no_viable_match"}

    records = await collect(run)
    progress = [r for r in records if r["type"] == "progress"]
    assert [r["stage"] for r in progress] == [
        "accepted", "retrieving", "checking_constraints", "finalizing",
    ]
    assert set(progress[1]) == {
        "type", "stage", "run_id", "sequence", "candidate_count", "retrieved_count",
    }
    assert progress[1]["candidate_count"] == 12
    assert progress[1]["retrieved_count"] == 0
    assert set(progress[2]) == {"type", "stage", "run_id", "sequence", "pending_count"}
    assert progress[2]["pending_count"] == 2
    assert set(progress[3]) == {"type", "stage", "run_id", "sequence"}
    assert "secret" not in json.dumps(records)
    assert "private" not in json.dumps(records)


@pytest.mark.asyncio
async def test_result_preserves_existing_payload_without_status_reinterpretation():
    payload = {
        "status": "blocked", "requires_confirmation": True,
        "execution_trace": [{"kind": "mcp_tool_result", "count": 12}],
        "pending_candidates": [{"recipient_id": "fixture", "facts_needed": ["capacity"]}],
    }

    async def run(report):
        return payload

    records = await collect(run)
    assert len(records) == 2
    assert records[-1]["data"] == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [
    RuntimeError("private credential and raw upstream response"),
    TimeoutError("provider timeout secret"),
])
async def test_provider_exception_is_generic_and_terminal(exception):
    async def run(report):
        await report("connecting", {})
        raise exception

    records = await collect(run)
    assert records[-1]["type"] == "error"
    assert records[-1]["code"] == "match_failed"
    assert "secret" not in json.dumps(records)
    assert "credential" not in json.dumps(records)
    assert sum(r["type"] == "error" for r in records) == 1


@pytest.mark.asyncio
async def test_whole_operation_timeout_cancels_runner():
    cancelled = asyncio.Event()

    async def run(report):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    records = await collect(
        run, timeout_seconds=0.04, heartbeat_seconds=0.01,
        cleanup_timeout_seconds=0.1,
    )
    assert records[0]["stage"] == "accepted"
    assert records[-1]["type"] == "error"
    assert records[-1]["code"] == "match_timeout"
    assert cancelled.is_set()
    assert any(r["type"] == "heartbeat" for r in records)
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))


@pytest.mark.asyncio
async def test_closing_iterator_cancels_producer_without_finishing_match():
    cancelled = asyncio.Event()
    completed = False

    async def run(report):
        nonlocal completed
        try:
            await report("retrieving", {})
            await asyncio.Event().wait()
            completed = True
        finally:
            cancelled.set()

    iterator = stream.stream_match(run)
    await anext(iterator)
    assert json.loads(await anext(iterator))["stage"] == "retrieving"
    await iterator.aclose()
    assert cancelled.is_set()
    assert not completed


@pytest.mark.asyncio
async def test_disconnected_pending_read_cancels_producer():
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def run(report):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    iterator = stream.stream_match(run)
    await anext(iterator)
    read = asyncio.create_task(anext(iterator))
    await entered.wait()
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    assert cancelled.is_set()
    await iterator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("asgi_version", ["2.3", "2.4"])
async def test_response_owns_iterator_when_disconnect_interrupts_send(asgi_version):
    cancelled = asyncio.Event()
    progress_send_started = asyncio.Event()

    async def run(report):
        try:
            await report("retrieving", {})
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    iterator = stream.stream_match(run, cleanup_timeout_seconds=0.02)
    response = stream.MatchStreamingResponse(iterator, media_type="application/x-ndjson")
    body_count = 0

    async def send(message):
        nonlocal body_count
        if message["type"] == "http.response.body":
            body_count += 1
            if body_count == 2:
                progress_send_started.set()
                if asgi_version == "2.4":
                    raise OSError("simulated disconnected transport")
                # ASGI 2.3 detects disconnect through receive and cancels send.
                await asyncio.Event().wait()

    async def receive():
        await progress_send_started.wait()
        return {"type": "http.disconnect"}

    try:
        call = response({"type": "http", "asgi": {"spec_version": asgi_version}}, receive, send)
        if asgi_version == "2.4":
            with pytest.raises(ClientDisconnect):
                await asyncio.wait_for(call, timeout=0.5)
        else:
            await asyncio.wait_for(call, timeout=0.5)
        # Keep both response and iterator alive: cleanup must not depend on GC.
        assert cancelled.is_set()
        assert iterator.ag_frame is None
    finally:
        await iterator.aclose()


@pytest.mark.asyncio
async def test_response_disconnect_cleanup_remains_bounded_if_runner_defers_cancellation():
    first_cancel = asyncio.Event()
    stopped = asyncio.Event()

    async def run(report):
        await report("retrieving", {})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            first_cancel.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    iterator = stream.stream_match(run, cleanup_timeout_seconds=0.01)
    response = stream.MatchStreamingResponse(iterator)
    body_count = 0

    async def send(message):
        nonlocal body_count
        if message["type"] == "http.response.body":
            body_count += 1
            if body_count == 2:
                raise OSError("simulated disconnected transport")

    async def receive():
        await asyncio.Event().wait()

    try:
        with pytest.raises(ClientDisconnect):
            await asyncio.wait_for(
                response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send),
                timeout=0.2,
            )
        assert first_cancel.is_set()
        await asyncio.wait_for(stopped.wait(), timeout=0.2)
    finally:
        await iterator.aclose()


@pytest.mark.asyncio
async def test_bounded_queue_backpressures_without_dropping_progress():
    progress_returned = []
    runner_done = asyncio.Event()

    async def run(report):
        for count in range(10):
            await report("checking_constraints", {"candidate_count": count})
            progress_returned.append(count)
        runner_done.set()
        return {"status": "draft"}

    iterator = stream.stream_match(run, queue_size=1)
    await anext(iterator)
    first_progress = json.loads(await anext(iterator))
    # The reader is now paused; allow the producer to fill its single queue slot.
    for _ in range(3):
        await asyncio.sleep(0)
    assert not runner_done.is_set()
    assert len(progress_returned) <= 2
    remaining = [json.loads(line) async for line in iterator]
    counts = [
        r["candidate_count"] for r in [first_progress, *remaining]
        if r["type"] == "progress"
    ]
    assert counts == list(range(10))
    assert runner_done.is_set()
    assert remaining[-1]["type"] == "result"


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [
    {"value": object()},
    {"value": float("nan")},
    ["wrong response shape"],
])
async def test_invalid_result_becomes_sanitized_terminal_error(result):
    async def run(report):
        return result

    records = await collect(run)
    assert records[-1]["type"] == "error"
    assert records[-1]["code"] == "match_failed"
    assert len(records) == 2


@pytest.mark.asyncio
async def test_bounded_cleanup_when_upstream_delays_first_cancellation():
    first_cancel = asyncio.Event()
    finally_cancelled = asyncio.Event()

    async def run(report):
        await report("connecting", {})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            first_cancel.set()
            try:
                await asyncio.Event().wait()
            finally:
                finally_cancelled.set()

    iterator = stream.stream_match(run, cleanup_timeout_seconds=0.01)
    await anext(iterator)
    await anext(iterator)
    await asyncio.wait_for(iterator.aclose(), timeout=0.2)
    assert first_cancel.is_set()
    await asyncio.wait_for(finally_cancelled.wait(), timeout=0.2)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"queue_size": 0}, {"queue_size": True},
    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"heartbeat_seconds": float("inf")}, {"cleanup_timeout_seconds": -1},
])
async def test_invalid_bounds_rejected_before_starting_runner(kwargs):
    called = False

    async def run(report):
        nonlocal called
        called = True
        return {}

    with pytest.raises(ValueError):
        await collect(run, **kwargs)
    assert not called
