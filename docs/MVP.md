# MVP boundary

The purpose of this file is to stop V1 becoming a platform. If a proposed
feature is not below the line, it does not ship in V1.

## Must work before pilot

| Capability | State |
|---|---|
| Answer a call, take a pickup or delivery order | PLANNED (Phase 5) |
| Full pizza grammar: sizes, specialties, halves, intensity, removals | **DONE** |
| Deterministic totals matching the register | **DONE** — 50 observed totals |
| One coupon, never stacked | **DONE** |
| Explicit confirmation before any order is real | **DONE** |
| Ticket physically printed and verified | PLANNED (Phase 4) |
| Transfer to human on allergy, card, refund, confusion, request | **DONE** |
| Hours, delivery radius, delivery minimum | **DONE** |
| After-hours capture with mandatory disclosure | **DONE** (dispatch queue PLANNED) |
| Failover to the store's real line if we are down | PLANNED (Phase 5) |
| Nightly reconciliation: our quote vs the PrISM ticket | PLANNED (Phase 9) |

## Can stay manual

- **Staff re-key the ticket into PrISM.** This is the core MVP bet: the store
  already re-keys Slice and DoorDash by hand, so we match a workflow they run
  every day rather than demanding an integration.
- 86'ing items — a phone call or a tap, not a synced inventory feed.
- Menu updates — a code change. One store, rare changes.
- Refunds, complaints, disputes — transfer.
- Payment — cash or card at pickup/delivery. **We never take card numbers.**

## Integrations

| Integration | Status |
|---|---|
| Telephony (Twilio/Telnyx) | **Mandatory** |
| Voice provider (ASR/LLM/TTS) | **Mandatory** — ADR-004 pending |
| Dedicated ESC/POS printer | **Mandatory** — ordered |
| PrISM write | **Intentionally deferred.** No public API; MicroWorks brokers every integration. Not a V1 dependency. |
| PrISM menu read | Optional. Would cut onboarding cost; ask via the owner. |
| SMS confirmation | Deferred to V1.5 |
| Payments | Out of scope |

## Required before real callers

1. Ten consecutive test orders printed and keyed without a staff question.
2. Twenty staff-placed calls completed correctly.
3. Failover proven by killing the service mid-call.
4. Eval corpus ≥ 150 cases with gates enforced in CI.
5. Recording disclosure line live; retention policy set.
6. Cost per call logged and inside budget.

## Required before a paying customer

1. Two weeks of overflow pilot with ≥98% modifier accuracy on real traffic.
2. Zero unconfirmed orders submitted.
3. An ROI number the owner recognizes: calls recovered, after-hours orders
   captured.
4. Nightly reconciliation running with the documented POS variance excluded.
5. Alerting proven by injected failure.

## Explicitly not in V1

Multi-store · self-service onboarding · analytics dashboard · upsell automation
· SMS · loyalty · scheduled orders beyond the after-hours hold · any second POS
adapter · any second restaurant vertical.
