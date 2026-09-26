"""
T-053 Phase 2 Part 4 — repeated-failure escalation through the REAL ASGI
routes, ending in an actual (fake) transfer call.

Same zero-threshold `VadConfig` lesson Part 3's own hang-diagnosis
established (see that STATUS.md entry): `no_speech_timeout_seconds=0.0`
here specifically, not just the min_speech/min_silence thresholds —
"no speech at all" is checked against REAL elapsed wall-clock time since
turn start, not a transition, so a nonzero value would reintroduce the
exact same real-wall-clock dependency that hung Part 3's own test.
"""

import base64

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from lakewood.interpreter import RuleBasedInterpreter
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.stt.fake import FakeSTTProvider
from lakewood.telephony.app import TelephonyAppConfig, create_app
from lakewood.telephony.audio_codec import pcm16_to_mulaw
from lakewood.telephony.call_control import FakeCallControlClient
from lakewood.telephony.failure_policy import TRANSFER_ANNOUNCEMENT
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
    def __init__(self):
        self.spoken: list[str] = []

    def synthesize(self, text, output_path):
        self.spoken.append(text)
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
    pcm = array.array("h", [0] * n_samples).tobytes()
    return pcm16_to_mulaw(pcm)


def test_two_silent_turns_escalate_and_transfer_via_the_real_routes():
    repo = InMemorySessionRepository()
    call_control = FakeCallControlClient()
    cfg = TelephonyAppConfig(
        repo=repo,
        auth_token=AUTH_TOKEN,
        stream_token_secret=STREAM_SECRET,
        interpreter_factory=RuleBasedInterpreter,
        stt_factory=lambda: FakeSTTProvider(default_transcript="large pepperoni"),
        tts_factory=_FakeWavTTS,
        vad_probability_factory=lambda: (lambda pcm16k: 0.0),   # never speech, ever
        public_media_ws_url="wss://rexi.example.com/telephony/twilio/media",
        vad_config=_ZERO_VAD,
        poll_seconds=0.0,
        call_control=call_control,
    )
    client = TestClient(create_app(cfg))

    params = {"CallSid": "CA_escalates", "From": FROM_NUMBER, "To": DID, "CallStatus": "ringing"}
    resp = client.post("/telephony/twilio/voice", data=params,
                        headers={"X-Twilio-Signature": _sign(BASE_URL, params)})
    assert resp.status_code == 200
    import re
    token = re.search(r'url="[^"]*\?token=([^"]+)"', resp.text).group(1)

    with client.websocket_connect(f"/telephony/twilio/media?token={token}") as ws:
        ws.send_json({
            "event": "start", "streamSid": "MZ",
            "start": {"streamSid": "MZ", "accountSid": "AC", "callSid": "CA_escalates",
                      "tracks": ["inbound"],
                      "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}},
        })
        ws.receive_json()   # disclosure

        # Turn 1: silence -> apology, no transfer yet.
        ws.send_json({
            "event": "media", "streamSid": "MZ",
            "media": {"track": "inbound", "chunk": "1", "timestamp": "20",
                      "payload": base64.b64encode(_mulaw_frame()).decode("ascii")},
        })
        first_reply = ws.receive_json()
        assert first_reply["event"] == "media"
        assert base64.b64decode(first_reply["media"]["payload"])

        # Turn 2: silence again -> escalates to transfer.
        ws.send_json({
            "event": "media", "streamSid": "MZ",
            "media": {"track": "inbound", "chunk": "2", "timestamp": "40",
                      "payload": base64.b64encode(_mulaw_frame()).decode("ascii")},
        })
        second_reply = ws.receive_json()
        assert second_reply["event"] == "media"
        assert base64.b64decode(second_reply["media"]["payload"])

    assert call_control.transfers == [("CA_escalates", "+12038060537")]
