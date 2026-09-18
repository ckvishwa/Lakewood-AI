"""Offline contract tests for the Windows -> warm WSL Parakeet boundary."""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
import wave
from http.server import HTTPServer

import pytest

from lakewood.stt.base import STTCallError, STTConfigError, UnusableAudioError
from lakewood.stt.parakeet_provider import ParakeetProvider
from scripts.parakeet_server import make_handler, transcript_text, wav_duration


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


def _wav(tmp_path, seconds=1.0):
    path = tmp_path / "turn.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def test_parakeet_posts_wav_and_returns_provider_neutral_result(monkeypatch, tmp_path):
    path = _wav(tmp_path, 1.25)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse({
            "transcript": "Two large pepperoni pizzas with no onions.",
            "model": "nvidia/parakeet-unified-en-0.6b",
            "audio_duration_seconds": 1.25,
            "inference_latency_seconds": 0.08,
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = ParakeetProvider(timeout_seconds=7).transcribe(str(path), "CALL-1")

    assert result.transcript == "Two large pepperoni pizzas with no onions."
    assert result.provider == "parakeet:nvidia/parakeet-unified-en-0.6b"
    assert result.confidence is None
    assert result.correlation_id == "CALL-1"
    assert result.audio_duration_seconds == 1.25
    assert captured["timeout"] == 7
    assert captured["request"].full_url == "http://127.0.0.1:8765/transcribe"
    assert captured["request"].headers["Content-type"] == "audio/wav"
    assert captured["request"].data == path.read_bytes()


@pytest.mark.parametrize("raw", ["nope", "0", "-1"])
def test_parakeet_rejects_invalid_timeout(monkeypatch, raw):
    monkeypatch.setenv("LAKEWOOD_PARAKEET_TIMEOUT", raw)
    with pytest.raises(STTConfigError, match="TIMEOUT"):
        ParakeetProvider()


@pytest.mark.parametrize("url", ["not-a-url", "http://10.0.0.5:8765", "http://user:pass@localhost:8765"])
def test_parakeet_rejects_nonlocal_or_credentialed_url(url):
    with pytest.raises(STTConfigError, match="URL"):
        ParakeetProvider(base_url=url)


def test_parakeet_rejects_missing_empty_short_and_corrupt_audio(tmp_path):
    provider = ParakeetProvider()
    with pytest.raises(STTCallError, match="not found"):
        provider.transcribe(str(tmp_path / "missing.wav"))
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    with pytest.raises(UnusableAudioError, match="empty"):
        provider.transcribe(str(empty))
    with pytest.raises(UnusableAudioError, match="duration"):
        provider.transcribe(str(_wav(tmp_path, 0.1)))
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"not wav")
    with pytest.raises(STTCallError, match="readable WAV"):
        provider.transcribe(str(corrupt))


def test_parakeet_service_unavailable_fails_closed(monkeypatch, tmp_path):
    path = _wav(tmp_path)

    def fail(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(STTCallError, match="service unavailable"):
        ParakeetProvider().transcribe(str(path))


def test_parakeet_422_is_unusable_audio(monkeypatch, tmp_path):
    path = _wav(tmp_path)

    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 422, "Unprocessable Entity", {},
            io.BytesIO(b'{"error":"model produced an empty transcript"}'))

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(UnusableAudioError, match="empty transcript"):
        ParakeetProvider().transcribe(str(path))


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b'{"model":"x"}', b'{"transcript":""}'])
def test_parakeet_malformed_or_empty_response_fails_closed(monkeypatch, tmp_path, payload):
    path = _wav(tmp_path)

    class RawResponse(FakeResponse):
        def __init__(self):
            self.body = payload

    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: RawResponse())
    error = UnusableAudioError if payload == b'{"transcript":""}' else STTCallError
    with pytest.raises(error):
        ParakeetProvider().transcribe(str(path))


def test_server_helpers_accept_hypothesis_and_plain_text(tmp_path):
    class Hypothesis:
        text = "  large pepperoni  "

    assert transcript_text([Hypothesis()]) == "large pepperoni"
    assert transcript_text(["  no onions "]) == "no onions"
    assert transcript_text([]) == ""
    assert wav_duration(str(_wav(tmp_path, 1.5))) == pytest.approx(1.5)


def test_real_local_http_boundary_round_trip(tmp_path):
    class FakeEngine:
        model_name = "test-parakeet"
        device = "cpu"

        def transcribe(self, path):
            assert wav_duration(path) == pytest.approx(1.0)
            return "large pepperoni", 0.01

    server = HTTPServer(("127.0.0.1", 0), make_handler(FakeEngine()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urllib.request.urlopen(f"http://{host}:{port}/healthz") as response:
            assert json.loads(response.read())["status"] == "ready"
        result = ParakeetProvider(
            base_url=f"http://{host}:{port}", timeout_seconds=2
        ).transcribe(str(_wav(tmp_path)), "ROUND-TRIP")
        assert result.transcript == "large pepperoni"
        assert result.provider == "parakeet:test-parakeet"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
