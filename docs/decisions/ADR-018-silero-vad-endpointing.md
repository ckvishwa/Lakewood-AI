# ADR-018 — Silero VAD for turn endpointing, reused from faster-whisper's own bundle

**Status:** Accepted · 2026-09-22

## Context

T-038 Phase 2's real hardware run measured ~5.9s median turn latency, of
which ~5.2s was `lakewood.voice`'s fixed 5-second `sd.rec()` capture window
— the loop always waited the full 5 seconds regardless of how long the
customer actually spoke. T-050 requires replacing that fixed window with
real voice-activity-based endpointing: start capture, detect when the
customer starts and stops speaking, stop as soon as speech ends (or a
safety cap is hit), never on a clock.

Candidates named by the task: **Silero VAD** or **WebRTC VAD** (`webrtcvad`).

## Decision

**Silero VAD**, via the ONNX model + `onnxruntime` inference code
`faster_whisper.vad` already ships (`faster_whisper/assets/silero_vad_v6.onnx`,
`faster_whisper.vad.SileroVADModel`/`get_vad_model`) — the exact model
faster-whisper itself uses internally for its own `vad_filter=True` option.
Not a new dependency: `faster-whisper>=1.0` is already an optional
requirement (ADR-008), and `onnxruntime` is already its own transitive
dependency (`pip show onnxruntime` → `Required-by: faster-whisper`).

`lakewood/vad.py` splits this into two deliberately separate pieces:

- `VadConfig`/`Endpointer` — a pure Python state machine (elapsed time +
  speech probability in, stop/continue out). No audio, no model, no numpy.
  Fully unit-tested with synthetic probability sequences
  (`tests/test_vad.py`).
- `lakewood.voice.SoundDeviceMicrophone` — the real, hardware-and-model
  dependent capture loop that feeds `Endpointer` real Silero probabilities.
  Lazy-imported (`sounddevice`, `faster_whisper.vad`), same pattern as
  every other optional provider in this codebase.

## Why Silero over WebRTC VAD

- **Zero net-new dependency.** WebRTC VAD (`webrtcvad`, a GMM-based, purely
  energy/spectral classifier from the original libwebrtc project) would be
  a new pip package and a new thing to keep installed/compatible. Silero is
  already inside an optional dependency this project already documents and
  already runs (ADR-008/ADR-016).
- **More robust to non-speech noise.** WebRTC VAD is a 2011-era classifier
  tuned for clean VoIP audio; it is well documented to false-trigger on
  non-speech transients (clatter, HVAC, background chatter) — exactly the
  acoustic environment a restaurant counter/kitchen phone line sits in.
  Silero is a small neural net trained specifically to discriminate speech
  from noise, not just energy from silence. Given this project's own
  priority order (P0 order correctness, P1 correctness, ... P6 latency), a
  VAD that clips a customer's words because a fryer basket clattered is a
  worse failure than a VAD that is a few milliseconds slower — accuracy of
  the endpoint decision matters more here than shaving the last bit of CPU.
- **Already load-bearing.** faster-whisper's own transcription quality
  already depends on this exact model behaving correctly (its `vad_filter`
  option), so there is no new unverified component being introduced — only
  a new caller of one that already has to work.

WebRTC VAD remains a documented fallback if Silero/onnxruntime CPU cost
turns out to matter on constrained hardware — not evaluated further because
nothing in this project's stated constraints (Windows dev machine and
eventual restaurant-side hardware, not an embedded device) makes that
tradeoff live yet.

## Why re-run-the-growing-buffer instead of one call per frame

`SileroVADModel.__call__` re-zeroes its internal LSTM hidden state
(`h`, `c`) on every call — it is built for one-shot batch chunking of a
complete recording (`get_speech_timestamps`), not literal per-frame
streaming. Calling it once per single ~32ms frame would mean the model
evaluates each frame with no memory of the frames before it, degrading the
speech/silence discrimination it's chosen for in the first place.

`SoundDeviceMicrophone` instead re-runs the model over the FULL buffer
captured so far on every ~150ms poll, so every call gets real temporal
context across the whole utterance-to-date (the same shape of input the
model is validated on). This recomputes earlier frames repeatedly, which is
cheap for a model this size (Silero is documented at roughly 1ms per second
of audio on CPU) over utterances capped at 15 seconds — trading a small,
bounded amount of redundant CPU work for correctness, not the other way
around.

## Endpointing defaults (`lakewood.vad.VadConfig`, all env-tunable)

| Parameter | Default | Reasoning |
|---|---|---|
| `threshold` | 0.5 | Silero's own documented default; no field data yet to justify deviating |
| `min_silence_ms` | 700 | Long enough to survive an ordinary filled pause ("large... um... pepperoni" — typically 300-600ms); short enough not to noticeably drag a turn. A judgment call, not a measured optimum — see Consequences |
| `min_speech_ms` | 200 | Filters a single noise blip (a clatter, a cough) from counting as "the customer started talking" |
| `no_speech_timeout_seconds` | 6.0 | Distinct from `min_silence_ms` (which only applies AFTER speech begins) — a customer who says nothing must not hold the line open forever |
| `max_utterance_seconds` | 15.0 | Absolute safety cap regardless of VAD state — background noise that keeps tripping the threshold, or a customer who never pauses, must not hang the call indefinitely |

## Consequences

- No real customer-call audio has been used to tune any of the above —
  these are reasonable starting points, not measured-optimal values. Field
  tuning against real calls (once a pilot exists) may move them.
- VAD-based capture requires `faster-whisper` installed even when
  `LAKEWOOD_STT_PROVIDER=parakeet` is the actual transcription provider —
  a real, disclosed coupling, not incidental: it is the cost of reusing an
  already-verified model instead of adding a second VAD dependency.
- `lakewood.voice.SoundDeviceMicrophone.capture(path, seconds)` keeps its
  existing signature for interface compatibility with `FakeMic` in tests,
  but `seconds` is now a MAX safety cap, not a fixed recording duration —
  a semantic change any other real caller of this class must be aware of.
- This does not choose the production telephony/voice vendor (ADR-004
  remains open) — it only replaces the local dev-loop's capture mechanism.

## Verification

`tests/test_vad.py` (7 cases): clean utterance stop timing, mid-sentence
pause survival, background-noise-blip rejection, silent-customer timeout,
continuous-speech safety cap, no-speech-vs-real-endpoint distinction, no
state leakage between turns — all synthetic, all offline, zero dependency
on the real Silero model or audio hardware.

`tests/test_voice_vad_microphone.py`: `SoundDeviceMicrophone` fails closed
with a clear, specific message when `sounddevice` or `faster_whisper.vad`
is unavailable, and `default_vad_config()`'s env-var wiring, both with
import-blocking so results don't depend on what happens to be installed.

What is **not** verified: the real Silero model's actual accuracy against
real restaurant-call audio (kitchen noise, phone-line compression, real
customer speech patterns) — this environment has a real microphone
(confirmed present) but no recorded real customer calls and no way for a
coding-agent session to literally speak into it. See STATUS.md's T-050
entry for what latency measurement WAS possible this session and how.
