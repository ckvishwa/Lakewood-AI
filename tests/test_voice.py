"""T-038 Phase 2 correction #2 — proves the voice loop drives turns through
the exact same `run_turn` the text path (chat.py's PersistentChat.run_turn)
uses, not a forked copy (same discipline as T-030's
tests/test_viewer_shared_path.py for the live viewer)."""
import inspect

from lakewood import chat as chat_module
from lakewood.chat import PersistentChat
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.stt.fake import FakeSTTProvider
from lakewood.tts.fake import FakeTTSProvider
from lakewood.voice import LocalVoiceLoop, VoiceTurn, report_latency


class FakeMic:
    def capture(self, path, seconds):
        open(path, "wb").close()
        return seconds


def _make_loop(monkeypatch, transcript="large pepperoni"):
    monkeypatch.setenv("LAKEWOOD_INTERPRETER", "rule_based")
    repo = InMemorySessionRepository()
    call = PersistentChat.start(repo, "+12037588880", "VOICE", "+12035551234")
    stt = FakeSTTProvider(default_transcript=transcript)
    loop = LocalVoiceLoop(call, stt, FakeTTSProvider(), FakeMic())
    return repo, loop


def test_voice_uses_persistent_chat_path(monkeypatch):
    repo, loop = _make_loop(monkeypatch)
    result = loop.turn()
    assert result.transcript == "large pepperoni"
    assert repo.load_session("STORE-001", "VOICE") is not None


def test_voice_and_text_paths_call_the_identical_run_turn_function(monkeypatch):
    """Spies on the module-level run_turn both PersistentChat.run_turn (used
    by chat.py's text sandbox, per chat.py:452) and LocalVoiceLoop.turn
    (voice.py:37) call through — proves they converge on one implementation,
    not two independently-maintained turn loops."""
    calls = []
    original = chat_module.run_turn

    def spy(*args, **kwargs):
        calls.append(id(original))
        return original(*args, **kwargs)

    monkeypatch.setattr(chat_module, "run_turn", spy)

    repo, loop = _make_loop(monkeypatch)
    # Path 1: voice loop.
    loop.turn()
    # Path 2: the same call object's run_turn, exactly as chat.py's text
    # sandbox main() calls it (chat.py:452: call.run_turn(interpreter, text)).
    loop.call.run_turn(loop.interpreter, "add mushroom")

    assert len(calls) == 2
    assert len(set(calls)) == 1  # both paths reached the identical function object


def test_voice_module_has_no_second_turn_loop_or_naked_session():
    """Static guard: voice.py must not construct its own oe.Session or call
    PersistentChat internals directly — every turn must go through
    call.run_turn, the one path chat.py's text sandbox also uses."""
    import lakewood.voice as voice_module
    src = inspect.getsource(voice_module)
    assert "Session(" not in src
    assert "def run_turn" not in src  # no local reimplementation
    assert src.count(".run_turn(") == 1  # exactly one call site: self.call.run_turn(...)


def test_report_latency_reports_median_and_p95_per_stage():
    turns = [
        VoiceTurn("t", "r", capture_seconds=c, stt_seconds=c, app_seconds=c, tts_seconds=c, total_seconds=c)
        for c in (1.0, 2.0, 3.0, 4.0, 5.0)
    ]
    report = report_latency(turns)
    assert "5 turn(s)" in report
    for stage in ("capture", "stt", "app", "tts", "total"):
        assert stage in report
    assert "median= 3.000s" in report or "median=3.000s" in report


def test_report_latency_handles_no_turns():
    assert report_latency([]) == "no turns recorded"


# --- T-039 Part 3: unusable audio degrades the turn, never kills the loop --

def test_unusable_audio_does_not_crash_the_voice_loop(monkeypatch):
    """Regression for the real T-038 Phase 2 finding: silence/background
    noise/a hesitation makes the STT provider raise UnusableAudioError (a
    real HTTP 422 from the Parakeet server in production), and this used to
    propagate straight out of LocalVoiceLoop.turn() with nothing catching
    it — on a phone line, an uncaught exception here is a dropped call. A
    turn like this must degrade to a spoken apology and let the call
    continue, not raise."""
    monkeypatch.setenv("LAKEWOOD_INTERPRETER", "rule_based")
    repo = InMemorySessionRepository()
    call = PersistentChat.start(repo, "+12037588880", "VOICE", "+12035551234")
    stt = FakeSTTProvider(default_transcript="")  # empty -> UnusableAudioError
    loop = LocalVoiceLoop(call, stt, FakeTTSProvider(), FakeMic())

    result = loop.turn()  # must not raise

    assert result.transcript == ""
    assert "didn't catch that" in result.reply.lower()
    # The turn never reached the interpreter: no phantom mutation, no wasted
    # confirmation-gate increment for an utterance that was never heard.
    assert call.chat.session.order.lines == []


def test_voice_loop_recovers_and_continues_after_unusable_audio(monkeypatch):
    """The call must survive PAST the bad turn, not just avoid crashing on
    it — the next real turn still reaches the real interpreter/domain."""
    monkeypatch.setenv("LAKEWOOD_INTERPRETER", "rule_based")
    repo = InMemorySessionRepository()
    call = PersistentChat.start(repo, "+12037588880", "VOICE", "+12035551234")
    stt = FakeSTTProvider(default_transcript="")
    loop = LocalVoiceLoop(call, stt, FakeTTSProvider(), FakeMic())

    loop.turn()  # unusable audio, degrades gracefully
    stt.default_transcript = "large pepperoni"  # the customer tries again
    result = loop.turn()

    assert result.transcript == "large pepperoni"
    line = call.chat.session.order.lines[0]
    assert line.size == "LARGE"
    assert any(t.name == "PEPPERONI" for t in line.toppings)
