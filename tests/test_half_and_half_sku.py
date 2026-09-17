"""
T-008: the HALF_AND_HALF SKU pricing mode (lakewood/pricing.py — two named
specialties, larger-half-only price, owner-confirmed specialty/specialty
only) is implemented and verified in tests/test_pricing.py and
tests/test_pricing_parity.py, but until this task no tool in `orders.TOOLS`
could ever construct one. `add_item(second_gourmet_number=...)` is the new,
minimal, structured entry point — no free-form text parsing, no invented
pricing rule, no tool accepts a price.
"""

import pytest

from lakewood import orders as oe
from lakewood.orders import (
    UNAVAILABLE, add_item, add_modifier, request_quote, set_order_type,
)


@pytest.fixture
def pickup():
    s = oe.Session(call_id="C", store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


def test_two_specialty_half_and_half_reaches_correct_price(pickup):
    """#10 Hawaiian / #8 BBQ Chicken, small — flat gourmet price, matches the
    already-verified 'SM half&half' case in tests/test_pricing_parity.py."""
    r = add_item(pickup, "PIZZA", size="small",
                gourmet_number=10, second_gourmet_number=8)
    assert r["status"] == "ok"
    assert request_quote(pickup)["subtotal"] == "16.00"


def test_final_cart_records_the_half_and_half_sku_mode(pickup):
    """The structured state, not inferred text, is what marks this SKU."""
    add_item(pickup, "PIZZA", size="large",
             gourmet_number=10, second_gourmet_number=8)
    line = pickup.order.lines[0]
    assert line.half_and_half == (10, 8)
    assert line.gourmet is None


def test_larger_specialty_side_pricing_matches_verified_test(pickup):
    """Reuses the exact verified pair from test_pricing_parity — large,
    flat gourmet price regardless of which two specialties."""
    add_item(pickup, "PIZZA", size="large",
             gourmet_number=10, second_gourmet_number=8)
    assert request_quote(pickup)["subtotal"] == "23.00"


def test_swapping_the_two_halves_does_not_change_total(pickup):
    a = oe.Session(call_id="A", store_id="STORE-001", from_number="+12035551234")
    set_order_type(a, "pickup")
    add_item(a, "PIZZA", size="large", gourmet_number=10, second_gourmet_number=8)

    b = oe.Session(call_id="B", store_id="STORE-001", from_number="+12035551234")
    set_order_type(b, "pickup")
    add_item(b, "PIZZA", size="large", gourmet_number=8, second_gourmet_number=10)

    assert request_quote(a)["subtotal"] == request_quote(b)["subtotal"]


def test_invalid_second_specialty_fails_closed(pickup):
    r = add_item(pickup, "PIZZA", size="large",
                gourmet_number=10, second_gourmet_number=99)
    assert r["status"] == "error"
    assert r["code"] == "GOURMET_NOT_FOUND"
    assert pickup.order.lines == []


def test_invalid_first_specialty_fails_closed(pickup):
    r = add_item(pickup, "PIZZA", size="large",
                gourmet_number=99, second_gourmet_number=10)
    assert r["status"] == "error"
    assert r["code"] == "GOURMET_NOT_FOUND"
    assert pickup.order.lines == []


def test_missing_first_half_is_rejected_not_silently_dropped(pickup):
    """second_gourmet_number alone, with no first half, is a caller mistake —
    must fail loud, never silently become a single-specialty pizza."""
    r = add_item(pickup, "PIZZA", size="large", second_gourmet_number=8)
    assert r["status"] == "error"
    assert r["code"] == "BAD_HALF_AND_HALF"
    assert pickup.order.lines == []


def test_unsupported_size_fails_closed(pickup):
    r = add_item(pickup, "PIZZA", size="not-a-size",
                gourmet_number=10, second_gourmet_number=8)
    assert r["status"] == "error"
    assert r["code"] == "SIZE_NOT_FOUND"
    assert pickup.order.lines == []


def test_86d_specialty_on_either_half_fails_closed(pickup):
    UNAVAILABLE.add("#8")
    try:
        r = add_item(pickup, "PIZZA", size="large",
                    gourmet_number=10, second_gourmet_number=8)
        assert r["status"] == "error"
        assert r["code"] == "UNAVAILABLE"
        assert pickup.order.lines == []
    finally:
        UNAVAILABLE.discard("#8")


def test_additive_half_and_half_is_unchanged(pickup):
    """Existing per-topping HALF_1/HALF_2 path (single gourmet or plain
    cheese base) does not go through half_and_half at all — untouched."""
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(pickup, lid, "pepperoni", "HALF_1")
    add_modifier(pickup, lid, "meatball", "HALF_2")
    line = pickup.order.lines[0]
    assert line.half_and_half is None
    assert request_quote(pickup)["subtotal"] == "21.00"


def test_cart_mutation_after_half_and_half_invalidates_quote(pickup):
    add_item(pickup, "PIZZA", size="large", gourmet_number=10, second_gourmet_number=8)
    q = request_quote(pickup)
    assert q["status"] == "ok"
    add_item(pickup, "6PC WINGS")
    assert pickup.state == "BUILDING"
    assert pickup.quote_id is None


def test_readback_names_both_specialties(pickup):
    r = add_item(pickup, "PIZZA", size="large", gourmet_number=10, second_gourmet_number=8)
    assert "HAWAIIAN" in r["description"]
    assert "BBQ CHICKEN" in r["description"]
