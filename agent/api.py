"""
Seattle Food Equity & Surplus Broker - FastAPI Server & Interactive UI
Exposes REST API endpoints for team integration and serves an interactive Map Dashboard.
"""

import os
import json
import logging
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from broker import FoodBrokerAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("food_broker_api")

app = FastAPI(
    title="Seattle Food Equity & Surplus Broker API",
    description="Intelligent Surplus Brokering using BigQuery Vector Search and Gemini",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

broker_agent = FoodBrokerAgent()


class MatchRequest(BaseModel):
    posting_text: str = "10 crates of spinach, harvested yesterday, needs cold storage"
    required_storage: str = "cold"
    pallet_count: float = 1.0
    hours_remaining: float = 6.0
    donor_name: str = "Seattle Metro Grocer (Pike Place)"
    donor_lat: float = 47.6097
    donor_lon: float = -122.3422


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


@app.post("/api/match")
def match_surplus(req: MatchRequest):
    """
    Main integration endpoint for team hand-off:
    Takes donor posting, runs BigQuery vector search + constraint filters,
    and returns ranked candidates, map markers, and the Gemini Match Brief.
    """
    result = broker_agent.broker_posting(
        posting_text=req.posting_text,
        required_storage=req.required_storage,
        pallet_count=req.pallet_count,
        hours_remaining=req.hours_remaining,
        donor_name=req.donor_name,
        donor_lat=req.donor_lat,
        donor_lon=req.donor_lon,
    )
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
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    #map { height: 520px; width: 100%; border-radius: 0.75rem; }
    .pulse-pin { animation: pulse 2s infinite; }
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
        <p class="text-xs text-slate-400">Powered by BigQuery Vector Search & Gemini 2.5 on Vertex AI</p>
      </div>
    </div>
    <div class="flex items-center gap-4 text-xs">
      <div class="flex items-center gap-2 bg-slate-700/60 px-3 py-1.5 rounded-full border border-slate-600">
        <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-ping"></span>
        <span class="text-slate-300">BigQuery: <strong>119 Profiles Ready</strong></span>
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
          <label class="block text-xs text-slate-400 mb-1">Donor Name / Origin</label>
          <input id="input-donor" type="text" value="Pike Place Grocer Dock"
                 class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500" />
        </div>

        <div>
          <label class="block text-xs text-slate-400 mb-1">Surplus Description (Query for Vector Search)</label>
          <textarea id="input-text" rows="3"
                    class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-emerald-500">10 crates of spinach, harvested yesterday, needs cold storage</textarea>
        </div>

        <div class="grid grid-cols-3 gap-3">
          <div>
            <label class="block text-xs text-slate-400 mb-1">Required Storage</label>
            <select id="input-storage" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-2 text-sm text-white focus:outline-none focus:border-emerald-500">
              <option value="cold" selected>Cold / Refrig</option>
              <option value="ambient">Ambient (Dry)</option>
              <option value="frozen">Frozen</option>
            </select>
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">Volume (Pallets)</label>
            <input id="input-pallets" type="number" step="0.5" value="1.0"
                   class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-2 text-sm text-white focus:outline-none focus:border-emerald-500" />
          </div>
          <div>
            <label class="block text-xs text-slate-400 mb-1">Clock (Hours)</label>
            <input id="input-hours" type="number" step="0.5" value="6.0"
                   class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-2 text-sm text-white focus:outline-none focus:border-emerald-500" />
          </div>
        </div>

        <button id="btn-match" onclick="runMatching()"
                class="mt-2 w-full bg-emerald-600 hover:bg-emerald-500 text-white font-semibold py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition duration-150 shadow-lg shadow-emerald-900/30">
          <i class="fa-solid fa-magnifying-glass-location"></i>
          <span>Run Vector Search & Broker Match</span>
        </button>
      </div>

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
        <div class="flex justify-between items-center px-1">
          <div class="flex items-center gap-2 text-sm font-semibold text-slate-200">
            <i class="fa-solid fa-map-location-dot text-emerald-400"></i>
            <span>Interactive Seattle Dispatch Map</span>
          </div>
          <div class="flex items-center gap-3 text-xs">
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-amber-500 inline-block"></span> Donor</span>
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-emerald-500 inline-block"></span> Winner</span>
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-sky-500 inline-block"></span> Viable</span>
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-rose-500 inline-block"></span> Eliminated Decoy</span>
          </div>
        </div>
        <div id="map"></div>
      </div>

      <!-- Match Brief Artifact Card -->
      <div id="brief-container" class="bg-slate-800 rounded-xl p-6 border border-slate-700 shadow-md flex flex-col gap-4 hidden">
        <div class="flex flex-wrap justify-between items-start border-b border-slate-700 pb-4">
          <div>
            <span class="text-xs font-bold text-emerald-400 uppercase tracking-wider">Top Match Brief</span>
            <h2 id="brief-winner-name" class="text-2xl font-bold text-white">Winner Name</h2>
            <p id="brief-winner-address" class="text-xs text-slate-400">Address</p>
          </div>
          <div class="flex gap-2">
            <span id="brief-badge-storage" class="text-xs font-semibold px-3 py-1 rounded bg-slate-700 text-sky-300 border border-slate-600">Storage</span>
            <span id="brief-badge-capacity" class="text-xs font-semibold px-3 py-1 rounded bg-slate-700 text-amber-300 border border-slate-600">Capacity</span>
            <span id="brief-badge-equity" class="text-xs font-semibold px-3 py-1 rounded bg-purple-900/50 text-purple-300 border border-purple-700">Equity High</span>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <!-- Justification -->
          <div class="bg-slate-900/60 p-4 rounded-lg border border-slate-700/60">
            <h3 class="text-xs font-semibold text-emerald-400 uppercase mb-1">Why Them (Justification)</h3>
            <p id="brief-justification" class="text-xs text-slate-300 leading-relaxed"></p>
          </div>
          <!-- Verification & Clock -->
          <div class="bg-slate-900/60 p-4 rounded-lg border border-slate-700/60">
            <h3 class="text-xs font-semibold text-sky-400 uppercase mb-1">Physical & Clock Verification</h3>
            <p id="brief-operational" class="text-xs text-slate-300 mb-2"></p>
            <p id="brief-clock" class="text-xs text-amber-300 font-medium"></p>
          </div>
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
            <span class="text-[11px] text-slate-400 uppercase font-semibold">Coordinator SMS (Urgent):</span>
            <div id="brief-sms" class="bg-slate-950 p-2.5 rounded border border-slate-800 text-xs text-emerald-300 font-mono mt-1"></div>
          </div>
          <div>
            <span class="text-[11px] text-slate-400 uppercase font-semibold">Email Outreach Draft:</span>
            <pre id="brief-email" class="bg-slate-950 p-3 rounded border border-slate-800 text-xs text-slate-300 font-sans whitespace-pre-wrap mt-1"></pre>
          </div>
        </div>

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
    let currentPayload = null;

    function initMap() {
      // Centered on Seattle
      map = L.map('map').setView([47.6062, -122.3321], 11);
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap &copy; CARTO',
        maxZoom: 19
      }).addTo(map);
      markersLayer = L.layerGroup().addTo(map);
    }

    async function loadScenarios() {
      const res = await fetch('/api/scenarios');
      const scenarios = await res.json();
      const container = document.getElementById('scenario-buttons');
      container.innerHTML = '';
      scenarios.forEach(sc => {
        const btn = document.createElement('button');
        btn.className = "text-left bg-slate-900/80 hover:bg-slate-700 p-2.5 rounded-lg border border-slate-700 transition flex flex-col";
        btn.innerHTML = `<span class="font-semibold text-xs text-slate-200">${sc.title}</span><span class="text-[11px] text-slate-400 truncate">${sc.posting_text}</span>`;
        btn.onclick = () => {
          document.getElementById('input-donor').value = sc.donor_name;
          document.getElementById('input-text').value = sc.posting_text;
          document.getElementById('input-storage').value = sc.required_storage;
          document.getElementById('input-pallets').value = sc.pallet_count;
          document.getElementById('input-hours').value = sc.hours_remaining;
          runMatching();
        };
        container.appendChild(btn);
      });
    }

    async function runMatching() {
      const btn = document.getElementById('btn-match');
      btn.disabled = true;
      btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>Querying BigQuery Vector Search & Gemini...</span>`;

      const reqBody = {
        donor_name: document.getElementById('input-donor').value,
        posting_text: document.getElementById('input-text').value,
        required_storage: document.getElementById('input-storage').value,
        pallet_count: parseFloat(document.getElementById('input-pallets').value),
        hours_remaining: parseFloat(document.getElementById('input-hours').value),
        donor_lat: 47.6097,
        donor_lon: -122.3422
      };

      try {
        const res = await fetch('/api/match', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(reqBody)
        });
        const data = await res.json();
        currentPayload = data;
        renderResults(data);
      } catch (err) {
        alert('Error executing match: ' + err);
      } finally {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-magnifying-glass-location"></i> <span>Run Vector Search & Broker Match</span>`;
      }
    }

    function renderResults(data) {
      markersLayer.clearLayers();

      if (data.status !== 'success') {
        alert(data.message || 'No viable match found');
        return;
      }

      const donor = data.donor;
      const winner = data.winner;
      const viable = data.runners_up || [];
      const decoys = data.eliminated_decoys || [];
      const brief = data.match_brief || {};

      // 1. Plot Donor Pin
      const donorIcon = L.divIcon({
        className: 'custom-pin',
        html: `<div class="bg-amber-500 text-slate-900 font-bold p-2 rounded-full shadow-lg flex items-center justify-center w-8 h-8 pulse-pin"><i class="fa-solid fa-store"></i></div>`,
        iconSize: [32, 32],
        iconAnchor: [16, 16]
      });
      L.marker([donor.latitude, donor.longitude], { icon: donorIcon })
        .addTo(markersLayer)
        .bindPopup(`<strong>Donor:</strong> ${donor.name}<br><em>${donor.posting_text}</em>`);

      // 2. Plot Winner Pin
      if (winner && winner.latitude && winner.longitude) {
        const winnerIcon = L.divIcon({
          className: 'custom-pin',
          html: `<div class="bg-emerald-500 text-white font-bold p-2 rounded-full shadow-lg flex items-center justify-center w-10 h-10 border-2 border-white"><i class="fa-solid fa-star text-base"></i></div>`,
          iconSize: [40, 40],
          iconAnchor: [20, 20]
        });
        const winnerMarker = L.marker([winner.latitude, winner.longitude], { icon: winnerIcon })
          .addTo(markersLayer)
          .bindPopup(`<strong>⭐ SELECTED WINNER: ${winner.name}</strong><br>${winner.street}, ${winner.city}<br>Cold Storage: ${winner.cold_storage}<br>Max Pallets: ${winner.max_pallets}<br>Tract Poverty: ${winner.tract_poverty_pct}% | No Vehicle: ${winner.tract_no_vehicle_pct}%`)
          .openPopup();

        // Draw dispatch line from donor to winner
        L.polyline([
          [donor.latitude, donor.longitude],
          [winner.latitude, winner.longitude]
        ], { color: '#10b981', weight: 3, dashArray: '6, 6' }).addTo(markersLayer);
      }

      // 3. Plot Viable Runners-up
      viable.forEach(v => {
        if (!v.latitude || !v.longitude) return;
        const vIcon = L.divIcon({
          html: `<div class="bg-sky-500 text-white p-1.5 rounded-full shadow-md flex items-center justify-center w-7 h-7"><i class="fa-solid fa-warehouse text-xs"></i></div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        });
        L.marker([v.latitude, v.longitude], { icon: vIcon })
          .addTo(markersLayer)
          .bindPopup(`<strong>Viable Alternative: ${v.name}</strong><br>Storage: ${v.cold_storage}<br>Capacity: ${v.max_pallets} pallets<br>Composite Score: ${v.composite_score}`);
      });

      // 4. Plot Eliminated Decoys
      decoys.forEach(d => {
        if (!d.latitude || !d.longitude) return;
        const dIcon = L.divIcon({
          html: `<div class="bg-rose-500 text-white p-1.5 rounded-full shadow-md flex items-center justify-center w-7 h-7 opacity-80"><i class="fa-solid fa-ban text-xs"></i></div>`,
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        });
        L.marker([d.latitude, d.longitude], { icon: dIcon })
          .addTo(markersLayer)
          .bindPopup(`<strong>❌ ELIMINATED DECOY: ${d.name}</strong><br><span class="text-rose-600 font-semibold">Reasons:</span><br>${(d.elimination_reasons || []).join('<br>')}`);
      });

      // 5. Update Decoy Defense List
      const decoyList = document.getElementById('decoys-list');
      decoyList.innerHTML = '';
      decoys.slice(0, 4).forEach(d => {
        const div = document.createElement('div');
        div.className = "p-2 rounded bg-slate-900/60 border border-rose-900/40 text-slate-300";
        div.innerHTML = `<strong class="text-rose-300">${d.name}</strong><br><span class="text-slate-400">${(d.elimination_reasons || []).join(' ')}</span>`;
        decoyList.appendChild(div);
      });

      // 6. Update Match Brief Card
      const briefContainer = document.getElementById('brief-container');
      briefContainer.classList.remove('hidden');

      document.getElementById('brief-winner-name').innerText = winner.name;
      document.getElementById('brief-winner-address').innerText = `${winner.street}, ${winner.city}, WA ${winner.zip_code}`;
      document.getElementById('brief-badge-storage').innerText = `Cold Storage: ${winner.cold_storage}`;
      document.getElementById('brief-badge-capacity').innerText = `Capacity: ${winner.max_pallets} Pallets`;
      document.getElementById('brief-badge-equity').innerText = `Equity: ${winner.tract_no_vehicle_pct}% No-Vehicle`;

      document.getElementById('brief-justification').innerText = brief.match_justification || '';
      document.getElementById('brief-operational').innerText = brief.operational_verification || '';
      document.getElementById('brief-clock').innerText = "⏱️ " + (brief.clock_action_plan || '');

      document.getElementById('brief-sms').innerText = brief.drafted_sms_outreach || '';
      document.getElementById('brief-email').innerText = brief.drafted_email_outreach || '';

      document.getElementById('raw-json').innerText = JSON.stringify(data, null, 2);

      // Pan map to fit markers
      const group = new L.featureGroup(markersLayer.getLayers());
      map.fitBounds(group.getBounds().pad(0.15));
    }

    function copyOutreach() {
      const sms = document.getElementById('brief-sms').innerText;
      navigator.clipboard.writeText(sms).then(() => alert('SMS copied to clipboard!'));
    }

    function copyJsonPayload() {
      if (!currentPayload) return;
      navigator.clipboard.writeText(JSON.stringify(currentPayload, null, 2)).then(() => alert('JSON payload copied to clipboard!'));
    }

    window.onload = () => {
      initMap();
      loadScenarios();
      runMatching(); // Run default spinach on load
    };
  </script>

</body>
</html>
"""
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8080, reload=False)
