# A2A Rescue Exchange — product and implementation proposal

**Status:** design only; not implemented or deployed. **Reviewed:** 20 October 2026.
**Track:** Seattle retail and grocer rescue. **Decision:** finish and verify the current matching bottlenecks before integrating this extension.

## Outcome and scope

A coordinator posts a surplus offer once. The broker finds eligible recipients, asks recipient and transport agents for time-limited proposals, and presents one executable plan for operator approval. After a future real operation, evidence could establish what was actually delivered. The innovation is closing the loop from semantic discovery to accountable coordination.

The first prototype demonstrates that loop with **real A2A messages and clearly labeled simulated partner agents**. It sends no email or SMS, books no vehicle, reserves no real inventory, enters no contract and transfers no money. A demonstration receipt is never represented as proof that physical food was rescued.

The supplied recipient operating profiles are generated or planted. Existing names, vector similarity and a valid constraint score do not establish an organization's participation, consent, available capacity or current opening hours. The exchange must keep that provenance visible.

## Starting point and integration gates

The existing path is `FastAPI → ADK/Gemini → custom broker tool → MCP Toolbox → BigQuery vector search → deterministic constraints → Match Brief`. It already uses Vertex and real MCP retrieval. Keep this path, its fixed donor deadline and its deterministic storage/capacity/time gates. A2A connects independently addressable agents; MCP remains the broker's tool interface.

The service is authenticated Cloud Run in `qwiklabs-gcp-04-b992b9561e32`, region `us-east1`; the BigQuery dataset is `a4i_food` in `US`. The current deployment configuration permits multiple instances. Request-scoped ADK memory is not a shared exchange ledger. Neither Cloud Run local disk nor an in-memory task store establishes durable one-time approval across replicas.

| Phase | Deliverable | Exit condition |
|---|---|---|
| 0 — working bottleneck | Current broker, mobile UI, trace, factual brief, FoodKeeper reference and replay audit | Positive match and honest refusal both pass the live ADK/MCP path; rejected candidates never return as alternatives; times and provenance remain factual |
| 1 — isolated protocol prototype | Fixed recipient/transport fixture agents; real A2A request/response; ephemeral, single-process simulation | SDK interoperability, expiry, approval and evidence tests pass; explicit reset/loss-of-state warning; no external side effects |
| 2 — authenticated integration | Mount protocol adapter beside existing API; verified actor identity; durable transactional exchange state | Cross-replica isolation, concurrency, recovery and replay tests pass; Cloud Run IAM remains enforced |
| 3 — partner pilot | Consenting partner endpoints, operational verification, bounded reservation/dispatch adapters | Partner onboarding and authorization, fulfillment/evidence procedures, failure compensation and operator controls are established |
| 4 — optional sponsored program | Separately funded, defined sponsor offer and controlled redemption workflow | Sponsor terms, budget, eligibility, settlement/reconciliation and applicable obligations are independently verified |

A later phase is not implied by completing an earlier phase. No new API, database, IAM grant, payment account or external partner integration is authorized by this document alone.

## Protocol and dependency baseline

Use **A2A protocol 1.0** and the official **Python `a2a-sdk[fastapi]==1.2.3`**, released 19 October 2026. The SDK declares Python 3.10 or newer. Its release and pinned source are authoritative for implementation; the repository currently uses Python 3.12. The additional SDK dependency has **not** been installed or compatibility-tested in this project. Resolve it in an isolated build and run the full existing suite before changing the lock or container. [Official SDK release](https://github.com/a2aproject/a2a-python/releases/tag/v1.2.3), [SDK dependency declaration](https://github.com/a2aproject/a2a-python/blob/v1.2.3/pyproject.toml).

Use JSON-RPC first: `SendMessage`, `GetTask`, `CancelTask`, and `ListTasks` where supported by the request handler. Send `A2A-Version: 1.0`. Advertise no streaming, push notifications or extended card until those features exist. The 1.0 card describes endpoints in `supportedInterfaces`; its `protocolVersion` belongs to each interface. Avoid the older `message/send`, top-level card `url`, `kind` discriminators and `blocking` configuration examples. [Version changes](https://a2a-protocol.org/latest/whats-new-v1/), [SDK constants](https://github.com/a2aproject/a2a-python/blob/v1.2.3/src/a2a/utils/constants.py).

Implementation seam:

```python
# Future adapter; this PRD does not add these imports to the running app.
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor
```

Implement `AgentExecutor.execute(context, event_queue)` and `cancel(context, event_queue)`. Use SDK task/status/artifact handling rather than handwritten JSON-RPC dispatch. The pinned route factory is `create_jsonrpc_routes(request_handler, rpc_url, context_builder=None, enable_v0_3_compat=False, shutdown_grace_period=0)`; keep legacy compatibility disabled for this prototype. Mount the resulting routes using `add_a2a_routes_to_fastapi`. [Official server tutorial](https://a2a-protocol.org/latest/tutorials/python/5-start-server/), [route exports](https://github.com/a2aproject/a2a-python/blob/v1.2.3/src/a2a/server/routes/__init__.py), [JSON-RPC factory](https://github.com/a2aproject/a2a-python/blob/v1.2.3/src/a2a/server/routes/jsonrpc_routes.py), [executor interface](https://github.com/a2aproject/a2a-python/blob/v1.2.3/src/a2a/server/agent_execution/agent_executor.py).

## AgentCard contract

This is a **proposed**, schema-shaped card for the future adapter; the URL does not currently promise an A2A endpoint. The root card at `/.well-known/agent-card.json` remains behind existing Cloud Run IAM. Private discovery uses a configured card address, not internet crawling. Fixture partner cards have distinct configured addresses and explicitly identify their simulation role. [Agent discovery](https://a2a-protocol.org/latest/topics/agent-discovery/).

```json
{
  "name": "Seattle Rescue Exchange — simulation",
  "description": "Coordinates simulated recipient and transport proposals. Does not contact organizations, reserve inventory or make payments.",
  "version": "0.1.0",
  "supportedInterfaces": [{
    "url": "https://seattle-food-broker-6ukbp746xa-ue.a.run.app/a2a",
    "protocolBinding": "JSONRPC",
    "protocolVersion": "1.0"
  }],
  "capabilities": {
    "streaming": false,
    "pushNotifications": false,
    "extendedAgentCard": false
  },
  "securitySchemes": {
    "cloudRunIdToken": {
      "httpAuthSecurityScheme": {
        "scheme": "Bearer",
        "bearerFormat": "JWT",
        "description": "Google-signed identity token accepted by the existing Cloud Run IAM configuration."
      }
    }
  },
  "securityRequirements": [{
    "schemes": {"cloudRunIdToken": {"list": []}}
  }],
  "defaultInputModes": ["application/json"],
  "defaultOutputModes": ["application/json"],
  "skills": [
    {
      "id": "quote_rescue",
      "name": "Propose a rescue plan",
      "description": "Collect time-limited fixture quotes for an eligible offer.",
      "tags": ["food-rescue", "simulation"]
    },
    {
      "id": "execute_demo_plan",
      "name": "Walk through an approved simulated plan",
      "description": "Request explicit operator approval and validate fixture delivery evidence.",
      "tags": ["operator-approval", "simulation"]
    }
  ]
}
```

The card's names, skill IDs and descriptions are application choices. The enclosing fields, nested security scheme and interface fields follow the canonical 1.0 definitions. Build and serialize this through SDK types. The three JSON examples in this document were parsed successfully against the official 1.2.3 protobuf descriptor, with unknown fields rejected; this checks their shapes, not a running server or dependency compatibility. A card is descriptive metadata, not an authentication mechanism or evidence of partner consent. [Canonical protocol definitions](https://github.com/a2aproject/A2A/blob/v1.0.0/specification/a2a.proto), [pinned SDK descriptor](https://github.com/a2aproject/a2a-python/blob/v1.2.3/src/a2a/types/a2a_pb2.py).

## Message, task and artifact contract

The A2A envelope carries protocol identity and lifecycle. All rescue-specific fields live inside a structured `Part.data` object, validated against our versioned application schema. They are **not new A2A standard fields**. The application schema identifier below is an internal identifier, not a claim of an approved A2A extension.

Example `SendMessage` request body, with `Content-Type: application/json` and `A2A-Version: 1.0`. The scenario timestamp is illustrative; production acceptance always checks the actual server clock.

```json
{
  "jsonrpc": "2.0",
  "id": "rpc-demo-001",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "msg-demo-001",
      "role": "ROLE_USER",
      "parts": [{
        "mediaType": "application/json",
        "data": {
          "schema": "urn:metaventions:rescue-exchange:1",
          "operation": "quote_rescue",
          "idempotency_key": "offer-demo-001-v1",
          "offer": {
            "offer_id": "offer-demo-001",
            "version": 1,
            "description": "Sealed shelf-stable bread",
            "quantity": {"value": 2, "unit": "pallet"},
            "storage_required": "ambient",
            "deadline_at": "2026-10-21T16:00:00Z",
            "mode": "simulation"
          }
        }
      }]
    },
    "configuration": {
      "acceptedOutputModes": ["application/json"],
      "historyLength": 0,
      "returnImmediately": false
    }
  }
}
```

The server assigns the new task `id` and context. A quote task may complete after producing proposals: `COMPLETED` here means the quote request finished, not that a delivery happened. A separate execution task in the same `contextId`, with `referenceTaskIds` referencing the quote task, pauses for operator approval.

Illustrative response to that execution task:

```json
{
  "jsonrpc": "2.0",
  "id": "rpc-demo-002",
  "result": {
    "task": {
      "id": "task-demo-execution-001",
      "contextId": "context-demo-001",
      "status": {
        "state": "TASK_STATE_INPUT_REQUIRED",
        "timestamp": "2026-10-20T17:00:00Z",
        "message": {
          "messageId": "msg-demo-approval-request-001",
          "role": "ROLE_AGENT",
          "parts": [{"text": "Review and approve the exact simulated plan before continuing."}]
        }
      },
      "artifacts": [{
        "artifactId": "artifact-demo-plan-001",
        "name": "Proposed simulated rescue plan",
        "parts": [{
          "mediaType": "application/json",
          "data": {
            "schema": "urn:metaventions:rescue-exchange:1",
            "artifact_type": "rescue_plan",
            "plan_id": "plan-demo-001",
            "plan_version": 1,
            "mode": "simulation",
            "approval_required": true,
            "external_actions_performed": []
          }
        }]
      }]
    }
  }
}
```

Follow-up `SendMessage` includes both `message.taskId` and `message.contextId`. `GetTask` uses `params: {"id": "task-demo-execution-001", "historyLength": 0}`. Reusing a terminal task is invalid: a correction or new attempt gets a new task and may reference the old one. Business approval uses `TASK_STATE_INPUT_REQUIRED`; `TASK_STATE_AUTH_REQUIRED` is reserved for missing authentication, not a synonym for an operator decision. [Task lifecycle](https://a2a-protocol.org/latest/topics/life-of-a-task/), [JSON-RPC and message semantics](https://a2a-protocol.org/latest/specification/).

| Application milestone | A2A state | Required meaning |
|---|---|---|
| Accepted request / collecting quotes | `TASK_STATE_SUBMITTED` / `TASK_STATE_WORKING` | Processing has started; no commitment |
| Quote report ready | `TASK_STATE_COMPLETED` | Proposal artifact exists, including a valid no-offer report |
| Plan needs decision | `TASK_STATE_INPUT_REQUIRED` | Exact immutable plan shown to operator |
| Simulated authorization consumed | `TASK_STATE_WORKING` | Only the approved simulation transition is permitted |
| Delivery evidence or review missing | `TASK_STATE_INPUT_REQUIRED` | No impact credit is earned yet |
| Evidence checks finished | `TASK_STATE_COMPLETED` | Verification report exists; report separately states whether evidence was accepted and whether it is simulated |
| Offer/quote expired or plan disallowed | `TASK_STATE_REJECTED` | Artifact gives reason and next possible action |
| Operator cancels | `TASK_STATE_CANCELED` | No new work; record any simulated transitions already performed |
| Tool/protocol/storage failure | `TASK_STATE_FAILED` | Honest failure with retriable/non-retriable reason; no fabricated success |

Application fields such as `quote_expired`, `awaiting_delivery_review` and `simulation_verified` belong in artifacts, not custom task-state enum values.

## Application records and deterministic decisions

| Record | Required data and invariant |
|---|---|
| Offer | Stable ID/version, immutable content hash, authenticated owner, quantity with unit, storage, fixed UTC deadline, source and simulation flag; any substantive edit creates a new version |
| Recipient quote | Offer hash, partner ID, quote ID/version, expiry, accepted quantity/unit, storage capability, receiving window, confirmation basis and operational provenance |
| Transport quote | Offer hash, partner ID, quote ID/version, expiry, load capacity/unit, storage capability, pickup and arrival estimates with uncertainty/source, proposed cost with currency or explicitly unknown |
| Plan | Offer/quote versions and hashes, selected recipient/transport, allocation quantities, timing and buffer, required confirmations, fallback candidates and reasons; no hidden model-selected action |
| Authorization | Verified actor, plan hash/version, precise allowed transition, server-issued nonce, expiry, idempotency key, consumed timestamp; never trust a client-supplied actor label |
| Delivery evidence | Plan reference, measured accepted quantity/unit if available, pickup/receipt times, donor/recipient attestations, evidence hashes, source and reviewer result; contradictory or incomplete evidence stays unresolved |
| Impact receipt | Unique delivery/line references, accepted measurements, verification level, rule version, evidence references, simulation flag; separate estimates and incentive ledger references |

The planner applies hard constraints before ranking. A recipient quote cannot override the donor deadline or manufacture cold-chain evidence. A matching storage label is not a food-safety clearance. Pickup/arrival intervals must leave the configured coordination buffer; distinguish raw time margin from buffer-adjusted slack. Missing or expired operational confirmation is unresolved, not a positive commitment.

Do not silently convert pallets, crates, loaves or gallons to kilograms. Keep FoodKeeper's original product state and timing basis; a reference shelf duration never becomes remaining freshness. Missing product state, package date or storage history remains unknown.

The model may explain a validated plan or draft a bounded message. It cannot mint partner authority, change eligibility, approve actions, revise deadlines, invent evidence, assign money or choose arbitrary network destinations. Sponsor generosity must not become a hidden ranking advantage for recipients.

## Approval, idempotency and expiry

1. **Approval binds the plan.** Display quantities, recipients, transport, clock, confirmations, simulation status and any hypothetical incentive. Approval references the canonical plan hash and exact versions. Selecting a new recipient, changing the route, quantity or incentive, or refreshing an expired quote requires a new plan and approval.
2. **One use means one transition.** Atomically check owner, current plan, nonce, expiry, unused authorization and expected state before consuming the authorization. A duplicate returns the prior receipt; it never starts a second simulated dispatch. A2A request/message IDs alone are not a business idempotency guarantee.
3. **Identity scopes every read and mutation.** Scope task, context, quote, authorization and evidence access by a server-verified principal. Referencing another user's task ID must not reveal it. Metadata such as `user_id` does not establish identity.
4. **Time is checked at action time.** Use timezone-aware server timestamps. Bound quote TTL by the offer deadline and require pickup/arrival feasibility at selection, approval and execution. A sleeping/retried request cannot consume expired authority.
5. **No uncontrolled retry.** Retry safe reads with bounds. Mutations use a stable idempotency key and compare the stored payload hash; the same key with different content conflicts. Future real side effects require a durable outbox plus downstream idempotency or an explicit reconciliation workflow.
6. **Fallback is a proposal.** If a partner declines or times out, show the next eligible plan and ask for new approval. Broader preauthorization of substitutions is a later feature requiring visible limits; it is not assumed here.

Phase 1 uses a lock-protected in-memory fixture store inside one process and labels restart loss explicitly. It demonstrates invariants only within that lifetime. Phase 2 needs a shared transactional ledger with unique keys, compare-and-set/transactions and retention rules. Pick the store after verifying existing project resources, permissions and cost constraints; do not enable a new database speculatively. SDK `InMemoryTaskStore` is not that ledger. [Pinned in-memory implementation](https://github.com/a2aproject/a2a-python/blob/v1.2.3/src/a2a/server/tasks/inmemory_task_store.py).

## Access and network boundaries

Keep the existing Cloud Run Invoker/IAM policy. Do not make the AgentCard or `/a2a` public to simplify a demo. A service caller uses a Google-signed ID token for the configured service audience; do not put tokens in cards, artifacts, client bundles, logs or repository files. [Cloud Run service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).

The SDK default context builder uses the ASGI request's authenticated `user`; if missing, it supplies an unauthenticated user. Cloud Run admission by itself does not populate that SDK identity object. A verified principal adapter is a required integration gate, including the existing Cloud Shell preview/proxy path. Never decode an unverified JWT and treat its claims as identity, or rely on a caller-controlled email header. Until the adapter is tested, keep the protocol prototype isolated from shared live task state. [Pinned SDK context builder](https://github.com/a2aproject/a2a-python/blob/v1.2.3/src/a2a/server/routes/common.py).

Partner destinations come exclusively from an operator-owned, fixed configuration. The first prototype uses named loopback fixture endpoints; real pilot endpoints require separate onboarding. No URL supplied in a message, card response, evidence field or model output is fetched. Disable redirects and push-notification callbacks; reject unsupported URL/file parts rather than dereferencing them. Restrict any future external integration to an explicit HTTPS host/port allowlist with private/metadata address protections and bounded payload/time limits. The fixed local fixture exception never allows caller-selected localhost ports.

Use JSON data parts only for the first prototype. Validate finite quantities, enumerations, lengths and timestamp formats; cap input/output size, partner count and task lifetime. Render untrusted text safely. Keep raw credentials, contact details and precise personal locations out of traces. A timeout, cancellation or partial partner failure must produce an explainable result with no claimed reservation.

## Evidence and the incentive concept

Keep four separate quantities:

| Layer | What may be recorded | What it must not imply |
|---|---|---|
| Physical impact | Measured, accepted delivery quantity in its original unit, evidence status and attributable recipients | A proposed match or uploaded hash is not a verified delivery |
| Estimated impact | Clearly labeled meal/CO₂ estimates with assumptions, factors, source and version | No certified carbon credit or exact meal count from an unsupported conversion |
| Internal impact units | Noncash, nontransferable program score under an explicit versioned rule, only for accepted evidence | No cash value, legal entitlement, guaranteed conversion or trading market |
| Sponsor program | Separate currency-denominated budget/pledge, eligibility, proposed stipend and settlement status | No payment until a funded program and authorized payout process exist |

The prototype's physical delivered quantity is **unknown**, not simulated kilograms added to real totals. Fixture evidence may produce a `simulation_verified` report and demo-only score. It must never increment a real impact ledger. Recipient confirmation, donor confirmation and any independent measurement are separate evidence assertions; provenance and review determine the verification level.

Prevent duplicate awards across repeated messages, retries, split loads and multiple agents by linking each accepted allocation to a unique offer line and delivery receipt. Reconcile partial delivery against planned quantities. Corrections create linked adjustments; they do not silently overwrite the original evidence.

The desired “convertible credits” become a **future sponsor redemption proposal**: a sponsor might offer a defined stipend against an eligible, verified delivery under a funded agreement. Until then, show “illustrative incentive — no cash value or payment.” A sponsor pledge is not available money; proposed, authorized, settled, failed and refunded amounts belong in a distinct ledger. No payment SDK, token, wallet, automatic transfer or real sponsor commitment is included.

Tax deductions, public subsidies, government benefits and certified carbon instruments are **unverified future research topics**, not outputs or promises. Any future program requires its actual jurisdiction, eligibility rules, evidence, approving authority and current primary-source review. This document makes no assertion that a Seattle donation qualifies for one.

## Smallest convincing demo

1. Post one Seattle bakery offer; display the existing real MCP/vector/constraint trace and generated-profile disclosure.
2. Request proposals from two named fixture recipient agents and one fixture transport agent using actual A2A HTTP calls. Show their cards, task IDs, quote expiry and structured artifacts.
3. Reject one incompatible or expired quote deterministically. Show a viable plan with the deadline, buffer and the exact unresolved confirmations.
4. Have the operator approve that specific **simulated** plan. Replay the same approval to demonstrate the same receipt and no second dispatch.
5. Inject fixture receipt evidence; show accepted quantity in its original unit, evidence checks and a conspicuous simulation label.
6. Show a demo impact receipt and a separate illustrative sponsor stipend with **zero real payout**. Expire a quote or change the plan and demonstrate that approval must be renewed.

Time-box the walkthrough to approximately three minutes. Actual recipient participation, confirmed routes, physical delivery, cash settlement and real government benefits remain outside the claim. A direct Python function call between local agents must not be presented as proof of A2A interoperability.

## Bounded implementation plan

| Proposed file | Responsibility |
|---|---|
| `agent/exchange_models.py` | Strict application records and JSON schema; no protocol envelope reimplementation |
| `agent/exchange_policy.py` | Plan eligibility, hashes, expiry, approval scope, evidence and impact rules |
| `agent/exchange_store.py` | Store interface and explicitly ephemeral fixture implementation; durable backend remains gated |
| `agent/a2a_exchange.py` | Official SDK executor, AgentCard, request-context adapter and route assembly |
| `agent/demo_partner_agents.py` | Clearly named recipient/transport fixture agents at fixed local endpoints |
| `scripts/demo_a2a_exchange.py` | Real SDK client walkthrough with bounded polling and deterministic fixtures |
| `tests/test_exchange_policy.py` | Clock, idempotency, approval, evidence and unit-accounting invariants |
| `tests/test_a2a_exchange.py` | Real SDK envelope/route interoperability and actor isolation |

Later integration adds one feature-flagged mount in `agent/api.py` and the pinned SDK extra in `requirements.txt`, only after Phase 0 passes and dependency compatibility is tested. Preserve starter data/notebooks and the current matcher/MCP implementation. No separate “smart contract,” blockchain, payment integration, autonomous web discovery or generic workflow engine is needed for the demonstration.

## Acceptance criteria before integration

- Existing broker tests and live positive/no-match checks still pass. The exchange cannot bypass deterministic constraints, move donor deadlines, erase provenance or reintroduce rejected alternatives.
- The official 1.2.3 client reads the card and performs `SendMessage`, `GetTask`, `CancelTask` and owner-scoped task listing against the actual route implementation. Schema parsing rejects legacy/malformed envelopes; unsupported optional capabilities fail explicitly.
- Terminal tasks cannot restart; follow-ups use the correct task/context IDs, and new attempts reference prior terminal tasks. Cancellation and failure never appear as completed deliveries.
- Concurrent duplicate approvals cause one transition; a repeated identical request returns the existing receipt. Reusing a key with changed content conflicts. Changed plans, expired quotes, expired approvals and mismatched owners cannot proceed.
- Quotes expiring during retrieval, operator review or retry are rejected at the next transition. Pickup/arrival uncertainty and buffer calculations remain explicit.
- An unauthenticated request fails at the preserved Cloud Run boundary. The principal adapter passes two-actor task isolation and forged-header/metadata tests. A preview proxy cannot accidentally collapse all operators into one authorization identity.
- No caller-controlled URL, redirect, callback, file reference or model-generated destination causes a network request. Only configured fixture endpoints receive requests; all external effects remain disabled.
- Missing, contradictory, duplicate and excessive-quantity evidence creates no award. Partial deliveries preserve original units and cannot exceed the accepted allocation. Fixtures never alter real impact or financial totals.
- The UI shows simulated partners, proposal/confirmation status, exact approval scope, timestamped evidence, estimated versus measured quantities, and no-value demo incentives.
- Before Phase 2, restart and multi-replica tests prove durable state, unique authorization consumption, task ownership, outbox/reconciliation semantics and retention. A passing single-process demo does not satisfy this gate.

## Open decisions and next action

Open: actual partner availability and consent; verified operator identity through the current preview path; durable store permitted by the lab; recipient/transport quote schemas and attestation policy; usable routing/travel-time source; food-handling review; official reuse policy; sponsor terms and budget; evidence retention and dispute handling.

**Single next action:** complete the current live broker/audit acceptance checks, then review this PRD and implement only the isolated Phase 1 protocol simulation. Keep the existing deployed broker stable while that prototype is evaluated.
