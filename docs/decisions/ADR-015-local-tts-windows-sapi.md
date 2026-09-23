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

## Amendment (T-051, 2026-09-22): speech rate ceiling + a speakable-rendering layer

**Finding:** SAPI's default `Rate=0` measured ~95-99 words/minute on a clean
readback sentence — genuinely slow (typical conversational/audiobook TTS
runs 150-180 wpm) — and a real 3-sentence confirmation readback measured
over 15 seconds to speak. Separately, several raw internal tokens
(`6PC WINGS`, `GARDEN SALAD SM`, `STRWBRY CHZCAKE`, a coupon's raw
`OFF_3_AT_30` code) were confirmed mispronounced by a real TTS->STT
round-trip test (SAPI reads `PC`/`SM`/`LG` as literal letters, `CHZCAKE` as
gibberish, and underscores as the spoken word "underscore"). Full evidence
and the before/after measurement table: `docs/STATUS.md`'s T-051 entry.

**Decision:**

1. `WindowsSapiTTSProvider` now sets `SpeechSynthesizer.Rate` explicitly
   (`lakewood/tts/windows_sapi.py`), default `DEFAULT_RATE = 3` (~142 wpm,
   normal conversational pace), overridable via `LAKEWOOD_TTS_RATE`, hard-
   capped at `MAX_SAFE_RATE = 4` (~158 wpm) — the constructor raises
   `TTSConfigError` above that. The ceiling is deliberate, not a measured
   optimum: this audio eventually crosses an 8kHz phone line, where speech
   intelligibility degrades with rate, and no rate above 4 has been
   validated over real compressed telephony audio in this project. Rate 5+
   (177+ wpm) was measured but NOT chosen as the default for this reason.
2. A new module, `lakewood/speech.py`, is the speakable-rendering layer
   between the deterministic readback builder (`orders.py`) and TTS. It
   maps a raw internal token (a menu SKU name, a coupon code) to how a
   person would say it — evidence-based per token (each mapping traces to a
   real TTS->STT round-trip result, not a guess) — and never decides WHAT
   is said, only HOW an already-chosen token is pronounced.
   `tests/test_speech.py` enforces completeness: any real menu item or
   topping that is neither explicitly mapped nor explicitly verified clean
   is a loud test failure, not a silent gap.
3. Money (`$16.10`-style formatting) was measured, not assumed, to already
   read correctly as a price (duration-matched a known-correct "sixteen
   dollars and ten cents" phrasing exactly) — deliberately left unchanged.

**Consequences:** a full confirmation readback's real spoken duration
dropped 37-47% across the four representative cart shapes measured (see
STATUS.md) from phrasing/token fixes alone, before the rate change; the
rate change is a further, separate, capped contribution on top. The
speakable-rendering layer is content-blind by construction — it cannot make
a readback less complete, only reword a token — so this amendment does not
touch F16's completeness guarantee (ADR-011).
