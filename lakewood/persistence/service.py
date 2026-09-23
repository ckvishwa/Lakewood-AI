"""
Thin orchestration layer — the only place that calls both `orders.py` and a
`SessionRepository` in the same function. `orders.py` itself stays exactly as
it was: pure, in-memory, no persistence import (see `__init__.py`). This
module is what a future voice/chat call loop wires up; T-037's own scope is
building it and proving it works, not wiring it into `chat.py`'s text sandbox
or a telephony loop (both later phases).
"""

from __future__ import annotations

import time

from .. import orders as oe
from .customers import normalize_phone
from .recovery import RevalidationResult, find_resumable_session, revalidate_and_reprice
from .repository import ConfirmedOrder, SessionRepository
from .serialization import session_to_dict


def create_session(store_id: str, call_id: str, from_number: str) -> oe.Session:
    """A brand-new call. `store_id` must already be server-resolved (F2) —
    this function never derives it, matching `config.py::store_for_did`'s own
    contract."""
    return oe.Session(call_id=call_id, store_id=store_id, from_number=from_number)


def resume_or_create(repo: SessionRepository, store_id: str, call_id: str,
                     from_number: str, now: float | None = None,
                     ) -> tuple[oe.Session, RevalidationResult | None]:
    """The recovery story's entry point. Looks for a resumable session for
    this (tenant, phone); if found and within the resume window, revalidates
    and re-prices it (never silently trusts the stored cart) and returns it
    alongside the `RevalidationResult` so the caller can OFFER it to the
    customer ("still have your large pepperoni — want to pick that back up,
    or start fresh?" is a conversation-layer/voice concern, out of this
    task's scope; this function only guarantees what's offered is honest).
    If nothing is resumable, returns a fresh session and `None`.
    """
    candidate = find_resumable_session(repo, store_id, from_number, now=now)
    if candidate is None:
        return create_session(store_id, call_id, from_number), None
    result = revalidate_and_reprice(candidate)
    return result.session, result


def save_progress(repo: SessionRepository, session: oe.Session) -> None:
    """Call after any tool mutates `session`. No-ops once the order is
    CONFIRMED or otherwise terminal — `confirm_and_persist` (CONFIRMED) and
    plain terminal transitions (TRANSFERRED/CANCELLED/etc.) don't get a
    resumable in-flight row; there's nothing left to resume."""
    if session.state == "CONFIRMED" or session.state in oe.TERMINAL:
        return
    repo.save_session(session)


def confirm_and_persist(repo: SessionRepository, session: oe.Session, quote_id: str,
                        idempotency_key: str | None = None, printer=None) -> dict:
    """F6 (idempotent confirm), extended across a process restart. `Session
    .idempotency` only protects a replay within the same in-memory object —
    exactly the gap a dropped connection after a successful `confirm_order`
    but before the customer heard the confirmation would expose. Checking
    `confirmed_orders` FIRST closes it: a retried confirm after a restart
    finds the original order, never a second one.

    T-049: `printer`, when given, is dispatched to exactly once — only on
    the branch below that actually just confirmed THIS order, never on the
    early-return replay-cache-hit branch above. That is the whole
    idempotency guarantee for "one confirmed order, one ticket": a retried/
    replayed confirm_order (same idempotency_key) returns before this
    function ever reaches the dispatch call. `dispatch_confirmed_order`
    itself is the second layer — it requires `session.state == "CONFIRMED"`
    and immediately transitions it away, so even a caller that reached this
    branch twice on the same live session object (not a real code path
    today, but not assumed away either) cannot double-print.

    A dispatch failure is a kitchen/staff-side problem, not a reason to
    retract the customer's already-valid confirmation — `result` (the
    customer-facing ok payload) is returned unchanged regardless of dispatch
    outcome; `dispatch_confirmed_order` logs the real outcome on `session`
    itself (see its own docstring) for anything that reads session state/
    events, and never raises past this function.

    T-049 FINAL: the dispatch outcome is now ALSO recorded durably on the
    `confirmed_orders` row itself (`mark_order_dispatched`/
    `mark_order_dispatch_failed`) — closing the gap T-049 disclosed: if the
    process crashes between `finalize_session` and dispatch completing, the
    order was previously confirmed with no durable trace of whether it ever
    reached a printer. It still defaults to `dispatch_status='PENDING'` at
    insert (migrations/0002_dispatch_status.up.sql) for that exact crash
    window, but now `list_undispatched_confirmed_orders` and
    `redispatch_pending_orders` below make a stuck order findable and
    recoverable rather than silently lost — see docs/STATUS.md's T-049
    FINAL entry.
    """
    if idempotency_key:
        existing = repo.get_confirmed_order_by_idempotency_key(
            session.store_id, idempotency_key)
        if existing is not None:
            return oe.ok(order_id=existing.order_id, total=oe.money(existing.total_cents),
                        ticket=existing.ticket)

    result = oe.confirm_order(session, quote_id, idempotency_key)
    if result.get("status") == "ok" and session.state == "CONFIRMED":
        phone = normalize_phone(session.from_number)
        customer_id = repo.get_or_create_customer(session.store_id, phone)
        record = ConfirmedOrder(
            store_id=session.store_id,
            order_id=session.order_id,
            call_id=session.call_id,
            customer_id=customer_id,
            total_cents=session.order.total(),
            ticket=result["ticket"],
            idempotency_key=idempotency_key,
            confirmed_at=time.time(),
            session_snapshot=session_to_dict(session),
        )
        repo.finalize_session(session, record)
        if printer is not None:
            from ..printer import dispatch_confirmed_order
            outcome = dispatch_confirmed_order(session, printer)
            _record_dispatch_outcome(repo, session.store_id, session.order_id, outcome)
    return result


def _record_dispatch_outcome(repo: SessionRepository, store_id: str, order_id: str,
                             outcome: dict) -> None:
    """T-049 FINAL: translate `dispatch_confirmed_order`'s return shape into
    the durable `dispatch_status` column. HELD_FOR_OPEN (`outcome["held"]`)
    deliberately stays PENDING — it isn't a failure, the store's own
    scheduler dispatches it later; there is no scheduler in this codebase
    yet (ARCHITECTURE.md's own disclosed gap), so "stays findable via
    list_undispatched_confirmed_orders until someone/something re-dispatches
    it" is the correct, honest state, not a silent success."""
    if outcome.get("status") == "ok" and outcome.get("printed"):
        repo.mark_order_dispatched(store_id, order_id, time.time())
    elif outcome.get("code") == "FAILED_DISPATCH":
        repo.mark_order_dispatch_failed(store_id, order_id)


def redispatch_pending_orders(repo: SessionRepository, store_id: str, printer) -> list[dict]:
    """T-049 FINAL: the RECOVERY path — closes both real gaps at once: (1)
    the crash-between-finalize-and-dispatch window (an order stuck
    PENDING, never actually attempted), and (2) a real dispatch failure a
    human has since fixed (network cable reconnected, paper reloaded,
    cover closed) and wants retried NOW, not automatically.

    ALWAYS manually invoked (a staff/ops action, or a script) — this
    project has no task scheduler yet (ARCHITECTURE.md's own disclosed
    gap: "no cron/task-runner in this codebase"), so this is never a
    background sweep deciding on its own to retry. Because every call is
    already a deliberate human/script decision, it retries BOTH PENDING
    and FAILED rows — "needs a human before retrying" is satisfied by this
    function only ever running when a human (or an explicit follow-up
    task) chose to run it, not by refusing FAILED rows internally. Proven
    live: T-049 FINAL's cable-pull demo produced a real FAILED order, and
    this function correctly re-dispatched it — exactly once — after the
    cable was reconnected (docs/STATUS.md).

    Idempotent by construction: a DISPATCHED row is never returned by
    `list_undispatched_confirmed_orders` in the first place, so it is
    never touched here regardless of how many times this function runs.
    Each reconstructed `Session` is rebuilt from its own `session_snapshot`
    (captured at the moment `confirm_order` set state to CONFIRMED, before
    dispatch ever ran — see `confirm_and_persist` above), so
    `dispatch_confirmed_order`'s own `state == "CONFIRMED"` guard passes
    exactly as it would have on the original attempt.
    """
    from ..printer import dispatch_confirmed_order
    from .serialization import session_from_dict

    results = []
    for record in repo.list_undispatched_confirmed_orders(store_id):
        session = session_from_dict(record.session_snapshot)
        outcome = dispatch_confirmed_order(session, printer)
        _record_dispatch_outcome(repo, store_id, record.order_id, outcome)
        results.append({"order_id": record.order_id, "outcome": outcome})
    return results
