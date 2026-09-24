"""T-053 Phase 2 Part 1 — fixed-window rate limiting."""

import pytest

from lakewood.telephony.rate_limit import RateLimiter


def test_allows_up_to_the_limit_then_blocks():
    limiter = RateLimiter(max_requests=3, window_seconds=60.0)
    assert limiter.allow("1.2.3.4", now=1_000.0) is True
    assert limiter.allow("1.2.3.4", now=1_001.0) is True
    assert limiter.allow("1.2.3.4", now=1_002.0) is True
    assert limiter.allow("1.2.3.4", now=1_003.0) is False


def test_different_keys_have_independent_windows():
    limiter = RateLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.allow("1.2.3.4", now=1_000.0) is True
    assert limiter.allow("5.6.7.8", now=1_000.0) is True
    assert limiter.allow("1.2.3.4", now=1_001.0) is False


def test_new_window_resets_the_count():
    limiter = RateLimiter(max_requests=2, window_seconds=10.0)
    assert limiter.allow("1.2.3.4", now=1_000.0) is True
    assert limiter.allow("1.2.3.4", now=1_001.0) is True
    assert limiter.allow("1.2.3.4", now=1_005.0) is False   # still in-window
    assert limiter.allow("1.2.3.4", now=1_011.0) is True    # new window


def test_blocked_requests_are_not_recorded_beyond_the_limit():
    limiter = RateLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.allow("1.2.3.4", now=1_000.0) is True
    for i in range(5):
        assert limiter.allow("1.2.3.4", now=1_001.0 + i) is False


@pytest.mark.parametrize("max_requests,window_seconds", [(0, 60.0), (-1, 60.0)])
def test_invalid_max_requests_rejected(max_requests, window_seconds):
    with pytest.raises(ValueError):
        RateLimiter(max_requests=max_requests, window_seconds=window_seconds)


@pytest.mark.parametrize("window_seconds", [0.0, -1.0])
def test_invalid_window_rejected(window_seconds):
    with pytest.raises(ValueError):
        RateLimiter(max_requests=5, window_seconds=window_seconds)
