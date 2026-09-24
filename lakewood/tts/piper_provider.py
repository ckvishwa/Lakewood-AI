"""Piper adapter — the Linux-deployable TTS provider (T-053 Phase 1 Part C).

`piper-tts` (ONNX runtime, CPU-only) is lazy-imported inside `__init__`,
same pattern `faster_whisper_provider.py`/`postgres_repository.py` already
use for their own optional dependencies — the offline suite needs nothing
installed to import this module, only to construct this class.

Chosen over Kokoro on measured evidence (docs/STATUS.md's T-053 Phase 1
Part C entry has the full comparison): ~8x faster synthesis (0.19s vs.
1.5s warm, for a real readback-length sentence), zero GPU usage for
either (both are pure ONNX/CPU, so GPU contention with Parakeet is not
actually a differentiator between them), and a real synthesize-transcode-
Parakeet round trip at 8kHz mu-law found Piper cleanly correct on every
item/modifier word across 4 realistic readback carts (25/25) where Kokoro
had one real degradation ("twelve piece wings" -> "12 peacewings", 23/25).
"""
from __future__ import annotations
import os, subprocess, time, wave
from .base import TTSCallError, TTSConfigError, TTSResult

DEFAULT_MODEL_PATH = "piper-voices/en_US-lessac-medium.onnx"


class PiperTTSProvider:
    def __init__(self, model_path: str | None = None):
        # Read at construction time, not import time — an env var set
        # after this module was first imported (e.g. by a test, or by a
        # caller configuring the process after startup) must still apply.
        self.model_path = (
            model_path
            or os.environ.get("LAKEWOOD_PIPER_MODEL")
            or DEFAULT_MODEL_PATH)
        try:
            from piper import PiperVoice
        except ImportError as e:
            raise TTSConfigError(
                "PiperTTSProvider requires piper-tts (pip install piper-tts) "
                "plus a downloaded voice model — not part of the stdlib-only "
                "lakewood/ runtime policy, same as faster-whisper (ADR-008)."
            ) from e
        if not os.path.isfile(self.model_path):
            raise TTSConfigError(
                f"Piper voice model not found at {self.model_path!r} — "
                f"download one with `python -m piper.download_voices "
                f"en_US-lessac-medium` (or set LAKEWOOD_PIPER_MODEL).")
        self._voice = PiperVoice.load(self.model_path)

    def synthesize(self, text: str, output_path: str) -> TTSResult:
        start = time.monotonic()
        try:
            with wave.open(output_path, "wb") as wf:
                self._voice.synthesize_wav(text, wf)
        except Exception as e:  # noqa: BLE001 — piper's own exception types aren't public API
            raise TTSCallError(f"Piper synthesis failed: {e}") from e
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise TTSCallError(f"Piper reported success but {output_path!r} is empty or missing")
        return TTSResult(output_path, "piper", time.monotonic() - start)

    def play(self, audio_path: str) -> None:
        if not os.path.isfile(audio_path):
            raise TTSCallError(f"cannot play {audio_path!r}: file does not exist")
        if os.path.getsize(audio_path) == 0:
            raise TTSCallError(f"cannot play {audio_path!r}: file is empty")
        try:
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
                check=True)
        except FileNotFoundError as e:
            raise TTSConfigError("Piper playback requires ffplay (ffmpeg) on PATH.") from e
        except subprocess.CalledProcessError as e:
            raise TTSCallError(f"ffplay failed: {e}") from e
