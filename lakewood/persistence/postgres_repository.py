"""
`PostgresSessionRepository` — the real adapter, for staging/production.

`psycopg2` is imported lazily, inside `__init__`, not at module import time —
the same pattern `stt/faster_whisper_provider.py` already uses for its own
optional dependency (ADR-008). Nothing else in this package, the full test
suite, or the T-016 offline gate imports this module unless a caller actually
constructs `PostgresSessionRepository`, so none of them need `psycopg2`
installed. See ADR-014 for why Postgres was chosen over SQLite despite this
project having exactly one store today, and why this class is verified here
only by schema-level static checks and contract tests skipped without a live
DSN (`tests/test_persistence_postgres_contract.py`) — no Postgres server is
reachable in this task's own environment.

Every query is parameterized (`%s` placeholders); `store_id` is never
string-formatted into SQL. Every table has `store_id` in its primary key or
its foreign key back to one that does, and every method here filters by it
explicitly — see `docs/decisions/ADR-014.md` and
`lakewood/persistence/migrations/0001_init.up.sql`.
"""

from __future__ import annotations

import uuid
from typing import Optional

from ..orders import Session, TERMINAL
from .customers import normalize_phone
from .repository import (
    ConfirmedOrder, ConfirmedOrderExists, SessionRepository,
)
from .serialization import session_from_dict, session_to_dict


class PostgresSessionRepository(SessionRepository):
    def __init__(self, dsn: str) -> None:
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as e:
            raise ImportError(
                "PostgresSessionRepository requires psycopg2-binary "
                "(pip install psycopg2-binary) — not part of the stdlib-only "
                "lakewood/ runtime policy, same as faster-whisper (ADR-008). "
                "The in-memory repository needs no extra dependency."
            ) from e
        self._psycopg2 = psycopg2
        self._Json = psycopg2.extras.Json
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = False

    def close(self) -> None:
        self._conn.close()

    # -- sessions -------------------------------------------------------

    def save_session(self, session: Session) -> None:
        if session.state == "CONFIRMED" or session.state in TERMINAL:
            raise ValueError(
                f"save_session refuses a {session.state} session — "
                f"call finalize_session for CONFIRMED.")
        phone = normalize_phone(session.from_number)
        customer_id = self.get_or_create_customer(session.store_id, phone)
        payload = session_to_dict(session)
        with self._conn, self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sessions
                    (store_id, call_id, customer_id, from_number, state, turn,
                     session_json, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (store_id, call_id) DO UPDATE SET
                    customer_id = EXCLUDED.customer_id,
                    state = EXCLUDED.state,
                    turn = EXCLUDED.turn,
                    session_json = EXCLUDED.session_json,
                    updated_at = now()
                """,
                (session.store_id, session.call_id, customer_id, session.from_number,
                 session.state, session.turn, self._Json(payload)),
            )

    def load_session(self, store_id: str, call_id: str) -> Optional[Session]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT session_json FROM sessions WHERE store_id = %s AND call_id = %s",
                (store_id, call_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return session_from_dict(row[0])

    def delete_session(self, store_id: str, call_id: str) -> None:
        with self._conn, self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE store_id = %s AND call_id = %s",
                (store_id, call_id),
            )

    def find_active_session_by_phone(self, store_id: str, from_number: str) -> Optional[Session]:
        phone = normalize_phone(from_number)
        terminal = tuple(TERMINAL)
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT s.session_json FROM sessions s
                JOIN customers c ON c.store_id = s.store_id AND c.customer_id = s.customer_id
                WHERE s.store_id = %s AND c.phone_normalized = %s
                  AND s.state != ALL(%s)
                ORDER BY s.updated_at DESC
                LIMIT 1
                """,
                (store_id, phone, list(terminal)),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return session_from_dict(row[0])

    def session_last_updated(self, store_id: str, call_id: str) -> Optional[float]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM updated_at) FROM sessions "
                "WHERE store_id = %s AND call_id = %s",
                (store_id, call_id),
            )
            row = cur.fetchone()
        return float(row[0]) if row else None

    def purge_expired_sessions(self, store_id: str, older_than_epoch: float) -> int:
        with self._conn, self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE store_id = %s "
                "AND updated_at < to_timestamp(%s)",
                (store_id, older_than_epoch),
            )
            return cur.rowcount

    # -- confirmed orders -------------------------------------------------

    def save_confirmed_order(self, record: ConfirmedOrder) -> None:
        with self._conn, self._conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO confirmed_orders
                        (store_id, order_id, call_id, customer_id, total_cents,
                         ticket, idempotency_key, confirmed_at, session_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), %s)
                    """,
                    (record.store_id, record.order_id, record.call_id, record.customer_id,
                     record.total_cents, record.ticket, record.idempotency_key,
                     record.confirmed_at, self._Json(record.session_snapshot)),
                )
            except self._psycopg2.errors.UniqueViolation as e:
                raise ConfirmedOrderExists(
                    f"order {record.order_id!r} for store {record.store_id!r} "
                    f"already exists — confirmed orders are immutable (F12)."
                ) from e

    _CONFIRMED_COLUMNS = (
        "store_id, order_id, call_id, customer_id, total_cents, ticket, "
        "idempotency_key, EXTRACT(EPOCH FROM confirmed_at), session_json, "
        "dispatch_status, EXTRACT(EPOCH FROM dispatched_at)"
    )

    def get_confirmed_order(self, store_id: str, order_id: str) -> Optional[ConfirmedOrder]:
        return self._select_confirmed(
            f"SELECT {self._CONFIRMED_COLUMNS} FROM confirmed_orders "
            "WHERE store_id = %s AND order_id = %s",
            (store_id, order_id))

    def get_confirmed_order_by_idempotency_key(
        self, store_id: str, idempotency_key: str) -> Optional[ConfirmedOrder]:
        return self._select_confirmed(
            f"SELECT {self._CONFIRMED_COLUMNS} FROM confirmed_orders "
            "WHERE store_id = %s AND idempotency_key = %s",
            (store_id, idempotency_key))

    def _select_confirmed(self, query: str, params: tuple) -> Optional[ConfirmedOrder]:
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
        if row is None:
            return None
        return ConfirmedOrder(
            store_id=row[0], order_id=row[1], call_id=row[2], customer_id=row[3],
            total_cents=row[4], ticket=row[5], idempotency_key=row[6],
            confirmed_at=float(row[7]), session_snapshot=row[8],
            dispatch_status=row[9], dispatched_at=float(row[10]) if row[10] is not None else None,
        )

    def finalize_session(self, session: Session, record: ConfirmedOrder) -> None:
        if record.store_id != session.store_id or record.call_id != session.call_id:
            raise ValueError("finalize_session: record does not match session")
        with self._conn:
            with self._conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO confirmed_orders
                            (store_id, order_id, call_id, customer_id, total_cents,
                             ticket, idempotency_key, confirmed_at, session_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), %s)
                        """,
                        (record.store_id, record.order_id, record.call_id, record.customer_id,
                         record.total_cents, record.ticket, record.idempotency_key,
                         record.confirmed_at, self._Json(record.session_snapshot)),
                    )
                except self._psycopg2.errors.UniqueViolation as e:
                    raise ConfirmedOrderExists(
                        f"order {record.order_id!r} for store {record.store_id!r} "
                        f"already exists — confirmed orders are immutable (F12)."
                    ) from e
                cur.execute(
                    "DELETE FROM sessions WHERE store_id = %s AND call_id = %s",
                    (session.store_id, session.call_id),
                )
            # single `with self._conn` block — one transaction, so a crash
            # between the insert and the delete is impossible; either both
            # commit or neither does.

    def purge_confirmed_orders(self, store_id: str, older_than_epoch: float) -> int:
        with self._conn, self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM confirmed_orders WHERE store_id = %s "
                "AND confirmed_at < to_timestamp(%s)",
                (store_id, older_than_epoch),
            )
            return cur.rowcount

    # -- dispatch status (T-049 FINAL) ---------------------------------------

    def mark_order_dispatched(self, store_id: str, order_id: str, dispatched_at: float) -> None:
        with self._conn, self._conn.cursor() as cur:
            cur.execute(
                "UPDATE confirmed_orders SET dispatch_status = 'DISPATCHED', "
                "dispatched_at = to_timestamp(%s) WHERE store_id = %s AND order_id = %s",
                (dispatched_at, store_id, order_id),
            )

    def mark_order_dispatch_failed(self, store_id: str, order_id: str) -> None:
        with self._conn, self._conn.cursor() as cur:
            cur.execute(
                "UPDATE confirmed_orders SET dispatch_status = 'FAILED' "
                "WHERE store_id = %s AND order_id = %s",
                (store_id, order_id),
            )

    def list_undispatched_confirmed_orders(self, store_id: str) -> list[ConfirmedOrder]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT {self._CONFIRMED_COLUMNS} FROM confirmed_orders "
                "WHERE store_id = %s AND dispatch_status != 'DISPATCHED' "
                "ORDER BY confirmed_at ASC",
                (store_id,),
            )
            rows = cur.fetchall()
        return [
            ConfirmedOrder(
                store_id=r[0], order_id=r[1], call_id=r[2], customer_id=r[3],
                total_cents=r[4], ticket=r[5], idempotency_key=r[6],
                confirmed_at=float(r[7]), session_snapshot=r[8],
                dispatch_status=r[9], dispatched_at=float(r[10]) if r[10] is not None else None,
            )
            for r in rows
        ]

    # -- customer identity --------------------------------------------------

    def get_or_create_customer(self, store_id: str, phone_normalized: str) -> str:
        with self._conn, self._conn.cursor() as cur:
            cur.execute(
                "SELECT customer_id FROM customers WHERE store_id = %s AND phone_normalized = %s",
                (store_id, phone_normalized),
            )
            row = cur.fetchone()
            if row:
                return row[0]
            customer_id = f"CUST-{uuid.uuid4().hex[:12].upper()}"
            cur.execute(
                "INSERT INTO customers (store_id, customer_id, phone_normalized, created_at) "
                "VALUES (%s, %s, %s, now())",
                (store_id, customer_id, phone_normalized),
            )
            return customer_id

    def delete_customer(self, store_id: str, customer_id: str) -> None:
        with self._conn, self._conn.cursor() as cur:
            # ON DELETE CASCADE (sessions) / SET NULL (confirmed_orders) —
            # see migrations/0001_init.up.sql — do the cascading, not Python.
            cur.execute(
                "DELETE FROM customers WHERE store_id = %s AND customer_id = %s",
                (store_id, customer_id),
            )
