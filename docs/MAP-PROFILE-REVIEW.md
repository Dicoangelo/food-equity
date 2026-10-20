# Map and recipient profile lane — review receipt

Built locally on 20 October 2026 from `codex/live-intake` at
`8e60e25361866ab4d7f5cd13495e102c33f46c60`, in the isolated sibling checkout
`food-equity-map-lane`, branch `codex/map-profile`.

## Result

The full-width Seattle workspace links a searchable recipient directory,
filtered map markers and an evidence drawer by `recipient_id`. Search, storage,
offer decision and source filters apply to the map and list together. Numbered
proposals preserve the backend's winner/backup order; pending and rejected
candidates remain inspectable. “Fit visible profiles” restores Seattle framing.

Profiles show generated versus invented provenance, coordinate precision,
capacity versus the evaluated offer, receiving storage, transport, service days,
acceptance evidence/reason codes, exact donor deadline and evaluation timestamp,
pickup estimates, raw margin, neighborhood rates and confirmation questions.
Unknown values stay unknown. Opening a profile makes no API/model request.

Only eligible candidates receive an offer-fit explanation. The backend score
remains authoritative; component bars disclose reconstruction from rounded
distance and percentage fields. Missing neighborhood inputs contribute zero in
the current engine without claiming zero need. The API does not export the
per-run buffer, so the drawer labels 15 minutes as the current engine default,
not a supplied per-run value. Comparison includes only the actual winner and up
to two returned eligible backups.

Desktop uses a nonmodal right drawer. Phones use a bottom sheet with a visible
close control, Escape, trapped keyboard focus, inert background and restored
focus. Marker selection has an outline and text, not color alone. External
Google Directions uses supplied approximate points; no Google route content
or drive estimate is drawn on OSM. Invalid evaluated donor coordinates disable
directions rather than substituting a demo origin.

## Verification

- Full offline Python suite: **233 passed** (two existing library warnings).
- Final served-asset/API checks: **31 passed**.
- Existing UI contract suite: passed, including bakery preset, exact clock,
  no-match/pending states, single-flight requests and voice draft Apply boundary.
- New explorer contract suite: passed, including ID merging, nulls, provenance,
  eligible-only components, filters, source ordering, safe directions, stale
  offer invalidation, marker keyboard selection and mobile focus behavior.
- Independent headless Chrome, local fixture server: **1440px, 390px, 320px**
  passed with no horizontal overflow or JavaScript errors. Checked list and
  keyboard marker selection, shared filters, comparison/focus restoration,
  expanded fit explanation, phone focus loop, and no profile-triggered API calls.
- Screenshots were visually reviewed. Local evidence is in
  `../../research/map-profile-qa/` relative to this checkout, outside Git.

Run the deterministic checks from the checkout:

```sh
python -m pytest tests -q
node tests/test_ui_contract.js
node tests/test_recipient_explorer.js
```

Optional browser QA requires an existing Playwright installation and Chromium.
Set `NODE_PATH` for a nonlocal Playwright package, `CHROME_PATH` for an installed
Chrome executable, and optionally `QA_OUTPUT_DIR` for screenshots:

```sh
node tests/test_recipient_explorer_browser.cjs
```

The browser test starts its own localhost fixture server and isolated browser.
It uses the application's existing public CDN assets and OSM tiles. Match and
city data are explicitly local fixtures, not evidence of deployed operations.

## Integration boundary

Production changes are confined to `agent/api.py` and two new same-origin
assets under `agent/ui/`. The existing Docker `COPY agent ./agent` includes
those assets. API response shapes, matcher weights/gates, fixed-deadline logic,
broker execution, and Live intake backend are unchanged.

The parent lane's newer Live cleanup is not in this branch; review this commit
on top of that work. No deployment, merge, push, cloud access change, original
browser/VM operation or credential retrieval occurred in this lane. Deployed
behavior, real operational availability and road routing remain unverified
by these local tests.
