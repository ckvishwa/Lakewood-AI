"""
Provider-agnostic speech-to-text interface (T-013 Track B).

    audio -> lakewood/stt/ (this boundary) -> transcript text
          -> existing TextInterpreter -> tools -> cart -> pricing

Nothing downstream of `STTResult.transcript` changes because of which STT
provider produced it — the exact same boundary discipline as
`lakewood/llm_provider.py`. No STT library type (faster-whisper's `Segment`,
whisper.cpp bindings, anything vendor-shaped) may appear outside this
package; a provider module translates its own output into `STTResult` and
nothing else leaks out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Segment:
    """One time-aligned chunk of the transcript, if the provider exposes
    timing — optional; not every provider (e.g. the fake) has real timing."""
    text: str
    start_seconds: float
    end_seconds: float


@dataclass
class STTResult:
    transcript: str
    confidence: float | None          # 0.0-1.0, or None if unavailable
    segments: list[Segment] = field(default_factory=list)
    provider: str = ""                # provider name, for logging/eval breakdown
    correlation_id: str = ""          # caller-supplied; never generated here
    audio_duration_seconds: float = 0.0
    latency_seconds: float = 0.0


class STTConfigError(Exception):
    """Missing/invalid provider configuration (model not found, no audio
    backend, bad config value). Fail closed — never silently substitute a
    different provider or a guessed transcript."""


class STTCallError(Exception):
    """The transcription attempt itself failed: provider crash, timeout,
    corrupt/unreadable audio. Infrastructure/input failure, distinct from a
    low-confidence-but-real transcript."""


class UnusableAudioError(STTCallError):
    """Audio was readable but not remotely usable for transcription — e.g.
    silence, zero duration, or a duration below a sane minimum. Distinct
    from a general call failure so callers can react differently (e.g. ask
    the customer to repeat rather than treat it as a provider outage)."""


class STTProvider(Protocol):
    def transcribe(self, audio_path: str, correlation_id: str = "") -> STTResult: ...
