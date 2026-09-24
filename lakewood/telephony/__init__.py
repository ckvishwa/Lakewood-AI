"""
T-053 Phase 2 — telephony. This system's first internet-exposed endpoint
(docs/decisions/ADR-021, closes ADR-004; T-054 gates this work on the
security audit passing first).

Vendor isolation (ADR-004's original constraint, unchanged): Twilio-specific
request/response shapes stay in this package. `orders.py`, `pricing.py`,
and `menu.py` never import anything from here, and this package never
mutates order/cart state directly — it only ever calls into the same
`PersistentChat`/`run_turn_traced` path text and local voice already use
(`lakewood/chat.py`).

Every module here is defensive infrastructure BEFORE any audio is
carried (Part 1 of the phase brief): webhook signature validation
(`webhook_auth.py`), media-WebSocket token auth (`stream_token.py`),
rate limiting (`rate_limit.py`), and idempotent call start
(`call_start.py`). All four are pure/stdlib-plus-lazy-import and fully
testable offline against no live Twilio account, per this phase's own
gate ("Telephony adapter lazy-imported, fake provider for tests").
"""
