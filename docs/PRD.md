# PRD — Lakewood Voice

**Status: CURRENT.** Scope changes require editing this file in the same commit.

## Vision

A pizza restaurant should never lose an order because nobody could get to the
phone. An AI answers, takes the order accurately, and hands the kitchen a ticket
staff can key in faster than they could have taken the call.

## Problem

Independent pizza shops still take a large share of orders by phone. During a
rush this is where the business leaks:

- staff leave the counter or the oven to answer
- simultaneous calls cannot be handled; callers hang up
- after-hours calls go entirely unanswered — **at our design-partner store this
  is confirmed lost revenue with no capture mechanism at all**
- upsells get forgotten
- complex modifiers are mis-taken

Generic AI receptionists do not handle pizza order grammar — halves, intensity
modifiers, specialty numbering, per-size topping tiers.

## Target customer, user, buyer

- **Customer (business):** independent pizza restaurants, 1–5 locations, 30+
  calls/day, complex menu, legacy or fragmented POS.
- **Design partner:** Lakewood Pizza, 562 Lakewood Rd, Waterbury CT. MicroWorks
  PrISM 8.1.153. Slice and DoorDash arrive on separate tablets and are **re-keyed
  by hand** into PrISM.
- **User (caller):** a hungry person who wants a fast, correct order.
- **User (staff):** whoever keys the ticket into PrISM.
- **Buyer:** the owner.

## Primary use cases (V1)

1. Take a pickup or delivery order, quote a correct total, confirm, print a
   ticket.
2. Answer hours, address, delivery radius, delivery minimum.
3. Apply exactly one coupon.
4. Capture after-hours orders and hold them until opening.
5. Transfer to a human when required.

## Non-goals

Explicitly **not** building: a POS · a delivery marketplace · a Slice or
DoorDash competitor · inventory · scheduling · accounting · CRM · a generic AI
receptionist · payment processing.

## V1 scope

**In:** voice answering, menu Q&A, order build with full pizza grammar,
deterministic pricing, coupons, confirmation, ticket printing, human transfer,
call logging, after-hours capture, overflow-only routing.

**Out:** taking card numbers (hard rule — see Security), PrISM write
integration, multi-store, self-service onboarding, analytics dashboard,
SMS confirmation, upsell automation.

## Post-V1

Printer bridge redundancy · SMS confirmation · upselling · PrISM adapter if
MicroWorks ever exposes an interface · Toast/Square/Clover adapters ·
multi-location · self-service onboarding.

## Success metrics

**North star:** completed phone orders per restaurant per week.

| Metric | Target | Notes |
|---|---|---|
| Modifier accuracy | ≥ 98% | denominator defined in `docs/EVALS.md` |
| Item accuracy | ≥ 99% | |
| Hallucinated menu items | 0 | hard gate |
| Orders confirmed without explicit assent | 0 | hard gate |
| Quote matches register | ≥ 95% | excluding documented POS variance |
| AI completion rate (no transfer) | ≥ 70% | |
| After-hours orders captured/week | measured | currently zero; pure upside |

## Reliability targets

- **Pilot:** best effort. Automatic failover to the store's real line if the
  service is down — a caller must never hit dead air.
- **Production:** 99.9% after maturity.
- A confirmed order must never be silently lost. Dispatch failure pages a human.

## Latency targets

| Stage | Budget |
|---|---|
| Tool call (in-process) | < 50 ms |
| ASR endpoint → first TTS byte | < 1.5 s |
| Perceived turn latency | < 2 s |
| Max call duration | 8 min (enforced in `orders.py`) |

## Cost constraints

**COGS must stay ≤ 25% of ARPU.** At ~30 calls/day × 3.5 min ≈ 3,150 min/month:

| Stack | ~$/min | Monthly COGS | Margin at $299 |
|---|---|---|---|
| Cascaded (ASR + small LLM + TTS) | 0.02 | $63 | 79% |
| Realtime "mini" class | 0.03 | $95 | 68% |
| Flagship speech-to-speech | 0.09 | $284 | **5% — non-viable** |

**Model choice is the business model.** Cost per call must be logged per store
from day one.

## Security requirements

- **The agent never accepts card numbers.** Card-shaped input triggers immediate
  transfer and the text is not persisted (`orders.screen_utterance`).
- `store_id` is bound to the inbound DID server-side, never a model parameter.
- No tool accepts or returns a writable price.
- TLS everywhere; secrets in environment or a secret manager, never committed.
- Call recording requires a disclosure line before any audio is retained.

## Privacy requirements

- Store the minimum: caller number, name, delivery address, order contents.
- No card data, ever.
- Transcript retention policy must be set before pilot (default 30 days).
- Allergy mentions transfer to a human; the agent makes no allergen assurances.

## Operational requirements

- Staff can 86 an item in one tap.
- Failure to print must alert a human, not vanish.
- The store's existing printer is owned by PrISM — we use a **dedicated** unit.

## Why this should exist

Voice-AI-for-restaurants is a crowded category, and pizza is the acknowledged
early-adopter segment. "Pizza-first" alone is not differentiation. What is
defensible:

1. **A measured accuracy claim** backed by a store-specific eval corpus that a
   competitor cannot copy in an afternoon.
2. **Legacy-POS pragmatism.** We match the re-key workflow the store already
   runs for Slice and DoorDash rather than demanding an integration.
3. **After-hours capture**, which is currently zero revenue for this store.

## Assumptions still requiring validation

| Assumption | How we find out |
|---|---|
| Customers will complete a pizza order with an AI | pilot completion rate |
| Owner will pay ~$299/mo | after the ROI number exists, not before |
| A re-keyed AI ticket is faster than a Slice re-key | **stopwatch both at the store — not yet done** |
| Coupons are honored by phone | one question to the owner (`docs/COUPON-VERIFICATION.md`) |
| Tax applies after coupon discount | three test orders at the register |
