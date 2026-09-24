"""
Idempotent call start (T-053 Phase 2 Part 1).

Providers retry webhooks — a call-started event Twilio didn't get a fast
enough 2xx for WILL be redelivered with the identical `CallSid`. Two
independent layers make a retried call-started event a no-op rather than a
second session:

1. **This module** — an in-process, TTL'd "have I started handling this
   CallSid already" guard, checked BEFORE any work runs (session lookup,
   stream-token minting). Catches a retry that arrives while the first
   delivery is still being handled, cheaply, with no I/O.
2. **The persistence layer** (already built, T-037/ADR-014, re-verified
   here rather than re-invented) — `sessions` is keyed
   `PRIMARY KEY (store_id, call_id)` with `save_session` doing
   `ON CONFLICT ... DO UPDATE` (`lakewood/persistence/postgres_repository.py`).
   As long as the telephony adapter derives `call_id` from Twilio's own
   stable `CallSid` (never a freshly generated id per webhook delivery —
   the one wiring rule this module exists to make easy to get right), a
   retry that slips past layer 1 (e.g. after a process restart, when the
   in-process ledger is empty again) still upserts the SAME row instead of
   creating a duplicate. Layer 1 is a cheap fast-path; layer 2 is the real
   durable guarantee.
"""

from __future__ import annotations

import threading
import time


class CallStartGuard:
    def __init__(self, ttl_seconds: float = 300.0):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}   # call_sid -> expiry

    def _sweep(self, now: float) -> None:
        expired = [sid for sid, exp in self._seen.items() if exp < now]
        for sid in expired:
            del self._seen[sid]

    def begin(self, call_sid: str, now: float | None = None) -> bool:
        """Returns True the first time `call_sid` is seen (caller should
        run the normal call-start path); returns False for a duplicate
        delivery within the TTL window (caller must short-circuit —
        re-derive the SAME response, typically by looking the already-
        created session back up, never by minting a second stream token
        or re-running side effects)."""
        if not call_sid:
            raise ValueError("call_sid must not be empty")
        now = time.time() if now is None else now
        with self._lock:
            self._sweep(now)
            if call_sid in self._seen:
                return False
            self._seen[call_sid] = now + self._ttl
            return True
