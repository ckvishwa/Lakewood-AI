"""T-053 Phase 2 Part 1 — signed/short-lived/single-use media stream token."""

import pytest

from lakewood.telephony.stream_token import StreamTokenError, StreamTokenIssuer

SECRET = "test-stream-token-secret-not-real"


def test_issued_token_consumes_to_the_same_call_sid():
    issuer = StreamTokenIssuer(SECRET, ttl_seconds=60.0)
    token = issuer.issue("CA_real_call_sid", now=1_000.0)
    assert issuer.consume(token, now=1_010.0) == "CA_real_call_sid"


def test_forged_token_is_refused():
    issuer = StreamTokenIssuer(SECRET, ttl_seconds=60.0)
    real = issuer.issue("CA_real_call_sid", now=1_000.0)
    payload_b64, _sig = real.rsplit(".", 1)
    forged = f"{payload_b64}.0000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(StreamTokenError, match="signature invalid"):
        issuer.consume(forged, now=1_001.0)


def test_token_signed_by_a_different_secret_is_refused():
    issuer = StreamTokenIssuer(SECRET, ttl_seconds=60.0)
    other_issuer = StreamTokenIssuer("a-completely-different-secret", ttl_seconds=60.0)
    token = other_issuer.issue("CA_real_call_sid", now=1_000.0)
    with pytest.raises(StreamTokenError, match="signature invalid"):
        issuer.consume(token, now=1_001.0)


def test_malformed_token_is_refused():
    issuer = StreamTokenIssuer(SECRET, ttl_seconds=60.0)
    with pytest.raises(StreamTokenError, match="malformed"):
        issuer.consume("not-a-real-token-at-all", now=1_000.0)
    with pytest.raises(StreamTokenError, match="malformed"):
        issuer.consume("", now=1_000.0)


def test_expired_token_is_refused():
    issuer = StreamTokenIssuer(SECRET, ttl_seconds=60.0)
    token = issuer.issue("CA_real_call_sid", now=1_000.0)
    with pytest.raises(StreamTokenError, match="expired"):
        issuer.consume(token, now=1_061.0)   # 61s later, past the 60s TTL


def test_token_cannot_be_replayed_single_use():
    """Single-use, not just short-lived — proves consuming a token twice
    fails on the SECOND attempt even well within the TTL window."""
    issuer = StreamTokenIssuer(SECRET, ttl_seconds=60.0)
    token = issuer.issue("CA_real_call_sid", now=1_000.0)
    assert issuer.consume(token, now=1_001.0) == "CA_real_call_sid"
    with pytest.raises(StreamTokenError, match="already used"):
        issuer.consume(token, now=1_002.0)


def test_two_tokens_for_the_same_call_are_independently_single_use():
    """A legitimate reconnect after a dropped WebSocket (Part 4) issues a
    second token for the same call_sid — consuming the first must not
    invalidate the second."""
    issuer = StreamTokenIssuer(SECRET, ttl_seconds=60.0)
    token_a = issuer.issue("CA_real_call_sid", now=1_000.0)
    token_b = issuer.issue("CA_real_call_sid", now=1_005.0)
    assert issuer.consume(token_a, now=1_006.0) == "CA_real_call_sid"
    assert issuer.consume(token_b, now=1_007.0) == "CA_real_call_sid"


def test_empty_secret_rejected_at_construction():
    with pytest.raises(ValueError):
        StreamTokenIssuer("", ttl_seconds=60.0)


def test_empty_call_sid_rejected_at_issue():
    issuer = StreamTokenIssuer(SECRET, ttl_seconds=60.0)
    with pytest.raises(ValueError):
        issuer.issue("", now=1_000.0)
