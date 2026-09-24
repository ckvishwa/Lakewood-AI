"""
T-053 Phase 2 Part 3 — the real ASGI app, end to end, fully offline.

FastAPI's own `TestClient` drives both the HTTP webhook and the WebSocket
media route in-process — genuinely exercises `app.py`'s real routing,
Part 1's security checks, and Part 2's `PhoneCallSession`, with no real
network and no real Twilio account (this phase's own gate).
"""

import array
import base64
import re
import wave

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from lakewood.interpreter import RuleBasedInterpreter
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.stt.base import STTCallError
from lakewood.stt.fake import FakeSTTProvider
from lakewood.telephony.app import TelephonyAppConfig, create_app
from lakewood.telephony.audio_codec import pcm16_to_mulaw
from lakewood.telephony.stream_token import StreamTokenIssuer
from lakewood.tts.base import TTSResult
from lakewood.vad import VadConfig

AUTH_TOKEN = "test-auth-token-not-real"
STREAM_SECRET = "test-stream-secret-not-real"
DID = "+12037588880"
FROM_NUMBER = "+12035551234"
BASE_URL = "http://testserver/telephony/twilio/voice"

# Zero thresholds, not just small ones: `on_inbound_frame` polls on REAL
# wall-clock time (`time.monotonic()` inside `app.py`'s WS handler, not
# injectable here the way the pure PhoneCallSession tests control `now`
# directly) — a nonzero-but-small threshold made this test genuinely flaky/
# slow depending on how fast the cross-thread WS round trip happens to run
# on a given machine (confirmed directly: it hung for over an hour under
# real wall-clock timing that never happened to cross a 5ms threshold
# fast enough). Zero removes the dependency on real elapsed time entirely
# — `(elapsed - start) * 1000 >= 0` is trivially true the instant a
# transition is observed, so the turn completes in exactly 3 polls
# regardless of machine speed.
_FAST_VAD = VadConfig(threshold=0.5, min_silence_ms=0, min_speech_ms=0,
                       no_speech_timeout_seconds=0.3, max_utterance_seconds=2.0)


class _FakeWavTTS:
    def __init__(self, sample_rate: int = 22050):
        self.sample_rate = sample_rate
        self.spoken: list[str] = []

    def synthesize(self, text: str, output_path: str) -> TTSResult:
        self.spoken.append(text)
        n = int(self.sample_rate * 0.15)
        pcm = array.array("h", [800 if i % 2 == 0 else -800 for i in range(n)])
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())
        return TTSResult(output_path, "fake-wav", 0.0)

    def play(self, audio_path: str) -> None:
        return None


def _scripted_vad(probs):
    state = {"i": 0}

    def fn(pcm16k_bytes: bytes) -> float:
        value = probs[min(state["i"], len(probs) - 1)]
        state["i"] += 1
        return value
    return fn


def _sign(url: str, params: dict, auth_token: str = AUTH_TOKEN) -> str:
    return RequestValidator(auth_token).compute_signature(url, params)


def _make_config(**overrides) -> TelephonyAppConfig:
    defaults = dict(
        repo=InMemorySessionRepository(),
        auth_token=AUTH_TOKEN,
        stream_token_secret=STREAM_SECRET,
        interpreter_factory=RuleBasedInterpreter,
        stt_factory=lambda: FakeSTTProvider(default_transcript="large pepperoni"),
        tts_factory=_FakeWavTTS,
        vad_probability_factory=lambda: _scripted_vad([0.9, 0.9, 0.0, 0.0]),
        public_media_ws_url="wss://rexi.example.com/telephony/twilio/media",
        vad_config=_FAST_VAD,
        poll_seconds=0.0,   # poll on every frame — see _FAST_VAD's own comment
    )
    defaults.update(overrides)
    return TelephonyAppConfig(**defaults)


def _call_params(call_sid="CA_e2e_test", from_number=FROM_NUMBER):
    return {"CallSid": call_sid, "From": from_number, "To": DID, "CallStatus": "ringing"}


def _mulaw_frame(n_samples: int = 160) -> bytes:
    pcm = array.array("h", [0] * n_samples).tobytes()
    return pcm16_to_mulaw(pcm)


def _extract_token(twiml_xml: str) -> str:
    match = re.search(r'url="[^"]*\?token=([^"]+)"', twiml_xml)
    assert match, f"no stream token found in TwiML: {twiml_xml!r}"
    return match.group(1)


# ---------------------------------------------------------------------------
# The webhook — security first
# ---------------------------------------------------------------------------

def test_valid_webhook_returns_twiml_with_a_stream_url():
    cfg = _make_config()
    client = TestClient(create_app(cfg))
    params = _call_params()
    resp = client.post("/telephony/twilio/voice", data=params,
                        headers={"X-Twilio-Signature": _sign(BASE_URL, params)})
    assert resp.status_code == 200
    assert "<Stream" in resp.text
    assert "rexi.example.com/telephony/twilio/media" in resp.text


def test_missing_signature_is_rejected():
    cfg = _make_config()
    client = TestClient(create_app(cfg))
    resp = client.post("/telephony/twilio/voice", data=_call_params())
    assert resp.status_code == 403


def test_forged_signature_is_rejected():
    cfg = _make_config()
    client = TestClient(create_app(cfg))
    resp = client.post("/telephony/twilio/voice", data=_call_params(),
                        headers={"X-Twilio-Signature": "forged=="})
    assert resp.status_code == 403


def test_rate_limit_blocks_after_the_configured_max():
    from lakewood.telephony.rate_limit import RateLimiter
    cfg = _make_config(rate_limiter=RateLimiter(max_requests=2, window_seconds=60.0))
    client = TestClient(create_app(cfg))
    params = _call_params()
    headers = {"X-Twilio-Signature": _sign(BASE_URL, params)}
    assert client.post("/telephony/twilio/voice", data=params, headers=headers).status_code == 200
    assert client.post("/telephony/twilio/voice", data=params, headers=headers).status_code == 200
    assert client.post("/telephony/twilio/voice", data=params, headers=headers).status_code == 429


# ---------------------------------------------------------------------------
# The media WebSocket — auth, then a full real turn
# ---------------------------------------------------------------------------

def test_media_websocket_without_a_valid_token_is_refused():
    cfg = _make_config()
    client = TestClient(create_app(cfg))
    with pytest.raises(Exception):
        with client.websocket_connect("/telephony/twilio/media?token=not-a-real-token"):
            pass


def test_media_websocket_for_an_unregistered_call_sid_is_refused():
    cfg = _make_config()
    client = TestClient(create_app(cfg))
    # A syntactically valid token for a call the app never registered
    # (e.g. the webhook was never actually called first).
    token = cfg.stream_tokens.issue("CA_never_registered")
    with pytest.raises(Exception):
        with client.websocket_connect(f"/telephony/twilio/media?token={token}"):
            pass


def test_full_call_disclosure_then_turn_then_reply():
    repo = InMemorySessionRepository()
    stt = FakeSTTProvider(default_transcript="large pepperoni")
    cfg = _make_config(repo=repo, stt_factory=lambda: stt,
                        vad_probability_factory=lambda: _scripted_vad([0.9, 0.9, 0.0, 0.0]))
    client = TestClient(create_app(cfg))

    params = _call_params(call_sid="CA_full_flow")
    resp = client.post("/telephony/twilio/voice", data=params,
                        headers={"X-Twilio-Signature": _sign(BASE_URL, params)})
    assert resp.status_code == 200
    token = _extract_token(resp.text)

    with client.websocket_connect(f"/telephony/twilio/media?token={token}") as ws:
        ws.send_json({
            "event": "start", "streamSid": "MZ_stream",
            "start": {
                "streamSid": "MZ_stream", "accountSid": "AC_x", "callSid": "CA_full_flow",
                "tracks": ["inbound"],
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            },
        })
        disclosure_msg = ws.receive_json()
        assert disclosure_msg["event"] == "media"
        assert len(base64.b64decode(disclosure_msg["media"]["payload"])) > 0

        # Drive frames until a reply turns up (bounded, deterministic thresholds).
        for i in range(30):
            ws.send_json({
                "event": "media", "streamSid": "MZ_stream",
                "media": {"track": "inbound", "chunk": str(i), "timestamp": str(i * 20),
                          "payload": base64.b64encode(_mulaw_frame()).decode("ascii")},
            })

        reply_msg = ws.receive_json()
        assert reply_msg["event"] == "media"
        assert len(base64.b64decode(reply_msg["media"]["payload"])) > 0

        ws.send_json({"event": "stop", "streamSid": "MZ_stream",
                      "stop": {"accountSid": "AC_x", "callSid": "CA_full_flow"}})

    saved = repo.load_session("STORE-001", "CA_full_flow")
    assert saved is not None
    assert len(saved.lines) == 1   # the real domain actually added the pizza


def test_outbound_track_frames_are_ignored():
    """Part 2's own requirement: never process Rexi's own played-back
    audio if the provider echoes it on the same socket."""
    repo = InMemorySessionRepository()
    stt = FakeSTTProvider(default_transcript="large pepperoni")
    cfg = _make_config(repo=repo, stt_factory=lambda: stt)
    client = TestClient(create_app(cfg))

    params = _call_params(call_sid="CA_outbound_test")
    resp = client.post("/telephony/twilio/voice", data=params,
                        headers={"X-Twilio-Signature": _sign(BASE_URL, params)})
    token = _extract_token(resp.text)

    with client.websocket_connect(f"/telephony/twilio/media?token={token}") as ws:
        ws.send_json({
            "event": "start", "streamSid": "MZ2",
            "start": {"streamSid": "MZ2", "accountSid": "AC", "callSid": "CA_outbound_test",
                      "tracks": ["inbound", "outbound"],
                      "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}},
        })
        ws.receive_json()   # disclosure

        for i in range(15):
            ws.send_json({
                "event": "media", "streamSid": "MZ2",
                "media": {"track": "outbound", "chunk": str(i), "timestamp": str(i * 20),
                          "payload": base64.b64encode(_mulaw_frame()).decode("ascii")},
            })
        ws.send_json({"event": "stop", "streamSid": "MZ2",
                      "stop": {"accountSid": "AC", "callSid": "CA_outbound_test"}})

    saved = repo.load_session("STORE-001", "CA_outbound_test")
    # Disclosure alone persists a row (T-057: proof it played must survive
    # a crash) — the real proof here is that outbound-track frames never
    # reached the turn engine, so no cart line was ever added.
    assert saved is not None
    assert len(saved.lines) == 0
