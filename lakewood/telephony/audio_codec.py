"""
mu-law codec + sample-rate conversion for the phone audio path
(T-053 Phase 2 Part 2).

```
provider WS (8kHz mu-law) -> decode -> resample 16kHz -> VAD -> STT
                                                                  |
                                        [PersistentChat/run_turn]
                                                                  |
provider WS (8kHz mu-law) <- encode <- resample 8kHz <- TTS   <--
```

Wraps stdlib `audioop` (`ulaw2lin`/`lin2ulaw` for the codec, `ratecv` for
rate conversion) rather than hand-rolling either. Both are exact,
decades-battle-tested primitives for exactly this telephony use case — a
subtly-wrong hand-rolled resampler or mu-law table would degrade real
caller audio in a way nothing in this codebase's offline tests could
catch (this project's own moat is accuracy on REAL speech, not a plausible-
looking waveform), so this is "buy, don't build" applied to a DSP
primitive the same way ADR-021 applied it to webhook signature validation.

**Known, disclosed risk:** `audioop` is deprecated and slated for removal
in Python 3.13 (this project currently pins/runs 3.12 in CI and locally —
confirmed, not assumed). Replacing it now, before an upgrade actually
forces the question, would be exactly the kind of speculative work
CLAUDE.md says not to do ("don't build for a hypothetical future
requirement"). Revisit this module specifically before any Python 3.13+
upgrade — filed in `docs/NEXT_TASKS.md`.
"""

from __future__ import annotations

import audioop  # noqa: F401 — stdlib, deprecated (removal in 3.13); see module docstring

PHONE_SAMPLE_RATE_HZ = 8000
STT_SAMPLE_RATE_HZ = 16000   # matches lakewood.voice's SoundDeviceMicrophone / Silero VAD


def mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    """8kHz mu-law bytes (Twilio's `media.payload`, base64-decoded by the
    caller) -> 16-bit signed linear PCM bytes, same sample rate."""
    return audioop.ulaw2lin(mulaw_bytes, 2)


def pcm16_to_mulaw(pcm16_bytes: bytes) -> bytes:
    """16-bit signed linear PCM bytes -> 8kHz mu-law bytes, ready to
    base64-encode into an outbound `media` message."""
    return audioop.lin2ulaw(pcm16_bytes, 2)


def resample_pcm16(pcm16_bytes: bytes, from_rate_hz: int, to_rate_hz: int) -> bytes:
    """Mono 16-bit linear PCM at `from_rate_hz` -> the same at `to_rate_hz`.
    One-shot (no streaming state carried across calls) — matches
    `lakewood.voice.SoundDeviceMicrophone`'s own pattern of recomputing over
    the full buffer each time rather than threading incremental state,
    for the same reason: simplicity over a marginal CPU saving at this
    call volume."""
    if from_rate_hz == to_rate_hz:
        return pcm16_bytes
    converted, _state = audioop.ratecv(pcm16_bytes, 2, 1, from_rate_hz, to_rate_hz, None)
    return converted
