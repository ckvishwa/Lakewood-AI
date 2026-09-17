"""
Tenant isolation must be structural, not a convention someone could forget to
apply in a query. T-037 acceptance criteria: "cross-tenant read structurally
impossible; a test proves it" and "tenant key unsettable by any tool, model
output, or client input; test proves it."

These tests use two DIFFERENT store_ids against the SAME repository instance
(`InMemorySessionRepository` — the one the offline gate actually runs). A
real second store doesn't exist yet (single design partner, ADR-014), but the
repository API takes `store_id` as an explicit parameter everywhere
specifically so this is testable today without one.
"""

import pytest

from lakewood import orders as oe
from lakewood.orders import add_item, set_order_type
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.persistence.repository import ConfirmedOrder


@pytest.fixture
def repo():
    return InMemorySessionRepository()


def _session(store_id: str, call_id: str, phone: str = "+12035551234") -> oe.Session:
    s = oe.Session(call_id=call_id, store_id=store_id, from_number=phone)
    set_order_type(s, "pickup")
    return s


def test_same_call_id_different_tenants_do_not_collide(repo):
    a = _session("STORE-001", "SAME-ID")
    add_item(a, "PIZZA", size="LARGE")
    b = _session("STORE-002", "SAME-ID")
    add_item(b, "PIZZA", size="XLARGE")
    repo.save_session(a)
    repo.save_session(b)

    loaded_a = repo.load_session("STORE-001", "SAME-ID")
    loaded_b = repo.load_session("STORE-002", "SAME-ID")
    assert loaded_a.order.lines[0].size == "LARGE"
    assert loaded_b.order.lines[0].size == "XLARGE"


def test_loading_with_the_wrong_store_id_returns_none_not_the_other_tenants_row(repo):
    a = _session("STORE-001", "C1")
    add_item(a, "PIZZA", size="LARGE")
    repo.save_session(a)
    # STORE-002 asking for call_id "C1" must never see STORE-001's row —
    # not even to learn that it exists under a different tenant.
    assert repo.load_session("STORE-002", "C1") is None


def test_find_active_session_by_phone_is_scoped_to_tenant(repo):
    """Two different stores, same customer phone number (a real customer can
    call two different pizza places) — one tenant's resume lookup must never
    surface the other tenant's in-flight cart."""
    a = _session("STORE-001", "CALL-A", phone="+12035551234")
    add_item(a, "PIZZA", size="LARGE")
    repo.save_session(a)
    b = _session("STORE-002", "CALL-B", phone="+12035551234")
    add_item(b, "PIZZA", size="SMALL")
    repo.save_session(b)

    found_1 = repo.find_active_session_by_phone("STORE-001", "+12035551234")
    found_2 = repo.find_active_session_by_phone("STORE-002", "+12035551234")
    assert found_1.call_id == "CALL-A"
    assert found_2.call_id == "CALL-B"


def test_customer_ids_are_scoped_to_tenant_even_for_the_same_phone(repo):
    cid1 = repo.get_or_create_customer("STORE-001", "+12035551234")
    cid2 = repo.get_or_create_customer("STORE-002", "+12035551234")
    assert cid1 != cid2, "the same phone number at two tenants must not share one customer_id"


def test_confirmed_order_lookup_is_scoped_to_tenant(repo):
    record = ConfirmedOrder(
        store_id="STORE-001", order_id="AI-SHARED", call_id="C1",
        customer_id=None, total_cents=1500, ticket="t", idempotency_key="k1",
        confirmed_at=0.0, session_snapshot={},
    )
    repo.save_confirmed_order(record)
    assert repo.get_confirmed_order("STORE-002", "AI-SHARED") is None
    assert repo.get_confirmed_order_by_idempotency_key("STORE-002", "k1") is None
    assert repo.get_confirmed_order("STORE-001", "AI-SHARED") is not None


def test_purge_expired_sessions_only_touches_the_named_tenant(repo):
    a = _session("STORE-001", "C1")
    add_item(a, "PIZZA", size="LARGE")
    repo.save_session(a)
    b = _session("STORE-002", "C2")
    add_item(b, "PIZZA", size="LARGE")
    repo.save_session(b)

    import time
    cutoff = time.time() + 1
    purged = repo.purge_expired_sessions("STORE-001", cutoff)
    assert purged == 1
    assert repo.load_session("STORE-001", "C1") is None
    assert repo.load_session("STORE-002", "C2") is not None, \
        "purging STORE-001 must never touch STORE-002's rows"


def test_no_tool_accepts_store_id_and_no_persisted_call_can_change_it():
    """F2, restated for the persistence boundary: `Session.store_id` is set
    exactly once, at construction, from `config.py::store_for_did` — never by
    a tool (already proven for the live tool surface by
    `tests/test_orders.py::test_f2_no_tool_accepts_store_id`). Saving and
    reloading a session must reproduce the SAME store_id, never one supplied
    by a repository caller independent of the session object itself —
    `SessionRepository.save_session`/`load_session` take `store_id` only to
    select which row, never to assign one."""
    import inspect

    for fn in oe.TOOLS:
        params = inspect.signature(fn).parameters
        assert "store_id" not in params, f"{fn.__name__} exposes store_id"

    from lakewood.persistence.memory_repository import InMemorySessionRepository
    from lakewood.persistence.serialization import session_from_dict, session_to_dict

    repo = InMemorySessionRepository()
    sess = _session("STORE-001", "C1")
    repo.save_session(sess)
    reloaded = repo.load_session("STORE-001", "C1")
    assert reloaded.store_id == "STORE-001"

    # Even a maliciously-edited stored dict cannot forge a cross-tenant read:
    # `load_session` filters by the (store_id, call_id) key BEFORE any dict
    # content is ever read back into a Session.
    tampered = session_to_dict(sess)
    tampered["store_id"] = "STORE-002"          # simulate a corrupted/forged row
    forged = session_from_dict(tampered)
    assert forged.store_id == "STORE-002"        # the dict->Session conversion itself
    # is honest about what it was given — the guarantee lives in the repository's
    # lookup key, not in blind trust of the blob's own claimed store_id. Prove
    # the repository never uses the blob's internal store_id for that lookup:
    repo._sessions[("STORE-001", "C1")] = tampered   # force a tenant/blob mismatch
    reloaded_again = repo.load_session("STORE-001", "C1")
    assert reloaded_again.call_id == "C1"
    # STORE-002 still cannot read it via the correct key for STORE-002:
    assert repo.load_session("STORE-002", "C1") is None
