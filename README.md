# Seattle Resilient Food Equity & Surplus Broker

**Agents for Impact 2026 · Challenge 02 · Retail & grocer rescue · Seattle metro**

Turn an urgent surplus offer into a proposed **Match Brief**: an organization and why, what it can take, the pickup deadline and margin, neighborhood context, outreach drafts and up to two eligible alternatives. Recipient acceptance remains a human confirmation.

**[Open the live broker](https://seattle-food-broker-6ukbp746xa-ue.a.run.app)** · [Three-minute team pre-read](docs/PRE-READ.md) · [Implementation handoff](docs/IMPLEMENTATION-HANDOFF.md) · [Control board](HACKATHON-BOARD.md) · [Architecture gallery](docs/architecture/README.md)

## Architecture at a glance

[![BeforeTheCrush architecture: a reviewed surplus offer moves through candidate retrieval and feasibility checks to a coordinator handoff.](docs/architecture/web/01-pitch/01-pitch-overview-1920.webp)](docs/architecture/README.md)

**[Browse all six diagrams →](docs/architecture/README.md)** · [Pitch](docs/architecture/README.md#pitch-pack) · [Technical](docs/architecture/README.md#technical-pack) · [Research](docs/architecture/README.md#research-pack) · [Download web pack](docs/architecture/downloads/BeforeTheCrush-web.zip)

Three packs explain the coordinator workflow, runtime and MCP boundaries, and decision method with data provenance. The diagrams describe [source snapshot `fcb4f76`](https://github.com/niatalwalkar/food-equity/tree/fcb4f76678713c55af3873acfc21e2871f44bcc7); they do not establish the current deployment or recipient availability. The gallery includes compressed WebP/JPEG assets, captions and source references.

## Team access

Use the **direct live link above** for the team demo. This Cloud Run service is public for the hackathon demo. The service-only access change succeeded, and unauthenticated HTTP checks returned **200** for `/`, `/api/health` and `/api/city`. The API uses the existing runtime identity for cloud access.

For an optional Cloud Shell preview, an already authorized **student Cloud Shell** can also use the proxy:

```bash
gcloud run services proxy seattle-food-broker \
  --project=qwiklabs-gcp-04-b992b9561e32 --region=us-east1 --port=8080
```

Keep that terminal running and open **Web Preview → Preview on port 8080**. On a local machine with existing authorized access, the same proxy serves `http://127.0.0.1:8080`. Direct team-demo access does not require the proxy. Credentials and tokens do not belong in this repository, screenshots or shared notes.

**Verified release, 20 October 2026:** source `d8f9c2159efa60f4e7b07324534fc509dd100d17`, revision `seattle-food-broker-00013-fum`, promoted to **100% traffic**. Before promotion, the exact candidate passed all five core checks: health, city data, FoodKeeper, real bakery ADK/MCP matching with one backup, and cold-capacity refusal. City data returned 389 tract points and 119 profiles. Receipts: workspace `research/LIVE-CORE-CANDIDATE-d8f9c21.json` and `research/LIVE-PROMOTION-d8f9c21.json`. Team `main` is `b6586cd`; [PR #10](https://github.com/niatalwalkar/food-equity/pull/10) merged at `18:00:12Z` on 20 October, following PR #9. Ownership transfer is parked and the fork is unchanged.

**Live intake and recipient explorer:** the unchanged typed Live smoke passed on the combined candidate: a two-pallet draft, exact deadline preserved, completed response, 228,482 generated-audio bytes and four transcript events; no match was automatically requested. After promotion, the same smoke also passed on the **public URL** (309,120 audio bytes, six transcript events, deadline preserved, no match request; `research/LIVE-INTAKE-PUBLIC-d8f9c21.json`). Sparse-patch predecessor `c60faa7` passed twice beforehand. Browser checks verified reviewed Apply followed by a separate match, the linked winner profile and eligible filter, a 390px phone modal and no overflow at 320px. Combined validation: **245 Python tests and both Node suites passed**. Physical microphone capture/playback remains unverified; typed intake and the standard form remain available.

**Team portal:** PR #9 is merged, but GitHub Pages is disabled (`has_pages=false`) and [workflow run 39467685689 failed](https://github.com/niatalwalkar/food-equity/actions/runs/39467685689). A repository admin must select **Settings → Pages → GitHub Actions**. Use the working Cloud Run link above; no published Pages URL is claimed.

## Challenge and demo

A Seattle grocer has mixed surplus and only hours to move it. A semantically relevant pantry may lack refrigeration, sufficient capacity or time to collect. The broker retrieves plausible recipients, then checks physical constraints before proposing a handoff.

1. Choose the bakery, urgent chilled or capacity-stress preset. Review quantity, storage, actual pickup origin and donor deadline.
2. Run matching. Inspect the proposal, honest no-match or unresolved facts; inspect rejected candidates and clock margins.
3. Search/filter the linked recipient list and map, open the winner's profile and compare eligible backups. Inspect storage, capacity, provenance, deadline and offer-fit components; the score is specific to this offer, not a charity rating.
4. Copy the Match Brief or outreach draft and confirm operations with the recipient. **The app sends nothing, reserves nothing and dispatches nothing.**

The bakery example uses source posting 3: “roughly 200 loaves of day-old bread and pastries from three store locations,” with an explicit **hypothetical one-pallet** capacity test and a 24-hour donor window. It does not convert loaves or crates into pallets. A chilled offer illustrates refrigeration/clock gates; a full-lot stress offer illustrates capacity rejection. Split-load optimization is not implemented.

## What runs

```text
Browser form → FastAPI → ADK Runner / Gemini 2.5 Flash on Vertex
  → request-bound broker tool → official MCP Toolbox on private loopback
  → BigQuery AI.EMBED + VECTOR_SEARCH → deterministic feasibility/ranking
  → final deadline recheck → factual Match Brief + bounded outreach choices
```

| Layer | Implementation |
|---|---|
| Web/UI | FastAPI with a same-origin interface; linked Leaflet/OpenStreetMap recipient explorer and phone profile sheet |
| Container | Python 3.12; official MCP Toolbox 1.14.0; supervised API and Toolbox processes |
| Agent | `google-adk==2.12.0`, `google-genai==2.31.0`; broker `gemini-2.5-flash` in `us-east1` |
| Retrieval | Team-operated Google MCP Toolbox; parameterized read-only `vector_search_recipients`, top 12, COSINE |
| Embeddings | `gemini-embedding-2`; canonical `a4i_food.recipient_embeddings`, 3,072 dimensions |
| Identity | Application Default Credentials (ADC); existing Cloud Run runtime service account; no app API key |
| Project/data | `qwiklabs-gcp-04-b992b9561e32`; Cloud Run `us-east1`; BigQuery dataset `a4i_food` in `US` |

This uses Google's maintained **MCP Toolbox operated by our team**, rather than claiming the separately hosted Google-managed BigQuery MCP service. Runtime dependency pins are in [requirements.txt](requirements.txt).

Storage, full-lot capacity, pickup timing, service days and bounded explicit acceptance rules are hard gates. Unknown critical facts remain pending. Vehicle refrigeration alone does not prove receiving-site cold storage. The default 15-minute buffer is a coordination policy, not a food-safety guarantee.

Among eligible candidates, ranking is `0.40 × semantic + 0.35 × neighborhood proxy + 0.25 × logistics`. Semantic is `max(0, 1 − cosine distance)`; neighborhood proxy averages poverty and no-vehicle rates; logistics uses transport/dock profile features. Missing neighborhood inputs contribute no score and remain disclosed. These design weights do not establish charity quality, fairness or delivered impact.

The absolute donor deadline persists across unchanged retries. Retrieval and model time consume it, and the broker checks it again before returning. Gemini chooses bounded outreach tone/question ordering; factual recipients, quantities and deadlines come from validated results.

## Data and evidence

| Data | What it establishes—and what it does not |
|---|---|
| Recipient corpus | 119 profiles: 110 `generated`, 9 `planted` in the verified city response. IRS-derived identities support the base corpus; operations are generated, and planted organizations may be invented. No profile has verified live operational availability. |
| `tract_demographics` | ACS-derived aggregate poverty, no-vehicle and assistance indicators. Map points are tract centroids; recipient points are approximate ZIP centroids. These are neighborhood proxies, not household demand or proven service catchments. |
| `surplus_postings` | Ten starter offers used by the replay audit; capacity assumptions are explicit in [audit-manifest.json](scripts/audit-manifest.json). |
| FoodKeeper | Pinned [661-product consumer storage/quality reference](data/README.md). Literal product options retain source fields and unresolved applicability. The lookup does not determine remaining freshness, certify commercial cold-chain safety or change the donor deadline. |
| Map | Illustrative connectors and approximate capability points. Google Directions opens separately; the app does not return verified road routes or driving times. |

The earlier `e1fbe03` replay made ten actual MCP retrievals with zero retrieval/evaluation errors: two proposals and eight no-matches against a 389-tract supplied baseline. It predates later acceptance guards. Its proposed-recipient neighborhood means were below the equal-tract baseline: **do not claim measured equity uplift**. Audit denominator and source-clock caveats are in the [handoff](docs/IMPLEMENTATION-HANDOFF.md#release-and-evidence-status).

In particular, sealed-formula posting 8 has a historical one-hour clock derived from an after-opening reference despite a 168-hour donor window. Its replay result cannot establish sealed-formula expiry or feasibility. Preserve the receipt and review the source mapping with the team before any corrected replay or data change.

## Repository map

```text
agent/api.py                 API and coordinator UI
agent/broker.py              ADK orchestration, MCP retrieval, trace, factual brief
agent/matcher.py             Pure feasibility, acceptance, clock and ranking logic
agent/tools.yaml             Read-only BigQuery MCP tool configuration
agent/city.py                Bounded map-data reads and cache
agent/foodkeeper.py          Advisory reference lookup
agent/live_intake.py         Optional Gemini 3.8 Live draft adapter with validated sparse edits
agent/ui/                    Linked recipient explorer JavaScript and CSS
data/                       FoodKeeper and starter profile components
notebooks/                  Original exploration and snapshot notebooks
bq/                         Legacy embedding proposal and single-profile diagnostic
scripts/serve.py             Toolbox/API process supervisor
scripts/deploy_cloud_run.sh  Existing-service deployment with account/project guards
scripts/deploy.sh            Short wrapper for the same deployment path
scripts/audit_postings.py    Ten-posting MCP replay with explicit assumptions
scripts/run_audit_job.py     Bounded audit-job supervisor
scripts/verify_live_release.py Authenticated, sanitized post-release smoke verifier
tests/                      Python and JavaScript contract/behavior tests
docs/                       Pre-read, handoff and clearly labeled future designs
Dockerfile                  Production image; root requirements.txt supplies dependencies
```

## Develop and verify

From the repository root, use Python 3.12 and an existing authorized development environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node tests/test_ui_contract.js
node tests/test_recipient_explorer.js
```

These tests use mocks/scripted model boundaries and do not establish cloud access. For the full local stack, ADC must already be available and the official Toolbox 1.14.0 binary must be installed for the host architecture. Do not export lab credentials into files. The Docker image already includes Toolbox.

```bash
export GOOGLE_CLOUD_PROJECT=qwiklabs-gcp-04-b992b9561e32
export GOOGLE_CLOUD_LOCATION=us-east1
export GOOGLE_GENAI_USE_VERTEXAI=true
export GEMINI_MODEL=gemini-2.5-flash
export TOOLBOX_URL=http://127.0.0.1:5000
export TOOLBOX_BINARY="$(command -v toolbox)"
python scripts/serve.py
```

Open `http://127.0.0.1:8080`. Toolbox stays on loopback port 5000. Serving the process is not proof of model/data permissions; verify actual matching separately. The Docker runtime uses its existing service identity.

API entry points: `GET /api/health` (process only), `/api/scenarios`, `/api/city`, `/api/foodkeeper?query=bread`, `POST /api/match`; schema at `/docs`. Match inputs require a donor, offer text, explicit storage/pallet quantity, coordinates and a positive pickup window of at most 168 hours; an absolute deadline must include its timezone. Results distinguish `success`, deterministic `draft`, `no_viable_match` and `blocked`.

## Release without resetting the lab

The release coordinator runs `bash scripts/deploy.sh` from the reviewed checkout in student Cloud Shell. The script checks the lab account class and project, requires the existing service, preserves its runtime identity/IAM and unrelated environment variables, and deploys the existing dataset path. It does not enable APIs, change billing or reload data. Its warm minimum of one instance consumes runtime resources.

After deployment, run `python scripts/verify_live_release.py --expected-revision ACTUAL_READY_REVISION --output /tmp/release-evidence.json` from that checkout; use a new output filename. It verifies the target and keeps authentication tokens in memory, then records sanitized health/data and real ADK/MCP positive/refusal evidence. A valid no-match remains distinct from a failed runtime. Review the actual deployed UI on desktop and phone as well.

**Do not run `scripts/load.sh` or notebook replacement cells as deployment setup.** They can replace shared tables. `bq/01_embed_profiles.sql` is an isolated, unexecuted legacy `text-embedding-004`/768-dimensional proposal, incompatible with the production corpus. `bq/02_equity_audit.sql` is a read-only selected-profile diagnostic, not the ten-posting audit.

Firestore reservations, decline-state automation, A2A partner negotiation, incentives, verified delivery accounting and production routing remain future work. See the [A2A design](docs/A2A-RESCUE-EXCHANGE-PRD.md) and [map/profile research](docs/MAP-PROFILE-PLAN.md). The linked recipient explorer is implemented; road routing and operational availability are unverified.
