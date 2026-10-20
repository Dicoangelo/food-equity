# Product Requirements Document (PRD)
## Resilient Food Equity & Surplus Broker (Seattle Metro)
**Competition:** Agents for Impact 2026 — Challenge 2: Food Equity & Surplus Broker  
**Team Lane:** Full-Stack Agent & Matching Architecture  
**Target Metro:** Seattle, Washington  
**Primary Track:** Retail & Grocer Food Rescue (High Perishability / 4–8 Hour Clock)  
**Version:** 1.0.0  
**Status:** In Active Development / Live Prototype Verified  

---

## 1. Executive Summary & Problem Statement

### 1.1 The Problem
In King County and metropolitan Seattle, grocery stores, food hubs, and distribution centers discard tons of high-value edible food daily—primarily leafy produce, dairy, prepared meals, and baked goods. These items are strictly perishable, operating on a **4-to-8 hour expiration clock** before store managers are forced to divert them to landfill compactors.

Simultaneously, local community food pantries, mutual aid tables, and soup kitchens face chronic shortages of fresh, nutritious items. Current food rescue processes rely on manual, ad-hoc phone trees and text chains that suffer from three fatal flaws:
1. **The Clock Failure:** Donated items sit on loading docks until they spoil because volunteer drivers cannot be mobilized within the remaining shelf life.
2. **The "Decoy Trap" (Operational Mismatch):** Naive matching systems connect grocers with pantries based on mission keywords (e.g., organizations that express an interest in fresh vegetables), without verifying that the organization lacks physical cold storage. Consequently, delicate greens spoil on un-refrigerated sidewalks.
3. **The Equity Blindspot:** Seattle has a 93% metro-wide household vehicle access rate, but the remaining 7% of transit-dependent, food-insecure households are intensely clustered in specific census tracts. Standard food distribution defaults to suburban mega-pantries with large parking lots, bypassing high-need urban food deserts.

### 1.2 The Solution
The **Seattle Resilient Food Equity & Surplus Broker** is an autonomous AI agentic system powered by **Google Cloud Vertex AI (Gemini 2.5 / 2.0)**, **BigQuery Vector Search (`gemini-embedding-2`)**, and the **Google Model Context Protocol (MCP) Toolbox for Databases**. 

The system ingests unstructured surplus postings from retail donors, parses physical constraints, runs vector search across 119 verified Seattle recipient profiles, eliminates operational decoys through hard physical filters, weights candidates by census tract socioeconomic indicators, and generates an actionable **Match Brief** with pre-drafted coordinator communications in under two seconds.

---

## 2. Target Personas & User Stories

| Persona | Role | Key Pain Point | Required System Feature |
|---|---|---|---|
| **Retail Produce Manager** (e.g., Pike Place / Safeway) | Has 10 crates of spinach that must clear the dock in 6 hours. | No time to fill out complex forms or wait on hold with dispatchers. | Single-sentence natural language intake via SMS, email, or web form. |
| **Food Rescue Coordinator** | Dispatches volunteer drivers and approves donor allocations. | Overwhelmed by phone calls; terrified of sending perishable loads to un-refrigerated sites. | Instant **Match Brief** verifying cold chain, capacity, and providing 1-click drafted SMS/email. |
| **Pantry Logistics Director** | Manages walk-in coolers and volunteer loading docks. | Unannounced bulk drop-offs that exceed cooler capacity or arrive outside operating hours. | Pre-validated pallet counts, operating windows, and explicit "Accept / Decline" claim loops. |
| **Volunteer Driver** | Picks up surplus from grocer dock and transports to recipient. | Needs clear addresses, dock instructions, and route navigation. | Turn-by-turn routing with verified recipient operating hours. |

---

## 3. System Architecture

The architecture maps directly to the team's system blueprint:

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 CLIENT & INTERACTION LAYER                                  │
│  ┌───────────────────────────────────────────────────────┐  ┌────────────────────────────┐  │
│  │   Coordinator Web UI & Map (FastAPI / Leaflet / Maps) │  │  Driver / Coordinator      │  │
│  │   - Interactive Seattle map with live color pins      │  │  Dispatch (SMS & Email)    │  │
│  │   - Scenario switcher & Team API Handoff drawer       │  │                            │  │
│  └───────────────────────────────────────────────────────┘  └────────────────────────────┘  │
└──────────────────────────────────────────────┬──────────────────────────────────────────────┘
                                               │ HTTP / REST API (`POST /api/match`)
┌──────────────────────────────────────────────▼──────────────────────────────────────────────┐
│                    AGENT REASONING & TOOL ORCHESTRATION LAYER (ADK)                         │
│  ┌─────────────────────────────┐  ┌──────────────────────────────────────────────────────┐  │
│  │ Intake Agent (Add-On)       │  │ Posting Parser & Normalizer                          │  │
│  │ Conversational pantry       │  │ Extracts storage, volume, deadline, perishability    │  │
│  │ onboarding into BigQuery    │  └──────────────────────────┬───────────────────────────┘  │
│  └─────────────────────────────┘                             │                              │
│  ┌────────────────────────────────────────────────────────┐  │                              │
│  │ ADK Agent Runner (Gemini 2.5 Flash via Vertex AI)      │◄─┘                              │
│  │ Synthesizes Match Briefs, justifications, & outreach   │                                 │
│  └────────────────────────────┬───────────────────────────┘                                 │
│                               │ Calls Tool                                                  │
│  ┌────────────────────────────▼───────────────────────────┐                                 │
│  │ Custom Python Tool: `broker_retail_match()`             │                                 │
│  │ Evaluates constraints, rejects decoys, scores equity   │                                 │
│  └────────────────────────────┬───────────────────────────┘                                 │
└───────────────────────────────┼─────────────────────────────────────────────────────────────┘
                                │ Parameterized Tool Invocation
┌───────────────────────────────▼─────────────────────────────────────────────────────────────┐
│                       MODEL CONTEXT PROTOCOL (MCP) TOOLBOX LAYER                            │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Google MCP Toolbox for Databases (`toolbox-server` v1.14.0 on Cloud Run)              │  │
│  │ - `vector_search_recipients`: Brute-force cosine distance search                      │  │
│  │ - `get_tract_equity_data`: Socioeconomic demography lookups                           │  │
│  │ - `get_food_shelf_life`: USDA FoodKeeper shelf-life queries                           │  │
│  └───────────────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                       │ SQL Queries & Embeddings
┌──────────────────────────────────────▼──────────────────────────────────────────────────────┐
│                            GOOGLE CLOUD DATA & STATE LAYER                                  │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ BigQuery Dataset: `a4i_food` (Location: US)                                            │  │
│  │ - `recipient_embeddings`: 119 profiles with 3072-dim vectors (`gemini-embedding-2`)    │  │
│  │   and nearest-neighbor spatial joins to census tracts via `ST_DISTANCE`                │  │
│  │ - `recipients`: 123 Seattle profiles (IRS BMF, storage, capacity, operating hours)     │  │
│  │ - `tract_demographics`: 389 ACS tracts (poverty_rate, no_vehicle_rate, income)        │  │
│  │ - `shelf_life`: 661 USDA FoodKeeper reference records                                 │  │
│  │ - `surplus_postings`: Benchmark donor test scenarios                                  │  │
│  └───────────────────────────────────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Cloud Firestore (Datastore Mode): State Tracking                                      │  │
│  │ - Pallet reservation locks                                                            │  │
│  │ - "They said no" recipient decline history (automatic fallback routing)               │  │
│  └───────────────────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Functional Specifications

### 4.1 Natural Language Posting Normalizer
- **Input:** Unstructured donor string (e.g., *"Got 10 crates of fresh spinach harvested yesterday, needs to leave our cold hold within 6 hours."*).
- **Extraction Schema:**
  - `item_name`: Food commodity.
  - `required_storage`: `cold` | `ambient` | `frozen`.
  - `pallet_count`: Numeric volume in pallets (converts crates/cases using standard tare metrics: ~10 crates = 0.5–1.0 pallet).
  - `hours_remaining`: Real-time window before destruction.
- **Reference Validation:** Cross-references USDA FoodKeeper table (`shelf_life`) to ensure grocer's claimed shelf life aligns with official food safety guidelines.

### 4.2 Candidate Generation (BigQuery Vector Search)
- **Vector Model:** `gemini-embedding-2` producing 3072-dimensional embeddings.
- **Search Execution:** In-database `VECTOR_SEARCH` with `options => '{"use_brute_force":true}'`.
- **Top-K Retrieval:** Retrieves top 12–15 nearest semantic matches based on mission statement, services, and recipient profile text.

### 4.3 Hard Constraint Filtering (Decoy Elimination)
Vector search acts strictly as a **candidate generator**. The broker executes deterministic physical constraint checks to eliminate infeasible matches:
1. **Cold Chain Constraint:** If `required_storage == 'cold'` and recipient `cold_storage IN ('none', '', 'dry')`, candidate is **ELIMINATED**.
2. **Freezer Constraint:** If `required_storage == 'frozen'` and recipient `frozen_storage == FALSE`, candidate is **ELIMINATED**.
3. **Volume Capacity:** If `pallet_count > candidate.max_pallets`, candidate is **ELIMINATED**.
4. **Clock Feasibility:** If `candidate.pickup_window_hours > (hours_remaining * 1.5)` AND `refrigerated_transport == FALSE`, candidate is **ELIMINATED**.

*Audit Trail Requirement:* Every eliminated candidate retains its specific failure reasons (e.g., *"Eliminated: requires cold storage, but organization profile has 'none'"*).

### 4.4 Equity & Logistics Composite Scoring
All viable candidates passing physical constraints are ranked using a weighted multi-factor scoring function:

$$\text{Score} = (0.40 \times \text{SemanticSimilarity}) + (0.35 \times \text{EquityScore}) + (0.25 \times \text{LogisticsScore})$$

Where:
- $\text{SemanticSimilarity} = 1.0 - \text{CosineDistance}$
- $\text{EquityScore} = (0.50 \times \text{TractPovertyRate}) + (0.50 \times \text{TractNoVehicleRate})$
- $\text{LogisticsScore} = 0.50 + (0.30 \text{ if ReeferTruck}) + (0.20 \text{ if LoadingDock})$

### 4.5 Gemini Match Brief Synthesis
The winning candidate, runners-up, and eliminated decoys are passed into Vertex AI `gemini-2.5-flash` to generate the formal **Match Brief**:
1. **Match Justification:** Explicitly defends the winner and contrasts against the top eliminated decoys.
2. **Operational Certification:** Confirms physical walk-in/commercial refrigeration and pallet capacity.
3. **Clock Action Plan:** Certifies pickup window against the donor deadline.
4. **Equity Rationale:** Highlights neighborhood need in the recipient's census tract.
5. **Urgent SMS Outreach:** Pre-drafted text message ($\le 160$ characters) for immediate coordinator dispatch.
6. **Email Outreach:** Pre-drafted formal message detailing pallets, timing, and loading dock instructions.

### 4.6 State Tracking & Decline Fallback ("They Said No")
- In production, coordinators or recipients can decline an offer (e.g., unexpected cooler maintenance).
- The state manager records the decline event for the posting in Firestore/memory.
- When an offer is declined, the system instantly recalculates and re-dispatches to **Runner-Up #2**, preventing deadlock.

---

## 5. API & Integration Contracts

### 5.1 Main Matching Endpoint
- **URL:** `POST /api/match`
- **Request Payload:**
```json
{
  "posting_text": "10 crates of spinach, harvested yesterday, needs cold storage",
  "required_storage": "cold",
  "pallet_count": 1.0,
  "hours_remaining": 6.0,
  "donor_name": "Pike Place Grocer Dock",
  "donor_lat": 47.6097,
  "donor_lon": -122.3422
}
```

- **Response Payload:**
```json
{
  "status": "success",
  "donor": {
    "name": "Pike Place Grocer Dock",
    "latitude": 47.6097,
    "longitude": -122.3422,
    "posting_text": "10 crates of spinach, harvested yesterday, needs cold storage",
    "required_storage": "cold",
    "pallet_count": 1.0,
    "hours_remaining": 6.0
  },
  "winner": {
    "recipient_id": 114,
    "name": "Maple Street Produce Alliance",
    "street": "",
    "city": "Seattle",
    "zip_code": "",
    "cold_storage": "walk-in",
    "max_pallets": 10,
    "pickup_window_hours": 12,
    "weekly_households": 600,
    "tract_id": "53033011101",
    "tract_poverty_pct": 10.2,
    "tract_no_vehicle_pct": 14.0,
    "composite_score": 0.3982,
    "latitude": 47.542,
    "longitude": -122.277
  },
  "runners_up": [
    {
      "name": "Help Us Serve Others A Nonprofit Corporation",
      "cold_storage": "commercial",
      "max_pallets": 1,
      "composite_score": 0.3621
    }
  ],
  "eliminated_decoys": [
    {
      "name": "Greenline Community Pantry",
      "elimination_reasons": [
        "Physical failure: requires cold storage, but organization has 'none' refrigeration."
      ]
    }
  ],
  "match_brief": {
    "match_justification": "Maple Street Produce Alliance was selected due to their robust walk-in cold storage and 10-pallet capacity, contrasting with Greenline Community Pantry which had zero refrigeration.",
    "operational_verification": "Verified 1.0 pallet volume against 10-pallet max capacity and walk-in cooler.",
    "clock_action_plan": "Pickup feasible within 6.0h deadline via dedicated refrigerated transport.",
    "equity_rationale": "Located in tract 53033011101 where 14.0% of families lack vehicle access.",
    "drafted_sms_outreach": "Urgent: Fresh spinach match! 10 crates (1 pallet) from Pike Place Dock. Needs pickup within 6 hrs. Reply YES to confirm. - Seattle Food Broker",
    "drafted_email_outreach": "Subject: Urgent Surplus Allocation: 10 crates spinach..."
  },
  "map_markers": {
    "donor": { "lat": 47.6097, "lon": -122.3422, "type": "donor" },
    "winner": { "lat": 47.542, "lon": -122.277, "type": "winner" },
    "viable": [ ... ],
    "decoys": [ ... ]
  }
}
```

---

## 6. Verification & Test Plan

| Test ID | Scenario | Input Parameters | Expected Result | Pass Criteria |
|---|---|---|---|---|
| **TEST-01** | **Spinach Decoy Trap** | 10 crates spinach, cold, 1 pallet, 6h clock | Rejects Greenline Community Pantry (cold_storage='none'). Selects Maple Street Produce Alliance. | Zero refrigeration pantries excluded from winner and runners-up. |
| **TEST-02** | **Dairy Volume/Clock** | 3 pallets whole milk, cold, 3 pallets, 4h clock | Rejects pantries with max_pallets < 3 or pickup_window > 4h without reefer transport. | Winner has $\ge 3$ pallet capacity and compatible clock. |
| **TEST-03** | **Bakery Ambient Rescue** | 2 pallets bread, ambient, 2 pallets, 24h clock | Pantries with `cold_storage = 'none'` ARE permitted since item is dry/ambient. | Ambient pantries eligible; cold storage not enforced as a blocker. |
| **TEST-04** | **Sub-second Vector Search** | Direct `VECTOR_SEARCH` query on BigQuery | Response returned in $< 1.5$ seconds. | Brute force vector search executes without timeout. |
| **TEST-05** | **Outreach Generation** | SMS character length limit check | SMS string $\le 160$ characters. | SMS contains donor name, volume, clock, and confirmation prompt. |

---

## 7. Cloud Deployment Roadmap

1. **Local Container Verification:** FastAPI + Uvicorn server operational at `http://0.0.0.0:8080`.
2. **MCP Server Integration:** Google MCP Toolbox for Databases running via `toolbox-server` with `tools.yaml`.
3. **Artifact Preparation:** Dockerfile configured for Cloud Run deployment with Google Application Default Credentials.
4. **Cloud Run Deployment:** Deploy with `--min-instances=1` to eliminate cold starts during live stage presentation.
