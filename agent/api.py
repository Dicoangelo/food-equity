"""
Seattle Food Equity & Surplus Broker - FastAPI Server & Interactive UI
Exposes REST API endpoints for team integration and serves an interactive Map Dashboard.
"""

import os
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("food_broker_api")

app = FastAPI(
    title="Seattle Food Equity & Surplus Broker API",
    description="Intelligent Surplus Brokering using BigQuery Vector Search and Gemini",
    version="1.0.0",
)

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
        "title": "🥖 Scenario 3: 2 Pallets Ambient Artisanal Bread",
        "posting_text": "2 pallets of fresh sourdough and sliced sandwich bread, baked this morning, ambient dry storage",
        "required_storage": "ambient",
        "pallet_count": 2.0,
        "hours_remaining": 24.0,
        "donor_name": "Capitol Hill Bakery Hub",
        "donor_lat": 47.6180,
        "donor_lon": -122.3160,
        "notes": "Tests ambient storage where refrigeration is NOT required, opening up smaller dry pantries."
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


@app.get("/healthz")
def health():
    return {"status": "ok", "cloud_dependencies": "not_checked"}


@app.post("/api/match")
async def match_surplus(req: MatchRequest):
    """Run the ADK/MCP broker; a returned draft is never a secured donation."""
    hours_remaining = req.hours_remaining
    if req.deadline_at is not None:
        hours_remaining = min(hours_remaining, (req.deadline_at - datetime.now(timezone.utc)).total_seconds() / 3600)
        if hours_remaining <= 0:
            raise HTTPException(status_code=422, detail="The pickup deadline has passed. Update the offer before matching.")
    try:
        result = await get_broker_agent().broker_posting_async(
            posting_text=req.posting_text,
            required_storage=req.required_storage,
            pallet_count=req.pallet_count,
            hours_remaining=hours_remaining,
            donor_name=req.donor_name,
            donor_lat=req.donor_lat,
            donor_lon=req.donor_lon,
        )
    except Exception as exc:
        # Cloud errors can include sensitive identifiers; return a stable public error.
        logger.error("Broker request failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=502, content={
            "status": "blocked", "message": "The live broker could not complete this request. Check the runtime logs and retry.",
            "generation_mode": "unavailable", "requires_confirmation": True,
        })
    return JSONResponse(content=result)


@app.get("/", response_class=HTMLResponse)
def index_dashboard():
    """Serves the full-featured interactive Map Dashboard for coordinators & team demos."""
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Seattle Resilient Food Equity & Surplus Broker</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    #map { height: 520px; width: 100%; border-radius: 0.75rem; }
    .pulse-pin { animation: pulse 2s infinite; }
    pre, #brief-sms { overflow-wrap: anywhere; }
    @media (max-width: 640px) {
      #map { height: 360px; }
      header { gap: 1rem; padding: 1rem; }
      header h1 { flex-wrap: wrap; }
      main { padding: 1rem; }
      #brief-container { padding: 1rem; }
    }
    @media (prefers-reduced-motion: reduce) { .pulse-pin { animation: none; } }
    @keyframes pulse {
      0% { transform: scale(0.95); opacity: 0.8; }
      50% { transform: scale(1.15); opacity: 1; }
      100% { transform: scale(0.95); opacity: 0.8; }
    }
  </style>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen flex flex-col font-sans">

  <!-- Header -->
  <header class="bg-slate-800 border-b border-slate-700 px-6 py-4 flex flex-wrap justify-between items-center shadow-lg">
    <div class="flex items-center gap-3">
      <div class="bg-emerald-500 text-slate-900 p-2.5 rounded-lg font-bold text-xl">
        <i class="fa-solid fa-truck-ramp-box"></i>
      </div>
      <div>
        <h1 class="text-xl font-bold tracking-tight text-white flex items-center gap-2">
          Seattle Food Equity & Surplus Broker
          <span class="text-xs font-semibold px-2.5 py-0.5 rounded bg-emerald-900 text-emerald-300 border border-emerald-700">Agents for Impact 2026</span>
        </h1>
        <p class="text-xs text-slate-400">Seattle retail & grocer rescue · ADK / MCP / Vertex AI</p>
      </div>
    </div>
    <div class="flex items-center gap-4 text-xs">
      <div class="flex items-center gap-2 bg-slate-700/60 px-3 py-1.5 rounded-full border border-slate-600">
        <span class="w-2.5 h-2.5 rounded-full bg-slate-400"></span>
        <span class="text-slate-300">Live run: <strong id="runtime-status">Not run yet</strong></span>
      </div>
      <div class="flex items-center gap-2 bg-slate-700/60 px-3 py-1.5 rounded-full border border-slate-600">
        <i class="fa-solid fa-map-pin text-sky-400"></i>
        <span class="text-slate-300">Metro: <strong>Seattle, WA</strong></span>
      </div>
    </div>
  </header>

  <!-- Main Grid -->
  <main class="flex-1 p-6 grid grid-cols-1 lg:grid-cols-12 gap-6 max-w-[1600px] w-full mx-auto">

    <!-- Left Controls Panel -->
    <div class="lg:col-span-4 flex flex-col gap-6">

      <!-- Scenario Selector -->
      <div class="bg-slate-800 rounded-xl p-5 border border-slate-700 shadow-md">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3 flex items-center gap-2">
          <i class="fa-solid fa-bolt text-amber-400"></i> Quick Test Scenarios
        </h2>
        <div class="flex flex-col gap-2" id="scenario-buttons">
          <!-- Loaded by JS -->
        </div>
      </div>

      <!-- Donor Input Form -->
      <div class="bg-slate-800 rounded-xl p-5 border border-slate-700 shadow-md flex flex-col gap-4">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-2">
          <i class="fa-solid fa-pen-to-square text-sky-400"></i> Donor Surplus Posting
        </h2>

        <div>
          <label for="input-donor" class="block text-xs text-slate-400 mb-1">Donor Name / Origin</label>
          <input id="input-donor" type="text" value="Pike Place Grocer Dock"
                 class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500" />
        </div>

        <div>
          <label for="input-text" class="block text-xs text-slate-400 mb-1">Surplus Description (Query for Vector Search)</label>
          <textarea id="input-text" rows="3"
                    class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500">10 crates of spinach, harvested yesterday, needs cold storage</textarea>
        </div>

        <div class="grid grid-cols-3 gap-3">
          <div>
            <label for="input-storage" class="block text-xs text-slate-400 mb-1">Required Storage</label>
            <select id="input-storage" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-2 text-sm text-white focus:outline-none focus:border-emerald-500">
              <option value="cold" selected>Cold / Refrig</option>
              <option value="ambient">Ambient (Dry)</option>
              <option value="frozen">Frozen</option>
            </select>
          </div>
          <div>
            <label for="input-pallets" class="block text-xs text-slate-400 mb-1">Volume (Pallets)</label>
            <input id="input-pallets" type="number" min="0.01" max="10000" step="0.5" value="1.0"
                   class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-2 text-sm text-white focus:outline-none focus:border-emerald-500" />
          </div>
          <div>
            <label for="input-hours" class="block text-xs text-slate-400 mb-1">Clock (Hours)</label>
            <input id="input-hours" type="number" min="0.01" max="168" step="0.5" value="6.0"
                   class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-2 text-sm text-white focus:outline-none focus:border-emerald-500" />
          </div>
        </div>

        <p id="origin-note" class="text-xs text-slate-400">Origin: Pike Place demo coordinates. Pallet quantity is a scenario assumption, not a crates-to-pallet conversion.</p>
        <div id="match-status" role="status" aria-live="polite" class="text-sm text-slate-300"></div>
        <button id="btn-match" onclick="runMatching()"
                class="mt-2 w-full bg-emerald-600 hover:bg-emerald-500 text-white font-semibold py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition duration-150 shadow-lg shadow-emerald-900/30">
          <i class="fa-solid fa-magnifying-glass-location"></i>
          <span>Run Vector Search & Broker Match</span>
        </button>
      </div>

      <!-- Optional advisory lookup: independent of the offer and matching clock. -->
      <details class="bg-slate-800 rounded-xl p-5 border border-slate-700 shadow-md">
        <summary class="text-sm font-semibold text-sky-300 cursor-pointer">Shelf-life evidence · USDA FoodKeeper</summary>
        <p class="text-xs text-slate-400 mt-3">Consumer storage guidance for quality and freshness, not a commercial cold-chain or food-safety determination. Review the exact product, subtype and storage basis. This lookup never extends or changes the donor's pickup deadline.</p>
        <label for="foodkeeper-query" class="block text-xs text-slate-300 mt-3 mb-1">Product to look up</label>
        <input id="foodkeeper-query" type="search" maxlength="120" placeholder="For example: spinach or milk" onkeydown="if(event.key === 'Enter'){event.preventDefault();searchFoodKeeper();}"
          class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white" />
        <button id="btn-foodkeeper" type="button" onclick="searchFoodKeeper()" class="mt-2 bg-sky-700 hover:bg-sky-600 px-3 py-2 rounded text-sm text-white">Look up USDA guidance</button>
        <p id="foodkeeper-status" role="status" aria-live="polite" class="text-xs text-amber-300 mt-3">No product selected. Search and review the matching options.</p>
        <p id="foodkeeper-notice" class="text-xs text-slate-400 mt-2"></p>
        <div id="foodkeeper-results" class="flex flex-col gap-2 mt-3"></div>
        <p id="foodkeeper-unknowns" class="text-xs text-slate-400 mt-3"></p>
      </details>

      <!-- Eliminated Decoys Defense -->
      <div class="bg-slate-800 rounded-xl p-5 border border-slate-700 shadow-md">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-rose-400 mb-3 flex items-center gap-2">
          <i class="fa-solid fa-shield-halved"></i> Filtered Decoys (Rejection Defense)
        </h2>
        <div id="decoys-list" class="flex flex-col gap-2 text-xs">
          <p class="text-slate-500 italic">Run matching to see decoys eliminated by constraints.</p>
        </div>
      </div>

    </div>

    <!-- Right Map & Brief Panel -->
    <div class="lg:col-span-8 flex flex-col gap-6">

      <!-- Map Card -->
      <div class="bg-slate-800 rounded-xl p-4 border border-slate-700 shadow-md flex flex-col gap-3">
        <div class="flex flex-wrap gap-3 justify-between items-center px-1">
          <div class="flex items-center gap-2 text-sm font-semibold text-slate-200">
            <i class="fa-solid fa-map-location-dot text-emerald-400"></i>
            <span>Seattle Match Map</span>
          </div>
          <div class="flex flex-wrap items-center gap-3 text-xs">
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-amber-500 inline-block"></span> Donor</span>
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-emerald-500 inline-block"></span> Winner</span>
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-sky-500 inline-block"></span> Viable</span>
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-rose-500 inline-block"></span> Eliminated Decoy</span>
          </div>
        </div>
        <div class="flex flex-wrap gap-3 items-center text-xs bg-slate-900/60 p-3 rounded-lg">
          <button id="btn-city" onclick="loadCityData()" class="bg-sky-700 hover:bg-sky-600 px-3 py-2 rounded text-white">Load city data</button>
          <label class="flex items-center gap-1"><input id="show-need" type="checkbox" checked onchange="renderCityLayers()"> Census need heatmap</label>
          <label class="flex items-center gap-1"><input id="show-recipients" type="checkbox" checked onchange="renderCityLayers()"> Recipient capabilities</label>
          <label for="heat-metric">Need indicator</label>
          <select id="heat-metric" onchange="renderCityLayers()" class="bg-slate-800 border border-slate-600 rounded px-2 py-1">
            <option value="need_score">Combined need</option><option value="poverty_rate">Poverty rate</option><option value="no_vehicle_rate">No-vehicle household rate</option>
          </select>
          <label for="capability-filter">Storage</label>
          <select id="capability-filter" onchange="renderCityLayers()" class="bg-slate-800 border border-slate-600 rounded px-2 py-1">
            <option value="all">All profiles</option><option value="cold">Cold storage</option><option value="frozen">Freezer</option><option value="ambient">Ambient / dry</option>
          </select>
        </div>
        <p id="city-status" role="status" class="text-xs text-amber-300">Needs data — load the Seattle census and recipient layers.</p>
        <div id="heat-legend" class="text-xs text-slate-300">
          <div style="height:8px;background:linear-gradient(to right,#2563eb,#06b6d4,#facc15,#f97316,#ef4444);border-radius:4px;max-width:280px"></div>
          <span id="heat-legend-label">Low → high combined need (0–1); mean of poverty and no-vehicle rates. Smoothed tract-centroid points, not tract boundaries or individual hunger.</span>
        </div>
        <p class="text-xs text-slate-400">Capability points: purple = freezer · blue = refrigeration · gray = other/unknown. These are dataset profiles, not live availability.</p>
        <p id="city-provenance" class="text-xs text-slate-400">Sources have not been loaded.</p>
        <details class="text-xs text-slate-400"><summary class="cursor-pointer">City data source details</summary><pre id="city-source-details" class="whitespace-pre-wrap break-words mt-2"></pre></details>
        <p id="route-status" class="text-xs text-amber-300">Routing unavailable — no verified road route has been returned. Any dashed line is an illustrative straight-line connection.</p>
        <a id="external-directions" class="hidden text-sm text-sky-300 underline" target="_blank" rel="noopener noreferrer">Open directions in Google Maps (approximate endpoints; verify the address)</a>
        <p class="text-xs text-slate-400">Recipient points may represent ZIP-level locations. Confirm actual collection address, acceptance, storage and pickup with the recipient. Census indicators describe neighborhoods, not individual eligibility.</p>
        <p id="map-tile-status" role="status" class="text-xs text-amber-300 hidden"></p>
        <div id="map"></div>
      </div>

      <details id="run-evidence" class="bg-slate-800 rounded-xl p-4 border border-slate-700 hidden">
        <summary class="text-sm text-slate-300 cursor-pointer">Live execution trace</summary>
        <pre id="execution-trace" class="whitespace-pre-wrap break-words text-xs text-slate-400 mt-2"></pre>
      </details>

      <!-- Match Brief Artifact Card -->
      <div id="brief-container" class="bg-slate-800 rounded-xl p-6 border border-slate-700 shadow-md flex flex-col gap-4 hidden">
        <div class="flex flex-wrap justify-between items-start border-b border-slate-700 pb-4">
          <div>
            <span class="text-xs font-bold text-emerald-400 uppercase tracking-wider">Proposed Match Brief</span>
            <h2 id="brief-winner-name" class="text-2xl font-bold text-white">Winner Name</h2>
            <p id="brief-winner-address" class="text-xs text-slate-400">Address</p>
          </div>
          <div class="flex gap-2">
            <span id="brief-badge-storage" class="text-xs font-semibold px-3 py-1 rounded bg-slate-700 text-sky-300 border border-slate-600">Storage</span>
            <span id="brief-badge-capacity" class="text-xs font-semibold px-3 py-1 rounded bg-slate-700 text-amber-300 border border-slate-600">Capacity</span>
            <span id="brief-badge-equity" class="text-xs font-semibold px-3 py-1 rounded bg-purple-900/50 text-purple-300 border border-purple-700">Equity High</span>
          </div>
        </div>

        <div>
          <h3 class="text-xs font-semibold text-sky-400 uppercase mb-2">Next two options</h3>
          <div id="alternatives-list" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs text-slate-300"></div>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <!-- Justification -->
          <div class="bg-slate-900/60 p-4 rounded-lg border border-slate-700/60">
            <h3 class="text-xs font-semibold text-emerald-400 uppercase mb-1">Why Them (Justification)</h3>
            <p id="brief-justification" class="text-xs text-slate-300 leading-relaxed"></p>
          </div>
          <!-- Verification & Clock -->
          <div class="bg-slate-900/60 p-4 rounded-lg border border-slate-700/60">
            <h3 class="text-xs font-semibold text-sky-400 uppercase mb-1">Profile Capability & Pickup Clock</h3>
            <p id="brief-operational" class="text-xs text-slate-300 mb-2"></p>
            <p id="brief-clock" class="text-xs text-amber-300 font-medium"></p>
          </div>
        </div>

        <div class="bg-slate-900/60 p-4 rounded-lg border border-slate-700/60">
          <h3 class="text-xs font-semibold text-purple-300 uppercase mb-1">Who this helps</h3>
          <p id="brief-equity" class="text-xs text-slate-300 leading-relaxed"></p>
          <p class="text-[11px] text-slate-400 mt-2">Neighborhood evidence is approximate: ACS tract indicators describe the area near the recipient's ZIP-centroid location, not individual need or the recipient's actual service catchment. Confirm whom the recipient serves.</p>
        </div>

        <!-- Outreach Drafts -->
        <div class="bg-slate-900/80 p-4 rounded-lg border border-slate-700 flex flex-col gap-3">
          <div class="flex justify-between items-center">
            <h3 class="text-xs font-semibold text-slate-300 uppercase flex items-center gap-2">
              <i class="fa-solid fa-paper-plane text-emerald-400"></i> Drafted Outreach Messages
            </h3>
            <button onclick="copyOutreach()" class="text-xs bg-slate-700 hover:bg-slate-600 px-3 py-1 rounded text-white transition flex items-center gap-1">
              <i class="fa-solid fa-copy"></i> Copy SMS
            </button>
          </div>
          <div>
            <span class="text-[11px] text-slate-400 uppercase font-semibold">Coordinator SMS draft (may use multiple segments):</span>
            <div id="brief-sms" class="bg-slate-950 p-2.5 rounded border border-slate-800 text-xs text-emerald-300 font-mono mt-1"></div>
          </div>
          <div>
            <span class="text-[11px] text-slate-400 uppercase font-semibold">Email Outreach Draft:</span>
            <pre id="brief-email" class="bg-slate-950 p-3 rounded border border-slate-800 text-xs text-slate-300 font-sans whitespace-pre-wrap mt-1"></pre>
          </div>
        </div>

        <p id="brief-provenance" class="text-xs text-amber-300"></p>
        <!-- Team Handoff JSON -->
        <div class="border-t border-slate-700 pt-3">
          <details class="cursor-pointer">
            <summary class="text-xs text-slate-400 hover:text-slate-200 font-medium flex items-center gap-1">
              <i class="fa-solid fa-code text-sky-400"></i> Inspect Full JSON Payload (Team API Handoff)
            </summary>
            <div class="mt-2 relative">
              <button onclick="copyJsonPayload()" class="absolute top-2 right-2 text-xs bg-slate-800 hover:bg-slate-700 border border-slate-600 px-2 py-1 rounded text-slate-200">
                Copy JSON
              </button>
              <pre id="raw-json" class="bg-slate-950 p-3 rounded text-[11px] text-sky-300 font-mono overflow-x-auto max-h-60"></pre>
            </div>
          </details>
        </div>

      </div>

    </div>

  </main>

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
    let currentOfferKey = null;
    let pickupDeadline = null;

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
      el.className = warning ? 'text-sm text-amber-300' : 'text-sm text-slate-300';
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
      map = L.map('map').setView([47.6062, -122.3321], 11);
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
      if (typeof ResizeObserver !== 'undefined') {
        let previousSize = '';
        const observer = new ResizeObserver(entries => {
          const size = entries[0].contentRect;
          const key = `${size.width},${size.height}`;
          if (size.width > 0 && size.height > 0 && key !== previousSize) {
            previousSize = key;
            requestAnimationFrame(() => map.invalidateSize({pan:false,animate:false}));
          }
        });
        observer.observe(document.getElementById('map'));
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
        renderCityLayers();
        document.getElementById('city-provenance').textContent = `Seattle aggregate data · retrieved ${data.as_of || 'time not supplied'}. Operational profiles require recipient confirmation; point locations are approximate.`;
        document.getElementById('city-source-details').textContent = JSON.stringify({provenance:data.provenance,limits:data.limits,warnings:data.warnings},null,2);
      } catch (error) {
        cityPayload = null;
        if (cityRecipientLayer) cityRecipientLayer.clearLayers();
        if (tractPointLayer) tractPointLayer.clearLayers();
        if (heatLayer && map) { map.removeLayer(heatLayer); heatLayer = null; }
        document.getElementById('city-status').textContent = `Needs data — ${error.message}`;
        document.getElementById('city-provenance').textContent = 'No city data displayed. No heat values or recipient capabilities were fabricated.';
      } finally { btn.disabled = false; }
    }
    function renderCityLayers() {
      if (!cityPayload) return;
      const tracts = cityPayload.tracts || [];
      const recipients = cityPayload.recipients || [];
      const metric = document.getElementById('heat-metric').value || 'need_score';
      const filter = document.getElementById('capability-filter').value || 'all';
      const points = heatPoints(tracts, metric);
      const visibleRecipients = recipients.filter(r => capabilityVisible(r, filter));
      const metricLabel = metric === 'need_score' ? 'Combined need = mean of poverty and no-vehicle rates' : metric === 'poverty_rate' ? 'Poverty rate' : 'No-vehicle household rate';
      document.getElementById('heat-legend-label').textContent = `Low → high ${metricLabel} (0–1). Smoothed tract-centroid need proxy; density affects intensity. Not tract boundaries, household food insecurity or individual eligibility.`;
      document.getElementById('city-status').textContent = `${points.length} census points with ${metricLabel.toLowerCase()} · ${visibleRecipients.length} recipient profiles in the selected capability view. ${(cityPayload.warnings || []).join(' ')}`;
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
        L.circleMarker([point.latitude,point.longitude], {radius:5,color:recipient.frozen_storage === true ? '#a78bfa' : cold ? '#38bdf8' : '#94a3b8',weight:2,fillOpacity:0.7})
          .addTo(cityRecipientLayer).bindPopup(popup(readable(recipient.name), [
            `Profile storage: ${readable(recipient.cold_storage)}; freezer: ${readable(recipient.frozen_storage)}`,
            `Profile capacity: ${readable(recipient.max_pallets, ' pallets')}`,
            `Receiving days: ${readable(recipient.service_days)}`, `Source: ${readable(recipient.profile_source)}`,
            'Approximate location; capability and current availability require confirmation.'
          ]));
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
        card.className = 'bg-slate-900/70 rounded-lg border border-slate-700 p-3 text-xs';
        const name = [product.name,product.subtitle].filter(Boolean).join(' — ');
        card.appendChild(textNode('summary',name || 'Unnamed product','font-semibold text-sky-200 cursor-pointer'));
        card.appendChild(textNode('p',`Category: ${readable(product.category)} · Subcategory: ${readable(product.subcategory)} · FoodKeeper ID: ${readable(product.product_id)}`,'text-slate-400 mt-2'));
        const advice = Array.isArray(product.storage_advice) ? product.storage_advice : [];
        if (!advice.length) card.appendChild(textNode('p','No storage interval is provided for this product. Do not infer one.','text-amber-300 mt-2'));
        advice.forEach(item => {
          const row = document.createElement('div');
          row.className = 'border-t border-slate-700 mt-2 pt-2';
          row.appendChild(textNode('strong',`${readable(item.storage)} · basis: ${readable(item.basis).replaceAll('_',' ')}`,'block text-slate-200'));
          row.appendChild(textNode('p',readable(item.raw_value),'text-slate-300 whitespace-pre-wrap'));
          row.appendChild(textNode('small',`Source field: ${readable(item.raw_field)}`,'text-slate-500 break-words'));
          card.appendChild(row);
        });
        const raw = document.createElement('details');
        raw.className = 'mt-3 text-slate-400';
        raw.appendChild(textNode('summary','Inspect original storage fields (null means not supplied)','cursor-pointer'));
        raw.appendChild(textNode('pre',JSON.stringify(product.raw_storage_fields || {},null,2),'whitespace-pre-wrap break-words mt-2'));
        card.appendChild(raw);
        container.appendChild(card);
      });
    }
    function applyScenario(sc) {
      document.getElementById('input-donor').value = sc.donor_name;
      document.getElementById('input-text').value = sc.posting_text;
      document.getElementById('input-storage').value = sc.required_storage;
      document.getElementById('input-pallets').value = sc.pallet_count;
      document.getElementById('input-hours').value = sc.hours_remaining;
      selectedOrigin = {latitude: sc.donor_lat, longitude: sc.donor_lon};
      currentOfferKey = null;
      pickupDeadline = null;
      document.getElementById('origin-note').textContent = `Origin: ${sc.donor_name} demo coordinates. Pallet quantity is a scenario assumption; confirm the actual volume.`;
    }
    async function loadScenarios() {
      try {
        const res = await fetch('/api/scenarios');
        if (!res.ok) throw new Error('Unable to load scenarios.');
        const scenarios = await res.json();
        const container = document.getElementById('scenario-buttons');
        container.replaceChildren();
        scenarios.forEach(sc => {
          const btn = document.createElement('button');
          btn.className = 'text-left bg-slate-900/80 hover:bg-slate-700 p-2.5 rounded-lg border border-slate-700 transition flex flex-col';
          btn.appendChild(textNode('span', sc.title, 'font-semibold text-xs text-slate-200'));
          btn.appendChild(textNode('span', sc.posting_text, 'text-[11px] text-slate-400 truncate'));
          btn.onclick = () => { applyScenario(sc); runMatching(); };
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
      const key = JSON.stringify(req);
      if (key !== currentOfferKey) {
        currentOfferKey = key;
        pickupDeadline = new Date(Date.now() + req.hours_remaining * 3600000).toISOString();
      }
      req.deadline_at = pickupDeadline;
      return req;
    }
    async function runMatching() {
      const btn = document.getElementById('btn-match');
      btn.disabled = true;
      btn.textContent = 'Running live retrieval and constraint checks…';
      document.getElementById('brief-container').classList.add('hidden');
      document.getElementById('run-evidence').classList.add('hidden');
      currentPayload = null;
      if (markersLayer) markersLayer.clearLayers();
      setStatus('Contacting the live broker…');
      document.getElementById('runtime-status').textContent = 'Running';
      document.getElementById('route-status').textContent = 'Routing pending — no current route has been verified.';
      document.getElementById('external-directions').classList.add('hidden');
      try {
        const reqBody = buildRequest();
        const res = await fetch('/api/match', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(reqBody)
        });
        const data = await res.json();
        if (!res.ok) {
          const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('; ') : data.detail;
          throw new Error(data.message || detail || `Request failed (${res.status}).`);
        }
        currentPayload = data;
        renderResults(data);
      } catch (err) {
        setStatus(err.message || 'The request could not complete. Please retry.', true);
        document.getElementById('runtime-status').textContent = 'Failed';
        document.getElementById('decoys-list').replaceChildren(textNode('p', 'No current result. Previous results have been cleared.'));
      } finally {
        btn.disabled = false;
        btn.textContent = 'Run Vector Search & Broker Match';
      }
    }
    function addMarker(point, color, symbol, title, lines) {
      if (!markersLayer || !validPoint(point)) return;
      // All HTML below is static application markup; source data go into text nodes.
      const icon = L.divIcon({className: 'custom-pin', html: `<div style="background:${color};color:white;border-radius:50%;padding:7px;text-align:center">${symbol}</div>`, iconSize: [32,32], iconAnchor: [16,16]});
      L.marker([point.latitude, point.longitude], {icon}).addTo(markersLayer).bindPopup(popup(title, lines));
    }
    function renderDecoys(decoys) {
      const container = document.getElementById('decoys-list');
      container.replaceChildren();
      if (!decoys.length) container.appendChild(textNode('p', 'No rejected candidates were returned for this run.'));
      decoys.slice(0, 4).forEach(d => {
        const row = document.createElement('div');
        row.className = 'p-2 rounded bg-slate-900/60 border border-rose-900/40 text-slate-300';
        row.appendChild(textNode('strong', d.name, 'block text-rose-300'));
        row.appendChild(textNode('span', (d.elimination_reasons || []).join(' '), 'text-slate-400'));
        container.appendChild(row);
      });
    }
    function renderResults(data) {
      if (markersLayer) markersLayer.clearLayers();
      const decoys = data.eliminated_decoys || [];
      renderDecoys(decoys);
      const trace = data.execution_trace || [];
      document.getElementById('run-evidence').classList.remove('hidden');
      document.getElementById('execution-trace').textContent = trace.length ? JSON.stringify(trace, null, 2) : 'No execution trace returned.';
      document.getElementById('raw-json').textContent = JSON.stringify(data, null, 2);
      if (!['success', 'draft'].includes(data.status) || !data.winner) {
        document.getElementById('brief-container').classList.add('hidden');
        document.getElementById('runtime-status').textContent = data.status === 'blocked' ? 'Blocked' : 'No match';
        document.getElementById('route-status').textContent = 'Routing unavailable — no current eligible match.';
        setStatus(data.message || 'No actionable candidate passed the checks. Review the rejected candidates and offer constraints.', true);
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
        row.className = 'bg-slate-900/60 p-3 rounded-lg border border-slate-700';
        row.appendChild(textNode('strong', `${index + 2}. ${readable(candidate.name)}`, 'block text-sky-300'));
        row.appendChild(textNode('p', `Profile storage: ${readable(candidate.cold_storage)} · Capacity: ${readable(candidate.max_pallets, ' pallets')}`));
        row.appendChild(textNode('p', `Pickup window: ${readable(candidate.pickup_window_hours, 'h')} · Recipient confirmation required.`));
        alternativeList.appendChild(row);
      });
      if (alternatives.length < 2) alternativeList.appendChild(textNode('p', `Only ${alternatives.length} alternative(s) passed the available checks; do not invent a backup.`));
      const fallback = data.generation_mode === 'deterministic_fallback' || data.status === 'draft';
      document.getElementById('runtime-status').textContent = fallback ? 'Fallback draft' : 'Completed';
      setStatus(fallback ? (data.message || 'Live retrieval completed, but Gemini did not produce a brief. Showing a deterministic draft for review.') : 'Proposed match ready. Confirm recipient acceptance and pickup before dispatch.', true);
      addMarker(donor, '#f59e0b', '●', `Donor: ${readable(donor.name)}`, [donor.posting_text || '']);
      addMarker(winner, '#10b981', '★', `Top candidate: ${readable(winner.name)}`, [
        `${readable(winner.street)}, ${readable(winner.city)}`,
        `Profile storage: ${readable(winner.cold_storage)}`,
        `Profile capacity: ${readable(winner.max_pallets, ' pallets')}`,
        `Source: ${readable(winner.profile_source)}`,
        'Recipient acceptance and actual pickup are not yet confirmed.'
      ]);
      renderRoute(data.route, donor, winner);
      alternatives.forEach(v => addMarker(v, '#0ea5e9', '●', `Alternative: ${readable(v.name)}`, [
        `Storage: ${readable(v.cold_storage)}`, `Capacity: ${readable(v.max_pallets, ' pallets')}`, `Source: ${readable(v.profile_source)}`
      ]));
      decoys.forEach(d => addMarker(d, '#f43f5e', '×', `Rejected: ${readable(d.name)}`, d.elimination_reasons || []));
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
    async function copyOutreach() {
      try { await navigator.clipboard.writeText(document.getElementById('brief-sms').textContent); setStatus('Draft SMS copied. It has not been sent.'); }
      catch (_) { setStatus('Clipboard unavailable. Select and copy the draft manually.', true); }
    }
    async function copyJsonPayload() {
      if (!currentPayload) return;
      try { await navigator.clipboard.writeText(JSON.stringify(currentPayload, null, 2)); setStatus('Result JSON copied.'); }
      catch (_) { setStatus('Clipboard unavailable. Select and copy the JSON manually.', true); }
    }
    window.onload = () => {
      initMap();
      loadScenarios();
      loadCityData();
      setStatus('Choose a scenario or enter an offer, then run a live match.');
    };
  </script>

</body>
</html>
"""
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8080, reload=False)
