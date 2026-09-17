"""
The recovery story — ADR-014's product decision, implemented.

**Decision (ADR-014): offer resume, never silently continue.** A dropped
call's cart is a proposal the next call gets to accept or discard, the same
posture the memory policy in CLAUDE.md takes toward anything remembered
about a customer ("every remembered thing is a proposal, re-validated and
re-priced before it is quoted"). This module finds the candidate and
revalidates it; it does not decide FOR the customer that they want it back,
and it never returns something already re-priced-and-silently-trusted.

**Resume window: `RESUME_WINDOW_SECONDS` (30 minutes).** Long enough for a
customer whose call actually dropped to call back without re-ordering from
scratch; short enough that a genuinely abandoned call from hours earlier
doesn't resurface as a stale surprise. Past the window, `find_resumable_session`
returns None — not because the row is gone (retention is a separate, longer
policy, see `retention.py`), but because re-offering a forgotten order after
a long silence is more confusing than helpful.

**Always re-validate AND always re-price, unconditionally.** `cart_hash` (F5)
hashes cart *contents* (sizes, toppings, quantities) — not the computed
price. If the menu changed between the drop and the callback, the hash is
unchanged even though `Order.total()` now returns a different number. A
stale, still-"valid"-looking `quote_id` is exactly the silent-wrong-price
failure CLAUDE.md's priority order puts at P0. `revalidate_and_reprice`
therefore invalidates any outstanding quote unconditionally on every resume,
regardless of whether anything actually changed — the customer hears a fresh
`request_quote` readback either way, never a cached number.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..menu import NON_PIZZA
from ..orders import (
    ALL_TOPPINGS, Session, TERMINAL, UNAVAILABLE, _gourmet_names,
)
from ..pricing import PizzaLine, SIZES, SimpleLine
from .repository import SessionRepository

RESUME_WINDOW_SECONDS = 30 * 60


@dataclass
class RevalidationResult:
    session: Session
    issues: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.issues


def find_resumable_session(repo: SessionRepository, store_id: str, from_number: str,
                            now: float | None = None) -> Session | None:
    """None if there's nothing to resume, or the candidate is outside the
    resume window. Does NOT revalidate — call `revalidate_and_reprice` on
    whatever this returns before offering or quoting anything from it."""
    now = now if now is not None else time.time()
    sess = repo.find_active_session_by_phone(store_id, from_number)
    if sess is None:
        return None
    last_updated = repo.session_last_updated(store_id, sess.call_id)
    if last_updated is None or now - last_updated > RESUME_WINDOW_SECONDS:
        return None
    return sess


def _revalidate_line(line, issues: list[str]) -> None:
    if isinstance(line, PizzaLine):
        if line.size not in SIZES:
            issues.append(f"{line.size} is no longer a size we offer.")
            return
        names = _gourmet_names(line.size)
        numbers = line.half_and_half if line.half_and_half else (
            (line.gourmet,) if line.gourmet is not None else ())
        for n in numbers:
            if not isinstance(n, int):
                continue
            if n not in names:
                issues.append(f"#{n} is no longer on the menu.")
            elif f"#{n}" in UNAVAILABLE:
                issues.append(f"#{n} {names[n]} isn't available right now.")
        for t in line.toppings:
            if t.name in UNAVAILABLE:
                issues.append(f"{t.name} isn't available right now.")
                continue
            if t.name not in ALL_TOPPINGS:
                issues.append(f"{t.name} is no longer on the menu.")
                continue
            try:
                t.tier_rate(line.size)
            except ValueError as e:
                issues.append(str(e))
    elif isinstance(line, SimpleLine):
        if line.name in UNAVAILABLE:
            issues.append(f"{line.name} isn't available right now.")
        elif line.name not in NON_PIZZA:
            issues.append(f"{line.name} is no longer on the menu.")
        elif NON_PIZZA[line.name] != line.unit_price:
            issues.append(
                f"{line.name}'s price has changed since this order was started.")


def revalidate_and_reprice(session: Session) -> RevalidationResult:
    """Mutates `session` in place (mirrors every `orders.py` tool's own
    pattern of mutating the `Session` it's given) and returns it alongside
    any issues found. Never removes a line itself — F7's "never guess"
    applies here too: flag it, let the conversation layer ask the customer
    what they'd like to do, exactly like a live `add_item` refusal would."""
    issues: list[str] = []
    if session.state not in TERMINAL:
        for line in session.lines.values():
            _revalidate_line(line, issues)
        # Unconditional: a cart_hash match only proves contents didn't
        # change, never that today's price is the same one quoted before.
        session.invalidate_quote()
        if session.state in ("QUOTED", "AWAITING_CONFIRMATION"):
            session.to("BUILDING")
    return RevalidationResult(session=session, issues=issues)
