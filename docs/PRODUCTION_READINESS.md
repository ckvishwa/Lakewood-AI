# Production readiness

Three tiers. Do not do production work during the prototype phase — but do not
enter a pilot missing a pilot item.

Legend: **DONE** · PLANNED · N/A

## Required for prototype (where we are)

| Item | State |
|---|---|
| Deterministic pricing, verified against reality | **DONE** — 50 observed totals |
| Explicit state machine with guards | **DONE** |
| Structured errors, no exceptions into the model | **DONE** |
| Idempotent confirm | **DONE** |
| No writable price in any tool signature | **DONE** — asserted by test |
| Store scope not model-supplied | **DONE** — asserted by test |
| Card data never accepted or persisted | **DONE** |
| Config separate from code | **DONE** — `config.py`, env only |
| Reproducible local env | **DONE** — `pip install -r requirements.txt` |
| Commit gate | **DONE** — `scripts/check.sh` |

## Required for pilot

| Item | State | Phase |
|---|---|---|
| Persistence surviving restart | PLANNED | 3 |
| Dropped-call recovery | PLANNED | 3 |
| Reversible migrations | PLANNED | 3 |
| Print verified by device status | **DONE in code**, untested on hardware | 4 |
| `FAILED_DISPATCH` alerting a human | PLANNED | 4 |
| Held-order scheduler for after-hours | PLANNED | 4 |
| Shared-secret auth on the tool API | PLANNED | 2 |
| TLS everywhere | PLANNED | 2 |
| Secrets from env / manager, never committed | **DONE** |
| Recording disclosure before audio retention | PLANNED | 5 |
| **Automatic failover to the store's real line** | PLANNED | 5 |
| Structured JSON logs per tool call | PLANNED | 7 |
| Cost per call tracked per store | PLANNED | 7 |
| Rate limit per calling number | PLANNED | 7 |
| Health check endpoint | PLANNED | 2 |
| Nightly SQLite backup off-box | PLANNED | 7 |
| L1 eval gate in CI | PARTIAL — harness live, 10/250 cases | 6 |
| Staging validated for one week | PLANNED | 8 |
| Nightly reconciliation vs PrISM tickets | PLANNED | 9 |

**The failover item is not optional.** A caller reaching dead air because our
service is down is worse for the owner than never installing us.

## Required for production (post-pilot)

| Item | State |
|---|---|
| 99.9% availability measured | PLANNED |
| Metrics, tracing, dashboards | PLANNED |
| Alert runbook with on-call | PLANNED |
| Disaster recovery drill executed | PLANNED |
| Transcript retention policy enforced | PLANNED |
| Two-party consent handling if expanding beyond CT | PLANNED |
| Security review of the tool API | PLANNED |
| Cost alerting per store with a hard ceiling | PLANNED |
| Rollback procedure exercised | PLANNED |
| Graceful degradation: menu-only mode when the printer is down | PLANNED |
| Multi-store scoping | N/A for V1 |
