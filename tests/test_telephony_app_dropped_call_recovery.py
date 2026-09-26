"""
T-053 Phase 2 Part 4 — "Provider WebSocket drops mid-call -> session
persisted; a callback recovers it."

**What's proven here, exactly, and what isn't (disclosed, not glossed
over):** a dropped WebSocket leaves the in-flight session PERSISTED
(already true, `save_progress` runs on every real turn) and a real
callback (a new call-started webhook, same phone number, within the
resume window) is correctly DETECTED as resumable by
`resume_or_create`/`find_resumable_session` (ADR-014, unchanged this
task). What is NOT yet built: actually SPEAKING the offer and listening
for yes/no — `app.py`'s webhook handler currently declines a genuine
callback's resume offer automatically (the safe default: never silently
resume a cart the customer hasn't confirmed they still want — CLAUDE.md's
memory policy) rather than ask. Filed as T-060
(`docs/NEXT_TASKS.md`) — this test proves the SAFE, currently-real
behavior, not the full desired UX.
"""

import base64
import re

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from lakewood.interpreter import RuleBasedInterpreter
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.stt.fake import FakeSTTProvider
from lakewood.telephony.app import TelephonyAppConfig, create_app
from lakewood.telephony.audio_codec import pcm16_to_mulaw
from lakewood.tts.base import TTSResult
from lakewood.vad import VadConfig
import array
import wave

AUTH_TOKEN = "test-auth-token-not-real"
STREAM_SECRET = "test-stream-secret-not-real"
DID = "+12037588880"
FROM_NUMBER = "+12035551234"
BASE_URL = "http://testserver/telephony/twilio/voice"

_ZERO_VAD = VadConfig(threshold=0.5, min_silence_ms=0, min_speech_ms=0,
                       no_speech_timeout_seconds=0.0, max_utterance_seconds=2.0)


class _FakeWavTTS:
    def synthesize(self, text, output_path):
        n = int(22050 * 0.1)
        pcm = array.array("h", [500] * n)
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(pcm.tobytes())
        return TTSResult(output_path, "fake-wav", 0.0)

    def play(self, audio_path):
        return None


def _sign(url: str, params: dict) -> str:
    return RequestValidator(AUTH_TOKEN).compute_signature(url, params)


def _mulaw_frame(n_samples: int = 160) -> bytes:
    return pcm16_to_mulaw(array.array("h", [0] * n_samples).tobytes())


def _scripted_vad_factory():
    """Speech, then silence -- a real turn, not endless speech (which
    would never trigger the silence-based endpoint) and not endless
    silence (which needs `no_speech_timeout_seconds`, not this path)."""
    probs = [0.9, 0.9, 0.0, 0.0]
    state = {"i": 0}

    def fn(pcm16k_bytes: bytes) -> float:
        value = probs[min(state["i"], len(probs) - 1)]
        state["i"] += 1
        return value
    return fn


def _make_config(repo, stt_transcript="large pepperoni"):
    return TelephonyAppConfig(
        repo=repo,
        auth_token=AUTH_TOKEN,
        stream_token_secret=STREAM_SECRET,
        interpreter_factory=RuleBasedInterpreter,
        stt_factory=lambda: FakeSTTProvider(default_transcript=stt_transcript),
        tts_factory=_FakeWavTTS,
        vad_probability_factory=_scripted_vad_factory,
        public_media_ws_url="wss://rexi.example.com/telephony/twilio/media",
        vad_config=_ZERO_VAD,
        poll_seconds=0.0,
    )


def _start_call_and_get_token(client, call_sid, from_number=FROM_NUMBER):
    params = {"CallSid": call_sid, "From": from_number, "To": DID, "CallStatus": "ringing"}
    resp = client.post("/telephony/twilio/voice", data=params,
                        headers={"X-Twilio-Signature": _sign(BASE_URL, params)})
    assert resp.status_code == 200
    return re.search(r'url="[^"]*\?token=([^"]+)"', resp.text).group(1)


def test_dropped_call_leaves_the_session_persisted():
    repo = InMemorySessionRepository()
    cfg = _make_config(repo)
    client = TestClient(create_app(cfg))
    token = _start_call_and_get_token(client, "CA_first_call")

    with client.websocket_connect(f"/telephony/twilio/media?token={token}") as ws:
        ws.send_json({
            "event": "start", "streamSid": "MZ1",
            "start": {"streamSid": "MZ1", "accountSid": "AC", "callSid": "CA_first_call",
                      "tracks": ["inbound"],
                      "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}},
        })
        ws.receive_json()   # disclosure
        for i in range(4):
            ws.send_json({
                "event": "media", "streamSid": "MZ1",
                "media": {"track": "inbound", "chunk": str(i), "timestamp": str(i * 20),
                          "payload": base64.b64encode(_mulaw_frame()).decode("ascii")},
            })
        ws.receive_json()   # the real turn reply -- a pizza was ordered
        # Dropped, not a clean `stop` -- the `with` block's own close on
        # exit is the closest offline stand-in for a real network drop.

    saved = repo.load_session("STORE-001", "CA_first_call")
    assert saved is not None
    assert len(saved.lines) == 1


def test_a_real_callback_is_detected_as_resumable_and_safely_declined():
    """The mechanism (detection) works; the UX (offering it back) is
    T-060, not this task. Proves the CURRENT, safe, disclosed behavior."""
    repo = InMemorySessionRepository()
    cfg = _make_config(repo)
    client = TestClient(create_app(cfg))

    token1 = _start_call_and_get_token(client, "CA_dropped_call")
    with client.websocket_connect(f"/telephony/twilio/media?token={token1}") as ws:
        ws.send_json({
            "event": "start", "streamSid": "MZ1",
            "start": {"streamSid": "MZ1", "accountSid": "AC", "callSid": "CA_dropped_call",
                      "tracks": ["inbound"],
                      "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}},
        })
        ws.receive_json()
        for i in range(4):
            ws.send_json({
                "event": "media", "streamSid": "MZ1",
                "media": {"track": "inbound", "chunk": str(i), "timestamp": str(i * 20),
                          "payload": base64.b64encode(_mulaw_frame()).decode("ascii")},
            })
        ws.receive_json()

    assert len(repo.load_session("STORE-001", "CA_dropped_call").lines) == 1

    # A genuine callback: a NEW CallSid, the SAME phone number.
    token2 = _start_call_and_get_token(client, "CA_callback_call")
    with client.websocket_connect(f"/telephony/twilio/media?token={token2}") as ws:
        ws.send_json({
            "event": "start", "streamSid": "MZ2",
            "start": {"streamSid": "MZ2", "accountSid": "AC", "callSid": "CA_callback_call",
                      "tracks": ["inbound"],
                      "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}},
        })
        ws.receive_json()
        for i in range(4):
            ws.send_json({
                "event": "media", "streamSid": "MZ2",
                "media": {"track": "inbound", "chunk": str(i), "timestamp": str(i * 20),
                          "payload": base64.b64encode(_mulaw_frame()).decode("ascii")},
            })
        ws.receive_json()

    # Safe default: the old cart was declined (deleted), not silently
    # resumed -- a fresh session under the new call_sid has its OWN
    # (also real, since the fake STT/interpreter still run for real) cart.
    assert repo.load_session("STORE-001", "CA_dropped_call") is None
    new_session = repo.load_session("STORE-001", "CA_callback_call")
    assert new_session is not None
    assert len(new_session.lines) == 1   # a fresh order, not the old one carried over
