# ADR-005 — SQLite for persistence

**Status:** Superseded by [ADR-014](ADR-014-persistence-postgres-multitenant.md) · 2026-09-16 · Proposed · 2026-09-06

T-037 made the tenancy decision (multi-tenant schema, single-instance
infrastructure) ahead of implementation, which changes the calculus this ADR
was built on: SQLite's single-writer model is fine for one store's ~30
calls/day (the load this ADR correctly reasoned about) but a retrofit to
multi-tenant later is expensive enough that ADR-014 chose Postgres from the
start instead. This ADR is kept for the record of what was decided, when,
and why — it was the right call given what was known in 2026-09, not a
mistake.

## Context

Phase 3 needs durable sessions, orders, an append-only event log, and a held
queue for after-hours orders. One store, roughly 30 calls/day.

## Options

1. Keep in-memory (status quo).
2. SQLite, single file, WAL.
3. Postgres.
4. A managed cloud database.

## Decision

Option 2.

## Rationale

30 calls/day is three orders of magnitude below where SQLite strains. It needs
no separate service, backs up with a file copy, and keeps the local dev loop at
zero dependencies — which is why the current suite runs in 0.16 s.

## Tradeoffs

Single-writer; not suitable for multiple app instances. Fine for a single-store
pilot; revisit at roughly store #5 or when a second process needs writes.

## Consequences

- Repository functions, **no ORM.** Explicit SQL, reversible migrations.
- Nightly file snapshot off-box satisfies backup.
- The multi-store decision is deliberately postponed, not designed around.
