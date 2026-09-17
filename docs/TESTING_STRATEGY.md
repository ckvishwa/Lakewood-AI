# Testing strategy

Coverage percentage is not a goal here. The goal is that a wrong total, a lost
order, or an unconfirmed order is impossible to ship without a red build.

## Layers

| Layer | What it pins | Where | Runtime | State |
|---|---|---|---|---|
| **Regression (observed reality)** | Every price the register produces | `tests/test_pricing.py` | <1s | **50 cases live** |
| **Unit / behavior** | FSM guards, tool contracts, fail-safes | `tests/test_orders.py`, `test_coupons.py`, `test_store_rules.py` | <1s | **live** |
| **Golden transcript (L1)** | Text in → tool calls out | `evals/` | ~3s | **10 cases, target 250** |
| **Contract** | HTTP layer matches `orders.TOOLS` | `tests/test_api.py` | — | PLANNED Phase 2 |
| **Integration** | Persistence, restart recovery | `tests/test_store.py` | — | PLANNED Phase 3 |
| **Hardware** | Real print + status readback | `tests/test_printer_hw.py` | — | PLANNED Phase 4, opt-in marker |
| **Audio (L2)** | ASR robustness, noise, accents | `evals/` | ~20m | PLANNED Phase 6 |
| **E2E** | Full call over SIP against staging | manual then scripted | — | PLANNED Phase 8 |
| **Latency** | Per-stage budget from `docs/PRD.md` | Phase 5 harness | — | PLANNED |
| **Failure injection** | Printer offline, provider down, mid-call kill | scripted | — | PLANNED Phase 7 |
| **Security** | No price/store_id params, no card retention | `tests/test_orders.py` | <1s | **live** |
| **Production smoke** | Health, one dry-run order, printer status | `scripts/smoke.sh` | — | PLANNED Phase 8 |

## The rule that matters most

`tests/test_pricing.py` is a **record of observed register behavior**, not a unit
test file. Every expected value came off a real POS screen.

**Never edit an expected value to make a test pass.** If one fails, either the
code is wrong or the store changed — and if the store changed, you need a new
screenshot before you touch the number. This is the single easiest way for an
agent to destroy the project's credibility.

The two `xfail(strict=True)` cases are documented POS behavior we cannot
reproduce (`docs/OPEN-QUESTIONS.md` §1). Strict means if they ever start
passing, the build goes red and tells us the store changed.

## Gates

| Before | Required |
|---|---|
| **Local commit** | `./scripts/check.sh` green |
| **Merge** | same, plus new/updated tests for every behavior change |
| **Staging deploy** | + contract, integration, and L1 eval gates from `docs/EVALS.md` |
| **Production deploy** | + L2 audio, latency budget met, failure injection passed, smoke green |

## What we do not do

- No mocking of the pricing engine. It is deterministic and fast; test the real
  thing.
- No fake implementations added to satisfy a test. If something needs hardware
  or a provider, mark it and skip it honestly.
- No asserting on log strings or internal call counts as a proxy for behavior.
- No snapshot tests of prompt output — that is what `evals/` is for.
