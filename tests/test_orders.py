"""
Order-engine tests: the PRD's ten acceptance cases plus the fail-safes.

The fail-safe tests matter more than the happy paths. Anything that lets a wrong
total reach a customer, or an order vanish, is a product-killing bug.
"""

import pytest

from lakewood import orders as oe
from lakewood.orders import (
    UNAVAILABLE, add_item, add_modifier, begin_confirmation, cancel_order,
    confirm_order, mark_disclosure_played, remove_item, remove_modifier,
    request_quote, screen_utterance, set_order_type, transfer_to_human,
    update_item,
)


@pytest.fixture
def sess():
    return oe.Session(call_id="C1", store_id="STORE-001",
                      from_number="+12035551234")


@pytest.fixture
def pickup(sess):
    set_order_type(sess, "pickup")
    return sess


# --- PRD §18 acceptance ----------------------------------------------------

def test_t1_one_large_cheese(pickup):
    assert add_item(pickup, "CHEESE PIZZA", size="large")["status"] == "ok"
    assert request_quote(pickup)["total"] == "16.10"


def test_t2_half_pepperoni_half_mushroom(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(pickup, lid, "pepperoni", "HALF_1")
    add_modifier(pickup, lid, "mushroom", "HALF_2")
    assert request_quote(pickup)["subtotal"] == "21.00"


def test_t3_extra_cheese_scoped_to_one_half(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(pickup, lid, "pepperoni", "HALF_1")
    add_modifier(pickup, lid, "mushroom", "HALF_2")
    request_quote(pickup)
    add_modifier(pickup, lid, "extra cheese", "HALF_1")
    assert pickup.quote_id is None, "F4: mutation must invalidate the quote"
    assert request_quote(pickup)["subtotal"] == "24.00"


def test_t4_retract_and_replace(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(pickup, lid, "pepperoni", "HALF_1")
    add_modifier(pickup, lid, "mushroom", "HALF_2")
    add_modifier(pickup, lid, "extra cheese", "HALF_1")
    remove_modifier(pickup, lid, "mushroom", "HALF_2")
    add_modifier(pickup, lid, "sausage", "HALF_2")
    assert request_quote(pickup)["subtotal"] == "24.00"


def test_t5_make_that_two(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(pickup, lid, "pepperoni", "HALF_1")
    add_modifier(pickup, lid, "sausage", "HALF_2")
    update_item(pickup, lid, quantity=2)
    assert request_quote(pickup)["subtotal"] == "42.00"


def test_t6_cancel_first_pizza(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    r = remove_item(pickup, lid)
    assert r["status"] == "ok" and r["remaining"] == 0
    assert request_quote(pickup)["code"] == "EMPTY_CART"


def test_t7_total_comes_from_backend(pickup):
    add_item(pickup, "CHEESE PIZZA", size="medium")
    add_item(pickup, "6PC WINGS")
    assert request_quote(pickup)["total"] == "22.54"


def test_t8_pickup_to_delivery(pickup):
    add_item(pickup, "CHEESE PIZZA", size="medium")
    add_item(pickup, "6PC WINGS")
    assert set_order_type(pickup, "delivery")["code"] == "ADDRESS_REQUIRED"
    set_order_type(pickup, "delivery", address="3 Vine St")
    q = request_quote(pickup)
    assert q["service_charge"] == "2.00" and q["total"] == "24.54"


def test_t9_unavailable_topping_refused(pickup):
    UNAVAILABLE.add("CLAMS")
    try:
        lid = add_item(pickup, "CHEESE PIZZA", size="small")["line_id"]
        assert add_modifier(pickup, lid, "clams")["code"] == "UNAVAILABLE"
    finally:
        UNAVAILABLE.discard("CLAMS")


def test_t10_human_request_transfers(sess):
    r = transfer_to_human(sess, "customer_request")
    assert r["action"] == "transfer" and sess.state == "TRANSFERRED"


# --- fail-safes ------------------------------------------------------------

def test_f4_mutation_after_quote_returns_to_building(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    request_quote(pickup)
    begin_confirmation(pickup)
    add_modifier(pickup, lid, "pepperoni")
    assert pickup.state == "BUILDING"


def test_f5_stale_quote_cannot_confirm(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    q = request_quote(pickup)
    begin_confirmation(pickup)
    add_modifier(pickup, lid, "pepperoni")
    assert confirm_order(pickup, q["quote_id"])["code"] in ("BAD_STATE", "STALE_QUOTE")


def test_f6_confirm_is_idempotent(pickup):
    add_item(pickup, "CHEESE PIZZA", size="large")
    q = request_quote(pickup)
    begin_confirmation(pickup)
    pickup.turn += 1  # T-019/F14: confirm_order requires a separate turn
    a = confirm_order(pickup, q["quote_id"], idempotency_key="k1")
    assert a["status"] == "ok", a
    b = confirm_order(pickup, q["quote_id"], idempotency_key="k1")
    assert a == b, "a dropped call mid-confirm must not create a second order"


def test_f12_confirmed_order_is_immutable(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    q = request_quote(pickup)
    begin_confirmation(pickup)
    pickup.turn += 1  # T-019/F14: confirm_order requires a separate turn
    confirm_order(pickup, q["quote_id"], idempotency_key="k")
    assert add_modifier(pickup, lid, "ham")["code"] == "ORDER_CLOSED"


def test_f8_card_number_transfers_and_is_not_stored(sess):
    r = screen_utterance(sess, "my card is 4111 1111 1111 1111")
    assert r["action"] == "transfer" and r["reason"] == "payment_card"
    assert not any("4111" in str(e) for e in sess.events)


@pytest.mark.parametrize("utterance", [
    "my son has a peanut allergy",
    "is this gluten free",
    "she's lactose intolerant",
])
def test_f9_allergy_always_transfers(sess, utterance):
    assert screen_utterance(sess, utterance)["reason"] == "allergy"


# --- T-057: call-recording disclosure (docs/SECURITY_AUDIT_T054.md,
# finding T054-04 — no disclosure/consent mechanism existed anywhere) -----

def test_mark_disclosure_played_sets_a_timestamp(sess):
    assert sess.disclosure_played_at is None
    r = mark_disclosure_played(sess)
    assert r["status"] == "ok"
    assert sess.disclosure_played_at is not None
    assert r["disclosure_played_at"] == sess.disclosure_played_at


def test_mark_disclosure_played_is_idempotent(sess):
    mark_disclosure_played(sess)
    first = sess.disclosure_played_at
    mark_disclosure_played(sess)
    assert sess.disclosure_played_at == first  # never re-timestamped


def test_f7_unknown_topping_returns_candidates_not_a_guess(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    r = add_modifier(pickup, lid, "pineapple juice")
    assert r["code"] == "TOPPING_NOT_FOUND" and "candidates" in r


def test_f7_unpriced_button_refuses(pickup, monkeypatch):
    """All tiers are confirmed now; inject one to keep the guard under test."""
    monkeypatch.setattr(oe, "UNKNOWN_TIER", {"TRUFFLE OIL"})
    monkeypatch.setattr(oe, "ALL_TOPPINGS", oe.ALL_TOPPINGS | {"TRUFFLE OIL"})
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    assert add_modifier(pickup, lid, "TRUFFLE OIL")["code"] == "PRICE_UNKNOWN"


def test_shrimp_is_now_orderable(pickup):
    """Was PRICE_UNKNOWN until the owner confirmed it as premium."""
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    assert add_modifier(pickup, lid, "SHRIMP")["status"] == "ok"


def test_topping_removal_is_free(pickup):
    """"No pineapple on the Hawaiian" costs nothing and gives no credit."""
    lid = add_item(pickup, "PIZZA", size="medium", gourmet_number=10)["line_id"]
    assert add_modifier(pickup, lid, "pineapple",
                        intensity="NONE")["status"] == "ok"


def test_f10_two_parse_failures_transfer(sess):
    sess.note_parse_failure()
    assert (sess.note_parse_failure() or {}).get("action") == "transfer"


def test_f2_no_tool_accepts_store_id():
    import inspect
    for fn in oe.TOOLS:
        params = set(inspect.signature(fn).parameters)
        assert "store_id" not in params, f"{fn.__name__} exposes store_id"


def test_f1_no_tool_accepts_a_price():
    import inspect
    for fn in oe.TOOLS:
        for p in inspect.signature(fn).parameters:
            assert p not in ("price", "total", "amount", "subtotal", "discount"), \
                f"{fn.__name__} exposes a writable price"
