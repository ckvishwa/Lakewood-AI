"""
T-053 Phase 2 Part 1 — Twilio webhook signature validation.

Uses the REAL `twilio.request_validator.RequestValidator` on both sides
(these tests build a genuine signature the same way Twilio's own SDK
would, then verify it through `validate_twilio_signature`) — proving the
wrapper actually calls the real vendor primitive, not a stand-in.
"""

import pytest

from lakewood.telephony.webhook_auth import WebhookAuthError, validate_twilio_signature

AUTH_TOKEN = "test-auth-token-not-a-real-secret"
URL = "https://rexi.example.com/telephony/twilio/voice"
PARAMS = {"CallSid": "CA1234567890abcdef1234567890abcdef", "From": "+12035551234",
          "To": "+12037588880", "CallStatus": "ringing"}


def _real_signature(url: str, params: dict, auth_token: str = AUTH_TOKEN) -> str:
    from twilio.request_validator import RequestValidator
    return RequestValidator(auth_token).compute_signature(url, params)


def test_valid_signature_is_accepted():
    sig = _real_signature(URL, PARAMS)
    validate_twilio_signature(AUTH_TOKEN, URL, PARAMS, sig)   # must not raise


def test_missing_signature_is_rejected():
    with pytest.raises(WebhookAuthError, match="missing"):
        validate_twilio_signature(AUTH_TOKEN, URL, PARAMS, None)


def test_empty_signature_is_rejected():
    with pytest.raises(WebhookAuthError, match="missing"):
        validate_twilio_signature(AUTH_TOKEN, URL, PARAMS, "")


def test_forged_signature_is_rejected():
    with pytest.raises(WebhookAuthError, match="did not match"):
        validate_twilio_signature(AUTH_TOKEN, URL, PARAMS, "not-a-real-signature==")


def test_signature_for_a_different_url_is_rejected():
    """A signature computed against one URL must not validate against
    another — proves the check is actually binding the signature to the
    request, not just checking a valid-shaped token exists."""
    sig = _real_signature(URL, PARAMS)
    other_url = "https://rexi.example.com/telephony/twilio/status-callback"
    with pytest.raises(WebhookAuthError, match="did not match"):
        validate_twilio_signature(AUTH_TOKEN, other_url, PARAMS, sig)


def test_signature_with_tampered_params_is_rejected():
    """The classic replay-with-a-tweak attack: a genuinely-signed request
    whose CallSid gets swapped for a different call after the fact."""
    sig = _real_signature(URL, PARAMS)
    tampered = dict(PARAMS, CallSid="CA_forged_call_sid_0000000000000000")
    with pytest.raises(WebhookAuthError, match="did not match"):
        validate_twilio_signature(AUTH_TOKEN, URL, tampered, sig)


def test_signature_under_wrong_auth_token_is_rejected():
    """Simulates a signature genuinely computed by SOME Twilio account,
    verified against the wrong account's auth token — must fail closed."""
    sig = _real_signature(URL, PARAMS, auth_token="a-different-accounts-token")
    with pytest.raises(WebhookAuthError, match="did not match"):
        validate_twilio_signature(AUTH_TOKEN, URL, PARAMS, sig)
