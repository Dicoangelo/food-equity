# Food Equity demo experience — review candidate

This package adds the animated rescue companion, a light teal/sage workspace, two original illustrations, reviewed donor-fact cards, recipient evidence comparison and an unsent handoff review. It preserves the existing Vertex, MCP, ranking, clock and Gemini Live backend.

The companion opens the existing agent session. Talk explicitly starts the microphone; Type does not. Proposed fields stay separate until Apply. Stop and closing the assistant release the session. Navigation and recipient modals retain focus ownership. Reduced-motion users do not receive character animation.

The 40-second Food Equity film is the first-entry cinematic, with immediate workspace/skip access, muted playback, and a once-per-session preference. The public app has no password or sign-in gate. Film: `agent/ui/food-equity-story-v1.mp4`, SHA256 `bcdd8d0cea751488ed567871913cc49a9202b3cfcefe0e371e48bffa6e8a784b`; generated imagery and narration provenance are retained in the separate AI Studio lab. Illustration prompts and original receipts are under `docs/visual-assets/`.

## Review locally

- `http://127.0.0.1:8797/`: current local UI connected to the existing public Cloud Run backend through a loopback-only proxy. It is not a deployed UI release.
- `http://127.0.0.1:8796/`: sample-data preview. No Gemini intake or microphone provider calls; matching is an explicit fixture.
- Production: `https://seattle-food-broker-6ukbp746xa-ue.a.run.app/`.

## Verification

- Combined baseline: 672 Python tests and 86 JavaScript tests pass, including the cinematic module.
- Browser checks: 320/390/1440px; no horizontal overflow; companion open/close and exact opener focus, guarded handoff copy, source/freshness/availability on both proposed recipient and backup, fixed deadline on matching retry.
- Actual live-backed preview: Gemini proposed two pallets of ambient bread, explicit Apply updated the fields, preserved the exact deadline, stopped the session, and did not run matching.
- Synthetic audio through the preview proxy: 3.483s input, one input transcript, 4.28s non-silent spoken output, owned completed turn, no draft mutation. This is not verification of the physical microphone or speaker playback.

## Release boundary

Dico approved deployment after preview and set a 16:35 Toronto deadline. PR19 merged as `11b3260`, but run `39489142843` failed revision creation on regional Cloud Run CPU quota. The public service was subsequently observed at older `d885b798`; that is not proof that this package or exact main was deployed. Check the final release commit, tagged candidate checks and traffic before announcing the upgrade. Do not substitute an older local rebuild or bypass the deployment guards.
