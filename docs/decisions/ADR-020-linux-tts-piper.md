# ADR-020 — Piper is the Linux-deployable production TTS provider

**Status:** Accepted · 2026-09-24 (T-053 Phase 1 Part C)

## Context

SAPI (ADR-015) is Windows-only and cannot run on ADR-019's deployment
target (a Linux cloud host). Something behind the existing
`TextToSpeechProvider` interface (`lakewood/tts/base.py`) must replace it
for production; SAPI stays the dev default.

## Candidates evaluated

**Piper** (ONNX runtime, CPU-only, MIT license) and **Kokoro** (ONNX
runtime via `kokoro-onnx`, CPU-only) — both real, both installed and
measured in an isolated `uv` venv inside WSL (the Linux stand-in this
phase's own text authorizes), against the real, already-running Parakeet
warm service.

**Hosted TTS: not measured, disclosed as such, not fabricated.** No
hosted-TTS credentials (ElevenLabs/Azure/Polly/OpenAI TTS/etc.) exist in
this environment. Cost/licensing for hosted options is a matter of public
pricing pages, not something this task can respect this project's own
"never claim unverified behavior works" rule while inventing a latency or
intelligibility number for a provider never actually called. **This is a
real gap in this ADR, not glossed over** — see Consequences.

## Measurements, real numbers

### Time to first audio (warm, single readback-length sentence)

| | Piper | Kokoro |
|---|---|---|
| Model load (cold, one-time) | 0.744s | 0.838s |
| Synthesis, warm | **0.188s** | 1.533–1.618s |
| Streaming time-to-first-chunk | 0.08s | ≈ full synthesis time (this text was short enough to be one internal chunk — Kokoro's own streaming granularity didn't help at this length) |

Piper is ~8x faster to a finished utterance and has a real, working
sub-100ms streaming path even for a short sentence; Kokoro's chunking
only pays off on longer multi-sentence text, which most readbacks aren't.

### 8kHz μ-law intelligibility — real synthesize → transcode → Parakeet round trip

Method: 4 realistic readback carts (simple order, multi-modifier, half-
and-half + drink, a non-pizza multi-item order) synthesized by each
provider, resampled to 8kHz, μ-law-encoded (real lossy 8-bit companding,
not just a resample), decoded back to 16kHz PCM, sent to the real,
already-running Parakeet warm service, and scored on the ORDER-CRITICAL
words each cart actually contains (items, quantities, modifiers, halves).
Dollar totals checked separately, numerically (Parakeet transcribes money
as digits — "$19.32" — never as spelled words, so a literal
"nineteen"/"thirty"/"two" string match against digit output is a scoring
artifact, not an intelligibility failure; caught and corrected before
reporting, not left in as an inflated-looking gap).

| | Piper | Kokoro |
|---|---|---|
| Item/modifier word accuracy | **25/25 (100%)** | 23/25 (92%) |
| Dollar totals numerically correct | 4/4 | 4/4 |
| Real degradation observed | none | "twelve piece wings" → "12 peacewings" (the non-pizza cart) — a genuine word-boundary merge under 8kHz μ-law, not a scoring artifact |

Piper's own default pace (`length_scale=1.0`, no tuning) measured
**~169 WPM** on the simple-cart sentence (15 words / 5.33s) — faster than
SAPI's own tuned ~142 WPM conversational target (T-051/ADR-015) — and
still scored cleanly at 8kHz in this test. Per ADR-015's own caution
("faster speech has not been validated over compressed 8kHz phone-line
audio... conservative until it has been"), this ADR does **not** push
Piper's rate any faster than its trained default without further
validation — the measured evidence supports the default, not an
extrapolation past it.

**Human listening check: not performed.** This session cannot listen to
audio. The task's own text is explicit that "STT-intelligible isn't
always human-intelligible" — a real human listening pass over
`/tmp/piper_*.wav`/`/tmp/kokoro_*.wav` (or freshly regenerated samples)
remains an open item before a pilot call, not something this ADR can
close by itself.

### GPU contention

**Not a differentiator.** Both `piper-tts` and `kokoro-onnx` run
entirely on CPU via ONNX Runtime's CPU execution provider — neither
imports torch, neither touches CUDA. Measured directly: GPU memory
(`nvidia-smi`) read identically (1599 MiB, Parakeet's own resident model)
before and during Kokoro synthesis. Parakeet's ~2.6GB GPU footprint is
never contended by either TTS candidate.

### Cost and licensing

Piper: MIT-licensed, runs locally, zero per-call cost — the strongest
possible answer to ADR-004's "COGS must stay ≤25% of ARPU" constraint,
since TTS becomes pure compute (already-provisioned CPU), not a per-
minute vendor line item. Kokoro (`kokoro-onnx`): Apache-2.0, same zero-
marginal-cost shape. Hosted options carry a real per-character/per-minute
cost this ADR has not priced against real call volume, precisely because
no live measurement was taken (see above).

## Decision

**Piper**, `en_US-lessac-medium` voice, default settings
(`length_scale=1.0`, no rate tuning). `LAKEWOOD_TTS_PROVIDER=piper`
selects it (`lakewood/tts/piper_provider.py`, lazy-imported, same
optional-dependency pattern as `faster_whisper_provider.py`/
`postgres_repository.py`); `windows_sapi` remains the dev default per
ADR-015, unchanged. `piper-tts` added to `requirements.txt` as an
explicit optional dependency; the voice model itself is downloaded
separately (`python -m piper.download_voices en_US-lessac-medium`), not
pip-installed, and is gitignored (a large binary, same treatment as
`*.onnx`/`models/` already get).

## Invariants re-verified against the new provider

- **Readback completeness** (`test_property_every_line_and_total_appear_
  in_the_full_readback`, `test_mutation_dropping_a_line_makes_the_
  completeness_check_go_red`): unaffected by provider choice by
  construction — these assert against the domain-produced reply STRING
  before any TTS call happens; `_speak_reply` hands that same string,
  unmodified, to whichever provider's `synthesize()` is configured. Ran
  green in the full suite with `PiperTTSProvider` present.
- **Speakable-rendering layer** (`lakewood/speech.py`): same reasoning —
  provider-independent, text-level.
- **Rate ceiling**: re-justified above, not carried over unchanged —
  Piper's own default already tested clean at 8kHz; no SAPI-style manual
  rate table needed.
- **No reply spoken before the domain layer produced it**: unaffected —
  this guarantee lives in `_speak_reply`'s call order
  (`run_turn`/`begin_confirmation` complete before any `synthesize()`
  call), not in which provider `synthesize()` happens to be.

## Consequences

- `lakewood/tts/__init__.py::make_tts_provider` gains the `"piper"`
  branch. `lakewood/tts/piper_provider.py` new.
- `requirements.txt` gains `piper-tts>=1.8` (optional, lazy-imported).
- `.gitignore` gains `piper-voices/` (downloaded model binaries).
- `tests/test_piper_tts.py` new: missing-package and missing-model-file
  config errors (offline, always run), factory routing, and three
  model-dependent tests that skip cleanly without a downloaded model —
  the offline suite stays green with neither `piper-tts` nor a model
  file installed, same guarantee ADR-014 gives Postgres.
- **Open, not closed by this ADR:** a human listening pass; a real hosted-
  TTS comparison once credentials exist; `ARCHITECTURE.md`'s TTS row
  update (done alongside this ADR, see that file).
- No change to `orders.py`, `pricing.py`, `menu.py`, or any domain/pricing
  logic — this task only ever touches the TTS provider boundary.

## Alternatives considered

**Kokoro.** Rejected as the primary choice on the measured evidence
above (8x slower, one real 8kHz degradation this test found) — not
rejected outright; its higher perceptual quality (widely reported,
unverified by this session's own listening) may still be worth a human
comparison pass before fully closing the door on it for a later
quality-over-latency tradeoff.

**A hosted provider.** Not chosen, and not rejected either — genuinely
undecided, since no credential exists in this environment to measure it
honestly. Filed as an explicit open item, not silently dropped.
