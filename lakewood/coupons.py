"""
Coupons. PrISM has these as codes, so staff key the code and PrISM computes the
discount. Our quote must land on the same number or the customer hears one total
and pays another.

ORDER OF OPERATIONS — inferred from the POS screen, which lists:

    Sub Total  ->  Discount  ->  Sales Tax  ->  Total

Tax therefore applies to (subtotal - discount), not to the raw subtotal.
That is a prediction, not an observation. `docs/COUPON-VERIFICATION.md` has
three orders that confirm or refute it in about two minutes at the register.

All four are marked "cannot be combined with any other offer" on the printed
menu, so `best_coupon` picks exactly one — the one worth most to the customer.

All money is integer cents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .pricing import PizzaLine, SimpleLine


@dataclass(frozen=True)
class Coupon:
    code: str
    description: str
    applies: Callable[[list], bool]
    discount: Callable[[list, int], int]
    spoken: str                      # how a caller is likely to ask for it


def _large_pizzas(lines) -> int:
    return sum(l.quantity for l in lines
               if isinstance(l, PizzaLine) and l.size == "LARGE")


def _has(lines, name: str) -> bool:
    return any(isinstance(l, SimpleLine) and l.name == name for l in lines)


def _large_one_topping(lines):
    for l in lines:
        if (isinstance(l, PizzaLine) and l.size == "LARGE" and not l.gourmet
                and not l.half_and_half
                and len([t for t in l.toppings if not t.removed and t.cost(l.size)]) == 1):
            return l
    return None


def _combo_27(lines, subtotal: int) -> int:
    """Large 1-topping + 6pc wings + 2 liter, all in for $27 plus tax."""
    pizza = _large_one_topping(lines)
    if not (pizza and _has(lines, "6PC WINGS") and _has(lines, "2LITER")):
        return 0
    bundle = pizza.base_price() + pizza.topping_cost() + 800 + 400
    return max(0, bundle - 2700)


COUPONS = [
    Coupon("COMBO_27",
           "Large 1-topping pizza + 6 wings + 2-liter soda for $27",
           lambda ls: _combo_27(ls, 0) > 0,
           lambda ls, sub: _combo_27(ls, sub),
           "the twenty-seven dollar deal"),
    Coupon("FREE_2L",
           "Buy 2 large pizzas, get a 2-liter soda free",
           lambda ls: _large_pizzas(ls) >= 2 and _has(ls, "2LITER"),
           lambda ls, sub: 400,
           "the free soda with two larges"),
    Coupon("OFF_5_AT_50",
           "$5 off any order of $50 or more",
           lambda ls: True,
           lambda ls, sub: 500 if sub >= 5000 else 0,
           "five dollars off fifty"),
    Coupon("OFF_3_AT_30",
           "$3 off any order of $30 or more",
           lambda ls: True,
           lambda ls, sub: 300 if sub >= 3000 else 0,
           "three dollars off thirty"),
]

BY_CODE = {c.code: c for c in COUPONS}


def eligible(lines, subtotal: int) -> list[tuple[Coupon, int]]:
    out = []
    for c in COUPONS:
        try:
            d = c.discount(lines, subtotal)
        except Exception:
            d = 0
        if d > 0:
            out.append((c, d))
    return out


def best_coupon(lines, subtotal: int) -> Optional[tuple[Coupon, int]]:
    """Exactly one — never stack. Ties go to the larger discount."""
    e = eligible(lines, subtotal)
    return max(e, key=lambda x: x[1]) if e else None


def apply(code: str, lines, subtotal: int) -> tuple[int, Optional[str]]:
    """Returns (discount_cents, error). Unknown or ineligible codes discount nothing."""
    c = BY_CODE.get((code or "").upper())
    if not c:
        return 0, f"No coupon called {code!r}."
    d = c.discount(lines, subtotal)
    if d <= 0:
        return 0, f"{c.code} doesn't apply to this order."
    return d, None
