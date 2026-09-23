-- T-049 FINAL: durable dispatch status. Closes the crash-between-finalize-
-- and-dispatch gap (docs/STATUS.md's T-049 entries) — without this, a
-- replayed confirm after a process crash hits the idempotency-key cache
-- (service.py::confirm_and_persist) and returns before ever reaching the
-- dispatch call again, so a confirmed, PAID order could silently never
-- reach the kitchen with no durable trace anywhere.
--
-- This is the ONE deliberate exception to confirmed_orders' insert-only
-- design (0001_init.up.sql's own comment). Dispatch status is a physical-
-- world fact that settles AFTER confirmation, not a correction to the
-- order — the order's own content/total/ticket remain exactly as
-- immutable as before; only this one new column is ever updated post-insert.
ALTER TABLE confirmed_orders
    ADD COLUMN dispatch_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (dispatch_status IN ('PENDING', 'DISPATCHED', 'FAILED')),
    ADD COLUMN dispatched_at TIMESTAMPTZ;

-- Staff/ops visibility — "which confirmed orders never reached the
-- kitchen" — the query T-049 FINAL requires ("staff can SEE a held-for-
-- print order"). Reuses this column rather than a separate held_orders
-- table: ARCHITECTURE.md lists `held_orders` under "Not yet built" (no
-- table, no code exists there to reuse) — this is the smallest correct
-- thing instead, per that task's own explicit fallback instruction.
CREATE INDEX confirmed_orders_undispatched_idx
    ON confirmed_orders (store_id, confirmed_at)
    WHERE dispatch_status != 'DISPATCHED';
