# Food Equity review components

These components adapt the three quick wins to existing production intake and match payloads. They make no network requests, change no ranking or model settings, and expose no sending action. The Google Cloud owner wires them into `agent/api.py` on the combined preview branch.

## Files and initialization

Serve `/ui/intake-review.js` and `/ui/intake-review.css` with the existing static-file route pattern. Load the script before the host creates the controller. Create three containers in the existing intake, comparison and handoff surfaces; replace duplicate comparison/copy surfaces instead of adding a second workflow.

```js
const quickReview = FoodEquityReview.create({
  snapshot: () => ({
    offer: {
      posting_text: /* current text field value */,
      donor_name: /* current donor name */,
      pallet_count: /* number or empty string */,
      required_storage: /* current storage value */,
      hours_remaining: /* current hours field value */,
      donor_lat: /* current origin latitude */,
      donor_lon: /* current origin longitude */,
      deadline_at: offerClock.deadline,
    },
    result: currentPayload,
    sources: /* current canonical source-state array */,
    locked: matchInFlight,
    revision: offerRevision,
  }),
  containers: {intake: intakeContainer, comparison: comparisonContainer, handoff: handoffContainer},
  onChange: state => {
    document.getElementById('btn-alert-apply').disabled = !state.canApply;
  },
});
```

The example is an adapter contract, not a standalone paste-in program. Read the DOM and existing clock without calling `buildRequest()`: reading review state must never create a deadline. Pass plain JSON snapshots, not `ViewContext.snapshot()`, DOM nodes, or functions. `locked` is an immediate action guard but excluded from ownership fingerprints. Use a monotonic offer revision for source/form edits, not map or focus changes. Do not call `refresh()` from `onChange`; it already runs only when meaningful controller state changes. The callback can fire during initialization, so it should not read a controller variable before assignment.

## Intake hooks

1. Immediately before `alertIntake.analyze()`, call `beginIntake()` and keep its opaque ticket for that request.
2. When `AlertIntake.show(draft, message)` receives a draft, call `receiveIntake(draft, ticket)`. A late or changed-offer response is rejected. The production controller remains responsible for its existing request cancellation.
3. `show(null)` at loading should call `clearIntake('loading')`, which clears old cards but preserves the request ticket. Errors/cancellation/reset call `clearIntake()`; a real offer change calls `invalidate()` as below. Do not cancel the ticket merely because the existing show callback fired its loading message.
4. Supported quoted facts have individual acknowledgements. Missing/uncertain fields remain owner tasks and are never marked supported. A draft containing only unknowns can still be applied to clear old form defaults; its tasks remain pending.
5. Replace direct Apply with `quickReview.applyReviewed(() => alertIntake.applyReviewed())`. Keep the callback synchronous. This adapter captures the reviewed statements before host `offerChanged()` invalidates state, then validates the actual applied form, fixed-or-earlier deadline and expiry before binding a receipt. A callback returning `true` alone is insufficient: the current production controller can return true even when its application callback rejects an expired deadline.

The module consumes the current backend's supported fields and questions. It does not add a new backend contradiction classifier or image extraction capability. Source photos must not acquire operational facts through these decorations.

## Offer, result and clock hooks

Call `invalidate('Offer changed. Review the current details again.')` on every donor text, quantity, storage, origin, scenario and explicit pickup-window change, including programmatic application paths. Increment the offer revision on those changes. Preserve the production fixed-or-earlier clock policy.

Call `refresh()` after result publication or clearing, source-state changes and lock/unlock. The existing deadline tick can call it once per second; unchanged rendering preserves acknowledgements and focus. The module invalidates result review when the full result/evidence snapshot or effective source freshness changes, or when the deadline expires. Clear the host's stale result on offer edits as the existing workflow does.

Comparison always uses `winner` followed by `runners_up` in server order. Every displayed candidate keeps profile provenance, retrieval status, unknown observation date and unconfirmed available capacity, independent of optional generated evidence selection. Do not replace the result's source envelope with an unrelated newly fetched profile and call it the same evaluated result.

## Handoff and copy hooks

The handoff is available for current eligible `success` and `draft` results, including deterministic fallback. It shows the canonical deadline, donor offer, proposed recipient, source labels and owner tasks. An explicit current-result checkbox is separate from donor-statement acknowledgement.

Use `copyHandoff()` for the reviewed, unsent export. The module builds its own text; it checks ownership, result eligibility and deadline before copying and rechecks after the asynchronous clipboard operation. A changed state produces a stale-copy message, never a success receipt for the new offer. `reviewResult(false)` withdraws the handoff review.

Remove or route all legacy Copy match brief / Copy SMS / Copy email actions through the same review/clock boundary. Do not leave an unguarded legacy path beside the guarded button. Preserve the existing no-send/no-reservation behavior. Missing clipboard access leaves the draft readable for manual copying.

## Assets and preview checks

Optional decorative PNGs are under `agent/ui/assets/quick-wins/`; `manifest.json` retains the exact generation prompts. Use restrained welcome/empty-state placement with `alt=""`, lazy loading, a reserved square box and `object-fit: contain`. Artwork carries no visible generation captions, per Dico's direction. Do not replace operational data, source labels or icons with artwork.

Run the focused Node tests, current intake/clock/source/view tests and the existing responsive browser harness. Exercise: stale extraction after a source edit; unknown-only application clearing defaults; expiry before application; changed result after acknowledgement; omitted candidate freshness; same-deadline offer changes; and edits/expiry while clipboard is pending. Check normal and fallback results at desktop and narrow-phone widths. Deliver the combined preview for Dico's review before merge or production deployment.
