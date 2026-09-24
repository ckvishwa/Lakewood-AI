"""
Menu data for Lakewood Pizza, 562 Lakewood Rd, Waterbury CT.

Loaded from `data/menu.json` — the single source of truth. Every value in
that file was read off the store's PrISM POS or the printed menu and
verified against 50 real register totals. Do not edit a price without a
screenshot to back it up, and edit it in `data/menu.json`, not here.

All money is integer cents. Never floats, never Decimal, for a price.
"""

import json
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "menu.json"

with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _RAW = json.load(_f)

# T-059: HOURS below is meaningless without knowing which timezone it's
# in — a per-store fact, same kind as the store's own address, never a
# global constant and never inferred from wherever the server process
# happens to run. Lives in data/menu.json next to `store.address` for the
# same reason HOURS does: one source of truth, multi-tenant-ready (a
# second store's own menu.json carries its own timezone).
STORE_TIMEZONE = _RAW["store"]["timezone"]

TAX_RATE_NUMERATOR = _RAW["currency"]["tax_rate_numerator"]
TAX_RATE_DENOMINATOR = _RAW["currency"]["tax_rate_denominator"]
DELIVERY_SERVICE_CHARGE = _RAW["currency"]["delivery_service_charge_cents"]
DELIVERY_MINIMUM = _RAW["currency"]["delivery_minimum_cents"]
DELIVERY_RADIUS_MILES = _RAW["currency"]["delivery_radius_miles"]

SIZES = {
    size: {
        "cheese": v["cheese_cents"],
        "reg": v["regular_topping_cents"],
        "prem": v["premium_topping_cents"],
        "gourmet": v["gourmet_cents"],
    }
    for size, v in _RAW["sizes"].items()
}

PREMIUM_TOPPINGS = set(_RAW["topping_tiers"]["premium"])
REGULAR_TOPPINGS = set(_RAW["topping_tiers"]["regular"])
FREE_MODIFIERS = set(_RAW["topping_tiers"]["free"])

# All tiers are now confirmed. Kept as a (normally empty) set so the fail-loud
# path in pricing.Topping.tier_rate() stays wired: any future POS button we
# haven't priced goes in `data/menu.json`'s topping_tiers.unknown and refuses
# to quote rather than guessing. See docs/OPEN-QUESTIONS.md.
UNKNOWN_TIER: set = set(_RAW["topping_tiers"]["unknown"])

FLAT_MODIFIERS = dict(_RAW["flat_modifiers_cents"])

GOURMET_ROUND = {int(k): v for k, v in _RAW["gourmet_pizzas"].items()}

# The Sicilian screen shows different names at a few numbers. Owner: STALE
# BUTTONS — not sold. The agent must never offer them, so this map is empty
# and Sicilian uses the standard list.
GOURMET_SICILIAN_OVERRIDES = {int(k): v for k, v in _RAW["gourmet_sicilian_overrides"].items()}

NON_PIZZA = dict(_RAW["non_pizza_items"])

HOURS = {int(k): (tuple(v) if v is not None else None) for k, v in _RAW["hours"].items()}

COUPON_DATA = _RAW["coupons"]


# ---------------------------------------------------------------------------
# Stable item IDs — every sellable item gets one. Pricing still comes from the
# tier tables above (a pizza's price depends on size/tier, not a flat per-SKU
# number), so these IDs are a catalog/lookup layer over the same verified
# data, not a second price table.
# ---------------------------------------------------------------------------

def _build_item_catalog() -> list:
    items = []
    for size, prices in SIZES.items():
        items.append({
            "id": f"PIZZA_{size}_CHEESE",
            "name": f"{size.title()} Cheese Pizza",
            "category": "pizza",
            "size": size,
            "base_price_cents": prices["cheese"],
            "active": True,
        })
        items.append({
            "id": f"PIZZA_{size}_GOURMET",
            "name": f"{size.title()} Specialty Pizza (any of the 27)",
            "category": "pizza",
            "size": size,
            "base_price_cents": prices["gourmet"],
            "active": True,
        })
    for number, name in GOURMET_ROUND.items():
        items.append({
            "id": f"GOURMET_{number}",
            "name": name,
            "category": "gourmet_pizza",
            "number": number,
            "active": True,
        })
    for name in sorted(REGULAR_TOPPINGS):
        items.append({
            "id": f"TOPPING_{name.replace(' ', '_')}",
            "name": name.title(),
            "category": "topping",
            "tier": "regular",
            "prices_cents": {size: SIZES[size]["reg"] for size in SIZES},
            "active": True,
        })
    for name in sorted(PREMIUM_TOPPINGS):
        items.append({
            "id": f"TOPPING_{name.replace(' ', '_')}",
            "name": name.title(),
            "category": "topping",
            "tier": "premium",
            "prices_cents": {size: SIZES[size]["prem"] for size in SIZES},
            "active": True,
        })
    for name in sorted(FREE_MODIFIERS):
        items.append({
            "id": f"MODIFIER_{name.replace(' ', '_')}",
            "name": name.title(),
            "category": "free_modifier",
            "base_price_cents": 0,
            "active": True,
        })
    for name, cents in FLAT_MODIFIERS.items():
        items.append({
            "id": f"MODIFIER_{name.replace(' ', '_')}",
            "name": name.title(),
            "category": "flat_modifier",
            "base_price_cents": cents,
            "active": True,
        })
    for name, cents in NON_PIZZA.items():
        items.append({
            "id": f"ITEM_{name.replace(' ', '_').replace('/', '_')}",
            "name": name.title(),
            "category": "non_pizza",
            "base_price_cents": cents,
            "active": True,
        })
    return items


ITEM_CATALOG = _build_item_catalog()
ITEMS_BY_ID = {item["id"]: item for item in ITEM_CATALOG}
