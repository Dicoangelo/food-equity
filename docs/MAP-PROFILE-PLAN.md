# Map and recipient profiles — implementation plan

Reviewed 20 October 2026. This is a design/readiness plan, not a claim that routing is enabled.

**Build the profile/map interaction first; keep road routing an independently verified capability.** The live map currently serves tract-centroid need signals and approximate recipient locations. A drawn connection is illustrative, not a road route.

## Reuse from HiddenGem

The [public HiddenGem page](https://hiddengem.metaventionsai.com/) was read successfully. Local source was inspected at `/Users/dicoangelo/projects/toronto-housing/dashboard.py` (cards, facts, source badges, component bars, compare) and `score.py` (hard gates and observed dimensions).

| Reusable interaction | Food-broker adaptation |
|---|---|
| Linked list/map, search, filter chips | One selected recipient ID highlights its card and marker; search by organization/city; filter storage, outcome and provenance. |
| Compact facts above details | Storage, documented capacity, pickup estimate, service days and confirmation state remain visible. |
| Source/verify badges | Generated operations, invented teaching case, unknown field and approximate location are separate badges. |
| Expandable score components | Explain **offer fit**, only after an offer has been evaluated. A city-only profile has no fit score. |
| Side-by-side comparison | Compare winner and up to two alternatives on the same offer/deadline. Show why a rejected semantic hit failed. |
| Missing values displayed as n/a | “Unknown” is never rendered as zero capacity, zero poverty, no freezer, or a verified absence. |

Do not copy HiddenGem branding, personal profile information, rental thresholds or star ratings. Its building-review stars have their own external evidence; our starter contains no verified charity-review or reliability dataset.

## Profile drawer contract

Use existing city and match responses first; no model call is needed when opening a profile. Merge by `recipient_id`, not name. Desktop: persistent map/list plus a right drawer. Mobile: accessible bottom sheet, visible close control and restored focus. Selected map points must remain distinguishable without color alone.

1. **Identity:** name, city, profile source and coordinate precision. Generated rows have starter organization identity but generated operations; planted rows are invented organizations. Do not invent a photo, website, contact or review.
2. **Offer decision:** eligible proposal / needs confirmation / rejected / not evaluated. Display the backend reason codes and acceptance evidence, including restrictions.
3. **Physical capability:** requested pallets beside documented maximum; refrigeration/freezer, transport and dock fields; explicit unknowns. Vehicle refrigeration does not establish receiving-site cold storage. Documented maximum is not confirmed current availability.
4. **Clock:** fixed donor deadline, evaluation time, profile pickup estimate, raw margin and coordination buffer. Do not call the estimate a verified road ETA.
5. **Neighborhood context:** the actual poverty/no-vehicle percentages, approximate tract association and missing-data caveat. The heat layer is a demographic proxy, not measured hunger or official food-desert classification.
6. **Next action:** inspect confirmation questions or compare alternatives. A profile action never silently reserves food or sends outreach.

**Offer fit, not organization rating:** expose the current engine's formula, `0.40 × semantic + 0.35 × need + 0.25 × logistics`, with semantic `max(0, 1 − cosine_distance)`, need `0.5 × poverty_rate + 0.5 × no_vehicle_rate`, and logistics `0.5 + 0.3 × refrigerated_transport + 0.2 × loading_dock`. Hard gates determine eligibility before ranking. The current engine contributes zero for missing need inputs while issuing a warning; disclose that limitation and display the missing raw inputs as unknown. Do not silently introduce HiddenGem's different weight-renormalization policy in the UI. Provenance remains an independent evidence label, not an invented confidence percentage.

## Google routing readiness

| Question | Verified documentation / remaining check |
|---|---|
| Can the backend use an attached service account? | Official [Routes client libraries](https://developers.google.com/maps/documentation/routes/client-libraries) document ADC authentication. Use the existing Cloud Run workload identity; no downloaded key or new API key. Actual lab authorization remains unverified. |
| API and quota identity | `routes.googleapis.com` must already be enabled and the existing project's Maps billing/quota must permit the request. The quota principal needs `serviceusage.services.use`, normally supplied by `roles/serviceusage.serviceUsageConsumer`; this is a requirement to check, not an instruction to grant it. See [quota-project authentication](https://docs.cloud.google.com/docs/quotas/set-quota-project). No Maps-specific IAM role or extra OAuth scope was verified in this review. |
| Endpoint | The official [REST reference](https://developers.google.com/maps/documentation/routes/reference/rest) publishes `https://routes.googleapis.com`. It does not publish an `us-east1` regional endpoint. The app's Cloud Run region does not make Routes region-local; do not invent a regional hostname. Lab/org restrictions still need verification. |
| Cost | [Routes billing](https://developers.google.com/maps/documentation/routes/usage-and-billing) depends on request features. Traffic-aware options use a higher feature tier. No free or lab-covered entitlement has been verified. No billing changes or API enablement were performed. |
| Existing OSM basemap | Current [service-specific terms §19.2](https://cloud.google.com/maps-platform/terms/maps-service-terms) restrict using Routes content with a non-Google map. Do not overlay a Google route or route-derived values on the Leaflet/OSM view. An actual Google Map or a separate no-map route view requires the applicable [Google Maps attribution](https://developers.google.com/maps/documentation/routes/policies). Attribution alone does not permit an OSM overlay. |

Smallest prospective request, after the readiness checks: one selected recipient, server-side ADC, no retries, bounded timeout, explicit action. `POST https://routes.googleapis.com/directions/v2:computeRoutes`; JSON origin/destination are `{"location":{"latLng":{"latitude":<number>,"longitude":<number>}}}`, with `travelMode: "DRIVE"`, `routingPreference: "TRAFFIC_UNAWARE"`, `computeAlternativeRoutes: false`. Request only `routes.duration,routes.distanceMeters,fallbackInfo` through `X-Goog-FieldMask`; supply the project as the OAuth quota project in process. This is an ordinary driving estimate, not live-traffic verification. See the [official request schema](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes).

Preserve `requested_at`, coordinate provenance, response status, duration/distance and fallback indicator in the immediate UI response. Label a successful response **Google-computed drive estimate between supplied points**. A ZIP-centroid route does not verify a facility entrance, volunteer arrival, loading time, transport suitability or recipient acceptance. Do not replace the matcher's pickup estimate with driving duration alone. Do not persist Google route content as audit fixtures without checking its permitted storage.

Until an authorized real request succeeds, keep `routing.status = unavailable` and the existing external Google Directions action. An external navigation link is useful but does not prove the broker calculated a route.

## Acceptance checks

- Selecting a marker/card opens the same profile; keyboard and mobile controls work; no unrelated model request fires.
- Missing capability/rate values remain unknown; generated/planted badges survive every view; no charity trust stars appear.
- Fit components reproduce the backend's score and are labeled per offer; a rejected candidate cannot become eligible through UI weighting.
- Route denial, quota failure, timeout or fallback is explicit and preserves the donor deadline. No invented ETA or straight-line “drive time” fills the gap.
- Only an actual provider response enables the driving-estimate state, in a permitted presentation; API credentials never reach the client.
