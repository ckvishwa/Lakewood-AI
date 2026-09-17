from __future__ import annotations
import time
from .base import TTSResult

class FakeTTSProvider:
    def __init__(self): self.spoken = []
    def synthesize(self, text, output_path):
        self.spoken.append(text)
        return TTSResult(output_path, "fake", 0.0)
    def play(self, audio_path): return None
