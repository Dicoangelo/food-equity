# Seattle rescue broker — team pre-read

**20 October 2026 · Retail and grocer rescue · Read in three minutes**

**[Open the live broker](https://seattle-food-broker-6ukbp746xa-ue.a.run.app)**. Public access is verified: unauthenticated requests to the page, health and city endpoints returned HTTP 200 after Dico authorized the service-only access change. The [README](../README.md#team-access) includes the existing lab proxy fallback.

We help a coordinator turn a time-limited surplus offer into a **proposed Match Brief**: who can take it, why, how much, what the pickup clock permits, which neighborhood it may help, outreach drafts and available alternatives. The important behavior is refusing a superficially attractive but physically unsuitable recipient.

**What actually runs.** The browser calls FastAPI → an ADK/Gemini agent → our custom broker tool → Google's MCP Toolbox → real BigQuery vector search. Deterministic checks bind storage, capacity, schedule and the fixed deadline. A final clock recheck catches time spent retrieving/generating. Gemini chooses outreach tone/question order; it cannot write factual recipient, quantity, date or backup claims. [Runtime sources](IMPLEMENTATION-HANDOFF.md#runtime-and-implementation-map).

**Walk the demo.**

1. Choose bakery, urgent chilled or the full-lot capacity stress scenario; this fills the form without spending a model call. Bakery uses the original 200-loaf posting with a hypothetical one-pallet capacity test and a 24-hour window, not a unit conversion. Review and run it.
2. Inspect the proposed recipient or honest refusal, clock margin, pending details and actual backups. “No match” can be the correct answer. Never lengthen the donor's deadline to force success.
3. Open the map for aggregate neighborhood context and approximate capability points; directions open separately in Google Maps. Check the actual collection address.
4. Review and copy the Match Brief, SMS or email. Confirm acceptance, available storage/capacity, receiving hours and pickup ETA. **The app sends and reserves nothing.**

**Say this clearly to judges.** Organization identities come from the IRS starter data; operating profiles are generated and planted examples may be wholly synthetic. The census need signal is an aggregate proxy, not household demand or a service catchment. Recipient points are ZIP centroids, and map connectors are not driving routes. FoodKeeper is consumer storage/quality reference material; its lookup does not establish safety, remaining freshness or extend a deadline. Pallet quantities are scenario assumptions, not automatic unit conversions.

**Evidence and release status.** Cloud Run source `9027ea5`, revision `seattle-food-broker-00004-sxn`, passed the authenticated release smoke: health, city layers, FoodKeeper, a real ADK/MCP bakery match with one backup, and a cold-capacity refusal. The source deadline stayed fixed. City data returned 389 tract points and 119 profiles (110 generated, nine planted). Team `main` is `0c952f3`, with PRs #7/#8 merged. These observations prove the current path ran, not future recipient acceptance. Receipt: workspace `research/LIVE-RELEASE-9027ea5.json` under the impact toolkit.

**Voice is the next release.** A Gemini 3.8 Live probe in `us-central1`, using the runtime identity, connected and returned 33,122 audio bytes. The optional browser intake integration defaults off and has 24 passing backend tests. Deployment, direct WebSocket, microphone and reviewed-draft acceptance remain pending. It must only draft form fields; matching stays a separate deliberate action.

The `e1fbe03` audit performed ten actual MCP retrievals with zero errors: two proposed matches (one of four retail offers and one of six cross-track), eight no-matches, 389 baseline tracts. Matched neighborhoods were **below** the overall equal-tract poverty/no-vehicle baseline. Claim functioning retrieval and disclosed constraints; this does not establish equity uplift or delivered food. **Source anomaly:** sealed-formula posting 8 replayed a one-hour source clock derived from an after-opening reference, despite a 168-hour donor window. That no-match does not establish feasibility or expiry for sealed formula. The source mapping needs review before a corrected replay; shared tables have not been changed. This audit also predates the new refusal guards. Receipt: workspace `research/AUDIT-E1FBE03.json` under the impact toolkit.

**Team integration.** Keep the canonical `a4i_food.recipient_embeddings` path. Teammate SQL is now separated into an isolated legacy embedding proposal and a read-only selected-profile diagnostic; both are outside deployment and remain unexecuted after cleanup. Full contracts, commands and limits are in [IMPLEMENTATION-HANDOFF.md](IMPLEMENTATION-HANDOFF.md). The [A2A exchange PRD](A2A-RESCUE-EXCHANGE-PRD.md) is design only; partner negotiation, dispatch, delivery evidence and incentives are future work.

**Next:** release and verify optional Live intake without changing the established matching path. Preserve a separate receipt for the replay audit on the current image.
