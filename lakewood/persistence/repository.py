"""
`SessionRepository` — the port. `memory_repository.py` and
`postgres_repository.py` are the two adapters (see ADR-014 for why both
exist: the full suite and the T-016 offline gate must keep running with no
database server).

**Tenant safety is structural, not a convention.** Every method takes
`store_id` as an explicit, separate argument from any record identifier
(`call_id`, `order_id`, `customer_id`) — never bundled inside an opaque key a
caller could forge. A lookup for the wrong `store_id` returns "not found," the
same shape as any other miss; it is never able to see the row exists but sync
belongs to someone else. Nothing in this module, `orders.py`'s tool surface,
or any model output can set `store_id` — it is resolved once, server-side,
from the inbound DID (`config.py::store_for_did`) before a `Session` is ever
constructed, and every repository call site here just forwards that same
value. `tests/test_persistence_tenant_isolation.py` proves a second tenant's
`store_id` cannot read, resume, or purge the first tenant's rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..orders import Session


class SessionNotFound(KeyError):
    """Raised by `load_session_or_raise`; `load_session` itself returns None."""


class ConfirmedOrderExists(ValueError):
    """A confirmed order is immutable (F12) — `save_confirmed_order` raises
    this rather than silently overwriting a row that already exists."""


@dataclass(frozen=True)
class ConfirmedOrder:
    """The durable record of a placed order. The order's own content/total/
    ticket are immutable once written — a correction creates a new linked
    order (F12), never an edit. `dispatch_status`/`dispatched_at` are the
    ONE deliberate exception (T-049 FINAL): a physical-world fact that
    settles AFTER confirmation, updated in place via `mark_order_dispatched`/
    `mark_order_dispatch_failed`, never via `save_confirmed_order` again."""
    store_id: str
    order_id: str
    call_id: str
    customer_id: Optional[str]
    total_cents: int
    ticket: str
    idempotency_key: Optional[str]
    confirmed_at: float
    session_snapshot: dict            # session_to_dict() at the moment of confirmation
    dispatch_status: str = "PENDING"  # PENDING | DISPATCHED | FAILED
    dispatched_at: Optional[float] = None


class SessionRepository(ABC):
    """Everything a resumable phone call needs. No method here accepts a
    price, and none accepts `store_id` from anywhere but server-resolved
    call context — same two invariants `orders.py` itself enforces
    (F1/F2), extended to the persistence boundary."""

    # -- in-flight session (cart) -------------------------------------------

    @abstractmethod
    def save_session(self, session: Session) -> None:
        """Upsert the in-flight session. Refuses a session whose state is
        CONFIRMED or otherwise terminal — call `finalize_session` for those,
        so a confirmed order can never be reached through the mutable path."""

    @abstractmethod
    def load_session(self, store_id: str, call_id: str) -> Optional[Session]:
        """Returns None if absent OR if `store_id` doesn't match — a caller
        can never distinguish "wrong tenant" from "never existed."""

    @abstractmethod
    def delete_session(self, store_id: str, call_id: str) -> None:
        """No-op if absent. Used by `finalize_session` and explicit
        cancellation/deletion paths."""

    @abstractmethod
    def find_active_session_by_phone(self, store_id: str, from_number: str) -> Optional[Session]:
        """Most recent non-terminal session for this (tenant, phone) pair, or
        None. This is the lookup `recovery.py` uses — it does not apply the
        resume-window/TTL policy itself, that's `recovery.py`'s job."""

    @abstractmethod
    def session_last_updated(self, store_id: str, call_id: str) -> Optional[float]:
        """Epoch seconds of the last `save_session` call for this row, or
        None if absent. Tracked by the repository, not `Session` itself —
        `orders.py` stays untouched by a persistence-only concern."""

    @abstractmethod
    def purge_expired_sessions(self, store_id: str, older_than_epoch: float) -> int:
        """Delete in-flight sessions last updated before the cutoff. Returns
        the count deleted. Never touches `confirmed_orders` — those have
        their own, longer retention (see `retention.py`)."""

    # -- confirmed orders (immutable) ----------------------------------------

    @abstractmethod
    def save_confirmed_order(self, record: ConfirmedOrder) -> None:
        """Insert-only. Raises `ConfirmedOrderExists` if `(store_id, order_id)`
        is already present — F12's immutability, enforced here too, not just
        assumed from `orders.py`'s own state machine."""

    @abstractmethod
    def get_confirmed_order(self, store_id: str, order_id: str) -> Optional[ConfirmedOrder]:
        ...

    @abstractmethod
    def get_confirmed_order_by_idempotency_key(
        self, store_id: str, idempotency_key: str) -> Optional[ConfirmedOrder]:
        """F6 across a restart: `Session.idempotency` only protects a retried
        `confirm_order` within the same in-memory object. Once an order is
        actually finalized, a replay of the same key must find THIS instead —
        see `service.py::confirm_and_persist`."""

    @abstractmethod
    def finalize_session(self, session: Session, record: ConfirmedOrder) -> None:
        """Atomically: write `record` to confirmed_orders, delete the
        matching in-flight session row. One transaction — a crash between the
        two steps must never leave both a confirmed order AND a resumable
        in-flight session for the same call."""

    @abstractmethod
    def purge_confirmed_orders(self, store_id: str, older_than_epoch: float) -> int:
        """Retention sweep for confirmed orders (see ADR-014's retention
        policy) — a longer window than in-flight sessions, and a separate
        method so the two retention periods can never be accidentally
        conflated at a call site."""

    # -- dispatch status (T-049 FINAL) ---------------------------------------

    @abstractmethod
    def mark_order_dispatched(self, store_id: str, order_id: str, dispatched_at: float) -> None:
        """The one deliberate post-insert update to a `confirmed_orders`
        row — see `ConfirmedOrder`'s own docstring. No-op if the order
        doesn't exist for this tenant (never raises for a caller that lost
        a race with a purge)."""

    @abstractmethod
    def mark_order_dispatch_failed(self, store_id: str, order_id: str) -> None:
        """Sets `dispatch_status='FAILED'` — `printer.py`'s own retries are
        already exhausted by the time this is called; this does not retry,
        it records that manual/staff intervention is needed. See
        `list_undispatched_confirmed_orders`."""

    @abstractmethod
    def list_undispatched_confirmed_orders(self, store_id: str) -> list[ConfirmedOrder]:
        """Every confirmed order for this tenant with `dispatch_status !=
        'DISPATCHED'` (PENDING or FAILED), oldest first — the staff/ops
        visibility query T-049 FINAL requires: an order that never reached
        the kitchen must be findable, not merely non-crashing."""

    # -- customer identity ----------------------------------------------------

    @abstractmethod
    def get_or_create_customer(self, store_id: str, phone_normalized: str) -> str:
        """Returns the tenant-scoped `customer_id` for this normalized phone
        number, creating one if this is the first time this tenant has seen
        it. Never the raw phone number itself — see `customers.py`."""

    @abstractmethod
    def delete_customer(self, store_id: str, customer_id: str) -> None:
        """Erasure path. Deletes the customer row and any in-flight sessions
        still referencing it. Confirmed orders are NOT deleted (they are the
        restaurant's own financial/audit record) — instead their
        `customer_id` is set to None, severing the identity link while
        leaving the transaction itself intact. See ADR-014 privacy section
        for why this is the chosen tradeoff, not silent data loss."""
