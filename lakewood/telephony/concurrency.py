"""
The concurrency model for the phone path (T-053 Phase 2 Part 2).

**Isolation vs. shared bottlenecks — deliberately not the same question.**
Each call gets its own `PhoneCallSession` (own audio buffer, own
`PersistentChat`/cart, own VAD `Endpointer`) — carts never mix, by
construction (Part 2's own acceptance criterion). But TWO calls sharing a
process can still share a REAL underlying resource that isn't itself
safe for concurrent use, and pretending otherwise would just make a
different call silently slow instead of visibly serialized. Three
resources, three different real answers, found by reading the actual
provider code rather than assumed:

1. **STT (Parakeet).** `scripts/parakeet_server.py` uses stdlib
   `http.server.HTTPServer` — NOT `ThreadingHTTPServer` — confirmed
   directly by reading it for this task. That server handles ONE request
   at a time; a second concurrent transcription request queues at the
   socket level until the first finishes. This is a REAL, already-existing
   constraint of infrastructure this phase does not touch (that script
   lives outside `lakewood/telephony/`, runs in a separate WSL/NeMo
   environment) — not a hypothetical caution. `STT_GATE` below makes this
   explicit and safe: at most one `stt.transcribe()` call in flight across
   ALL concurrent phone calls, ever. Two callers waiting on it get
   serialized STT latency, not corrupted audio or a crash — the honest,
   disclosed tradeoff Part 5's "two calls at once" scenario must measure,
   not hide.
2. **TTS (Piper).** `PiperTTSProvider.synthesize()` calls into an
   in-process ONNX Runtime session (`self._voice.synthesize_wav`) — no
   verified thread-safety story for concurrent `Run()` calls on ONE shared
   session (ONNX Runtime documents this as generally safe, but Piper's own
   Python wrapper, and any phonemizer it uses underneath, is unverified
   here). The safe default this phase takes: **each call constructs its
   own `PiperTTSProvider`** (its own loaded voice/session — model load is
   ~0.7s one-time per ADR-020's own measurement, cheap at pilot volume),
   so there is no SHARED session for a thread-safety question to even
   apply to. `TTS_GATE` below is a resource cap only (bounding how many
   CPU-bound synthesis threads can run at once under a burst), not a
   correctness requirement.
3. **Domain/LLM (`PersistentChat.run_turn`).** No gate. `orders.py`/the
   Postgres repository were already proven safe under REAL concurrent
   access (T-053 Phase 1 Part B's two concurrency-bug fixes, re-verified
   by `postgres-contract` CI). An LLM call is an ordinary concurrent-safe
   HTTP client call. This is the literal mechanism behind Part 2's "one
   call's LLM latency never blocks another's audio" requirement — by
   never gating this path at all, not by a clever scheduler.

All three run through a bounded worker pool (`asyncio.to_thread`/executor,
wired in Part 3's real WS server), never inline on whatever async event
loop is multiplexing the WebSocket connections — a blocking STT/TTS/HTTP
call inline on that loop would stall every OTHER call's audio I/O, the
exact failure Part 2's brief warns against.
"""

from __future__ import annotations

import threading
from typing import Callable, TypeVar

T = TypeVar("T")

# See docstring point 1 — a real, confirmed constraint of the existing
# Parakeet warm service, not a guess.
STT_MAX_CONCURRENT = 1

# See docstring point 2 — a resource cap, not a correctness requirement
# (each call already gets its own PiperTTSProvider instance). Sized well
# above any realistic concurrent-call count at pilot volume (~30 calls/day
# total, not concurrent) so it only ever matters as a burst safety net.
TTS_MAX_CONCURRENT = 8


class ConcurrencyGate:
    """Wraps a blocking call so at most `max_concurrent` invocations run
    at once across every caller sharing this gate; excess callers block
    until a slot frees up. A thin `threading.BoundedSemaphore` wrapper —
    deliberately not a custom scheduler (CLAUDE.md: standard tools over
    custom ones where they win)."""

    def __init__(self, max_concurrent: int):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self.max_concurrent = max_concurrent
        self._sem = threading.BoundedSemaphore(max_concurrent)

    def run(self, fn: Callable[..., T], *args, **kwargs) -> T:
        with self._sem:
            return fn(*args, **kwargs)


# Process-wide — one gate per resource, shared by every concurrent
# PhoneCallSession in this process (ADR-019's single-host topology; revisit
# only if a second host ever becomes real).
STT_GATE = ConcurrencyGate(STT_MAX_CONCURRENT)
TTS_GATE = ConcurrencyGate(TTS_MAX_CONCURRENT)
