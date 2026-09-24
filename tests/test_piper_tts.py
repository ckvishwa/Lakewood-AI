"""T-053 Phase 1 Part C — PiperTTSProvider. Offline-safe: piper-tts is
lazy-imported (ADR-008's own pattern), and no test here requires a
downloaded voice model — the offline suite stays green with neither
piper-tts nor a model file installed, same guarantee ADR-014 gives
PostgresSessionRepository.
"""
import builtins
import os

import pytest

from lakewood.tts.base import TTSCallError, TTSConfigError


def test_missing_piper_package_raises_a_clear_config_error(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "piper" or name.startswith("piper."):
            raise ImportError("simulated: piper-tts not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from lakewood.tts.piper_provider import PiperTTSProvider
    with pytest.raises(TTSConfigError, match="piper-tts"):
        PiperTTSProvider(model_path="does-not-matter.onnx")


def test_missing_model_file_raises_a_clear_config_error():
    try:
        import piper  # noqa: F401
    except ImportError:
        pytest.skip("piper-tts not installed in this environment")
    from lakewood.tts.piper_provider import PiperTTSProvider
    with pytest.raises(TTSConfigError, match="not found"):
        PiperTTSProvider(model_path="/definitely/does/not/exist.onnx")


def test_factory_selects_piper_provider(monkeypatch):
    """lakewood.tts.make_tts_provider() routes LAKEWOOD_TTS_PROVIDER=piper
    to the real class — without requiring a model file to prove the
    routing itself (construction failing past that point is covered by
    the two tests above)."""
    monkeypatch.setenv("LAKEWOOD_TTS_PROVIDER", "piper")
    monkeypatch.setenv("LAKEWOOD_PIPER_MODEL", "/definitely/does/not/exist.onnx")
    from lakewood.tts import make_tts_provider
    try:
        make_tts_provider()
    except TTSConfigError as e:
        assert "not found" in str(e) or "piper-tts" in str(e)
    else:
        pytest.fail("expected TTSConfigError for a nonexistent model path")


_REAL_MODEL_PATH = os.environ.get(
    "LAKEWOOD_PIPER_MODEL_TEST", "piper-voices/en_US-lessac-medium.onnx")
_HAS_REAL_MODEL = os.path.isfile(_REAL_MODEL_PATH)


@pytest.mark.skipif(not _HAS_REAL_MODEL,
                    reason="no downloaded Piper voice model in this environment")
def test_synthesize_produces_a_real_nonempty_wav_file(tmp_path):
    from lakewood.tts.piper_provider import PiperTTSProvider
    provider = PiperTTSProvider(model_path=_REAL_MODEL_PATH)
    out = tmp_path / "readback.wav"
    result = provider.synthesize("Got it, one large pepperoni pizza.", str(out))
    assert os.path.isfile(out) and os.path.getsize(out) > 0
    assert result.provider == "piper"
    assert result.latency_seconds > 0


@pytest.mark.skipif(not _HAS_REAL_MODEL,
                    reason="no downloaded Piper voice model in this environment")
def test_play_raises_on_missing_file():
    from lakewood.tts.piper_provider import PiperTTSProvider
    provider = PiperTTSProvider(model_path=_REAL_MODEL_PATH)
    with pytest.raises(TTSCallError, match="does not exist"):
        provider.play("/definitely/does/not/exist.wav")


@pytest.mark.skipif(not _HAS_REAL_MODEL,
                    reason="no downloaded Piper voice model in this environment")
def test_play_raises_on_empty_file(tmp_path):
    from lakewood.tts.piper_provider import PiperTTSProvider
    provider = PiperTTSProvider(model_path=_REAL_MODEL_PATH)
    out = tmp_path / "empty.wav"
    out.touch()
    with pytest.raises(TTSCallError, match="empty"):
        provider.play(str(out))
