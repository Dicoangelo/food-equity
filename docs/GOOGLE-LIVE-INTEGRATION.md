# Gemini Live — bounded voice intake

**Implementation snapshot: 20 October 2026.** Dico selected `gemini-3.8-live` and explicitly authorized public access to the hackathon demo. The service-specific public binding is a release-coordinator action; its successful receipt is separate from this document. Cloud Run and the existing broker remain in `us-east1`; the separate Live connection uses `LIVE_VERTEX_LOCATION=us-central1` in the same project, `qwiklabs-gcp-04-b992b9561e32`. Backend Google credentials remain server-side.

The coordinator verified model access under the existing Cloud Run runtime service account: job `seattle-live38-probe-9027`, execution `qg9qd`, returned `connected=true`, `audio_bytes=33122`, `ok=true` in `us-central1`. Workspace receipt: `research/LIVE38-PROBE-9027.json`. That proves the synthetic audio turn; full browser/typed-tool integration still requires its own receipt.

## Verified contracts and remaining probes

| Item | Evidence and implication |
|---|---|
| Selected model | Google's current card lists `gemini-3.8-live` as GA, released September 24, 2026. Available locations include `us-central1` and the `us`/`eu` multi-regions; `us-east1` is not listed. Function calling is supported; structured output and thinking are not. [Official model card](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-live). |
| Existing-region option | `gemini-live-2.5-flash-native-audio` is also GA and lists `us-east1`. This is an alternative only if Dico chooses it; do not silently downgrade or change region after a denied probe. [Official 2.5 Live card](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/2-5-flash-live-api). |
| Transport and auth | Regional WSS with OAuth bearer authentication. Use the backend's ADC through `genai.Client(vertexai=True, project=..., location=..., credentials=...)`; never give browser code an access token or API key. Raw v1 endpoint for the selected region: `wss://us-central1-aiplatform.googleapis.com/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent`. [Session guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api/start-manage-session). |
| Audio | Input is raw signed 16-bit little-endian PCM at 16 kHz; output is 24 kHz PCM. A browser MediaRecorder WebM blob is not a PCM frame. [Live overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api). |
| Installed SDK | The repo pins `google-genai==2.31.0`. Local inspection verified `client.aio.live.connect`, `send_client_content`, `send_realtime_input`, `send_tool_response`, `client.aio.aclose`, and transcription/config fields. `LiveConnectConfig(response_modalities=["AUDIO"])` validates offline. No dependency upgrade is needed for this probe. |
| Tools | Declare tools in session configuration, handle `message.tool_call.function_calls`, validate arguments, and return matching name/id with `send_tool_response`. Direct GenAI Live does not automatically execute MCP. Preserve the existing ADK/MCP broker behind the confirmed form rather than exposing its tools during intake. [Official tool guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api/configure-gemini-capabilities). |
| Hosting | Cloud Run supports WebSockets, subject to the request timeout. An open connection uses active resources; reconnects may reach a different instance. A browser session must not rely on best-effort affinity for durable state. [Cloud Run WebSockets](https://docs.cloud.google.com/run/docs/triggering/websockets). |

The current documentation includes transcription and response-modality examples that differ between model generations. First prove an audio-only connection, then probe input/output transcription and tool-call behavior with this exact model/version. Do not interpret a successful text-generation request to the existing Gemini model as proof of Live capability.

The model probe established access for that request and identity. Available capacity/quotas and the public browser WebSocket path remain separate checks. A generic published session limit is not the lab project's allowance.

## Reuse the supplied dinner demo

The supplied [A4I dinner mini-demo](https://github.com/haggman/A4I2026-minidemo-dinner/tree/5ccf2e651e7f276d99f2fbdd08f7a49babce44f2) has concrete ADK Live patterns:

- [server.py](https://github.com/haggman/A4I2026-minidemo-dinner/blob/5ccf2e651e7f276d99f2fbdd08f7a49babce44f2/dinner_scout/server.py): per-page state/session, WebSocket, `LiveRequestQueue`, `Runner.run_live`, upstream audio and downstream audio/transcription/interruption events.
- [Browser implementation](https://github.com/haggman/A4I2026-minidemo-dinner/blob/5ccf2e651e7f276d99f2fbdd08f7a49babce44f2/static/index.html): AudioWorklet/resampling, explicit Start/Stop, interruption playback flush, same-origin socket and typed fallback.
- Its SDK versions differ from ours. Inspect and test adapters against our pins rather than replacing requirements. Retain its Apache-2.0 license and NOTICE attribution for copied code. Do not inherit credential files, setup scripts, unrelated Maps/Places tools, hard-coded model locations, transcript stdout logging, raw provider errors, shared user identities or unbounded queues.

The implemented intake uses the existing GenAI SDK directly and follows the demo's bidirectional transport pattern. It leaves the matching ADK runner unchanged. No dinner-demo source was copied into the backend module. The direct SDK probe below remains useful for isolating model/auth support from browser transport.

## Smallest useful flow

1. Coordinator clicks **Start voice intake** and permits the microphone. Capture only while visibly active; Stop ends capture, playback and both stream directions.
2. Live asks for missing product/quantity units, preservation/storage, pickup location, and the actual pickup deadline. It can suggest a draft, but cannot certify safety, invent pallet conversions or overwrite known facts without showing the change.
3. One local function, `draft_offer`, accepts only bounded optional draft fields. Server validation returns missing/ambiguous facts. It has no broker, message-sending, reservation, payment, approval or arbitrary URL capability. Coordinates can come from the visible initial form context, never model inference. Drafts carry a session ID and increasing version.
4. Show transcript and editable field diffs. Unknown pallet quantity stays missing. An utterance such as “two hours from now” is anchored to the captured server timestamp and shown as an absolute zoned deadline for confirmation; conversation/reconnect time must consume that clock.
5. **Apply draft** fills the existing form and clears stale results. **Review and run** is the existing explicit action; it submits to **`POST /api/match`**. There is no `/api/broker` route in the current repo. Existing validation, ADK/MCP retrieval, deterministic gates and final clock recheck remain authoritative.

An audible “yes” or a model tool call is not permission to dispatch or contact anyone. Even after matching, the existing Match Brief is a proposal requiring recipient confirmation.

## Public-demo access and resource bounds

The routes are `GET /api/live/config` and `WS /api/live/intake`. Dico authorized a public hackathon demo. Once the service-specific public binding is verified, browser callers need no Google credential. The backend continues to use its existing runtime service account and ADC when connecting to Vertex. No token is placed in browser code, WebSocket URLs or logs.

The module accepts the actual browser origin or an exact HTTPS entry in `FOOD_LIVE_ALLOWED_ORIGINS` for the Cloud Shell preview. **Origin checks are not authentication:** non-browser callers can supply that header. The public service does not establish a user identity or enforce per-user quotas. Current bounds are two Live sessions per process, 180 seconds per session, 60 seconds without incoming input, 16 KiB per audio/JSON frame and 10 MB of incoming PCM per session. These are resource bounds, not a spending cap. Deployment instance limits remain separate.

`FOOD_LIVE_ENABLED` defaults to false and remains the operator switch. The module rejects arbitrary endpoints and unknown tools, keeps drafts/transcripts in the current session, does not log raw audio/transcripts, and cancels both stream tasks before provider cleanup. Reconnecting requires visible review of the retained draft and exact deadline. The implementation is in `agent/live_intake.py`, with tests in `tests/test_live_intake.py`; existing SDK pins and form contracts are retained.

After deployment, run the typed-only protocol smoke without a microphone:

```bash
python scripts/verify_live_intake.py --base-url https://seattle-food-broker-6ukbp746xa-ue.a.run.app
```

It opens the public WebSocket, supplies a synthetic one-pallet context with an absolute deadline, asks the model to change only the quantity to two pallets, and verifies the resulting versioned draft and completed response preserve that deadline. It prints status/counts only, sends Stop and never calls `/api/match`. Passing does not establish microphone/playback quality or recipient acceptance. A mismatched-origin rejection is a browser-origin test, not proof of authenticated access.

## Minimal student-ADC probe

Run only in the Cloud Shell/VM whose **ADC identity**, not merely active gcloud configuration, has already been verified as the assigned student. This uses the current Python environment with `google-genai==2.31.0`. It makes one short synthetic model request, captures no microphone/audio file, executes no tools, and prints only status/counts. No credential is printed or saved.

```bash
python - <<'PY'
import asyncio
import json
import google.auth
from google import genai
from google.genai import types

PROJECT = "qwiklabs-gcp-04-b992b9561e32"
LOCATION = "us-central1"
MODEL = "gemini-3.8-live"

async def main():
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    client = genai.Client(
        vertexai=True, project=PROJECT, location=LOCATION,
        credentials=credentials, http_options=types.HttpOptions(api_version="v1"),
    )
    connected, audio_bytes = False, 0
    try:
        async with asyncio.timeout(45):
            async with client.aio.live.connect(
                model=MODEL,
                config=types.LiveConnectConfig(response_modalities=["AUDIO"]),
            ) as session:
                connected = True
                await session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text="Say ready in one short sentence.")],
                    ),
                    turn_complete=True,
                )
                async for event in session.receive():
                    content = event.server_content
                    if content and content.model_turn:
                        for part in content.model_turn.parts or []:
                            blob = part.inline_data
                            if blob and (blob.mime_type or "").startswith("audio/"):
                                audio_bytes += len(blob.data or b"")
                    if content and content.turn_complete:
                        break
        print(json.dumps(dict(
            model=MODEL, location=LOCATION, connected=connected,
            audio_bytes=audio_bytes, ok=connected and audio_bytes > 0,
        )))
    except Exception as exc:
        print(json.dumps(dict(
            model=MODEL, location=LOCATION, connected=connected, ok=False,
            error_type=type(exc).__name__, code=str(getattr(exc, "code", "unknown")),
        )))
    finally:
        await client.aio.aclose()

asyncio.run(main())
PY
```

Success proves only model connection and one generated audio turn under that identity. Next gates: prove the intended public WebSocket path and reject mismatched browser origins; verify microphone resampling, transcription, interruption and tool-call arguments; exercise missing quantity/deadline, contradictory corrections, reconnect and expired-offer behavior. Phone Safari/Chrome and microphone denial must retain the working typed flow. A region-policy or permission denial is a blocker to diagnose, not a reason to switch projects or bypass policy.

## Prior corpus consulted

Before web research, a cross-notebook query consulted **Agents for Impact — Innovation Lab**, `465d6523-bd82-42b8-ab71-cfe94379d3ed`, and **Food Equity — B12Give Reference & Hackathon Build**, `c325500c-cd9d-4a57-a265-8b27b52e43f6`. Both reported no Live/streaming API contracts in their sources. They support the human-review/deterministic-broker boundary; some deployment snapshots are older than this repo. Current official model/API documents and inspected installed SDK contracts therefore govern this proposal.


## Isolated interruption-refinement protocol (review only)

The `codex/context-agent` branch adds bounded provider replacement behind the existing browser WebSocket. This is not evidence of deployment or a fresh model probe. See [CURRENT-VIEW-REVIEW.md](CURRENT-VIEW-REVIEW.md) for the untrusted display boundary, explicit review flow and release gates.

- `ready`: `provider_epoch`, `input_correlation=true`, `audio_activity=true`, `max_recoveries=3`.
- Text: `{type:"text", text, request_id}`; the browser always supplies a positive monotonic ID. The typed verifier also supports the older deployed protocol.
- Audio: `{type:"audio_start", provider_epoch, activity_id}` → owned `turn_started` with `activity_id` → owned `audio_ready`. Each input binary frame is eight header bytes (big-endian unsigned epoch/activity ID) plus PCM16k mono bytes; matching `audio_end` emits SDK `ActivityEnd`.
- Model audio: `{type:"audio", provider_epoch, ...Owner, pcm}` with base64 PCM24k mono, at most 32,000 decoded bytes per envelope. Transcript, action result and completion carry the same generation and owner.
- Recovery: `recovering` identifies the retired turn and reason; `resumed` arrives only after old transport cleanup and replacement connection. It does not resend or approve an operation. New `turn_started` echoes the input ID; delayed starts cannot acquire a newer pending request's ownership. Accepted `view_context_ack` also reports `recovery_required`; a view update after a completed turn can release the UI wait without reconnecting.

The installed `google-genai==2.31.0` schema supports `RealtimeInputConfig(automatic_activity_detection=AutomaticActivityDetection(disabled=True))` and `send_realtime_input(activity_start=ActivityStart(), activity_end=ActivityEnd())`. Tests validate those concrete SDK fields and mock the transport; real model acceptance, microphone onset/noise behavior and reconnection latency remain unverified for this increment. The reference provider docs are [Live API activity detection](https://cloud.google.com/vertex-ai/generative-ai/docs/live-api/streamed-conversations) and [Google Gen AI SDK Live](https://googleapis.github.io/python-genai/#live).
