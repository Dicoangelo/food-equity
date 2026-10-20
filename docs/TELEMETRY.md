# Release identity and request telemetry

`GET /api/version` returns `commit_sha` (`APP_COMMIT_SHA`), `revision`
(`K_REVISION`) and `service` (`K_SERVICE`). Missing or malformed identifiers are
`null`; they are never guessed. This endpoint and the unchanged `/api/health`
and `/healthz` checks make no model or data calls. Release identity is not proof
that Gemini, Toolbox or BigQuery works: run the release verifier separately.

Every HTTP response includes `X-Request-ID`. The app accepts a canonical,
36-character hexadecimal UUID from that request header, normalizes its case,
or generates a new UUID. Invalid IDs are not echoed. A caller-supplied ID is
for correlation only and establishes no identity or authorization.

The app emits one JSON line to stdout per HTTP request, which Cloud Run collects
in Cloud Logging. Fields are `event: http_request`, `severity`, `method`, `path`,
`status`, `duration_ms`, `request_id`, `revision`, `commit_sha` and `service`.
`path` is the matched route template; unmatched URLs become `<unmatched>`.
Duration covers application processing until response headers are available,
not complete streamed-body transfer. Server errors use `ERROR` severity.

These application events contain no query strings, request/response bodies,
authorization headers, identities, audio, transcripts or exception text. The
standard launcher disables Uvicorn access logs, which otherwise include raw
URLs. WebSocket sessions are intentionally excluded from this HTTP middleware;
no audio/transcription telemetry is added. Cloud Run's platform request logs
have their own fields and policies; these controls apply to application logs.

Unhandled HTTP exceptions produce a generic 500 with the same request ID in the
header and JSON body. Expected API errors retain their existing response
contracts. Health response bodies remain unchanged.

## Investigate a failed request

Ask for the request ID and approximate time, without requesting credentials or
an offer transcript. In Cloud Logging, filter the Cloud Run service:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="seattle-food-broker"
jsonPayload.event="http_request"
jsonPayload.request_id="REPLACE_WITH_REQUEST_UUID"
```

Inspect status, duration and revision/commit. `GET /api/version` shows the release
currently serving that request; traffic may span revisions during a rollout.
The deployment workflow must set the exact source commit as `APP_COMMIT_SHA`.
This document describes the implementation, not a verified deployment receipt.

Offline checks: `python -m pytest tests/test_telemetry.py tests/test_api_contract.py -q`.
