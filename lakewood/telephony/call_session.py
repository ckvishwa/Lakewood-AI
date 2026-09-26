"""
The per-call audio engine (T-053 Phase 2 Part 2).

Framework- and vendor-agnostic by construction: this module never imports
`twilio_protocol` or touches a socket. The real WS transport (Part 3)
decodes provider frames, filters to the caller's own inbound track only
(Part 2's own "consume the caller's audio track only" requirement — done
at the transport layer, since only it knows a vendor-specific field like
Twilio's `media.track`), and pushes plain mu-law bytes into
`on_inbound_frame`. This class owns everything downstream of that: VAD
endpointing, STT, the SAME `PersistentChat.run_turn` text/local-voice
already use (never `orders.py` directly — ADR-021/this package's own
constraint), and TTS, entirely isolated per call (its own buffer, its own
`Endpointer`, its own `PersistentChat`/cart — Part 2's "carts never mix"
requirement).

Concurrency: `STT_GATE`/`TTS_GATE` (`concurrency.py`) bound the two real
shared-resource risks; `run_turn` itself is never gated (see that module's
own docstring for why each of those three decisions is what it is).
"""

from __future__ import annotations

import os
import tempfile
import wave
from dataclasses import dataclass, field
from typing import Callable

from .. import orders as oe
from ..chat import PersistentChat, TextInterpreter
from ..stt.base import STTCallError, STTProvider
from ..tts.base import TextToSpeechProvider
from ..vad import Endpointer, VadConfig
from ..voice import default_vad_config
from .audio_codec import (
    PHONE_SAMPLE_RATE_HZ, STT_SAMPLE_RATE_HZ, mulaw_to_pcm16, pcm16_to_mulaw, resample_pcm16,
)
from .concurrency import STT_GATE, TTS_GATE
from .failure_policy import TRANSFER_ANNOUNCEMENT, FailureEscalation

# Same wording voice.py's LocalVoiceLoop.turn() uses for the identical
# failure (STTCallError/silence) — one apology vocabulary across every
# call surface, not a phone-specific variant.
_STT_TROUBLE_REPLY = "Sorry, I didn't catch that — could you say that again?"

# T-050's own TTS output rate assumption for playback (WAV files piper/SAPI
# write are read at whatever rate their own header says — never assumed).


@dataclass
class CallTurnOutcome:
    """One completed conversational turn on the phone path — never a
    partial one; `on_inbound_frame` returns `None` until a full turn is
    ready, then exactly one of these."""
    transcript: str
    reply: str
    outbound_mulaw: bytes   # ready to frame into outbound `media` messages
    should_transfer: bool = False   # Part 4: repeated-failure escalation fired this turn


VadProbabilityFn = Callable[[bytes], float]   # 16kHz mono PCM16 bytes -> speech probability


class PhoneCallSession:
    """One instance per live call. Precondition, enforced by construction
    order, not checked here: `call.chat` must already be set (a resume
    offer, if any, already accepted/declined) — the same precondition
    `PersistentChat.disclosure_played_at`/`mark_disclosure_played()`
    themselves enforce (they raise `RuntimeError` otherwise). Part 3's
    real webhook/WS handler owns resolving that before this object is
    ever constructed."""

    def __init__(self, call: PersistentChat, interpreter: TextInterpreter,
                 stt: STTProvider, tts: TextToSpeechProvider,
                 vad_probability_fn: VadProbabilityFn,
                 vad_config: VadConfig | None = None,
                 poll_seconds: float = 0.15):
        self.call = call
        self.interpreter = interpreter
        self.stt = stt
        self.tts = tts
        self.vad_probability_fn = vad_probability_fn
        self.vad_config = vad_config or default_vad_config()
        self.poll_seconds = poll_seconds
        # Whole-CALL state, deliberately not reset per turn (unlike
        # everything in `_reset_turn`) — Part 4's escalation tracks
        # consecutive failures ACROSS turns.
        self._escalation = FailureEscalation()
        self._reset_turn()

    def _reset_turn(self) -> None:
        self._inbound_pcm8k = bytearray()
        self._turn_start: float | None = None
        self._last_poll_at: float | None = None
        self._endpointer = Endpointer(self.vad_config)

    # -- disclosure ----------------------------------------------------

    def on_call_started(self) -> bytes:
        """T-057: must be called once, before ANY inbound frame reaches
        `on_inbound_frame` — mirrors `LocalVoiceLoop.turn()`'s own
        call-start disclosure check, applied at the WHOLE-CALL level here
        instead of per-turn (the phone path has an explicit call-start
        hook the local-mic path never did). A no-op (empty audio) if this
        call already has a disclosure timestamp (a resumed session from
        Part 3's recovery path never re-plays it)."""
        if self.call.disclosure_played_at is not None:
            return b""
        outbound = self._synthesize_to_mulaw(oe.DISCLOSURE_TEXT)
        self.call.mark_disclosure_played()
        return outbound

    # -- inbound audio ---------------------------------------------------

    def on_inbound_frame(self, mulaw_payload: bytes, now: float) -> CallTurnOutcome | None:
        """Feed one inbound mu-law frame (the caller's own track only —
        the transport layer's job to filter). Returns a `CallTurnOutcome`
        once VAD says the turn is complete; otherwise `None` (still
        capturing). Raises `RuntimeError` if `on_call_started()` hasn't
        run yet — never silently accepts audio before disclosure."""
        if self.call.disclosure_played_at is None:
            raise RuntimeError(
                "on_inbound_frame called before on_call_started() — "
                "disclosure must play before any audio is processed")

        if self._turn_start is None:
            self._turn_start = now
            self._last_poll_at = now
        self._inbound_pcm8k.extend(mulaw_to_pcm16(mulaw_payload))

        if now - self._last_poll_at < self.poll_seconds:
            return None
        self._last_poll_at = now

        pcm16k = resample_pcm16(bytes(self._inbound_pcm8k), PHONE_SAMPLE_RATE_HZ, STT_SAMPLE_RATE_HZ)
        elapsed = now - self._turn_start
        prob = self.vad_probability_fn(pcm16k)
        if not self._endpointer.update(elapsed, prob):
            return None

        transcript, reply, is_failure = self._finish_turn(pcm16k)

        # Part 4: repeated-failure escalation — one counter across every
        # failure type (silence, STT error, LLM/provider trouble; see
        # failure_policy.py's own docstring for why). A successful turn
        # (is_failure=False) resets it, even if this turn itself asked a
        # clarifying question — that's normal conversation, not a failure.
        should_transfer = self._escalation.record(is_failure)
        if should_transfer:
            reply = TRANSFER_ANNOUNCEMENT

        outcome = CallTurnOutcome(transcript, reply, self._synthesize_to_mulaw(reply),
                                  should_transfer=should_transfer)
        self._reset_turn()
        return outcome

    def _finish_turn(self, pcm16k: bytes) -> tuple[str, str, bool]:
        """Returns `(transcript, reply, is_failure)` — NOT yet
        synthesized; `on_inbound_frame` applies the escalation policy and
        synthesizes exactly once, so a turn that escalates never
        synthesizes the ordinary apology only to immediately discard it
        for the transfer announcement instead."""
        if not self._endpointer.speech_detected:
            # Scenario 6 (Part 5): the customer said nothing at all.
            return "", _STT_TROUBLE_REPLY, True

        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(STT_SAMPLE_RATE_HZ)
                wf.writeframes(pcm16k)
            try:
                stt_result = STT_GATE.run(self.stt.transcribe, path, correlation_id=self.call.call_id)
            except STTCallError:
                return "", _STT_TROUBLE_REPLY, True
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        result = self.call.run_turn(self.interpreter, stt_result.transcript)
        # LLMInterpreter exposes last_provider_error for exactly this
        # (interpreter.py's own T-039A Part 4 comment); RuleBasedInterpreter
        # has no such concept — getattr keeps this call surface working
        # with either, never assuming the attribute exists.
        is_failure = getattr(self.interpreter, "last_provider_error", None) is not None
        return stt_result.transcript, result.reply, is_failure

    # -- outbound audio ----------------------------------------------------

    def _synthesize_to_mulaw(self, text: str) -> bytes:
        if not text:
            return b""
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            TTS_GATE.run(self.tts.synthesize, text, path)
            with wave.open(path, "rb") as wf:
                rate = wf.getframerate()
                channels = wf.getnchannels()
                pcm = wf.readframes(wf.getnframes())
            if channels != 1:
                raise RuntimeError(
                    f"TTS output has {channels} channels — the phone path "
                    f"requires mono; got a stereo/multi-channel WAV from "
                    f"{self.tts!r}")
            pcm8k = resample_pcm16(pcm, rate, PHONE_SAMPLE_RATE_HZ)
            return pcm16_to_mulaw(pcm8k)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
