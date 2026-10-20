# Hackathon control board — 20 October 2026, 14:55 EDT

**Seattle retail/grocer rescue · five people.** Project `qwiklabs-gcp-04-b992b9561e32`; `us-east1` / `us-east1-b`; BigQuery `a4i_food` in `US`.

| Item | Verified state |
|---|---|
| Access | Student Cloud Run console and Cloud Shell verified. Public access is scoped to this service. Dicoangelo contributes fork PRs; Nia merges. |
| Data | Vertex, Run, Build, Artifact Registry and BigQuery enabled. City read: 119 profiles (110 generated, 9 planted), 389 tracts. Operations remain unconfirmed. |
| Live release | [Public broker](https://seattle-food-broker-6ukbp746xa-ue.a.run.app/) serves main `56bd96e`; exact revision `seattle-food-broker-g56bd96e708-1792521851-a1d045` at 100%. |
| Delivery | [Run 39477211341](https://github.com/niatalwalkar/food-equity/actions/runs/39477211341) passed tests, keyless authentication, build, 7 candidate checks, 2 Live checks and exact-revision promotion. Runtime configuration preserved. |
| Agent proof | Real ADK → MCP → BigQuery bakery retrieval: 12 candidates, proposal + backup. Cold-capacity stress refused. Stream began in 136 ms; result at 6.0 s. Gemini 3.8 Live typed draft/audio preserved the deadline twice. |
| Team workflow | Pull team main, work on separate branches, PR, Nia merges, GitHub deploys. This chat owns release. Do not deploy local checkouts or switch traffic manually during CI. |
| Next isolated slice | Exact-ID source evidence via MCP + clearer progress/deadline/map flow on `codex/broker-evidence-flow`; not live. Context Q&A/interruption work stays in separate review-only `codex/context-agent`. |

**Toronto:** feature freeze **15:30** → submit **16:30** → hard deadline **16:59**.

**Open:** physical microphone rehearsal, verified road routing, parser integration, Pages activation, named deck/video/submission owners and rubric/reuse confirmation. No acceptance, delivery or equity uplift claimed.

**Single next action:** review the next MCP/UI PR after its tests and phone checks; merge only through the shared release workflow.
