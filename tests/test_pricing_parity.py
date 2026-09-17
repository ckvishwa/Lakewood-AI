"""
PrISM menu + pricing parity suite — the release gate for the domain engine.

Every case is tagged with its provenance:

  PHOTOGRAPHED — the exact total was read off a real PrISM receipt/screenshot.
                 These numbers are ground truth; never adjust them to match
                 the code. They already live in tests/test_pricing.py,
                 tests/test_orders.py, and tests/test_coupons.py — this file
                 imports/reproduces the same literal values and organizes
                 them into the six required parity categories.

  COMPOSED      — built by combining independently-PHOTOGRAPHED item prices
                 (single toppings, single pizzas, single non-pizza items)
                 through a rule that is ITSELF verified by other
                 PHOTOGRAPHED cases in this repo: the linear sum-of-lines
                 subtotal, the integer half-up tax formula, and the flat
                 untaxed $2 delivery charge. No case in this file invents or
                 guesses a pricing rule; COMPOSED cases only exercise
                 verified rules against new (but real-menu) combinations.

  UNVERIFIED    — the coupon category. Discount *amounts* are printed on the
                 menu and confirmed (see tests/test_coupons.py), but WHICH
                 side of the discount the tax applies to has not been
                 confirmed at the register — see docs/COUPON-VERIFICATION.md
                 and docs/OPEN-QUESTIONS.md item 2. These cases assert the
                 currently-assumed rule (tax on the discounted subtotal) so a
                 future accidental change is caught, but the category as a
                 whole is NOT release-verified until that register check
                 happens (docs/NEXT_TASKS.md T-003).

All amounts are integer cents. No floats, no Decimal, anywhere in this file.
"""

import pytest

from lakewood.menu import NON_PIZZA
from lakewood.pricing import Order, PizzaLine, SimpleLine, Topping

T = Topping


def _pizza(size, toppings=(), gourmet=None, hh=None, qty=1):
    return PizzaLine(size=size, gourmet=gourmet, half_and_half=hh,
                     toppings=list(toppings), quantity=qty)


def _totals(order_type, lines):
    o = Order(order_type=order_type, lines=list(lines))
    return o.subtotal(), o.tax(), o.total()


# ===========================================================================
# 1. SINGLE ITEM — 10 cases
# ===========================================================================

SINGLE_ITEM = [
    ("SM cheese",         "PHOTOGRAPHED", [_pizza("SMALL")],                1100,  81, 1181),
    ("MD cheese",         "PHOTOGRAPHED", [_pizza("MEDIUM")],               1300,  96, 1396),
    ("LG cheese",         "PHOTOGRAPHED", [_pizza("LARGE")],                1500, 110, 1610),
    ("Sicilian cheese",   "PHOTOGRAPHED", [_pizza("SICILIAN")],             1800, 132, 1932),
    ("Party cheese",      "PHOTOGRAPHED", [_pizza("PARTY")],                2600, 191, 2791),
    ("SM #1 gourmet",     "PHOTOGRAPHED", [_pizza("SMALL", gourmet=1)],     1600, 118, 1718),
    ("MD #10 gourmet",    "PHOTOGRAPHED", [_pizza("MEDIUM", gourmet=10)],   1900, 140, 2040),
    ("LG #11 gourmet",    "PHOTOGRAPHED", [_pizza("LARGE", gourmet=11)],    2300, 169, 2469),
    ("XL cheese",         "COMPOSED",     [_pizza("XLARGE")],               1700, 125, 1825),
    ("Party gourmet base","COMPOSED",     [_pizza("PARTY", gourmet=1)],     3600, 265, 3865),
]


@pytest.mark.parametrize("name,src,lines,e_sub,e_tax,e_tot", SINGLE_ITEM,
                         ids=[c[0] for c in SINGLE_ITEM])
def test_single_item(name, src, lines, e_sub, e_tax, e_tot):
    sub, tax, tot = _totals("CARRY_OUT", lines)
    assert (sub, tax, tot) == (e_sub, e_tax, e_tot)


# ===========================================================================
# 2. TOPPING / MODIFIER — 10 cases (all PHOTOGRAPHED)
# ===========================================================================

MODIFIER = [
    ("SM+mushrooms",     [_pizza("SMALL",   [T("MUSHROOMS")])],      1300,  96, 1396),
    ("MD+mushrooms",     [_pizza("MEDIUM",  [T("MUSHROOMS")])],      1550, 114, 1664),
    ("LG+mushrooms",     [_pizza("LARGE",   [T("MUSHROOMS")])],      1800, 132, 1932),
    ("XL+mushrooms",     [_pizza("XLARGE",  [T("MUSHROOMS")])],      2050, 151, 2201),
    ("SCLN+mushrooms",   [_pizza("SICILIAN",[T("MUSHROOMS")])],      2100, 154, 2254),
    ("PARTY+mushrooms",  [_pizza("PARTY",   [T("MUSHROOMS")])],      3000, 221, 3221),
    ("SM+chicken",       [_pizza("SMALL",   [T("CHICKEN")])],        1350,  99, 1449),
    ("LG+chicken",       [_pizza("LARGE",   [T("CHICKEN")])],        1850, 136, 1986),
    ("SM+(2)pepperoni",  [_pizza("SMALL",  [T("PEPPERONI", qty=2)])],1500, 110, 1610),
    ("SM+(3)pepperoni",  [_pizza("SMALL",  [T("PEPPERONI", qty=3)])],1700, 125, 1825),
]


@pytest.mark.parametrize("name,lines,e_sub,e_tax,e_tot", MODIFIER,
                         ids=[c[0] for c in MODIFIER])
def test_modifier(name, lines, e_sub, e_tax, e_tot):
    sub, tax, tot = _totals("CARRY_OUT", lines)
    assert (sub, tax, tot) == (e_sub, e_tax, e_tot)


# ===========================================================================
# 3. HALF-AND-HALF — 10 cases (all PHOTOGRAPHED)
# ===========================================================================

HALF_AND_HALF = [
    ("SM #1 0v1 halves", [_pizza("SMALL", [T("WHITE SAUCE", "HALF_1"),
                                           T("RED SAUCE", "HALF_1"),
                                           T("PEPPERONI", "HALF_2")], gourmet=1)],
     1800, 132, 1932),
    ("LG 1v1 pep|meatball", [_pizza("LARGE", [T("PEPPERONI", "HALF_1"),
                                              T("MEATBALLS", "HALF_2")])],
     2100, 154, 2254),
    ("LG 2v2", [_pizza("LARGE", [T("PEPPERONI", "HALF_1"), T("SAUSAGE", "HALF_1"),
                                 T("HAM", "HALF_2"), T("MUSHROOMS", "HALF_2")])],
     2700, 198, 2898),
    ("LG 4v1", [_pizza("LARGE", [T("PEPPERONI", "HALF_1"), T("SAUSAGE", "HALF_1"),
                                 T("HAM", "HALF_1"), T("MUSHROOMS", "HALF_1"),
                                 T("BLACK OLIVES", "HALF_2")])],
     3000, 221, 3221),
    ("LG 1v2 premium", [_pizza("LARGE", [T("CHICKEN", "HALF_1"),
                                         T("PEPPERONI", "HALF_2"), T("SAUSAGE", "HALF_2")])],
     2450, 180, 2630),
    ("LG whole+half", [_pizza("LARGE", [T("PEPPERONI"), T("MUSHROOMS", "HALF_2")])],
     2100, 154, 2254),
    ("LG whole+1v1", [_pizza("LARGE", [T("PEPPERONI"), T("SAUSAGE", "HALF_1"),
                                       T("MUSHROOMS", "HALF_2")])],
     2400, 176, 2576),
    ("LG whole+1v1 prem", [_pizza("LARGE", [T("PEPPERONI"), T("SAUSAGE", "HALF_1"),
                                            T("CHICKEN", "HALF_2")])],
     2450, 180, 2630),
    ("HH cheese 1v1", [_pizza("LARGE", hh=("CHEESE", "CHEESE"),
                              toppings=[T("PEPPERONI", "HALF_1"), T("MEATBALLS", "HALF_2")])],
     1800, 132, 1932),
    ("HH cheese 4v2", [_pizza("LARGE", hh=("CHEESE", "CHEESE"),
                              toppings=[T("BACON", "HALF_1"), T("MUSHROOMS", "HALF_1"),
                                        T("JALAPENO", "HALF_1"), T("TOMATOES", "HALF_1"),
                                        T("HAM", "HALF_2"), T("BLACK OLIVES", "HALF_2")])],
     2700, 198, 2898),
]


@pytest.mark.parametrize("name,lines,e_sub,e_tax,e_tot", HALF_AND_HALF,
                         ids=[c[0] for c in HALF_AND_HALF])
def test_half_and_half(name, lines, e_sub, e_tax, e_tot):
    sub, tax, tot = _totals("CARRY_OUT", lines)
    assert (sub, tax, tot) == (e_sub, e_tax, e_tot)


# ===========================================================================
# 4. MULTI-ITEM — 10 cases (3 PHOTOGRAPHED receipts + 7 COMPOSED carts)
# ===========================================================================

MULTI_ITEM = [
    ("carryout_pizza_wings", "PHOTOGRAPHED", "CARRY_OUT", [
        _pizza("MEDIUM", [T("RED SAUCE")], gourmet=10),
        SimpleLine("6PC WINGS", NON_PIZZA["6PC WINGS"]),
        SimpleLine("BLUE CHEESE", NON_PIZZA["BLUE CHEESE CUP"]),
    ], 2800, 206, 3006),
    ("delivery_calzone_dessert", "PHOTOGRAPHED", "DELIVERY", [
        SimpleLine("CALZONE", NON_PIZZA["CALZONE"] + NON_PIZZA["CALZONE ITEM"] * 2),
        SimpleLine("STRWBRY CHZCAKE", NON_PIZZA["STRWBRY CHZCAKE"], quantity=2),
    ], 2400, 176, 2776),
    ("delivery_burger_grinder_salad", "PHOTOGRAPHED", "DELIVERY", [
        SimpleLine("CHEESEBURGER", NON_PIZZA["CHEESEBURGER"]),
        SimpleLine("12in GRNDR MEATBALL", NON_PIZZA["GRINDER"]),
        SimpleLine("SM GARDEN SLD", NON_PIZZA["GARDEN SALAD SM"]),
    ], 2700, 198, 3098),
    ("LG cheese + MD cheese", "COMPOSED", "CARRY_OUT", [
        _pizza("LARGE"), _pizza("MEDIUM"),
    ], 2800, 206, 3006),
    ("SM cheese + 6PC WINGS", "COMPOSED", "CARRY_OUT", [
        _pizza("SMALL"), SimpleLine("6PC WINGS", NON_PIZZA["6PC WINGS"]),
    ], 1900, 140, 2040),
    ("LG#11 + 12PC WINGS", "COMPOSED", "CARRY_OUT", [
        _pizza("LARGE", gourmet=11), SimpleLine("12PC WINGS", NON_PIZZA["12PC WINGS"]),
    ], 3700, 272, 3972),
    ("Party cheese + 2x cheesecake", "COMPOSED", "CARRY_OUT", [
        _pizza("PARTY"), SimpleLine("CHEESECAKE", NON_PIZZA["CHEESECAKE"], quantity=2),
    ], 3500, 257, 3757),
    ("MD cheese + grinder + can", "COMPOSED", "CARRY_OUT", [
        _pizza("MEDIUM"), SimpleLine("GRINDER", NON_PIZZA["GRINDER"]),
        SimpleLine("CAN", NON_PIZZA["CAN"]),
    ], 2650, 195, 2845),
    ("Sicilian cheese + wrap (delivery)", "COMPOSED", "DELIVERY", [
        _pizza("SICILIAN"), SimpleLine("WRAP", NON_PIZZA["WRAP"]),
    ], 3000, 221, 3421),
    ("LG cheese + stromboli + 2L", "COMPOSED", "CARRY_OUT", [
        _pizza("LARGE"), SimpleLine("STROMBOLI", NON_PIZZA["STROMBOLI"]),
        SimpleLine("2LITER", NON_PIZZA["2LITER"]),
    ], 2900, 213, 3113),
]


@pytest.mark.parametrize("name,src,order_type,lines,e_sub,e_tax,e_tot", MULTI_ITEM,
                         ids=[c[0] for c in MULTI_ITEM])
def test_multi_item(name, src, order_type, lines, e_sub, e_tax, e_tot):
    sub, tax, tot = _totals(order_type, lines)
    assert (sub, tax, tot) == (e_sub, e_tax, e_tot)


# ===========================================================================
# 5. DELIVERY — 5 cases (3 PHOTOGRAPHED + 2 COMPOSED)
# ===========================================================================

DELIVERY = [
    ("MD cheese + 6PC WINGS (delivery)", "PHOTOGRAPHED", [
        _pizza("MEDIUM"), SimpleLine("6PC WINGS", NON_PIZZA["6PC WINGS"]),
    ], 2100, 154, 2454),
    ("delivery_calzone_dessert", "PHOTOGRAPHED", [
        SimpleLine("CALZONE", NON_PIZZA["CALZONE"] + NON_PIZZA["CALZONE ITEM"] * 2),
        SimpleLine("STRWBRY CHZCAKE", NON_PIZZA["STRWBRY CHZCAKE"], quantity=2),
    ], 2400, 176, 2776),
    ("delivery_burger_grinder_salad", "PHOTOGRAPHED", [
        SimpleLine("CHEESEBURGER", NON_PIZZA["CHEESEBURGER"]),
        SimpleLine("12in GRNDR MEATBALL", NON_PIZZA["GRINDER"]),
        SimpleLine("SM GARDEN SLD", NON_PIZZA["GARDEN SALAD SM"]),
    ], 2700, 198, 3098),
    ("LG cheese + 6PC WINGS (delivery)", "COMPOSED", [
        _pizza("LARGE"), SimpleLine("6PC WINGS", NON_PIZZA["6PC WINGS"]),
    ], 2300, 169, 2669),
    ("Party cheese (delivery)", "COMPOSED", [
        _pizza("PARTY"),
    ], 2600, 191, 2991),
]


@pytest.mark.parametrize("name,src,lines,e_sub,e_tax,e_tot", DELIVERY,
                         ids=[c[0] for c in DELIVERY])
def test_delivery(name, src, lines, e_sub, e_tax, e_tot):
    sub, tax, tot = _totals("DELIVERY", lines)
    assert (sub, tax, tot) == (e_sub, e_tax, e_tot)


# ===========================================================================
# 6. COUPON / SPECIAL — 5 cases — UNVERIFIED category (tax-ordering not yet
#    confirmed at the register; see docs/COUPON-VERIFICATION.md). Discount
#    *amounts* are printed-menu/PHOTOGRAPHED; totals below assume the
#    currently-coded rule (tax applies to the discounted subtotal).
# ===========================================================================

COUPON = [
    ("OFF_3_AT_30 on party+wings", [
        _pizza("PARTY"), SimpleLine("6PC WINGS", NON_PIZZA["6PC WINGS"]),
    ], 300, 3400, 228, 3328),
    ("COMBO_27", [
        _pizza("LARGE", [T("PEPPERONI")]),
        SimpleLine("6PC WINGS", NON_PIZZA["6PC WINGS"]),
        SimpleLine("2LITER", NON_PIZZA["2LITER"]),
    ], 300, 3000, 198, 2898),
    ("FREE_2L on two larges", [
        _pizza("LARGE", qty=2), SimpleLine("2LITER", NON_PIZZA["2LITER"]),
    ], 400, 3400, 221, 3221),
    ("OFF_5_AT_50 on two party pizzas", [
        _pizza("PARTY", qty=2),
    ], 500, 5200, 345, 5045),
    ("OFF_3_AT_30 on gourmet+wings", [
        _pizza("MEDIUM", gourmet=10), SimpleLine("12PC WINGS", NON_PIZZA["12PC WINGS"]),
    ], 300, 3300, 221, 3221),
]


@pytest.mark.parametrize("name,lines,discount,e_sub,e_tax,e_tot", COUPON,
                         ids=[c[0] for c in COUPON])
def test_coupon_unverified_tax_ordering(name, lines, discount, e_sub, e_tax, e_tot):
    o = Order(order_type="CARRY_OUT", lines=list(lines), coupon_discount=discount)
    assert o.subtotal() == e_sub
    assert o.tax() == e_tax
    assert o.total() == e_tot
