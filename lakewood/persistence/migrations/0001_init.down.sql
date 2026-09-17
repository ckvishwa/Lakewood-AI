-- Reverses 0001_init.up.sql. Drop order matters: FKs before referents.
-- `schema_migrations` is owned by runner.py, not by this file — it stays,
-- so the runner can still record that this migration was reverted.
DROP TABLE IF EXISTS confirmed_orders;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS stores;
