"""Client adapter for the warm Parakeet service running inside WSL.

The Lakewood application remains a normal Windows Python process.  NeMo,
PyTorch and CUDA stay in the Linux environment where they are already
verified; only provider-neutral JSON crosses the localhost boundary.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

from .base import STTCallError, STTConfigError, STTResult, UnusableAudioError


DEFAULT_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MIN_USABLE_DURATION_SECONDS = 0.2


class ParakeetProvider:
    """Send one PCM WAV turn to an already-loaded Parakeet model.

    Configuration:
      LAKEWOOD_PARAKEET_URL       default http://127.0.0.1:8765
      LAKEWOOD_PARAKEET_TIMEOUT   default 30 seconds

    The service is deliberately not auto-started here.  Silent process
    spawning would hide an 11-second model load and make failure ownership
    unclear; operators start and health-check the WSL service explicitly.
    """

    def __init__(self, base_url: str | None = None, timeout_seconds: float | None = None):
        self.base_url = (base_url or os.environ.get("LAKEWOOD_PARAKEET_URL", DEFAULT_URL)).rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise STTConfigError("LAKEWOOD_PARAKEET_URL must be an http(s) URL")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise STTConfigError("LAKEWOOD_PARAKEET_URL must use a loopback host")
        if parsed.username or parsed.password:
            raise STTConfigError("LAKEWOOD_PARAKEET_URL must not contain credentials")
        raw_timeout = timeout_seconds
        if raw_timeout is None:
            raw_timeout = os.environ.get("LAKEWOOD_PARAKEET_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
        try:
            self.timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise STTConfigError("LAKEWOOD_PARAKEET_TIMEOUT must be a number") from exc
        if self.timeout_seconds <= 0:
            raise STTConfigError("LAKEWOOD_PARAKEET_TIMEOUT must be greater than zero")

    @staticmethod
    def _read_audio(audio_path: str) -> tuple[bytes, float]:
        if not os.path.isfile(audio_path):
            raise STTCallError(f"Audio file not found: {audio_path}")
        size = os.path.getsize(audio_path)
        if size == 0:
            raise UnusableAudioError(f"Audio file is empty: {audio_path}")
        if size > MAX_AUDIO_BYTES:
            raise STTCallError(
                f"Audio file exceeds {MAX_AUDIO_BYTES} byte local-service limit: {audio_path}")
        try:
            with wave.open(audio_path, "rb") as audio:
                frame_rate = audio.getframerate()
                duration = audio.getnframes() / frame_rate if frame_rate else 0.0
        except (EOFError, wave.Error) as exc:
            raise STTCallError(f"Audio is not a readable WAV file: {audio_path}") from exc
        if duration < MIN_USABLE_DURATION_SECONDS:
            raise UnusableAudioError(
                f"Audio unusable for transcription (duration={duration:.2f}s): {audio_path}")
        with open(audio_path, "rb") as audio:
            return audio.read(), duration

    def transcribe(self, audio_path: str, correlation_id: str = "") -> STTResult:
        audio, local_duration = self._read_audio(audio_path)
        if "\r" in correlation_id or "\n" in correlation_id:
            raise STTCallError("correlation_id may not contain a newline")
        request = urllib.request.Request(
            f"{self.base_url}/transcribe",
            data=audio,
            method="POST",
            headers={
                "Content-Type": "audio/wav",
                "Content-Length": str(len(audio)),
                "X-Correlation-ID": correlation_id,
            },
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310 -- base_url validated http(s)+loopback+no-credentials at __init__ (line 41-47), never customer-influenced
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            if exc.code == 422:
                raise UnusableAudioError(f"Parakeet rejected the audio: {detail}") from exc
            raise STTCallError(f"Parakeet service HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise STTCallError(
                f"Parakeet service unavailable at {self.base_url}: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise STTCallError("Parakeet service returned malformed JSON") from exc
        latency = time.monotonic() - started

        if not isinstance(payload, dict):
            raise STTCallError("Parakeet service response must be a JSON object")
        transcript = payload.get("transcript")
        if not isinstance(transcript, str):
            raise STTCallError("Parakeet service response is missing transcript text")
        transcript = transcript.strip()
        if not transcript:
            raise UnusableAudioError("Parakeet produced an empty transcript")
        duration = payload.get("audio_duration_seconds", local_duration)
        if not isinstance(duration, (int, float)) or duration <= 0:
            duration = local_duration
        model = payload.get("model", "nvidia/parakeet-unified-en-0.6b")
        if not isinstance(model, str):
            model = "nvidia/parakeet-unified-en-0.6b"
        return STTResult(
            transcript=transcript,
            confidence=None,  # NeMo RNNT does not expose calibrated 0-1 confidence here.
            provider=f"parakeet:{model}",
            correlation_id=correlation_id,
            audio_duration_seconds=float(duration),
            latency_seconds=latency,
        )


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            return payload["error"]
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    return exc.reason or "request failed"
