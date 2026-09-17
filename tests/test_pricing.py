"""
Regression suite: every number here was photographed off the store's PrISM POS.

If one of these fails, the pricing model changed and the change is wrong until
proven otherwise with a new screenshot. Never "fix" a test to match the code.
"""

import pytest

from lakewood.menu import NON_PIZZA
from lakewood.pricing import Order, PizzaLine, SimpleLine, Topping, money


def _pizza(size, toppings=(), gourmet=None, hh=None, qty=1):
    return PizzaLine(size=size, gourmet=gourmet, half_and_half=hh,
                     toppings=list(toppings), quantity=qty)


def _sub(line, order_type="CARRY_OUT"):
    o = Order(order_type=order_type, lines=[line])
    return o.subtotal(), o.tax(), o.total()


T = Topping

TESTS = [
    # --- plain cheese, all six sizes -------------------------------------
    ("SM cheese",        _pizza("SMALL"),                          "11.00", "0.81", "11.81"),
    ("MD cheese",        _pizza("MEDIUM"),                         "13.00", "0.96", "13.96"),
    ("LG cheese",        _pizza("LARGE"),                          "15.00", "1.10", "16.10"),
    ("Sicilian cheese",  _pizza("SICILIAN"),                       "18.00", "1.32", "19.32"),
    ("Party cheese",     _pizza("PARTY"),                          "26.00", "1.91", "27.91"),

    # --- regular tier across sizes ---------------------------------------
    ("SM+mushrooms",     _pizza("SMALL",   [T("MUSHROOMS")]),      "13.00", "0.96", "13.96"),
    ("MD+mushrooms",     _pizza("MEDIUM",  [T("MUSHROOMS")]),      "15.50", "1.14", "16.64"),
    ("LG+mushrooms",     _pizza("LARGE",   [T("MUSHROOMS")]),      "18.00", "1.32", "19.32"),
    ("XL+mushrooms",     _pizza("XLARGE",  [T("MUSHROOMS")]),      "20.50", "1.51", "22.01"),
    ("SCLN+mushrooms",   _pizza("SICILIAN",[T("MUSHROOMS")]),      "21.00", "1.54", "22.54"),
    ("PARTY+mushrooms",  _pizza("PARTY",   [T("MUSHROOMS")]),      "30.00", "2.21", "32.21"),

    # --- premium tier across sizes ---------------------------------------
    ("SM+chicken",       _pizza("SMALL",   [T("CHICKEN")]),        "13.50", "0.99", "14.49"),
    ("MD+chicken",       _pizza("MEDIUM",  [T("CHICKEN")]),        "16.00", "1.18", "17.18"),
    ("LG+chicken",       _pizza("LARGE",   [T("CHICKEN")]),        "18.50", "1.36", "19.86"),
    ("XL+chicken",       _pizza("XLARGE",  [T("CHICKEN")]),        "21.00", "1.54", "22.54"),
    ("SCLN+chicken",     _pizza("SICILIAN",[T("CHICKEN")]),        "22.00", "1.62", "23.62"),
    ("PARTY+chicken",    _pizza("PARTY",   [T("CHICKEN")]),        "31.00", "2.28", "33.28"),
    ("SM+ricotta",       _pizza("SMALL",   [T("RICOTTA")]),        "13.50", "0.99", "14.49"),
    ("SM+steak",         _pizza("SMALL",   [T("STEAK")]),          "13.50", "0.99", "14.49"),

    # --- page-2 modifiers -------------------------------------------------
    ("SM+parmesan",      _pizza("SMALL", [T("PARMESAN CHEESE")]),  "13.00", "0.96", "13.96"),
    ("SM+pesto (free)",  _pizza("SMALL", [T("PESTO SAUCE")]),      "11.00", "0.81", "11.81"),
    ("SM+side ranch",    _pizza("SMALL", [T("SIDE RANCH")]),       "11.25", "0.83", "12.08"),
    ("SM+side bluechz",  _pizza("SMALL", [T("SIDE BLUE CHEESE")]), "11.25", "0.83", "12.08"),

    # --- DOUBLE / TRIPLE --------------------------------------------------
    ("SM+(2)pepperoni",  _pizza("SMALL",  [T("PEPPERONI", qty=2)]), "15.00", "1.10", "16.10"),
    ("SM+(3)pepperoni",  _pizza("SMALL",  [T("PEPPERONI", qty=3)]), "17.00", "1.25", "18.25"),
    ("MD+(2)pepperoni",  _pizza("MEDIUM", [T("PEPPERONI", qty=2)]), "18.00", "1.32", "19.32"),
    ("MD+(3)pepperoni",  _pizza("MEDIUM", [T("PEPPERONI", qty=3)]), "20.50", "1.51", "22.01"),

    # --- multi-topping ----------------------------------------------------
    ("LG+pep+mush+chkn", _pizza("LARGE", [T("PEPPERONI"), T("MUSHROOMS"),
                                          T("CHICKEN")]),          "24.50", "1.80", "26.30"),

    # --- REGULAR SKU splits: every topping charged ------------------------
    ("SM #1 0v1 halves", _pizza("SMALL", [T("WHITE SAUCE", "HALF_1"),
                                          T("RED SAUCE", "HALF_1"),
                                          T("PEPPERONI", "HALF_2")], gourmet=1),
                                                                   "18.00", "1.32", "19.32"),
    ("LG 1v1 pep|meatball", _pizza("LARGE", [T("PEPPERONI", "HALF_1"),
                                             T("MEATBALLS", "HALF_2")]),
                                                                   "21.00", "1.54", "22.54"),
    ("LG 2v2",           _pizza("LARGE", [T("PEPPERONI", "HALF_1"), T("SAUSAGE", "HALF_1"),
                                          T("HAM", "HALF_2"), T("MUSHROOMS", "HALF_2")]),
                                                                   "27.00", "1.98", "28.98"),
    ("LG 4v1",           _pizza("LARGE", [T("PEPPERONI", "HALF_1"), T("SAUSAGE", "HALF_1"),
                                          T("HAM", "HALF_1"), T("MUSHROOMS", "HALF_1"),
                                          T("BLACK OLIVES", "HALF_2")]),
                                                                   "30.00", "2.21", "32.21"),
    ("LG 1v2 premium",   _pizza("LARGE", [T("CHICKEN", "HALF_1"),
                                          T("PEPPERONI", "HALF_2"), T("SAUSAGE", "HALF_2")]),
                                                                   "24.50", "1.80", "26.30"),

    # --- whole + half on the same pizza -----------------------------------
    ("LG whole+half",    _pizza("LARGE", [T("PEPPERONI"), T("MUSHROOMS", "HALF_2")]),
                                                                   "21.00", "1.54", "22.54"),
    ("LG whole+1v1",     _pizza("LARGE", [T("PEPPERONI"), T("SAUSAGE", "HALF_1"),
                                          T("MUSHROOMS", "HALF_2")]),
                                                                   "24.00", "1.76", "25.76"),
    ("LG whole+1v1 prem",_pizza("LARGE", [T("PEPPERONI"), T("SAUSAGE", "HALF_1"),
                                          T("CHICKEN", "HALF_2")]),
                                                                   "24.50", "1.80", "26.30"),

    # --- HALF & HALF SKU: larger half only --------------------------------
    ("HH cheese 1v1",    _pizza("LARGE", hh=("CHEESE", "CHEESE"),
                                toppings=[T("PEPPERONI", "HALF_1"),
                                          T("MEATBALLS", "HALF_2")]),
                                                                   "18.00", "1.32", "19.32"),
    ("HH cheese 4v2",    _pizza("LARGE", hh=("CHEESE", "CHEESE"),
                                toppings=[T("BACON", "HALF_1"), T("MUSHROOMS", "HALF_1"),
                                          T("JALAPENO", "HALF_1"), T("TOMATOES", "HALF_1"),
                                          T("HAM", "HALF_2"), T("BLACK OLIVES", "HALF_2")]),
                                                                   "27.00", "1.98", "28.98"),

    # --- gourmet ----------------------------------------------------------
    ("SM #1",            _pizza("SMALL",  gourmet=1),              "16.00", "1.18", "17.18"),
    ("MD #10",           _pizza("MEDIUM", gourmet=10),             "19.00", "1.40", "20.40"),
    ("LG #11",           _pizza("LARGE",  gourmet=11),             "23.00", "1.69", "24.69"),
    ("SCLN #1 + sauce",  _pizza("SICILIAN", [T("WHITE SAUCE")], gourmet=1),
                                                                   "24.00", "1.76", "25.76"),
    ("MD #10 +bacon+ckn",_pizza("MEDIUM", [T("WHITE SAUCE"), T("BACON"), T("CHICKEN")],
                                gourmet=10),                       "24.50", "1.80", "26.30"),
    ("MD #10 no pineapple", _pizza("MEDIUM", [T("WHITE SAUCE"),
                                              T("PINEAPPLE", removed=True)], gourmet=10),
                                                                   "19.00", "1.40", "20.40"),

    # --- HALF & HALF: flat gourmet price, no surcharge --------------------
    ("SM half&half",     _pizza("SMALL",  hh=(10, 8)),             "16.00", "1.18", "17.18"),
    ("MD half&half",     _pizza("MEDIUM", hh=(10, 8)),             "19.00", "1.40", "20.40"),
    ("LG half&half",     _pizza("LARGE",  hh=(10, 8)),             "23.00", "1.69", "24.69"),
]


# Two observations that this model does NOT reproduce. Both were entered on a
# regular SKU yet priced like a HALF & HALF (larger half only). Both were
# re-run and reproduced exactly, so they are real, not mis-taps — but no
# variable we can see distinguishes them from the additive cases above.
# Suspected cause: a second entry path (the bottom bar shows "Half 1" in some
# screenshots and "Half 2" in others, i.e. a stateful toggle).
# Production impact: staff taking this path UNDERCHARGE by $6-7 per pizza.
# The nightly reconciliation report should surface these.
ANOMALIES = [
    ("XL cheese 3v2",  "additive 34.50", "POS showed 27.50"),
    ("LG #17 4v2",     "additive 41.00", "POS showed 35.00"),
]




@pytest.mark.parametrize("name,line,e_sub,e_tax,e_tot",
                         TESTS, ids=[t[0] for t in TESTS])
def test_observed_total(name, line, e_sub, e_tax, e_tot):
    sub, tax, tot = _sub(line)
    assert (money(sub), money(tax), money(tot)) == (e_sub, e_tax, e_tot)


RECEIPTS = [
    ("carryout_pizza_wings",
     Order("CARRY_OUT", [
         _pizza("MEDIUM", [Topping("RED SAUCE")], gourmet=10),
         SimpleLine("6PC WINGS", NON_PIZZA["6PC WINGS"]),
         SimpleLine("BLUE CHEESE", NON_PIZZA["BLUE CHEESE CUP"]),
     ]), "28.00", "2.06", "30.06"),
    ("delivery_calzone_dessert",
     Order("DELIVERY", [
         SimpleLine("CALZONE", NON_PIZZA["CALZONE"] + NON_PIZZA["CALZONE ITEM"] * 2),
         SimpleLine("STRWBRY CHZCAKE", NON_PIZZA["STRWBRY CHZCAKE"], quantity=2),
     ]), "24.00", "1.76", "27.76"),
    ("delivery_burger_grinder_salad",
     Order("DELIVERY", [
         SimpleLine("CHEESEBURGER", NON_PIZZA["CHEESEBURGER"]),
         SimpleLine('12in GRNDR MEATBALL', NON_PIZZA["GRINDER"]),
         SimpleLine("SM GARDEN SLD", NON_PIZZA["GARDEN SALAD SM"]),
     ]), "27.00", "1.98", "30.98"),
]


@pytest.mark.parametrize("name,order,e_sub,e_tax,e_tot",
                         RECEIPTS, ids=[r[0] for r in RECEIPTS])
def test_observed_receipt(name, order, e_sub, e_tax, e_tot):
    assert (money(order.subtotal()), money(order.tax()), money(order.total())) \
        == (e_sub, e_tax, e_tot)


def test_unpriced_button_fails_loud(monkeypatch):
    """
    All tiers are confirmed today, so this injects a hypothetical new POS button.
    The mechanism must stay wired: an unpriced topping raises instead of guessing.
    A crash in the logs beats a wrong total on a customer's phone.
    """
    from lakewood import menu, pricing
    monkeypatch.setattr(menu, "UNKNOWN_TIER", {"TRUFFLE OIL"})
    monkeypatch.setattr(pricing, "UNKNOWN_TIER", {"TRUFFLE OIL"})
    with pytest.raises(ValueError):
        _pizza("LARGE", [Topping("TRUFFLE OIL")]).topping_cost()


@pytest.mark.xfail(reason="Two POS readings this model cannot reproduce; both "
                          "re-run and confirmed. Suspected second entry path. "
                          "See docs/OPEN-QUESTIONS.md", strict=True)
@pytest.mark.parametrize("line,observed", [
    (_pizza("XLARGE", [Topping("PEPPERONI", "HALF_1"), Topping("SAUSAGE", "HALF_1"),
                       Topping("MUSHROOMS", "HALF_1"), Topping("HAM", "HALF_2"),
                       Topping("BLACK OLIVES", "HALF_2")]), "27.50"),
    (_pizza("LARGE", [Topping("BACON", "HALF_1"), Topping("MUSHROOMS", "HALF_1"),
                      Topping("JALAPENO", "HALF_1"), Topping("TOMATOES", "HALF_1"),
                      Topping("HAM", "HALF_2"), Topping("BLACK OLIVES", "HALF_2")],
            gourmet=17), "35.00"),
])
def test_known_anomalies(line, observed):
    assert money(_sub(line)[0]) == observed
