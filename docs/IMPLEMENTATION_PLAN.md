# Implementation plan

Strict dependency order. **Do not start a phase whose exit criteria upstream are
unmet.** The single most valuable property of this project is that the entire
domain core is testable without voice, telephony, or hardware — phases are
ordered to preserve that as long as possible.

| Phase | Name | State |
|---|---|---|
| 0 | Repository stabilization | **DONE** |
| 1 | Deterministic domain core | **DONE** |
| 2 | Tool API layer | NEXT |
| 3 | Persistence | PLANNED |
| 4 | Ticket dispatch on real hardware | PLANNED (hardware ordered) |
| 5 | Voice + telephony | PLANNED |
| 6 | Eval corpus at scale | PLANNED (runs partly in parallel with 5) |
| 7 | Observability, cost, security | PLANNED |
| 8 | Staging | PLANNED |
| 9 | Overflow pilot | PLANNED |

---

## Phase 0 — Repository stabilization · DONE

**Objective.** A repo any agent can clone, install, and verify in under a minute.

**Delivered.** Package layout, `pytest.ini`, `requirements.txt`,
`scripts/check.sh`, git history, README.

**Exit criteria (met).** `./scripts/check.sh` green from a clean checkout.

---

## Phase 1 — Deterministic domain core · DONE

**Objective.** Prices, order state, and tool semantics that are correct without
any AI involved.

**Delivered.** `menu.py`, `pricing.py`, `coupons.py`, `orders.py`.
50 observed register totals reproduced exactly. 16 tools. 13 fail-safes.
111 passed / 2 xfailed.

**Exit criteria (met).** All ten PRD acceptance cases pass as text. No tool
accepts a price or a `store_id`, asserted by test.

**Residual.** `search_menu` and `check_availability` are untested (see
`docs/NEXT_TASKS.md` T-002). Coupon tax ordering is an assumption
(`docs/COUPON-VERIFICATION.md`).

---

## Phase 2 — Tool API layer · NEXT

**Objective.** Expose `orders.TOOLS` over HTTP so a voice provider can call it.

**Why now.** Every provider option in Phase 5 needs an HTTP tool endpoint. This
is the smallest piece of work that unblocks the most downstream choices, and it
requires no vendor decision.

**Dependencies.** Phase 1. None external.

**Deliverables.**
- `lakewood/api.py` — FastAPI app, one route per tool, generated from
  `orders.TOOLS` so the two can never drift.
- JSON schema per tool, emitted for provider registration.
- Shared-secret auth; `store_id` resolved from the inbound DID in the call
  payload, never from the body.
- `GET /healthz`.

**Files.** `lakewood/api.py` (new), `lakewood/config.py`, `requirements.txt`,
`tests/test_api.py` (new).

**Tests required.** Every tool reachable; auth rejects unsigned requests; a
request body containing `store_id` or `price` is rejected; schema round-trips.

**Acceptance.** A full order can be completed via HTTP calls alone.

**Exit.** `tests/test_api.py` green; schema file committed.

**Do NOT work on yet.** Prompts, provider SDKs, persistence, real audio.

**Risk.** Business logic leaking into route handlers. Mitigation: routes are
generated, not hand-written.

---

## Phase 3 — Persistence

**Objective.** A call that drops does not lose the order.

**Why now.** `Session` is in-memory; a process restart loses live calls. Must
land before real calls (Phase 5), not after.

**Dependencies.** Phase 2.

**Deliverables.** SQLite (WAL), tables per `ARCHITECTURE.md`, repository
functions, reversible migrations, dropped-call recovery by caller ID within
15 minutes, append-only `order_events`.

**Tests.** Session survives restart; idempotent confirm survives restart;
recovery offers resume; events are append-only.

**Exit.** Kill the process mid-order, restart, resume the same order.

**Do NOT.** Add an ORM. Add Postgres. Add multi-store schema.

---

## Phase 4 — Ticket dispatch on hardware

**Objective.** A confirmed order physically reaches the kitchen, verified.

**Why now.** Printer has been ordered. Without this, nothing reaches the store.

**Dependencies.** Phase 3 (held queue needs persistence).

**Deliverables.** `lakewood/dispatch.py` — retry policy, held-order scheduler,
`FAILED_DISPATCH` alerting. `printer.py` reduced to protocol + formatting.
First real print.

**Tests.** Retry on paper-out; `FAILED_DISPATCH` after 3; held order prints at
opening; ticket layout matches PrISM entry order.

**Acceptance.** A ticket printed on the dedicated unit is keyed into PrISM by a
staff member without asking a question.

**Exit.** Ten consecutive test orders print and reconcile.

**Blocker.** Hardware in transit. **Never test against the store's existing
printer** — PrISM owns that port.

---

## Phase 5 — Voice + telephony

**Objective.** A real phone call completes an order.

**Why now.** Only after tools, state, and paper all work.

**Dependencies.** Phases 2–4.

**Deliverables.** ADR-004 decided; provider adapter behind an interface;
telephony number with **overflow-only routing** and failover to the store line;
system prompt + few-shots, versioned; recording disclosure line; barge-in;
filler speech during tool calls.

**Tests.** L2 audio evals; latency budget measured per stage; failover verified
by killing the service mid-call.

**Exit.** 20 consecutive staff-placed test calls complete correctly.

**Do NOT.** Point at real customers.

---

## Phase 6 — Eval corpus at scale

**Objective.** Accuracy becomes a defended number, not a claim.

**Why now.** The harness exists with 10 cases; it must reach ~250 before pilot
numbers mean anything. Corpus writing runs in parallel with Phase 5.

**Deliverables.** ~250 cases per the bucket weights in `docs/EVALS.md`; L1 in
CI as a release gate; `score` mode wired to the Phase 5 adapter; per-case cost.

**Exit.** Gates in `docs/EVALS.md` enforced on every prompt or model change.

---

## Phase 7 — Observability, cost, security

Structured logs, metrics, alerts, per-call cost, rate limits, recording
retention policy, secret rotation. **Exit:** cost per call visible per store and
alerting on `FAILED_DISPATCH` proven by injection.

---

## Phase 8 — Staging

Full stack on a test DID with `PRINTER_DRY_RUN=1`. Store staff place calls.
**Exit:** one week, zero pricing disputes.

---

## Phase 9 — Overflow pilot

Route only calls that ring past 4 rings or hit busy. Zero downside for the
owner, and it produces the ROI number ("we recovered N calls you would have
lost"). Nightly reconciliation runs from day one.

**Exit:** two weeks; ≥98% modifier accuracy on production traffic; zero
unconfirmed orders submitted; owner willing to expand to full answering.
