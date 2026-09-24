"""
Signed, short-lived, single-use tokens for the media WebSocket URL
(T-053 Phase 2 Part 1).

Neither Twilio nor Telnyx signs the media WebSocket connection itself —
confirmed directly against Twilio's own `<Stream>` docs for ADR-021, which
say nothing about WS-level authentication. Anyone who learns the stream
URL (a proxy log, a browser history, a leaked TwiML response) could
otherwise open an audio stream straight into this server. This module is
the self-built defense the phase brief calls for: a token minted by THIS
server when it answers the call-started webhook (already validated by
`webhook_auth.py`), embedded as a query param in the `<Stream url=...>` we
hand back to Twilio, and verified here the moment the WebSocket connects
— before a single audio frame is trusted.

Stdlib-only (`hmac`/`hashlib`/`secrets`/`base64`) — this is exactly the
kind of small, well-understood primitive CLAUDE.md says to build rather
than pull in a dependency for; the RequestValidator in `webhook_auth.py`
is different because that one has to match TWILIO's own signing scheme
byte-for-byte, which is not something to reimplement.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time


class StreamTokenError(Exception):
    """Raised for a missing, malformed, expired, forged, or already-used
    token. The caller must refuse the WebSocket upgrade (close the
    connection) before reading any audio frame — no partial trust."""


class StreamTokenIssuer:
    """One instance per process, holding the signing secret and the
    single-use ledger. Not persisted across a restart — a token that
    outlives its own process is already past its short TTL by design, so
    losing the ledger on restart never re-enables a stale token; it only
    ever means a restart mid-connect makes a legitimate reconnect ask for
    a fresh token, which is the safe direction to fail in.
    """

    def __init__(self, secret: str, ttl_seconds: float = 90.0):
        if not secret:
            raise ValueError("stream token secret must not be empty")
        self._secret = secret.encode("utf-8")
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._consumed: dict[str, float] = {}   # nonce -> its own expiry, for sweeping

    def _sweep(self, now: float) -> None:
        # Lazy cleanup on every call — cheap at pilot volume (~30 calls/day),
        # no background thread needed (operational simplicity).
        expired = [nonce for nonce, exp in self._consumed.items() if exp < now]
        for nonce in expired:
            del self._consumed[nonce]

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self, call_sid: str, now: float | None = None) -> str:
        """Mints a fresh token for this call. A distinct nonce per call, so
        even two tokens issued for the SAME `call_sid` (e.g. a legitimate
        reconnect after a dropped WebSocket, T-053 Phase 2 Part 4) are
        independently single-use — consuming one never invalidates or
        collides with the other."""
        if not call_sid:
            raise ValueError("call_sid must not be empty")
        now = time.time() if now is None else now
        nonce = secrets.token_urlsafe(16)
        expiry = now + self._ttl
        payload = f"{call_sid}:{nonce}:{expiry:.6f}"
        payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
        signature = self._sign(payload)
        return f"{payload_b64}.{signature}"

    def consume(self, token: str, now: float | None = None) -> str:
        """Verifies `token` and marks it used. Returns the `call_sid` it
        was issued for. Every failure mode — malformed, forged signature,
        expired, already consumed — raises the SAME `StreamTokenError`
        type; callers must not distinguish them to a network caller (that
        would leak which check failed to whoever is probing)."""
        now = time.time() if now is None else now
        try:
            payload_b64, signature = token.rsplit(".", 1)
            payload = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
            call_sid, nonce, expiry_str = payload.split(":", 2)
            expiry = float(expiry_str)
        except (ValueError, UnicodeDecodeError) as exc:
            raise StreamTokenError("malformed stream token") from exc

        expected = self._sign(payload)
        if not hmac.compare_digest(expected, signature):
            raise StreamTokenError("stream token signature invalid")

        with self._lock:
            self._sweep(now)
            if now > expiry:
                raise StreamTokenError("stream token expired")
            if nonce in self._consumed:
                raise StreamTokenError("stream token already used")
            self._consumed[nonce] = expiry

        return call_sid
