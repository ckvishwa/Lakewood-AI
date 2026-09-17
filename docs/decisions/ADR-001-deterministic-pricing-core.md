# ADR-001 — The model never computes a price

**Status:** Accepted · 2026-09-06

## Context

The agent must quote totals a customer will pay. A restaurant POS applies
size-tiered toppings, specialty flat prices, split-pizza rules, coupons, and
tax. An LLM asked to compute this will be right most of the time, which is the
worst possible failure profile.

## Problem

Where does pricing authority live?

## Options

1. LLM computes totals from menu text in context.
2. LLM computes, deterministic engine verifies.
3. Deterministic engine computes; the LLM cannot express a price at all.

## Decision

Option 3. `lakewood/pricing.py` owns every number. No tool in `orders.TOOLS`
accepts or returns a writable price, asserted by `test_f1_no_tool_accepts_a_price`.
Unknown prices raise rather than estimate.

## Rationale

A wrong total is a counter argument, a refund, and a lost account. The rules are
finite and were fully derived from 50 observed register totals — there is no
reason to make them probabilistic.

## Tradeoffs

Every new menu rule needs code and a screenshot, not a prompt edit. Onboarding a
second store costs engineering time. Accepted: correctness first.

## Consequences

- `tests/test_pricing.py` is a regression record, not a unit test file.
- The fail-loud path must stay wired even when all tiers are known.
- Menu data must never be inferred from a photo without verification.
