# Recipient evidence and coordinator flow

The broker already uses ADK/Gemini → MCP Toolbox → BigQuery vector search → deterministic feasibility checks. This slice adds an explicit exact-ID source read when a coordinator inspects a profile, alongside a clearer offer and result flow.

## Interface

The offer form comes first. Example offers are optional and collapsed. The map sits in the results column beside the form. A valid request focuses the review area immediately, with Cancel, the fixed Seattle-time deadline, and only progress stages actually received from the backend. Cancellation retains the clock and received activity; it does not create a result. Error/cancel returns keyboard focus to the review area.

A profile includes **Refresh source evidence**. Opening or filtering profiles remains local. Refresh explicitly calls `GET /api/recipients/{recipient_id}/evidence`, which discovers and calls `get_recipient_evidence` through the existing loopback Toolbox. The parameterized query reads selected fields from `a4i_food.recipient_embeddings`; a maximum of two rows permits duplicate detection. No embedding, contact, arbitrary SQL, write or model call is exposed.

The fresh read appears separately from the earlier evaluated proposal. It never changes the match, score, offer or deadline. Retrieval time is not a source observation time or confirmation. Generated/planted profiles keep their provenance; current availability remains unconfirmed. The user must rerun matching to evaluate source changes against the fixed offer clock.

Responses distinguish ready, missing, ambiguous/invalid and unavailable evidence. The service bounds discovery, query, payload and cleanup. The browser cancels on close/selection change and rejects a late or mismatched recipient response. No automatic retry or refresh loop is added.

## Verification and release

Full Python suite: 447 passed at the combined checkpoint; final focused verifier tests also passed after additional cases. All three Node suites passed (41 tests/subtests). Browser fixture checks covered desktop 1512px and phone 390/320px, form-first/map placement, actual progress, Cancel, corrected retry with the same deadline, profile refresh, unknown provenance fields and no horizontal overflow. Fixtures make no paid model or cloud calls; they are not evidence of live MCP success. Screenshots and fixture server are in `../../research/evidence-flow-*`.

The release verifier now requires one real exact-ID MCP evidence read using an ID from its existing city response, before its two existing model requests. A failed source read prevents traffic promotion. Existing keyless authentication, exact commit/tag/revision guards and runtime/traffic drift checks remain in place.

Separate context Q&A/interruption refinements and the broader reference-driven UI redesign are review-only work and are not part of this slice.
