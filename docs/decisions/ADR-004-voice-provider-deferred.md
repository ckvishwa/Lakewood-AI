# ADR-004 — Voice provider: buy, don't build; decision deferred to Phase 5

**Status:** Closed · 2026-09-06, closed 2026-09-24 (T-053 Phase 2 Part 0).
The STT/TTS legs of the original cascade are self-hosted (ADR-016, ADR-020),
not bought — this ADR's own cost table assumed a bought stack for those
legs and is superseded there. The one leg still bought is telephony
transport, decided in **ADR-021** (Twilio) — no separate ADR-004a needed;
ADR-021 is the vendor record this ADR asked for.

## Context

The system needs telephony, ASR, turn-taking, and TTS. None of these are our
moat — the pizza order grammar and the store-specific eval corpus are.

## Constraint that decides it

COGS must stay ≤ 25% of ARPU. At ~3,150 min/month:

| Stack | ~$/min | Monthly | Margin @ $299 |
|---|---|---|---|
| Cascaded (ASR + small LLM + TTS) | 0.02 | $63 | 79% |
| Realtime "mini" class | 0.03 | $95 | 68% |
| Flagship speech-to-speech | 0.09 | $284 | **5% — non-viable** |

**Model choice is the business model.**

## Decision

Buy the voice stack; do not build audio infrastructure. Specific vendor deferred
to Phase 5 and must be recorded as ADR-004a when chosen. Whatever is chosen sits
behind an interface in `lakewood/voice/`; no vendor type may appear in
`pricing.py`, `orders.py`, or `menu.py`.

## Rationale

Deferring costs nothing — Phases 2–4 are provider-agnostic — and pricing in this
category moves fast enough that deciding late is an advantage.

## Consequences

- Cost per call must be logged from the first call.
- Any provider or model change is gated by `docs/EVALS.md`, not manual testing.
- Switching providers must be a Phase-5-only change.
