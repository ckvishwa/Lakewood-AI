# ADR-006 — Pilot on overflow calls only

**Status:** Accepted · 2026-09-06

## Context

Asking an owner to route all phone traffic to an unproven AI is a large trust
ask, and it makes the ROI unmeasurable — you cannot count calls you would have
missed once you stop missing them.

## Options

1. Full call answering from day one.
2. Shadow mode: listen, suggest, never speak.
3. Overflow only: rings past 4 rings, or busy.

## Decision

Option 3.

## Rationale

Zero downside for the owner — those calls are lost today. It produces the ROI
number for free: *"we recovered 41 calls you would have lost last week."* And it
solves the PRD's otherwise-unmeasurable "missed calls avoided" metric, because
the denominator is exactly the calls that overflowed.

## Tradeoffs

Lower call volume means slower eval-corpus growth from production traffic.
Accepted — corpus growth is not the bottleneck; trust is.

## Consequences

- Telephony must support conditional forwarding on no-answer and busy.
- After-hours capture pairs naturally: also currently-zero revenue.
- Expansion to full answering is an explicit later decision with its own gate.
