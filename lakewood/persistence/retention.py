"""
Retention and deletion — ADR-014's privacy section, implemented.

Three different lifetimes, on purpose, never conflated at a call site:

- **In-flight sessions** (`SESSION_RETENTION_SECONDS`, 24 hours): an
  abandoned cart nobody ever confirmed or explicitly cancelled. Kept briefly
  past the 30-minute resume window (`recovery.RESUME_WINDOW_SECONDS`) in case
  of a same-day dispute ("I called and it never went through"), then purged.
  Contains a phone number and whatever the customer was ordering — the most
  sensitive, least-justified-to-keep data this system holds, so it gets the
  shortest life.
- **Confirmed orders** (`CONFIRMED_ORDER_RETENTION_DAYS`, 400 days): the
  restaurant's own financial/audit record. Kept a little over a year —
  enough for a same-time-last-year comparison and any ordinary dispute
  window. If the restaurant's actual bookkeeping/legal requirement differs,
  that's an owner question this task doesn't have standing to answer alone
  (same pattern as T-003's coupon-tax-ordering owner action) — flagged in
  ADR-014, not guessed at here.
- **Customer identity** (no automatic expiry): the phone -> `customer_id`
  mapping lives as long as the customer keeps calling. Removed only on
  request, via `delete_customer`.

Nothing here runs on a schedule yet — there is no cron/task-runner in this
codebase (the event store and any background-job infrastructure are later
phases, explicitly out of scope for T-037). These are the deterministic
functions a future scheduled sweep calls; calling them is this task's job,
scheduling them is not.
"""

from __future__ import annotations

import time

from .repository import SessionRepository

SESSION_RETENTION_SECONDS = 24 * 60 * 60
CONFIRMED_ORDER_RETENTION_DAYS = 400
CONFIRMED_ORDER_RETENTION_SECONDS = CONFIRMED_ORDER_RETENTION_DAYS * 24 * 60 * 60


def sweep_expired_sessions(repo: SessionRepository, store_id: str,
                           now: float | None = None) -> int:
    now = now if now is not None else time.time()
    return repo.purge_expired_sessions(store_id, now - SESSION_RETENTION_SECONDS)


def sweep_old_confirmed_orders(repo: SessionRepository, store_id: str,
                               now: float | None = None) -> int:
    now = now if now is not None else time.time()
    return repo.purge_confirmed_orders(store_id, now - CONFIRMED_ORDER_RETENTION_SECONDS)


def erase_customer(repo: SessionRepository, store_id: str, customer_id: str) -> None:
    """The deletion path: removes the customer's identity record and any
    in-flight session still tied to it. Confirmed orders are NOT deleted —
    `delete_customer` (see `repository.py`) severs the `customer_id` link on
    those rows instead of destroying the transaction record itself."""
    repo.delete_customer(store_id, customer_id)
