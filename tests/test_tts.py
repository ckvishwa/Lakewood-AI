"""T-038 Phase 2 correction #1 — windows_sapi's synthesize()/play() must not
report success without proof (a real file on disk). Offline: stubs `_run`
so no real PowerShell/SAPI call happens; proves the guard, not SAPI itself."""
import os
import tempfile

import pytest

from lakewood.tts.base import TTSCallError
from lakewood.tts.windows_sapi import WindowsSapiTTSProvider


def test_synthesize_raises_if_no_file_appears(monkeypatch):
    """A provider whose subprocess exits 0 but writes no file (e.g. an
    unexpanded caller-side path variable) must not report success."""
    provider = WindowsSapiTTSProvider()
    monkeypatch.setattr(provider, "_run", lambda script: None)  # simulate "succeeded", wrote nothing
    missing_path = os.path.join(tempfile.gettempdir(), "lakewood-tts-does-not-exist.wav")
    if os.path.exists(missing_path):
        os.remove(missing_path)
    with pytest.raises(TTSCallError, match="no file exists"):
        provider.synthesize("large pepperoni", missing_path)


def test_synthesize_raises_if_file_is_empty(monkeypatch, tmp_path):
    provider = WindowsSapiTTSProvider()
    out = tmp_path / "empty.wav"
    def fake_run(script):
        out.touch()  # zero-byte file: subprocess "succeeded" but produced no audio
        return None
    monkeypatch.setattr(provider, "_run", fake_run)
    with pytest.raises(TTSCallError, match="empty"):
        provider.synthesize("large pepperoni", str(out))


def test_play_raises_on_missing_file():
    provider = WindowsSapiTTSProvider()
    with pytest.raises(TTSCallError, match="does not exist"):
        provider.play(os.path.join(tempfile.gettempdir(), "lakewood-tts-does-not-exist.wav"))


def test_play_raises_on_empty_file(tmp_path):
    provider = WindowsSapiTTSProvider()
    out = tmp_path / "empty.wav"
    out.touch()
    with pytest.raises(TTSCallError, match="empty"):
        provider.play(str(out))
