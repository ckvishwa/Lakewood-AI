"""
"A guard that holds in memory and not after a reload isn't a guard" — the
task's own framing. Each test here builds state through the REAL tools
(never constructs a `Session` with fields set by hand), persists, reloads
into a brand-new `Session` object via the repository, and proves the same
fail-safe still fires against the reloaded object exactly as it would have
against the original.
"""

import pytest

from lakewood import orders as oe
from lakewood.orders import (
    add_item, begin_confirmation, confirm_order, request_quote,
    set_order_type, _register_disambiguation,
)
from lakewood.persistence.memory_repository import InMemorySessionRepository


@pytest.fixture
def repo():
    return InMemorySessionRepository()


def _fresh(call_id="C1"):
    s = oe.Session(call_id=call_id, store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


def test_f14_same_turn_confirmation_prohibition_survives_reload(repo):
    """Build up to AWAITING_CONFIRMATION, save, reload as a NEW object, then
    try to confirm in what the reloaded object still thinks is the same
    turn — must still be refused (F14), exactly as it would in memory."""
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    begin_confirmation(sess)
    assert sess.confirmation_turn == sess.turn        # same-turn, as begin_confirmation left it
    repo.save_session(sess)

    reloaded = repo.load_session("STORE-001", "C1")
    assert reloaded is not sess                        # genuinely a different object
    assert reloaded.confirmation_turn == reloaded.turn
    result = confirm_order(reloaded, reloaded.quote_id)
    assert result["status"] == "error"
    assert result["code"] == "PREMATURE_CONFIRMATION"


def test_f14_confirmation_succeeds_after_reload_in_a_later_turn(repo):
    """The other half of F14: once a genuinely later turn arrives — even on
    the reloaded object — confirmation must still work. Proves the reload
    didn't just make the gate permanently stuck closed."""
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    begin_confirmation(sess)
    repo.save_session(sess)

    reloaded = repo.load_session("STORE-001", "C1")
    reloaded.turn += 1                                  # the customer's next real utterance
    result = confirm_order(reloaded, reloaded.quote_id)
    assert result["status"] == "ok"
    assert reloaded.state == "CONFIRMED"


def test_f15_unresolved_lookup_blocks_confirmation_after_reload(repo):
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    sess.unresolved_lookups.append("root beer")
    sess.to("BUILDING")
    request_quote(sess)
    repo.save_session(sess)

    reloaded = repo.load_session("STORE-001", "C1")
    assert reloaded.unresolved_lookups == ["root beer"]
    result = begin_confirmation(reloaded)
    assert result["status"] == "error"
    assert result["code"] == "UNRESOLVED_REQUEST"


def test_f17_pending_disambiguation_blocks_confirmation_after_reload(repo):
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    hits = [{"kind": "topping", "name": "PEPPERS"}, {"kind": "topping", "name": "JALAPENO"}]
    _register_disambiguation(sess, "peppers", hits)
    request_quote(sess)
    repo.save_session(sess)

    reloaded = repo.load_session("STORE-001", "C1")
    assert len(reloaded.pending_disambiguations) == 1
    result = begin_confirmation(reloaded)
    assert result["status"] == "error"
    assert result["code"] == "UNRESOLVED_REQUEST"


def test_f16_readback_still_present_after_reload(repo):
    """F16: begin_confirmation always returns a real cart-diff readback —
    proven here against a session that has been through a save/reload cycle,
    not just a freshly-built one."""
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    repo.save_session(sess)

    reloaded = repo.load_session("STORE-001", "C1")
    result = begin_confirmation(reloaded)
    assert result["status"] == "ok"
    assert "readback" in result and result["readback"]


def test_mutating_after_reload_forces_a_fresh_quote(repo):
    """Any real tool mutation (F4) resets state to BUILDING and clears the
    quote — proven here against a session that has already been through a
    save/reload cycle. The old quote_id can no longer confirm anything;
    the customer must hear a fresh, re-priced total first."""
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    old_quote_id = sess.quote_id
    repo.save_session(sess)

    reloaded = repo.load_session("STORE-001", "C1")
    add_item(reloaded, "PIZZA", size="SMALL")           # cart changes after the quote
    reloaded.turn += 1
    result = confirm_order(reloaded, old_quote_id)
    assert result["status"] == "error"
    assert result["code"] == "BAD_STATE"                # F4 already forced BUILDING


def test_f5_cart_hash_check_still_enforced_after_reload(repo):
    """The hash check itself (F5), independent of the state guard above:
    force a reloaded session back to AWAITING_CONFIRMATION with a cart that
    no longer matches its own stored `quote_hash` — the shape a storage-layer
    inconsistency would produce, not a normal tool call (every real mutation
    tool resets state itself, per the test above). `confirm_order` must still
    catch the mismatch rather than trust a state flag alone."""
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    quote_id = sess.quote_id
    sess.to("AWAITING_CONFIRMATION")
    sess.confirmation_turn = sess.turn - 1              # pretend the readback already happened
    repo.save_session(sess)

    reloaded = repo.load_session("STORE-001", "C1")
    assert reloaded is not None
    reloaded.order.lines[0].quantity = 2                # tamper directly — bypass F4 on purpose
    result = confirm_order(reloaded, quote_id)
    assert result["status"] == "error"
    assert result["code"] == "CART_CHANGED"


def test_f6_idempotent_confirm_survives_reload_via_confirmed_orders_lookup(repo):
    """`Session.idempotency` only protects a replay against the SAME
    in-memory object — the realistic dropped-connection-after-confirm case
    needs the repository's own idempotency-key lookup
    (`get_confirmed_order_by_idempotency_key`), exercised through
    `service.confirm_and_persist`."""
    from lakewood.persistence.service import confirm_and_persist

    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    begin_confirmation(sess)
    sess.turn += 1
    first = confirm_and_persist(repo, sess, sess.quote_id, idempotency_key="idem-xyz")
    assert first["status"] == "ok"
    assert repo.load_session("STORE-001", "C1") is None      # finalized, in-flight row gone

    # Simulate a fresh process picking up a retried confirm for the same call:
    # nothing in memory ties it to `sess` anymore — only the idempotency key.
    replay_sess = _fresh(call_id="C1")
    replay_sess.state = "AWAITING_CONFIRMATION"
    second = confirm_and_persist(repo, replay_sess, "irrelevant-quote-id", idempotency_key="idem-xyz")
    assert second["status"] == "ok"
    assert second["order_id"] == first["order_id"]
    assert second["total"] == first["total"]
