"""Shared test fixtures.

T-058: `dispatch_confirmed_order` calls `orders.store_status()` with no
`now` argument, so it reads REAL wall-clock time. Any test that exercises
a normal (non-held) dispatch and doesn't pin this explicitly is not
hermetic — it silently depends on whatever the real time happens to be
when the suite runs, which is why ten tests passed locally (dev machine's
local time happened to fall in-hours) and failed in CI (the runner's UTC
clock landed on Wednesday 22:xx, which `HOURS`' exclusive upper bound
(11, 22) reads as already closed). See docs/STATUS.md's T-058 entry for
the full diagnosis — this was never a competing-state-machine bug.
"""
import pytest


@pytest.fixture
def open_store(monkeypatch):
    """Force the store to read as open, deterministically, regardless of
    real wall-clock time or which machine/timezone runs the test."""
    monkeypatch.setattr(
        "lakewood.orders.store_status",
        lambda *a, **kw: {"open": True, "closes_at": "10 PM"})


@pytest.fixture
def closed_store(monkeypatch):
    """The mirror of open_store — force the store to read as closed,
    deterministically, for tests that exercise the after-hours/HELD_FOR_OPEN
    path on purpose."""
    monkeypatch.setattr(
        "lakewood.orders.store_status",
        lambda *a, **kw: {"open": False, "next_open": "10 AM",
                          "next_open_iso": "2026-09-23T10:00:00"})
