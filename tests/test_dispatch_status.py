"""
T-049 FINAL — durable dispatch status: closes the crash-between-finalize-
and-dispatch gap (docs/STATUS.md's T-049 entries). All offline: no
printer hardware, no Postgres (InMemorySessionRepository + dry-run
TicketPrinter throughout).
"""

from pathlib import Path

import pytest

from lakewood import orders as oe
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.persistence.service import (
    confirm_and_persist, redispatch_pending_orders,
)
from lakewood.printer import PrinterStatus, TicketPrinter

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "lakewood" / "persistence" / "migrations"


# ---------------------------------------------------------------------------
# Migration itself: parseable, reversible, doesn't touch the order's own
# immutable columns.
# ---------------------------------------------------------------------------

def test_migration_0002_up_adds_only_dispatch_columns():
    up = (_MIGRATIONS_DIR / "0002_dispatch_status.up.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE confirmed_orders" in up
    assert "dispatch_status" in up
    assert "dispatched_at" in up
    # never touches the order's own immutable columns
    for immutable_col in ("total_cents", "ticket", "session_json", "order_id"):
        assert f"DROP COLUMN {immutable_col}" not in up
        assert f"ALTER COLUMN {immutable_col}" not in up


def test_migration_0002_down_reverses_exactly_what_up_adds():
    up = (_MIGRATIONS_DIR / "0002_dispatch_status.up.sql").read_text(encoding="utf-8")
    down = (_MIGRATIONS_DIR / "0002_dispatch_status.down.sql").read_text(encoding="utf-8")
    assert "DROP COLUMN IF EXISTS dispatch_status" in down
    assert "DROP COLUMN IF EXISTS dispatched_at" in down
    assert "DROP INDEX IF EXISTS confirmed_orders_undispatched_idx" in down
    assert "CREATE INDEX confirmed_orders_undispatched_idx" in up


# ---------------------------------------------------------------------------
# Repository-level: mark/list, tenant-scoped, no-op on unknown order
# ---------------------------------------------------------------------------

def _seed_confirmed_order(repo, store_id="STORE-001", call_id="C1", order_id_hint=None):
    sess = oe.Session(call_id=call_id, store_id=store_id, from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1
    result = confirm_and_persist(repo, sess, q["quote_id"], f"confirm:{call_id}")
    return result["order_id"]


def test_new_confirmed_order_defaults_to_pending_and_is_listed_undispatched():
    repo = InMemorySessionRepository()
    order_id = _seed_confirmed_order(repo)
    rec = repo.get_confirmed_order("STORE-001", order_id)
    assert rec.dispatch_status == "PENDING"
    assert rec.dispatched_at is None
    listed = repo.list_undispatched_confirmed_orders("STORE-001")
    assert [r.order_id for r in listed] == [order_id]


def test_mark_dispatched_updates_status_and_removes_from_undispatched_list():
    repo = InMemorySessionRepository()
    order_id = _seed_confirmed_order(repo)
    repo.mark_order_dispatched("STORE-001", order_id, 12345.0)
    rec = repo.get_confirmed_order("STORE-001", order_id)
    assert rec.dispatch_status == "DISPATCHED"
    assert rec.dispatched_at == 12345.0
    assert repo.list_undispatched_confirmed_orders("STORE-001") == []


def test_mark_dispatch_failed_keeps_order_visible_in_undispatched_list():
    repo = InMemorySessionRepository()
    order_id = _seed_confirmed_order(repo)
    repo.mark_order_dispatch_failed("STORE-001", order_id)
    rec = repo.get_confirmed_order("STORE-001", order_id)
    assert rec.dispatch_status == "FAILED"
    listed = repo.list_undispatched_confirmed_orders("STORE-001")
    assert [r.order_id for r in listed] == [order_id]


def test_mark_dispatched_on_unknown_order_is_a_safe_no_op():
    repo = InMemorySessionRepository()
    repo.mark_order_dispatched("STORE-001", "NOPE", 1.0)  # must not raise
    repo.mark_order_dispatch_failed("STORE-001", "NOPE")  # must not raise


def test_undispatched_list_is_tenant_scoped():
    repo = InMemorySessionRepository()
    _seed_confirmed_order(repo, store_id="STORE-001", call_id="A")
    _seed_confirmed_order(repo, store_id="STORE-002", call_id="B")
    assert len(repo.list_undispatched_confirmed_orders("STORE-001")) == 1
    assert len(repo.list_undispatched_confirmed_orders("STORE-002")) == 1


def test_delete_customer_does_not_reset_dispatch_status_to_pending():
    """Regression: the original delete_customer reconstruction built a new
    ConfirmedOrder field-by-field and would have silently dropped
    dispatch_status back to its default (PENDING) for an already-DISPATCHED
    order — found and fixed while adding these fields, see
    memory_repository.py's own comment."""
    repo = InMemorySessionRepository()
    order_id = _seed_confirmed_order(repo)
    repo.mark_order_dispatched("STORE-001", order_id, 999.0)
    rec = repo.get_confirmed_order("STORE-001", order_id)
    repo.delete_customer("STORE-001", rec.customer_id)
    after = repo.get_confirmed_order("STORE-001", order_id)
    assert after.dispatch_status == "DISPATCHED"
    assert after.dispatched_at == 999.0
    assert after.customer_id is None


# ---------------------------------------------------------------------------
# confirm_and_persist wiring: real dispatch outcome recorded durably
# ---------------------------------------------------------------------------

def test_successful_dispatch_marks_the_order_dispatched():
    repo = InMemorySessionRepository()
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1

    result = confirm_and_persist(repo, sess, q["quote_id"], "k1",
                                 printer=TicketPrinter(dry_run=True))
    rec = repo.get_confirmed_order("STORE-001", result["order_id"])
    assert rec.dispatch_status == "DISPATCHED"
    assert rec.dispatched_at is not None


class _AlwaysOfflinePrinter(TicketPrinter):
    def status(self):
        return PrinterStatus(online=False, cover_open=False, paper_out=False, paper_low=False)


def test_failed_dispatch_marks_the_order_failed_not_silently_pending(monkeypatch):
    monkeypatch.setattr("lakewood.printer.time.sleep", lambda s: None)
    repo = InMemorySessionRepository()
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1

    result = confirm_and_persist(repo, sess, q["quote_id"], "k1",
                                 printer=_AlwaysOfflinePrinter(dry_run=True))
    rec = repo.get_confirmed_order("STORE-001", result["order_id"])
    assert rec.dispatch_status == "FAILED"
    assert rec.dispatched_at is None
    # staff/ops visibility: this order is findable
    assert result["order_id"] in [r.order_id for r in repo.list_undispatched_confirmed_orders("STORE-001")]


# ---------------------------------------------------------------------------
# The recovery path: redispatch_pending_orders
# ---------------------------------------------------------------------------

class _CountingPrinter(TicketPrinter):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.dispatch_calls = 0

    def dispatch(self, payload, retries=3, backoff=2.0):
        self.dispatch_calls += 1
        return super().dispatch(payload, retries=retries, backoff=backoff)


def test_redispatch_recovers_an_order_stuck_pending_by_a_simulated_crash():
    """Simulates the exact gap: an order finalized (in confirmed_orders,
    dispatch_status=PENDING) but the process 'crashed' before dispatch ever
    ran — no printer was passed to confirm_and_persist at all. A later,
    separate call to redispatch_pending_orders (the recovery action) must
    find it and successfully dispatch it, reconstructed purely from its own
    session_snapshot."""
    repo = InMemorySessionRepository()
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1

    result = confirm_and_persist(repo, sess, q["quote_id"], "k1")  # no printer — simulated crash
    rec = repo.get_confirmed_order("STORE-001", result["order_id"])
    assert rec.dispatch_status == "PENDING"

    printer = _CountingPrinter(dry_run=True)
    outcomes = redispatch_pending_orders(repo, "STORE-001", printer)

    assert len(outcomes) == 1
    assert outcomes[0]["order_id"] == result["order_id"]
    assert outcomes[0]["outcome"]["status"] == "ok"
    assert printer.dispatch_calls == 1
    after = repo.get_confirmed_order("STORE-001", result["order_id"])
    assert after.dispatch_status == "DISPATCHED"


def test_redispatch_never_touches_an_already_dispatched_order():
    """A DISPATCHED order is never returned by the underlying query, so a
    repeated redispatch_pending_orders call never re-prints it — the real
    idempotency guarantee (one confirmed order, one ticket) that matters."""
    repo = InMemorySessionRepository()
    order_id = _seed_confirmed_order(repo)
    repo.mark_order_dispatched("STORE-001", order_id, 1.0)

    printer = _CountingPrinter(dry_run=True)
    outcomes = redispatch_pending_orders(repo, "STORE-001", printer)

    assert outcomes == []
    assert printer.dispatch_calls == 0


def test_redispatch_retries_a_failed_order_when_explicitly_invoked():
    """T-049 FINAL's real cable-pull demo: an order that failed to dispatch
    (network down) must be retriable once a human fixes the underlying
    problem and explicitly triggers recovery — redispatch_pending_orders
    IS that explicit trigger (never a background sweep deciding on its
    own), so retrying a FAILED row here is a deliberate human action, not
    a silent automatic retry loop."""
    repo = InMemorySessionRepository()
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1
    result = confirm_and_persist(repo, sess, q["quote_id"], "k1")
    repo.mark_order_dispatch_failed("STORE-001", result["order_id"])

    printer = _CountingPrinter(dry_run=True)
    outcomes = redispatch_pending_orders(repo, "STORE-001", printer)

    assert len(outcomes) == 1
    assert outcomes[0]["order_id"] == result["order_id"]
    assert outcomes[0]["outcome"]["status"] == "ok"
    assert printer.dispatch_calls == 1
    rec = repo.get_confirmed_order("STORE-001", result["order_id"])
    assert rec.dispatch_status == "DISPATCHED"

    # a SECOND recovery run must not print it again
    outcomes2 = redispatch_pending_orders(repo, "STORE-001", printer)
    assert outcomes2 == []
    assert printer.dispatch_calls == 1


def test_redispatch_is_a_no_op_when_nothing_is_pending():
    repo = InMemorySessionRepository()
    printer = _CountingPrinter(dry_run=True)
    assert redispatch_pending_orders(repo, "STORE-001", printer) == []
    assert printer.dispatch_calls == 0
