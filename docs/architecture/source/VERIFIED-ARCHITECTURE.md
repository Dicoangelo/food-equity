# BeforeTheCrush — verified architecture source packet

Audit date: **2026-10-20** (America/Toronto).

Repository: [niatalwalkar/food-equity](https://github.com/niatalwalkar/food-equity)
Branch: `codex/demo-experience`. Commit: **`fcb4f76678713c55af3873acfc21e2871f44bcc7`**. Working tree clean at audit.

Every figure must carry **Repo snapshot fcb4f76**. The requested BeforeTheCrush name is a presentation label; this packet does not infer that branding is implemented in the source UI.

## Evidence boundary

Read-only source inspection. No cloud calls, live data reads, tests, credential access or deployment verification.

README.md:22 records release d8f9c21 / seattle-food-broker-00013-fum, while docs/TEAM-STACK.md:3 refers to dbf99118 and PR #19. These are historical documentation snapshots. Current local HEAD fcb4f76 was not established as the serving production commit.

Source file hashes and machine-readable claim/evidence mappings are in `claims.json`. Line references below refer to the audited commit. Code/configuration takes precedence over an older architecture sketch. Dataset counts are documented release evidence and were not independently refreshed here.

## Recommended diagram family

- **Pitch — overview:** intake, reviewed offer, grounded retrieval, deterministic gates, match proposal and human handoff.
- **Pitch — human handoff:** inspect proposal and backups; review profile, deadline and evidence; copy SMS/email draft; human confirms capacity/acceptance/ETA; external Maps directions.
- **Technical — runtime:** same container has FastAPI/Uvicorn, the ADK Agent/Runner, broker/matcher/evidence Python code and Google GenAI SDK, plus private Toolbox on 5000; only Vertex model inference and BigQuery are external managed services. API listens on 8080. Optional Live voice is a separate lane with its SDK adapter inside the API process.
- **Technical — tools/data:** four exact tools, BigQuery recipient/tract tables, local FoodKeeper HTTP loopback, and separate bounded BigQuery map reads.
- **Research — gates/ranking:** eligible/rejected/pending split, storage/capacity/clock/service/acceptance gates, 40/35/25 ranking and final clock recheck.
- **Research — data provenance:** IRS-derived identities/generated operations/teaching cases, ACS neighborhood proxies and pinned FoodKeeper; label limits and absence of measured equity uplift.

## Corrected main sequence

Coordinator alert → reviewed structured offer → FastAPI / NDJSON progress → local ADK Agent/Runner (calling external Vertex Gemini 2.5 Flash) → request-bound broker tool → loopback MCP candidate retrieval → deterministic eligibility and ranking → MCP context enrichment → bounded Gemini handoff plan → final deadline recheck → factual match brief, SMS/email drafts and up to two eligible backups → human confirmation.

`get_recipient_evidence` is an additional exact-profile read, not a mandatory stage inside every broker match. FoodKeeper enrichment is advisory and cannot change the donor deadline.

## Corrections to the supplied sketch

| Supplied wording | Repo-aligned wording |
|---|---|
| SSE streaming | NDJSON streamed progress |
| Three tools including check_foodkeeper | Four read tools; current FoodKeeper tool is lookup_shelf_life |
| FoodKeeper in BigQuery shelf-life layer | Pinned local JSON exposed through loopback FastAPI HTTP tool |
| 7.1% no-vehicle baseline equity multiplier | 40/35/25 weighted ranking with mean poverty/no-vehicle neighborhood proxy |
| Clock margin > 0.25h | Default margin >= 0.25h with positive remaining time |
| Cold-chain physics / safety clearance | Profile-based storage and operational gates |
| Automated they-said-no rerouting cascade | Up to two eligible backup proposals; decline automation future work |
| Dispatch and logistics execution | Coordinator handoff, draft outreach and external directions |
| Gemini dynamically writes all factual outreach | Gemini selects bounded plan; renderer supplies validated facts |
| 5 PM compactor alert fully parsed into deadline | Clock-time quote remains for coordinator review; explicit hour durations only |
| Live map implies verified route / travel time | Approximate locations and illustrative connector; external Google Maps directions |
| Real profiles imply real current capacity | IRS-derived identities with generated operational details plus teaching cases |
| Entire current diagram is verified deployed | Repo snapshot fcb4f76; source implementation and documented release evidence only |
| Vertex/ADK outside the container | ADK Agent/Runner, broker and Google GenAI SDK are inside Cloud Run; Vertex inference is external |

## Source-grounded claim inventory

### flow — Reviewed offer → grounded retrieval → feasibility checks → coordinator handoff

FastAPI calls the ADK broker, whose bound Python tool retrieves through MCP, evaluates candidates, enriches context, obtains a bounded model plan, rechecks the fixed clock and renders a proposal.

Evidence: [agent/api.py:324–357](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/api.py#L324); [agent/broker.py:342–433](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L342); [agent/broker.py:467–539](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L467).

Limit: The pipeline returns proposals or an honest blocked/no-match/draft result, not confirmed operations.

### intake — Free-text alert → quoted fields → coordinator review

Gemini 2.5 Flash on Vertex through ADK extracts explicitly declared storage, pallet count, product terms and hour durations. Clock times remain quoted for coordinator review; crates/loaves are not converted to pallets.

Evidence: [agent/free_text_intake.py:30](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/free_text_intake.py#L30); [agent/free_text_intake.py:130–156](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/free_text_intake.py#L130).

Limit: Never turn expiry or FoodKeeper guidance into a donor pickup deadline.

### runtime — Cloud Run us-east1 · FastAPI 8080 · private Toolbox 5000

Service seattle-food-broker targets project qwiklabs-gcp-04-b992b9561e32 in us-east1. One container supervises official Toolbox and Uvicorn; Toolbox binds 127.0.0.1:5000 and API binds 0.0.0.0:8080 by default. Google ADK, the broker, deterministic matcher, context/evidence services and Google GenAI SDK are Python application dependencies/code running inside the Cloud Run container, in the API process. Vertex model inference and BigQuery are external managed services.

Evidence: [scripts/deploy_cloud_run.sh:6–8](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/scripts/deploy_cloud_run.sh#L6); [scripts/serve.py:25–43](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/scripts/serve.py#L25); [Dockerfile:1–20](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/Dockerfile#L1).

Limit: These are code/configuration facts, not a fresh verification of the currently serving deployment.

### identity — Assigned Google Cloud project · runtime service identity

Application identity uses ADC and the existing Cloud Run service account. Local deployment helper requires the lab student account class and the assigned project; CI uses a dedicated deployment service account.

Evidence: [README.md:55–56](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L55); [scripts/deploy_cloud_run.sh:6–9](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/scripts/deploy_cloud_run.sh#L6); [scripts/deploy_cloud_run.sh:34–40](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/scripts/deploy_cloud_run.sh#L34); [scripts/deploy_cloud_run.sh:79–85](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/scripts/deploy_cloud_run.sh#L79).

Limit: This audit did not inspect credentials, authenticate, call cloud APIs or verify image-generation model access.

### progress — Live NDJSON progress

POST /api/match/stream returns application/x-ndjson. Its request-scoped stream contains sanitized stages and bounded counts, heartbeat/result/error records, with cancellation on disconnect.

Evidence: [agent/api.py:340–357](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/api.py#L340); [agent/match_stream.py:1–5](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/match_stream.py#L1); [agent/match_stream.py:22–29](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/match_stream.py#L22); [agent/match_stream.py:98–112](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/match_stream.py#L98).

Limit: Do not label this SSE, a durable job, or a persistent event bus.

### broker_model — Local ADK broker ↔ Vertex AI Gemini 2.5 Flash

Default inference model is gemini-2.5-flash in us-east1, accessed through a Vertex client configured with project/location. ADK Agent and Runner execute as Python library code in the Cloud Run API process. ADK exposes a request-bound broker_match function, not model-controlled arbitrary SQL or recipient selection. Only Gemini inference belongs in the external Vertex AI service box; do not place ADK in that external box.

Evidence: [agent/broker.py:34–36](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L34); [agent/broker.py:260–280](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L260); [agent/broker.py:398–433](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L398).

Limit: Environment can override GEMINI_MODEL and GOOGLE_CLOUD_LOCATION; the snapshot default is what is verified here.

### mcp — Google MCP Toolbox 1.14.0 · MCP 2025-03-26

The container copies the official Toolbox 1.14.0 binary. Broker, exact recipient evidence and context toolsets use MCP_v20250326.

Evidence: [Dockerfile:1–11](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/Dockerfile#L1); [agent/broker.py:278–280](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L278); [agent/recipient_evidence.py:165–176](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/recipient_evidence.py#L165); [agent/context_evidence.py:215–225](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/context_evidence.py#L215).

Limit: Team-operated official Toolbox; do not portray this as a separately hosted Google-managed BigQuery MCP service.

### four_tools — Four bounded read tools

Configured tools are vector_search_recipients, get_recipient_evidence, get_tract_equity and lookup_shelf_life.

Evidence: [agent/tools.yaml:16–39](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L16); [agent/tools.yaml:42–74](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L42); [agent/tools.yaml:92–107](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L92); [agent/tools.yaml:110–134](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L110).

Limit: check_foodkeeper is not a current MCP tool name.

### bq_read_policy — Parameterized BigQuery reads · writes blocked

The BigQuery source is restricted to a4i_food, location US, readOnly true, writeMode blocked, maximum 20 result rows and maximumBytesBilled 100000000.

Evidence: [agent/tools.yaml:1–13](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L1).

Limit: The retrieval-specific query returns at most 12 candidates.

### vector_retrieval — AI.EMBED + cosine VECTOR_SEARCH · top 12 candidates

vector_search_recipients queries a4i_food.recipient_embeddings with AI.EMBED using gemini-embedding-2; top_k 12, COSINE, use_brute_force true. README documents 3072-dimensional vectors.

Evidence: [agent/tools.yaml:27–35](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L27); [README.md:53–54](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L53).

Limit: Semantic candidates have not yet passed operational gates.

### recipient_evidence — Exact recipient profile evidence

get_recipient_evidence queries one canonical recipient_id from recipient_embeddings; limit two detects ambiguous duplicate identifiers.

Evidence: [agent/tools.yaml:42–74](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L42).

Limit: Stored profile evidence does not confirm current availability, acceptance or reservations. This tool is separate from the broker vector retrieval step.

### tract_context — ACS neighborhood context

get_tract_equity selects one exact Washington tract from a4i_food.tract_demographics, returning poverty/no-vehicle and related fields. Its need_score is the mean of valid poverty and no-vehicle rates.

Evidence: [agent/tools.yaml:110–134](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L110).

Limit: Aggregate neighborhood indicators do not establish service catchments, live demand or individual circumstances.

### foodkeeper_topology — Toolbox → local FoodKeeper reference

lookup_shelf_life is an HTTP tool on 127.0.0.1:8080/api/foodkeeper; the container includes data/foodkeeper.json. The context module pins its SHA256.

Evidence: [agent/tools.yaml:76–107](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/tools.yaml#L76); [Dockerfile:13](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/Dockerfile#L13); [agent/foodkeeper.py:8–20](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/foodkeeper.py#L8); [agent/context_evidence.py:28–29](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/context_evidence.py#L28).

Limit: Do not draw this as a BigQuery shelf-life table or a real-time USDA API call.

### constraints — Storage · full-lot capacity · timing · service days · acceptance

Pure deterministic checks gate candidate feasibility before ranking. Explicit rejection reasons produce rejected; missing critical facts produce needs_confirmation; only eligible rows compete for winner.

Evidence: [agent/matcher.py:239–357](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L239); [agent/matcher.py:405–414](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L405).

Limit: These are profile-based coordination checks; recipient confirmation remains required.

### storage — Cold storage must match the offer

Chilled offers reject none/dry/ambient storage. Missing or unrecognized storage remains unresolved. Refrigerated vehicle alone does not establish receiving-site storage; frozen offers require confirmed freezer support.

Evidence: [agent/matcher.py:292–311](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L292).

Limit: Avoid the phrase cold-chain physics or a claim of food-safety certification.

### full_lot — Full-lot capacity gate

Candidate max_pallets must be known and at least the explicit offered pallet quantity.

Evidence: [agent/matcher.py:262–264](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L262); [agent/matcher.py:312–317](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L312); [README.md:37](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L37).

Limit: Split-load optimization is not implemented; no inferred crate-to-pallet conversion.

### clock — Fixed deadline · at least 15 minutes of coordination margin

Default buffer is 0.25 hours. margin = remaining - pickup; rejected when remaining <= 0, margin <= 0, or margin < buffer. Exactly 0.25 hours passes the default boundary.

Evidence: [agent/matcher.py:239–246](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L239); [agent/matcher.py:271–281](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L271); [agent/matcher.py:318–330](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L318).

Limit: The buffer is a coordination policy, not a food-safety guarantee; profile pickup estimate is not verified travel time.

### clock_recheck — Recheck the deadline after model work

Original deadline persists; retrieval and synthesis consume the same window. Ranking is recomputed immediately before return. A changed winner causes a labeled deterministic draft.

Evidence: [agent/broker.py:311–324](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L311); [agent/broker.py:467–484](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L467); [agent/broker.py:505–522](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L505).

Limit: A variable named dispatch_clock_recheck does not mean the app actually dispatches a pickup.

### ranking — 40% semantic + 35% neighborhood proxy + 25% logistics

Among eligible candidates semantic=max(0,1-cosine distance); neighborhood=0.5*poverty+0.5*no_vehicle; logistics=0.5+0.3 if refrigerated_transport +0.2 if loading_dock. Composite weights are 0.40,0.35,0.25; sort descending composite, then distance and recipient_id.

Evidence: [agent/matcher.py:379–410](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L379).

Limit: Missing neighborhood inputs contribute no score but remain disclosed; weights do not prove fairness, charity quality or delivered impact. There is no current 7.1% baseline multiplier.

### backups — Up to two eligible backup proposals

Ranking returns winner and viable[1:3]. UI displays fewer than two backups honestly when unavailable.

Evidence: [agent/matcher.py:410–415](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/matcher.py#L410); [agent/api.py:2038–2044](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/api.py#L2038).

Limit: Automated decline-state fallback remains future work; no automatic rerouting or outreach.

### brief — Grounded facts + bounded Gemini handoff choices

Gemini chooses tone, confirmation_focus and evidence_order. Deterministic renderer supplies factual recipient, quantity and deadline sections from the final checked result; model prose is not the factual source.

Evidence: [agent/broker.py:415–433](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L415); [agent/broker.py:505–539](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L505).

Limit: Outputs are draft SMS/email and match brief, not sent messages.

### outcomes — Proposal · no viable match · blocked · labeled draft

The broker distinguishes no retrieval/ranking (blocked), no eligible winner (no_viable_match), successful Gemini plan (success), and valid retrieval/gates but synthesis failure or winner change (draft).

Evidence: [agent/broker.py:491–539](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L491).

Limit: A deterministic draft must not be mislabeled successful model synthesis.

### human_handoff — Human confirms acceptance, capacity and pickup

Recipient acceptance, storage, capacity, pickup ETA, receiving hours and transport remain confirmation questions. App prepares handoff text and does not send, reserve, dispatch or establish delivery.

Evidence: [agent/broker.py:229–238](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L229); [agent/broker.py:422–432](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/broker.py#L422); [README.md:32–35](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L32); [docs/TEAM-STACK.md:38](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L38).

Limit: No actual pickup is secured by a proposal or by copying a message.

### map — Leaflet / OpenStreetMap + neighborhood overlays

Frontend is HTML/CSS/JavaScript and Tailwind with Leaflet/OpenStreetMap. City layer reads bounded BigQuery rows through the BigQuery SDK, with tract and recipient geography bounds.

Evidence: [docs/TEAM-STACK.md:31–33](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L31); [agent/city.py:115–154](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/city.py#L115).

Limit: Do not draw all map-data reads as MCP tool calls. Map coordinates may be approximate ZIP centroids.

### directions — Open directions in Google Maps

The UI constructs an external google.com/maps/dir URL from donor/winner coordinates. The in-app connector is illustrative; no road geometry or travel time is claimed.

Evidence: [agent/api.py:1641–1645](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/api.py#L1641); [docs/TEAM-STACK.md:33–38](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L33).

Limit: No integrated Routes API, embedded Google Maps basemap, or verified driving-time claim.

### recipient_data — 119 profiles · 110 generated + 9 teaching cases

Repository documentation reports 119 Seattle-area recipient profiles: 110 with generated operational details and nine planted teaching cases. IRS-derived identities underpin the base corpus.

Evidence: [docs/TEAM-STACK.md:7–12](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L7); [docs/TEAM-STACK.md:18–20](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L18); [README.md:70](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L70).

Limit: Counts are documented release evidence, not a fresh live query in this audit; generated operations are unverified and planted organizations may be invented.

### tract_data — 389 ACS tract points · neighborhood proxies

Repository documentation reports 389 Census ACS tract points. Current tract snapshot does not retain observation year.

Evidence: [docs/TEAM-STACK.md:8](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L8); [docs/TEAM-STACK.md:19](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L19); [README.md:71](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L71).

Limit: Not live household demand, verified service areas or individual attributes.

### foodkeeper_data — 661 pinned consumer guidance references

Repository documents 661 pinned USDA FoodKeeper product references, retaining product options and unresolved applicability.

Evidence: [docs/TEAM-STACK.md:9](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L9); [docs/TEAM-STACK.md:21](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L21); [README.md:73](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L73).

Limit: No remaining freshness, commercial cold-chain safety clearance or change to donor deadline.

### no_training — Retrieval-grounded application

Datasets inform the agent through retrieval; the project did not train or fine-tune a model.

Evidence: [docs/TEAM-STACK.md:12](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L12).

### equity_evidence — Equity-aware comparison; impact remains unmeasured

README states the historical e1fbe03 ten-offer replay had two proposals and eight no-matches, predating later acceptance guards. Proposed-recipient neighborhood means were below the equal-tract baseline.

Evidence: [README.md:76–78](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L76).

Limit: Do not claim measured equity uplift or use the historical replay as a current acceptance-guard evaluation.

### voice_lane — Optional voice assistant · Gemini 3.8 Live

Separate optional Gemini 3.8 Live lane uses the Google GenAI SDK in the Cloud Run API process to connect to external Vertex Live inference. Browser audio uses same-origin /api/live/intake WebSocket, Web Audio API/AudioWorklet. Default Vertex location us-central1; enabled only by FOOD_LIVE_ENABLED.

Evidence: [docs/TEAM-STACK.md:30](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/docs/TEAM-STACK.md#L30); [agent/live_intake.py:50–67](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/live_intake.py#L50); [agent/live_intake.py:829–853](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/live_intake.py#L829); [agent/api.py:1314](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/api.py#L1314).

Limit: Keep separate from Gemini 2.5 Flash broker and Cloud Run us-east1. Presence in source is not proof this feature is enabled in production.

### voice_boundary — Voice proposes a draft; coordinator applies and runs

Live tools are draft_offer, read_current_match, read_recipient_evidence and show_alternatives. Draft changes only a proposal; three reads inspect bounded browser presentation state. Review/apply and explicit matching remain separate human actions.

Evidence: [agent/live_intake.py:349–391](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/live_intake.py#L349); [agent/action_state.py:102–119](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/agent/action_state.py#L102).

Limit: Voice read_recipient_evidence is a browser-state read, distinct from MCP get_recipient_evidence that retrieves the exact database row. Voice cannot match, fetch, rerank, send, reserve, approve, dispatch or pay.

### future_work — Reservations and decline automation are future work

Firestore reservations, decline-state automation, A2A negotiation, incentives, verified delivery accounting and production routing are explicitly future work.

Evidence: [README.md:142](https://github.com/niatalwalkar/food-equity/blob/fcb4f76678713c55af3873acfc21e2871f44bcc7/README.md#L142).

Limit: Omit from implemented architecture or clearly isolate as future work; do not connect as running services.

## Optional voice lane — keep distinct

**Browser microphone / audio → same-origin WebSocket `/api/live/intake` → Google GenAI SDK inside Cloud Run → external Vertex Gemini 3.8 Live (`us-central1` by default) → proposed draft or bounded displayed-state answer.** Cloud Run remains in `us-east1`. The feature is optional and gated by `FOOD_LIVE_ENABLED`; its presence in the repo does not establish it is enabled on the current production revision.

Voice proposes changes with exact quoted evidence. The coordinator must review and apply the proposal, then explicitly run matching. The voice lane does not invoke matching, rerank alternatives, fetch database evidence, navigate, send outreach, reserve, dispatch or pay. Its `read_recipient_evidence` reads the browser presentation snapshot; the separate MCP `get_recipient_evidence` reads an exact BigQuery recipient record. Do not merge those two tools in a diagram.

Source: `docs/TEAM-STACK.md:30`, `agent/live_intake.py:50–67,349–391,829–853`, `agent/action_state.py:102–119`.

## Runtime boundary — explicit placement

Inside the Cloud Run container: FastAPI/Uvicorn, imported Google ADK Agent/Runner, broker_match and matcher/evidence Python code, Google GenAI SDK clients, pinned FoodKeeper JSON and the separate loopback Toolbox process. External managed services: Vertex Gemini inference and BigQuery. Browser UI executes on the user device. This is established by `Dockerfile:9–14`, `requirements.txt:1–8`, `scripts/serve.py:25–43` and `agent/broker.py:13–22,412–447`. An architecture box titled Vertex AI must not contain ADK as a hosted service.

## Delivery and verification context

The docs identify GitHub/GitHub Actions with Workload Identity Federation, Cloud Build and Artifact Registry, exact-commit candidate checks and controlled promotion. These may appear as a small delivery inset if needed; no current cloud or CI state was verified in this audit. See `docs/TEAM-STACK.md:34–36` and `scripts/deploy_cloud_run.sh:31–100`.

No source repository files were changed by this audit.
