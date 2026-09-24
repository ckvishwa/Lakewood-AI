"""
Twilio Media Streams WebSocket message protocol (T-053 Phase 2 Part 2).

Pure parsing/building — no network, no async, nothing WS-transport-
specific. This is the one place Twilio's own JSON message shapes are
allowed to appear (ADR-021's vendor-isolation constraint); the actual
transport (Part 3's real WS server) and `PhoneCallSession` (this Part's
audio engine) both work in terms of the typed events below, never raw
dicts, so a future second telephony vendor only needs a new module like
this one, not changes to the audio engine itself.

Reference: https://www.twilio.com/docs/voice/media-streams/websocket-messages
(confirmed directly against this doc while writing ADR-021: 8kHz
`audio/x-mulaw`, and each `media` message's `track` field distinguishes
`"inbound"` (caller) from `"outbound"` (this system's own played-back
audio) on the same bidirectional socket).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass


class TwilioProtocolError(Exception):
    """A message from Twilio didn't parse as any known/expected shape.
    Fail closed — never guess at a malformed or unknown event, this is
    an internet-facing socket."""


@dataclass(frozen=True)
class ConnectedEvent:
    protocol: str
    version: str


@dataclass(frozen=True)
class StartEvent:
    stream_sid: str
    call_sid: str
    account_sid: str
    media_encoding: str
    media_sample_rate_hz: int
    media_channels: int
    custom_parameters: dict[str, str]


@dataclass(frozen=True)
class MediaEvent:
    stream_sid: str
    track: str                 # "inbound" or "outbound"
    chunk: str
    timestamp_ms: str
    payload_mulaw: bytes       # already base64-decoded


@dataclass(frozen=True)
class StopEvent:
    stream_sid: str
    call_sid: str
    account_sid: str


@dataclass(frozen=True)
class MarkEvent:
    stream_sid: str
    name: str


InboundEvent = ConnectedEvent | StartEvent | MediaEvent | StopEvent | MarkEvent

_KNOWN_EVENTS = {"connected", "start", "media", "stop", "mark"}


def parse_inbound_message(message: dict) -> InboundEvent:
    """`message` is the already-JSON-decoded WS text frame. Raises
    `TwilioProtocolError` on anything that doesn't match a known,
    complete shape — a malformed or truncated message must never be
    silently skipped or partially trusted."""
    try:
        event = message["event"]
    except (KeyError, TypeError) as exc:
        raise TwilioProtocolError(f"message has no 'event' field: {message!r}") from exc

    if event not in _KNOWN_EVENTS:
        raise TwilioProtocolError(f"unknown Twilio Media Streams event: {event!r}")

    try:
        if event == "connected":
            return ConnectedEvent(protocol=message["protocol"], version=message["version"])
        if event == "start":
            start = message["start"]
            fmt = start["mediaFormat"]
            return StartEvent(
                stream_sid=start["streamSid"],
                call_sid=start["callSid"],
                account_sid=start["accountSid"],
                media_encoding=fmt["encoding"],
                media_sample_rate_hz=int(fmt["sampleRate"]),
                media_channels=int(fmt["channels"]),
                custom_parameters=dict(start.get("customParameters") or {}),
            )
        if event == "media":
            media = message["media"]
            payload = base64.b64decode(media["payload"])
            return MediaEvent(
                stream_sid=message["streamSid"],
                track=media.get("track", "inbound"),
                chunk=media["chunk"],
                timestamp_ms=media["timestamp"],
                payload_mulaw=payload,
            )
        if event == "stop":
            stop = message["stop"]
            return StopEvent(
                stream_sid=message["streamSid"],
                call_sid=stop["callSid"],
                account_sid=stop["accountSid"],
            )
        if event == "mark":
            return MarkEvent(stream_sid=message["streamSid"], name=message["mark"]["name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TwilioProtocolError(f"malformed {event!r} message: {message!r}") from exc

    raise AssertionError("unreachable — event was validated against _KNOWN_EVENTS above")


def build_outbound_media_message(stream_sid: str, mulaw_payload: bytes) -> dict:
    """The one outbound message type this phase needs — Rexi's own audio,
    played back to the caller. `clear`/DTMF/barge-in messages are out of
    scope (this phase's brief explicitly excludes full barge-in)."""
    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": base64.b64encode(mulaw_payload).decode("ascii")},
    }


def build_outbound_mark_message(stream_sid: str, name: str) -> dict:
    """Lets the caller ask Twilio to echo back a `mark` event once queued
    audio up to this point has actually finished playing — used by
    `PhoneCallSession` to know when its own TTS playback is really done,
    the phone-line equivalent of `WindowsSapiTTSProvider.play`'s blocking
    `PlaySync` (T-050's own non-overlapping capture/playback guarantee,
    reused here rather than re-invented)."""
    return {"event": "mark", "streamSid": stream_sid, "mark": {"name": name}}
