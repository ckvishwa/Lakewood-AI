"""
T-053 Phase 1 (Part B.5): concurrency the in-memory repository structurally
cannot show — real threads, real separate connections, a real Postgres
server. Skipped unless `LAKEWOOD_POSTGRES_TEST_DSN` is set, same gate as
`test_persistence_postgres_contract.py`.

Found live, while building these: `PostgresSessionRepository`'s five
read-only methods left their connection "idle in transaction" indefinitely
(no `with self._conn:` to commit/rollback under `autocommit=False`) — a
real deadlock reproduced directly (a stuck read blocked a later TRUNCATE),
not a theoretical concern. Fixed in the same task; see
`lakewood/persistence/postgres_repository.py`'s own comment on
`load_session`. These tests exercise the FIXED behavior.
"""

from __future__ import annotations

import os
import threading

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("LAKEWOOD_POSTGRES_TEST_DSN"),
    reason="LAKEWOOD_POSTGRES_TEST_DSN not set — no live Postgres")

DSN = os.environ.get("LAKEWOOD_POSTGRES_TEST_DSN")


@pytest.fixture(autouse=True)
def _schema():
    from lakewood.persistence.migrations.runner import migrate_up
    migrate_up(DSN)


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    import psycopg2
    conn = psycopg2.connect(DSN)
    with conn, conn.cursor() as cur:
        cur.execute("TRUNCATE sessions, confirmed_orders, customers CASCADE")
    conn.close()


def test_two_connections_writing_the_same_call_id_never_deadlocks_or_corrupts():
    """Two separate PostgresSessionRepository instances (two real
    connections, the shape a real deployment with more than one
    orchestrator process/thread actually has) both `save_session` the SAME
    (store_id, call_id) key at the same time. The guarantee under test is
    not "who wins" (last-write-wins on a genuine race is fine, no ordering
    is promised) — it's that the concurrent UPSERT never deadlocks and the
    row afterward is a clean, complete write from ONE of the two attempts,
    never a corrupted partial merge of both."""
    from lakewood import orders as oe
    from lakewood.orders import add_item, set_order_type
    from lakewood.persistence.postgres_repository import PostgresSessionRepository

    repo_a = PostgresSessionRepository(DSN)
    repo_b = PostgresSessionRepository(DSN)

    sess_a = oe.Session(call_id="RACE-1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(sess_a, "pickup")
    add_item(sess_a, "PIZZA", size="LARGE")

    sess_b = oe.Session(call_id="RACE-1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(sess_b, "pickup")
    add_item(sess_b, "PIZZA", size="SMALL")

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _write(repo, sess):
        try:
            barrier.wait(timeout=5)
            repo.save_session(sess)
        except Exception as e:  # noqa: BLE001 — captured for the assertion below
            errors.append(e)

    t1 = threading.Thread(target=_write, args=(repo_a, sess_a))
    t2 = threading.Thread(target=_write, args=(repo_b, sess_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "a thread is still blocked — deadlock"
    assert errors == [], f"concurrent save_session raised: {errors}"

    final = repo_a.load_session("STORE-001", "RACE-1")
    assert final is not None
    assert final.order.lines[0].size in ("LARGE", "SMALL")  # one clean write, not a merge

    repo_a.close()
    repo_b.close()


def test_confirm_replay_racing_a_real_dispatch_keeps_exactly_one_ticket():
    """F6 idempotency, under an ACTUAL race — not sequential test order,
    which is all the in-memory dict-based repository could ever prove
    (single-threaded dict access is never really concurrent). Two
    connections both attempt `finalize_session` with the SAME
    idempotency_key at the same instant; the unique partial index on
    (store_id, idempotency_key) must let exactly one succeed and the
    other must observe ConfirmedOrderExists — never two rows, never a
    silent double-charge."""
    from lakewood import orders as oe
    from lakewood.orders import add_item, request_quote, set_order_type
    from lakewood.persistence.postgres_repository import PostgresSessionRepository
    from lakewood.persistence.repository import ConfirmedOrder, ConfirmedOrderExists

    repo_a = PostgresSessionRepository(DSN)
    repo_b = PostgresSessionRepository(DSN)

    def _confirmed_session(call_id):
        s = oe.Session(call_id=call_id, store_id="STORE-001", from_number="+12035551234")
        set_order_type(s, "pickup")
        add_item(s, "PIZZA", size="LARGE")
        request_quote(s)
        s.to("CONFIRMED")
        return s

    # The real scenario: the SAME call (call_id="RACE-2") confirmed twice —
    # a genuine replay (e.g. a client retry after a dropped response) racing
    # what looks like a second, independent confirm attempt. Both records
    # must reference that same call_id, matching what finalize_session
    # actually validates.
    key = "RACE-CONFIRM-1"
    record_a = ConfirmedOrder(
        store_id="STORE-001", order_id="RACE-ORDER-A", call_id="RACE-2",
        customer_id=None, total_cents=1500, ticket="ticket-a", idempotency_key=key,
        confirmed_at=0.0, session_snapshot={"stub": "a"})
    record_b = ConfirmedOrder(
        store_id="STORE-001", order_id="RACE-ORDER-B", call_id="RACE-2",
        customer_id=None, total_cents=1500, ticket="ticket-b", idempotency_key=key,
        confirmed_at=0.0, session_snapshot={"stub": "b"})

    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def _finalize(name, repo, sess, record):
        try:
            barrier.wait(timeout=5)
            repo.finalize_session(sess, record)
            outcomes[name] = "ok"
        except ConfirmedOrderExists:
            outcomes[name] = "exists"
        except Exception as e:  # noqa: BLE001
            outcomes[name] = e

    t1 = threading.Thread(target=_finalize, args=("a", repo_a, _confirmed_session("RACE-2"), record_a))
    t2 = threading.Thread(target=_finalize, args=("b", repo_b, _confirmed_session("RACE-2"), record_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "a thread is still blocked — deadlock"
    results = sorted(outcomes.values(), key=str)
    assert results == ["exists", "ok"], f"expected exactly one winner, one ConfirmedOrderExists: {outcomes}"

    winner = repo_a.get_confirmed_order_by_idempotency_key("STORE-001", key)
    assert winner is not None
    assert winner.order_id in ("RACE-ORDER-A", "RACE-ORDER-B")  # exactly one row exists

    repo_a.close()
    repo_b.close()
