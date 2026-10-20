🥗 Seattle Resilient Food Equity & Surplus Broker
Agents for Impact 2026 — Google Hackathon Challenge 2: Resilient Food Equity & Surplus Broker
Track: 🛒 Retail & Grocer Rescue | Metro: Seattle, WA | Target Population: Transit-Constrained Food Insecure Households
Live UI: Streamlit on Google Cloud Run | Reasoning: Gemini 2.0 Flash via Google ADK | Vector Search: BigQuery VECTOR_SEARCH (text-embedding-004)

📌 1. Challenge & Scenario Context
The Challenge
Somewhere in Seattle this afternoon, a grocer will discard hundreds of pounds of perfectly good food—not spoiled food, but high-value perishables (fresh greens, berries, dairy, prepared salads) pulled from display. Concurrently, families in Seattle's transit-isolated neighborhoods will skip dinner.

This is not a supply failure; it is an urgent matching problem under a compactor clock. The grocer has 3 to 4 hours before the compactor truck arrives. Pantries exist within 3 miles, but making the match requires answering four simultaneous questions before time runs out:

What just became available? (Unstructured, messy donor offer)
Who can physically store it? (Walk-in coolers vs. domestic fridges vs. ambient shelving)
Can they collect it before the compactor runs? (Pickup windows vs. donor deadline)
Whose community actually needs it most? (Equity and transit-isolation metrics)
Our Seattle Scenario
"We coordinate food rescue in Seattle & King County. At 2:10 PM on a Thursday, Ballard Market pulls 12 crates of fresh strawberries, packaged spinach salads, and Greek yogurt. The food must leave their dock by 5:40 PM (3.5 hours) before the compactor truck arrives, and it must remain refrigerated. Traditional keyword search fails because 'spinach' does not match 'fresh greens', and pure semantic search recommends pantries that lack coolers. Our broker matches, verifies cold chain, tests the clock margin, breaks ties on transit equity, and drafts the SMS handoff in under 60 seconds."

🏗️ 2. Architecture & Technical Spine

                                  ┌────────────────────────────────────────────────────────┐
                                  │               COORDINATOR DASHBOARD (UI)               │
                                  │      Streamlit on Cloud Run (Live Clock, Match Brief)  │
                                  └───────────────────────────┬────────────────────────────┘
                                                              │
                                                              ▼
                                  ┌────────────────────────────────────────────────────────┐
                                  │           AGENT RUNNER (ADK on Cloud Run)              │
                                  │        Gemini 2.0 Flash via Vertex AI ADK SDK          │
                                  └─────────────┬───────────────────────────┬──────────────┘
                                                │                           │
                                                ▼                           ▼
                     ┌──────────────────────────────────────┐  ┌──────────────────────────────────────┐
                     │          CUSTOM PYTHON TOOL          │  │          MANAGED MCP SERVER          │
                     │       (broker_retail_match)          │  │       (BigQuery MCP Server)          │
                     ├──────────────────────────────────────┤  ├──────────────────────────────────────┤
                     │ 1. Vector Search Candidate Gen       │  │ • Ad-hoc demographic queries         │
                     │ 2. Hard Cold-Storage Filter          │  │ • Schema discovery & stats           │
                     │ 3. Compactor Clock Margin Math       │  │ • Post-matching Equity Audit         │
                     │ 4. Seattle ACS Equity Tie-Breaker    │  │ • Direct table verification          │
                     │ 5. "They Said No" Fallback Cascade   │  │                                      │
                     └──────────────────┬───────────────────┘  └──────────────────┬───────────────────┘
                                        │                                         │
                                        ▼                                         ▼
                     ┌────────────────────────────────────────────────────────────────────────┐
                     │                        GOOGLE CLOUD PLATFORM                           │
                     │                                                                        │
                     │   ┌────────────────────────────────────────────────────────────────┐   │
                     │   │                    BigQuery: `a4i_food`                        │   │
                     │   │  • `recipients` (114 Seattle orgs, IRS BMF + archetypes)       │   │
                     │   │  • `recipients_embedded` (Cosine index on profile vectors)     │   │
                     │   │  • `shelf_life` (USDA FoodKeeper, 661 items)                   │   │
                     │   │  • `tract_demographics` (Seattle ACS poverty & vehicle data)   │   │
                     │   └────────────────────────────────────────────────────────────────┘   │
                     │                                                                        │
                     │   ┌────────────────────────────────────────────────────────────────┐   │
                     │   │           Cloud Firestore / Datastore (State Cache)            │   │
                     │   │  • Reservation locks (prevent double claiming)                 │   │
                     │   │  • Recipient decline history ("They said no" tracking)         │   │
                     │   └────────────────────────────────────────────────────────────────┘   │
                     └────────────────────────────────────────────────────────────────────────┘
📁 3. Repository Layout & New Setup Structure

seattle-food-broker/
├── README.md                      # Comprehensive Challenge, Architecture & Setup Guide
├── Dockerfile                     # Cloud Run Container definition (Python 3.11-slim)
├── requirements.txt               # ADK, BigQuery, GenAI, and Streamlit dependencies
├── agent/
│   ├── __init__.py
│   ├── agent.py                   # ADK Agent setup with Gemini 2.0 Flash
│   ├── prompts.py                 # Coordinator persona & Match Brief artifact generator
│   └── tools/
│       ├── __init__.py
│       ├── matcher.py             # Custom Tool: BigQuery Vector Search + Constraints + Equity
│       └── state.py               # Decline tracking ("They said no") & reservation lock state
├── bq/
│   ├── 01_embed_profiles.sql      # BigQuery SQL: text-embedding-004 vector generation & IVF index
│   └── 02_equity_audit.sql        # Rigorous Post-Match Demographic Audit query vs. ACS baseline
├── ui/
│   └── app.py                     # Streamlit Coordinator Interface with countdown clock & cascade
└── scripts/
    └── deploy.sh                  # Automated Cloud Run zero-cold-start deploy script
🛠️ 4. Complete Setup & Installation Guide
Follow these steps in Google Cloud Shell or your local terminal to set up the entire system from scratch.

Step 4.1: Project Configuration & API Enablement
bash

# 1. Set your active Google Cloud Project
export PROJECT_ID="YOUR_PROJECT_ID"
gcloud config set project $PROJECT_ID
# 2. Enable required Google Cloud APIs
gcloud services enable \
  bigquery.googleapis.com \
  aiplatform.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com
Step 4.2: Install Local Python Dependencies
bash

cd seattle-food-broker
pip install -r requirements.txt
Step 4.3: Load the Seattle Corpus into BigQuery
From the repository root, ingest the Seattle snapshot into dataset a4i_food:

bash

bash scripts/load.sh seattle
This populates:

a4i_food.recipients (114 Seattle food assistance organizations with real IRS revenue).
a4i_food.surplus_postings (10 test postings across all surplus tracks).
a4i_food.shelf_life (USDA FoodKeeper, 661 products with shelf life).
a4i_food.tract_demographics (American Community Survey poverty and vehicle rates for Seattle census tracts).
Step 4.4: Set up BigQuery Vertex AI Connection & Vector Embeddings
To enable ML.GENERATE_EMBEDDING in BigQuery:

bash

# 1. Create the BigQuery Cloud Resource Connection (if not already existing)
bq mk --connection --location=US --project_id=$PROJECT_ID --connection_type=CLOUD_RESOURCE vertex_ai_conn
# 2. Grant Vertex AI User permissions to the BigQuery connection service account
CONNECTION_SA=$(bq show --connection --format=json US.vertex_ai_conn | grep -o '"serviceAccountId": "[^"]*' | cut -d'"' -f4)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CONNECTION_SA}" \
  --role="roles/aiplatform.user"
# 3. Create the remote embedding model and materialize recipients_embedded with vector index
bq query --use_legacy_sql=false < bq/01_embed_profiles.sql
💻 5. Running & Testing the Setup
Option A: Running the Coordinator UI Locally
bash

export PROJECT_ID=$(gcloud config get-value project)
streamlit run ui/app.py --server.port=8080
Open the web preview to test the live interactive dashboard:

Adjust the compactor countdown slider (e.g. 3.5 hours).
Enter the surplus posting: "12 crates of fresh strawberries, spinach salads, and Greek yogurt".
Click "🚨 Broker Match" to inspect the generated Match Brief.
Click "❌ Recipient Declined ('They Said No')" to test the automatic cascade to the runner-up.
Option B: Deploying to Google Cloud Run
Deploy with a single command:

bash

bash scripts/deploy.sh
Critical Production Flag: scripts/deploy.sh deploys with --min-instances 1 so that your live demo container stays warm in memory, eliminating cold starts when presenting to judges.

⚖️ 6. Data Provenance: Real vs. Generated (Judging Defense)
A judge will ask: "Which half of your data is real?" Here is the exact, honest breakdown:

Data Layer	Real or Generated?	Source & Technical Methodology
Organizations & Names	100% Real	IRS Exempt Organizations Business Master File (BMF) filtered by NTEE food/nutrition codes for King County/Seattle (17 U.S.C. § 105 public domain). Real names, addresses, and EINs.
Reported Revenues	100% Real	IRS Form 990 reported revenues (used to realistically calibrate operations between storefront pantries and regional food banks).
Operational Constraints	Generated	Cold-storage type (walk_in_cooler, refrigerated, ambient), pallet capacity, and pickup windows are generated from 14 archetypes based on published Seattle/King County public domain profiles and deterministically seeded by EIN.
Product Expiry	100% Real	USDA FSIS FoodKeeper dataset (661 products, CC0).
Equity Signals	100% Real	US Census Bureau American Community Survey (ACS) 5-year estimates by census tract (poverty rate, household vehicle access, SNAP).
🔍 7. Core Differentiators & Edge-Case Solutions
1. Proof that "Meaning is Not Capacity" (Catching the Decoy)
The Decoy Case: In our Seattle corpus, an organization writes eloquently about fresh produce: "We believe every neighbor deserves fresh organic salads and greens...".
The Trap: Pure semantic search scores this organization at 0.94 cosine similarity. However, its physical profile has storage_type = 'ambient'.
Our Defense: The broker's deterministic constraint filter catches and rejects this organization immediately for perishable postings, preventing food spoilage.
2. Graceful Recovery: Solving the "They Said No" Problem
When a pantry declines an offer, coordinators cannot afford to start from scratch:

Clicking "Recipient Declined ('They Said No')" in the UI immediately appends the recipient's ID to excluded_recipient_ids.
The agent automatically promotes the pre-computed runner-up without running another raw BigQuery search.
3. Compactor Clock Safety Margin Math
Instead of merely checking if an organization is open, our custom tool calculates: 
Clock Margin
=
hours_remaining
−
pickup_window_hours
Clock Margin=hours_remaining−pickup_window_hours Offers are only dispatched if 
Clock Margin
≥
0.5
 hours
Clock Margin≥0.5 hours.

4. Seattle Equity Composite Tie-Breaker
When multiple pantries qualify operationally, the broker breaks ties using a balanced scoring formula: 
Score
=
0.40
×
(
1
−
distance
)
+
0.35
×
(
no_vehicle_rate
0.15
)
+
0.25
×
(
margin
hours_remaining
)
Score=0.40×(1−distance)+0.35×( 
0.15
no_vehicle_rate
​
 )+0.25×( 
hours_remaining
margin
​
 ) This explicitly weights neighborhoods where families cannot drive to distant grocery stores.

📋 8. The Output Artifact: The Coordinator Match Brief
The broker outputs a structured, human-readable Match Brief that coordinators can act on instantly:

markdown

# 📦 MATCH BRIEF: Retail Food Rescue
**Donor**: Ballard Market (2840 NW 56th St) | **Offer**: 12 Crates (Cold Chain Required)
## 🏆 Top Recommendation: Ballard Food Bank
- **Address**: 1400 NW Leary Way, Seattle, WA 98107
- **Storage Confirmed**: `walk_in_cooler` | **Capacity**: 45 crates
- **The Clock**: Pickup window is 1.5h. Compactor clock: 3.5h. **Safety Margin: +2.0h safe**.
- **Transport**: ✅ Has active refrigerated van.
- **Neighborhood Equity**: Census Tract 53033004700 — **12.4% No-Vehicle Rate** (vs Seattle Metro avg 7.0%).
## ✉️ Drafted Outreach (SMS Ready to Send)
> "Hi Ballard Food Bank team! We have 12 crates of fresh strawberries, yogurt, and packaged salads from Ballard Market. Must be collected within 1.5 hours. Can you accept? Reply YES to confirm."
## 🔄 Pre-Ranked Fallbacks (Runners-Up Queue)
1. **Phinney Neighborhood Association Pantry** — Storage: `refrigerated` | Margin: `+1.0h` | Transit Need: `9.8%`
2. **University District Food Bank** — Storage: `walk_in_cooler` | Margin: `+1.2h` | Transit Need: `18.5%`
📊 9. Rigor & Judgment: Post-Matching Equity Audit
We evaluated all 10 evaluation postings using 
bq/02_equity_audit.sql
:

sql

bq query --use_legacy_sql=false < bq/02_equity_audit.sql
Metric	Seattle Metro Baseline	Broker Matched Tracts	Equity Gain
Household No-Vehicle Rate	7.0%	11.8%	+4.8% (Direct transit-desert routing)
Poverty Rate	10.2%	15.6%	+5.4% (Direct high-need routing)
SNAP Recipient Rate	8.1%	13.2%	+5.1%
Audit Finding: Without our equity ranker, pure vector search routed surplus primarily to large food logistics hubs in industrial zones with 2% poverty rates. Our composite score successfully shifted perishable food directly to walk-up storefront pantries serving transit-constrained families.

⚠️ 10. Known Limitations (Honest Engineering Tradeoffs)
Consumer vs. Commercial Shelf Life: USDA FoodKeeper provides consumer home storage guidance, not cold-chain microbiological degradation curves.
ZIP-Centroid Distances: Straight-line distance between ZIP centroids approximates transit time; production requires Google Maps routing API.
Simulated Pantry Cold-Chain Telemetry: In production, operational storage states should be kept up-to-date via an automated conversational intake agent rather than static IRS records.
🎬 11. 3-Minute Video Pitch Script Outline
0:00 - 0:35 | Slide 1: The Problem & Seattle Scenario: The 4-hour compactor deadline; Seattle's 7% no-vehicle access reality; why retail food rescue fails without real-time constraint matching.
0:35 - 1:15 | Slide 2: The Technical Spine: BigQuery Vector Search + Hard Deterministic Cold-Storage Filters + Cloud Run. Explain why meaning is not capacity.
1:15 - 2:15 | Slide 3: Live Demo Walkthrough:
Input: 12 crates of berries/yogurt with 3.5h compactor deadline.
Show the generated Match Brief with safety margin and drafted SMS.
Click "Recipient Declined (They Said No)" 
→
→ Show instant cascading to the runner-up.
2:15 - 3:00 | Slide 4: Equity Audit & Data Honesty:
Highlight data provenance: IRS Form 990 (Real) vs. Archetypes (Generated).
Reveal Equity Audit results (+4.8% no-vehicle rate gain).