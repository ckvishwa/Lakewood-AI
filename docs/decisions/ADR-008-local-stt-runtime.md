# ADR-008 — Local STT runtime: faster-whisper

**Status:** Superseded for the local pilot by ADR-016 · 2026-09-17

This ADR remains the historical rationale for the faster-whisper adapter,
which is still supported as a CPU fallback. The measured provider choice for
the local pilot is now Parakeet; see ADR-016.

## Context

Track B of the local model tier needs a local, offline speech-to-text
runtime behind a typed, provider-agnostic interface, so the eventual voice
pipeline (`CUSTOMER → TELEPHONY/VOICE → LANGUAGE UNDERSTANDING → ...`) can be
developed and evaluated without a paid STT API or telephony infrastructure.

Two realistic local options were named in the task: **faster-whisper**
(CTranslate2-based reimplementation of Whisper) and **whisper.cpp** (a C++
port with GGML/GGUF quantized models).

## Decision

**faster-whisper.** `pip install faster-whisper` — one command, no separate
compiler toolchain or model-conversion step. It bundles PyAV for audio
decoding, so no standalone `ffmpeg` install was needed either (verified:
`ffmpeg` is not on this machine's PATH and faster-whisper worked anyway).
whisper.cpp needs a C++ build (or a prebuilt binary per platform) and manual
GGUF model management — meaningfully more setup friction for a dev-loop tool
with no corresponding benefit here: this task's own scope excludes latency
tuning, and CPU performance between the two is comparable at the `small`
model size this task operates at.

## Implementation

- `lakewood/stt/base.py` — `STTProvider` protocol, `STTResult`/`Segment`
  dataclasses, `STTConfigError`/`STTCallError`/`UnusableAudioError`. No
  faster-whisper (or any vendor) type crosses this boundary — enforced by a
  static test (`tests/test_stt.py::
  test_no_faster_whisper_types_leak_past_the_stt_package`).
- `lakewood/stt/faster_whisper_provider.py` — the real adapter. Lazy-imports
  `faster_whisper` inside `__init__` so the rest of the test suite (and the
  fake provider) needs nothing installed; missing dependency raises
  `STTConfigError` with the exact `pip install` command, never a silent
  fallback.
- `lakewood/stt/fake.py` — `FakeSTTProvider`, deterministic and offline,
  mirroring `tests/test_ollama_provider.py`'s scripted-response pattern for
  the LLM side. All 16 behavior tests in `tests/test_stt.py` run against
  this; none require a model download.
- Config: `LAKEWOOD_STT_PROVIDER` (`fake` default, or `faster_whisper`),
  `LAKEWOOD_STT_MODEL` (default `small`), `LAKEWOOD_STT_DEVICE` (default
  `cpu`), `LAKEWOOD_STT_COMPUTE_TYPE` (default `int8`).

## Audio fixtures — an honest caveat

`lakewood/stt/fixtures/*.wav` (10 files) are **synthesized via Windows SAPI**
(`System.Speech.Synthesis`), not human-recorded — there is no microphone or
human-recording capability in this environment. This is a real limitation,
stated in `lakewood/stt/fixtures/manifest.json`'s own `_provenance` field,
not hidden: TTS audio has none of the accent variation, background noise,
phone-line compression, or disfluency real customer calls will have. These
fixtures prove the STT plumbing and the domain-weighted scoring method work
end to end against real audio bytes and a real local model — they are not
evidence of real-world accuracy. Before any accuracy claim for production,
this corpus needs real customer call recordings (or a realistic noise-
augmented set), not synthesized speech.

## Real finding from running these fixtures through faster-whisper (`small`)

Per-category domain-weighted accuracy (`python -m lakewood.stt.eval` with
`LAKEWOOD_STT_PROVIDER=faster_whisper`): sizes 9/9, toppings 11/11,
negations 3/3, scope 7/7 — all 100%. **Quantities: 1/5 (20%).** Whisper
normalizes spoken numbers to digits ("six" → "6", "eight" → "8"), which the
manifest's spelled-out expected words correctly flagged as a mismatch. This
is not a scoring bug — it's a real STT behavior with a real downstream
consequence: any interpreter (rule-based or LLM) parsing quantities from STT
output must handle digit-form numbers, not just spelled-out ones. See
`docs/STATUS.md` "Local Model Tier milestone" for the full transcript
evidence and the separate, more serious finding this session's testing also
surfaced (a real negation-handling defect in `RuleBasedInterpreter`,
unrelated to STT).

## Consequences

- `requirements.txt` gains `faster-whisper` as an **optional** dev/eval
  dependency (same tier as `pyyaml`/`pytest` — not part of the stdlib-only
  `lakewood/` runtime policy, and not imported unless
  `LAKEWOOD_STT_PROVIDER=faster_whisper` is actually selected).
- Production STT provider choice remains open — this is dev/eval tooling,
  same posture as ADR-007 for the LLM tier.
