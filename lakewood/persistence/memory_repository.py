"""
`InMemorySessionRepository` — the only repository the test suite and the
T-016 offline gate ever touch. No file, no network, no external process.

Round-trips every `Session` through `serialization.py`'s dict form on every
save/load, exactly like `PostgresSessionRepository` does through JSON — a
save/load bug in the (de)serializer shows up here too, not only when a real
Postgres happens to be available (see ADR-014, "why the in-memory adapter
still serializes").
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from ..orders import Session, TERMINAL
from .customers import normalize_phone
from .repository import (
    ConfirmedOrder, ConfirmedOrderExists, SessionRepository,
)
from .serialization import session_from_dict, session_to_dict


class InMemorySessionRepository(SessionRepository):
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], dict] = {}          # (store_id, call_id) -> session dict
        self._updated_at: dict[tuple[str, str], float] = {}
        self._session_customer: dict[tuple[str, str], str] = {}   # (store_id, call_id) -> customer_id
        self._confirmed: dict[tuple[str, str], ConfirmedOrder] = {}  # (store_id, order_id)
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}  # (store_id, key) -> (store_id, order_id)
        self._customers: dict[tuple[str, str], str] = {}          # (store_id, phone) -> customer_id
        self._customer_phone: dict[tuple[str, str], str] = {}     # (store_id, customer_id) -> phone

    # -- sessions -------------------------------------------------------

    def save_session(self, session: Session) -> None:
        if session.state == "CONFIRMED" or session.state in TERMINAL:
            raise ValueError(
                f"save_session refuses a {session.state} session — "
                f"call finalize_session for CONFIRMED, or leave a terminal "
                f"transfer/cancel row as its last saved in-flight state only "
                f"if it was saved BEFORE the terminal transition.")
        key = (session.store_id, session.call_id)
        customer_id = self.get_or_create_customer(
            session.store_id, normalize_phone(session.from_number))
        self._sessions[key] = session_to_dict(session)
        self._updated_at[key] = time.time()
        self._session_customer[key] = customer_id

    def load_session(self, store_id: str, call_id: str) -> Optional[Session]:
        row = self._sessions.get((store_id, call_id))
        if row is None:
            return None
        return session_from_dict(row)

    def delete_session(self, store_id: str, call_id: str) -> None:
        key = (store_id, call_id)
        self._sessions.pop(key, None)
        self._updated_at.pop(key, None)
        self._session_customer.pop(key, None)

    def find_active_session_by_phone(self, store_id: str, from_number: str) -> Optional[Session]:
        phone = normalize_phone(from_number)
        candidates = []
        for (sid, call_id), row in self._sessions.items():
            if sid != store_id or normalize_phone(row["from_number"]) != phone:
                continue
            if row["state"] in TERMINAL:
                continue
            candidates.append((self._updated_at[(sid, call_id)], call_id))
        if not candidates:
            return None
        candidates.sort()
        _, call_id = candidates[-1]
        return self.load_session(store_id, call_id)

    def session_last_updated(self, store_id: str, call_id: str) -> Optional[float]:
        return self._updated_at.get((store_id, call_id))

    def purge_expired_sessions(self, store_id: str, older_than_epoch: float) -> int:
        doomed = [
            key for key, ts in self._updated_at.items()
            if key[0] == store_id and ts < older_than_epoch
        ]
        for key in doomed:
            self._sessions.pop(key, None)
            self._updated_at.pop(key, None)
            self._session_customer.pop(key, None)
        return len(doomed)

    # -- confirmed orders -------------------------------------------------

    def save_confirmed_order(self, record: ConfirmedOrder) -> None:
        key = (record.store_id, record.order_id)
        if key in self._confirmed:
            raise ConfirmedOrderExists(
                f"order {record.order_id!r} for store {record.store_id!r} "
                f"already exists — confirmed orders are immutable (F12).")
        self._confirmed[key] = record
        if record.idempotency_key:
            self._idempotency[(record.store_id, record.idempotency_key)] = key

    def get_confirmed_order(self, store_id: str, order_id: str) -> Optional[ConfirmedOrder]:
        return self._confirmed.get((store_id, order_id))

    def get_confirmed_order_by_idempotency_key(
        self, store_id: str, idempotency_key: str) -> Optional[ConfirmedOrder]:
        key = self._idempotency.get((store_id, idempotency_key))
        if key is None:
            return None
        return self._confirmed.get(key)

    def finalize_session(self, session: Session, record: ConfirmedOrder) -> None:
        if record.store_id != session.store_id or record.call_id != session.call_id:
            raise ValueError("finalize_session: record does not match session")
        self.save_confirmed_order(record)          # raises first if it already exists — nothing else mutates
        self.delete_session(session.store_id, session.call_id)

    def purge_confirmed_orders(self, store_id: str, older_than_epoch: float) -> int:
        doomed = [
            key for key, rec in self._confirmed.items()
            if key[0] == store_id and rec.confirmed_at < older_than_epoch
        ]
        for key in doomed:
            rec = self._confirmed.pop(key)
            if rec.idempotency_key:
                self._idempotency.pop((store_id, rec.idempotency_key), None)
        return len(doomed)

    # -- customer identity --------------------------------------------------

    def get_or_create_customer(self, store_id: str, phone_normalized: str) -> str:
        key = (store_id, phone_normalized)
        existing = self._customers.get(key)
        if existing:
            return existing
        customer_id = f"CUST-{uuid.uuid4().hex[:12].upper()}"
        self._customers[key] = customer_id
        self._customer_phone[(store_id, customer_id)] = phone_normalized
        return customer_id

    def delete_customer(self, store_id: str, customer_id: str) -> None:
        phone = self._customer_phone.pop((store_id, customer_id), None)
        if phone is not None:
            self._customers.pop((store_id, phone), None)
        dead_sessions = [
            key for key, cid in self._session_customer.items()
            if key[0] == store_id and cid == customer_id
        ]
        for key in dead_sessions:
            self._sessions.pop(key, None)
            self._updated_at.pop(key, None)
            self._session_customer.pop(key, None)
        for key, rec in list(self._confirmed.items()):
            if key[0] == store_id and rec.customer_id == customer_id:
                self._confirmed[key] = ConfirmedOrder(
                    store_id=rec.store_id, order_id=rec.order_id, call_id=rec.call_id,
                    customer_id=None, total_cents=rec.total_cents, ticket=rec.ticket,
                    idempotency_key=rec.idempotency_key, confirmed_at=rec.confirmed_at,
                    session_snapshot=rec.session_snapshot,
                )
