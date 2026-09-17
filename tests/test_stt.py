"""
T-013 Track B: lakewood/stt/ interface + fake provider + domain-weighted
scoring. Everything here runs offline, no model load — real faster-whisper
transcription is exercised separately (see test file docstring note in
docs/STATUS.md "Local Model Tier milestone"; not part of this offline suite
by design, matching the fake-LLM-provider pattern already established).
"""

import os

import pytest

from lakewood.stt.base import (
    STTCallError, STTConfigError, STTResult, Segment, UnusableAudioError,
)
from lakewood.stt.eval import _load_manifest, score, summarize
from lakewood.stt.fake import FakeSTTProvider


# --- base types --------------------------------------------------------------

def test_stt_result_shape():
    r = STTResult(transcript="large pepperoni", confidence=0.9, provider="fake",
                  correlation_id="abc")
    assert r.transcript == "large pepperoni"
    assert r.segments == []


def test_segment_shape():
    s = Segment(text="large", start_seconds=0.0, end_seconds=0.5)
    assert s.text == "large"


# --- fake provider -----------------------------------------------------------

def test_fake_provider_returns_scripted_transcript():
    provider = FakeSTTProvider({"a.wav": "large pepperoni"})
    r = provider.transcribe("a.wav", correlation_id="c1")
    assert r.transcript == "large pepperoni"
    assert r.provider == "fake"
    assert r.correlation_id == "c1"
    assert provider.calls == ["a.wav"]


def test_fake_provider_raises_stt_call_error_for_unscripted_path():
    provider = FakeSTTProvider({})
    with pytest.raises(STTCallError):
        provider.transcribe("unknown.wav")


def test_fake_provider_can_script_a_failure():
    provider = FakeSTTProvider({"bad.wav": STTCallError("simulated provider crash")})
    with pytest.raises(STTCallError, match="simulated provider crash"):
        provider.transcribe("bad.wav")


def test_fake_provider_empty_transcript_is_unusable_audio():
    provider = FakeSTTProvider({"silence.wav": ""})
    with pytest.raises(UnusableAudioError):
        provider.transcribe("silence.wav")


def test_fake_provider_never_returns_none_or_guessed_transcript():
    """Every failure mode raises — never a silently-empty or fabricated
    STTResult for a path the provider wasn't told about."""
    provider = FakeSTTProvider({"known.wav": "ok"})
    for bad_path in ("unknown1.wav", "unknown2.wav", ""):
        with pytest.raises(STTCallError):
            provider.transcribe(bad_path)


# --- fixture manifest ---------------------------------------------------------

def test_manifest_loads_and_every_fixture_file_exists():
    cases = _load_manifest()
    assert len(cases) >= 10
    fixtures_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "lakewood", "stt", "fixtures")
    for c in cases:
        assert os.path.exists(os.path.join(fixtures_dir, f"{c['id']}.wav")), \
            f"missing audio fixture for {c['id']}"


def test_manifest_covers_required_domain_word_categories():
    cases = _load_manifest()
    categories_present = set()
    for c in cases:
        for cat, words in c["domain_words"].items():
            if words:
                categories_present.add(cat)
    assert {"sizes", "toppings", "negations", "quantities", "scope"} <= categories_present


# --- domain-weighted scoring, against the fake provider -----------------------

def test_perfect_transcripts_score_100_percent_every_category():
    # Score against each case's own EXPECTED transcript — i.e. a "perfect"
    # STT run — to prove the scoring method itself is correct, independent
    # of any real model's error rate.
    cases = _load_manifest()
    fixtures_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "lakewood", "stt", "fixtures")
    transcripts = {os.path.join(fixtures_dir, f"{c['id']}.wav"): c["expected_transcript"]
                  for c in cases}
    provider = FakeSTTProvider(transcripts)
    results = score(provider)
    assert not any(r.error for r in results)
    totals = summarize(results)
    for cat, (hit, total) in totals.items():
        if total:
            assert hit == total, f"{cat}: {hit}/{total} on a perfect transcript"


def test_missing_word_shows_up_as_a_category_miss():
    cases = _load_manifest()
    fixtures_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "lakewood", "stt", "fixtures")
    transcripts = {os.path.join(fixtures_dir, f"{c['id']}.wav"): c["expected_transcript"]
                  for c in cases}
    # Corrupt exactly one case: drop the negation word, simulating a real
    # STT mistake ("no onions" -> "onions").
    target = next(c for c in cases if "no" in c["domain_words"].get("negations", []))
    bad_path = os.path.join(fixtures_dir, f"{target['id']}.wav")
    transcripts[bad_path] = target["expected_transcript"].replace("no ", "")
    provider = FakeSTTProvider(transcripts)
    results = score(provider)
    target_result = next(r for r in results if r.case_id == target["id"])
    hit, total = target_result.category_hits["negations"]
    assert hit < total


def test_provider_error_on_one_fixture_does_not_crash_the_whole_run():
    cases = _load_manifest()
    fixtures_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "lakewood", "stt", "fixtures")
    transcripts = {os.path.join(fixtures_dir, f"{c['id']}.wav"): c["expected_transcript"]
                  for c in cases}
    broken_path = os.path.join(fixtures_dir, f"{cases[0]['id']}.wav")
    transcripts[broken_path] = STTCallError("simulated crash")
    provider = FakeSTTProvider(transcripts)
    results = score(provider)
    assert len(results) == len(cases)
    broken = next(r for r in results if r.case_id == cases[0]["id"])
    assert broken.error


# --- boundary discipline ------------------------------------------------------

def test_no_faster_whisper_types_leak_past_the_stt_package():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "lakewood"
    for name in ("menu.py", "pricing.py", "orders.py", "interpreter.py", "chat.py"):
        src = (root / name).read_text()
        assert "faster_whisper" not in src
        assert "whisper" not in src.lower()


def test_faster_whisper_provider_missing_dependency_fails_closed(monkeypatch):
    """Simulate faster-whisper not being installed — must raise
    STTConfigError, never crash with a bare ImportError or silently switch
    providers."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError("simulated: not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from lakewood.stt.faster_whisper_provider import FasterWhisperProvider
    with pytest.raises(STTConfigError, match="faster-whisper is not installed"):
        FasterWhisperProvider()


def test_make_stt_provider_selects_fake_by_default(monkeypatch):
    monkeypatch.delenv("LAKEWOOD_STT_PROVIDER", raising=False)
    from lakewood.stt.faster_whisper_provider import make_stt_provider
    assert isinstance(make_stt_provider(), FakeSTTProvider)


def test_make_stt_provider_rejects_unknown_name(monkeypatch):
    monkeypatch.setenv("LAKEWOOD_STT_PROVIDER", "not-a-real-provider")
    from lakewood.stt.faster_whisper_provider import make_stt_provider
    with pytest.raises(STTConfigError, match="not-a-real-provider"):
        make_stt_provider()
