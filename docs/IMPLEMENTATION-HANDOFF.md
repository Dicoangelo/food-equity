# Seattle Food Equity — implementation handoff

**Snapshot: 20 October 2026.** Track: Seattle retail and grocer rescue. The deliverable is a **proposed Match Brief**, with recipient reasons, capacity/storage checks, a fixed clock, neighborhood context, outreach drafts and up to two actual eligible alternatives. It does not reserve inventory, contact recipients or dispatch transport.

## Release and evidence status

| Layer | Verified status at this handoff |
|---|---|
| Team repository | `main` is `9d7a931`; PRs #2, #4 and #6 are merged. This document and the current flow/acceptance/deadline changes are working-tree changes awaiting release. |
| Live application | Authenticated Cloud Run service `seattle-food-broker`, `us-east1`; revision ending `00003`, source `e1fbe03`. FoodKeeper, city data and real ADK/MCP execution were verified on that deployment. It is not proof that newer working-tree changes are live. |
| Local verification | Release coordinator reports **172 Python tests and the Node UI contract suite passing**. Local browser checks at 320px and 390px found no horizontal overflow and verified 44px controls. These are local checks, not a completed post-deployment UI acceptance pass. |
| Live replay audit | Job execution `seattle-food-audit-e1fbe03-dh844` completed in 38.12s: ten actual MCP retrievals, zero retrieval/evaluation errors, 389 supplied baseline tracts. Two proposed matches: 1/4 retail and 1/6 cross-track; eight honest `no_match` results. |
| Still pending | Deploy the combined changes; verify the released UI, positive/negative paths and `/api/health`; rerun the audit against the release image. The earlier audit predates the new acceptance guards. |

Workspace evidence is under `outputs/google-impact-toolkit-2026-10-20/research/`: `AUDIT-E1FBE03.json`, `LIVE-CLOUD-RUN-MAP-VERIFIED.jpg` and `UI-PHONE-390.png`. The audit uses evaluation time `2026-10-20T16:30:00+00:00`; it is a historical replay. These workspace receipts are not embedded in this document.

The audit's proposed-recipient neighborhood means were **below** the full equal-tract baseline: poverty −2.05 percentage points, no-vehicle households −3.62 points and assistance −3.11 points. Do not claim measured equity uplift, rescued food, actual deliveries or causal impact from this result.

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
| [agent/foodkeeper.py](../agent/foodkeeper.py) | Literal all-query-term search over the pinned 661-product reference; no inferred remaining freshness. |
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
| `GET /healthz`, `GET /api/health` | Same process-only response: `{"status":"ok","cloud_dependencies":"not_checked"}`. Keep `/healthz` for probes; the new `/api/health` alias avoids the Google edge interception observed on external `/healthz`. Alias release verification is pending. |
| `GET /docs`, `GET /openapi.json` | FastAPI schema/documentation behind the same authenticated service boundary. |

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

Eligible ranking uses 40% semantic similarity, 35% neighborhood proxy and 25% logistics features. These are design weights, not learned fairness guarantees. Missing neighborhood components contribute no ranking score and remain disclosed as unknown. The displayed heat value is the mean of poverty and no-vehicle rates only when both exist.

| Data | Meaning and limits |
|---|---|
| `a4i_food.recipient_embeddings` | Canonical runtime table; live inventory: 119 profiles and 3,072-dimensional embeddings. Identity/address/NTEE originate in the IRS starter data; operating capacity, storage, hours and narrative are generated. `planted` rows include invented organizations. |
| `a4i_food.tract_demographics` | ACS-derived aggregate neighborhood indicators. Map returns 389 tract-centroid points, within the Seattle metro display bounds; dates are not supplied by the current table. No individual hunger/eligibility inference. |
| `a4i_food.surplus_postings` | Ten starter offers for audit replay; pallet quantities are explicitly hypothetical manifest assumptions, not conversions of crates, loaves, gallons or tons. Audit can retain the starter's 720-hour essentials window; web offers remain capped at 168 hours. |
| `data/foodkeeper.json` | Pinned USDA consumer freshness/quality reference, described in [data/README.md](../data/README.md). Original purchase/opening/thawing fields and nulls are retained. No deadline mutation or commercial cold-chain clearance. |
| Map/directions | OSM tiles with attribution; recipient ZIP centroids and tract points are approximate. Google Maps opens a separate directions page using approximate endpoints. Dashed connectors are illustrative; no verified road route/drive time is returned by the app. |

The UI bakery preset uses original posting 3: “roughly 200 loaves of day-old bread and pastries from three store locations,” ambient, a hypothetical one-pallet capacity test and a 24-hour donor window. This is not a loaves-to-pallet conversion. The urgent chilled case is three pallets/four hours; the 50-pallet full-lot stress case assumes no split-load solver. A positive result depends on actual evaluation time and current profile gates; do not promise a winner. See [api.py](../agent/api.py) and [audit-manifest.json](../scripts/audit-manifest.json) for the exact assumptions.

## Teammate SQL and safe integration

The teammate `bq/` files were merged; their current working-tree cleanup removes shell wrappers and separates two purposes:

- [01_embed_profiles.sql](../bq/01_embed_profiles.sql) is an isolated **legacy sandbox proposal**, using `text-embedding-004`/768 dimensions and only `*_legacy_004_proposal` objects. `CREATE` fails on existing objects instead of replacing them. Connection/model permissions are unverified. It is not deployment setup and must never replace or mix vectors with the current 3,072-dimensional runtime corpus.
- [02_equity_audit.sql](../bq/02_equity_audit.sql) is a read-only selected-profile/tract diagnostic against the canonical corpus and current tract table. Profile 91 is an example, not a claimed winner. It shows valid observation counts and an equal-tract baseline; it is not the ten-posting replay.

The SQL cleanup passed static checks but has **not been submitted to BigQuery**. No data migration is required. The existing loader uses replacement writes; neither deployment nor this handoff requires reloading shared tables.

## Verification and next release

From the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node tests/test_ui_contract.js
```

The Python suite includes real ADK execution with a scripted model and mocked MCP boundary; this is offline correctness evidence, not a cloud call. Node tests exercise safe DOM output, fixed clock, presets, single-flight locking, pending facts, copies, map and FoodKeeper. API tests also parse the actually rendered JavaScript and assert health/UI requests do not invoke the broker.

In the already verified lab environment, [deploy_cloud_run.sh](../scripts/deploy_cloud_run.sh) checks the active project/account class and deploys an authenticated service. It changes no IAM or billing settings and does not replace data. Current configuration includes a service-level warm minimum of one and maximum four instances; this consumes runtime resources while idle. Keep credentials out of files/output and preserve the existing authentication boundary.

After release, use `python scripts/smoke_live.py` for real execution evidence, then test a declared ambient offer with `--require-match` only if the current constraints allow it. Inspect actual `mcp_result`, `adk_function_result`, clock recheck and generation mode. Check the released UI at desktop and phone widths, including map failure/partial data, no-match, pending facts, copy actions and FoodKeeper. Run the reviewed audit manifest against the release image from a declared evaluation timestamp; preserve the report and distinguish replay from current-time matching.

**Next action:** release and verify the combined broker/UI changes, then rerun the audit. [A2A-RESCUE-EXCHANGE-PRD.md](A2A-RESCUE-EXCHANGE-PRD.md) is a design-only proposal for future simulated partner protocols; no A2A endpoint, partner negotiation, durable exchange ledger, payments or delivered-impact accounting is implemented.
