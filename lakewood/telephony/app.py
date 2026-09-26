"""
The real ASGI HTTP + WebSocket server (T-053 Phase 2 Part 3 — "wire what's
already built"). Requires `fastapi`+`uvicorn` (requirements.txt this
phase) — the one module in `lakewood/telephony/` that does; every other
module in this package stays framework-free.

Wires together, in order: Part 1's security (webhook signature, WS stream
token, rate limiting, idempotent call start), Part 2's audio engine
(`PhoneCallSession`, the concurrency gates), and what was already built
before this phase even started — `config.py::store_for_did`,
`persistence.service.resume_or_create` (via `PersistentChat.start`, the
SAME call text/local-voice already use), and `from_number` ->
`customer_id` (already automatic inside `save_session`, T-037). No new
session/order logic lives here — this module is I/O plumbing only.

Fully offline-testable: FastAPI's own `TestClient` drives both the HTTP
webhook and the WebSocket route in-process (`tests/test_telephony_app.py`)
— no real network, no real Twilio account, per this phase's own gate.

**Deliberately simple, disclosed, not silently glossed over:** outbound
TTS audio is sent as ONE `media` message per turn (not chunked into
Twilio's own 20ms inbound-frame size) — Twilio's docs support arbitrary
outbound payload sizes; chunking would only matter if a real call showed
buffering/backpressure problems, which Part 5's real-call testing, not
guesswork here, is what would actually prove.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from ..chat import PersistentChat, TextInterpreter
from ..config import CONFIG, store_for_did
from ..persistence.repository import SessionRepository
from ..stt.base import STTProvider
from ..tts.base import TextToSpeechProvider
from ..vad import VadConfig
from .call_control import CallControlError
from .call_session import PhoneCallSession, VadProbabilityFn
from .call_start import CallStartGuard
from .rate_limit import RateLimiter
from .stream_token import StreamTokenError, StreamTokenIssuer
from .twilio_protocol import (
    MediaEvent, StartEvent, StopEvent, TwilioProtocolError,
    build_outbound_media_message, parse_inbound_message,
)
from .webhook_auth import WebhookAuthError, validate_twilio_signature

logger = logging.getLogger("lakewood.telephony")

InterpreterFactory = Callable[[], TextInterpreter]
STTFactory = Callable[[], STTProvider]
TTSFactory = Callable[[], TextToSpeechProvider]
VadProbabilityFactory = Callable[[], VadProbabilityFn]


@dataclass
class TelephonyAppConfig:
    """Everything the app needs, injected — real providers in production
    (Part 5), fakes in every offline test. A fresh STT/TTS/VAD instance
    per call is constructed via the factories below, matching
    `concurrency.py`'s own documented per-call-instance decision for TTS
    (and, by the same reasoning, VAD)."""
    repo: SessionRepository
    auth_token: str
    stream_token_secret: str
    interpreter_factory: InterpreterFactory
    stt_factory: STTFactory
    tts_factory: TTSFactory
    vad_probability_factory: VadProbabilityFactory
    public_media_ws_url: str   # e.g. "wss://rexi.example.com/telephony/twilio/media"
    rate_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(30, 60.0))
    printer: object | None = None
    stream_token_ttl_seconds: float = 90.0
    # None -> PhoneCallSession's own production default
    # (lakewood.voice.default_vad_config()). Tests override with tighter
    # thresholds/faster polling so the offline suite doesn't have to wait
    # out real production-tuned silence windows.
    vad_config: VadConfig | None = None
    poll_seconds: float = 0.15
    # T-053 Phase 2 Part 4. None (the default) means "no real transfer
    # mechanism configured" — a transfer still plays the spoken
    # announcement and ends this WS's own involvement, but the actual
    # PSTN redirect never happens; logged loudly (never silently) so a
    # real deployment that forgot to wire a `TwilioCallControlClient`
    # finds out from its logs, not from a customer complaint.
    call_control: object | None = None

    def __post_init__(self) -> None:
        self.stream_tokens = StreamTokenIssuer(self.stream_token_secret, self.stream_token_ttl_seconds)
        self.call_start_guard = CallStartGuard()
        self._sessions: dict[str, PhoneCallSession] = {}
        self._sessions_lock = Lock()

    def register_session(self, call_sid: str, session: PhoneCallSession) -> None:
        with self._sessions_lock:
            self._sessions[call_sid] = session

    def pop_session(self, call_sid: str) -> PhoneCallSession | None:
        with self._sessions_lock:
            return self._sessions.pop(call_sid, None)

    def get_session(self, call_sid: str) -> PhoneCallSession | None:
        with self._sessions_lock:
            return self._sessions.get(call_sid)


def create_app(cfg: TelephonyAppConfig, max_workers: int = 16) -> FastAPI:
    app = FastAPI()
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="telephony")
    app.state.telephony_config = cfg
    app.state.telephony_executor = executor

    @app.post("/telephony/twilio/voice")
    async def twilio_voice(request: Request):
        source_ip = request.client.host if request.client else "unknown"
        if not cfg.rate_limiter.allow(source_ip, now=time.time()):
            return PlainTextResponse("rate limited", status_code=429)

        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        signature = request.headers.get("X-Twilio-Signature")
        try:
            validate_twilio_signature(cfg.auth_token, str(request.url), params, signature)
        except WebhookAuthError as e:
            logger.warning("rejected Twilio webhook from %s: %s", source_ip, e)
            return PlainTextResponse("forbidden", status_code=403)

        call_sid = params.get("CallSid")
        from_number = params.get("From")
        to_did = params.get("To")
        if not call_sid or not from_number or not to_did:
            return PlainTextResponse("bad request: missing CallSid/From/To", status_code=400)

        # Idempotent call start (T-053 Phase 2 Part 1) — observability/
        # replay-defense; harmless either way since resume_or_create's own
        # DB upsert is the real durable guarantee (see call_start.py).
        cfg.call_start_guard.begin(call_sid, now=time.time())

        store_for_did(to_did)   # F2: resolved server-side; result unused here,
                                 # single-store today — PersistentChat.start
                                 # re-derives it the same way internally.
        call = PersistentChat.start(cfg.repo, to_did, call_sid, from_number, printer=cfg.printer)
        if call.resume_offer is not None:
            if call.resume_offer.call_id == call_sid:
                # Same CallSid retried -> the same underlying call, not a
                # real customer callback (tests/test_telephony_call_start
                # .py's own reasoning) — accept transparently rather than
                # surface a "welcome back" offer for a call that never
                # actually ended.
                call.accept_resume()
            else:
                # A GENUINE callback (different CallSid, same phone
                # number, within the resume window) — never silently
                # resume a cart the customer hasn't confirmed they still
                # want (CLAUDE.md's memory policy: "may not assume the
                # customer still wants what they ordered before"). The
                # real UX this phase's own brief asks for ("a hang-up and
                # callback offers the cart back") — SPEAKING the offer and
                # listening for yes/no — needs disclosure to already have
                # played first, which itself requires `call.chat` to be
                # set (`PersistentChat.mark_disclosure_played`'s own
                # precondition); resolving that properly is real,
                # nontrivial voice-flow work, not "wiring what's already
                # built" — filed as T-060, not attempted here. Declining
                # is the SAFE default in the meantime: the customer starts
                # a fresh order rather than the assistant guessing they
                # want the old one back.
                call.decline_resume()

        session = PhoneCallSession(
            call, cfg.interpreter_factory(), cfg.stt_factory(), cfg.tts_factory(),
            cfg.vad_probability_factory(), vad_config=cfg.vad_config, poll_seconds=cfg.poll_seconds)
        cfg.register_session(call_sid, session)

        token = cfg.stream_tokens.issue(call_sid)
        stream_url = f"{cfg.public_media_ws_url}?token={token}"

        vr = VoiceResponse()
        connect = Connect()
        stream = Stream(url=stream_url)
        connect.append(stream)
        vr.append(connect)
        return Response(content=str(vr), media_type="application/xml")

    @app.websocket("/telephony/twilio/media")
    async def twilio_media(websocket: WebSocket):
        token = websocket.query_params.get("token")
        try:
            call_sid = cfg.stream_tokens.consume(token or "", now=time.time())
        except StreamTokenError as e:
            logger.warning("rejected media WebSocket connect: %s", e)
            await websocket.close(code=4401)
            return

        session = cfg.get_session(call_sid)
        if session is None:
            logger.warning("media WebSocket for unknown call_sid %s", call_sid)
            await websocket.close(code=4404)
            return

        await websocket.accept()
        loop = asyncio.get_running_loop()
        stream_sid: str | None = None
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                    event = parse_inbound_message(message)
                except (json.JSONDecodeError, TwilioProtocolError) as e:
                    logger.warning("dropped malformed telephony WS message: %s", e)
                    continue

                if isinstance(event, StartEvent):
                    stream_sid = event.stream_sid
                    disclosure_audio = await loop.run_in_executor(executor, session.on_call_started)
                    if disclosure_audio:
                        await websocket.send_json(
                            build_outbound_media_message(stream_sid, disclosure_audio))
                elif isinstance(event, MediaEvent):
                    if event.track != "inbound":
                        continue   # never process Rexi's own outbound audio (Part 2)
                    outcome = await loop.run_in_executor(
                        executor, session.on_inbound_frame, event.payload_mulaw, time.monotonic())
                    if outcome is not None and stream_sid and outcome.outbound_mulaw:
                        await websocket.send_json(
                            build_outbound_media_message(stream_sid, outcome.outbound_mulaw))
                    if outcome is not None and outcome.should_transfer:
                        # T-053 Phase 2 Part 4: the announcement already
                        # went out above; the actual PSTN redirect is a
                        # REST call, not something this WS can do to
                        # itself — CONFIG.transfer_number, never a caller-
                        # supplied number (same server-bound posture as
                        # store_id/F2).
                        if cfg.call_control is not None:
                            try:
                                await loop.run_in_executor(
                                    executor, cfg.call_control.transfer, call_sid, CONFIG.transfer_number)
                            except CallControlError as e:
                                logger.error("transfer failed for call_sid %s: %s", call_sid, e)
                        else:
                            logger.warning(
                                "escalated call_sid %s to transfer, but no call_control "
                                "client is configured — no real PSTN redirect happened", call_sid)
                        break
                elif isinstance(event, StopEvent):
                    break
                # ConnectedEvent/MarkEvent: no action needed this phase.
        except WebSocketDisconnect:
            logger.info("telephony WebSocket disconnected for call_sid %s", call_sid)
        finally:
            cfg.pop_session(call_sid)

    return app
