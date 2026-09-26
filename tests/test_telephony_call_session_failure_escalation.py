"""
T-053 Phase 2 Part 4 — repeated-failure escalation, proven at the
`PhoneCallSession` level (not just `failure_policy.py`'s own unit tests)
against the real turn-completion path: two consecutive silent/STT-failed/
LLM-failed turns produce a `CallTurnOutcome` with `should_transfer=True`
and the transfer announcement as its reply — never a third repeat of the
same apology.
"""

import array
import wave

from lakewood.chat import PersistentChat
from lakewood.interpreter import Interpretation, RuleBasedInterpreter
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.stt.base import STTCallError
from lakewood.stt.fake import FakeSTTProvider
from lakewood.telephony.call_session import PhoneCallSession
from lakewood.telephony.failure_policy import TRANSFER_ANNOUNCEMENT
from lakewood.tts.base import TTSResult
from lakewood.vad import VadConfig

DID = "+12037588880"
FROM_NUMBER = "+12035551234"

_FAST_VAD = VadConfig(threshold=0.5, min_silence_ms=10, min_speech_ms=10,
                       no_speech_timeout_seconds=0.5, max_utterance_seconds=2.0)


class _FakeWavTTS:
    def __init__(self):
        self.spoken: list[str] = []

    def synthesize(self, text, output_path):
        self.spoken.append(text)
        n = int(22050 * 0.1)
        pcm = array.array("h", [500] * n)
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(pcm.tobytes())
        return TTSResult(output_path, "fake-wav", 0.0)

    def play(self, audio_path):
        return None


class _FailingProviderInterpreter:
    """Simulates an LLM/provider failure on every turn — mirrors
    `LLMInterpreter`'s own `last_provider_error` contract without needing
    a real network call."""

    def __init__(self):
        self.last_provider_error = None

    def interpret(self, chat, text):
        self.last_provider_error = "simulated provider failure"
        return Interpretation(say="Sorry, I'm having trouble right now — could you repeat that?")


class ScriptedVad:
    def __init__(self, probs):
        self._probs = list(probs)
        self._i = 0

    def __call__(self, pcm16k_bytes):
        value = self._probs[min(self._i, len(self._probs) - 1)]
        self._i += 1
        return value


def _mulaw_silence_frame(n_samples: int = 160) -> bytes:
    from lakewood.telephony.audio_codec import pcm16_to_mulaw
    return pcm16_to_mulaw(array.array("h", [0] * n_samples).tobytes())


def _make_session(interpreter=None, stt=None, vad=None):
    repo = InMemorySessionRepository()
    call = PersistentChat.start(repo, DID, "CA_escalation_test", FROM_NUMBER)
    interpreter = interpreter or RuleBasedInterpreter()
    stt = stt or FakeSTTProvider(default_transcript="large pepperoni")
    vad = vad or ScriptedVad([0.0])
    session = PhoneCallSession(call, interpreter, stt, _FakeWavTTS(), vad,
                               vad_config=_FAST_VAD, poll_seconds=0.05)
    session.on_call_started()
    return session


def _drive_one_silent_turn(session, start_now: float) -> "object":
    now = start_now
    outcome = None
    for _ in range(20):
        now += 0.05
        outcome = session.on_inbound_frame(_mulaw_silence_frame(), now=now)
        if outcome is not None:
            break
    assert outcome is not None
    return outcome, now


def test_two_consecutive_silent_turns_escalate_to_transfer():
    vad = ScriptedVad([0.0])   # never speech, every turn
    session = _make_session(vad=vad)

    first, now = _drive_one_silent_turn(session, 0.0)
    assert first.should_transfer is False
    assert "didn't catch" in first.reply

    second, _now = _drive_one_silent_turn(session, now + 1.0)
    assert second.should_transfer is True
    assert second.reply == TRANSFER_ANNOUNCEMENT
    assert len(second.outbound_mulaw) > 0


def test_two_consecutive_llm_failures_escalate_to_transfer():
    interpreter = _FailingProviderInterpreter()
    vad = ScriptedVad([0.9, 0.9, 0.0, 0.0])
    session = _make_session(interpreter=interpreter, vad=vad)

    now = 0.0
    outcome1 = None
    for _ in range(20):
        now += 0.05
        outcome1 = session.on_inbound_frame(_mulaw_silence_frame(), now=now)
        if outcome1 is not None:
            break
    assert outcome1 is not None
    assert outcome1.should_transfer is False

    vad2 = ScriptedVad([0.9, 0.9, 0.0, 0.0])
    session.vad_probability_fn = vad2
    outcome2 = None
    for _ in range(20):
        now += 0.05
        outcome2 = session.on_inbound_frame(_mulaw_silence_frame(), now=now)
        if outcome2 is not None:
            break
    assert outcome2 is not None
    assert outcome2.should_transfer is True
    assert outcome2.reply == TRANSFER_ANNOUNCEMENT


def test_a_successful_turn_between_failures_resets_the_escalation():
    stt = FakeSTTProvider(default_transcript="large pepperoni")
    vad = ScriptedVad([0.0])
    session = _make_session(stt=stt, vad=vad)

    first, now = _drive_one_silent_turn(session, 0.0)
    assert first.should_transfer is False

    # A real successful turn in between.
    session.vad_probability_fn = ScriptedVad([0.9, 0.9, 0.0, 0.0])
    success_outcome = None
    for _ in range(20):
        now += 0.05
        success_outcome = session.on_inbound_frame(_mulaw_silence_frame(), now=now)
        if success_outcome is not None:
            break
    assert success_outcome is not None
    assert success_outcome.transcript == "large pepperoni"
    assert success_outcome.should_transfer is False

    # Back to silence — this is only the FIRST failure again, not the second.
    session.vad_probability_fn = ScriptedVad([0.0])
    third, _now = _drive_one_silent_turn(session, now + 1.0)
    assert third.should_transfer is False


def test_stt_call_error_counts_toward_escalation():
    stt = FakeSTTProvider(default_transcript=STTCallError("provider crashed"))
    vad = ScriptedVad([0.9, 0.9, 0.0, 0.0])
    session = _make_session(stt=stt, vad=vad)

    now = 0.0
    for turn in range(2):
        session.vad_probability_fn = ScriptedVad([0.9, 0.9, 0.0, 0.0])
        outcome = None
        for _ in range(20):
            now += 0.05
            outcome = session.on_inbound_frame(_mulaw_silence_frame(), now=now)
            if outcome is not None:
                break
        assert outcome is not None
        if turn == 0:
            assert outcome.should_transfer is False
        else:
            assert outcome.should_transfer is True
            assert outcome.reply == TRANSFER_ANNOUNCEMENT
