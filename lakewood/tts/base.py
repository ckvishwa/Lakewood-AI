"""Provider-neutral local text-to-speech boundary."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

@dataclass
class TTSResult:
    audio_path: str
    provider: str
    latency_seconds: float

class TTSConfigError(Exception): pass
class TTSCallError(Exception): pass

class TextToSpeechProvider(Protocol):
    def synthesize(self, text: str, output_path: str) -> TTSResult: ...
    def play(self, audio_path: str) -> None: ...
