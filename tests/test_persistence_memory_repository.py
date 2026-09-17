"""
`InMemorySessionRepository` — save/load/finalize/purge, and the F-series
invariants surviving a save+reload boundary. This is the repository the full
suite and the T-016 offline gate actually exercise; the Postgres adapter's
contract is checked separately (`test_persistence_postgres_contract.py`,
schema-only + skipped-without-DSN).
"""

import time

import pytest

from lakewood import orders as oe
from lakewood.orders import (
    add_item, add_modifier, begin_confirmation, confirm_order, request_quote,
    set_order_type,
)
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.persistence.repository import ConfirmedOrder, ConfirmedOrderExists


@pytest.fixture
def repo():
    return InMemorySessionRepository()


@pytest.fixture
def sess():
    s = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


def test_save_then_load_round_trips(repo, sess):
    add_item(sess, "PIZZA", size="LARGE")
    repo.save_session(sess)
    loaded = repo.load_session("STORE-001", "C1")
    assert loaded is not None
    assert loaded.order.subtotal() == sess.order.subtotal()


def test_load_missing_returns_none(repo):
    assert repo.load_session("STORE-001", "NOPE") is None


def test_delete_session(repo, sess):
    add_item(sess, "PIZZA", size="LARGE")
    repo.save_session(sess)
    repo.delete_session("STORE-001", "C1")
    assert repo.load_session("STORE-001", "C1") is None


def test_save_refuses_confirmed_session(repo, sess):
    add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    begin_confirmation(sess)
    sess.turn += 1                      # F14 — separate turn before confirming
    confirm_order(sess, sess.quote_id)
    assert sess.state == "CONFIRMED"
    with pytest.raises(ValueError):
        repo.save_session(sess)


def test_find_active_session_by_phone_returns_most_recent(repo):
    a = oe.Session(call_id="A", store_id="STORE-001", from_number="+12035551234")
    set_order_type(a, "pickup")
    repo.save_session(a)
    time.sleep(0.01)
    b = oe.Session(call_id="B", store_id="STORE-001", from_number="+12035551234")
    set_order_type(b, "pickup")
    repo.save_session(b)
    found = repo.find_active_session_by_phone("STORE-001", "+12035551234")
    assert found.call_id == "B"


def test_find_active_session_by_phone_skips_terminal(repo, sess):
    add_item(sess, "PIZZA", size="LARGE")
    repo.save_session(sess)
    request_quote(sess)
    begin_confirmation(sess)
    sess.turn += 1
    confirm_order(sess, sess.quote_id)
    # CONFIRMED sessions are never saved via save_session (previous test) —
    # simulate the realistic shape: the row was deleted by finalize_session,
    # so nothing is left to find.
    repo.delete_session("STORE-001", "C1")
    assert repo.find_active_session_by_phone("STORE-001", "+12035551234") is None


def test_finalize_session_moves_to_confirmed_and_deletes_in_flight_row(repo, sess):
    add_item(sess, "PIZZA", size="LARGE")
    repo.save_session(sess)
    request_quote(sess)
    begin_confirmation(sess)
    sess.turn += 1
    result = confirm_order(sess, sess.quote_id)
    record = ConfirmedOrder(
        store_id=sess.store_id, order_id=sess.order_id, call_id=sess.call_id,
        customer_id="CUST-X", total_cents=sess.order.total(), ticket=result["ticket"],
        idempotency_key=None, confirmed_at=time.time(), session_snapshot={},
    )
    repo.finalize_session(sess, record)
    assert repo.load_session("STORE-001", "C1") is None
    fetched = repo.get_confirmed_order("STORE-001", sess.order_id)
    assert fetched is not None
    assert fetched.total_cents == sess.order.total()


def test_confirmed_order_is_immutable(repo, sess):
    record = ConfirmedOrder(
        store_id="STORE-001", order_id="AI-000001", call_id="C1",
        customer_id=None, total_cents=1500, ticket="t", idempotency_key=None,
        confirmed_at=time.time(), session_snapshot={},
    )
    repo.save_confirmed_order(record)
    with pytest.raises(ConfirmedOrderExists):
        repo.save_confirmed_order(record)


def test_confirmed_order_lookup_by_idempotency_key(repo):
    record = ConfirmedOrder(
        store_id="STORE-001", order_id="AI-000002", call_id="C2",
        customer_id=None, total_cents=1500, ticket="t", idempotency_key="idem-1",
        confirmed_at=time.time(), session_snapshot={},
    )
    repo.save_confirmed_order(record)
    found = repo.get_confirmed_order_by_idempotency_key("STORE-001", "idem-1")
    assert found is not None and found.order_id == "AI-000002"
    assert repo.get_confirmed_order_by_idempotency_key("STORE-001", "missing") is None


def test_purge_expired_sessions(repo, sess):
    add_item(sess, "PIZZA", size="LARGE")
    repo.save_session(sess)
    cutoff = time.time() + 1     # everything saved so far is "older than" this
    time.sleep(0.01)
    purged = repo.purge_expired_sessions("STORE-001", cutoff)
    assert purged == 1
    assert repo.load_session("STORE-001", "C1") is None


def test_get_or_create_customer_is_idempotent(repo):
    cid1 = repo.get_or_create_customer("STORE-001", "+12035551234")
    cid2 = repo.get_or_create_customer("STORE-001", "+12035551234")
    assert cid1 == cid2


def test_delete_customer_removes_in_flight_session_and_unlinks_confirmed_order(repo, sess):
    add_item(sess, "PIZZA", size="LARGE")
    repo.save_session(sess)
    cid = repo.get_or_create_customer("STORE-001", "+12035551234")
    record = ConfirmedOrder(
        store_id="STORE-001", order_id="AI-000003", call_id="C3",
        customer_id=cid, total_cents=1500, ticket="t", idempotency_key=None,
        confirmed_at=time.time(), session_snapshot={},
    )
    repo.save_confirmed_order(record)
    repo.delete_customer("STORE-001", cid)
    assert repo.load_session("STORE-001", "C1") is None       # in-flight session gone
    still_there = repo.get_confirmed_order("STORE-001", "AI-000003")
    assert still_there is not None                            # financial record kept
    assert still_there.customer_id is None                    # identity link severed
