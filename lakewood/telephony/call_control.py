"""
Transferring a LIVE call to a human (T-053 Phase 2 Part 4).

A call already mid-stream (Media Streams) can't be redirected by
returning new TwiML the way the initial webhook can — the call has to be
updated via Twilio's REST API instead (`calls(call_sid).update(twiml=...)`).
This is the one place that REST call happens; `FailureEscalation`
(`failure_policy.py`) decides WHEN, this module only knows HOW.

`config.py::StoreConfig`'s own loop-guard (`transfer_number != inbound_did`)
is enforced at config load, before this module ever runs — a call to
`.transfer()` trusts its `to_number` argument the same way `printer.py`
trusts an already-validated address; this module does not re-check it.
"""

from __future__ import annotations

from twilio.twiml.voice_response import Dial, VoiceResponse


class CallControlError(Exception):
    """A transfer attempt failed at the provider level (network, invalid
    call_sid, call already ended). The caller must not treat a failed
    transfer as a successful one — see `app.py`'s own handling."""


def build_transfer_twiml(to_number: str) -> str:
    """The TwiML a live call gets redirected to. No `<Say>` here — the
    transfer announcement (`failure_policy.TRANSFER_ANNOUNCEMENT`) already
    played over the existing media stream before this redirect happens;
    repeating it in TwiML would say it twice."""
    vr = VoiceResponse()
    vr.append(Dial(to_number))
    return str(vr)


class TwilioCallControlClient:
    """Real transfer, via Twilio's REST API. Lazy-imported (`twilio.rest
    .Client`) — same optional-dependency posture as `webhook_auth.py`."""

    def __init__(self, account_sid: str, auth_token: str):
        from twilio.rest import Client
        self._client = Client(account_sid, auth_token)

    def transfer(self, call_sid: str, to_number: str) -> None:
        try:
            self._client.calls(call_sid).update(twiml=build_transfer_twiml(to_number))
        except Exception as e:   # noqa: BLE001 — the twilio SDK's own exception types aren't public API
            raise CallControlError(f"failed to transfer call {call_sid!r}: {e}") from e


class FakeCallControlClient:
    """Records every transfer attempt instead of calling Twilio — this
    phase's own gate ("fake provider for tests")."""

    def __init__(self):
        self.transfers: list[tuple[str, str]] = []

    def transfer(self, call_sid: str, to_number: str) -> None:
        self.transfers.append((call_sid, to_number))
