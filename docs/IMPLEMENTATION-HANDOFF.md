# Seattle Food Equity — implementation handoff

**Snapshot: 20 October 2026.** Track: Seattle retail and grocer rescue. The deliverable is a **proposed Match Brief**, with recipient reasons, capacity/storage checks, a fixed clock, neighborhood context, outreach drafts and up to two actual eligible alternatives. It does not reserve inventory, contact recipients or dispatch transport.

**[Open the live broker](https://seattle-food-broker-6ukbp746xa-ue.a.run.app)** · [Access/proxy instructions](../README.md#team-access)

## Release and evidence status

| Layer | Verified status at this handoff |
|---|---|
| Team repository | `main` is `b6586cd8f807239b489a72f7f72b774d4e9e6254`; [PR #10](https://github.com/niatalwalkar/food-equity/pull/10) merged at `2026-10-20T18:00:12Z`, following PR #9. Ownership transfer is parked at Dico's request; the fork is unchanged. |
| Public application | Combined source `d8f9c2159efa60f4e7b07324534fc509dd100d17`, exact revision `seattle-food-broker-00013-fum`, promoted to 100% traffic in `us-east1`; no moving latest-revision pointer. The tagged candidate passed health/city/FoodKeeper, real ADK/MCP bakery success with one backup and cold-capacity refusal. |
| Public access | Dico authorized the verified service-only public invoker binding. Runtime ADC identity and private-loopback Toolbox remain separate from public invocation. |
| Data | City response: 389 tract points, 119 profiles (110 generated, nine planted). FoodKeeper returned options without changing the clock. |
| Local verification | Combined suite: 245 Python tests and both Node UI/explorer suites passed. Independent focused Live/smoke suite: 50 passed; static repair feedback, atomic rejection and exact deadline retention verified. |
| Live acceptance | Original unchanged typed smoke also passed on the public URL after promotion at `17:58:07Z`: ready/draft update/deadline/response all true, 309,120 audio bytes/six transcript events, no match request. Earlier combined candidate: draft updated to two pallets, exact deadline preserved, response complete, 228,482 generated-audio bytes/four transcript events, no match request. Predecessor `c60faa7` passed twice (242,880/four and 279,362/five). |
| Browser verification | Typed draft → explicit Apply → separate real MCP match with winner/backup retained the exact deadline. Combined UI checks verified real bakery → winner profile, eligible filter with two options, 390px phone modal and 320px no-overflow layout; no new app errors. Physical microphone capture/playback remains unverified. |
| Team portal | PR #9 merged, but Pages `has_pages=false`; workflow `39467685689` failed. Admin setup: Settings → Pages → GitHub Actions. No Pages URL claimed. |
| Historical audit | `seattle-food-audit-e1fbe03-dh844`: ten MCP retrievals, zero evaluation/retrieval errors, 389 baseline tracts; two proposals, eight no-matches. Predates acceptance refinements and includes posting 8's source-clock defect. |
| Still pending | Physical microphone capture/playback, Pages admin setup and a current-image replay audit receipt. Repository ownership transfer remains parked; the fork is unchanged. |

Workspace evidence is under `outputs/google-impact-toolkit-2026-10-20/research/`: `LIVE-CORE-CANDIDATE-d8f9c21.json`, `LIVE-INTAKE-CANDIDATE-d8f9c21.json`, `LIVE-INTAKE-PUBLIC-d8f9c21.json` and `LIVE-PROMOTION-d8f9c21.json` establish candidate checks, exact promotion and the final public-origin Live pass. `LIVE-INTAKE-CANDIDATE-c60faa7-1.json` and `-2.json` preserve the prior repeated Live passes. Historical `AUDIT-E1FBE03.json` uses evaluation time `2026-10-20T16:30:00+00:00`; it is a replay, not current operations. These workspace receipts are not embedded in this document.

The historical audit's proposed-recipient neighborhood means were **below** the full equal-tract baseline: poverty −2.05 percentage points, no-vehicle households −3.62 points and assistance −3.11 points. Do not claim measured equity uplift, rescued food, actual deliveries or causal impact from this result.

**Known source-clock defect — sealed formula, posting 8.** The receipt preserves `hours_available=168`, `foodkeeper_product="Formula"`, `storage_state="pantry"` and `shelf_life_hours=1`; the replay correctly uses the stored `hours_remaining=1`. The manifest's `expected_hours_available=168` checks donor availability, not the effective replay clock. There is no API clamp in this path. However, the one-hour cap comes from the prepared-formula after-opening reference and is not applicable to the posting's “48 cases of sealed infant formula, 8 months to expiry.” The current [starter notebook](../notebooks/c2_01_load_explore.ipynb), sections 10 and 13, already documents this exact defect, maps the sealed example to “Liquid concentrate or ready-to-feed formula,” and includes a “sealed goods keep their clock” sanity check. The historical table values therefore differ from that corrected notebook. This is a data-state mismatch, not a verified conservative expiry.

Keep the original audit receipt intact. Before presenting a corrected ten-posting feasibility result, align with the team on the intended product/state mapping, check the source row and the notebook's sealed-goods check, then preserve a separate replay receipt with the reviewed source clock and exact release. Do not silently substitute 168 hours, rerun a replacement loader or alter the shared table as part of deployment. Retrieval counts remain execution evidence; posting 8's historical no-match cannot establish real sealed-formula feasibility. The runtime FoodKeeper lookup remains advisory and never changes a donor deadline.

## Runtime and implementation map

```text
Coordinator browser
  → FastAPI validation
  → ADK Runner / Gemini on Vertex
  → custom broker_match tool, bound to the validated offer
  → official MCP Toolbox / vector_search_recipients
  → BigQuery AI.EMBED + VECTOR_SEARCH
  → deterministic eligibility/ranking
  → final clock recheck
  → factual Match Brief + bounded Gemini outreach choices
```

| Source | Responsibility |
|---|---|
| [agent/api.py](../agent/api.py) | API validation, lazy service creation and same-origin inline UI. Data enter text nodes; no source-controlled HTML. |
| [agent/broker.py](../agent/broker.py) | Actual ADK Runner, request-scoped session, MCP discovery/call, trace, final recheck and deterministic brief rendering. Gemini selects only a validated tone/question ordering. |
| [agent/matcher.py](../agent/matcher.py) | Pure storage, capacity, pickup, schedule and explicit acceptance gates; eligible-only ranking. Its direct BigQuery helper is diagnostic, not the web retrieval path. |
| [agent/tools.yaml](../agent/tools.yaml) | One parameterized read-only MCP tool: `gemini-embedding-2`, COSINE, brute-force search, top 12; embeddings excluded from returned rows. |
| [agent/city.py](../agent/city.py) | Bounded BigQuery map reads, 300-second ready cache, 15-second failed/partial cache, no model calls. |
| [recipient-explorer.js](../agent/ui/recipient-explorer.js), [CSS](../agent/ui/recipient-explorer.css) | Linked search/filter/map/profile views by recipient ID; winner/backups preserve backend order; mobile sheet traps/restores focus; opening a profile makes no API/model call. |
| [agent/foodkeeper.py](../agent/foodkeeper.py) | Literal all-query-term search over the pinned 661-product reference; no inferred remaining freshness. |
| [agent/live_intake.py](../agent/live_intake.py) | Gemini 3.8 Live draft adapter with validated sparse patches: bounded session, explicit quantity/window evidence, fixed-clock preservation, review before applying to the form. It cannot match, send or reserve. |
| [scripts/serve.py](../scripts/serve.py), [Dockerfile](../Dockerfile) | Official Toolbox 1.14.0 on loopback port 5000; FastAPI on `PORT`/8080, supervised together. Only HTTP is exposed. |
| [scripts/audit_postings.py](../scripts/audit_postings.py), [scripts/run_audit_job.py](../scripts/run_audit_job.py) | Ten-posting replay through MCP and the matcher without Gemini; full-tract baseline, explicit manifest and bounded job wrapper. |

There is one implemented ADK coordinator with a custom tool, not a deployed swarm of independent recipient/transport agents. MCP uses Google's maintained **MCP Toolbox operated by the team**; it is not a claim that the separately hosted BigQuery MCP service is in use.

Vertex defaults to `gemini-2.5-flash` in `us-east1`. BigQuery dataset `a4i_food` is in `US`. Runtime credentials are ADC; no API key is required in the app. The pinned dependencies are in [requirements.txt](../requirements.txt). `GOOGLE_CLOUD_PROJECT` must be configured; `GOOGLE_CLOUD_LOCATION`, `GEMINI_MODEL`, `TOOLBOX_URL` and optional explicit `CORS_ORIGINS` control the app. Toolbox currently fixes its allowed dataset to `a4i_food`, so changing a dataset environment variable alone does not migrate retrieval.

## API contract

| Route | Behavior |
|---|---|
| `GET /` | Coordinator UI; serving HTML creates no cloud client or model request. The loaded browser fetches scenarios and city layers; matching requires a click. |
| `GET /api/scenarios` | Static donor scenarios. Choosing a UI preset fills the form without calling the broker. |
| `POST /api/match` | Real ADK/MCP flow. A no-match or fallback is an explicit result, not an invented success. |
| `GET /api/city` | `ready`, `partial` or `unavailable`; sanitized `tracts`, `recipients`, `provenance`, `limits`, `warnings`, `errors`, `as_of`, `cache`. |
| `GET /api/foodkeeper?query=…` | Query 2–120 characters; at most five literal-match options, exact subtype/storage basis, raw fields, source hash and unresolved applicability. No automatic product selection. |
| `GET /healthz`, `GET /api/health` | Same process-only response: `{"status":"ok","cloud_dependencies":"not_checked"}`. Keep `/healthz` for probes; the new `/api/health` alias avoids the Google edge interception observed on external `/healthz`. `/api/health` passed the combined `d8f9c21` candidate check. |
| `GET /docs`, `GET /openapi.json` | FastAPI schema/documentation with the same service access policy; public-demo access is enabled. |

Match input: `posting_text` 5–4000 characters; `donor_name` 1–200; finite `pallet_count` >0 to 10,000; `hours_remaining` >0 to 168; geographic `donor_lat`/`donor_lon`; `required_storage` = `cold`, `ambient` or `frozen`; optional timezone-aware `deadline_at`. Refrigeration/dry aliases normalize, unknown fields are forbidden, and expired deadlines fail validation.

Match output always describes confirmation requirements and provenance. Successful retrieval includes `winner`, `runners_up` (0–2), `eliminated_decoys`, `pending_confirmation`, `map_markers`, `donor`, `evaluated_at` and `execution_trace`. Candidate fields include profile source, geography precision, capacity/storage, timing basis, deadline/margin, reason codes and acceptance evidence. Brief fields are `match_justification`, `operational_verification`, `clock_action_plan`, `equity_rationale`, `drafted_sms_outreach` and `drafted_email_outreach`.

| Result | Meaning |
|---|---|
| `success` / `adk_gemini` | Real retrieval/gates completed; Gemini returned valid bounded outreach choices. Factual prose still comes from the final ranking. |
| `draft` / `deterministic_fallback` | Real retrieval/gates completed; synthesis failed or the clock changed the winner. A clearly labeled deterministic draft remains reviewable. |
| `no_viable_match` / `constraints` | No retrieved recipient passed all gates; unresolved details and rejections remain visible. The replay audit calls this `no_match`. |
| `blocked` / `unavailable` | Discovery, authentication, retrieval or execution could not establish a ranking. No match is fabricated. |

## Decisions, clock and provenance

The UI follows **offer → review → handoff**. Only one match request can run at once; form/presets lock during it, and edits clear previous result/copy state. Loading, service failure, no-match, pending details and fallback each have distinct states. Copying a brief, SMS or email sends nothing.

The donor deadline is fixed when an offer is run. Unchanged retries retain it. The API forwards the absolute deadline through lazy initialization, and the broker uses the earlier of the supplied deadline and relative window. Retrieval and model time consume that window; constraints run again before returning. The default 15-minute coordination buffer is not a food-safety rule. Pickup timing comes from supplied/profile estimates, not verified driving time. Recognized Seattle service days are checked; unknown schedules stay pending.

Known storage conflict, insufficient full-lot capacity, missed clock/buffer or known closed service day rejects a candidate. Unknown/contradictory critical fields stay pending. Vehicle refrigeration does not prove receiving-site cold storage. Explicit starter-profile refusal guards cover meat/pork, certification, labeling, infant-only, nonperishable and formula-package clauses. Unmatched/conditional clauses and unsupported tails of recognized refusals go pending; a qualified food restriction is not treated as a blanket food ban. These are bounded literal patterns, not a general allergy, ingredient, religious-certification or safety verifier. Every “eligible” profile still requires human confirmation.

The linked explorer filters map/list together and exposes capacity, storage, schedule, acceptance, exact deadline and provenance. Eligible-only offer-fit bars explain this specific offer, not charity quality; reconstruction from rounded response fields is disclosed and the backend score/order stays authoritative. Directions refresh when the donor origin changes; invalid evaluated origins disable the link.

Eligible ranking uses 40% semantic similarity, 35% neighborhood proxy and 25% logistics features. These are design weights, not learned fairness guarantees. Missing neighborhood components contribute no ranking score and remain disclosed as unknown. The displayed heat value is the mean of poverty and no-vehicle rates only when both exist.

| Data | Meaning and limits |
|---|---|
| `a4i_food.recipient_embeddings` | Canonical runtime table; live inventory: 119 profiles (110 generated, nine planted) and 3,072-dimensional embeddings. Identity/address/NTEE originate in the IRS starter data; operating capacity, storage, hours and narrative are generated. `planted` rows include invented organizations. |
| `a4i_food.tract_demographics` | ACS-derived aggregate neighborhood indicators. Map returns 389 tract-centroid points, within the Seattle metro display bounds; dates are not supplied by the current table. No individual hunger/eligibility inference. |
| `a4i_food.surplus_postings` | Ten starter offers for audit replay; pallet quantities are explicitly hypothetical manifest assumptions, not conversions of crates, loaves, gallons or tons. Audit can retain the starter's 720-hour essentials window; web offers remain capped at 168 hours. |
| `data/foodkeeper.json` | Pinned USDA consumer freshness/quality reference, described in [data/README.md](../data/README.md). Original purchase/opening/thawing fields and nulls are retained. No deadline mutation or commercial cold-chain clearance. |
| Map/directions | OSM tiles with attribution; recipient ZIP centroids and tract points are approximate. Google Maps opens a separate directions page using approximate endpoints. Dashed connectors are illustrative; no verified road route/drive time is returned by the app. |

The UI bakery preset uses original posting 3: “roughly 200 loaves of day-old bread and pastries from three store locations,” ambient, a hypothetical one-pallet capacity test and a 24-hour donor window. This is not a loaves-to-pallet conversion. The urgent chilled case is three pallets/four hours; the 50-pallet full-lot stress case assumes no split-load solver. A positive result depends on actual evaluation time and current profile gates; do not promise a winner. See [api.py](../agent/api.py) and [audit-manifest.json](../scripts/audit-manifest.json) for the exact assumptions.

## Teammate SQL and safe integration

The teammate `bq/` files and wrapper cleanup are merged. They separate two purposes:

- [01_embed_profiles.sql](../bq/01_embed_profiles.sql) is an isolated **legacy sandbox proposal**, using `text-embedding-004`/768 dimensions and only `*_legacy_004_proposal` objects. `CREATE` fails on existing objects instead of replacing them. Connection/model permissions are unverified. It is not deployment setup and must never replace or mix vectors with the current 3,072-dimensional runtime corpus.
- [02_equity_audit.sql](../bq/02_equity_audit.sql) is a read-only selected-profile/tract diagnostic against the canonical corpus and current tract table. Profile 91 is an example, not a claimed winner. It shows valid observation counts and an equal-tract baseline; it is not the ten-posting replay.

The SQL cleanup passed static checks but has **not been submitted to BigQuery**. No data migration is required. The existing loader uses replacement writes; neither deployment nor this handoff requires reloading shared tables.

## Verification and next release

From the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node tests/test_ui_contract.js
node tests/test_recipient_explorer.js
```

The Python suite includes real ADK execution with a scripted model and mocked MCP boundary; this is offline correctness evidence, not a cloud call. Node tests exercise safe DOM output, fixed clock, presets, single-flight locking, pending facts, copies, map and FoodKeeper. API tests also parse the actually rendered JavaScript and assert health/UI requests do not invoke the broker.

In the already verified lab environment, [deploy_cloud_run.sh](../scripts/deploy_cloud_run.sh) checks the active project/account class and updates the existing service without changing its invoker policy. It changes no IAM or billing settings and does not replace data. Current configuration includes a service-level warm minimum of one and maximum four instances; this consumes runtime resources while idle. Keep credentials out of files/output. Public invocation was separately authorized by Dico for this demo; deployment does not itself modify IAM.

After each release, run the standalone verifier from student Cloud Shell:

```bash
python scripts/verify_live_release.py \
  --expected-revision ACTUAL_READY_REVISION \
  --output /tmp/release-evidence.json
```

Use a new output filename. It validates the target, holds the identity token in process memory and records sanitized health/city/FoodKeeper plus real ADK/MCP bakery/refusal evidence. A successful no-match is distinct from a runtime failure. The combined `d8f9c21` candidate core receipt has both a bakery success and a cold-capacity refusal; health alone does not verify cloud dependencies. Check direct access and the released UI at desktop/phone widths separately. Replay audits must use a declared evaluation timestamp and exact release image, with source assumptions preserved.

The Live routes are `GET /api/live/config` and WebSocket `/api/live/intake`. They use server-side ADC, session/message bounds and exact origin checks; origin checks are not authentication. `FOOD_LIVE_ENABLED` defaults off in code and remains the operator switch. The earlier copied-context-hours guard rejection was addressed by sparse-patch prompting/static feedback in `c60faa7`, then the unchanged typed smoke passed twice and passed again on combined `d8f9c21`. This change did not relax validation. Invalid edits remain atomic; unrelated context hours must be omitted, and unchanged deadlines are retained.

Cleanup fix `b2ed2f8` bounds terminal status/close I/O to one second each. Independent backpressure replay returned in 2.361 seconds with both sends cancelled, provider/client closed, reader stopped and zero session slots retained. Verify candidate Start/Stop, microphone denial, typed fallback, fixed deadlines, disconnect and expiry. Applying a draft must not trigger matching or send outreach.

**Next action:** verify physical microphone capture/playback and coordinate Pages admin setup. [A2A-RESCUE-EXCHANGE-PRD.md](A2A-RESCUE-EXCHANGE-PRD.md) remains design only; no A2A endpoint, partner negotiation, durable exchange ledger, payments or delivered-impact accounting is implemented.
