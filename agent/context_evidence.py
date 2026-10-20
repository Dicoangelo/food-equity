"""Bounded MCP reference reads for an already validated offer and candidate set.

await ContextEvidenceService().collect(
    food_terms=["bread"], candidate_tract_ids=["53033000100"])

The caller supplies literal food terms and tract IDs from retrieved candidates.
This service accepts no offer, deadline, ranking or dispatch action. Its output
is advisory evidence; failures never create replacement matches or freshness.
"""
import asyncio
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
import re
from typing import Any, Literal, TypedDict
import unicodedata

try:
    from .foodkeeper import FoodKeeperService
    from .recipient_evidence import bounded
except ImportError:
    from foodkeeper import FoodKeeperService
    from recipient_evidence import bounded

FOODKEEPER_SHA256 = "e84caab9d4b603209e31d21d5027c41d29adfecbca7051ff7322e9fb0226c01d"
TOOLS = ("lookup_shelf_life", "get_tract_equity")
MAX_PAYLOAD_BYTES = 131_072
MAX_LOOKUPS_PER_KIND = 3
MAX_INPUTS = 30
Status = Literal["ready", "no_match", "unknown", "unavailable"]


class EvidenceEntry(TypedDict):
    status: Status
    request: dict[str, str]
    source: dict[str, Any]
    retrieved_at: str | None
    data: dict[str, Any] | None
    code: str | None
    requires_confirmation: bool
    operational_data_verified: bool


class ContextEvidence(TypedDict):
    status: Literal["ready", "partial", "unknown", "unavailable", "not_requested"]
    foodkeeper: list[EvidenceEntry]
    tract_equity: list[EvidenceEntry]
    execution_trace: list[dict[str, Any]]
    warnings: list[str]
    limits: dict[str, int]
    deadline_modified: bool


class InvalidEvidence(ValueError):
    pass


class ToolUnavailable(ValueError):
    pass


def require(condition):
    if not condition:
        raise InvalidEvidence("Invalid reference evidence")


def unwrap(payload, depth=0):
    """Decode only bounded JSON/Toolbox envelopes, never provider prose."""
    require(depth <= 4)
    try:
        require(len((payload if isinstance(payload, str) else json.dumps(
            payload, allow_nan=False)).encode()) <= MAX_PAYLOAD_BYTES)
    except (ValueError, TypeError, UnicodeError):
        raise InvalidEvidence("Invalid reference payload") from None
    if isinstance(payload, str):
        try:
            return unwrap(json.loads(payload), depth + 1)
        except ToolUnavailable:
            raise
        except (ValueError, TypeError):
            raise InvalidEvidence("Invalid reference JSON") from None
    if isinstance(payload, dict):
        if payload.get("error") or payload.get("isError"):
            raise ToolUnavailable("MCP tool unavailable")
        for key in ("result", "rows"):
            if key in payload:
                return unwrap(payload[key], depth + 1)
        if "content" in payload:
            blocks = payload["content"]
            require(isinstance(blocks, list) and 1 <= len(blocks) <= 2)
            require(all(isinstance(block, dict) and block.get("type") == "text" for block in blocks))
            values = [unwrap(block.get("text"), depth + 1) for block in blocks]
            if len(values) == 1:
                return values[0]
            require(all(isinstance(value, list) for value in values))
            return [row for value in values for row in value]
    require(isinstance(payload, (dict, list)))
    return payload


@lru_cache(maxsize=1)
def pinned_foodkeeper():
    service = FoodKeeperService()
    require(service.search("bread")["source"]["sha256"] == FOODKEEPER_SHA256)
    return service


def normalize_foodkeeper(payload, query):
    body = unwrap(payload)
    require(isinstance(body, dict))
    # Require an actual successful MCP response to agree with the pinned local
    # reference. The local reference is a validator, never a retrieval fallback.
    expected = pinned_foodkeeper().search(query, limit=5)
    require({key: body.get(key) for key in expected} == expected)
    data = {key: body[key] for key in expected if key != "source"}
    return ("ready" if body["status"] == "options" else "no_match"), data


def nullable_number(row, key, minimum, maximum=None):
    require(key in row)
    value = row[key]
    if value is None:
        return None
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and value >= minimum and (maximum is None or value <= maximum))
    return value


def normalize_tract(payload, tract_id):
    rows = unwrap(payload)
    if isinstance(rows, dict) and "tract_id" in rows:
        rows = [rows]
    require(isinstance(rows, list) and len(rows) <= 2)
    if not rows:
        return "no_match", None
    if len(rows) == 2:
        return "unknown", None
    row = rows[0]
    require(isinstance(row, dict) and row.get("tract_id") == tract_id)
    result = {"tract_id": tract_id}
    for key, low, high in (
        ("tract_lat", -90, 90), ("tract_lon", -180, 180),
        ("total_pop", 0, None), ("median_income", 0, None),
        ("poverty_rate", 0, 1), ("no_vehicle_rate", 0, 1), ("assistance_rate", 0, 1),
    ):
        result[key] = nullable_number(row, key, low, high)
    need = nullable_number(row, "need_score", 0, 1)
    poverty, vehicle = result["poverty_rate"], result["no_vehicle_rate"]
    expected_need = (poverty + vehicle) / 2 if poverty is not None and vehicle is not None else None
    require((need is None and expected_need is None) or
            (need is not None and expected_need is not None and abs(need - expected_need) <= 1e-9))
    result.update(
        need_score=need, rate_units="fraction_0_to_1", geography_precision="tract_centroid",
        observation_year=None, service_catchment_verified=False,
        unknown_fields=sorted(key for key, value in result.items() if value is None),
    )
    return "ready", result


def source_for(tool):
    if tool == "lookup_shelf_life":
        return {
            "kind": "foodkeeper_reference", "transport": "mcp", "tool": tool,
            "path": "data/foodkeeper.json", "sha256": FOODKEEPER_SHA256,
            "product_count": 661, "source_observed_at": None,
            "scope": "Pinned consumer storage/quality reference; applicability unconfirmed.",
        }
    return {
        "kind": "acs_derived_starter_snapshot", "transport": "mcp", "tool": tool,
        "table": "a4i_food.tract_demographics", "source_observed_at": None,
        "observation_year": None, "rate_units": "fraction_0_to_1",
        "scope": "Aggregate tract indicators; approximate recipient association, not a service catchment.",
    }


def entry(tool, args, status, *, data=None, retrieved_at=None, code=None) -> EvidenceEntry:
    return {
        "status": status, "request": dict(args), "source": source_for(tool),
        "retrieved_at": retrieved_at, "data": data, "code": code,
        "requires_confirmation": True, "operational_data_verified": False,
    }


def select_inputs(values, *, tracts=False):
    """Preserve first-seen order, cap distinct inputs, never echo invalid inputs."""
    if not isinstance(values, (list, tuple)):
        return [], True, False
    selected, seen = [], set()
    invalid, capped = False, len(values) > MAX_INPUTS
    for value in values[:MAX_INPUTS]:
        if not isinstance(value, str):
            invalid = True
            continue
        value = value.strip()
        valid = bool(re.fullmatch(r"53[0-9]{9}", value)) if tracts else (
            2 <= len(value) <= 120 and bool(re.search(r"\w", value))
            and not re.search(r"[\x00-\x1f\x7f]", value))
        if not valid:
            invalid = True
            continue
        identity = value if tracts else unicodedata.normalize("NFKC", value).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        if len(selected) == MAX_LOOKUPS_PER_KIND:
            capped = True
            continue
        selected.append(value)
    return selected, invalid, capped


def create_toolset():
    from toolbox_adk import CredentialStrategy, ToolboxToolset
    from toolbox_core.protocol import Protocol
    url = os.getenv("TOOLBOX_URL", "http://127.0.0.1:5000")
    credentials = None
    if url.startswith("https://"):
        credentials = CredentialStrategy.workload_identity(target_audience=url)
    elif url not in ("http://127.0.0.1:5000", "http://localhost:5000"):
        raise ValueError("Unencrypted Toolbox must be loopback-only")
    return ToolboxToolset(server_url=url, tool_names=list(TOOLS), credentials=credentials,
                          protocol=Protocol.MCP_v20250326)


class ContextEvidenceService:
    def __init__(self, toolset_factory=create_toolset, *, timeout_seconds=20,
                 lookup_timeout_seconds=10, cleanup_seconds=2):
        self.toolset_factory = toolset_factory
        self.timeout_seconds, self.lookup_timeout_seconds = timeout_seconds, lookup_timeout_seconds
        self.cleanup_seconds = cleanup_seconds

    async def collect(self, *, food_terms=(), candidate_tract_ids=()) -> ContextEvidence:
        terms, bad_terms, capped_terms = select_inputs(food_terms)
        tracts, bad_tracts, capped_tracts = select_inputs(candidate_tract_ids, tracts=True)
        warnings = [code for flag, code in (
            (bad_terms, "invalid_food_terms"), (bad_tracts, "invalid_candidate_tract_ids"),
            (capped_terms, "food_terms_capped"), (capped_tracts, "tract_ids_capped"),
        ) if flag]
        requests = [("lookup_shelf_life", {"query": term}) for term in terms] + [
            ("get_tract_equity", {"tract_id": tract}) for tract in tracts]
        trace = []
        results = [None] * len(requests)
        tasks, toolset = [], None
        fallback_code = "context_discovery_unavailable"

        async def lookup(index, name, args, tools):
            candidates = [tool for tool in tools if tool.name == name]
            if len(candidates) != 1:
                results[index] = entry(name, args, "unavailable", code="context_tool_unavailable")
                trace.append({"stage": "context_mcp_result", "tool": name, "status": "unavailable",
                              "request_index": index})
                return
            trace.append({"stage": "context_mcp_call", "tool": name, "request_index": index,
                          "request_sha256": hashlib.sha256(json.dumps(
                              args, sort_keys=True).encode()).hexdigest()})
            retrieved_at = None
            try:
                raw = await bounded(candidates[0].run_async(args=dict(args), tool_context=None),
                                    self.lookup_timeout_seconds)
                retrieved_at = datetime.now(timezone.utc).isoformat()
                status, data = normalize_foodkeeper(raw, args["query"]) if name == "lookup_shelf_life" else (
                    normalize_tract(raw, args["tract_id"]))
                results[index] = entry(name, args, status, data=data, retrieved_at=retrieved_at,
                                       code="ambiguous_tract_id" if status == "unknown" else None)
            except InvalidEvidence:
                results[index] = entry(name, args, "unknown", retrieved_at=retrieved_at,
                                       code="invalid_context_evidence")
            except TimeoutError:
                results[index] = entry(name, args, "unavailable", code="context_lookup_timeout")
            except Exception:
                results[index] = entry(name, args, "unavailable", code="context_lookup_unavailable")
            trace.append({"stage": "context_mcp_result", "tool": name,
                          "status": results[index]["status"], "request_index": index})

        try:
            if requests:
                async with asyncio.timeout(self.timeout_seconds):
                    toolset = self.toolset_factory()
                    tools = await bounded(toolset.get_tools(), min(5, self.timeout_seconds))
                    trace.append({"stage": "context_mcp_discovery",
                                  "tools": sorted({tool.name for tool in tools} & set(TOOLS))})
                    fallback_code = "context_operation_timeout"
                    tasks = [asyncio.create_task(lookup(index, name, args, tools))
                             for index, (name, args) in enumerate(requests)]
                    await asyncio.gather(*tasks)
        except TimeoutError:
            fallback_code = "context_operation_timeout"
        except Exception:
            fallback_code = "context_discovery_unavailable"
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Shutdown is bounded separately; cancellation remains a control signal.
            if tasks:
                try:
                    await bounded(asyncio.gather(*tasks, return_exceptions=True), self.cleanup_seconds)
                except Exception:
                    warnings.append("context_task_cleanup_incomplete")
            if toolset is not None:
                try:
                    await bounded(toolset.close(), self.cleanup_seconds)
                except Exception:
                    warnings.append("context_cleanup_incomplete")
        for index, (name, args) in enumerate(requests):
            if results[index] is None:
                results[index] = entry(name, args, "unavailable", code=fallback_code)
                trace.append({"stage": "context_mcp_result", "tool": name,
                              "status": "unavailable", "request_index": index})
        usable = sum(result["status"] in ("ready", "no_match") for result in results)
        status = ("ready" if usable == len(results) and not warnings else "partial") if usable else (
            "unknown" if any(result["status"] == "unknown" for result in results) else
            "unavailable" if results else "unknown" if warnings else "not_requested")
        return {
            "status": status, "foodkeeper": results[:len(terms)], "tract_equity": results[len(terms):],
            "execution_trace": trace, "warnings": warnings,
            "limits": {"distinct_products": MAX_LOOKUPS_PER_KIND, "distinct_tracts": MAX_LOOKUPS_PER_KIND},
            "deadline_modified": False,
        }
