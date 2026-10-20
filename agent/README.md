# Seattle retail & grocer rescue agent

The implemented path is `api.py → ADK Runner → broker_match Python tool →
MCP Toolbox → BigQuery VECTOR_SEARCH → deterministic constraints → grounded Match Brief`.
The custom tool binds the validated posting; the model cannot invent candidates or
relax the storage, capacity or deadline gates. Every response carries a compact
execution trace. A retrieved profile is a proposal requiring recipient confirmation.
Gemini chooses a bounded outreach tone and question order. All factual brief fields
and outreach details are rendered from the final validated ranking and request;
free-form model prose cannot reintroduce rejected recipients or invent dates.

`matcher.py` contains pure constraint/ranking logic. Its direct BigQuery helper
remains available for diagnosis, but the web broker calls the real MCP path in
`broker.py`. `tools.yaml` exposes one parameterized, read-only vector query through
Google's official MCP Toolbox. This is a team-operated Google-maintained server,
not a claim that the separate hosted BigQuery MCP service is being used.

## Run and deploy

Python 3.12 and the repository-root `requirements.txt` are pinned. The root
Dockerfile copies the official Toolbox **1.14.0** binary and runs it on loopback
port5000 beside FastAPI port8080. The HTTP service is the only exposed port.
No API keys or credential files belong in this repository; both BigQuery and
Vertex use the runtime's Application Default Credentials.

From the verified student Cloud Shell, with the project already selected:

```bash
bash scripts/deploy_cloud_run.sh
```

The script deploys authenticated `seattle-food-broker` in `us-east1`, with zero
minimum and two maximum instances. It does not enable APIs, change billing,
grant IAM roles or remove any lab service. The deployment identity must already
have permission to build/deploy; the runtime identity needs existing BigQuery
job/data read and Vertex access. BigQuery's `a4i_food` dataset location is **US**,
independent of the Cloud Run/Vertex runtime region.

For a terminal smoke test using the active student's identity (token stays in
process memory and is never printed):

```bash
python scripts/smoke_live.py
```

This checks completed agent execution, including honest no-match results. To require
a positive match, supply an offer and `--require-match`, for example:

```bash
python scripts/smoke_live.py --storage ambient --quantity 2 --hours 24 \
  --query "2 pallets of fresh sourdough and sliced sandwich bread, baked this morning, ambient dry storage" --require-match
```

The outcome depends on the actual time and profile service days; no scenario guarantees
a viable match. On 20 October 2026, this bakery offer produced an eligible winner and
two alternatives through the deployed service. Cold-food tests also correctly returned
no viable match. These are generated-profile demonstrations, not secured donations.

For browser use, start an authenticated local Cloud Run proxy in Cloud Shell:

```bash
gcloud run services proxy seattle-food-broker --project qwiklabs-gcp-04-b992b9561e32 --region us-east1 --port 8080
```

Use Cloud Shell's port8080 Web Preview. `/healthz` only proves the web process;
a successful `/api/match` response with `mcp_result`, `adk_function_result`, and
`generation_mode=adk_gemini` is required to prove the end-to-end path.

## Data and evidence

The read-only tool expects the existing `a4i_food.recipient_embeddings` table.
The live inventory checked 119 rows, 3072 dimensions and `profile_source` values
`generated`/`planted`, plus tract enrichment. The query uses `gemini-embedding-2`
and explicit `COSINE` distance. Do not run the loader or replace teammate tables
as part of deployment. Individual organization operations are generated profiles;
planted examples are synthetic. ZIP centroids and illustrative map lines are
not exact facilities or driving routes.
The city endpoint returned 389 tract points and 119 recipient profiles. The UI uses
OpenStreetMap standard tiles with attribution and browser caching; it does not prefetch
or offer offline tiles. See the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

Model failure after successful retrieval yields `status=draft` and
`generation_mode=deterministic_fallback`. Tool/auth/query failure yields `blocked`
with no fabricated match. No outreach is sent. No reservation is made.

Run offline tests:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node tests/test_ui_contract.js
```

The runtime tests execute a real ADK Runner with a scripted model and fake MCP
boundary; they are not live cloud proof.

## What has to be true of what you build here

- **An ADK agent**, in Python.
- **At least one tool you built yourself.** A Python function tool, or one you defined in MCP
  Toolbox—either counts. The obvious candidate wraps your match query, but the more valuable
  one holds the logic that isn't a single query: composing the search string, applying the
  constraint filter, assembling the Match Brief. Consuming only prebuilt generic tools and
  calling that your design does not count.
- **At least one Google-managed MCP server, consumed.** Don't author your own—use BigQuery's
  built-in server or the [MCP Toolbox for Databases](https://github.com/googleapis/mcp-toolbox).
- **Deployed to Google Cloud**—Agent Runtime or Cloud Run, your choice.
- **Your required differentiator: BigQuery vector search.** Your agent must genuinely *call*
  the semantic search. Filtering on categories or `LIKE '%spinach%'` does not count, and it is
  the most common way a team misses the point while appearing to hit it.

## The thing worth remembering while you build

Vector search is a **candidate generator, not an answer**. There is at least one organization
in your corpus that semantically looks like a perfect match and physically cannot store the
food. What you do *after* the search—storage compatibility, pallet capacity, the pickup window
against the clock—is where this agent becomes useful instead of merely clever.

See Section 14 of [`notebooks/c2_01_load_explore.ipynb`](../notebooks/c2_01_load_explore.ipynb)
for the query shape and the four syntax traps (it reads fine on GitHub without running).
