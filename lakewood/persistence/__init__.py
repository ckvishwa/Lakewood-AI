"""
Persistence layer — T-037.

Nothing in here is imported by `orders.py`, `pricing.py`, or `menu.py`. This
package is a side-car that knows how to save/load a `Session` (see
`serialization.py`); the domain module stays exactly as it was, in-memory and
provider-agnostic, matching CLAUDE.md's rule that persistence types never leak
into the domain layer.

Two `SessionRepository` implementations exist (`repository.py` defines the
interface):

  - `memory_repository.InMemorySessionRepository` — dict-backed, zero
    dependencies. Used by the full test suite and the T-016 offline gate,
    which must keep running with no database server, no network, no
    container (see docs/decisions/ADR-014).
  - `postgres_repository.PostgresSessionRepository` — real Postgres, lazy-
    imports `psycopg2` so nothing else in this package (or the offline gate)
    needs it installed, the same pattern `stt/faster_whisper_provider.py`
    already uses for its optional dependency.

`recovery.py` and `retention.py` implement the product decisions recorded in
ADR-014 (resume window, revalidate-before-quoting, purge/deletion policy).
`customers.py` normalizes phone numbers to a tenant-scoped `customer_id`.
`service.py` is the thin orchestration layer chat/voice code calls; it is the
only place that calls both `orders.py` and a repository in the same function.
"""
