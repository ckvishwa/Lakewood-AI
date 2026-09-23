DROP INDEX IF EXISTS confirmed_orders_undispatched_idx;
ALTER TABLE confirmed_orders
    DROP COLUMN IF EXISTS dispatch_status,
    DROP COLUMN IF EXISTS dispatched_at;
