"""Fresh exact-ID dataset evidence through the existing read-only MCP Toolbox.

No Gemini, matcher, ranking, deadline mutation, contact or operational action.
Retrieval proves only what this dataset returned, never recipient availability.
"""
import asyncio
from datetime import datetime, timezone
from functools import lru_cache
import json
import logging
import os
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse

try:
    from .city import _boolean, _number, _text
except ImportError:
    from city import _boolean, _number, _text

router = APIRouter()
logger = logging.getLogger(__name__)
TOOL_NAME = "get_recipient_evidence"
MAX_PAYLOAD_BYTES = 131_072
SOURCE = {
    "kind": "bigquery_recipient_snapshot",
    "table": "a4i_food.recipient_embeddings",
    "transport": "mcp", "tool": TOOL_NAME,
    "source_observed_at": None, "operational_confirmed_at": None,
}
MESSAGES = {
    "ready": "Stored profile evidence retrieved. Current acceptance, capacity and pickup remain unconfirmed; the current match has not changed.",
    "unknown": "The dataset response did not identify one unambiguous usable profile. No evidence or match was substituted.",
    "not_found": "No stored profile was returned for this exact recipient ID. This is not a statement about real-world availability.",
    "unavailable": "Recipient evidence could not be retrieved. The current match has not changed; retry the evidence read.",
    "invalid_id": "Use one canonical recipient identifier from the loaded directory.",
}


def canonical_id(value):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", value):
        raise ValueError("Invalid recipient identifier")
    return value


class EvidenceInvalid(ValueError):
    pass


class ToolReportedError(ValueError):
    pass


def parse_rows(payload, depth=0):
    """Accept the existing Toolbox row envelopes, with explicit size/depth bounds."""
    if depth > 4:
        raise EvidenceInvalid("Nested envelope")
    try:
        if len((payload if isinstance(payload, str) else json.dumps(
                payload, allow_nan=False)).encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise EvidenceInvalid("Oversized evidence")
    except (TypeError, ValueError) as exc:
        raise EvidenceInvalid("Invalid JSON evidence") from exc
    if isinstance(payload, str):
        try:
            return parse_rows(json.loads(payload), depth + 1)
        except (ValueError, TypeError) as exc:
            if isinstance(exc, ToolReportedError):
                raise
            raise EvidenceInvalid("Invalid JSON evidence") from exc
    if isinstance(payload, dict):
        if payload.get("error") or payload.get("isError"):
            raise ToolReportedError("MCP tool failed")
        for field in ("result", "rows"):
            if field in payload:
                return parse_rows(payload[field], depth + 1)
        if "content" in payload:
            blocks = payload["content"]
            if not isinstance(blocks, list) or len(blocks) > 2:
                raise EvidenceInvalid("Invalid content envelope")
            rows = []
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "text":
                    raise EvidenceInvalid("Unexpected content")
                rows.extend(parse_rows(block.get("text"), depth + 1))
            return parse_rows(rows, depth + 1)
        if "recipient_id" in payload:
            payload = [payload]
    if not isinstance(payload, list) or len(payload) > 2 or any(not isinstance(row, dict) for row in payload):
        raise EvidenceInvalid("Expected at most two rows")
    return payload


def normalize_profile(row, identifier):
    """Allowlist fields, preserve unknowns and retain profiles without map coordinates."""
    if canonical_id(row.get("recipient_id")) != identifier:
        raise EvidenceInvalid("Unexpected recipient identifier")
    name = _text(row.get("name"), 160)
    if name is None:
        raise EvidenceInvalid("Missing profile name")
    profile = {key: _text(row.get(key), limit) for key, limit in (
        ("city", 160), ("zip_code", 32), ("archetype", 160), ("service_days", 160),
        ("profile_text", 4000), ("tract_id", 100),
    )}
    cold = _text(row.get("cold_storage"), 40)
    source = row.get("profile_source")
    source = source if source in ("generated", "planted") else "unknown"
    profile.update({
        "recipient_id": identifier, "name": name,
        "lat": _number(row.get("latitude"), -90, 90),
        "lon": _number(row.get("longitude"), -180, 180),
        "cold_storage": cold.lower() if cold else None,
        "frozen_storage": _boolean(row.get("frozen_storage")),
        "loading_dock": _boolean(row.get("loading_dock")),
        "refrigerated_transport": _boolean(row.get("refrigerated_transport")),
        "max_pallets": _number(row.get("max_pallets"), 0),
        "pickup_window_hours": _number(row.get("pickup_window_hours"), 0),
        "weekly_households": _number(row.get("weekly_households"), 0),
        "tract_poverty_rate": _number(row.get("tract_poverty_rate"), 0, 1),
        "tract_no_vehicle_rate": _number(row.get("tract_no_vehicle_rate"), 0, 1),
        "tract_median_income": _number(row.get("tract_median_income"), 0),
        "tract_dist_km": _number(row.get("tract_dist_km"), 0),
        "profile_source": source, "synthetic_organization": source == "planted",
        "profile_text_truncated": row.get("profile_text_truncated") is True or
            (isinstance(row.get("profile_text"), str) and len(row["profile_text"]) > 4000),
        "geography_precision": "zip_centroid", "operational_data_verified": False,
        "requires_confirmation": True,
        "cold_storage_confirmation_required": cold is None or cold.lower() not in
            {"none", "dry", "ambient", "domestic", "commercial", "walk-in"},
    })
    # A partial coordinate pair is not a usable location.
    if profile["lat"] is None or profile["lon"] is None:
        profile["lat"] = profile["lon"] = None
    return profile


def response(identifier, status, *, profile=None, retrieved_at=None, code=None):
    return {
        "status": status, "recipient_id": identifier, "profile": profile,
        "retrieved_at": retrieved_at, "fetched_at": retrieved_at,
        "observation_date": None, "source": dict(SOURCE),
        "requires_confirmation": True, "operational_data_verified": False,
        "unknown_fields": sorted(key for key, value in (profile or {}).items() if value is None),
        "code": code, "message": MESSAGES[status],
    }


async def bounded(awaitable, timeout):
    """Stop awaiting at the bound, even if a provider delays cancellation."""
    task = asyncio.ensure_future(awaitable)
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            raise TimeoutError
        return task.result()
    finally:
        if not task.done():
            task.cancel()


def create_toolset():
    # Imports and ADC discovery remain lazy; serving UI/health does no cloud work.
    from toolbox_adk import CredentialStrategy, ToolboxToolset
    from toolbox_core.protocol import Protocol
    url = os.getenv("TOOLBOX_URL", "http://127.0.0.1:5000")
    credentials = None
    if url.startswith("https://"):
        credentials = CredentialStrategy.workload_identity(target_audience=url)
    elif url not in ("http://127.0.0.1:5000", "http://localhost:5000"):
        raise ValueError("Unencrypted Toolbox must be loopback-only")
    return ToolboxToolset(server_url=url, tool_names=[TOOL_NAME], credentials=credentials,
                          protocol=Protocol.MCP_v20250326)


class RecipientEvidenceService:
    def __init__(self, toolset_factory=create_toolset, *, timeout_seconds=25, cleanup_seconds=2):
        self.toolset_factory = toolset_factory
        self.timeout_seconds, self.cleanup_seconds = timeout_seconds, cleanup_seconds

    async def get(self, identifier):
        identifier = canonical_id(identifier)
        toolset = None
        retrieved_at = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                toolset = self.toolset_factory()
                tools = await bounded(toolset.get_tools(), 5)
                matches = [tool for tool in tools if tool.name == TOOL_NAME]
                if len(matches) != 1:
                    raise ToolReportedError("Required exact-ID MCP tool unavailable")
                # toolbox-adk 1.4 permits None for server-auth tools without per-user context.
                raw = await bounded(matches[0].run_async(
                    args={"recipient_id": identifier}, tool_context=None), 20)
                rows = parse_rows(raw)
                retrieved_at = datetime.now(timezone.utc).isoformat()
                if not rows:
                    return response(identifier, "not_found", retrieved_at=retrieved_at, code="recipient_not_found")
                if len(rows) != 1:
                    return response(identifier, "unknown", retrieved_at=retrieved_at, code="ambiguous_recipient_id")
                try:
                    profile = normalize_profile(rows[0], identifier)
                except ValueError as exc:
                    raise EvidenceInvalid("Invalid exact-ID evidence") from exc
                return response(identifier, "ready", profile=profile, retrieved_at=retrieved_at)
        except EvidenceInvalid:
            return response(identifier, "unknown", retrieved_at=retrieved_at, code="invalid_evidence")
        except TimeoutError:
            return response(identifier, "unavailable", code="evidence_timeout")
        except Exception as exc:
            logger.warning("Recipient evidence unavailable (%s)", type(exc).__name__)
            return response(identifier, "unavailable", code="evidence_unavailable")
        finally:
            if toolset is not None:
                try:
                    await bounded(toolset.close(), self.cleanup_seconds)
                except Exception:
                    logger.warning("Recipient evidence MCP cleanup did not complete")


@lru_cache(maxsize=1)
def get_evidence_service():
    return RecipientEvidenceService()


@router.get("/api/recipients/{recipient_id}/evidence")
async def recipient_evidence(recipient_id: str):
    try:
        identifier = canonical_id(recipient_id)
    except ValueError:
        return JSONResponse(status_code=422, headers={"Cache-Control": "no-store"}, content={
            **response(recipient_id[:100], "unknown", code="invalid_recipient_id"),
            "message": MESSAGES["invalid_id"],
        })
    result = await get_evidence_service().get(identifier)
    status = {"ready": 200, "unknown": 409, "not_found": 404, "unavailable": 503}[result["status"]]
    return JSONResponse(status_code=status, content=result, headers={"Cache-Control": "no-store"})
