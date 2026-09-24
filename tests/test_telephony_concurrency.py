"""
T-053 Phase 2 Part 2 — the concurrency gate.

Proves the actual property that matters: a gate with `max_concurrent=N`
NEVER lets more than N wrapped calls run at the same wall-clock instant,
using real threads (not a mock), and that calls beyond the limit still
complete (they wait, they aren't dropped or errored).
"""

import threading
import time

import pytest

from lakewood.telephony.concurrency import ConcurrencyGate


def _run_concurrently(gate: ConcurrencyGate, n_callers: int, hold_seconds: float = 0.05):
    lock = threading.Lock()
    state = {"current": 0, "max_seen": 0, "completed": 0}

    def slow_call():
        with lock:
            state["current"] += 1
            state["max_seen"] = max(state["max_seen"], state["current"])
        time.sleep(hold_seconds)
        with lock:
            state["current"] -= 1
            state["completed"] += 1

    threads = [threading.Thread(target=lambda: gate.run(slow_call)) for _ in range(n_callers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    return state


def test_gate_of_one_fully_serializes():
    gate = ConcurrencyGate(max_concurrent=1)
    state = _run_concurrently(gate, n_callers=5, hold_seconds=0.05)
    assert state["max_seen"] == 1
    assert state["completed"] == 5


def test_gate_of_two_allows_two_but_not_three():
    gate = ConcurrencyGate(max_concurrent=2)
    state = _run_concurrently(gate, n_callers=6, hold_seconds=0.05)
    assert state["max_seen"] == 2
    assert state["completed"] == 6


def test_gate_run_returns_the_wrapped_function_result():
    gate = ConcurrencyGate(max_concurrent=1)
    assert gate.run(lambda x: x * 2, 21) == 42


def test_gate_propagates_exceptions_and_releases_the_slot():
    gate = ConcurrencyGate(max_concurrent=1)

    def boom():
        raise ValueError("real failure, not swallowed")

    with pytest.raises(ValueError, match="real failure"):
        gate.run(boom)
    # The slot must be released even after an exception — a second call
    # must not deadlock waiting on a permit that never comes back.
    assert gate.run(lambda: "ok") == "ok"


@pytest.mark.parametrize("bad_max", [0, -1])
def test_invalid_max_concurrent_rejected(bad_max):
    with pytest.raises(ValueError):
        ConcurrencyGate(max_concurrent=bad_max)
