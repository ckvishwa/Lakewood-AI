"""
Session <-> plain-dict conversion, for repositories to store as JSON.

Deliberately hand-written field-by-field rather than a generic
`dataclasses.asdict`/`pickle`: `orders.py` FAIL-SAFES (F5, F14, F15, F17) live
in specific `Session` fields, and a generic serializer would silently keep
working even if a future field were added and forgotten here. Explicit lists
below mean a new `Session` field that isn't wired into `session_to_dict`
raises no error but also isn't persisted — the round-trip test
(`tests/test_persistence_serialization.py`) enumerates every current
dataclass field and fails if one is missing from either direction, so the gap
is caught immediately rather than silently losing a fail-safe across a reload.

Line identity: `Session.lines[line_id]` and the matching entry in
`Session.order.lines` are, in memory, the SAME object (see `orders.py::add_item`
— both point at one `PizzaLine`/`SimpleLine` instance). `update_item` and
`remove_modifier` mutate through `sess.lines[...]`; `Order.subtotal()` reads
through `sess.order.lines`. Reconstructing two independent copies would let
them drift after the first mutation post-reload. `_session_from_dict` rebuilds
each line exactly once and reuses the same object for both containers,
preserving that identity across the persistence boundary.
"""

from __future__ import annotations

from ..orders import Session
from ..pricing import Order, PizzaLine, SimpleLine, Topping

SERIALIZATION_VERSION = 1


def _topping_to_dict(t: Topping) -> dict:
    return {"name": t.name, "portion": t.portion, "qty": t.qty,
            "removed": t.removed, "lite": t.lite}


def _topping_from_dict(d: dict) -> Topping:
    return Topping(name=d["name"], portion=d["portion"], qty=d["qty"],
                   removed=d["removed"], lite=d["lite"])


def _line_to_dict(line) -> dict:
    if isinstance(line, PizzaLine):
        return {
            "kind": "pizza",
            "size": line.size,
            "gourmet": line.gourmet,
            "half_and_half": list(line.half_and_half) if line.half_and_half else None,
            "toppings": [_topping_to_dict(t) for t in line.toppings],
            "quantity": line.quantity,
        }
    if isinstance(line, SimpleLine):
        return {"kind": "simple", "name": line.name,
                "unit_price": line.unit_price, "quantity": line.quantity}
    raise TypeError(f"unknown line type: {type(line)!r}")


def _line_from_dict(d: dict):
    if d["kind"] == "pizza":
        hh = tuple(d["half_and_half"]) if d["half_and_half"] is not None else None
        return PizzaLine(
            size=d["size"], gourmet=d["gourmet"], half_and_half=hh,
            toppings=[_topping_from_dict(t) for t in d["toppings"]],
            quantity=d["quantity"],
        )
    if d["kind"] == "simple":
        return SimpleLine(name=d["name"], unit_price=d["unit_price"],
                          quantity=d["quantity"])
    raise ValueError(f"unknown serialized line kind: {d['kind']!r}")


def session_to_dict(sess: Session) -> dict:
    """Pure function — never mutates `sess`. Produces a JSON-safe dict."""
    line_ids = list(sess.lines.keys())
    lines_by_id = {lid: _line_to_dict(sess.lines[lid]) for lid in line_ids}
    return {
        "_version": SERIALIZATION_VERSION,
        "call_id": sess.call_id,
        "store_id": sess.store_id,                # F2 — see repository.py guard
        "from_number": sess.from_number,
        "started_at": sess.started_at,
        "state": sess.state,
        "order_id": sess.order_id,
        "order": {
            "order_type": sess.order.order_type,
            "line_ids": line_ids,                  # order.lines reconstructed from this
            "coupon_code": sess.order.coupon_code,
            "coupon_discount": sess.order.coupon_discount,
        },
        "lines": lines_by_id,
        "_next_line": sess._next_line,
        "customer_name": sess.customer_name,
        "address": sess.address,
        "delivery_note": sess.delivery_note,
        "quote_id": sess.quote_id,
        "quote_hash": sess.quote_hash,
        "quote_at": sess.quote_at,
        "scheduled_for": sess.scheduled_for,
        "disclosure_played_at": sess.disclosure_played_at,
        "parse_failures": sess.parse_failures,
        "transfer_reason": sess.transfer_reason,
        "idempotency": dict(sess.idempotency),
        "events": list(sess.events),
        "turn": sess.turn,
        "confirmation_turn": sess.confirmation_turn,           # F14
        "unresolved_lookups": list(sess.unresolved_lookups),   # F15
        "pending_disambiguations": [                            # F17
            {"query": e["query"], "candidates": list(e["candidates"]),
             "key": sorted(e["key"]) if isinstance(e["key"], (set, frozenset)) else e["key"],
             "ask_count": e["ask_count"]}
            for e in sess.pending_disambiguations
        ],
    }


def session_from_dict(d: dict) -> Session:
    """Inverse of `session_to_dict`. Raises on an unknown `_version` rather
    than guessing at a shape it wasn't tested against (F7's own "never guess"
    rule, applied to our own stored data instead of a customer's words)."""
    version = d.get("_version")
    if version != SERIALIZATION_VERSION:
        raise ValueError(
            f"cannot load session: serialized with version {version!r}, "
            f"this build expects {SERIALIZATION_VERSION!r}")

    lines_by_id = {lid: _line_from_dict(ld) for lid, ld in d["lines"].items()}
    order_line_ids = d["order"]["line_ids"]

    sess = Session(
        call_id=d["call_id"],
        store_id=d["store_id"],
        from_number=d["from_number"],
        started_at=d["started_at"],
    )
    sess.state = d["state"]
    sess.order_id = d["order_id"]
    sess.order = Order(
        order_type=d["order"]["order_type"],
        lines=[lines_by_id[lid] for lid in order_line_ids],   # same objects, preserved order
        coupon_code=d["order"]["coupon_code"],
        coupon_discount=d["order"]["coupon_discount"],
    )
    sess.lines = lines_by_id
    sess._next_line = d["_next_line"]
    sess.customer_name = d["customer_name"]
    sess.address = d["address"]
    sess.delivery_note = d["delivery_note"]
    sess.quote_id = d["quote_id"]
    sess.quote_hash = d["quote_hash"]
    sess.quote_at = d["quote_at"]
    sess.scheduled_for = d["scheduled_for"]
    sess.disclosure_played_at = d.get("disclosure_played_at")
    sess.parse_failures = d["parse_failures"]
    sess.transfer_reason = d["transfer_reason"]
    sess.idempotency = dict(d["idempotency"])
    sess.events = list(d["events"])
    sess.turn = d["turn"]
    sess.confirmation_turn = d["confirmation_turn"]
    sess.unresolved_lookups = list(d["unresolved_lookups"])
    sess.pending_disambiguations = [
        {"query": e["query"], "candidates": list(e["candidates"]),
         "key": frozenset(tuple(x) if isinstance(x, list) else x for x in e["key"]),
         "ask_count": e["ask_count"]}
        for e in d["pending_disambiguations"]
    ]
    return sess
