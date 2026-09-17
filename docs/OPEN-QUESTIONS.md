# Open questions

Ordered by what blocks progress.

## OPEN

### 1. Two POS readings the pricing model cannot reproduce  — ACCEPTED VARIANCE

Both re-run and confirmed, so they are real, not mis-taps.

| Order | Model says | POS showed |
|---|---|---|
| XL cheese, 3 toppings v 2 | $34.50 | **$27.50** |
| LG #17, 4 toppings v 2 | $41.00 | **$35.00** |

Both were entered on a regular SKU but priced like a HALF & HALF (larger half
only). Suspected cause: a second entry path — the bottom button bar reads
`Half | Half 1` in some screenshots and `Half | Half 2` in others, i.e. a
stateful toggle.

**Business impact:** staff taking that path undercharge by $6–7 per pizza, and
it is invisible in reports. This is worth raising with the owner on its own
merits, ahead of any demo.

**Owner's position: the store has entered orders this way for years and is not
changing.** So this is accepted variance, not a bug to chase.

Practical consequence: when staff take that entry path, the register rings
$6–7 **lower** than our quote. The error is in the customer's favour, which is
the safe direction — but the reconciliation report must treat these as expected
variance rather than alarms, or it will cry wolf nightly.

Encoded as `xfail(strict=True)` in `tests/test_pricing.py` — if the store's
behavior ever changes, the suite goes red and says so.

### 2. Does tax apply before or after a coupon discount?  — BLOCKS: quote accuracy

Coupons are PrISM codes (owner-confirmed) and are implemented in
`lakewood/coupons.py`. One assumption remains: the POS screen lists
Sub Total → Discount → Sales Tax, so we tax the discounted amount.

**Three orders confirm or refute it in two minutes** — see
`docs/COUPON-VERIFICATION.md`. Also open there: the printed coupons say "must
present to redeem," so does the store honor them by phone at all?

---

## ANSWERED (owner, Sept 6)

| Question | Answer | Where it lives |
|---|---|---|
| HALF & HALF button scope | Specialty pizzas only (BBQ, Hawaiian, etc). Normal toppings use the regular half. | `pricing.PizzaLine.topping_cost` |
| Fresh mozzarella, pineapple, artichoke, garlic | Regular tier | `menu.REGULAR_TOPPINGS` |
| Shrimp | Premium tier | `menu.PREMIUM_TOPPINGS` |
| Five toppings | Five button presses, five charges. No bundle. | `test_five_toppings_are_five_charges` |
| Hours | Tue–Sat 11 AM–10 PM, Sun 12 PM–9 PM, **closed Monday** | `menu.HOURS` |
| After-hours calls | Currently unanswered — pure lost revenue. AI takes the order and holds it. | `orders.store_status`, `HELD_FOR_OPEN` |
| Delivery zone | Under 7 miles | `menu.DELIVERY_RADIUS_MILES` |
| Delivery minimum | $18 subtotal | `menu.DELIVERY_MINIMUM` |
| Sicilian #3/#5/#8 | **Stale buttons** — not sold, never offer | `menu.GOURMET_SICILIAN_OVERRIDES = {}` |
| Coupons | Already integrated in PrISM as codes | `lakewood/coupons.py` |
| Printer | **Ordered** | `printer.py`, dry-run until it arrives |

### Note on after-hours

The agent takes the order, but the readback **must** disclose the hold:

> "We're closed right now, so this is held for Tuesday at 11 AM — not tonight."

That sentence is generated in `_short_readback` and is not optional. A caller at
10:30 PM who thinks food is coming tonight is a complaint, not a captured order.
Nothing prints to a dark kitchen: `dispatch_confirmed_order` moves the order to
`HELD_FOR_OPEN` and a scheduler prints the queue at opening.
