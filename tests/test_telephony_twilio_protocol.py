"""T-053 Phase 2 Part 2 — Twilio Media Streams WS message parsing."""

import base64

import pytest

from lakewood.telephony.twilio_protocol import (
    ConnectedEvent, MarkEvent, MediaEvent, StartEvent, StopEvent,
    TwilioProtocolError, build_outbound_mark_message, build_outbound_media_message,
    parse_inbound_message,
)


def test_parses_connected_event():
    msg = {"event": "connected", "protocol": "Call", "version": "1.0.0"}
    event = parse_inbound_message(msg)
    assert event == ConnectedEvent(protocol="Call", version="1.0.0")


def test_parses_start_event():
    msg = {
        "event": "start",
        "sequenceNumber": "1",
        "streamSid": "MZ_stream",
        "start": {
            "streamSid": "MZ_stream",
            "accountSid": "AC_account",
            "callSid": "CA_call",
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            "customParameters": {"foo": "bar"},
        },
    }
    event = parse_inbound_message(msg)
    assert event == StartEvent(
        stream_sid="MZ_stream", call_sid="CA_call", account_sid="AC_account",
        media_encoding="audio/x-mulaw", media_sample_rate_hz=8000, media_channels=1,
        custom_parameters={"foo": "bar"})


def test_parses_start_event_with_no_custom_parameters():
    msg = {
        "event": "start",
        "streamSid": "MZ_stream",
        "start": {
            "streamSid": "MZ_stream", "accountSid": "AC_account", "callSid": "CA_call",
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
    }
    event = parse_inbound_message(msg)
    assert event.custom_parameters == {}


def test_parses_media_event_and_decodes_base64_payload():
    raw_audio = b"\xff\x7e\x00\x01" * 4
    msg = {
        "event": "media",
        "streamSid": "MZ_stream",
        "media": {
            "track": "inbound", "chunk": "1", "timestamp": "20",
            "payload": base64.b64encode(raw_audio).decode("ascii"),
        },
    }
    event = parse_inbound_message(msg)
    assert isinstance(event, MediaEvent)
    assert event.track == "inbound"
    assert event.payload_mulaw == raw_audio


def test_media_event_default_track_is_inbound_if_absent():
    msg = {"event": "media", "streamSid": "MZ",
           "media": {"chunk": "1", "timestamp": "0", "payload": base64.b64encode(b"x").decode()}}
    event = parse_inbound_message(msg)
    assert event.track == "inbound"


def test_parses_stop_event():
    msg = {"event": "stop", "streamSid": "MZ_stream",
           "stop": {"accountSid": "AC_account", "callSid": "CA_call"}}
    event = parse_inbound_message(msg)
    assert event == StopEvent(stream_sid="MZ_stream", call_sid="CA_call", account_sid="AC_account")


def test_parses_mark_event():
    msg = {"event": "mark", "streamSid": "MZ_stream", "mark": {"name": "reply-done"}}
    event = parse_inbound_message(msg)
    assert event == MarkEvent(stream_sid="MZ_stream", name="reply-done")


def test_unknown_event_is_rejected():
    with pytest.raises(TwilioProtocolError, match="unknown"):
        parse_inbound_message({"event": "dtmf", "streamSid": "MZ"})


def test_missing_event_field_is_rejected():
    with pytest.raises(TwilioProtocolError, match="no 'event'"):
        parse_inbound_message({"streamSid": "MZ"})


def test_message_that_is_not_a_dict_is_rejected():
    with pytest.raises(TwilioProtocolError):
        parse_inbound_message("not a dict")   # type: ignore[arg-type]


@pytest.mark.parametrize("event,broken", [
    ("start", {"event": "start", "streamSid": "MZ", "start": {"callSid": "CA"}}),   # missing accountSid/mediaFormat
    ("media", {"event": "media", "streamSid": "MZ", "media": {"chunk": "1"}}),       # missing payload/timestamp
    ("stop", {"event": "stop", "streamSid": "MZ", "stop": {}}),                       # missing callSid/accountSid
    ("mark", {"event": "mark", "streamSid": "MZ", "mark": {}}),                       # missing name
    ("connected", {"event": "connected"}),                                            # missing protocol/version
])
def test_malformed_known_event_is_rejected_not_guessed(event, broken):
    with pytest.raises(TwilioProtocolError, match=f"malformed {event!r}"):
        parse_inbound_message(broken)


def test_build_outbound_media_message_round_trips_the_payload():
    payload = b"\x00\x01\x02\x03"
    msg = build_outbound_media_message("MZ_stream", payload)
    assert msg["event"] == "media"
    assert msg["streamSid"] == "MZ_stream"
    assert base64.b64decode(msg["media"]["payload"]) == payload


def test_build_outbound_mark_message():
    msg = build_outbound_mark_message("MZ_stream", "reply-1")
    assert msg == {"event": "mark", "streamSid": "MZ_stream", "mark": {"name": "reply-1"}}
