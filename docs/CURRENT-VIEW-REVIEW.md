# Current-match context and action review

20 October 2026 · Local review candidate · No deployment or provider validation

This increment adds bounded questions about the displayed match and selected recipient, shared source-status semantics, and turn-owned action receipts. It preserves the existing broker, deterministic constraints, MCP retrieval, explicit Apply → Run flow and absolute donor deadline.

## Isolation and scope

The work is isolated on `codex/context-agent` in the sibling `food-equity-context-lane` checkout, based on `b6586cd8f807239b489a72f7f72b774d4e9e6254`. The checkout's push URL is disabled. Delivery work subsequently merged on the team repository is not included; integration must review its API, telemetry and verification changes rather than replacing those files wholesale.

Integration overlap: a parallel approved workflow lane is introducing a shared `OfferClock` helper for the existing pickup-clock defect. This branch makes only a local invariant correction: quantity, wording and storage edits retain the exact deadline, while explicit pickup-window edits and a new scenario reset it. Eventual integration must keep the shared clock helper and port the context/restart hooks and regressions onto it; do not restore the legacy `buildRequest` implementation from this branch.

No deployment, Google Cloud API request, real model call, microphone capture, production data change, dependency upgrade or new credential was used to validate this increment. The local browser fixture may load the application's existing public style and map assets; its broker/data responses are synthetic. No upstream implementation was copied. This is an original bounded implementation with no share links, 3D scenes, automated navigation, outreach, reservation, dispatch or payment.

## Context is a presentation projection

`agent/view_context.py` accepts a strict, bounded browser projection. It is **not an authenticated broker result**. Every read returns `authority=untrusted_browser_projection`, an explicit origin limitation and `operational_availability=unconfirmed`. A malicious browser can submit coherent but false display facts. Schema validation prevents malformed or oversized input; it does not prove those facts.

The model receives a small selection summary, then reads only the supplied projection with:

- `read_current_match`: current reported evaluation and its limitations.
- `read_recipient_evidence`: an exact canonical recipient ID in the snapshot, including a city-only `not_evaluated` profile.
- `show_alternatives`: existing reported alternative order. This completes a read; it does not change the map, open a panel, rerank or retrieve candidates.

There are no result-reference tokens or server result store. Unexpected references and fields are rejected. A missing evaluation, failed retrieval, changed offer and expired donor deadline have distinct refusal/stale outcomes. Names are never used to resolve identity. The projection carries at most 16 candidate excerpts and 50 visible IDs, with explicit truncation; outgoing UI context is limited to 48 KiB within a dedicated 64 KiB protocol bound.

Organization text and HTML remain data rendered through text nodes. Model commentary is labeled separately from deterministic action receipts. Neither commentary nor browser-projected eligibility can invoke the matcher or authorize an operation.

## One source-state contract

Python and browser normalizers share `ready`, `partial`, `stale` and `unavailable`, plus source kind, retrieval time, expiry, cache state, coverage, provenance and a bounded error code. All state pairs are tested. Partial coverage is retained; a healthy layer with an unavailable layer is partial; any stale layer keeps the aggregate stale.

`fetched_at` is a retrieval timestamp, not a source observation or operational confirmation. The current source has neither observation nor confirmation dates, so both remain null. Generated/planted profiles remain unconfirmed even after a fresh retrieval. A match result's expiry is its donor deadline and describes evaluation validity, not the age of the recipient data.

Map, selected profile and Live read payloads use this contract. The browser schedules a local expiry repaint, without a fetch, and increments the view revision. Backend reads also recheck expiry without mutating captured state. Failed refreshes retain the existing application's explicit missing-layer behavior; they do not silently present cached missing data as fresh.

## Action ownership and interruption

Each server-issued turn binds `turn_id`, `expected_offer_revision` and `expected_view_revision`. Every tool call must echo that owner. A per-connection registry accepts at most 30 call IDs: an identical replay returns a cached result without another effect or UI acknowledgement; changed arguments under the same ID are refused.

Action results contain a bounded call ID, captured ownership, an outcome (`applied`, `refused`, `cancelled` or `stale`), effect, resulting revision and a static acknowledgement. `draft_proposed` means only the draft changed. It does not mean the form was applied, matching ran or a recipient accepted food. `read_only` means a local projection was read, with no external effect.

Interruption is processed before transcript, audio or tool calls, including when they share one provider envelope. Completed, stale and cross-session owners cannot mutate drafts. The browser checks ownership and current revisions again before showing receipts or accepting a draft. Applying a proposal still requires the existing explicit UI action, and running matching remains a separate action.

Google Live output audio and prose do not carry this application's own turn ID. To avoid assigning late output to a successor, this implementation closes the session on interruption, a view change during a turn, or overlapping typed turns. An offer revision change also closes an idle session, because form context is captured once and must not silently retain an older donor, location or deadline. The user starts a fresh session to continue; the retained form deadline is preserved. This deliberately bounded behavior needs live usability review before any attempt to support seamless barge-in.

## Verification and remaining release gates

Final local validation: **296 Python tests passed**, all five Node suites passed, and fixture-browser widths **320, 390 and 1440 passed** with no horizontal overflow or unexpected matching calls. Tests exercise forged projections, unknown dates, source expiry, city-only profiles, stale offers, duplicate and changed call IDs, cross-session ownership, interruption-plus-tool events, late output, explicit UI effects and deadline preservation. Python reported two existing dependency warnings (Starlette's TestClient transport deprecation and ADK's experimental JSON-schema declaration flag); there were no test failures.

From this checkout:

```sh
../food-equity/.venv/bin/python -m pytest -q
node tests/test_source_state.js
node tests/test_view_context.js
node tests/test_view_transport.js
node tests/test_ui_contract.js
node tests/test_recipient_explorer.js
```

The browser fixture uses Playwright from the local Codex runtime and an isolated installed Chrome process; it does not reuse an authenticated browser profile:

```sh
NODE_PATH='/Users/dicoangelo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' \
CHROME_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
QA_OUTPUT_DIR='/Users/dicoangelo/Documents/New project/outputs/google-impact-toolkit-2026-10-20/research/context-feature-qa' \
node tests/test_recipient_explorer_browser.cjs
```

Preview evidence is saved in `../../research/context-feature-qa/` as `evidence-320.png`, `evidence-390.png`, `evidence-1440.png`, and corresponding `profile-` and `directory-` images. These are synthetic fixture previews. The separate review receipt records all three commit hashes after the final commit is created.

Local fixtures do not establish real microphone quality, new Live model tool adherence, deployed WebSocket compatibility or source truth. Before integration: review the final diff, reconcile delivery/telemetry changes, then separately authorize and test a candidate deployment. No automatic merge or deployment is part of this review candidate.
