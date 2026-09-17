"""
Local STT via faster-whisper (CTranslate2) — good CPU performance, the
reason it's picked over whisper.cpp for this task (no separate C++ build
step; pip-installable; runs acceptably on CPU-only dev machines). See
`docs/decisions/ADR-008-local-stt-runtime.md`.

`faster_whisper` types (`Segment`, `TranscriptionInfo`) never leave this
module — everything downstream sees only `lakewood.stt.base.STTResult`.
"""

from __future__ import annotations

import os
import time

from .base import STTCallError, STTConfigError, STTResult, Segment, UnusableAudioError

DEFAULT_MODEL_SIZE = "small"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"
MIN_USABLE_DURATION_SECONDS = 0.2


class FasterWhisperProvider:
    """
    Config (all explicit, nothing auto-detected):
      LAKEWOOD_STT_MODEL          default 'small' (tiny/base/small/medium/large-v3)
      LAKEWOOD_STT_DEVICE         default 'cpu'
      LAKEWOOD_STT_COMPUTE_TYPE   default 'int8' (int8/int8_float16/float16/float32)

    The model is downloaded (once, cached by faster_whisper/huggingface_hub)
    on first use of a given size — that first call can be slow; subsequent
    calls load from the local cache.
    """

    def __init__(self, model_size: str | None = None):
        self.model_size = model_size or os.environ.get("LAKEWOOD_STT_MODEL", DEFAULT_MODEL_SIZE)
        self.device = os.environ.get("LAKEWOOD_STT_DEVICE", DEFAULT_DEVICE)
        self.compute_type = os.environ.get("LAKEWOOD_STT_COMPUTE_TYPE", DEFAULT_COMPUTE_TYPE)
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise STTConfigError(
                "faster-whisper is not installed. Real local STT mode "
                "(LAKEWOOD_STT_PROVIDER=faster_whisper) requires it: "
                "`pip install faster-whisper`. This never falls back to a "
                "different provider automatically.") from e
        try:
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type)
        except Exception as e:
            raise STTConfigError(
                f"Could not load faster-whisper model {self.model_size!r} "
                f"(device={self.device!r}, compute_type={self.compute_type!r}): {e}") from e

    def transcribe(self, audio_path: str, correlation_id: str = "") -> STTResult:
        if not os.path.exists(audio_path):
            raise STTCallError(f"Audio file not found: {audio_path}")
        start = time.monotonic()
        try:
            segments_iter, info = self._model.transcribe(audio_path, beam_size=5)
            segments = list(segments_iter)
        except Exception as e:
            raise STTCallError(f"faster-whisper transcription failed: {e}") from e
        latency = time.monotonic() - start

        duration = getattr(info, "duration", 0.0) or 0.0
        if duration < MIN_USABLE_DURATION_SECONDS or not segments:
            raise UnusableAudioError(
                f"Audio unusable for transcription (duration={duration:.2f}s, "
                f"{len(segments)} segments): {audio_path}")

        text = " ".join(s.text.strip() for s in segments).strip()
        # avg_logprob is a per-segment log-probability, not a 0-1 confidence.
        # Convert to a rough 0-1 scale for STTResult's contract; this is an
        # approximation, documented as such — never presented as a calibrated
        # probability.
        avg_logprob = sum(s.avg_logprob for s in segments) / len(segments)
        confidence = max(0.0, min(1.0, 1.0 + avg_logprob))

        return STTResult(
            transcript=text,
            confidence=confidence,
            segments=[Segment(s.text.strip(), s.start, s.end) for s in segments],
            provider=f"faster_whisper:{self.model_size}",
            correlation_id=correlation_id,
            audio_duration_seconds=duration,
            latency_seconds=latency,
        )


def make_stt_provider():
    name = os.environ.get("LAKEWOOD_STT_PROVIDER", "fake")
    if name == "fake":
        from .fake import FakeSTTProvider
        return FakeSTTProvider()
    if name == "faster_whisper":
        return FasterWhisperProvider()
    if name == "parakeet":
        from .parakeet_provider import ParakeetProvider
        return ParakeetProvider()
    raise STTConfigError(
        f"Unknown LAKEWOOD_STT_PROVIDER={name!r}; choose 'fake', "
        "'faster_whisper', or 'parakeet'.")
