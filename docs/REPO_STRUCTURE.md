# Repository structure

The current layout is small and clean. **No reorganization is needed or
wanted right now.** This document defines where new code goes so it stays that
way.

```
lakewood/          domain core — no I/O, no providers, no framework
  menu.py          pure data. Every number verified against a POS screenshot.
  pricing.py       deterministic totals. No LLM, no network, no clock.
  coupons.py       four offers, mutually exclusive.
  orders.py        Session, state machine, and the 16 tools a model may call.
  printer.py       ESC/POS driver. The only hardware-touching module today.
  config.py        environment reads. No literals, no secrets.
tests/             pytest. Expected values are observed POS totals.
evals/             golden transcripts (text in → tool calls out) + runner.
docs/              PRD, architecture, plans, decisions, status.
data/              menu seed with provenance tags. Reference, not runtime.
scripts/           check.sh — the commit gate.
```

## Responsibilities

### `lakewood/` — domain core

**Belongs:** pricing rules, order state, menu data, tool definitions.

**Must NOT belong:** HTTP handlers, database calls, vendor SDKs, prompt text,
audio handling, anything that needs a network to run. The core must stay
testable with zero services running — that property is why the suite runs in
0.16 s and why it is trustworthy.

`printer.py` is the deliberate exception: it touches hardware, so it carries a
`dry_run` flag and is the only place a socket appears today.

### `tests/`

**Belongs:** behavior tests. Every expected money value must be traceable to a
real register reading.

**Must NOT belong:** tests that assert implementation detail, or coverage
padding. `test_pricing.py` is a regression record, not a unit test file — a
failure there means the world changed.

### `evals/`

**Belongs:** golden transcripts, the runner, metric definitions.

**Must NOT belong:** unit tests of Python functions. Evals score *model
behavior*; tests pin *code behavior*.

### `docs/`

**Belongs:** things a new agent must read. `decisions/` holds ADRs — append
only; supersede rather than edit.

**Must NOT belong:** meeting notes, speculation, or documentation of code that
does not exist without a `PLANNED` label.

### `data/`

Reference material with provenance tags. **Not read at runtime** — `menu.py` is
the runtime source. Keeping two copies is a known duplication; see below.

## Planned additions (do not create early)

```
lakewood/api.py         thin HTTP mapping onto orders.TOOLS. No business logic.  [Phase 2]
lakewood/store.py       SQLite persistence. Repository pattern, no ORM.          [Phase 3]
lakewood/dispatch.py    print queue, retries, held-order scheduler.              [Phase 4]
lakewood/voice/         provider adapter behind an interface.                    [Phase 5]
lakewood/prompts/       system prompt + few-shots, versioned.                    [Phase 5]
migrations/             reversible SQL.                                          [Phase 3]
```

Create each only when its phase begins. An empty directory is a lie about
progress.

## Known structural debt

| Issue | Severity | Plan |
|---|---|---|
| `data/menu-seed.yaml` duplicates `menu.py` and can drift | Low | Keep as provenance record; add a test asserting parity, or delete after Phase 1 |
| `docs/eval-harness-spec.md` overlaps `docs/EVALS.md` | Low | Superseded by EVALS.md; delete the spec |
| `printer.py` mixes protocol, formatting, and dispatch policy | Medium | Split dispatch policy into `dispatch.py` at Phase 4 |
| `orders.py` at 663 lines is the largest module | Low | Acceptable; split only if it passes ~900 |
