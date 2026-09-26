"""T-053 Phase 2 Part 4 — transferring a live call to a human."""

import pytest

from lakewood.telephony.call_control import (
    CallControlError, FakeCallControlClient, TwilioCallControlClient,
    build_transfer_twiml,
)

TRANSFER_NUMBER = "+12038060537"


def test_build_transfer_twiml_dials_the_number():
    xml = build_transfer_twiml(TRANSFER_NUMBER)
    assert "<Dial>+12038060537</Dial>" in xml
    assert "<Say>" not in xml   # announcement already played over the stream


def test_fake_client_records_transfers():
    fake = FakeCallControlClient()
    fake.transfer("CA_real_call", TRANSFER_NUMBER)
    assert fake.transfers == [("CA_real_call", TRANSFER_NUMBER)]


def test_fake_client_records_multiple_transfers_in_order():
    fake = FakeCallControlClient()
    fake.transfer("CA_1", TRANSFER_NUMBER)
    fake.transfer("CA_2", TRANSFER_NUMBER)
    assert fake.transfers == [("CA_1", TRANSFER_NUMBER), ("CA_2", TRANSFER_NUMBER)]


def test_real_client_wraps_provider_failure(monkeypatch):
    from twilio.rest import Client

    client = TwilioCallControlClient("AC_fake", "fake-auth-token")

    class _BoomCalls:
        def update(self, twiml):
            raise RuntimeError("network error talking to Twilio")

    monkeypatch.setattr(Client, "calls", lambda self, call_sid: _BoomCalls())
    with pytest.raises(CallControlError, match="failed to transfer"):
        client.transfer("CA_real_call", TRANSFER_NUMBER)
