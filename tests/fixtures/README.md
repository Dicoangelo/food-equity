# Live audio release fixture

`live-audio-check.wav` is 3.483 seconds of locally synthesized, nonpersonal speech:
“Please say the audio check is working. Do not change the food offer.” No microphone,
personal recording, cloud TTS, or credential was used.

Generated on macOS using its system default voice (no named voice selected):

```bash
/usr/bin/say --file-format=WAVE --data-format=LEI16@16000 -r 170 \
  -o tests/fixtures/live-audio-check.wav \
  'Please say the audio check is working. Do not change the food offer.'
```

Format: mono PCM16 little-endian, 16 kHz; file size: 115,552 bytes.
SHA256 of the committed WAV file:
`4b282360cbeb67506643f2ba30b043ce748435488afab889913a0103044b8902`.
Regeneration may vary with the installed system voice or WAV metadata.

The deployment script uses this fixture from the exact Git archive after its typed
Live probes and writes `audio.json` before promotion. It requires input transcription,
owned non-silent response PCM, and turn completion. The fixture need not enter the
runtime Docker image: verification runs from the local archive workspace. This gate
does not verify physical microphone capture or browser speaker playback.
