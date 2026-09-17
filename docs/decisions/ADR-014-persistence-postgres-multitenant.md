# ADR-014 — Persistence: Postgres, multi-tenant schema, recovery, retention

**Status:** Accepted · 2026-09-16

**Supersedes ADR-005** (SQLite for persistence). ADR-005 was written for a
single-store deployment and never implemented (`docs/ARCHITECTURE.md`'s
Storage row has stayed CURRENT: none since it was proposed). The tenancy
decision behind this ADR was made explicitly, ahead of this task, by the
same reasoning ADR-005 itself used in reverse: retrofitting tenancy after
data exists is expensive and its failure mode — one store reading another
store's orders — is bad enough to design against now, while there is
exactly one store's worth of data to migrate if the decision turns out
wrong.

## Context

T-037: land persistence before any real call. `Session` is in-memory today —
a dropped call loses the order, a process restart loses every order in
flight, and there is nothing to attach a customer to or reorder from. Every
later phase (voice, memory, event store, pilot) depends on this landing
first.

## Decision 1 — Postgres, single instance, over SQLite

**Postgres**, not SQLite, despite this task's own single design partner and
~30 calls/day — the same load ADR-005 correctly judged as three orders of
magnitude below where SQLite strains.

**Why the reversal:** ADR-005 optimized for the load this system has TODAY.
This decision optimizes for the schema shape the product needs once a second
store exists — multi-tenant, from the start, single-instance infrastructure
for now. A later SQLite -> Postgres migration is exactly the kind of dialect-
divergence risk CLAUDE.md warns against ("Do NOT run SQLite in dev and
Postgres in prod... produces bugs that only appear in production"); starting
on Postgres now means dev, staging, and any future production environment
are always the same engine. **Explicitly out of scope, per the task's own
boundary:** connection pooling tiers, row-level-security policies, tenant
provisioning flows, admin tooling, per-tenant config management, sharding,
replicas. This is one Postgres instance serving one store's traffic through
a schema that happens to already be tenant-shaped.

## Decision 2 — offline gate stays offline: two repository implementations

**Chosen: a repository/port interface, with an in-memory implementation for
tests and a Postgres implementation for real use** (the first of the two
options the task offered), not a containerized Postgres for integration
tests only.

**Why:** the in-memory implementation is what `tests/test_persistence_*.py`
and the T-016 offline gate actually run against — proven directly, not
assumed: `psycopg2` is not installed in this task's own dev environment, and
the full 472-test suite (including every persistence test) passes anyway.
`SessionRepository` (`lakewood/persistence/repository.py`) is the ABC;
`InMemorySessionRepository` and `PostgresSessionRepository` both implement it
in full. `PostgresSessionRepository` lazy-imports `psycopg2` inside
`__init__` — the exact pattern `stt/faster_whisper_provider.py` already
established for its own optional dependency (ADR-008) — so nothing outside
an actual `PostgresSessionRepository(dsn)` call needs it installed.

A containerized-Postgres-for-integration-tests approach was considered and
rejected as this task's PRIMARY verification path (though the machinery for
it exists — see Verification below): this dev environment has Docker
installed but its daemon is not reachable (`docker info` fails: `open
//./pipe/dockerDesktopLinuxEngine: The system cannot find the file
specified.`, checked directly). Building the offline gate's confidence on
"someone always has a working Docker daemon" would make it not actually
offline. The in-memory repository round-trips every `Session` through the
SAME `serialization.py` dict form the Postgres adapter serializes to JSON —
a (de)serialization bug shows up in the always-run suite, not only when a
real Postgres happens to be available.

**Both implementations are still real, not one real and one fake:**
`PostgresSessionRepository` is fully written (`postgres_repository.py`),
parameterized SQL only, one transaction for `finalize_session`. It is
verified today by (a) `test_persistence_postgres_contract.py`'s
import/construction tests, which run offline and pass, and (b) 6 static
schema tests (`test_persistence_schema.py`) that parse
`migrations/0001_init.up.sql` directly and prove every tenant table carries
`store_id` in its primary/unique keys and a foreign key back to `stores` —
with zero database server involved. **It has not been exercised against a
live Postgres server in this task** — no server was reachable in this
environment. `test_persistence_postgres_contract.py::TestLivePostgresContract`
is written and will run the moment `LAKEWOOD_POSTGRES_TEST_DSN` is set (in
CI or staging, both of which do have a real Postgres available). This is
stated as UNVERIFIED-against-a-live-server, not claimed as DONE, per
CLAUDE.md's own rule on unverified behavior.

## Decision 3 — schema shape: JSONB blob + indexed columns, not a fully normalized cart

`sessions.session_json` and `confirmed_orders.session_json` hold the full
`Session` snapshot (`serialization.py::session_to_dict`) as JSONB, alongside
real, indexed columns for everything a query actually needs to filter or
sort by today: `store_id`, `call_id`/`order_id`, `customer_id`, `state`,
`turn`, `updated_at`/`confirmed_at`, `idempotency_key`.

**Why not a fully normalized `order_lines`/`toppings` schema:** at MVP scale,
normalizing the cart into rows would be pure ceremony — every current
consumer (`recovery.py`'s revalidation, `render_ticket`, `Order.quote()`)
already operates on the in-memory `Session`/`Order` object graph, reloaded
whole via `session_from_dict`. A normalized schema would need an ORM or a
hand-rolled row<->object mapper to feed that same graph back, which is
exactly the ORM CLAUDE.md says not to add without a clear need. **Not
everything needs a row** (the task's own framing) — the row is the session;
the cart is a value inside it, not yet a query target of its own. Revisit
when the event store or nightly reconciliation phase needs to query line
items directly across sessions (`docs/ARCHITECTURE.md`'s TARGET
`order_events` table, still a later phase).

## Decision 4 — tenant isolation is structural, not conventional

Every table in `migrations/0001_init.up.sql` except `schema_migrations`
(which tracks migrations against the whole database, not tenant data) has
`store_id` in its primary key or a foreign key back to `stores(store_id)`.
Every `SessionRepository` method takes `store_id` as an explicit argument,
separate from any record id — never bundled into an opaque key a caller
could forge. A lookup for the wrong `store_id` returns "not found," the same
shape as any other miss.

**`store_id` is unsettable by any tool, model, or client input** — unchanged
from F2 (`tests/test_orders.py::test_f2_no_tool_accepts_store_id`), extended
to the persistence boundary: no `SessionRepository` method accepts a
`store_id` that didn't already come from a server-resolved `Session.store_id`
(`config.py::store_for_did`). Proven directly:
`tests/test_persistence_tenant_isolation.py` (7 tests — same call_id at two
tenants doesn't collide, wrong-store_id load returns None, phone-based resume
lookup is tenant-scoped, customer_ids don't leak across tenants, confirmed-
order lookup is tenant-scoped, purge only touches the named tenant, and a
directly-tampered stored blob still can't forge a cross-tenant read because
the repository's lookup key — not the blob's self-reported `store_id` — is
what's authoritative) and `test_persistence_schema.py` (6 tests, static SQL
analysis, no server needed).

## Decision 5 — recovery: offer resume, never silently continue

**A dropped call's cart is a proposal the next call gets to accept or
discard** — the same posture CLAUDE.md's memory policy takes toward anything
remembered about a customer ("every remembered thing is a proposal, re-
validated and re-priced before it is quoted"). `resume_or_create` finds the
candidate and revalidates it; it does not decide FOR the customer that they
want it back. Wiring the actual spoken/typed offer ("still have your large
pepperoni — pick that back up, or start fresh?") is voice/chat-layer work,
explicitly out of this task's scope (no telephony or chat-flow changes were
made) — this task builds and tests the machinery that layer will call
(`lakewood/persistence/service.py::resume_or_create`).

**Resume window: 30 minutes** (`recovery.RESUME_WINDOW_SECONDS`). Long enough
for a genuinely dropped call's customer to call back without re-ordering from
scratch; short enough that a session from hours earlier doesn't resurface as
a confusing surprise. Past the window, `find_resumable_session` returns
`None` — the row isn't gone (that's a separate, longer retention policy,
Decision 6), it's just not offered.

**Revalidation is unconditional, and so is re-pricing.** `cart_hash` (F5)
hashes cart *contents*, never the computed price — if the menu changed while
the call was dropped, the hash still matches even though `Order.total()`
would return a different number today. `revalidate_and_reprice`
(`recovery.py`) therefore: (a) checks every line against the CURRENT
`SIZES`/`ALL_TOPPINGS`/`UNAVAILABLE`/gourmet-number tables, exactly the
authority checks `add_item`/`add_modifier` themselves use, flagging (never
silently dropping — F7's "never guess," applied to our own stored data) any
line that's no longer valid or 86'd; and (b) unconditionally invalidates any
outstanding quote and reverts `QUOTED`/`AWAITING_CONFIRMATION` back to
`BUILDING`, regardless of whether anything was actually found wrong — a
clean cart_hash match is never treated as proof today's price is unchanged.
The customer always hears a fresh `request_quote` readback on resume, never a
cached number.

**`orders.py` itself is untouched.** No new `Session` field, no new
argument, no import of anything from `lakewood/persistence/`. Recovery is
built entirely as a side-car that calls the SAME tools
(`_gourmet_names`, `UNAVAILABLE`, `ALL_TOPPINGS`, `Topping.tier_rate`) the
live tool surface already uses — a resumed cart is checked by the identical
authority a fresh `add_item` call would be, not a second, parallel
validation implementation that could drift from it.

## Decision 6 — retention and deletion

Three different lifetimes, never conflated at a call site
(`lakewood/persistence/retention.py`):

| Data | Retention | Why |
|---|---|---|
| In-flight session (cart, phone number) | 24 hours (`SESSION_RETENTION_SECONDS`) | Kept briefly past the 30-min resume window for a same-day dispute ("I called and it never went through"), then purged. The most sensitive, least-justified-to-keep data this system holds — shortest life. |
| Confirmed order | 400 days (`CONFIRMED_ORDER_RETENTION_DAYS`) | The restaurant's own financial/audit record. A little over a year covers a same-time-last-year comparison and an ordinary dispute window. |
| Customer identity (phone -> customer_id) | No automatic expiry | Lives as long as the customer keeps calling. Removed only on request. |

**The confirmed-order retention period is this task's own assumption, not a
verified legal/business requirement** — flagged explicitly, same pattern as
T-003's still-open coupon-tax-ordering owner action. If the restaurant's
actual bookkeeping obligation differs, that's an owner question, not
something this task has standing to answer alone.

**Deletion path:** `delete_customer` removes the customer's identity row and
any in-flight session still tied to it (`ON DELETE CASCADE` in Postgres;
explicit cascade in `InMemorySessionRepository`). **Confirmed orders are NOT
deleted** — `customer_id` on that row is set to `NULL`
(`ON DELETE SET NULL`), severing the identity link while leaving the
restaurant's own transaction record intact. This is a deliberate tradeoff:
full erasure of a financial record on request would conflict with F12's
immutability guarantee and with ordinary bookkeeping practice; anonymizing
the identity link while preserving the transaction is the chosen middle
ground. Proven: `tests/test_persistence_memory_repository.py::
test_delete_customer_removes_in_flight_session_and_unlinks_confirmed_order`.

**Not stored, and not decided here:** raw call audio or full transcripts —
there is no voice layer yet, so this is moot for this task, but is flagged
as a retention decision the voice-layer task must make explicitly when it
lands, not inherit by default. **Never logged in full:** `customers.py::
mask_phone` returns last-4-digits-only for any log/print path; nothing in
this task's own code writes a full phone number to a log line (it goes to
`session_json`/`from_number` columns, not `print`/`log()` calls).

**Nothing here runs on a schedule.** `sweep_expired_sessions`/
`sweep_old_confirmed_orders` are deterministic functions a future cron/task-
runner calls — there is no scheduler in this codebase yet (event-store/
background-job infrastructure is a later phase). Building the functions is
this task's job; scheduling them is not.

## Alternatives considered

**SQLite, single store, as ADR-005 originally proposed.** Rejected — the
tenancy decision was made ahead of this task specifically to avoid the
retrofit cost ADR-005 didn't anticipate needing.

**A fully normalized cart schema (line items, toppings as rows) instead of a
JSONB blob.** Rejected for now — see Decision 3. Revisit when a real cross-
session query need over line items exists.

**Silent auto-resume (continue the old cart without asking).** Rejected —
violates the memory policy's own "propose, never assume" rule, and a
customer who meant to start over would have their abandoned order silently
reappear.

**No resume window — always offer whatever's on file.** Rejected — a
session from hours or days ago resurfacing unprompted is confusing, not
helpful; CLAUDE.md's own "context is built, not accumulated" principle
argues against carrying forward state whose relevance has expired.

## Consequences

- `ARCHITECTURE.md`'s Storage row moves from CURRENT: none to a documented
  Postgres schema; the TARGET diagram's `STORE_DB` node is now `Postgres`,
  not `SQLite`.
- `docs/decisions/ADR-005-sqlite-persistence.md` is marked Superseded by this
  ADR, not deleted — it recorded a real decision at the time, made with the
  information available then.
- `requirements.txt` gains `psycopg2-binary` as an optional dependency,
  documented the same way `faster-whisper` already is (lazy-imported, not
  part of the stdlib-only `lakewood/` runtime policy).
- No change to any existing domain test, `validate` (73/73), the rule-based
  ratchet (36/73), or pricing parity (50/50) — this task added a new,
  separate package (`lakewood/persistence/`) and did not modify `orders.py`,
  `pricing.py`, `menu.py`, or `coupons.py`.
- Wiring `resume_or_create`/`confirm_and_persist` into `chat.py`'s text
  sandbox or a real voice/telephony call loop is explicitly NOT done here —
  next task's scope.
