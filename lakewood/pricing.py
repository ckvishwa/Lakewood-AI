"""
Deterministic pricing. The LLM never touches this module.

Two split-pizza modes, confirmed by the owner and reproduced on the POS:
  regular SKU (CHEESE PIZZA / single gourmet) + Half/Half 2 -> every topping charged
  HALF & HALF SKU (half-specialty / half-specialty)         -> larger half only
The same pizza differs by $3 between the two paths. See docs/OPEN-QUESTIONS.md.

All money is integer cents internally — never float, never Decimal. `money()`
formats cents to a dollar string only at the display boundary (quote() dict,
ticket rendering); nothing upstream of that boundary does string math.
"""

from dataclasses import dataclass, field
from typing import Literal, Optional

from .menu import *          # noqa: F403 — menu is pure data
from .menu import SIZES, PREMIUM_TOPPINGS, REGULAR_TOPPINGS, FREE_MODIFIERS, \
    FLAT_MODIFIERS, UNKNOWN_TIER, GOURMET_ROUND, GOURMET_SICILIAN_OVERRIDES, \
    NON_PIZZA, TAX_RATE_NUMERATOR, TAX_RATE_DENOMINATOR, DELIVERY_SERVICE_CHARGE


def money(cents: int) -> str:
    """Format integer cents as a dollar string, e.g. 1181 -> '11.81'."""
    if cents < 0:
        raise ValueError(f"negative money: {cents} cents")
    return f"{cents // 100}.{cents % 100:02d}"


def tax_cents(base_cents: int) -> int:
    """
    7.35% half-up on the taxable base, computed in pure integer arithmetic
    (no float, no Decimal). PrISM-verified: 3000 cents * .0735 = 220.5 -> 221.
    """
    numerator = base_cents * TAX_RATE_NUMERATOR
    half = TAX_RATE_DENOMINATOR // 2
    return (numerator + half) // TAX_RATE_DENOMINATOR


# ===========================================================================
# MODEL
# ===========================================================================

Portion = Literal["WHOLE", "HALF_1", "HALF_2"]


@dataclass
class Topping:
    name: str
    portion: Portion = "WHOLE"
    qty: int = 1                  # 1 normal, 2 = DOUBLE, 3 = TRIPLE
    removed: bool = False         # NO  — free, no credit
    lite: bool = False            # LITE — free

    def tier_rate(self, size: str) -> int:
        n = self.name.upper()
        if self.removed or self.lite or n in FREE_MODIFIERS:
            return 0
        if n in FLAT_MODIFIERS:
            return FLAT_MODIFIERS[n]
        if n in PREMIUM_TOPPINGS:
            return SIZES[size]["prem"]
        if n in REGULAR_TOPPINGS:
            return SIZES[size]["reg"]
        if n in UNKNOWN_TIER:
            raise ValueError(
                f"Topping tier unknown for {n!r}. Confirm against the POS "
                f"before quoting — never guess a price."
            )
        raise ValueError(f"Unknown topping: {n!r}")

    def cost(self, size: str) -> int:
        return self.tier_rate(size) * self.qty


@dataclass
class PizzaLine:
    size: str
    gourmet: Optional[int] = None            # 1-27, or None for cheese
    half_and_half: Optional[tuple] = None    # (n1, n2) two gourmet numbers
    toppings: list = field(default_factory=list)
    quantity: int = 1

    def base_price(self) -> int:
        s = SIZES[self.size]
        if self.half_and_half:
            # HALF & HALF SKU: gourmet price if two specialties, cheese if plain.
            if all(isinstance(x, int) for x in self.half_and_half):
                return s["gourmet"]
            return s["cheese"]
        if self.gourmet:
            return s["gourmet"]
        return s["cheese"]

    def topping_cost(self) -> int:
        """
        Two pricing modes, confirmed by the owner and reproduced on the POS.

        Regular SKU (CHEESE PIZZA or a single gourmet) + Half / Half 2:
            every topping is charged, at the full whole-pizza rate.
            This is what staff use for an ordinary half-and-half.

        HALF & HALF SKU (intended for half-specialty / half-specialty):
            only the costlier half is charged.

        The same pizza priced down the two paths differs — a LG pepperoni /
        meatball split is $21.00 as a CHEESE PIZZA and $18.00 as a HALF & HALF.
        """
        whole = sum(t.cost(self.size) for t in self.toppings if t.portion == "WHOLE")
        h1 = sum(t.cost(self.size) for t in self.toppings if t.portion == "HALF_1")
        h2 = sum(t.cost(self.size) for t in self.toppings if t.portion == "HALF_2")

        if self.half_and_half:
            return whole + max(h1, h2)
        return whole + h1 + h2          # additive — the normal path

    def line_total(self) -> int:
        return (self.base_price() + self.topping_cost()) * self.quantity

    def display_name(self) -> str:
        if self.half_and_half:
            n1, n2 = self.half_and_half
            if isinstance(n1, int) and isinstance(n2, int):
                names = GOURMET_ROUND.copy()
                if self.size == "SICILIAN":
                    names.update(GOURMET_SICILIAN_OVERRIDES)
                return (f"{self.size} HALF & HALF — "
                       f"#{n1} {names[n1].upper()} / #{n2} {names[n2].upper()}")
            return f"{self.size} HALF & HALF"
        if self.gourmet:
            names = GOURMET_ROUND.copy()
            if self.size == "SICILIAN":
                names.update(GOURMET_SICILIAN_OVERRIDES)
            return f"{self.size} #{self.gourmet} {names[self.gourmet].upper()}"
        return f"{self.size} CHEESE PIZZA"


@dataclass
class SimpleLine:
    """Anything that isn't a pizza."""
    name: str
    unit_price: int
    quantity: int = 1

    def line_total(self) -> int:
        return self.unit_price * self.quantity


@dataclass
class Order:
    order_type: Literal["CARRY_OUT", "DELIVERY"] = "CARRY_OUT"
    lines: list = field(default_factory=list)
    coupon_code: Optional[str] = None
    coupon_discount: int = 0

    def subtotal(self) -> int:
        return sum(l.line_total() for l in self.lines)

    def discount(self) -> int:
        return self.coupon_discount

    def taxable_base(self) -> int:
        # PrISM lists Sub Total -> Discount -> Sales Tax, so tax follows the
        # discount. See docs/COUPON-VERIFICATION.md before trusting this.
        return max(0, self.subtotal() - self.discount())

    def tax(self) -> int:
        return tax_cents(self.taxable_base())

    def service_charge(self) -> int:
        return DELIVERY_SERVICE_CHARGE if self.order_type == "DELIVERY" else 0

    def total(self) -> int:
        # subtotal -> -discount -> +tax -> +service charge. The charge is untaxed.
        return self.taxable_base() + self.tax() + self.service_charge()

    def quote(self) -> dict:
        return {
            "subtotal": money(self.subtotal()),
            "discount": money(self.discount()),
            "coupon": self.coupon_code,
            "tax": money(self.tax()),
            "service_charge": money(self.service_charge()),
            "total": money(self.total()),
            "lines": [
                {"name": getattr(l, "display_name", lambda: l.name)(),
                 "total": money(l.line_total())}
                for l in self.lines
            ],
        }


# ===========================================================================
# POS TICKET RENDERER
# Formats an order in PrISM's own entry sequence so re-keying is fast.
# ===========================================================================

def render_ticket(order: Order, order_id: str, when: str) -> str:
    head = "CARRY-OUT" if order.order_type == "CARRY_OUT" else "DELIVERY"
    out = [f"** AI ORDER — {head} **", f"Ord# {order_id}   {when}", ""]

    for line in order.lines:
        if not isinstance(line, PizzaLine):
            out.append(f"{line.quantity}  {line.name}")
            continue

        out.append(f"{line.quantity}  {line.display_name()}")

        if line.half_and_half:
            names = GOURMET_ROUND.copy()
            if line.size == "SICILIAN":
                names.update(GOURMET_SICILIAN_OVERRIDES)
            n1, n2 = line.half_and_half
            out.append(f"     <1st Half>  #{n1} {names[n1].upper()}")
            out.append(f"     <2nd Half>  #{n2} {names[n2].upper()}")

        for label, portion in (("", "WHOLE"),
                               ("<1st Half>", "HALF_1"),
                               ("<2nd Half>", "HALF_2")):
            group = [t for t in line.toppings if t.portion == portion]
            if not group:
                continue
            if label:
                out.append(f"     {label}")
            for t in group:
                prefix = ""
                if t.removed:
                    prefix = "<no> "
                elif t.lite:
                    prefix = "<lite> "
                elif t.qty == 2:
                    prefix = "(2) "
                elif t.qty == 3:
                    prefix = "(3) "
                out.append(f"       {prefix}{t.name}")
        out.append("")

    out.append(f"QUOTED TOTAL: ${money(order.total())}")
    return "\n".join(out)
