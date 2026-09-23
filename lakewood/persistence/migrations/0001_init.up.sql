-- T-037 / ADR-014: multi-tenant schema, single Postgres instance.
--
-- Every table below carries `store_id` in its primary key, or references one
-- through a foreign key that does — there is no table a query can join or
-- select from without a tenant boundary. `store_id` is never accepted from a
-- tool, a model, or a client request (F2, unchanged and extended here); it
-- is only ever the value `config.py::store_for_did` already resolved before
-- any of this runs.
--
-- No ORM (CLAUDE.md: no new framework without clear need) — plain SQL,
-- applied by `migrations/runner.py`, tracked in `schema_migrations`.
-- `schema_migrations` itself is created by `runner.py` (CREATE TABLE IF NOT
-- EXISTS, before any numbered migration runs) — not by this file, so a
-- migration never has to guess whether it's the first one ever applied.

-- One row per tenant. Seeded once per store onboarding (out of scope: an
-- onboarding flow — this task's single design partner is inserted by the
-- migration runner itself, see `migrations/runner.py`).
CREATE TABLE stores (
    store_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    inbound_did  TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phone -> customer_id. Never the reverse — nothing else in this schema uses
-- a raw phone number as a key (see `customers.py`).
CREATE TABLE customers (
    store_id          TEXT NOT NULL REFERENCES stores(store_id),
    customer_id       TEXT NOT NULL,
    phone_normalized  TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, customer_id),
    UNIQUE (store_id, phone_normalized)
);

-- In-flight sessions (the cart). One row per call. `session_json` is the
-- full `Session` snapshot (`serialization.py`) — deliberately a JSONB blob
-- rather than a fully normalized cart/line-item schema: at MVP scale (~30
-- calls/day, one store) a normalized schema would be pure ceremony for zero
-- present benefit, and every column a query actually needs to filter/sort by
-- (state, turn, timestamps, tenant, customer) is still a real indexed
-- column, not buried in the blob. Revisit only if a real query need for
-- normalized cart line items shows up (event store / reconciliation phase).
CREATE TABLE sessions (
    store_id      TEXT NOT NULL REFERENCES stores(store_id),
    call_id       TEXT NOT NULL,
    customer_id   TEXT,
    from_number   TEXT NOT NULL,
    state         TEXT NOT NULL,
    turn          INTEGER NOT NULL DEFAULT 0,
    session_json  JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, call_id),
    FOREIGN KEY (store_id, customer_id) REFERENCES customers(store_id, customer_id)
        ON DELETE CASCADE
);

CREATE INDEX sessions_store_updated_idx ON sessions (store_id, updated_at);
CREATE INDEX sessions_store_customer_idx ON sessions (store_id, customer_id);

-- Confirmed orders. Insert-only for the ORDER ITSELF (F12) — a correction
-- after confirmation creates a new linked order (future phase), never edits
-- this one's content/total/ticket. One deliberate exception, added in
-- 0002_dispatch_status.up.sql: `dispatch_status`/`dispatched_at` ARE
-- updated post-insert — that's a physical-world fact settling after
-- confirmation, not a correction to the order.
CREATE TABLE confirmed_orders (
    store_id          TEXT NOT NULL REFERENCES stores(store_id),
    order_id          TEXT NOT NULL,
    call_id           TEXT NOT NULL,
    customer_id       TEXT,
    total_cents       INTEGER NOT NULL,
    ticket            TEXT NOT NULL,
    idempotency_key   TEXT,
    confirmed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_json      JSONB NOT NULL,
    PRIMARY KEY (store_id, order_id),
    FOREIGN KEY (store_id, customer_id) REFERENCES customers(store_id, customer_id)
        ON DELETE SET NULL
);

-- F6 across a restart: a replayed confirm with the same idempotency_key must
-- find this row, never insert a second order. Partial index — most calls
-- won't carry a key, and NULLs must never collide with each other.
CREATE UNIQUE INDEX confirmed_orders_idempotency_idx
    ON confirmed_orders (store_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX confirmed_orders_confirmed_at_idx ON confirmed_orders (store_id, confirmed_at);
