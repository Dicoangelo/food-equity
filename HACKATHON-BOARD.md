# Hackathon control board — 20 October 2026

**Seattle · retail/grocer rescue · five-person team.** Project `qwiklabs-gcp-04-b992b9561e32`; runtime `us-east1`, zone `us-east1-b`; BigQuery `a4i_food` in `US`.

| Item | Verified state |
|---|---|
| Accounts/control | **VERIFIED:** Antigravity Account and Cloud Shell active gcloud identity match assigned student. Cloud Shell commands work; region/zone set. VM gcloud defaults **UNVERIFIED**. |
| Cloud inventory | **VERIFIED:** student `roles/owner`; Vertex, Run, Build, Artifact Registry, BigQuery/connection APIs already enabled. Eight lab browser services, nine running VMs, three service accounts; no buckets/Artifact Registry repositories at inventory time. Lab resources preserved. |
| Data/model | **VERIFIED LIVE:** 119 recipient embeddings, 3072 dimensions, generated/planted source labels. COSINE `VECTOR_SEARCH` + `AI.EMBED` works; Gemini 2.5 Flash responds in `us-east1`. |
| Repository | **VERIFIED:** Nia main `36df397`; Dico PR #1 `c741544`, open. Fixes isolated locally in original `agent/`, `scripts/`, `tests/` layout. VM parser/state additions preserved separately. |
| Quality/build | **VERIFIED:** 56 Python tests + Node UI checks pass, including actual ADK Runner with mocked model/MCP boundary. Cloud container execution still pending. |
| Deployment | **UNVERIFIED:** no finished broker Cloud Run URL yet. Lab browser services are not the application. |

**Deadline (Toronto):** freeze **15:30** → submit target **16:30** → Command Console hard deadline **16:59**.

**Open:** lane owners/integration branch; inherited policies/quota (policy read timed out); live ADK/MCP trace + deployed request; VM board copy; reuse guidance. Generated profiles do not establish recipient availability.

**Single next action:** deploy the authenticated broker, then capture a successful ADK/MCP request.
