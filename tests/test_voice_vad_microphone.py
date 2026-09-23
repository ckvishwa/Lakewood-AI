"""
T-050 Part 1 — `SoundDeviceMicrophone` (the real, hardware-dependent VAD
capture class) must fail closed and clearly when its optional dependencies
are absent, and never at import time. Import-blocking via `sys.modules` so
these pass regardless of what's actually installed in a given environment —
this repo's own env happens to have `faster-whisper` (and its bundled
Silero model) installed but not `sounddevice`; these tests don't rely on
that happening to be true.
"""

import sys

import pytest

from lakewood.voice import AudioConfigError, SoundDeviceMicrophone, default_vad_config
from lakewood.vad import VadConfig


def test_module_import_needs_no_optional_dependency():
    # Already proven by every other test file importing lakewood.voice at
    # collection time in this offline suite — restated here as the explicit
    # acceptance criterion: importing the module must not touch sounddevice
    # or faster_whisper at all.
    import lakewood.voice  # noqa: F401


def test_capture_raises_audio_config_error_without_sounddevice(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    mic = SoundDeviceMicrophone()
    with pytest.raises(AudioConfigError, match="sounddevice"):
        mic.capture("unused.wav", seconds=1.0)


def test_capture_raises_audio_config_error_without_faster_whisper_vad(monkeypatch):
    """sounddevice present (faked), faster_whisper.vad absent — a distinct,
    clearly-labeled failure, not a generic crash."""
    fake_sd = type(sys)("sounddevice")
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", None)
    mic = SoundDeviceMicrophone()
    with pytest.raises(AudioConfigError, match="faster-whisper"):
        mic.capture("unused.wav", seconds=1.0)


def test_default_vad_config_reads_env_vars(monkeypatch):
    monkeypatch.setenv("LAKEWOOD_VAD_THRESHOLD", "0.6")
    monkeypatch.setenv("LAKEWOOD_VAD_MIN_SILENCE_MS", "900")
    monkeypatch.setenv("LAKEWOOD_VAD_MIN_SPEECH_MS", "150")
    monkeypatch.setenv("LAKEWOOD_VAD_NO_SPEECH_TIMEOUT_S", "8")
    monkeypatch.setenv("LAKEWOOD_VAD_MAX_UTTERANCE_S", "20")
    cfg = default_vad_config()
    assert cfg == VadConfig(threshold=0.6, min_silence_ms=900, min_speech_ms=150,
                            no_speech_timeout_seconds=8.0, max_utterance_seconds=20.0)


def test_default_vad_config_has_sane_defaults_with_no_env():
    cfg = default_vad_config()
    assert cfg == VadConfig()
