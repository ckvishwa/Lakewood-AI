"""
T-053 Phase 2 Part 2 — the phone audio engine (`PhoneCallSession`).

No real audio hardware, no real Twilio account, no real Silero/Piper/
Parakeet model — a scripted VAD probability function stands in for the
model-dependent half `lakewood.voice.SoundDeviceMicrophone` normally
supplies (same lazy-import/fake-provider split every other optional
dependency in this codebase already uses), `FakeSTTProvider` stands in for
Parakeet, and a small real-WAV-writing fake stands in for Piper — real
enough that `_synthesize_to_mulaw`'s actual resample/encode code runs for
real, not mocked out.
"""

import array
import wave

import pytest

from lakewood.chat import PersistentChat
from lakewood.interpreter import RuleBasedInterpreter
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.stt.base import STTCallError
from lakewood.stt.fake import FakeSTTProvider
from lakewood.telephony.audio_codec import PHONE_SAMPLE_RATE_HZ, mulaw_to_pcm16
from lakewood.telephony.call_session import PhoneCallSession
from lakewood.tts.base import TTSResult
from lakewood.vad import VadConfig

DID = "+12037588880"
FROM_NUMBER = "+12035551234"

# Fast, deterministic thresholds for tests — real production values (in
# lakewood.voice.default_vad_config) are tuned for real speech cadence and
# would make every test here slow for no benefit.
_FAST_VAD = VadConfig(
    threshold=0.5, min_silence_ms=10, min_speech_ms=10,
    no_speech_timeout_seconds=0.5, max_utterance_seconds=2.0,
)


class _FakeWavTTS:
    """Writes a real, short WAV file (unlike `lakewood.tts.fake.FakeTTSProvider`,
    which writes nothing) — this module's own `_synthesize_to_mulaw` reads
    the file back to resample/encode it, so the fake has to produce real
    bytes, not just record that it was called. Deliberately NOT
    `PHONE_SAMPLE_RATE_HZ` (22050, like Piper's real voices) — exercises
    the real resample path, not just a pass-through."""

    def __init__(self, sample_rate: int = 22050):
        self.sample_rate = sample_rate
        self.spoken: list[str] = []

    def synthesize(self, text: str, output_path: str) -> TTSResult:
        self.spoken.append(text)
        n_samples = int(self.sample_rate * 0.2)   # 200ms
        pcm = array.array("h", [1000 if i % 2 == 0 else -1000 for i in range(n_samples)])
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())
        return TTSResult(output_path, "fake-wav", 0.0)

    def play(self, audio_path: str) -> None:
        return None


class ScriptedVad:
    """Returns probabilities from a fixed script, one per call, holding
    the last value once exhausted (so a test doesn't have to script every
    single poll if the tail is all silence/speech)."""

    def __init__(self, probs: list[float]):
        self._probs = list(probs)
        self._i = 0
        self.calls = 0

    def __call__(self, pcm16k_bytes: bytes) -> float:
        self.calls += 1
        value = self._probs[min(self._i, len(self._probs) - 1)]
        self._i += 1
        return value


def _mulaw_silence_frame(n_samples: int = 160) -> bytes:
    """160 samples = 20ms @ 8kHz, Twilio's real frame size."""
    from lakewood.telephony.audio_codec import pcm16_to_mulaw
    pcm = array.array("h", [0] * n_samples).tobytes()
    return pcm16_to_mulaw(pcm)


def _make_session(stt=None, tts=None, vad=None, vad_config=_FAST_VAD, **kwargs):
    repo = InMemorySessionRepository()
    call = PersistentChat.start(repo, DID, "CA_test_call", FROM_NUMBER)
    assert call.chat is not None   # fresh call/number — never a resume offer
    interpreter = RuleBasedInterpreter()
    stt = stt or FakeSTTProvider(default_transcript="large pepperoni")
    tts = tts or _FakeWavTTS()
    vad = vad or ScriptedVad([0.0])
    session = PhoneCallSession(call, interpreter, stt, tts, vad,
                               vad_config=vad_config, **kwargs)
    return session, call, stt, tts


# ---------------------------------------------------------------------------
# Disclosure
# ---------------------------------------------------------------------------

def test_disclosure_plays_once_on_call_started():
    session, call, _stt, tts = _make_session()
    assert call.disclosure_played_at is None
    audio = session.on_call_started()
    assert len(audio) > 0
    assert call.disclosure_played_at is not None
    assert tts.spoken[0]   # the disclosure text was actually synthesized


def test_disclosure_is_a_noop_on_an_already_disclosed_call():
    session, call, _stt, tts = _make_session()
    session.on_call_started()
    first_played_at = call.disclosure_played_at
    audio = session.on_call_started()
    assert audio == b""
    assert call.disclosure_played_at == first_played_at   # unchanged, not re-stamped
    assert len(tts.spoken) == 1   # never synthesized twice


def test_inbound_frame_before_disclosure_raises():
    session, _call, _stt, _tts = _make_session()
    with pytest.raises(RuntimeError, match="on_call_started"):
        session.on_inbound_frame(_mulaw_silence_frame(), now=0.0)


# ---------------------------------------------------------------------------
# A full turn: speech, then silence, transcribed, run through the real
# domain (RuleBasedInterpreter + PersistentChat.run_turn), synthesized back.
# ---------------------------------------------------------------------------

def test_full_turn_produces_a_real_transcript_and_reply():
    vad = ScriptedVad([0.9, 0.9, 0.9, 0.0, 0.0])   # speech, then silence
    stt = FakeSTTProvider(default_transcript="large pepperoni")
    session, call, _stt, tts = _make_session(stt=stt, vad=vad, poll_seconds=0.05)
    session.on_call_started()
    tts.spoken.clear()

    outcome = None
    now = 0.0
    for _ in range(20):
        now += 0.05
        outcome = session.on_inbound_frame(_mulaw_silence_frame(), now=now)
        if outcome is not None:
            break

    assert outcome is not None
    assert outcome.transcript == "large pepperoni"
    assert "pepperoni" in outcome.reply.lower() or "$" in outcome.reply
    assert len(outcome.outbound_mulaw) > 0
    assert len(call.chat.session.lines) == 1   # the real domain actually added the pizza
    assert tts.spoken == [outcome.reply]


def test_two_sessions_are_fully_isolated():
    """Part 2's own acceptance criterion: carts never mix."""
    vad_a = ScriptedVad([0.9, 0.9, 0.0, 0.0])
    vad_b = ScriptedVad([0.9, 0.9, 0.0, 0.0])
    stt_a = FakeSTTProvider(default_transcript="large pepperoni")
    stt_b = FakeSTTProvider(default_transcript="medium cheese pizza")

    repo = InMemorySessionRepository()
    call_a = PersistentChat.start(repo, DID, "CA_call_a", "+12035551111")
    call_b = PersistentChat.start(repo, DID, "CA_call_b", "+12035552222")
    interp_a, interp_b = RuleBasedInterpreter(), RuleBasedInterpreter()
    session_a = PhoneCallSession(call_a, interp_a, stt_a, _FakeWavTTS(), vad_a,
                                 vad_config=_FAST_VAD, poll_seconds=0.05)
    session_b = PhoneCallSession(call_b, interp_b, stt_b, _FakeWavTTS(), vad_b,
                                 vad_config=_FAST_VAD, poll_seconds=0.05)
    session_a.on_call_started()
    session_b.on_call_started()

    now = 0.0
    for _ in range(20):
        now += 0.05
        session_a.on_inbound_frame(_mulaw_silence_frame(), now=now)
        session_b.on_inbound_frame(_mulaw_silence_frame(), now=now)

    assert len(call_a.chat.session.lines) == 1
    assert len(call_b.chat.session.lines) == 1
    line_a = next(iter(call_a.chat.session.lines.values()))
    line_b = next(iter(call_b.chat.session.lines.values()))
    a_toppings = {t.name.upper() for t in line_a.toppings}
    b_toppings = {t.name.upper() for t in line_b.toppings}
    assert "PEPPERONI" in a_toppings
    assert "PEPPERONI" not in b_toppings


# ---------------------------------------------------------------------------
# Failure behavior (Part 4 groundwork, proven here at the engine level)
# ---------------------------------------------------------------------------

def test_no_speech_at_all_produces_an_apology_not_a_crash():
    vad = ScriptedVad([0.0])   # never speech
    session, call, _stt, tts = _make_session(vad=vad, poll_seconds=0.05)
    session.on_call_started()
    tts.spoken.clear()

    outcome = None
    now = 0.0
    for _ in range(20):
        now += 0.05
        outcome = session.on_inbound_frame(_mulaw_silence_frame(), now=now)
        if outcome is not None:
            break

    assert outcome is not None
    assert outcome.transcript == ""
    assert "didn't catch" in outcome.reply
    assert len(call.chat.session.lines) == 0   # nothing was ever ordered


def test_stt_failure_degrades_to_apology_not_an_exception():
    vad = ScriptedVad([0.9, 0.9, 0.0, 0.0])
    stt = FakeSTTProvider(default_transcript=STTCallError("provider crashed"))
    session, call, _stt, tts = _make_session(stt=stt, vad=vad, poll_seconds=0.05)
    session.on_call_started()

    outcome = None
    now = 0.0
    for _ in range(20):
        now += 0.05
        outcome = session.on_inbound_frame(_mulaw_silence_frame(), now=now)
        if outcome is not None:
            break

    assert outcome is not None
    assert "didn't catch" in outcome.reply
    assert len(call.chat.session.lines) == 0


def test_mono_tts_output_required():
    class _StereoTTS:
        def synthesize(self, text, output_path):
            with wave.open(output_path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(22050)
                wf.writeframes(array.array("h", [0, 0] * 100).tobytes())
            return TTSResult(output_path, "stereo-fake", 0.0)

        def play(self, audio_path):
            return None

    session, _call, _stt, _tts = _make_session(tts=_StereoTTS())
    with pytest.raises(RuntimeError, match="channels"):
        session.on_call_started()
