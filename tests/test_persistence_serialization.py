"""
`session_to_dict`/`session_from_dict` round-trip. Offline, no repository
involved — pure dict conversion, run first because everything else in the
persistence package depends on it not lying.
"""

import dataclasses

import pytest

from lakewood import orders as oe
from lakewood.orders import (
    add_item, add_modifier, apply_coupon, begin_confirmation, request_quote,
    set_order_type,
)
from lakewood.persistence.serialization import session_from_dict, session_to_dict


@pytest.fixture
def sess():
    s = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


def test_round_trip_preserves_every_dataclass_field(sess):
    """If a future `Session` field is added and forgotten in
    `serialization.py`, this must fail — not silently drop a fail-safe
    across a reload."""
    add_item(sess, "PIZZA", size="LARGE")
    d = session_to_dict(sess)
    restored = session_from_dict(d)
    for f in dataclasses.fields(oe.Session):
        assert getattr(restored, f.name) == getattr(sess, f.name), f.name


def test_round_trip_preserves_cart_and_price(sess):
    r = add_item(sess, "PIZZA", size="LARGE")
    add_modifier(sess, r["line_id"], "pepperoni")
    restored = session_from_dict(session_to_dict(sess))
    assert restored.order.subtotal() == sess.order.subtotal()
    assert restored.order.total() == sess.order.total()
    assert len(restored.order.lines) == 1


def test_line_identity_preserved_across_reload(sess):
    """`sess.lines[lid]` and the matching entry in `sess.order.lines` must be
    the SAME object after reload — `update_item`/`remove_modifier` mutate
    through `sess.lines`, `Order.subtotal()` reads through `sess.order.lines`.
    Two independent copies would silently drift after the first mutation."""
    r = add_item(sess, "PIZZA", size="LARGE")
    restored = session_from_dict(session_to_dict(sess))
    line_via_dict = restored.lines[r["line_id"]]
    line_via_order = restored.order.lines[0]
    assert line_via_dict is line_via_order
    add_modifier(restored, r["line_id"], "mushrooms")
    assert len(line_via_order.toppings) == 1, \
        "mutating via sess.lines must be visible through sess.order.lines"


def test_round_trip_preserves_f14_confirmation_turn(sess):
    r = add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    begin_confirmation(sess)
    assert sess.confirmation_turn == sess.turn
    restored = session_from_dict(session_to_dict(sess))
    assert restored.confirmation_turn == sess.confirmation_turn
    assert restored.turn == sess.turn
    assert restored.quote_id == sess.quote_id
    assert restored.quote_hash == sess.quote_hash


def test_round_trip_preserves_f15_unresolved_lookups(sess):
    sess.unresolved_lookups.append("root beer")
    restored = session_from_dict(session_to_dict(sess))
    assert restored.unresolved_lookups == ["root beer"]


def test_round_trip_preserves_f17_pending_disambiguations(sess):
    from lakewood.orders import _register_disambiguation
    hits = [{"kind": "topping", "name": "PEPPERS"}, {"kind": "topping", "name": "JALAPENO"}]
    _register_disambiguation(sess, "peppers", hits)
    restored = session_from_dict(session_to_dict(sess))
    assert len(restored.pending_disambiguations) == 1
    entry = restored.pending_disambiguations[0]
    assert entry["query"] == "peppers"
    assert entry["ask_count"] == 0
    assert entry["candidates"] == hits
    assert entry["key"] == frozenset(("topping", h["name"], None) for h in hits)


def test_round_trip_preserves_coupon(sess):
    add_item(sess, "PIZZA", size="LARGE")
    add_item(sess, "PIZZA", size="LARGE")           # $30 subtotal — meets OFF_3_AT_30's minimum
    applied = apply_coupon(sess, "OFF_3_AT_30")
    assert applied["status"] == "ok"
    restored = session_from_dict(session_to_dict(sess))
    assert restored.order.coupon_code == sess.order.coupon_code == "OFF_3_AT_30"
    assert restored.order.coupon_discount == sess.order.coupon_discount == 300


def test_round_trip_preserves_disclosure_played_at(sess):
    """T-057: a non-None value must actually survive the round trip —
    `test_round_trip_preserves_every_dataclass_field` alone can't prove
    this, since it stays None in that fixture either way."""
    oe.mark_disclosure_played(sess)
    assert sess.disclosure_played_at is not None
    restored = session_from_dict(session_to_dict(sess))
    assert restored.disclosure_played_at == sess.disclosure_played_at


def test_from_dict_rejects_unknown_version(sess):
    d = session_to_dict(sess)
    d["_version"] = 999
    with pytest.raises(ValueError):
        session_from_dict(d)
