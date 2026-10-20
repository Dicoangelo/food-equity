# Offer clocks and cancellable match progress

This branch fixes the two reproduced decision bugs from the Google platform review and adds progress tied to real backend stages. A donor name, description, storage or quantity edit no longer resets the pickup clock. Unrecognized schedule qualifiers no longer count as known receiving availability.

## Integration state

- Branch: `codex/coherent-offer` in the isolated `food-equity-workflow-lane` checkout.
- Rebased onto `e48bd62`, including team main `90eb4cc` and the PR #13 candidate-tag/artifact fixes. The main checkout was not changed. The separate context-agent lane is excluded.
- Dico authorized integration and live release on 20 October. The main build chat remains the single release owner and has reserved merge/promotion until its first CI release is confirmed or it explicitly hands ownership over.
- This slice contains strict schedule interpretation, the shared offer clock, real progress/cancellation and pure-ASGI telemetry compatibility. These belong together: the earlier telemetry wrapper prevents disconnect cleanup on one ASGI path.
- CI runs all three Node suites. The exact-candidate verifier must exercise the browser's stream and same-origin script before promotion, alongside the existing JSON refusal, data and Live gates. This document is not a deployment receipt.

## User-visible behavior

The first validated match/voice context fixes an absolute pickup deadline. Retries and non-clock edits retain it. An explicit pickup-window edit, a new scenario or an applied, reviewed voice deadline can replace it. Expiry blocks a retry until the donor's window is explicitly updated. This is in-memory page state; reloads do not restore an offer.

The browser uses `POST /api/match/stream`. It shows accepted, connecting, retrieving, checking constraints, preparing a brief when a candidate passes, and finalizing as the corresponding work happens. Candidate counts describe the retrieved shortlist, not the entire directory. Progress never authorizes a handoff; only the terminal broker result determines the outcome.

Cancel closes the browser request, clears any pending result and unlocks the form. A result arriving during cancellation is discarded. Retry preserves the fixed deadline. Browser transport failure or timeout leaves an explicit recoverable state. The existing JSON `POST /api/match` contract remains available.

## Backend contract

Both endpoints validate the same request and derive remaining hours from the fixed deadline before the broker starts. `stream_match` emits NDJSON records with a request-scoped UUID and increasing sequence. Only allowlisted stage names and bounded integer counts cross the progress channel; exception messages and arbitrary tool text do not.

The stream sends an initial accepted event before starting provider work, then bounded queued progress and 15-second heartbeats. A terminal result contains the original broker payload. `match_failed` and `match_timeout` are sanitized terminal error codes. `MatchStreamingResponse` explicitly closes the generator when response sending is interrupted, covering both ASGI disconnect mechanisms. Request telemetry now forwards ASGI events directly so it does not replace the stream lifecycle; its existing request-ID, safe log fields and time-to-headers measurement remain intact.

Budgets are 150 seconds for the broker operation, 2 seconds for connection cleanup and 1 second for optional progress listeners. The stream has a 160-second response budget and 1-second producer cleanup; the browser aborts at 175 seconds. Cancellation is cooperative. It stops local tasks when the SDK honors cancellation; it does not prove that an already-submitted remote job stopped. There is no durable/background job or automatic retry in this slice.

Schedule interpretation now matches complete known formats. Daily and weekday aliases, full weekday lists and supported ordinal monthly schedules remain recognized. Negation, appointments, unsupported monthly qualifiers, time ranges and mixed instructions return unknown and require confirmation. Tests cover all 12 starter schedules across October 2026.

The ADK/MCP/Gemini flow, deterministic gates, ranking and final deadline recheck remain in place. Moving mandatory retrieval ahead of model orchestration, widening retrieval recall, authenticated callers, durable offer versions, idempotency and persisted jobs remain separate follow-up work. No Google service, billing or IAM setting was added here.

## Verification

The full post-rebase Python suite passed: **328 tests**, with the existing Starlette/httpx deprecation and ADK experimental-schema warnings. The three offline Node suites passed: **41 tests/subtests**, including 39 dedicated clock/stream tests plus the UI and recipient-explorer suites. The optional Playwright fixture script was adapted for the new asset/transport and syntax-checked; it was not executed by this pass.

The local browser fixture used the actual FastAPI stream endpoint with a fake broker. Progress appeared before completion, Cancel restored the form, and server receipts recorded one canceled worker and one completed edited retry with identical absolute deadlines. No live model, microphone or cloud operation was called. Screenshots and receipts are in `../../research/google-platform-review/evidence/workflow/` relative to this checkout. The final browser check includes the telemetry integration. Full-app ASGI 2.3 and 2.4 disconnect tests also pass, alongside request-ID, body forwarding, privacy and WebSocket bypass checks.

Research context: [Google infrastructure NotebookLM](https://notebook.google.com/notebook/92b87f78-ea19-43b8-831f-8fa7c9e1b3b9), containing the Google platform sources and prior review. The prior review is a dated baseline; use this document for implementation status.
