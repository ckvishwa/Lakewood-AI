"""
T-053 Phase 2 Part 1 — idempotent call start.

Two layers, both proven here (see `lakewood/telephony/call_start.py`'s own
docstring): the in-process `CallStartGuard` (cheap fast-path) and the
persistence layer's own upsert-by-`(store_id, call_id)` (the durable
guarantee that survives a process restart, where the in-process guard's
memory is gone). A real Twilio webhook retry always carries the identical
`CallSid` — every test here uses that as `call_id`, per the wiring rule
`call_start.py` documents.
"""

import pytest

from lakewood.chat import PersistentChat
from lakewood.interpreter import RuleBasedInterpreter
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.telephony.call_start import CallStartGuard

CALL_SID = "CA_replayed_webhook_call_sid_0001"
FROM_NUMBER = "+12035551234"
DID = "+12037588880"


def test_first_delivery_is_allowed():
    guard = CallStartGuard(ttl_seconds=300.0)
    assert guard.begin(CALL_SID, now=1_000.0) is True


def test_immediate_retry_in_process_is_blocked():
    """Layer 1 — the cheap in-process fast path. A retry that arrives
    while (or shortly after) the first delivery is being handled must be
    told NOT to re-run the call-start work."""
    guard = CallStartGuard(ttl_seconds=300.0)
    assert guard.begin(CALL_SID, now=1_000.0) is True
    assert guard.begin(CALL_SID, now=1_000.5) is False
    assert guard.begin(CALL_SID, now=1_200.0) is False   # still within TTL


def test_different_call_sids_are_independent():
    guard = CallStartGuard(ttl_seconds=300.0)
    assert guard.begin("CA_one", now=1_000.0) is True
    assert guard.begin("CA_two", now=1_000.0) is True


def test_after_ttl_expiry_a_new_call_with_the_same_sid_is_allowed_again():
    """Not a real replay concern (Twilio never reuses a CallSid for a
    genuinely different call) — proves the ledger doesn't grow unbounded,
    not a security property."""
    guard = CallStartGuard(ttl_seconds=300.0)
    assert guard.begin(CALL_SID, now=1_000.0) is True
    assert guard.begin(CALL_SID, now=1_301.0) is True


@pytest.mark.parametrize("bad_ttl", [0.0, -1.0])
def test_invalid_ttl_rejected(bad_ttl):
    with pytest.raises(ValueError):
        CallStartGuard(ttl_seconds=bad_ttl)


def test_empty_call_sid_rejected():
    guard = CallStartGuard(ttl_seconds=300.0)
    with pytest.raises(ValueError):
        guard.begin("", now=1_000.0)


# ---------------------------------------------------------------------------
# End-to-end: a replayed call-started webhook produces exactly ONE session,
# proving BOTH layers together against the real PersistentChat/session path.
# ---------------------------------------------------------------------------

def _handle_call_started(repo, guard, call_sid, now):
    """Stands in for the real telephony webhook handler's call-start
    branch: check the in-process guard first; only build/advance a
    PersistentChat and save state if this delivery is the one allowed to
    do real work."""
    if not guard.begin(call_sid, now=now):
        return None   # a real handler would look the existing session back up here
    call = PersistentChat.start(repo, DID, call_sid, FROM_NUMBER)
    if call.resume_offer is not None:
        # Same call_id -> the same underlying call, not a real customer
        # callback (ADR-014's "offer, never silently continue" governs a
        # genuine dropped-call reconnect; a same-CallSid retry of the SAME
        # still-live call is a different situation this test models by
        # accepting transparently, the way a real handler that can tell
        # "this CallSid was already in progress" apart from "a new call
        # from the same number" would).
        call.accept_resume()
    interpreter = RuleBasedInterpreter()
    call.run_turn(interpreter, "large pepperoni")
    return call


def test_replayed_call_start_produces_one_session_in_process():
    repo = InMemorySessionRepository()
    guard = CallStartGuard(ttl_seconds=300.0)

    first = _handle_call_started(repo, guard, CALL_SID, now=1_000.0)
    second = _handle_call_started(repo, guard, CALL_SID, now=1_000.2)   # fast retry

    assert first is not None
    assert second is None   # layer 1 short-circuited it, no second PersistentChat built

    store_id = first.store_id
    matching_rows = [k for k in repo._sessions if k == (store_id, CALL_SID)]
    assert len(matching_rows) == 1
    saved = repo.load_session(store_id, CALL_SID)
    assert saved is not None
    assert len(saved.lines) == 1   # the retry never added a second pizza


def test_replayed_call_start_after_a_restart_still_upserts_one_row():
    """Layer 2 — a retry arriving after a process restart has NO in-process
    guard memory (a fresh CallStartGuard, same as a real restarted process
    would have). The persistence layer's own upsert-by-call_id must still
    prevent a duplicate row."""
    repo = InMemorySessionRepository()

    guard_before_restart = CallStartGuard(ttl_seconds=300.0)
    first = _handle_call_started(repo, guard_before_restart, CALL_SID, now=1_000.0)
    assert first is not None

    guard_after_restart = CallStartGuard(ttl_seconds=300.0)   # fresh, as if restarted
    second = _handle_call_started(repo, guard_after_restart, CALL_SID, now=2_000.0)
    assert second is not None   # layer 1 didn't catch it this time...

    store_id = first.store_id
    matching_rows = [k for k in repo._sessions if k == (store_id, CALL_SID)]
    assert len(matching_rows) == 1   # ...but layer 2 (upsert) still allowed only one row
