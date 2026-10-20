# Dynamic surplus-alert broker

This adds an evidence-driven planning step to the existing Vertex ADK broker.
It is separate from the UI/context review checkpoint and must pass the candidate
release gates before production.

1. Paste a grocer alert and choose **Extract details with Gemini**.
   `/api/intake/parse` uses Gemini 2.5 Flash on Vertex through ADK. It returns
   literal product terms, quoted storage/quantity/deadline evidence and missing
   questions. Nothing runs a match or sends outreach automatically.
2. Review and apply. Unsupported pallet/storage values clear example defaults.
   No crate/weight conversion is invented. A clock time such as “compactor at
   4pm” remains a quoted question until date/timezone are confirmed. Explicit
   pickup hours are anchored at extraction; an established clock may only tighten.
3. The ADK coordinator calls `broker_match` once. Its MCP vector search retrieves
   at most12 candidates. Deterministic storage, quantity, schedule and timing
   checks retain authority over eligibility and the winner.
4. MCP `get_tract_equity` reads at most3 exact candidate tract IDs from BigQuery.
   MCP `lookup_shelf_life` reads at most3 literal product terms from the pinned
   FoodKeeper endpoint through a loopback-only Toolbox HTTP source. Together the
   supplemental reads have a20-second budget; failure stays explicit.
5. Gemini receives the actual candidates, source statuses and a bounded catalog
   of evidence cards. It selects operational tradeoffs, including disadvantages,
   and prioritizes the confirmation questions. The renderer resolves those
   references again after the final deadline check, and builds an offer-specific
   SMS with literal food terms and the selected priority. No unconstrained model
   claims are used as factual proof.

The result includes `context_evidence`, per-tool `execution_trace`,
`synthesis_evidence`, the fixed `donor.deadline_at` and the coordinator brief.
Model failure, invalid citations or a winner changed by elapsed time produces a
labeled deterministic draft. Supplemental evidence failure does not erase
retrieved candidates or extend the deadline.

## Evidence boundaries

- ACS-derived starter snapshots have no retained observation year. Fetch time is
  not observation time. ZIP-centroid tract association is not service catchment
  or evidence about any individual's needs.
- FoodKeeper is consumer storage/quality guidance. Products require selection
  and state/date/temperature-history confirmation. It does not measure remaining
  freshness, establish donation safety or replace the pickup deadline.
- Operational profiles, including generated teaching data, require real recipient
  confirmation. The SMS/email is a draft: nothing has been dispatched or booked.
- Toolbox remains private on loopback. Its FoodKeeper GET has a fixed host/path,
  no model-provided URL or headers, and an allowlist limited to127.0.0.1.
  No extra credentials, IAM grants, billing changes or database writes are added.

## Deployment and integration

The HTTP MCP source expects the existing FastAPI port8080. The release verifier
must prove the actual Toolbox response on a tagged Cloud Run candidate, alongside
intake extraction, semantic retrieval, final clock checks and both Live probes.
The broader workspace redesign and interruption recovery are separate review
branches; their API markup hooks must preserve `AlertIntake`, `OfferClock` and
the new source-evidence envelopes when reconciled.
