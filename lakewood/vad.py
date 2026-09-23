"""
Voice-activity-based endpointing — replaces the fixed 5-second capture
window (T-038 Phase 2's dominant latency term, ~5.2s of ~5.9s/turn) with a
real end-of-speech detector. See ADR-018 for the Silero-vs-WebRTC choice.

Two layers, deliberately split:

- `Endpointer`/`VadConfig` below: a PURE state machine. It consumes
  (elapsed_seconds, speech_probability) samples and decides when a turn is
  over. No audio, no model, no numpy — fully unit-testable with synthetic
  probability sequences, so the actual turn-taking LOGIC (does a mid-
  sentence pause wrongly end the turn? does background noise wrongly start
  one? does a silent customer hang the call forever?) is verified without
  any hardware or optional dependency. This is what stays offline-green.

- `lakewood.voice`'s real microphone class feeds this state machine real
  Silero-VAD probabilities computed from live audio. That half is hardware/
  model-dependent and lazy-imported, same pattern as `sounddevice`,
  `faster-whisper`, `psycopg2` elsewhere in this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VadConfig:
    """All defaults are conservative, field-tunable starting points, not
    measured-optimal values — no real customer-call data exists yet to tune
    against (see ADR-018).

    threshold: Silero's own speech-probability cutoff. 0.5 is Silero's
        documented default and is left unchanged here — this project has no
        evidence yet that this restaurant's call audio needs a different one.
    min_silence_ms: how long silence must persist AFTER speech has started
        before the turn is considered over. This is the number that trades
        off "cuts people off mid-sentence" against "every turn drags," and
        it is genuinely a judgment call (see module docstring in voice.py
        and ADR-018's reasoning). 700ms: long enough to survive an ordinary
        thinking pause ("large... um... pepperoni" — a filled pause is
        typically 300-600ms), short enough to not noticeably drag a turn.
    min_speech_ms: a speech-probability blip shorter than this (a single
        loud clatter, a cough) does not count as "the customer started
        talking" — filters spurious one-frame triggers.
    no_speech_timeout_seconds: if NO speech is ever detected from the start
        of capture, how long to wait before giving up (distinct from
        min_silence_ms, which only applies AFTER speech has begun) — a
        customer who says nothing must not hold the line open forever.
    max_utterance_seconds: absolute safety cap regardless of VAD state —
        background noise that keeps tripping the speech threshold, or a
        customer who genuinely never pauses, must not hang the call
        indefinitely. This is the same safety-first instinct as printer.py's
        retry cap or orders.py's MAX_DISAMBIGUATION_ASKS: an upper bound
        that always terminates, even when the "smart" logic doesn't.
    """
    threshold: float = 0.5
    min_silence_ms: int = 700
    min_speech_ms: int = 200
    no_speech_timeout_seconds: float = 6.0
    max_utterance_seconds: float = 15.0


class Endpointer:
    """Feed `update(elapsed_seconds, speech_prob)` in increasing-time order
    after every new audio chunk; it returns True the instant capture should
    stop. One instance is single-use — construct a fresh one per turn."""

    def __init__(self, config: VadConfig | None = None):
        self.config = config or VadConfig()
        self._speech_started = False
        self._speech_run_start: float | None = None
        self._silence_run_start: float | None = None

    def update(self, elapsed_seconds: float, speech_prob: float) -> bool:
        c = self.config
        is_speech = speech_prob >= c.threshold

        if is_speech:
            if not self._speech_started:
                if self._speech_run_start is None:
                    self._speech_run_start = elapsed_seconds
                if (elapsed_seconds - self._speech_run_start) * 1000 >= c.min_speech_ms:
                    self._speech_started = True
            self._silence_run_start = None
        else:
            self._speech_run_start = None
            if self._speech_started and self._silence_run_start is None:
                self._silence_run_start = elapsed_seconds

        if self._speech_started and self._silence_run_start is not None:
            if (elapsed_seconds - self._silence_run_start) * 1000 >= c.min_silence_ms:
                return True
        if not self._speech_started and elapsed_seconds >= c.no_speech_timeout_seconds:
            return True
        if elapsed_seconds >= c.max_utterance_seconds:
            return True
        return False

    @property
    def speech_detected(self) -> bool:
        """Whether real speech (past min_speech_ms) was ever seen this
        turn — lets the caller distinguish a genuine no-input timeout from
        a normal end-of-speech stop, the same distinction
        `stt.base.UnusableAudioError` already makes downstream."""
        return self._speech_started
