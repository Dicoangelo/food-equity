# Food Equity team demo

Public app: https://seattle-food-broker-6ukbp746xa-ue.a.run.app/
Release identity: https://seattle-food-broker-6ukbp746xa-ue.a.run.app/api/version

## Three-minute walkthrough

1. Open **Offer & match**. Use the bakery scenario for a repeatable starting point. Explain that this is a Seattle retail and grocer rescue prototype, using teaching/generated recipient profiles and Census context.
2. Open **Assistant**. Opening it does not enable the microphone. Click **Start microphone**, allow access, and speak clearly near the mic: “Change the quantity to two pallets of sealed shelf-stable bread. Keep the pickup deadline and location unchanged. Propose the edit for review.” A typed note is available if the venue is noisy.
3. Review the proposed fields and Seattle pickup deadline. **Apply reviewed draft to form** stops the session and updates the offer. It does not request a match. Click **Find a viable match** separately.
4. Show live progress, the selected recipient, two alternatives where available, clock margin, capacity/storage constraints and the map. Explain the vector-search retrieval, deterministic feasibility checks, Census/FoodKeeper evidence and Gemini synthesis.
5. Open the recipient and evidence views. Copy the personalized handoff draft. It is ready for coordinator review; copying does not send it, reserve capacity or confirm delivery.

## Live session controls

- Typed sessions can add the microphone without replacing the current connection or draft.
- A correction or view change can reconnect the provider while keeping the browser session, draft and absolute pickup deadline. Stale audio/actions are discarded. If recovery asks for repetition, pause and repeat the last phrase.
- **Stop session**, closing the assistant or leaving the page ends capture/playback. Sessions have a three-minute maximum; recovery does not extend it.
- If microphone access is blocked, allow it for this HTTPS site in the browser or use the typed note. Headphones help prevent speaker feedback in a loud room.

## What the release checks establish

The deployment stages an exact commit without production traffic, checks the broker/data/extraction path, runs two typed Live sessions and a synthesized-speech Live turn, then promotes only that checked revision. The audio probe requires input transcription, owned non-silent response PCM and a completed turn without changing the offer.

Synthetic speech checks do **not** prove the presenter's microphone or audible speaker output. Before presenting, say a short offer on the actual laptop/phone, confirm the transcript and audible response, then stop and confirm the microphone indicator clears. Phone layout checks are separate from physical-device audio checks.

Recipient operations and availability require confirmation. Map positions may be ZIP centroids, pickup times are profile estimates, FoodKeeper is reference guidance, and Census context is not live demand. Do not describe a proposed match as an accepted pickup or completed delivery.
