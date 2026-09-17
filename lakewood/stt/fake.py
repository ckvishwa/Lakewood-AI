"""Deterministic, offline STT provider — no model load, no audio decoding.
Mirrors the fake-provider pattern used for LLM testing
(`tests/test_ollama_provider.py`'s scripted urlopen): every behavior test in
this repo can run against this without downloading a model or touching a
real audio file."""

from __future__ import annotations

import time

from .base import STTCallError, STTResult, UnusableAudioError


class FakeSTTProvider:
    """`transcripts` maps an audio_path (or any caller-chosen key) to either
    a canned transcript string or an Exception to raise. A path not in the
    mapping raises `STTCallError` — never a guessed transcript."""

    def __init__(self, transcripts: dict[str, str | Exception] | None = None,
                 default_transcript: str | Exception | None = None):
        self.transcripts = dict(transcripts or {})
        # Opt-in only: callers that don't control the audio_path (e.g. a
        # caller generating a temp filename per turn) can still get a fixed
        # transcript back without pre-seeding every possible path.
        self.default_transcript = default_transcript
        self.calls: list[str] = []

    def transcribe(self, audio_path: str, correlation_id: str = "") -> STTResult:
        self.calls.append(audio_path)
        if audio_path in self.transcripts:
            value = self.transcripts[audio_path]
        elif self.default_transcript is not None:
            value = self.default_transcript
        else:
            raise STTCallError(f"FakeSTTProvider has no scripted transcript for {audio_path!r}")
        if isinstance(value, Exception):
            raise value
        if value.strip() == "":
            raise UnusableAudioError(f"scripted empty transcript for {audio_path!r}")
        return STTResult(transcript=value, confidence=1.0, provider="fake",
                         correlation_id=correlation_id, latency_seconds=0.0)
