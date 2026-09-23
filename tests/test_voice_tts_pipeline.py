"""
T-050 Part 3 (sentence-pipelined TTS) and Part 4 (capture/playback never
overlap) — no audio hardware, no VAD library, no real TTS engine. Everything
here runs against `_RecordingTTS`/fake mics, offline.
"""

import time

from lakewood.chat import PersistentChat
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.stt.fake import FakeSTTProvider
from lakewood.tts.base import TTSResult
from lakewood.voice import LocalVoiceLoop, _split_sentences


def test_split_sentences_splits_on_boundaries():
    assert _split_sentences("Got it — added a large pepperoni. Anything else?") == [
        "Got it — added a large pepperoni.", "Anything else?"]


def test_split_sentences_single_sentence_stays_one_item():
    assert _split_sentences("Got it.") == ["Got it."]


def test_split_sentences_empty_reply_is_safe():
    assert _split_sentences("") == [""]


def test_split_sentences_no_trailing_punctuation_still_one_item():
    assert _split_sentences("What size would you like") == ["What size would you like"]


class _FakeMic:
    def capture(self, path, seconds):
        open(path, "wb").close()
        return 0.01


class _RecordingTTS:
    """Records (kind, text-or-path, elapsed) for every synthesize/play call,
    with a configurable synth delay so pipelining overlap is provable."""
    def __init__(self, synth_delay=0.03):
        self.synth_delay = synth_delay
        self.events: list[tuple[str, str, float]] = []
        self._t0 = time.monotonic()

    def synthesize(self, text, output_path):
        time.sleep(self.synth_delay)
        self.events.append(("synth", text, time.monotonic() - self._t0))
        return TTSResult(output_path, "recording-fake", self.synth_delay)

    def play(self, audio_path):
        self.events.append(("play", audio_path, time.monotonic() - self._t0))


def _make_loop(monkeypatch, call_id="VOICE-TTS", tts=None, mic=None):
    monkeypatch.setenv("LAKEWOOD_INTERPRETER", "rule_based")
    repo = InMemorySessionRepository()
    call = PersistentChat.start(repo, "+12037588880", call_id, "+12035551234")
    stt = FakeSTTProvider(default_transcript="large pepperoni")
    loop = LocalVoiceLoop(call, stt, tts or _RecordingTTS(), mic or _FakeMic())
    return call, loop, stt


def test_multi_sentence_reply_synthesizes_and_plays_each_sentence_in_order(monkeypatch):
    # A larger delay (vs T-050's original 0.02s) keeps the first-audio-vs-
    # total-synthesis comparison robust to thread-scheduling jitter under
    # system load (observed flaky at 0.02s on a loaded machine) without
    # weakening what the assertions actually claim.
    tts = _RecordingTTS(synth_delay=0.08)
    call, loop, stt = _make_loop(monkeypatch, tts=tts)
    turn = loop.turn()  # "Got it — added a large pepperoni. Anything else?" (2 sentences)

    kinds = [e[0] for e in tts.events]
    synths = [e for e in tts.events if e[0] == "synth"]
    plays = [e for e in tts.events if e[0] == "play"]
    assert len(synths) == len(plays) >= 1
    # every sentence's synth completes strictly before its own play
    for s, p in zip(synths, plays):
        assert s[2] <= p[2]
    # first audio arrives at ~the FIRST sentence's synth time, not the whole
    # reply's — the actual pipelining claim. A small tolerance absorbs
    # thread-scheduling jitter in `time.sleep`, not the claim itself.
    assert turn.tts_first_audio_seconds >= tts.synth_delay * 0.7
    if len(synths) > 1:
        assert turn.tts_first_audio_seconds < turn.tts_seconds


def test_reply_text_is_never_spoken_before_the_domain_layer_produced_it(monkeypatch):
    """Never let partial/streamed text bypass validation: every sentence
    handed to synthesize() must be a substring of the ALREADY-COMPLETE
    domain-produced reply — nothing is ever spoken before `run_turn`
    finishes building the real reply string."""
    tts = _RecordingTTS(synth_delay=0.0)
    call, loop, stt = _make_loop(monkeypatch, tts=tts)
    turn = loop.turn()
    spoken_text = " ".join(e[1] for e in tts.events if e[0] == "synth")
    for word in spoken_text.split():
        assert word.strip(".,!?") in turn.reply


def test_capture_never_starts_before_previous_turns_playback_finished(monkeypatch):
    """T-050 Part 4: half-duplex by construction — capture for turn N+1
    must never appear before the LAST play of turn N. Runs a 3-turn
    sequence (build cart, quote, begin_confirmation/readback) so it covers
    the exact readback scenario Part 4 calls out."""
    order: list[str] = []

    class _TrackingMic(_FakeMic):
        def capture(self, path, seconds):
            order.append("capture")
            return super().capture(path, seconds)

    class _TrackingTTS(_RecordingTTS):
        def play(self, audio_path):
            order.append("play")
            return super().play(audio_path)

    tts = _TrackingTTS(synth_delay=0.01)
    mic = _TrackingMic()
    call, loop, stt = _make_loop(monkeypatch, tts=tts, mic=mic)

    stt.default_transcript = "large pepperoni"
    loop.turn()
    stt.default_transcript = "what's my total?"
    loop.turn()
    stt.default_transcript = "yes place it"
    loop.turn()  # begin_confirmation — this turn's reply is the readback

    assert order[0] == "capture"
    capture_positions = [i for i, k in enumerate(order) if k == "capture"]
    # every capture after the first is immediately preceded by a play —
    # i.e. the previous turn's audio had fully finished before this
    # capture began. Never a capture immediately after another capture.
    for i in capture_positions[1:]:
        assert order[i - 1] == "play", (
            f"capture at index {i} was not immediately preceded by a play: {order}")


def test_confirmation_readback_audio_never_bleeds_into_next_turns_transcript(monkeypatch):
    """The interpreter/transcript path is untouched by TTS timing at all —
    a customer's NEXT utterance is a fresh STT call on a fresh capture,
    never merged with the readback. Regression guard for the exact failure
    mode Part 4 warns about (audio during the readback silently becoming
    part of the confirmation)."""
    call, loop, stt = _make_loop(monkeypatch)
    stt.default_transcript = "large pepperoni"
    loop.turn()
    stt.default_transcript = "what's my total?"
    loop.turn()
    stt.default_transcript = "yes place it"
    readback_turn = loop.turn()
    assert "begin_confirmation" not in readback_turn.reply  # sanity: real readback text
    stt.default_transcript = "go ahead"
    confirm_turn = loop.turn()
    # the confirm turn's own transcript is exactly what STT returned for
    # THIS capture — never the readback text, never a merge of the two.
    assert confirm_turn.transcript == "go ahead"
