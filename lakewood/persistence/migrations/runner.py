"""
Tiny migration runner — no framework (CLAUDE.md: no new framework without
clear need; this is ~40 lines because the project needs "run these .sql files
in order, once, and let me undo the last one," nothing more).

    python -m lakewood.persistence.migrations.runner up   "$DATABASE_URL"
    python -m lakewood.persistence.migrations.runner down "$DATABASE_URL"

`psycopg2` is lazy-imported, same as `postgres_repository.py` — this module
is never imported by the test suite or the T-016 offline gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ...config import CONFIG

_DIR = Path(__file__).resolve().parent


def _migrations(direction: str) -> list[tuple[str, Path]]:
    suffix = f".{direction}.sql"
    found = sorted(_DIR.glob(f"*{suffix}"))
    return [(p.name[: -len(suffix)], p) for p in found]


def migrate_up(dsn: str) -> None:
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        for version, path in _migrations("up"):
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            with conn, conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) "
                    "ON CONFLICT DO NOTHING",
                    (version,),
                )
            print(f"applied {version}")
        with conn, conn.cursor() as cur:
            # Single design partner today — seed its row so `sessions`/
            # `customers` foreign keys have something to point at without a
            # manual admin step. Out of scope: a real onboarding flow for a
            # second store (T-037 is explicitly single-store-in-multi-tenant-
            # schema; see ADR-014).
            cur.execute(
                "INSERT INTO stores (store_id, name, inbound_did) VALUES (%s, %s, %s) "
                "ON CONFLICT (store_id) DO NOTHING",
                (CONFIG.store_id, CONFIG.name, CONFIG.inbound_did),
            )
    finally:
        conn.close()


def migrate_down(dsn: str) -> None:
    """Reverts exactly one migration — the most recently applied one. Never
    reverts more than one per invocation; run it again to go further back."""
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
            )
            row = cur.fetchone()
        if row is None:
            print("nothing to revert")
            return
        version = row[0]
        down_path = _DIR / f"{version}.down.sql"
        if not down_path.exists():
            raise FileNotFoundError(f"no down migration for {version}: {down_path}")
        with conn, conn.cursor() as cur:
            cur.execute(down_path.read_text(encoding="utf-8"))
            cur.execute("DELETE FROM schema_migrations WHERE version = %s", (version,))
        print(f"reverted {version}")
    finally:
        conn.close()


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("up", "down"):
        print(__doc__)
        sys.exit(1)
    (migrate_up if sys.argv[1] == "up" else migrate_down)(sys.argv[2])


if __name__ == "__main__":
    main()
