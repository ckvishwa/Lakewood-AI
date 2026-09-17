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
                        idempotency_key: str | None = None) -> dict:
    """F6 (idempotent confirm), extended across a process restart. `Session
    .idempotency` only protects a replay within the same in-memory object —
    exactly the gap a dropped connection after a successful `confirm_order`
    but before the customer heard the confirmation would expose. Checking
    `confirmed_orders` FIRST closes it: a retried confirm after a restart
    finds the original order, never a second one.
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
    return result
