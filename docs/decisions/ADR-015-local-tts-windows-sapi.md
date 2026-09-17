# ADR-015 — Local TTS: Windows SAPI for the local voice-loop MVP

**Status:** Accepted · 2026-09-16

## Decision

Use Windows SAPI through `lakewood/tts/windows_sapi.py` for T-038's local,
Windows-only push-to-talk loop. It synthesizes a WAV and plays it through the
system speaker. The provider sits behind `TextToSpeechProvider`; fake TTS is
the offline-test default.

## Rationale

SAPI is local/offline, already present on supported Windows developer machines,
and has no model download, GPU, licensing purchase, or Python runtime required.
That minimizes first-audio latency and operational friction for a primitive
local MVP. Kokoro remains a plausible later option, but would add model/runtime
installation and Windows packaging risk without evidence it is needed before
the first real loop is measured. Phone-quality voice is unverified until the
required hardware check.

## Consequences

No provider types cross `lakewood/tts/`; domain and persistence remain audio
agnostic. SAPI is not a telephony decision and does not supersede ADR-004.

**Evaluation risk (added T-038 Phase 2, 2026-09-17): SAPI-to-SAPI proves
plumbing, not voice or STT quality.** ADR-008's own fixtures
(`lakewood/stt/fixtures/*.wav`) are also SAPI-synthesized, flagged there as
unrepresentative of real speech. A local voice-loop smoke test that speaks
SAPI TTS output into a SAPI-adjacent or faster-whisper STT input is a clean
synthetic voice talking to a recognizer under lab conditions — no accent
variation, no background noise, no phone-line compression, no disfluency,
no kitchen noise behind a real caller. It proves the capture -> STT ->
interpreter -> domain -> TTS -> playback pipeline is wired correctly end to
end. **It is not voice-quality evidence and not STT-accuracy evidence.**
Any claim about how SAPI actually sounds to a caller, or how accurately STT
performs against a real human voice, needs a human speech source — this
ADR's choice does not provide one and was never meant to.
