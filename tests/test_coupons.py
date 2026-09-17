"""
Coupons live in PrISM as codes; staff key them. Our quote must match what the
register produces, so these lock the arithmetic AND the order of operations.

The discount-before-tax assumption is not yet verified against the POS —
see docs/COUPON-VERIFICATION.md. If those three checks come back different,
these expected values change and pricing.taxable_base moves.
"""

import pytest

from lakewood import orders as oe
from lakewood.menu import NON_PIZZA
from lakewood.orders import (
    add_item, add_modifier, apply_coupon, remove_coupon, request_quote,
    set_order_type,
)


@pytest.fixture
def pickup():
    s = oe.Session(call_id="C", store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


def test_three_off_thirty(pickup):
    add_item(pickup, "CHEESE PIZZA", size="party")          # $26
    add_item(pickup, "6PC WINGS")                           # $8  -> $34
    r = apply_coupon(pickup, "OFF_3_AT_30")
    assert r["discount"] == "3.00"
    q = request_quote(pickup)
    assert q["subtotal"] == "34.00" and q["discount"] == "3.00"
    assert q["tax"] == "2.28"        # 7.35% of 31.00, not of 34.00
    assert q["total"] == "33.28"


def test_three_off_thirty_not_offered_below_threshold(pickup):
    add_item(pickup, "CHEESE PIZZA", size="large")          # $15
    assert apply_coupon(pickup, "OFF_3_AT_30")["code"] == "COUPON_NOT_APPLICABLE"


def test_five_off_fifty_beats_three_off_thirty(pickup):
    add_item(pickup, "CHEESE PIZZA", size="party", quantity=2)   # $52
    r = apply_coupon(pickup)                                     # let engine pick
    assert r["coupon"] == "OFF_5_AT_50" and r["discount"] == "5.00"


def test_free_two_liter_with_two_large(pickup):
    add_item(pickup, "CHEESE PIZZA", size="large", quantity=2)   # $30
    add_item(pickup, "2LITER")                                   # $4  -> $34
    r = apply_coupon(pickup, "FREE_2L")
    assert r["discount"] == "4.00"
    assert request_quote(pickup)["subtotal"] == "34.00"


def test_combo_27(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(pickup, lid, "pepperoni")                  # 15 + 3 = 18
    add_item(pickup, "6PC WINGS")                           # +8  = 26
    add_item(pickup, "2LITER")                              # +4  = 30
    r = apply_coupon(pickup, "COMBO_27")
    assert r["discount"] == "3.00"                          # 30 -> 27
    q = request_quote(pickup)
    assert q["total"] == "28.98"                            # 27 + 7.35%


def test_combo_27_rejected_without_the_soda(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(pickup, lid, "pepperoni")
    add_item(pickup, "6PC WINGS")
    assert apply_coupon(pickup, "COMBO_27")["code"] == "COUPON_NOT_APPLICABLE"


def test_coupons_never_stack(pickup):
    """Printed terms: cannot be combined. A second apply replaces the first."""
    add_item(pickup, "CHEESE PIZZA", size="party", quantity=2)
    apply_coupon(pickup, "OFF_5_AT_50")
    apply_coupon(pickup, "OFF_3_AT_30")
    assert pickup.order.coupon_code == "OFF_3_AT_30"
    assert request_quote(pickup)["discount"] == "3.00"


def test_coupon_invalidates_the_quote(pickup):
    add_item(pickup, "CHEESE PIZZA", size="party")
    add_item(pickup, "6PC WINGS")
    request_quote(pickup)
    apply_coupon(pickup, "OFF_3_AT_30")
    assert pickup.quote_id is None, "F4: a discount changes the total"


def test_removing_a_coupon_restores_the_total(pickup):
    add_item(pickup, "CHEESE PIZZA", size="party")
    add_item(pickup, "6PC WINGS")
    apply_coupon(pickup, "OFF_3_AT_30")
    remove_coupon(pickup)
    q = request_quote(pickup)
    assert q["discount"] == "0.00" and q["total"] == "36.50"


def test_unknown_code_discounts_nothing(pickup):
    add_item(pickup, "CHEESE PIZZA", size="party")
    assert apply_coupon(pickup, "HALF_OFF_EVERYTHING")["code"] \
        == "COUPON_NOT_APPLICABLE"
    assert pickup.order.coupon_discount == 0


def test_coupon_appears_on_the_ticket(pickup):
    from lakewood.printer import TicketPrinter
    from lakewood.orders import begin_confirmation, confirm_order
    add_item(pickup, "CHEESE PIZZA", size="party")
    add_item(pickup, "6PC WINGS")
    apply_coupon(pickup, "OFF_3_AT_30")
    q = request_quote(pickup)
    begin_confirmation(pickup)
    confirm_order(pickup, q["quote_id"], idempotency_key="k")
    p = TicketPrinter(dry_run=True)
    payload = p.build(pickup.order.quote() and "", "carry-out", "AI-1",
                      total=q["total"], coupon="OFF_3_AT_30", discount="3.00")
    assert b"KEY THIS CODE IN PRISM" in payload
