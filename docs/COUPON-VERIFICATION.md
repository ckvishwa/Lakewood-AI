# Coupon verification — 3 orders, ~2 minutes

Coupons are PrISM codes, so our quote has to land on the same number the
register produces. One thing is still an assumption.

## The assumption

Every PrISM screen lists the totals in this order:

```
Sub Total   $34.00
Discount     $3.00
Sales Tax    $2.28
Total       $33.28
```

We read that as **tax applying after the discount** — 7.35% of $31.00, not of
$34.00. On a $3 discount the two readings differ by 22¢; on the $27 combo they
differ more. Small, but it's the difference between a quote that matches and one
that doesn't, and mismatches at the counter are what erode a pilot.

## The three checks

Ring these up, apply the coupon code, and read the Sales Tax line.

### 1. $3 off $30

`PARTY CHEESE PIZZA` + `6PC WINGS`, then the $3-off-$30 coupon.

| Sales Tax | Meaning |
|---|---|
| **$2.28** | tax after discount — our model is right |
| $2.50 | tax before discount — tell me, `pricing.taxable_base` changes |

### 2. $27 combo

`LARGE CHEESE` + pepperoni + `6PC WINGS` + `2 LITER`, then the combo coupon.

Expected: Sub Total $30.00 · Discount $3.00 · **Tax $1.98** · Total $28.98

Also worth noting: does PrISM show it as a $3 discount, or does it re-price the
lines? Either is fine — we only need the final number to match.

### 3. Free 2-liter

Two `LARGE CHEESE` + `2 LITER`, then the free-soda coupon.

Expected: Sub Total $34.00 · Discount $4.00 · **Tax $2.21** · Total $32.21

The question here is whether PrISM discounts $4.00 or zeroes the soda line.
Same total either way — but if the ticket shows the soda at $0.00, our ticket
layout should match so re-keying stays fast.

## Also worth asking the owner

The printed coupons all say **"must present to redeem."** A phone customer can't
present one. Does the store honor them over the phone?

- **Yes** → nothing changes; the AI applies them as built.
- **No** → set `COUPONS = []` in `lakewood/coupons.py` and the AI stops offering
  them. Better that than the agent promising $3 off and the counter refusing it.

This is a one-sentence answer and it decides whether the whole coupon path ships.
