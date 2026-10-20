# Food Equity workspace checkpoint

20 October 2026. This checkpoint joins the existing offer workflow and the three current-view features with the requested interface redesign.

## Included

- Integration merge `05ea767` preserves PR #15 source at `585e697` and context commits `71e8831`, `970408c`, and `21ff8d5`.
- A native Food Equity workspace informed by the actual Frontier Alpha and Partnerships UI source: focused offer/match, recipient, and evidence views; concise current-offer summary; optional assistant.
- Presentation lives in `agent/ui/workspace.css` and `workspace.js`. Existing form, matching, source, profile, and voice contracts retain ownership of their behavior.
- Profile-to-assistant transitions retain the selected recipient, source evidence, and exact pickup clock. Closing restores visible focus. Prior evidence receipts are invalidated when the underlying view changes.
- Responsive checks cover 1440, 1024, 768, 390, and 320px. Hidden maps do not redraw zero-sized heat canvases.

## Verification

- Full Python suite: 504 passed.
- Six existing Node suite files passed, including offer clock/stream, UI, recipient evidence, source state, bounded view context, and turn transport.
- Browser fixture suite passed all five widths, preserving map instance, form/result/deadline, source state, selected recipient, keyboard focus, and modal boundaries. No extra API requests, Live WebSockets, microphone requests, or browser errors.
- Independent review fixes cover a reset pickup window retaining an old summary label and a profile close restoring focus into a hidden workspace.
- Local review: `node tests/test_recipient_explorer_browser.cjs --serve`, then `http://127.0.0.1:8769`. The banner labels sample data; this is not a live-provider verification.

## Release boundary

The Google Cloud chat is the sole cloud release owner. Dico requested “huddle, deploy, then break off again.” Stage this exact clean commit as a tagged Cloud Run candidate, without changing production traffic or merging to main merely to obtain a preview. The existing deployment script stages by default; `--promote` is a separate action.

Production at huddle was PR #15 merge `d885b798`, revision `seattle-food-broker-gd885b798d9-1792522962-0fc338`.

Exclude the dynamic-broker branch and the separate interruption recovery slice from this first checkpoint. Provider interruption still uses the earlier terminal-session behavior in the included context baseline. Recovery is independently committed at `35e6999`, with bounded reconnection and old-generation event rejection; it still needs reconciliation and combined testing against this workspace before release.

After deployment, record the exact candidate URL, revision, commit, test receipts, and unchanged production traffic. Resume UI, recovery, and dynamic-broker work on separate branches from the agreed checkpoint. The expected future merge seams are `agent/api.py`, `agent/ui/view-context.js`, and their browser/transport tests; preserve the shared OfferClock, streaming cancellation, profile suspension, and workspace asset routes.
