"""Request-scoped NDJSON progress for matching; no persistence or background jobs.

The caller validates the offer before returning a StreamingResponse. The supplied
runner receives an asynchronous progress callback and returns the ordinary broker
payload. Disconnect cancellation closes this iterator and cancels its runner.
"""
import asyncio
import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from anyio import CancelScope
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


ProgressCallback = Callable[[str, Mapping[str, Any]], Awaitable[None]]
MatchRunner = Callable[[ProgressCallback], Awaitable[dict]]

PROGRESS_STAGES = frozenset({
    "connecting", "retrieving", "checking_constraints",
    "enriching_evidence", "preparing_brief", "finalizing",
})
COUNT_FIELDS = frozenset({
    "candidate_count", "retrieved_count", "eligible_count",
    "rejected_count", "pending_count",
})
MAX_PROGRESS_COUNT = 1_000_000


class MatchStreamingResponse(StreamingResponse):
    """Own the match iterator even when disconnect interrupts sending a chunk."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # StreamingResponse can leave an async generator suspended at yield
            # when send raises or is cancelled. Do not rely on GC to cancel the
            # provider worker. stream_match owns a bounded cleanup operation.
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                with CancelScope(shield=True):
                    await close()


def _progress_details(details: Mapping[str, Any]) -> dict[str, int]:
    """Only bounded counts leave the broker through the progress channel."""
    if not isinstance(details, Mapping):
        return {}
    return {
        key: value
        for key in COUNT_FIELDS
        if type(value := details.get(key)) is int
        and 0 <= value <= MAX_PROGRESS_COUNT
    }


def _error(code: str) -> dict:
    return {
        "type": "error",
        "code": code,
        "message": (
            "Matching took too long. Please retry."
            if code == "match_timeout"
            else "Matching could not finish. Please retry."
        ),
    }


def _consume_task_exception(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def _cancel_producer(task: asyncio.Task, timeout: float) -> None:
    """Bound cleanup even if an upstream coroutine delays cancellation."""
    # Register before awaiting: repeated request cancellation must not leave an
    # eventual producer exception unobserved.
    task.add_done_callback(_consume_task_exception)
    if task.done():
        return
    task.cancel()
    try:
        # Starlette uses an AnyIO cancel scope on ASGI 2.3 disconnect. Shield
        # cleanup from that scope while preserving its own strict timeout.
        with CancelScope(shield=True):
            await asyncio.wait({task}, timeout=timeout)
    finally:
        if not task.done():
            # Python cannot forcibly terminate a coroutine suppressing
            # cancellation. Ask again without retaining the response forever.
            task.cancel()


async def stream_match(
    run: MatchRunner,
    *,
    timeout_seconds: float = 160,
    heartbeat_seconds: float = 15,
    queue_size: int = 8,
    cleanup_timeout_seconds: float = 1,
) -> AsyncIterator[str]:
    """Yield ordered NDJSON records for one validated, non-durable match run.

    ``await report(stage, details)`` applies backpressure through a bounded queue;
    valid progress events are not discarded to make room for newer events.
    ``accepted`` means only that the server accepted the request. ``result.data``
    is the original broker response and retains its status and confirmation rules.
    Unknown stages and non-allowlisted progress details are never transmitted.
    """
    for value in (timeout_seconds, heartbeat_seconds, cleanup_timeout_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Stream durations must be positive finite numbers")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Stream durations must be positive finite numbers")
    if type(queue_size) is not int or queue_size < 1:
        raise ValueError("Stream queue size must be a positive integer")

    run_id = str(uuid4())
    sequence = 0
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=queue_size)
    producer: asyncio.Task | None = None

    def encode(message: dict) -> str:
        nonlocal sequence
        sequence += 1
        return json.dumps(
            {**message, "run_id": run_id, "sequence": sequence},
            separators=(",", ":"), allow_nan=False,
        ) + "\n"

    async def report(stage: str, details: Mapping[str, Any]) -> None:
        if isinstance(stage, str) and stage in PROGRESS_STAGES:
            await queue.put({
                "type": "progress",
                "stage": stage,
                **_progress_details(details),
            })

    async def produce() -> None:
        timer = asyncio.timeout(timeout_seconds)
        try:
            async with timer:
                result = await run(report)
                if not isinstance(result, dict):
                    raise TypeError("The match runner must return a dictionary")
                # Validate before queueing so serialization errors cannot expose
                # an exception or leave the client waiting without a terminal.
                json.dumps(result, allow_nan=False)
                await queue.put({"type": "result", "data": result})
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await queue.put(_error(
                "match_timeout" if timer.expired() else "match_failed",
            ))
        except Exception:
            await queue.put(_error("match_failed"))

    try:
        # No provider work starts until the response consumer receives accepted.
        yield encode({"type": "progress", "stage": "accepted"})
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        producer = asyncio.create_task(produce(), name=f"match-stream-{run_id}")
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                yield encode(_error("match_timeout"))
                return
            try:
                message = await asyncio.wait_for(
                    queue.get(), timeout=min(heartbeat_seconds, remaining),
                )
            except TimeoutError:
                if asyncio.get_running_loop().time() >= deadline:
                    yield encode(_error("match_timeout"))
                    return
                yield encode({"type": "heartbeat"})
                continue
            yield encode(message)
            if message["type"] in {"result", "error"}:
                return
    finally:
        if producer is not None:
            await _cancel_producer(producer, cleanup_timeout_seconds)
