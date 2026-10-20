# Food Equity — implemented stack and data ecosystem

For the six-slide team pitch: the following describes the implemented project, not a list of every Google product available in the lab. Runtime/data counts were verified in the `dbf99118` production release; PR #19 upgrades Live recovery, audio verification and mobile assistance.

## Paste-ready data slide

- **Recipient knowledge base:** BigQuery holds 119 Seattle-area recipient profiles with storage, capacity, pickup and location fields: 110 profiles with generated operational details and nine teaching cases.
- **Neighborhood context:** 389 Census ACS tract points provide poverty and transportation-access indicators for equity-aware comparison.
- **Food guidance and incoming offers:** 661 pinned USDA FoodKeeper product references complement the coordinator's free-text or spoken surplus alert, quantity, storage, origin and pickup deadline.
- **Grounded retrieval:** Gemini/ADK calls MCP Toolbox tools for BigQuery vector search, exact recipient evidence, tract context and FoodKeeper guidance. Retrieved candidates pass explicit capacity, storage and deadline checks before the match brief.

These datasets inform the agent through retrieval; no model was trained or fine-tuned. Recipient availability and acceptance require confirmation.

## Exact sources and interfaces

| Component | Actual use |
|---|---|
| `qwiklabs-gcp-04-b992b9561e32.a4i_food.recipient_embeddings` | Recipient profiles, operational fields, associated tract context and vectors. |
| `qwiklabs-gcp-04-b992b9561e32.a4i_food.tract_demographics` | ACS-derived tract snapshot. Observation year is not retained in the current dataset. |
| IRS-derived organization identities | Identity basis for the source corpus; generated operational details are not verified real-world capabilities. |
| Pinned USDA FoodKeeper reference | 661 product references; consumer storage/quality guidance, not proof of remaining freshness or safety clearance. |
| `gemini-embedding-2` and BigQuery `VECTOR_SEARCH` | Semantic retrieval of up to 12 recipient candidates. |
| Google MCP Toolbox | Read tools: `vector_search_recipients`, `get_recipient_evidence`, `get_tract_equity`, `lookup_shelf_life`. |

## Application and infrastructure

| Layer | Technology and purpose |
|---|---|
| Agent | Vertex AI Gemini 2.5 Flash, Google ADK: extraction, tool orchestration and grounded brief/handoff synthesis. |
| Live assistant | Gemini 3.8 Live through the Google GenAI SDK; same-origin WebSocket, Web Audio API and AudioWorklet. Cloud Run is in `us-east1`; Live uses `us-central1`. |
| Backend | Python, FastAPI, Pydantic, Uvicorn; validated API contracts, streamed progress and deterministic feasibility checks. |
| Frontend | HTML/CSS/JavaScript, Tailwind CSS, Leaflet and OpenStreetMap; responsive workspace, recipient profiles, map and neighborhood overlays. |
| Directions | Google Maps external directions URL. No integrated Google Maps/Routes API or measured road-travel-time claim. |
| Hosting | Cloud Run, Docker, Cloud Build and Artifact Registry in the assigned Google Cloud project. |
| Delivery | GitHub/GitHub Actions, Workload Identity Federation, exact-commit candidate checks and controlled promotion. |
| Verification | pytest, Node tests, browser QA, Cloud Logging and JSON release receipts; PR #19 adds synthesized input/output speech verification. |

The map can use approximate/ZIP-centroid coordinates. Pickup timing is a profile estimate. The system proposes matches and drafts outreach; it does not confirm capacity, dispatch messages, reserve pickups or establish completed deliveries.
