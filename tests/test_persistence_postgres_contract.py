"""
`PostgresSessionRepository` contract tests. Two tiers:

1. Import/construction tests that run ALWAYS, offline — proving the module
   never requires `psycopg2` unless something actually tries to connect
   (lazy import, ADR-008's own pattern).
2. Full behavioral contract tests, run against a REAL Postgres — skipped
   unless `LAKEWOOD_POSTGRES_TEST_DSN` is set. No Postgres server is
   reachable in this task's own dev environment (Windows, no services,
   Docker Desktop not running — checked directly, not assumed), so tier 2 is
   UNVERIFIED here; tier 1 plus `test_persistence_schema.py`'s static
   analysis are what's actually been run. See ADR-014.
"""

from __future__ import annotations

import os

import pytest

DSN = os.environ.get("LAKEWOOD_POSTGRES_TEST_DSN")


def test_postgres_repository_module_imports_without_psycopg2_installed():
    """The module itself must be importable even when psycopg2 isn't
    installed — only actually constructing a repository should require it."""
    import importlib
    import sys

    mod_name = "lakewood.persistence.postgres_repository"
    sys.modules.pop(mod_name, None)
    mod = importlib.import_module(mod_name)
    assert hasattr(mod, "PostgresSessionRepository")


def test_constructing_without_psycopg2_raises_a_clear_error(monkeypatch):
    import builtins

    from lakewood.persistence.postgres_repository import PostgresSessionRepository

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg2" or name.startswith("psycopg2."):
            raise ImportError("simulated: psycopg2 not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match="psycopg2-binary"):
        PostgresSessionRepository("postgresql://localhost/doesnotmatter")


pytestmark_live = pytest.mark.skipif(
    not DSN, reason="LAKEWOOD_POSTGRES_TEST_DSN not set — no live Postgres in this environment")


@pytestmark_live
class TestLivePostgresContract:
    """Same behavioral contract `test_persistence_memory_repository.py`
    proves against `InMemorySessionRepository`, run against a real
    Postgres when one is available. Kept as a small, representative subset
    (not a full duplicate of every in-memory test) — the point is proving
    the SAME repository interface behaves the same way against the real
    adapter, not re-deriving domain coverage a second time."""

    @pytest.fixture(autouse=True)
    def _schema(self):
        from lakewood.persistence.migrations.runner import migrate_up
        migrate_up(DSN)

    @pytest.fixture
    def repo(self):
        from lakewood.persistence.postgres_repository import PostgresSessionRepository
        r = PostgresSessionRepository(DSN)
        yield r
        r.close()

    def test_save_load_round_trip(self, repo):
        from lakewood import orders as oe
        from lakewood.orders import add_item, set_order_type

        sess = oe.Session(call_id="PG-TEST-1", store_id="STORE-001",
                          from_number="+12035551234")
        set_order_type(sess, "pickup")
        add_item(sess, "PIZZA", size="LARGE")
        repo.save_session(sess)
        loaded = repo.load_session("STORE-001", "PG-TEST-1")
        assert loaded.order.subtotal() == sess.order.subtotal()
        repo.delete_session("STORE-001", "PG-TEST-1")

    def test_cross_tenant_read_returns_none(self, repo):
        from lakewood import orders as oe
        from lakewood.orders import add_item, set_order_type

        sess = oe.Session(call_id="PG-TEST-2", store_id="STORE-001",
                          from_number="+12035551234")
        set_order_type(sess, "pickup")
        add_item(sess, "PIZZA", size="LARGE")
        repo.save_session(sess)
        assert repo.load_session("STORE-002", "PG-TEST-2") is None
        repo.delete_session("STORE-001", "PG-TEST-2")
