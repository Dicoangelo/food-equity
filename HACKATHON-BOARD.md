# Hackathon control board — 20 October 2026, 15:17 EDT

**Seattle retail/grocer rescue · five people.** Project `qwiklabs-gcp-04-b992b9561e32`; `us-east1` / `us-east1-b`; BigQuery `a4i_food` in `US`.

| Item | Verified state |
|---|---|
| Access | Student Cloud Shell reverified and configured. Dicoangelo contributes fork PRs; Nia merges. Public access remains scoped to the broker. |
| Data | City:119 profiles (110 generated,9 planted),389 tracts. Operations and pickup estimates remain unconfirmed. |
| Production | Public broker reports main `d885b798`, revision ending `0fc338`,100% in console. CI verified/promoted revision `07d20a` of that SHA; a later rebuild changed the revision. |
| Delivery proof | Run39479334651 test/deploy jobs passed:8 core gates including exact-ID MCP evidence,2 Live probes. Receipts saved in research/CI-d885b798. |
| UI checkpoint | PR16 / exact `515ba6f` is staging with no traffic; tag `g515ba6f71c-878d7e`. Await actual release and workspace-asset verification. |
| Dynamic broker | Isolated `codex/dynamic-broker`: Vertex free-text review, MCP vector+tract+FoodKeeper, grounded comparative brief and personalized draft.554 Python tests pass; candidate cloud proof pending. |
| Recovery | Review-only `35e6999`:323 Python,5 Node suites and320/390/1440 fixture QA pass. Excluded from this UI checkpoint; live microphone/provider recovery unverified. |
| Team workflow | This Google Cloud chat owns releases. Branch → PR → Nia merge → CI. Keep manual deploys/traffic changes paused. |

**Toronto:** freeze15:30 → submit16:30 → hard deadline16:59.

**Open:** physical microphone rehearsal, verified road routing, Pages activation, named submission owners and rubric/reuse confirmation. No acceptance, dispatch or delivery claimed.

**Single next action:** verify the UI checkpoint's tagged URL while preserving production traffic, then split back into isolated build branches.
