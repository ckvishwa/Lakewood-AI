"""
Static analysis of the migration SQL itself — offline, no database server,
no network, no container. Proves the SCHEMA enforces tenant scoping even in
an environment (like this one) where no live Postgres is reachable to run a
real integration test against. See `test_persistence_postgres_contract.py`
for the live-DSN-only contract tests this complements, and ADR-014 for why
both exist.
"""

from __future__ import annotations

import re
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "lakewood" / "persistence" / "migrations"
_UP_SQL = (_MIGRATIONS_DIR / "0001_init.up.sql").read_text(encoding="utf-8")

_TABLE_RE = re.compile(
    r"CREATE TABLE (\w+)\s*\((.*?)\n\);", re.DOTALL,
)

# `schema_migrations` is owned by `migrations/runner.py`, not this file (see
# its own comment) — deliberately tenant-less, tracks which migrations ran
# against the whole database, not per-tenant data. `stores` IS the tenant
# root: its own `store_id TEXT PRIMARY KEY` (inline, not a table-level
# constraint) trivially satisfies "scoped by store_id" by definition, so it's
# exempted from the constraint-shape check below, not from having store_id.
_TENANT_LESS_TABLES: set[str] = set()
_TENANT_ROOT_TABLE = "stores"


def _tables() -> dict[str, str]:
    return {name: body for name, body in _TABLE_RE.findall(_UP_SQL)}


def test_migration_file_is_parseable_and_defines_the_expected_tables():
    tables = _tables()
    assert set(tables) == {"stores", "customers", "sessions", "confirmed_orders"}


def test_every_tenant_table_has_a_store_id_column():
    for name, body in _tables().items():
        if name in _TENANT_LESS_TABLES:
            continue
        assert re.search(r"\bstore_id\s+TEXT\b", body), \
            f"{name} has no store_id column"


def test_every_tenant_table_scopes_its_primary_or_unique_keys_by_store_id():
    """A store_id column alone isn't the guarantee — an unscoped PRIMARY KEY
    or UNIQUE constraint could still let one tenant's insert collide with, or
    a naive query forget to filter, another tenant's row. Every constraint
    that identifies a row must include store_id."""
    for name, body in _tables().items():
        if name in _TENANT_LESS_TABLES or name == _TENANT_ROOT_TABLE:
            continue
        constraints = re.findall(r"(PRIMARY KEY|UNIQUE)\s*\(([^)]*)\)", body)
        assert constraints, f"{name} defines no PRIMARY KEY/UNIQUE constraint"
        for kind, cols in constraints:
            col_list = [c.strip() for c in cols.split(",")]
            assert "store_id" in col_list, \
                f"{name}'s {kind} ({cols}) does not include store_id — " \
                f"a cross-tenant collision or unscoped uniqueness check is possible"


def test_stores_table_itself_is_the_tenant_root_not_referenced_circularly():
    tables = _tables()
    assert "REFERENCES stores" not in tables["stores"]


def test_customers_sessions_and_orders_all_reference_stores():
    tables = _tables()
    for name in ("customers", "sessions", "confirmed_orders"):
        assert "REFERENCES stores(store_id)" in tables[name], \
            f"{name} does not foreign-key back to stores"


def test_no_tool_or_migration_lets_a_client_supply_store_id_at_write_time():
    """The repository layer (not SQL alone) is what prevents this in
    practice — `postgres_repository.py`'s `save_session`/`get_or_create_customer`
    always take `store_id` as a caller-supplied Python argument resolved from
    `Session.store_id` (itself server-bound, F2), never parsed out of a
    request body inside this SQL. This test is a documentation-linked
    tripwire: if a future migration adds a `DEFAULT` or trigger that could
    let store_id silently take a client-controlled value, this fails."""
    assert "store_id TEXT NOT NULL DEFAULT" not in _UP_SQL.replace("\n", " ")
