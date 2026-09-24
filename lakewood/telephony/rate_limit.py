"""
Rate limiting for the telephony webhook/WS endpoints (T-053 Phase 2 Part 1).

A fixed-window counter per key (source IP today; could be extended to
`from_number` later if abuse shows up there instead) — not a token bucket,
not a sliding log. At pilot volume (~30 calls/day, one store) the extra
smoothness a sliding window buys is not worth the extra state; a fixed
window's worst-case burst-at-the-boundary behavior is a well-understood,
acceptable tradeoff (operational simplicity, CLAUDE.md P5). Stdlib-only,
in-process, single-host — matches this system's whole deployment topology
(ADR-019); revisit only if a second host/instance is ever real.
"""

from __future__ import annotations

import threading


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max = max_requests
        self._window = window_seconds
        self._lock = threading.Lock()
        # key -> (window_start, count)
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, now: float) -> bool:
        """Returns True and records the request if `key` is still under
        its limit for the window containing `now`; returns False (and
        does NOT record) once the limit is hit. Callers must reject the
        request (HTTP 429) on False — this function only decides, it
        never raises."""
        with self._lock:
            window_start, count = self._windows.get(key, (now, 0))
            if now - window_start >= self._window:
                window_start, count = now, 0
            if count >= self._max:
                self._windows[key] = (window_start, count)
                return False
            self._windows[key] = (window_start, count + 1)
            return True
