"""
Seattle Food Equity & Surplus Broker - FastAPI Server & Interactive UI
Exposes REST API endpoints for team integration and serves an interactive Map Dashboard.
"""

import asyncio
import os
import logging
import json
import re
import sys
import time
from uuid import uuid4
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("food_broker_api")
request_logger = logging.getLogger("food_broker_request")
request_logger.setLevel(logging.INFO)
request_logger.propagate = False
if not request_logger.handlers:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    request_logger.addHandler(stream_handler)


def release_info():
    """Only explicitly named, bounded deployment identifiers are public."""
    def identifier(name, pattern):
        value = os.getenv(name, "")
        return value if re.fullmatch(pattern, value) else None
    return {
        "commit_sha": identifier("APP_COMMIT_SHA", r"[0-9a-fA-F]{7,64}"),
        "revision": identifier("K_REVISION", r"[a-z][a-z0-9-]{0,62}"),
        "service": identifier("K_SERVICE", r"[a-z][a-z0-9-]{0,62}"),
    }

app = FastAPI(
    title="Seattle Food Equity & Surplus Broker API",
    description="Intelligent Surplus Brokering using BigQuery Vector Search and Gemini",
    version="1.0.0",
)


class RequestTelemetryMiddleware:
    """Observe HTTP traffic without replacing its streaming response lifecycle."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied_id = Headers(scope=scope).get("x-request-id", "")
        request_id = supplied_id.lower() if re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", supplied_id
        ) else str(uuid4())
        started = time.perf_counter()
        response_started = False
        disconnected = False
        status = None
        headers_duration_ms = None
        failed = False

        async def observed_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message: Message) -> None:
            nonlocal response_started, disconnected, status, headers_duration_ms
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers_duration_ms = round((time.perf_counter() - started) * 1000, 3)
                message = {**message, "headers": list(message.get("headers", []))}
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            try:
                await send(message)
            except OSError:
                disconnected = True
                raise
        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
            if response_started or disconnected:
                # A started response cannot become a second JSON response.
                # Preserve transport errors so the response can close its work.
                raise
            # Never serialize exception text, request content or credentials.
            response = JSONResponse(status_code=500, content={
                "status": "error", "message": "The request could not complete. Retry and share the request ID with the team.",
                "request_id": request_id,
            })
            await response(scope, observed_receive, observed_send)
        finally:
            route = scope.get("route")
            metadata = release_info()
            method = scope.get("method")
            request_logger.info(json.dumps({
                "severity": "ERROR" if (failed and not disconnected) or (status is not None and status >= 500) else "INFO",
                "event": "http_request",
                "method": method if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER",
                # A known route template cannot contain request path/query data.
                "path": getattr(route, "path", "<unmatched>"),
                "status": status,
                "duration_ms": headers_duration_ms if headers_duration_ms is not None else round((time.perf_counter() - started) * 1000, 3),
                "request_id": request_id,
                "revision": metadata["revision"],
                "commit_sha": metadata["commit_sha"],
                "service": metadata["service"],
            }, separators=(",", ":")))


app.add_middleware(RequestTelemetryMiddleware)


try:
    from .live_intake import router as live_router
except ImportError:
    try:
        from agent.live_intake import router as live_router
    except ImportError:
        from live_intake import router as live_router
app.include_router(live_router)

try:
    from .recipient_evidence import router as evidence_router
except ImportError:
    try:
        from agent.recipient_evidence import router as evidence_router
    except ImportError:
        from recipient_evidence import router as evidence_router
app.include_router(evidence_router)

# The browser UI is same-origin. Cross-origin clients must be explicitly configured.
allowed_origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=allowed_origins, allow_credentials=False,
        allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Authorization"],
    )


@lru_cache(maxsize=1)
def get_broker_agent():
    # Serving the UI/health check must not start credential discovery or a model call.
    try:
        from .broker import FoodBrokerAgent
    except ImportError:
        from broker import FoodBrokerAgent
    return FoodBrokerAgent()


@lru_cache(maxsize=1)
def get_city_service():
    try:
        from .city import CityDataService
    except ImportError:
        from city import CityDataService
    return CityDataService()


@lru_cache(maxsize=1)
def get_foodkeeper_service():
    try:
        from .foodkeeper import FoodKeeperService
    except ImportError:
        from foodkeeper import FoodKeeperService
    return FoodKeeperService()


class MatchRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, str_strip_whitespace=True, extra="forbid")
    posting_text: str = Field(min_length=5, max_length=4000)
    required_storage: Literal["cold", "ambient", "frozen"] = "cold"
    pallet_count: float = Field(gt=0, le=10000)
    hours_remaining: float = Field(gt=0, le=168)
    donor_name: str = Field(min_length=1, max_length=200)
    donor_lat: float = Field(ge=-90, le=90)
    donor_lon: float = Field(ge=-180, le=180)
    deadline_at: Optional[datetime] = None

    @field_validator("required_storage", mode="before")
    @classmethod
    def normalize_storage(cls, value):
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        return {"refrigerated": "cold", "refrigeration": "cold", "dry": "ambient"}.get(normalized, normalized)

    @field_validator("deadline_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline_at must include its timezone")
        return value


SAMPLE_SCENARIOS = [
    {
        "id": "spinach",
        "title": "🛒 Scenario 1: 10 Crates Fresh Spinach",
        "posting_text": "10 crates of spinach, harvested yesterday, needs cold storage",
        "required_storage": "cold",
        "pallet_count": 1.0,
        "hours_remaining": 6.0,
        "donor_name": "Pike Place Grocer Dock",
        "donor_lat": 47.6097,
        "donor_lon": -122.3422,
        "notes": "Classic challenge scenario. Traps zero-refrigeration pantries that claim to want produce."
    },
    {
        "id": "dairy",
        "title": "🥛 Scenario 2: 3 Pallets Organic Whole Milk & Yogurt",
        "posting_text": "3 pallets of organic whole milk and Greek yogurt, sell-by in 48h, strictly refrigerated cold chain",
        "required_storage": "cold",
        "pallet_count": 3.0,
        "hours_remaining": 4.0,
        "donor_name": "South Seattle Dairy Distribution",
        "donor_lat": 47.5301,
        "donor_lon": -122.3210,
        "notes": "Tests tight 4-hour clock and bulk 3-pallet capacity limits."
    },
    {
        "id": "bakery",
        "title": "🥖 Scenario 3: Audited Bakery Posting",
        "posting_text": "roughly 200 loaves of day-old bread and pastries from three store locations",
        "required_storage": "ambient",
        "pallet_count": 1.0,
        "hours_remaining": 24.0,
        "donor_name": "Capitol Hill Bakery Hub",
        "donor_lat": 47.6180,
        "donor_lon": -122.3160,
        "notes": "Original starter posting 3 · hypothetical 1-pallet capacity test, not a loaves-to-pallet conversion · ambient storage · 24-hour donor window. Results depend on the current clock and profile constraints."
    }
]


@app.get("/api/scenarios")
def get_scenarios():
    return SAMPLE_SCENARIOS


@app.get("/api/city")
def city_data():
    """Read aggregate census and recipient capability data, never embeddings or credentials."""
    try:
        return JSONResponse(content=get_city_service().get_city_data())
    except Exception as exc:
        logger.error("City data request failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={
            "city": "Seattle", "status": "unavailable", "tracts": [], "recipients": [],
            "message": "City data could not be loaded from BigQuery. No heatmap values have been invented.",
        })


@app.get("/api/foodkeeper")
def foodkeeper_search(query: str = Query(..., min_length=2, max_length=120)):
    """Read-only consumer guidance lookup; never changes the donor pickup deadline."""
    query = query.strip()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="Enter at least two characters for a product lookup.")
    try:
        return JSONResponse(content=get_foodkeeper_service().search(query, limit=5))
    except ValueError:
        raise HTTPException(status_code=422, detail="Use a specific product query of 2–120 characters.")
    except Exception as exc:
        logger.error("FoodKeeper lookup failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={
            "status": "unavailable", "products": [], "selection_required": True,
            "message": "The pinned USDA FoodKeeper guidance could not be read. No shelf life has been assumed.",
        })


@app.get("/api/health")
@app.get("/healthz")
def health():
    return {"status": "ok", "cloud_dependencies": "not_checked"}


@app.get("/api/version")
def version():
    """Build identity only; not a cloud-dependency readiness check."""
    return JSONResponse(content=release_info(), headers={"Cache-Control": "no-store"})


def broker_arguments(req: MatchRequest):
    """Validate the fixed donor clock before starting either response transport."""
    hours_remaining = req.hours_remaining
    if req.deadline_at is not None:
        hours_remaining = min(hours_remaining, (req.deadline_at - datetime.now(timezone.utc)).total_seconds() / 3600)
        if hours_remaining <= 0:
            raise HTTPException(status_code=422, detail="The pickup deadline has passed. Update the offer before matching.")
    return {**req.model_dump(exclude={"hours_remaining"}), "hours_remaining": hours_remaining}


@app.post("/api/match")
async def match_surplus(req: MatchRequest):
    """Run the ADK/MCP broker; a returned draft is never a secured donation."""
    arguments = broker_arguments(req)
    try:
        result = await get_broker_agent().broker_posting_async(**arguments)
    except Exception as exc:
        # Cloud errors can include sensitive identifiers; return a stable public error.
        logger.error("Broker request failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=502, content={
            "status": "blocked", "message": "The live broker could not complete this request. Check the runtime logs and retry.",
            "generation_mode": "unavailable", "requires_confirmation": True,
        })
    return JSONResponse(content=result)


@app.post("/api/match/stream")
async def match_surplus_stream(req: MatchRequest):
    """Stream real, sanitized stages; disconnect cancels this request's worker."""
    try:
        from .match_stream import MatchStreamingResponse, stream_match
    except ImportError:
        try:
            from agent.match_stream import MatchStreamingResponse, stream_match
        except ImportError:
            from match_stream import MatchStreamingResponse, stream_match
    arguments = broker_arguments(req)

    async def run(report):
        return await get_broker_agent().broker_posting_async(**arguments, progress_callback=report)

    return MatchStreamingResponse(
        stream_match(run), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/ui/offer-workflow.js", include_in_schema=False)
def offer_workflow_script():
    return FileResponse(Path(__file__).parent / "ui/offer-workflow.js", media_type="text/javascript")


@app.get("/ui/recipient-explorer.js", include_in_schema=False)
def recipient_explorer_script():
    return FileResponse(Path(__file__).parent / "ui/recipient-explorer.js", media_type="text/javascript")


@app.get("/ui/recipient-explorer.css", include_in_schema=False)
def recipient_explorer_style():
    return FileResponse(Path(__file__).parent / "ui/recipient-explorer.css", media_type="text/css")


@app.get("/ui/source-state.js", include_in_schema=False)
def source_state_script():
    return FileResponse(Path(__file__).parent / "ui/source-state.js", media_type="text/javascript")


@app.get("/ui/view-context.js", include_in_schema=False)
def view_context_script():
    return FileResponse(Path(__file__).parent / "ui/view-context.js", media_type="text/javascript")



@app.get("/ui/workspace.css", include_in_schema=False)
def workspace_style():
    return FileResponse(Path(__file__).parent / "ui/workspace.css", media_type="text/css")


@app.get("/ui/workspace.js", include_in_schema=False)
def workspace_script():
    return FileResponse(Path(__file__).parent / "ui/workspace.js", media_type="text/javascript")


@app.get("/", response_class=HTMLResponse)
def index_dashboard():
    """Serves the full-featured interactive Map Dashboard for coordinators & team demos."""
    html_content = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Food Equity · Seattle workspace</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <link rel="stylesheet" href="/ui/workspace.css">
  <link rel="stylesheet" href="/ui/recipient-explorer.css">
</head>
<body id="rescue-app">
  <a href="#offer-panel" class="skip-link">Skip to surplus offer</a>
  <button id="workspace-nav-scrim" class="workspace-scrim" aria-label="Close navigation" hidden></button>
  <aside id="workspace-navigation" class="workspace-sidebar" aria-label="Coordinator workspace">
    <button id="workspace-nav-close" type="button" class="action-secondary workspace-nav-close" aria-label="Close workspace navigation">Close navigation</button>
    <div class="workspace-name"><span class="workspace-symbol" aria-hidden="true"><i class="fa-solid fa-seedling"></i></span><span>Food Equity<small>Surplus to community</small></span></div>
    <div class="workspace-switcher"><span class="workspace-switcher-icon" aria-hidden="true"><i class="fa-solid fa-location-dot"></i></span><span><strong>Seattle</strong><small>Coordinator workspace</small></span></div>
    <p class="nav-label">Workspace</p>
    <nav class="workspace-nav" aria-label="Workspace sections">
      <button type="button" data-workspace-view="match" aria-current="page"><i class="fa-solid fa-layer-group" aria-hidden="true"></i> Offer &amp; match</button>
      <button type="button" data-workspace-view="recipients"><i class="fa-solid fa-map-location-dot" aria-hidden="true"></i> Recipients</button>
      <button type="button" data-workspace-view="evidence"><i class="fa-solid fa-book-open" aria-hidden="true"></i> Evidence</button>
    </nav>
    <p class="nav-label">Support</p>
    <nav class="workspace-nav" aria-label="Workspace support">
      <button type="button" data-open-assistant aria-controls="assistant-drawer" aria-expanded="false"><i class="fa-regular fa-comments" aria-hidden="true"></i> Workspace assistant</button>
      <a href="#foodkeeper-panel" data-workspace-target="foodkeeper-panel"><i class="fa-solid fa-leaf" aria-hidden="true"></i> Shelf-life reference</a>
    </nav>
    <div class="workspace-note"><span class="workspace-note-icon" aria-hidden="true"><i class="fa-solid fa-shield-halved"></i></span><strong>Coordinator review</strong>Check acceptance, address and pickup with the recipient before arranging a handoff.</div>
    <div class="workspace-user"><span aria-hidden="true">FE</span><div>Food Equity<small>Seattle · retail &amp; grocer rescue</small></div></div>
  </aside>
  <div class="page-shell">
    <header class="workspace-topbar">
      <div class="workspace-breadcrumb"><button id="workspace-nav-toggle" type="button" class="workspace-menu-button" aria-label="Toggle workspace navigation" aria-controls="workspace-navigation" aria-expanded="false"><i class="fa-solid fa-bars" aria-hidden="true"></i></button><span>Workspace</span><span aria-hidden="true">/</span><strong>Seattle</strong></div>
      <div class="workspace-topbar-actions"><span class="status-pill"><span class="status-dot" aria-hidden="true"></span>Proposals require confirmation</span><button type="button" class="action-secondary" data-open-assistant aria-controls="assistant-drawer" aria-expanded="false"><i class="fa-regular fa-comments" aria-hidden="true"></i><span>Assistant</span></button></div>
    </header>
    <nav class="workspace-tabs" aria-label="Workspace views">
      <button type="button" data-workspace-view="match" aria-current="page">Offer &amp; match</button>
      <button type="button" data-workspace-view="recipients">Recipients</button>
      <button type="button" data-workspace-view="evidence">Evidence</button>
    </nav>
    <header class="app-header">
      <div><p class="eyebrow">Food rescue coordination</p><h1 id="workspace-view-title">Match workspace</h1><p id="workspace-view-description" class="header-description">Give surplus food a clear next step. Review the offer, check recipient fit, and prepare a handoff.</p></div>
      <div class="header-status"><span class="status-pill"><span class="w-2 h-2 rounded-full dot-neutral" aria-hidden="true"></span><span>Check: <strong id="runtime-status">Not run yet</strong></span></span></div>
    </header>
    <dl class="workspace-summary" aria-label="Current offer summary">
      <div class="workspace-metric"><dt>Offer quantity</dt><dd id="workspace-quantity">1 pallet</dd><small>As entered by coordinator</small></div>
      <div class="workspace-metric"><dt>Storage needed</dt><dd id="workspace-storage">Refrigerated</dd><small>Recipient capability to check</small></div>
      <div class="workspace-metric"><dt>Pickup timing</dt><dd id="workspace-clock">6h pickup window</dd><small>Fixed deadline shown after check starts</small></div>
      <div class="workspace-metric"><dt>Current check</dt><dd id="workspace-check">Not run yet</dd><small>A proposal still needs confirmation</small></div>
    </dl>
    <main id="workspace-content" tabindex="-1">
      <section id="match-workspace" class="workspace-view" aria-label="Offer and match">
          <nav class="workflow" aria-label="Rescue workflow">
    <button id="step-offer" class="workflow-step" aria-current="step" onclick="focusSection('offer-panel')"><span class="step-number">1</span><span><strong>Enter offer</strong><small>Food, storage and pickup window</small></span></button>
    <button id="step-review" class="workflow-step" onclick="focusSection('review-panel')"><span class="step-number">2</span><span><strong>Review match</strong><small>Reasons, clock and alternatives</small></span></button>
    <button id="step-handoff" class="workflow-step" disabled onclick="focusSection('handoff-panel')"><span class="step-number">3</span><span><strong>Prepare handoff</strong><small>Review and copy an outreach draft</small></span></button>
  </nav>
        <div class="workspace-grid">
          <div class="offer-column">
            <div id="offer-panel" tabindex="-1" class="surface-card rounded-xl p-5 border border-line flex flex-col gap-4">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-muted flex items-center gap-2">
          <i class="fa-solid fa-pen-to-square text-accent"></i> Surplus offer
        </h2>

        <div>
          <label for="input-donor" class="block text-xs text-muted mb-1">Donor / pickup origin</label>
          <input id="input-donor" oninput="offerChanged()" type="text" value="Pike Place Grocer Dock"
                 class="w-full surface-field border border-line rounded-lg px-3 py-2 text-sm text-ink focus:outline-none" />
        </div>

        <div>
          <label for="input-text" class="block text-xs text-muted mb-1">What is available?</label>
          <textarea id="input-text" oninput="offerChanged()" rows="3"
                    class="w-full surface-field border border-line rounded-lg px-3 py-2 text-sm text-ink focus:outline-none">10 crates of spinach, harvested yesterday, needs cold storage</textarea>
        </div>

        <div id="offer-fields" class="grid grid-cols-3 gap-3">
          <div>
            <label for="input-storage" class="block text-xs text-muted mb-1">Storage</label>
            <select id="input-storage" oninput="offerChanged()" class="w-full surface-field border border-line rounded-lg px-2.5 py-2 text-sm text-ink focus:outline-none">
              <option value="cold" selected>Cold</option>
              <option value="ambient">Ambient</option>
              <option value="frozen">Frozen</option>
            </select>
          </div>
          <div>
            <label for="input-pallets" class="block text-xs text-muted mb-1">Pallets</label>
            <input id="input-pallets" oninput="offerChanged()" type="number" min="0.01" max="10000" step="0.5" value="1.0"
                   class="w-full surface-field border border-line rounded-lg px-2.5 py-2 text-sm text-ink focus:outline-none" />
          </div>
          <div>
            <label for="input-hours" class="block text-xs text-muted mb-1">Window (hours)</label>
            <input id="input-hours" oninput="offerChanged('hours')" type="number" min="0.01" max="168" step="0.5" value="6.0"
                   class="w-full surface-field border border-line rounded-lg px-2.5 py-2 text-sm text-ink focus:outline-none" />
          </div>
        </div>

        <p id="origin-note" class="text-xs text-muted">Origin: Pike Place demo coordinates. Pallet quantity is a scenario assumption, not a crates-to-pallet conversion.</p>
        <p id="voice-clock-note" class="text-xs text-secondary hidden"></p>
        <div id="match-status" role="status" aria-live="polite" class="text-sm text-secondary"></div>
        <button id="btn-match" onclick="runMatching()"
                class="button-primary mt-2 w-full">
          <i class="fa-solid fa-magnifying-glass-location"></i>
          <span>Find a viable match</span>
        </button>
      </div>
            <details id="scenario-panel" class="surface-card rounded-xl p-5 border border-line">
        <summary class="text-sm font-semibold text-accent">
          <i class="fa-solid fa-bolt text-warning"></i> Load an example offer · optional
        </summary>
        <p class="text-xs text-muted mb-3">Choose an offer to fill the form. Review its assumptions before running a live match.</p>
        <div class="flex flex-col gap-2" id="scenario-buttons">
          <!-- Loaded by JS -->
        </div>
      </details>
            <div class="assistant-invitation"><span class="assistant-invitation-icon" aria-hidden="true"><i class="fa-regular fa-comments"></i></span><div><strong>Talk it through</strong><p>Draft an offer or ask about the current evidence.</p><button type="button" data-open-assistant class="text-action" aria-controls="assistant-drawer" aria-expanded="false">Open assistant <span aria-hidden="true">→</span></button></div></div>
          </div>
          <div class="results-column">
            <section id="review-panel" tabindex="-1" class="result-state" data-state="idle" aria-live="polite" aria-busy="false">
        <p class="text-xs text-muted mb-1">Current check</p>
        <h2 id="result-title" class="text-xl font-semibold text-ink">Ready when your offer is</h2>
        <p id="result-description" class="text-sm text-secondary mt-2">Review the offer, then check recipients for storage, capacity and pickup fit.</p>
        <p id="result-counts" class="status-copy"></p>
        <div id="review-clock" class="deadline-strip hidden">
          <span>Fixed donor pickup deadline · Seattle time</span>
          <time id="review-deadline"></time>
          <span id="review-time-left"></span>
          <small>Only a donor-confirmed pickup-window change replaces this clock.</small>
        </div>
        <div id="match-progress" class="run-progress hidden">
          <h3>Live check activity</h3>
          <p class="text-xs text-muted">Only stages reported by the broker appear here. The final result determines whether a proposal is viable.</p>
          <ol id="progress-history" class="progress-history" aria-label="Reported broker stages"></ol>
          <p id="progress-outcome" class="text-xs text-secondary"></p>
        </div>
        <button id="btn-cancel-match" class="action-secondary hidden mt-3" type="button" onclick="cancelMatching()">Cancel check</button>
        <div id="result-actions" class="result-actions hidden">
          <button id="action-review-offer" class="action-secondary" onclick="focusSection('offer-panel')">Review offer</button>
          <button id="action-retry" class="action-secondary hidden" onclick="runMatching()">Retry live check</button>
          <button id="action-handoff" class="action-secondary hidden" onclick="focusSection('handoff-panel')">Review outreach drafts</button>
        </div>
      </section>
            <div id="brief-container" class="surface-card rounded-xl p-6 border border-line flex flex-col gap-4 hidden">
        <div class="flex flex-wrap justify-between items-start border-b border-line pb-4">
          <div>
            <span class="text-xs font-bold text-positive uppercase tracking-wider">Proposed recipient</span>
            <h2 id="brief-winner-name" class="text-2xl font-bold text-ink">Winner Name</h2>
            <p id="brief-winner-address" class="text-xs text-muted">Address</p>
            <button id="inspect-winner" class="action-secondary" type="button" onclick="RecipientExplorer.select(currentPayload?.winner?.recipient_id, this)">Inspect profile &amp; offer fit</button>
          </div>
          <div class="brief-badges">
            <span id="brief-badge-storage" class="text-xs font-semibold px-3 py-1 rounded surface-inset text-accent border border-control">Storage</span>
            <span id="brief-badge-capacity" class="text-xs font-semibold px-3 py-1 rounded surface-inset text-warning border border-control">Capacity</span>
            <span id="brief-badge-equity" class="text-xs font-semibold px-3 py-1 rounded surface-selected text-accent border border-line">Equity High</span>
          </div>
        </div>

        <div>
          <h3 class="text-xs font-semibold text-accent uppercase mb-2">Available backup options</h3>
          <div id="alternatives-list" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs text-secondary"></div>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <!-- Justification -->
          <div class="surface-inset p-4 rounded-lg border border-line">
            <h3 class="text-xs font-semibold text-positive uppercase mb-1">Why this recipient</h3>
            <p id="brief-justification" class="text-xs text-secondary leading-relaxed"></p>
          </div>
          <!-- Verification & Clock -->
          <div class="surface-inset p-4 rounded-lg border border-line">
            <h3 class="text-xs font-semibold text-accent uppercase mb-1">Profile Capability & Pickup Clock</h3>
            <p id="brief-operational" class="text-xs text-secondary mb-2"></p>
            <p id="brief-clock" class="text-xs text-warning font-medium"></p>
          </div>
        </div>

        <div class="surface-inset p-4 rounded-lg border border-line">
          <h3 class="text-xs font-semibold text-accent uppercase mb-1">Who this helps</h3>
          <p id="brief-equity" class="text-xs text-secondary leading-relaxed"></p>
          <p class="text-xs text-muted mt-2">Neighborhood evidence is approximate: ACS tract indicators describe the area near the recipient's ZIP-centroid location, not individual need or the recipient's actual service catchment. Confirm whom the recipient serves.</p>
        </div>

        <!-- Outreach Drafts -->
        <div id="handoff-panel" tabindex="-1" class="surface-inset p-4 rounded-lg border border-line flex flex-col gap-3">
          <div class="flex flex-wrap gap-3 justify-between items-center">
            <h3 class="text-xs font-semibold text-secondary uppercase flex items-center gap-2">
              <i class="fa-solid fa-paper-plane text-positive"></i> 3. Prepare the handoff
            </h3>
            <button onclick="copyMatchBrief()" class="action-secondary">Copy match brief</button>
            <button onclick="copyOutreach()" class="action-secondary">
              <i class="fa-solid fa-copy"></i> Copy SMS
            </button>
          </div>
          <p class="text-xs text-muted">Confirm recipient acceptance, current storage/capacity and pickup ETA. Check the actual address before dispatch. These drafts are ready for your review; nothing has been sent or reserved.</p>
          <p id="handoff-feedback" role="status" aria-live="polite" class="text-xs text-positive"></p>
          <div>
            <span class="text-xs text-muted uppercase font-semibold">Coordinator SMS draft (may use multiple segments):</span>
            <div id="brief-sms" class="surface-inset p-2.5 rounded border border-line text-xs text-positive font-mono mt-1"></div>
          </div>
          <div>
            <div class="flex flex-wrap justify-between items-center gap-2"><span class="text-xs text-muted font-semibold">Email outreach draft</span><button onclick="copyEmail()" class="action-secondary">Copy email</button></div>
            <pre id="brief-email" class="surface-inset p-3 rounded border border-line text-xs text-secondary font-sans whitespace-pre-wrap mt-1"></pre>
          </div>
        </div>

        <p id="brief-provenance" class="text-xs text-warning"></p>
        <!-- Team Handoff JSON -->
        <div class="border-t border-line pt-3">
          <details class="cursor-pointer">
            <summary class="text-xs text-muted font-medium flex items-center gap-1">
              <i class="fa-solid fa-code text-accent"></i> Technical result details
            </summary>
            <div class="mt-2 relative">
              <button onclick="copyJsonPayload()" class="action-secondary mb-2">
                Copy JSON
              </button>
              <pre id="raw-json" class="surface-inset p-3 rounded text-xs text-accent font-mono overflow-x-auto max-h-60"></pre>
            </div>
          </details>
        </div>

      </div>
            <div id="match-map-slot"><div id="map-panel" class="surface-card rounded-xl p-4 border border-line flex flex-col gap-3">
        <div class="flex flex-wrap gap-3 justify-between items-center px-1">
          <div class="flex items-center gap-2 text-sm font-semibold text-ink">
            <i class="fa-solid fa-map-location-dot text-positive"></i>
            <span>Recipient network</span>
          </div>
          <div class="flex flex-wrap items-center gap-3 text-xs">
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full dot-donor inline-block"></span> Donor</span>
            <span>1 = proposed · 2–3 = backups · ? = needs details · × = rejected</span>
          </div>
        </div>

        <div class="map-toolbar">
          <button id="btn-city" onclick="loadCityData()" class="action-secondary">Load city data</button>
          <button id="fit-recipient-view" onclick="fitRecipientView()" class="action-secondary">Fit visible profiles</button>
          <details class="map-options">
            <summary>Map layers &amp; storage</summary>
            <div id="map-controls" class="map-layer-controls">


          <label class="flex items-center gap-1"><input id="show-need" type="checkbox" checked onchange="viewFiltersChanged()"> Census need heatmap</label>
          <label class="flex items-center gap-1"><input id="show-recipients" type="checkbox" checked onchange="viewFiltersChanged()"> Recipient capabilities</label>
          <label for="heat-metric">Need indicator</label>
          <select id="heat-metric" onchange="viewFiltersChanged()" class="surface-card border border-control rounded px-2 py-1">
            <option value="need_score">Combined need</option><option value="poverty_rate">Poverty rate</option><option value="no_vehicle_rate">No-vehicle household rate</option>
          </select>
          <label for="capability-filter">Storage</label>
          <select id="capability-filter" onchange="viewFiltersChanged()" class="surface-card border border-control rounded px-2 py-1">
            <option value="all">All profiles</option><option value="cold">Cold storage</option><option value="frozen">Freezer</option><option value="ambient">Ambient / dry</option>
          </select>
        </div>
          </details>
        </div>
        <p id="city-status" role="status" class="text-xs text-warning">Needs data — load the Seattle census and recipient layers.</p>
        <p id="map-tile-status" role="status" class="text-xs text-warning hidden"></p>
        <p class="text-sm text-secondary">Explore loaded profiles. Current acceptance and capacity still need confirmation.</p>
        <div class="recipient-browser">
          <div id="map" aria-label="Seattle approximate recipient and census locations"></div>
          <section class="recipient-directory" aria-label="Recipient directory">
            <label for="recipient-search">Find a recipient</label>
            <input id="recipient-search" maxlength="100" type="search" placeholder="Organization or city" oninput="viewFiltersChanged()" autocomplete="off">
            <div class="recipient-filters">
              <div>
                <label for="outcome-filter">Offer decision</label>
                <select id="outcome-filter" onchange="viewFiltersChanged()">
                  <option value="all">All decisions</option>
                  <option value="eligible">Eligible proposals</option>
                  <option value="needs_confirmation">Needs confirmation</option>
                  <option value="rejected">Rejected</option>
                  <option value="not_evaluated">Not evaluated</option>
                </select>
              </div>
              <div>
                <label for="provenance-filter">Profile source</label>
                <select id="provenance-filter" onchange="viewFiltersChanged()">
                  <option value="all">All sources</option>
                  <option value="generated">Generated operations</option>
                  <option value="planted">Invented teaching case</option>
                  <option value="unknown">Unknown source</option>
                </select>
              </div>
            </div>
            <p id="recipient-count" role="status" aria-live="polite">Loading recipient profiles…</p>
            <div id="recipient-list"></div>
          </section>
        </div>
        <section id="recipient-compare" aria-label="Compare current eligible proposals"></section>
        <details class="map-evidence"><summary>Map key, sources &amp; limitations</summary><div id="heat-legend" class="text-xs text-secondary">
          <div style="height:8px;background:linear-gradient(to right,#2563eb,#06b6d4,#facc15,#f97316,#ef4444);border-radius:4px;max-width:280px"></div>
          <span id="heat-legend-label">Low → high combined need (0–1); mean of poverty and no-vehicle rates. Smoothed tract-centroid points, not tract boundaries or individual hunger.</span>
        </div>
        <p class="text-xs text-muted">Unevaluated capability points: purple = freezer · blue = refrigeration · gray = other/unknown. An outlined marker is selected. These are dataset profiles, not live availability.</p>
        <p id="city-provenance" class="text-xs text-muted">Sources have not been loaded.</p>
        <div id="map-source-slot"><div id="source-state-summary" class="surface-inset p-3 rounded-lg" aria-label="Evidence source status"></div></div>
        <details class="text-xs text-muted"><summary class="cursor-pointer">Sources and limits of this map</summary><div id="city-caveats"></div><pre id="city-source-details" class="whitespace-pre-wrap break-words mt-2"></pre></details>
        <p id="route-status" class="text-xs text-warning">Routing unavailable — no verified road route has been returned. Any dashed line is an illustrative straight-line connection.</p>
        <a id="external-directions" class="hidden text-sm text-accent underline" target="_blank" rel="noopener noreferrer">Open directions in Google Maps (approximate endpoints; verify the address)</a>
        <p class="text-xs text-muted">Recipient points may represent ZIP-level locations. Confirm actual collection address, acceptance, storage and pickup with the recipient. Census indicators describe neighborhoods, not individual eligibility.</p>

      </details>
</div></div>
          </div>
        </div>
      </section>
      <section id="recipients-workspace" class="workspace-view" aria-label="Recipients" hidden><div id="recipient-view-slot"></div></section>
      <section id="evidence-workspace" class="workspace-view" aria-label="Evidence" hidden>
        <div class="evidence-intro"><i class="fa-solid fa-circle-info" aria-hidden="true"></i><p><strong>Know what supports the decision.</strong> Profile data, neighborhood indicators and shelf-life guidance answer different questions. Current recipient acceptance and capacity require direct confirmation.</p></div>
        <section class="surface-card evidence-sources" aria-labelledby="evidence-source-title"><h2 id="evidence-source-title">Sources behind this view</h2><p>What was loaded, how current it is, and what remains unconfirmed.</p><div id="evidence-source-slot"></div></section>
        <div class="evidence-grid"><div><div id="exclusions-panel" class="surface-card rounded-xl p-5 border border-line">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-negative mb-3 flex items-center gap-2">
          <i class="fa-solid fa-shield-halved"></i> Why candidates were ruled out
        </h2>
        <div id="decoys-list" class="flex flex-col gap-2 text-xs">
          <p class="text-muted italic">Run a match to see the specific constraints that ruled candidates out.</p>
        </div>
        <div id="pending-container" class="hidden border-t border-line pt-4 mt-4">
          <h3 class="text-sm font-semibold text-warning mb-2">Needs more information</h3>
          <div id="pending-list" class="flex flex-col gap-2 text-xs"></div>
        </div>
      </div><details id="run-evidence" class="surface-card rounded-xl p-4 border border-line hidden">
        <summary class="text-sm text-secondary cursor-pointer">How this check ran</summary>
        <pre id="execution-trace" class="whitespace-pre-wrap break-words text-xs text-muted mt-2"></pre>
      </details></div><div><details id="foodkeeper-panel" class="surface-card rounded-xl p-5 border border-line">
        <summary class="text-sm font-semibold text-accent cursor-pointer">Shelf-life evidence · USDA FoodKeeper</summary>
        <p class="text-xs text-muted mt-3">Consumer storage guidance for quality and freshness, not a commercial cold-chain or food-safety determination. Review the exact product, subtype and storage basis. This lookup never extends or changes the donor's pickup deadline.</p>
        <label for="foodkeeper-query" class="block text-xs text-secondary mt-3 mb-1">Product to look up</label>
        <input id="foodkeeper-query" type="search" maxlength="120" placeholder="For example: spinach or milk" onkeydown="if(event.key === 'Enter'){event.preventDefault();searchFoodKeeper();}"
          class="w-full surface-field border border-line rounded-lg px-3 py-2 text-sm text-ink" />
        <button id="btn-foodkeeper" type="button" onclick="searchFoodKeeper()" class="action-secondary mt-2">Look up USDA guidance</button>
        <p id="foodkeeper-status" role="status" aria-live="polite" class="text-xs text-warning mt-3">No product selected. Search and review the matching options.</p>
        <p id="foodkeeper-notice" class="text-xs text-muted mt-2"></p>
        <div id="foodkeeper-results" class="flex flex-col gap-2 mt-3"></div>
        <p id="foodkeeper-unknowns" class="text-xs text-muted mt-3"></p>
      </details></div></div>
      </section>
    </main>
    <footer class="workspace-footer"><span><span class="status-dot" aria-hidden="true"></span>Seattle coordinator workspace</span><span>Outreach drafts are never sent automatically</span></footer>
  </div>
  <button id="assistant-scrim" class="workspace-scrim" aria-label="Close assistant session" hidden></button>
  <aside id="assistant-drawer" role="dialog" aria-modal="true" aria-labelledby="assistant-title" tabindex="-1" hidden>
    <div class="assistant-header"><div><p class="eyebrow">Your current workspace</p><h2 id="assistant-title">Food Equity assistant</h2></div><button id="assistant-close" type="button" class="action-secondary" aria-label="Close assistant and stop session">Close session</button></div>
    <p class="assistant-context-note">Discussing <strong id="assistant-context-name">the current offer and loaded evidence</strong>.</p>
    <details open id="voice-panel" class="surface-card rounded-xl p-5 border border-line">
        <summary class="text-sm font-semibold text-accent cursor-pointer">Ask about this workspace</summary>
        <p class="text-xs text-muted mt-3">Gemini Live can draft an offer or explain the current result and selected profile using the loaded display. It cannot verify current availability or rerun matching. Review demo assumptions first. Microphone audio is sent only after Start microphone; typed notes work without it. Review and apply the draft yourself, then run matching separately. Nothing is dispatched.</p>
        <p id="voice-status" role="status" aria-live="polite" class="text-xs text-secondary mt-3">Checking whether live intake is available…</p>
        <div class="result-actions">
          <button id="voice-start" type="button" class="action-secondary" disabled onclick="startVoiceSession(true)">Start microphone</button>
          <button id="voice-stop" type="button" class="action-secondary" disabled onclick="stopVoiceSession()">Stop session</button>
        </div>
        <label for="voice-text" class="block text-xs text-secondary mt-3 mb-1">Or ask about the current view</label>
        <textarea id="voice-text" rows="2" maxlength="4000" disabled placeholder="Why this recipient? What needs confirmation? What are the current alternatives?" class="w-full surface-field border border-line rounded-lg px-3 py-2 text-sm text-ink"></textarea>
        <button id="voice-send" type="button" class="action-secondary mt-2" disabled onclick="sendVoiceText()">Send note</button>
        <p class="text-xs text-muted mt-3">Conversation text is model commentary. The action receipt below confirms whether a draft proposal or a display-only read actually completed.</p>
        <div id="voice-transcript" class="voice-transcript text-xs text-secondary mt-3" aria-label="Session transcript"></div>
        <div id="voice-action-receipt" class="text-xs text-secondary mt-3" role="status" aria-live="polite"></div>
        <div id="voice-review" class="hidden border-t border-line mt-3 pt-3">
          <h3 class="text-sm font-semibold text-ink">Review proposed offer fields</h3>
          <div id="voice-draft" class="voice-draft text-xs text-secondary mt-2"></div>
          <p id="voice-missing" role="status" class="text-xs text-warning mt-2"></p>
          <p class="text-xs text-muted mt-2">Check the exact deadline, pickup origin, quantity and storage before applying. Applying stops the session and updates the form; it does not request a match.</p>
          <button id="voice-apply" type="button" class="action-secondary mt-2" disabled onclick="applyVoiceDraft()">Apply reviewed draft to form</button>
        </div>
      </details>
  </aside>
  <aside id="recipient-detail" class="hidden" role="dialog" aria-labelledby="recipient-detail-title" tabindex="-1">
    <button id="recipient-close" type="button" class="action-secondary" onclick="RecipientExplorer.close()" aria-label="Close recipient profile">Close profile</button>
    <div id="recipient-detail-content"></div>
  </aside>

  <script src="/ui/offer-workflow.js"></script>
  <!-- Leaflet & App JS -->
  <script>
    let map;
    let markersLayer;
    let cityRecipientLayer;
    let tractPointLayer;
    let heatLayer;
    let cityPayload = null;
    let currentPayload = null;
    let selectedOrigin = {latitude: 47.6097, longitude: -122.3422};
    const offerClock = new OfferWorkflow.OfferClock();
    let matchInFlight = false;
    let activeMatchController = null;
    let matchStopReason = null;
    let scenarioButtons = [];
    const receivedStages = [];
    let deadlineTicker = null;
    const offerFieldIds = ['input-donor','input-text','input-storage','input-pallets','input-hours'];

    function textNode(tag, text, className = '') {
      const node = document.createElement(tag);
      node.textContent = text == null ? 'Unknown' : String(text);
      if (className) node.className = className;
      return node;
    }
    function popup(title, lines) {
      const node = document.createElement('div');
      node.appendChild(textNode('strong', title));
      for (const line of lines) node.appendChild(textNode('div', line));
      return node;
    }
    function setStatus(message, warning = false) {
      const el = document.getElementById('match-status');
      el.textContent = message;
      el.className = warning ? 'text-sm text-warning' : 'text-sm text-secondary';
    }
    function focusSection(id) {
      window.FoodWorkspace?.reveal(id);
      const target = document.getElementById(id);
      if (typeof target.scrollIntoView === 'function') target.scrollIntoView({block:'start',behavior:'auto'});
      if (typeof target.focus === 'function') target.focus({preventScroll:true});
    }
    function setWorkflow(step) {
      ['offer','review','handoff'].forEach(name => {
        const button = document.getElementById(`step-${name}`);
        if (name === step) button.setAttribute('aria-current','step');
        else button.removeAttribute('aria-current');
      });
    }
    function lockOffer(locked) {
      offerFieldIds.forEach(id => { document.getElementById(id).disabled = locked; });
      scenarioButtons.forEach(button => { button.disabled = locked; });
      document.getElementById('btn-match').disabled = locked;
      refreshVoiceControls();
    }
    function reviewState(state, title, description, counts = '') {
      const panel = document.getElementById('review-panel');
      panel.setAttribute('data-state',state);
      panel.setAttribute('aria-busy',state === 'loading' ? 'true' : 'false');
      document.getElementById('result-title').textContent = title;
      document.getElementById('result-description').textContent = description;
      document.getElementById('result-counts').textContent = counts;
      const active = !['idle','loading'].includes(state);
      document.getElementById('result-actions').classList.toggle('hidden',!active);
      document.getElementById('action-retry').classList.toggle('hidden',!['blocked','canceled'].includes(state));
      const hasMatch = ['success','draft'].includes(state);
      document.getElementById('action-handoff').classList.toggle('hidden',!hasMatch);
      document.getElementById('step-handoff').disabled = !hasMatch;
    }
    function offerChanged(field) {
      if (matchInFlight) return;
      if (field === 'hours') {
        offerClock.reset();
        document.getElementById('voice-clock-note').classList.add('hidden');
      }
      if (voice.socket || voice.starting) stopVoiceSession('Offer changed. Start a new session to use the updated form.');
      voice.draft = null;
      refreshVoiceControls();
      currentPayload = null;
      resetMatchProgress(false);
      refreshDeadlineDisplay();
      if (typeof ViewContext !== 'undefined') ViewContext.changed('offer');
      document.getElementById('brief-container').classList.add('hidden');
      document.getElementById('run-evidence').classList.add('hidden');
      document.getElementById('external-directions').classList.add('hidden');
      document.getElementById('external-directions').removeAttribute('href');
      document.getElementById('route-status').textContent = 'Routing unavailable — run the current offer to get a proposed destination.';
      document.getElementById('pending-container').classList.add('hidden');
      document.getElementById('runtime-status').textContent = 'Offer changed';
      document.getElementById('handoff-feedback').textContent = '';
      document.getElementById('decoys-list').replaceChildren(textNode('p','Run the updated offer to refresh its candidate checks.'));
      scenarioButtons.forEach(button => button.setAttribute('aria-pressed','false'));
      if (markersLayer) markersLayer.clearLayers();
      renderCityLayers();
      reviewState('idle','Ready to check this offer','Review the quantity, storage and actual pickup window, then run a live match.');
      setWorkflow('offer');
      setStatus('Ready to check the current offer.');
    }
    // Voice intake is a bounded drafting session. Only runMatching calls the broker.
    const voice = {enabled:false, config:null, socket:null, starting:false, ready:false, generation:0,
      stream:null, inputContext:null, outputContext:null, source:null, worklet:null, workletURL:null,
      playback:new Set(), nextPlayback:0, timer:null, viewTimer:null, resolveReady:null, initialDraftSeen:false,
      draft:null, draftId:null, draftVersion:-1, transcript:[], sentBytes:0};
    function voiceStatus(message) { document.getElementById('voice-status').textContent = message; }
    function flushVoiceView() {
      if (voice.viewTimer) { clearTimeout(voice.viewTimer); voice.viewTimer = null; }
      if (typeof ViewContext !== 'undefined' && voice.ready && voice.socket?.readyState === 1)
        voice.socket.send(JSON.stringify({type:'view_context',context:ViewContext.snapshot()}));
    }
    function voiceDraftErrors(draft) {
      const f = draft?.fields || {}, errors = [];
      if (typeof f.donor_name !== 'string' || !f.donor_name.trim() || f.donor_name.length > 200) errors.push('donor name');
      if (typeof f.posting_text !== 'string' || f.posting_text.trim().length < 5 || f.posting_text.length > 4000) errors.push('surplus description');
      if (!['ambient','cold','frozen'].includes(f.required_storage)) errors.push('storage');
      if (!Number.isFinite(f.pallet_count) || f.pallet_count <= 0 || f.pallet_count > 10000) errors.push('pallet quantity');
      if (!Number.isFinite(f.hours_remaining) || f.hours_remaining <= 0 || f.hours_remaining > 168) errors.push('pickup window');
      if (!validPoint({latitude:f.donor_lat,longitude:f.donor_lon})) errors.push('origin coordinates');
      if (typeof f.deadline_at !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/i.test(f.deadline_at)
          || !Number.isFinite(Date.parse(f.deadline_at)) || Date.parse(f.deadline_at) <= Date.now()
          || Date.parse(f.deadline_at) > Date.now() + 168 * 3600000) errors.push('future deadline with timezone');
      return errors;
    }
    function refreshVoiceControls() {
      const available = voice.enabled && !matchInFlight;
      document.getElementById('voice-start').disabled = !available || !!voice.socket || voice.starting;
      document.getElementById('voice-stop').disabled = !voice.socket && !voice.starting;
      document.getElementById('voice-send').disabled = !available || voice.starting
        || (typeof ViewActionReceipts !== 'undefined' && ViewActionReceipts.busy());
      document.getElementById('voice-text').disabled = !available;
      document.getElementById('voice-apply').disabled = matchInFlight || !voice.draft?.ready_for_review
        || voiceDraftErrors(voice.draft).length > 0;
    }
    async function loadVoiceConfig() {
      try {
        const response = await fetch('/api/live/config');
        if (!response.ok) throw new Error('unavailable');
        voice.config = await response.json();
        voice.enabled = voice.config.enabled === true && typeof WebSocket !== 'undefined';
        voiceStatus(voice.enabled ? `Optional live intake is available. Start the microphone or send a typed note. Sessions last up to ${Math.ceil(voiceSessionSeconds()/60)} minutes.`
          : 'Live intake is not enabled here. The offer form and matching work normally.');
      } catch (_) {
        voice.enabled = false;
        voiceStatus('Live intake is unavailable. Continue with the offer form.');
      }
      refreshVoiceControls();
    }
    function voiceSessionSeconds() {
      const seconds = voice.config?.max_session_seconds;
      return Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds,300) : 180;
    }
    function voiceFormContext() {
      // Include only visible, valid values. Missing fields stay missing, never guessed.
      const fields = {}, donor = document.getElementById('input-donor').value.trim();
      const description = document.getElementById('input-text').value.trim();
      const storage = document.getElementById('input-storage').value;
      const pallets = Number(document.getElementById('input-pallets').value);
      const hours = Number(document.getElementById('input-hours').value);
      if (donor && donor.length <= 200) fields.donor_name = donor;
      if (description.length >= 5 && description.length <= 4000) fields.posting_text = description;
      if (['ambient','cold','frozen'].includes(storage)) fields.required_storage = storage;
      if (Number.isFinite(pallets) && pallets > 0 && pallets <= 10000) fields.pallet_count = pallets;
      if (Number.isFinite(hours) && hours > 0 && hours <= 168) fields.hours_remaining = hours;
      if (validPoint(selectedOrigin)) { fields.donor_lat = selectedOrigin.latitude; fields.donor_lon = selectedOrigin.longitude; }
      try { fields.deadline_at = buildRequest().deadline_at; }
      catch (_) { if (offerClock.deadline) fields.deadline_at = offerClock.deadline; }
      return fields;
    }
    function renderVoiceDraft(message) {
      if (!message || typeof message.draft_id !== 'string' || !Number.isInteger(message.draft_version) || message.draft_version < 0) return false;
      if ((voice.draftId && voice.draftId !== message.draft_id) || message.draft_version <= voice.draftVersion) return false;
      voice.draftId = message.draft_id;
      voice.draftVersion = message.draft_version;
      voice.draft = message;
      const box = document.getElementById('voice-draft');
      box.replaceChildren();
      const fields = message.fields || {};
      const labels = {donor_name:'Donor',posting_text:'Available surplus',required_storage:'Storage',pallet_count:'Pallets',
        donor_lat:'Origin latitude',donor_lon:'Origin longitude',deadline_at:'Exact pickup deadline',hours_remaining:'Hours remaining at draft time'};
      Object.entries(labels).forEach(([key,label]) => {
        const value = fields[key];
        box.appendChild(textNode('p',`${label}: ${value == null ? 'Not supplied' : String(value).slice(0,4000)}`));
      });
      const errors = voiceDraftErrors(message);
      const missing = Array.isArray(message.missing_fields) ? message.missing_fields.slice(0,12).map(value=>String(value).slice(0,100)) : [];
      document.getElementById('voice-missing').textContent = message.ready_for_review && !errors.length
        ? `Ready for your review.${message.deadline_changed ? ' The proposed deadline changed; confirm it with the donor.' : ''}`
        : `Still needs confirmation: ${[...new Set([...missing,...errors])].join(', ') || 'the intake agent has not completed this draft'}.`;
      document.getElementById('voice-review').classList.remove('hidden');
      refreshVoiceControls();
      return true;
    }
    function appendVoiceTranscript(message) {
      if (!['user','model'].includes(message.role) || typeof message.text !== 'string') return;
      const text = message.text.slice(0,4000), last = voice.transcript[voice.transcript.length - 1];
      if (last && last.role === message.role && !last.final) { last.text = (last.text + text).slice(-6000); last.final = message.final === true; }
      else voice.transcript.push({role:message.role,text,final:message.final === true});
      voice.transcript = voice.transcript.slice(-16);
      const box = document.getElementById('voice-transcript');
      box.replaceChildren(...voice.transcript.map(item=>textNode('p',`${item.role === 'user' ? 'You' : 'Intake agent'}: ${item.text}`)));
    }
    function flushVoicePlayback() {
      voice.playback.forEach(source => { try { source.stop(); source.disconnect(); } catch (_) {} });
      voice.playback.clear();
      voice.nextPlayback = 0;
    }
    function playVoiceAudio(buffer) {
      const ctx = voice.outputContext;
      if (!ctx || ctx.state !== 'running' || !buffer.byteLength || buffer.byteLength % 2 || buffer.byteLength > 1024 * 1024) return;
      if (voice.nextPlayback - ctx.currentTime > 20) { flushVoicePlayback(); voiceStatus('Audio playback was delayed. Continue using the transcript.'); return; }
      const pcm = new DataView(buffer), audio = ctx.createBuffer(1,buffer.byteLength / 2,24000);
      const channel = audio.getChannelData(0);
      for (let i=0;i<channel.length;i++) channel[i] = pcm.getInt16(i*2,true) / 32768;
      const source = ctx.createBufferSource();
      source.buffer = audio;
      source.connect(ctx.destination);
      voice.playback.add(source);
      source.onended = () => { voice.playback.delete(source); source.disconnect(); };
      const start = Math.max(ctx.currentTime,voice.nextPlayback);
      source.start(start);
      voice.nextPlayback = start + audio.duration;
    }
    function stopVoiceSession(message = 'Session stopped. Review any draft above, or continue with the offer form.') {
      voice.generation += 1;
      voice.starting = false;
      voice.ready = false;
      if (voice.timer) { clearTimeout(voice.timer); voice.timer = null; }
      if (voice.viewTimer) { clearTimeout(voice.viewTimer); voice.viewTimer = null; }
      if (typeof ViewActionReceipts !== 'undefined') ViewActionReceipts.reset();
      if (voice.resolveReady) { voice.resolveReady(false); voice.resolveReady = null; }
      const socket = voice.socket;
      voice.socket = null;
      if (socket) {
        socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
        if (socket.readyState === 1) { try { socket.send(JSON.stringify({type:'audio_end'})); socket.send(JSON.stringify({type:'stop'})); } catch (_) {} }
        try { socket.close(); } catch (_) {}
      }
      if (voice.stream) voice.stream.getTracks().forEach(track => { track.onended = null; track.stop(); });
      voice.stream = null;
      if (voice.worklet) { voice.worklet.port.onmessage = null; voice.worklet.disconnect(); }
      if (voice.source) voice.source.disconnect();
      voice.worklet = voice.source = null;
      flushVoicePlayback();
      [voice.inputContext,voice.outputContext].forEach(ctx => { if (ctx && ctx.state !== 'closed') ctx.close().catch(()=>{}); });
      voice.inputContext = voice.outputContext = null;
      if (voice.workletURL) URL.revokeObjectURL(voice.workletURL);
      voice.workletURL = null;
      voiceStatus(message);
      refreshVoiceControls();
    }
    // Original streaming linear resampler: real device sample rate -> 16 kHz mono PCM.
    // It holds the final sample across worklet blocks and transfers 20 ms frames.
    const voiceWorkletSource = `
      class OfferPCM extends AudioWorkletProcessor {
        constructor() { super(); this.tail = new Float32Array(0); this.position = 0; this.frame = new Int16Array(320); this.count = 0; }
        process(inputs) {
          const input = inputs[0]?.[0];
          if (!input) return true;
          const samples = new Float32Array(this.tail.length + input.length);
          samples.set(this.tail); samples.set(input,this.tail.length);
          while (this.position + 1 < samples.length) {
            const left = Math.floor(this.position), fraction = this.position - left;
            const value = Math.max(-1,Math.min(1,samples[left] + (samples[left+1]-samples[left])*fraction));
            this.frame[this.count++] = Math.round(value * (value < 0 ? 32768 : 32767));
            if (this.count === 320) { this.port.postMessage(this.frame.buffer,[this.frame.buffer]); this.frame = new Int16Array(320); this.count = 0; }
            this.position += sampleRate / 16000;
          }
          const keep = Math.min(Math.floor(this.position),samples.length-1);
          this.tail = samples.slice(keep); this.position -= keep;
          return true;
        }
      }
      registerProcessor('offer-pcm',OfferPCM);
    `;
    async function startVoiceMicrophone(generation) {
      if (!navigator.mediaDevices?.getUserMedia || typeof AudioWorkletNode === 'undefined') throw new Error('Microphone capture is unavailable in this browser.');
      const stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
      if (generation !== voice.generation) { stream.getTracks().forEach(track=>track.stop()); return; }
      voice.stream = stream;
      stream.getTracks().forEach(track => { track.onended = () => {
        if (generation === voice.generation) stopVoiceSession('Microphone capture ended. Review the draft or use a typed note.');
      }; });
      const Audio = window.AudioContext || window.webkitAudioContext;
      const ctx = new Audio();
      voice.inputContext = ctx;
      await ctx.resume();
      if (generation !== voice.generation) return;
      voice.workletURL = URL.createObjectURL(new Blob([voiceWorkletSource],{type:'text/javascript'}));
      await ctx.audioWorklet.addModule(voice.workletURL);
      if (generation !== voice.generation) return;
      voice.worklet = new AudioWorkletNode(ctx,'offer-pcm');
      voice.source = ctx.createMediaStreamSource(stream);
      voice.worklet.port.onmessage = event => {
        if (generation !== voice.generation || !voice.ready || voice.socket?.readyState !== 1) return;
        if (voice.viewTimer) flushVoiceView();
        const bytes = event.data.byteLength;
        if (voice.socket.bufferedAmount > 262144 || voice.sentBytes + bytes > 10000000) {
          stopVoiceSession('Audio upload reached its limit or the network fell behind. Review the draft or start a new session.'); return;
        }
        voice.sentBytes += bytes;
        voice.socket.send(event.data);
      };
      voice.source.connect(voice.worklet);
      voice.worklet.connect(ctx.destination); // The worklet emits silence; no microphone loopback.
      voiceStatus('Microphone is on. Describe or clarify the offer; Stop ends audio capture.');
    }
    async function startVoiceSession(useMicrophone = false) {
      if (!voice.enabled || matchInFlight || voice.starting || voice.socket) return false;
      const generation = ++voice.generation;
      voice.starting = true;
      voice.draft = null;
      voice.draftId = null;
      voice.draftVersion = -1;
      voice.initialDraftSeen = false;
      voice.transcript = [];
      voice.sentBytes = 0;
      document.getElementById('voice-transcript').replaceChildren();
      document.getElementById('voice-review').classList.add('hidden');
      refreshVoiceControls();
      voiceStatus('Connecting to live intake… The offer form remains available.');
      try {
        const Audio = window.AudioContext || window.webkitAudioContext;
        if (Audio) {
          voice.outputContext = new Audio();
          await voice.outputContext.resume().catch(()=>{});
          if (generation !== voice.generation) return false;
        }
        const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/live/intake`);
        voice.socket = socket;
        socket.binaryType = 'arraybuffer';
        const connected = new Promise(resolve => { voice.resolveReady = resolve; });
        voice.timer = setTimeout(()=>stopVoiceSession('Live intake did not connect. Use the form or try again.'),20000);
        socket.onmessage = event => {
          if (generation !== voice.generation) return;
          if (event.data instanceof ArrayBuffer) {
            if (typeof ViewActionReceipts === 'undefined' || ViewActionReceipts.current()) playVoiceAudio(event.data);
            return;
          }
          if (typeof event.data !== 'string' || event.data.length > 65536) return;
          let message;
          try { message = JSON.parse(event.data); } catch (_) { return; }
          if (!message || typeof message !== 'object') return;
          if (message.type === 'ready' && !voice.ready) {
            if (typeof ViewContext !== 'undefined') socket.send(JSON.stringify({type:'view_context',context:ViewContext.snapshot()}));
            socket.send(JSON.stringify({type:'context',fields:voiceFormContext()}));
            voice.ready = true;
            voice.starting = false;
            clearTimeout(voice.timer);
            voice.timer = setTimeout(()=>stopVoiceSession('The session time limit was reached. Review the draft or start another session.'),voiceSessionSeconds()*1000);
            voice.resolveReady?.(true); voice.resolveReady = null;
            voiceStatus('Connected. Send a note or speak when the microphone is on.');
            refreshVoiceControls();
          } else if (message.type === 'view_context_ack') {
            if (message.status !== 'accepted') stopVoiceSession('The view snapshot was refused. Review the current view and restart intake.');
          } else if (message.type === 'turn_started' && typeof ViewActionReceipts !== 'undefined') {
            if (!ViewActionReceipts.begin(message)) stopVoiceSession('A response belonged to an older view. Restart intake from the current view.');
            refreshVoiceControls();
          } else if (message.type === 'action_result' && typeof ViewActionReceipts !== 'undefined') {
            ViewActionReceipts.accept(message);
            refreshVoiceControls();
          } else if (message.type === 'draft') {
            if (typeof ViewActionReceipts === 'undefined' || (!voice.initialDraftSeen && !ViewActionReceipts.current())) {
              voice.initialDraftSeen = true; renderVoiceDraft(message);
            }
          } else if (message.type === 'transcript') {
            if (typeof ViewActionReceipts === 'undefined' || ViewActionReceipts.owns(message)) appendVoiceTranscript(message);
          } else if (message.type === 'interrupted') {
            if (typeof ViewActionReceipts === 'undefined') flushVoicePlayback();
            else stopVoiceSession('The turn was interrupted. Restart intake so its actions belong to the current view.');
          } else if (message.type === 'turn_complete') {
            if (typeof ViewActionReceipts === 'undefined' || ViewActionReceipts.finish(message)) {
              voice.transcript.forEach(item=>{item.final=true;}); refreshVoiceControls();
            }
          } else if (message.type === 'status') {
            if (typeof ViewActionReceipts === 'undefined' || !ViewActionReceipts.busy() || ViewActionReceipts.owns(message))
              voiceStatus(String(message.message || 'Live intake connected.').slice(0,1000));
          }
          else if (message.type === 'error') stopVoiceSession(String(message.message || 'Live intake is unavailable. Use the offer form.').slice(0,1000));
        };
        socket.onerror = () => { if (generation === voice.generation) stopVoiceSession('Live intake could not connect. Check app access or continue with the form.'); };
        socket.onclose = () => { if (generation === voice.generation) stopVoiceSession('Live session closed. Review the draft or continue with the form.'); };
        const ready = await connected;
        if (!ready || generation !== voice.generation) return false;
        if (useMicrophone) {
          try { await startVoiceMicrophone(generation); }
          catch (_) {
            if (generation === voice.generation) stopVoiceSession('Microphone unavailable or permission denied. Send a typed note to start a text-only session, or use the form.');
            return false;
          }
        }
        return generation === voice.generation;
      } catch (_) {
        if (generation === voice.generation) stopVoiceSession('Live intake could not start. Continue with the form or try a typed note.');
        return false;
      }
    }
    async function sendVoiceText() {
      const input = document.getElementById('voice-text'), text = input.value.trim();
      if (!voice.enabled || matchInFlight || voice.starting || !text || text.length > 4000) return;
      if (typeof ViewActionReceipts !== 'undefined' && ViewActionReceipts.busy()) return;
      const ready = voice.ready || await startVoiceSession(false);
      if (!ready || voice.socket?.readyState !== 1) return;
      flushVoiceView();
      if (typeof ViewActionReceipts !== 'undefined') ViewActionReceipts.expectTurn();
      voice.socket.send(JSON.stringify({type:'text',text}));
      refreshVoiceControls();
      input.value = '';
      voiceStatus('Note sent. Review the intake agent’s draft before applying it.');
    }
    function applyVoiceDraft() {
      const draft = voice.draft;
      if (matchInFlight || !draft?.ready_for_review || voiceDraftErrors(draft).length) {
        voiceStatus('The draft is incomplete or its deadline has passed. Confirm the missing details before applying.');
        refreshVoiceControls(); return;
      }
      const fields = draft.fields;
      stopVoiceSession('Reviewed draft applied. Check the form, then select Find a viable match.');
      offerChanged();
      document.getElementById('input-donor').value = fields.donor_name;
      document.getElementById('input-text').value = fields.posting_text;
      document.getElementById('input-storage').value = fields.required_storage;
      document.getElementById('input-pallets').value = fields.pallet_count;
      document.getElementById('input-hours').value = fields.hours_remaining;
      selectedOrigin = {latitude:fields.donor_lat,longitude:fields.donor_lon};
      offerClock.apply(fields.deadline_at, fields.hours_remaining);
      document.getElementById('origin-note').textContent = `Reviewed origin: ${fields.donor_lat}, ${fields.donor_lon}. Confirm the actual pickup location; any demo coordinates remain approximate.`;
      refreshDeadlineDisplay();
      setStatus('Voice draft applied for review. The exact deadline is preserved. No match or outreach has been requested.');
      if (typeof RecipientExplorer !== 'undefined') RecipientExplorer.refresh();
      focusSection('offer-panel');
    }
    function validPoint(point) {
      return point && Number.isFinite(point.latitude) && Number.isFinite(point.longitude)
        && Math.abs(point.latitude) <= 90 && Math.abs(point.longitude) <= 180;
    }
    function readable(value, suffix = '') {
      return value === null || value === undefined ? 'Unknown' : String(value) + suffix;
    }
    function initMap() {
      if (typeof L === 'undefined') {
        document.getElementById('map').textContent = 'Map library unavailable. Match briefs remain usable.';
        return;
      }
      // ResizeObserver owns container changes, including workspace view switches.
      // Leaflet's window listener otherwise redraws hidden heat canvases at 0px.
      map = L.map('map', {trackResize:false}).setView([47.6062, -122.3321], 11);
      map.on?.('moveend', () => { if (typeof ViewContext !== 'undefined') ViewContext.changed(); });
      // Standard interactive OSM tiles use normal browser Referer and HTTP caching.
      // No prefetch, offline downloads, cache-busting parameters or API keys.
      const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
        maxZoom: 19, updateWhenIdle: true, keepBuffer: 1
      });
      const tileNotice = document.getElementById('map-tile-status');
      let tileErrors = 0;
      tiles.on('loading', () => { tileErrors = 0; });
      tiles.on('tileerror', () => {
        tileErrors += 1;
        tileNotice.textContent = 'Street-map tiles are temporarily unavailable. Data markers and match briefs still work; use the Google Maps directions link for navigation.';
        tileNotice.classList.remove('hidden');
      });
      tiles.on('load', () => {
        if (!tileErrors) { tileNotice.textContent = ''; tileNotice.classList.add('hidden'); }
      });
      tiles.addTo(map);
      const resizeVisibleMap = () => {
        const container = document.getElementById('map');
        if (container.clientWidth > 0 && container.clientHeight > 0) {
          map.invalidateSize({pan:false,animate:false});
        }
      };
      if (typeof ResizeObserver !== 'undefined') {
        let previousSize = '';
        const observer = new ResizeObserver(entries => {
          const size = entries[0].contentRect;
          const key = `${size.width},${size.height}`;
          if (size.width > 0 && size.height > 0 && key !== previousSize) {
            previousSize = key;
            requestAnimationFrame(resizeVisibleMap);
          }
        });
        observer.observe(document.getElementById('map'));
      } else {
        window.addEventListener?.('resize', resizeVisibleMap);
      }
      cityRecipientLayer = L.layerGroup().addTo(map);
      tractPointLayer = L.layerGroup().addTo(map);
      markersLayer = L.layerGroup().addTo(map);
    }
    function rate(value) {
      return Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
    }
    function cityPoint(row) {
      return {latitude: row.lat ?? row.latitude, longitude: row.lon ?? row.longitude};
    }
    function heatPoints(tracts, metric) {
      return tracts.flatMap(tract => {
        const point = cityPoint(tract);
        const intensity = rate(tract[metric]);
        return validPoint(point) && intensity !== null ? [[point.latitude, point.longitude, intensity]] : [];
      });
    }
    function capabilityVisible(recipient, filter) {
      const cold = String(recipient.cold_storage || '').toLowerCase();
      if (filter === 'cold') return ['walk-in','commercial','domestic','medical'].includes(cold);
      if (filter === 'frozen') return recipient.frozen_storage === true;
      if (filter === 'ambient') return ['none','dry','ambient'].includes(cold);
      return true;
    }
    async function loadCityData() {
      const btn = document.getElementById('btn-city');
      btn.disabled = true;
      document.getElementById('city-status').textContent = 'Loading aggregate census and recipient data from BigQuery…';
      try {
        const response = await fetch('/api/city');
        const data = await response.json();
        if (!response.ok || !['ready','success','ok','partial'].includes(data.status)) throw new Error(data.message || 'City dataset is not ready.');
        cityPayload = data;
        if (typeof ViewContext !== 'undefined') ViewContext.changed('sources');
        renderCityLayers();
        document.getElementById('city-provenance').textContent = `Seattle aggregate data · retrieved ${data.as_of || 'time not supplied'}. Operational profiles require recipient confirmation; point locations are approximate.`;
        document.getElementById('city-source-details').textContent = JSON.stringify({provenance:data.provenance,limits:data.limits},null,2);
        const caveats = document.getElementById('city-caveats');
        caveats.replaceChildren();
        (data.warnings || []).forEach(warning => caveats.appendChild(textNode('p',warning)));
      } catch (error) {
        cityPayload = null;
        if (typeof ViewContext !== 'undefined') ViewContext.changed('sources');
        if (cityRecipientLayer) cityRecipientLayer.clearLayers();
        if (tractPointLayer) tractPointLayer.clearLayers();
        if (heatLayer && map) { map.removeLayer(heatLayer); heatLayer = null; }
        document.getElementById('city-status').textContent = `Needs data — ${error.message}`;
        document.getElementById('city-provenance').textContent = 'No city data displayed. No heat values or recipient capabilities were fabricated.';
        renderCityLayers();
      } finally { btn.disabled = false; }
    }
    function viewFiltersChanged() {
      if (typeof ViewContext !== 'undefined') ViewContext.changed();
      renderCityLayers();
    }
    function renderCityLayers() {
      if (typeof RecipientExplorer !== 'undefined') RecipientExplorer.refresh();
      const tracts = cityPayload?.tracts || [];
      const recipients = cityPayload?.recipients || [];
      const metric = document.getElementById('heat-metric').value || 'need_score';
      const filter = document.getElementById('capability-filter').value || 'all';
      const points = heatPoints(tracts, metric);
      const visibleRecipients = typeof RecipientExplorer !== 'undefined'
        ? RecipientExplorer.visibleRecipients() : recipients.filter(r => capabilityVisible(r, filter));
      const metricLabel = metric === 'need_score' ? 'Combined need = mean of poverty and no-vehicle rates' : metric === 'poverty_rate' ? 'Poverty rate' : 'No-vehicle household rate';
      document.getElementById('heat-legend-label').textContent = `Low → high ${metricLabel} (0–1). Smoothed tract-centroid need proxy; density affects intensity. Not tract boundaries, household food insecurity or individual eligibility.`;
      if (cityPayload) {
        document.getElementById('city-status').textContent = `${points.length} census points · ${visibleRecipients.length} recipient profiles in this view. Source caveats are listed below.`;
        document.getElementById('city-status').className = 'text-xs text-secondary';
      }
      if (!map || !cityRecipientLayer || !tractPointLayer) return;
      cityRecipientLayer.clearLayers();
      tractPointLayer.clearLayers();
      if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
      if (document.getElementById('show-need').checked) {
        if (typeof L.heatLayer === 'function') {
          heatLayer = L.heatLayer(points, {radius:30, blur:23, maxZoom:12, max:1, minOpacity:0.15,
            gradient:{0.15:'#2563eb',0.35:'#06b6d4',0.55:'#facc15',0.75:'#f97316',1:'#ef4444'}}).addTo(map);
        } else document.getElementById('city-status').textContent += ' Heat renderer unavailable; showing clickable census points only.';
        tracts.forEach(tract => {
          const point = cityPoint(tract);
          if (!validPoint(point)) return;
          const percentage = value => rate(value) === null ? 'Unknown' : `${(value * 100).toFixed(1)}%`;
          L.circleMarker([point.latitude,point.longitude], {radius:3,color:'#e2e8f0',weight:1,fillOpacity:0.25})
            .addTo(tractPointLayer).bindPopup(popup(`Census tract ${readable(tract.tract_id)}`, [
              `Poverty: ${percentage(tract.poverty_rate)}`, `Households without a vehicle: ${percentage(tract.no_vehicle_rate)}`,
              `Combined need: ${rate(tract.need_score) === null ? 'Unknown' : tract.need_score.toFixed(3)}`,
              `Population: ${readable(tract.population)}`, 'Approximate tract centroid. Neighborhood-level evidence only.'
            ]));
        });
      }
      if (document.getElementById('show-recipients').checked) visibleRecipients.forEach(recipient => {
        const point = cityPoint(recipient);
        if (!validPoint(point)) return;
        const cold = capabilityVisible(recipient, 'cold');
        const decision = recipient._decision || 'not_evaluated';
        const rank = [currentPayload?.winner, ...(currentPayload?.runners_up || [])]
          .findIndex(row => row && String(row.recipient_id) === String(recipient.recipient_id)) + 1;
        let marker;
        if (decision !== 'not_evaluated') {
          const color = decision === 'rejected' ? '#b5474d' : decision === 'needs_confirmation' ? '#94631f' : rank === 1 ? '#197764' : '#176b96';
          const symbol = decision === 'rejected' ? '×' : decision === 'needs_confirmation' ? '?' : String(rank || '✓');
          // Only application-owned symbols/colors are HTML; profile data remain text nodes.
          const icon = L.divIcon({className:'custom-pin',html:`<div style="background:${color};color:white;border-radius:50%;padding:5px;text-align:center;font-weight:700">${symbol}</div>`,iconSize:[32,32],iconAnchor:[16,16]});
          marker = L.marker([point.latitude,point.longitude], {icon,title:readable(recipient.name)});
        } else {
          marker = L.circleMarker([point.latitude,point.longitude], {radius:5,color:recipient.frozen_storage === true ? '#a78bfa' : cold ? '#38bdf8' : '#94a3b8',weight:2,fillOpacity:0.7});
        }
        marker.addTo(cityRecipientLayer).bindPopup(popup(readable(recipient.name), [
            `Profile storage: ${readable(recipient.cold_storage)}; freezer: ${readable(recipient.frozen_storage)}`,
            `Profile capacity: ${readable(recipient.max_pallets, ' pallets')}`,
            `Receiving days: ${readable(recipient.service_days)}`, `Source: ${readable(recipient.profile_source)}`,
            'Approximate location; capability and current availability require confirmation.'
          ]));
        if (typeof RecipientExplorer !== 'undefined') RecipientExplorer.markMarker(marker, recipient);
      });
    }
    function renderRoute(route, donor, winner) {
      const directions = document.getElementById('external-directions');
      directions.classList.add('hidden');
      directions.removeAttribute('href');
      if (validPoint(donor) && validPoint(winner)) {
        directions.href = `https://www.google.com/maps/dir/?api=1&origin=${donor.latitude},${donor.longitude}&destination=${winner.latitude},${winner.longitude}&travelmode=driving`;
        directions.classList.remove('hidden');
      }
      // Google directions open on Google Maps; no road geometry or travel time is invented here.
      document.getElementById('route-status').textContent = 'Routing unavailable on this map — open Google Maps to calculate directions using approximate endpoints. Dashed line is an illustrative straight-line connection; no travel time is claimed.';
      if (markersLayer && validPoint(donor) && validPoint(winner)) L.polyline([[donor.latitude,donor.longitude],[winner.latitude,winner.longitude]], {color:'#10b981',weight:3,dashArray:'6, 6'}).addTo(markersLayer);
    }
    function fitRecipientView() {
      if (!map) return;
      const recipients = typeof RecipientExplorer !== 'undefined'
        ? RecipientExplorer.visibleRecipients() : (cityPayload?.recipients || []);
      fitSeattleResults(currentPayload?.donor || {}, {}, recipients.map(cityPoint));
    }
    async function searchFoodKeeper() {
      const button = document.getElementById('btn-foodkeeper');
      const status = document.getElementById('foodkeeper-status');
      const query = document.getElementById('foodkeeper-query').value.trim();
      document.getElementById('foodkeeper-results').replaceChildren();
      document.getElementById('foodkeeper-notice').textContent = '';
      document.getElementById('foodkeeper-unknowns').textContent = '';
      if (query.length < 2 || query.length > 120) {
        status.textContent = 'Enter a specific product query of 2–120 characters.';
        return;
      }
      button.disabled = true;
      status.textContent = 'Looking up the pinned USDA FoodKeeper dataset…';
      try {
        const response = await fetch(`/api/foodkeeper?query=${encodeURIComponent(query)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || (typeof data.detail === 'string' ? data.detail : 'The lookup could not complete.'));
        renderFoodKeeper(data);
      } catch (error) {
        status.textContent = error.message || 'Guidance unavailable. No shelf life has been assumed.';
      } finally { button.disabled = false; }
    }
    function renderFoodKeeper(data) {
      const container = document.getElementById('foodkeeper-results');
      container.replaceChildren();
      const products = Array.isArray(data.products) ? data.products.slice(0,5) : [];
      document.getElementById('foodkeeper-status').textContent = products.length
        ? `${data.total_matches ?? products.length} matching option(s)${data.truncated ? '; showing the first five' : ''}. No product is automatically selected; review the subtype and storage state.`
        : 'No matching product found. Try another product name or fewer terms; no shelf life has been assumed.';
      document.getElementById('foodkeeper-notice').textContent = data.advisory_notice || 'Consumer quality and freshness guidance only; not a commercial food-safety determination.';
      document.getElementById('foodkeeper-unknowns').textContent = `Confirm before applying any guidance: ${(data.unknowns_required || ['Exact product and subtype','Opened, unopened or thawed state','Relevant dates, package instructions and storage history']).join('; ')}. Donor pickup deadline remains unchanged.`;
      products.forEach(product => {
        const card = document.createElement('details');
        card.className = 'surface-inset rounded-lg border border-line p-3 text-xs';
        const name = [product.name,product.subtitle].filter(Boolean).join(' — ');
        card.appendChild(textNode('summary',name || 'Unnamed product','font-semibold text-accent cursor-pointer'));
        card.appendChild(textNode('p',`Category: ${readable(product.category)} · Subcategory: ${readable(product.subcategory)} · FoodKeeper ID: ${readable(product.product_id)}`,'text-muted mt-2'));
        const advice = Array.isArray(product.storage_advice) ? product.storage_advice : [];
        if (!advice.length) card.appendChild(textNode('p','No storage interval is provided for this product. Do not infer one.','text-warning mt-2'));
        advice.forEach(item => {
          const row = document.createElement('div');
          row.className = 'border-t border-line mt-2 pt-2';
          row.appendChild(textNode('strong',`${readable(item.storage)} · basis: ${readable(item.basis).replaceAll('_',' ')}`,'block text-ink'));
          row.appendChild(textNode('p',readable(item.raw_value),'text-secondary whitespace-pre-wrap'));
          row.appendChild(textNode('small',`Source field: ${readable(item.raw_field)}`,'text-muted break-words'));
          card.appendChild(row);
        });
        const raw = document.createElement('details');
        raw.className = 'mt-3 text-muted';
        raw.appendChild(textNode('summary','Inspect original storage fields (null means not supplied)','cursor-pointer'));
        raw.appendChild(textNode('pre',JSON.stringify(product.raw_storage_fields || {},null,2),'whitespace-pre-wrap break-words mt-2'));
        card.appendChild(raw);
        container.appendChild(card);
      });
    }
    function applyScenario(sc) {
      if (matchInFlight) return;
      offerChanged();
      document.getElementById('input-donor').value = sc.donor_name;
      document.getElementById('input-text').value = sc.posting_text;
      document.getElementById('input-storage').value = sc.required_storage;
      document.getElementById('input-pallets').value = sc.pallet_count;
      document.getElementById('input-hours').value = sc.hours_remaining;
      selectedOrigin = {latitude: sc.donor_lat, longitude: sc.donor_lon};
      offerClock.reset();
      refreshDeadlineDisplay();
      document.getElementById('origin-note').textContent = `Origin: ${sc.donor_name} demo coordinates. Pallet quantity is a scenario assumption; confirm the actual volume.`;
      setStatus(sc.notes || 'Scenario loaded. Review the offer, then run a live match.');
      if (typeof RecipientExplorer !== 'undefined') RecipientExplorer.refresh();
    }
    function retailPresets(scenarios) {
      const bakery = scenarios.find(sc => sc.id === 'bakery');
      const chilled = scenarios.find(sc => sc.id === 'dairy') || scenarios.find(sc => sc.id === 'spinach');
      const presets = [];
      if (bakery) presets.push({...bakery, title:'Bakery surplus'});
      if (chilled) presets.push({...chilled, title:'Urgent chilled offer', notes:`Refrigeration required · ${chilled.pallet_count} pallets · ${chilled.hours_remaining} hours. Review which physical constraints block a match.`});
      if (bakery) presets.push({...bakery, id:'capacity-pressure', title:'Full-lot capacity stress test', pallet_count:50,
        posting_text:'50 pallets of boxed bread and baked goods, ambient dry storage; the recipient must accept the full lot',
        notes:'Demo stress case · 50 pallets, no assumed split. Check capacity rejections; only change a real offer when the donor confirms it.'});
      return presets.length ? presets : scenarios;
    }
    async function loadScenarios() {
      try {
        const res = await fetch('/api/scenarios');
        if (!res.ok) throw new Error('Unable to load scenarios.');
        const scenarios = await res.json();
        const container = document.getElementById('scenario-buttons');
        container.replaceChildren();
        scenarioButtons = [];
        retailPresets(scenarios).forEach(sc => {
          const btn = document.createElement('button');
          btn.className = 'scenario-choice';
          btn.setAttribute('aria-pressed','false');
          btn.disabled = matchInFlight;
          btn.appendChild(textNode('span', sc.title, 'font-semibold text-xs text-ink'));
          btn.appendChild(textNode('span', sc.notes || sc.posting_text));
          btn.onclick = () => { if (matchInFlight) return; applyScenario(sc); btn.setAttribute('aria-pressed','true'); };
          scenarioButtons.push(btn);
          container.appendChild(btn);
        });
      } catch (err) { setStatus(err.message, true); }
    }
    function buildRequest() {
      const req = {
        donor_name: document.getElementById('input-donor').value.trim(),
        posting_text: document.getElementById('input-text').value.trim(),
        required_storage: document.getElementById('input-storage').value,
        pallet_count: Number(document.getElementById('input-pallets').value),
        hours_remaining: Number(document.getElementById('input-hours').value),
        donor_lat: selectedOrigin.latitude,
        donor_lon: selectedOrigin.longitude
      };
      if (!req.donor_name || req.posting_text.length < 5) throw new Error('Add a donor name and a surplus description.');
      if (!Number.isFinite(req.pallet_count) || req.pallet_count <= 0 || req.pallet_count > 10000) throw new Error('Enter a positive pallet quantity, up to 10,000.');
      if (!Number.isFinite(req.hours_remaining) || req.hours_remaining <= 0 || req.hours_remaining > 168) throw new Error('Enter a pickup window greater than zero and at most 168 hours.');
      req.deadline_at = offerClock.forOffer(req.hours_remaining);
      refreshDeadlineDisplay();
      return req;
    }
    function deadlinePresentation(deadline, now = Date.now()) {
      const instant = Date.parse(deadline);
      if (typeof deadline !== 'string' || !Number.isFinite(instant)) return null;
      const local = new Intl.DateTimeFormat('en-US', {timeZone:'America/Los_Angeles',
        weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(new Date(instant));
      const expired = instant <= now;
      const seconds = Math.floor((instant - now) / 1000);
      const minutes = Math.floor(seconds / 60);
      const remaining = expired ? 'Deadline passed. Confirm a new window with the donor before retrying.'
        : minutes < 1 ? 'Less than a minute remains.'
        : `${Math.floor(minutes / 60) ? Math.floor(minutes / 60) + 'h ' : ''}${minutes % 60}m remaining.`;
      return {local, remaining, expired};
    }
    function refreshDeadlineDisplay() {
      // Presentation reads the existing clock; it never establishes or extends it.
      const presentation = deadlinePresentation(offerClock.deadline);
      const panel = document.getElementById('review-clock');
      const note = document.getElementById('voice-clock-note');
      panel.classList.toggle('hidden', !presentation);
      note.classList.toggle('hidden', !presentation);
      if (!presentation) return;
      panel.setAttribute('data-state', presentation.expired ? 'expired' : 'active');
      const time = document.getElementById('review-deadline');
      time.textContent = presentation.local;
      time.setAttribute('datetime', offerClock.deadline);
      time.setAttribute('title', offerClock.deadline);
      document.getElementById('review-time-left').textContent = presentation.remaining;
      note.textContent = `Fixed pickup deadline: ${presentation.local} (Seattle). Other offer edits and retries preserve this clock.`;
    }
    function renderMatchProgress(active = true) {
      const list = document.getElementById('progress-history');
      list.replaceChildren();
      receivedStages.forEach((entry, index) => {
        const row = document.createElement('li');
        row.setAttribute('data-stage', entry.stage);
        if (active && index === receivedStages.length - 1) row.setAttribute('aria-current','step');
        const body = textNode('div', OfferWorkflow.stages[entry.stage]);
        if (entry.count !== null) body.appendChild(textNode('small',`${entry.count} retrieved candidates · shortlist only`));
        row.appendChild(body);
        list.appendChild(row);
      });
    }
    function resetMatchProgress(visible) {
      receivedStages.length = 0;
      renderMatchProgress(false);
      document.getElementById('match-progress').classList.toggle('hidden', !visible);
      document.getElementById('progress-outcome').textContent = visible ? 'Waiting for the first broker stage report…' : '';
    }
    function finishMatchProgress(outcome) {
      renderMatchProgress(false);
      const summaries = {
        success:'Result received. Review the proposal and confirm recipient availability.',
        draft:'Fallback result received. Review the draft; recipient availability remains unconfirmed.',
        no_match:'Result received. No recipient passed the current checks.',
        no_viable_match:'Result received. No recipient passed the current checks.',
        blocked:'The broker could not complete a usable result. Stage reports alone are not a match.',
        canceled:'Check canceled. No result was used; received stages remain visible for context.',
        error:'The check did not finish. No result was used; retry retains the fixed deadline.',
      };
      document.getElementById('progress-outcome').textContent = summaries[outcome] || summaries.error;
    }
    function cancelMatching() {
      if (!matchInFlight || !activeMatchController) return;
      matchStopReason = 'canceled';
      activeMatchController.abort();
    }
    function matchProgress(stage, details = {}) {
      if (!matchInFlight || !Object.hasOwn(OfferWorkflow.stages, stage)) return;
      const count = Number.isSafeInteger(details.candidate_count) && details.candidate_count >= 0 && details.candidate_count <= 1000000 ? details.candidate_count : null;
      const previous = receivedStages.find(entry => entry.stage === stage);
      if (previous) { if (count !== null) previous.count = count; }
      else receivedStages.push({stage, count});
      renderMatchProgress();
      document.getElementById('progress-outcome').textContent = 'Awaiting the final broker result. No handoff is confirmed.';
      refreshDeadlineDisplay();
      const message = OfferWorkflow.stages[stage];
      document.getElementById('result-description').textContent = message;
      setStatus(message);
      if (count !== null) {
        document.getElementById('result-counts').textContent =
          `${count} retrieved candidates. Checks apply to this shortlist.`;
      }
    }
    async function runMatching() {
      if (matchInFlight) return;
      let reqBody;
      try { reqBody = buildRequest(); }
      catch (error) {
        resetMatchProgress(false);
        refreshDeadlineDisplay();
        setStatus(error.message || 'Review the offer fields before running a match.', true);
        focusSection('offer-panel');
        return;
      }
      matchInFlight = true;
      resetMatchProgress(true);
      lockOffer(true);
      const btn = document.getElementById('btn-match');
      const cancel = document.getElementById('btn-cancel-match');
      let requestTimer;
      matchStopReason = null;
      btn.disabled = true;
      btn.textContent = 'Running live retrieval and constraint checks…';
      document.getElementById('brief-container').classList.add('hidden');
      document.getElementById('run-evidence').classList.add('hidden');
      document.getElementById('pending-container').classList.add('hidden');
      document.getElementById('pending-list').replaceChildren();
      document.getElementById('decoys-list').replaceChildren(textNode('p','Candidate checks are in progress for this offer.'));
      currentPayload = null;
      if (markersLayer) markersLayer.clearLayers();
      if (typeof ViewContext !== 'undefined') ViewContext.changed('clear_result');
      renderCityLayers();
      setStatus('Checking semantic candidates, storage, capacity and the same pickup deadline…');
      reviewState('loading','Checking this offer','The live broker is retrieving candidates and checking feasibility. The offer is locked until this request finishes.');
      setWorkflow('review');
      document.getElementById('runtime-status').textContent = 'Running';
      document.getElementById('route-status').textContent = 'Routing pending — no current route has been verified.';
      document.getElementById('external-directions').classList.add('hidden');
      document.getElementById('external-directions').removeAttribute('href');
      try {
        activeMatchController = new AbortController();
        cancel.classList.remove('hidden');
        focusSection('review-panel');
        requestTimer = setTimeout(() => {
          matchStopReason = 'timeout';
          activeMatchController?.abort();
        }, 175000);
        const res = await fetch('/api/match/stream', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(reqBody),
          signal: activeMatchController.signal,
        });
        const data = await OfferWorkflow.readMatchStream(res, matchProgress, activeMatchController.signal);
        // Cancellation may win the microtask race after the final record arrives.
        if (activeMatchController.signal.aborted) {
          const error = new Error('Check canceled.'); error.name = 'AbortError'; throw error;
        }
        currentPayload = data;
        renderResults(data);
        finishMatchProgress(data.status);
        focusSection('review-panel');
      } catch (err) {
        const canceled = matchStopReason === 'canceled';
        const message = canceled ? 'Check canceled. No result was used. Retry keeps the same pickup deadline.'
          : matchStopReason === 'timeout' ? 'The live check reached its time limit. Retry keeps the same pickup deadline.'
          : err.message || 'The request could not complete. Please retry.';
        setStatus(message, true);
        document.getElementById('runtime-status').textContent = canceled ? 'Canceled' : 'Failed';
        reviewState(canceled ? 'canceled' : 'blocked',canceled ? 'Check canceled' : 'The live check did not finish',message);
        setWorkflow('review');
        finishMatchProgress(canceled ? 'canceled' : 'error');
        document.getElementById('decoys-list').replaceChildren(textNode('p', 'No current result. Previous results have been cleared.'));
        focusSection('review-panel');
      } finally {
        clearTimeout(requestTimer);
        activeMatchController = null;
        cancel.classList.add('hidden');
        matchInFlight = false;
        lockOffer(false);
        btn.textContent = 'Find a viable match';
      }
    }
    function addMarker(point, color, symbol, title, lines) {
      if (!markersLayer || !validPoint(point)) return;
      // All HTML below is static application markup; source data go into text nodes.
      const icon = L.divIcon({className: 'custom-pin', html: `<div style="background:${color};color:white;border-radius:50%;padding:7px;text-align:center">${symbol}</div>`, iconSize: [32,32], iconAnchor: [16,16]});
      const marker = L.marker([point.latitude, point.longitude], {icon, title}).addTo(markersLayer).bindPopup(popup(title, lines));
      if (typeof RecipientExplorer !== 'undefined' && point.recipient_id != null) RecipientExplorer.markMarker(marker, point);
    }
    function profileButton(candidate) {
      if (candidate.recipient_id == null) return null;
      const button = textNode('button', 'Inspect profile & checks', 'action-secondary');
      button.type = 'button';
      button.onclick = () => {
        if (typeof RecipientExplorer !== 'undefined') RecipientExplorer.select(candidate.recipient_id, button);
      };
      return button;
    }
    function renderDecoys(decoys) {
      const container = document.getElementById('decoys-list');
      container.replaceChildren();
      if (!decoys.length) container.appendChild(textNode('p', 'No rejected candidates were returned for this run.'));
      decoys.slice(0, 4).forEach(d => {
        const row = document.createElement('div');
        row.className = 'p-3 rounded-lg surface-card border border-negative text-secondary';
        row.appendChild(textNode('strong', d.name, 'block text-negative'));
        row.appendChild(textNode('span', (d.elimination_reasons || []).join(' '), 'text-muted'));
        const inspect = profileButton(d);
        if (inspect) row.appendChild(inspect);
        container.appendChild(row);
      });
    }
    function renderResults(data) {
      currentPayload = data;
      if (typeof ViewContext !== 'undefined') ViewContext.changed('result');
      renderCityLayers();
      document.getElementById('handoff-feedback').textContent = '';
      if (markersLayer) markersLayer.clearLayers();
      const decoys = data.eliminated_decoys || [];
      renderDecoys(decoys);
      const pending = data.pending_confirmation || [];
      const pendingList = document.getElementById('pending-list');
      pendingList.replaceChildren();
      document.getElementById('pending-container').classList.toggle('hidden',!pending.length);
      pending.forEach(candidate => {
        const row = document.createElement('div');
        row.className = 'p-3 rounded surface-inset border border-warning';
        row.appendChild(textNode('strong',candidate.name,'block text-warning'));
        const reasons = [...new Set([...(candidate.elimination_reasons || []),...(candidate.confirmations_required || [])])];
        row.appendChild(textNode('p',reasons.join(' ') || 'Confirm the missing recipient details before treating this as a viable match.','text-secondary'));
        const inspect = profileButton(candidate);
        if (inspect) row.appendChild(inspect);
        pendingList.appendChild(row);
      });
      const trace = data.execution_trace || [];
      document.getElementById('run-evidence').classList.remove('hidden');
      document.getElementById('execution-trace').textContent = trace.length ? JSON.stringify(trace, null, 2) : 'No execution trace returned.';
      document.getElementById('raw-json').textContent = JSON.stringify(data, null, 2);
      if (!['success', 'draft'].includes(data.status) || !data.winner) {
        document.getElementById('brief-container').classList.add('hidden');
        document.getElementById('runtime-status').textContent = data.status === 'blocked' ? 'Blocked' : 'No match';
        document.getElementById('route-status').textContent = 'Routing unavailable — no current eligible match.';
        document.getElementById('external-directions').classList.add('hidden');
        document.getElementById('external-directions').removeAttribute('href');
        setStatus(data.message || 'No actionable candidate passed the checks. Review the rejected candidates and offer constraints.', true);
        const blocked = data.status === 'blocked';
        reviewState(blocked ? 'blocked' : 'no-match', blocked ? 'Live broker unavailable' : 'No current recipient passed the checks',
          blocked ? (data.message || 'Retry the live check after the service is available.') : 'Review the ruled-out candidates and missing details. Confirm new facts with the donor or recipient before changing the offer; do not extend a deadline to force a match.',
          `${decoys.length} rejected · ${pending.length} awaiting details`);
        setWorkflow('review');
        return;
      }
      const donor = data.donor || {};
      const winner = data.winner;
      const alternatives = data.runners_up || [];
      const brief = data.match_brief || {};
      const alternativeList = document.getElementById('alternatives-list');
      alternativeList.replaceChildren();
      alternatives.slice(0, 2).forEach((candidate, index) => {
        const row = document.createElement('div');
        row.className = 'surface-inset p-3 rounded-lg border border-line';
        row.appendChild(textNode('strong', `${index + 2}. ${readable(candidate.name)}`, 'block text-accent'));
        row.appendChild(textNode('p', `Profile storage: ${readable(candidate.cold_storage)} · Capacity: ${readable(candidate.max_pallets, ' pallets')}`));
        row.appendChild(textNode('p', `Pickup window: ${readable(candidate.pickup_window_hours, 'h')} · Recipient confirmation required.`));
        const inspect = profileButton(candidate);
        if (inspect) row.appendChild(inspect);
        alternativeList.appendChild(row);
      });
      if (alternatives.length < 2) alternativeList.appendChild(textNode('p', alternatives.length ? 'One backup passed the available checks. A second option is not available.' : 'No backup passed the available checks. Confirm the top candidate before planning a collection.'));
      const fallback = data.generation_mode === 'deterministic_fallback' || data.status === 'draft';
      document.getElementById('runtime-status').textContent = fallback ? 'Fallback draft' : 'Completed';
      setStatus(fallback ? (data.message || 'Live retrieval completed, but Gemini did not produce a brief. Showing a deterministic draft for review.') : 'Proposed match ready. Confirm recipient acceptance and pickup before dispatch.', true);
      reviewState(fallback ? 'draft' : 'success',fallback ? 'A fallback draft is ready for review' : 'A proposed match is ready',
        fallback ? (data.message || 'Review the deterministic draft and source evidence before contacting the recipient.') : 'Review the recipient, clock margin and backup options, then prepare your outreach. Acceptance is still unconfirmed.',
        `1 proposed · ${alternatives.length} backup${alternatives.length === 1 ? '' : 's'} · ${decoys.length} rejected · ${pending.length} awaiting details`);
      setWorkflow('review');
      addMarker(donor, '#f59e0b', '●', `Donor: ${readable(donor.name)}`, [donor.posting_text || '']);
      renderRoute(data.route, donor, winner);
      document.getElementById('brief-container').classList.remove('hidden');
      document.getElementById('brief-winner-name').textContent = readable(winner.name);
      document.getElementById('brief-winner-address').textContent = `${readable(winner.street)}, ${readable(winner.city)}, WA ${readable(winner.zip_code)}`;
      document.getElementById('brief-badge-storage').textContent = `Profile storage: ${readable(winner.cold_storage)}`;
      document.getElementById('brief-badge-capacity').textContent = `Profile capacity: ${readable(winner.max_pallets, ' pallets')}`;
      document.getElementById('brief-badge-equity').textContent = `Tract no-vehicle: ${readable(winner.tract_no_vehicle_pct, '%')}`;
      document.getElementById('brief-justification').textContent = brief.match_justification || '';
      document.getElementById('brief-operational').textContent = brief.operational_verification || '';
      document.getElementById('brief-clock').textContent = brief.clock_action_plan || '';
      document.getElementById('brief-equity').textContent = brief.equity_rationale || 'No neighborhood rationale returned. Review the tract evidence and confirm the recipient’s service area.';
      document.getElementById('brief-sms').textContent = brief.drafted_sms_outreach || '';
      document.getElementById('brief-email').textContent = brief.drafted_email_outreach || '';
      document.getElementById('brief-provenance').textContent = [
        data.provenance_warning || 'Operational profile data are for this hackathon demonstration; verify details directly with the recipient.',
        `Profile source: ${readable(winner.profile_source)}.`,
        fallback ? 'Generation mode: deterministic fallback.' : `Generation mode: ${readable(data.generation_mode)}.`
      ].join(' ');
      if (map) fitSeattleResults(donor, winner, alternatives);
    }
    function seattleFitPoints(donor, winner, alternatives) {
      // Only current eligible options control framing. Distant/rejected profiles
      // must not zoom the Seattle demo out across the country.
      return [donor,winner,...alternatives].filter(point => validPoint(point)
        && point.latitude >= 47.20 && point.latitude <= 47.86
        && point.longitude >= -122.55 && point.longitude <= -121.98)
        .map(point => [point.latitude,point.longitude]);
    }
    function fitSeattleResults(donor, winner, alternatives) {
      const points = seattleFitPoints(donor,winner,alternatives);
      map.fitBounds(points.length ? points : [[47.20,-122.55],[47.86,-121.98]],
        {padding:[24,24],maxZoom:points.length ? 13 : 11,animate:false});
    }
    function handoffText(data) {
      if (!data || !data.winner) return '';
      const brief = data.match_brief || {};
      const winner = data.winner;
      return [
        'PROPOSED MATCH — recipient confirmation required',
        `Recipient: ${readable(winner.name)}`,
        `Address: ${readable(winner.street)}, ${readable(winner.city)}, WA ${readable(winner.zip_code)}`,
        brief.match_justification || '', brief.operational_verification || '',
        brief.clock_action_plan || '', brief.equity_rationale || '',
        `Available backups: ${(data.runners_up || []).map(row => row.name).join('; ') || 'None returned'}`,
        `Generation mode: ${readable(data.generation_mode)}.`,
        '', 'SMS draft:', brief.drafted_sms_outreach || '', '', 'Email draft:', brief.drafted_email_outreach || '',
        '', data.provenance_warning || 'Dataset profiles require recipient confirmation. Nothing has been sent or reserved.'
      ].join('\n');
    }
    async function copyHandoff(text, label) {
      if (!currentPayload || !currentPayload.winner || !text) return;
      try {
        await navigator.clipboard.writeText(text);
        document.getElementById('handoff-feedback').textContent = `${label} copied. Review it before sending; no outreach was sent by this app.`;
        setWorkflow('handoff');
      } catch (_) {
        document.getElementById('handoff-feedback').textContent = 'Clipboard unavailable. Select and copy the draft manually.';
      }
    }
    async function copyOutreach() { await copyHandoff(document.getElementById('brief-sms').textContent,'SMS draft'); }
    async function copyEmail() { await copyHandoff(document.getElementById('brief-email').textContent,'Email draft'); }
    async function copyMatchBrief() { await copyHandoff(handoffText(currentPayload),'Match brief'); }
    async function copyJsonPayload() {
      if (!currentPayload) return;
      try { await navigator.clipboard.writeText(JSON.stringify(currentPayload, null, 2)); setStatus('Result JSON copied.'); }
      catch (_) { setStatus('Clipboard unavailable. Select and copy the JSON manually.', true); }
    }
    window.onload = () => {
      if (typeof ViewContext !== 'undefined') ViewContext.onChange(() => {
        if (!voice.ready || voice.socket?.readyState !== 1) return;
        if (ViewActionReceipts.busy()) {
          stopVoiceSession('The displayed view changed during the turn. Restart intake to use the updated evidence.');
          return;
        }
        if (voice.viewTimer) clearTimeout(voice.viewTimer);
        const generation = voice.generation;
        voice.viewTimer = setTimeout(() => {
          if (generation === voice.generation && voice.ready && voice.socket?.readyState === 1)
            flushVoiceView();
        }, 80);
      });
      initMap();
      if (typeof ViewContext !== 'undefined') ViewContext.start();
      loadScenarios();
      loadCityData();
      loadVoiceConfig();
      setStatus('Choose a scenario or enter an offer, then run a live match.');
      setWorkflow('offer');
      refreshDeadlineDisplay();
      deadlineTicker = setInterval(refreshDeadlineDisplay, 1000);
    };
    window.addEventListener?.('pagehide', () => {
      if (deadlineTicker !== null) clearInterval(deadlineTicker);
      deadlineTicker = null;
      cancelMatching();
      stopVoiceSession('Session closed.');
      if (typeof ViewContext !== 'undefined') ViewContext.dispose();
    });
  </script>
  <script src="/ui/source-state.js"></script>
  <script src="/ui/view-context.js"></script>
  <script src="/ui/workspace.js"></script>
  <script src="/ui/recipient-explorer.js"></script>

</body>
</html>
"""
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8080, reload=False, access_log=False)
