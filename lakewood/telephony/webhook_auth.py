"""
Twilio webhook signature validation (T-053 Phase 2 Part 1).

Uses Twilio's own maintained `RequestValidator`, not a hand-rolled HMAC
comparison — per ADR-021's own consequence and Twilio's own documented
recommendation (a hand-rolled comparison is exactly the kind of security
primitive CLAUDE.md says to buy, not build, and a subtly wrong
reimplementation of "sort params, append to URL, HMAC-SHA1, base64" would
fail silently in the one direction that matters: accepting a forged
request). Lazy-imported — the `twilio` package is optional the same way
`psycopg2`/`piper-tts`/`faster-whisper` are; no core domain file imports
this module.

This validates the INITIAL HTTP webhook only (call-started, status
callbacks). The media WebSocket itself is never signed by Twilio — see
`stream_token.py` for that separate, self-built mechanism.
"""

from __future__ import annotations


class WebhookAuthError(Exception):
    """Raised for a missing or invalid X-Twilio-Signature. The caller must
    reject the request (HTTP 403) before any further processing — no
    partial trust, no "log and continue"."""


def validate_twilio_signature(auth_token: str, url: str, params: dict[str, str],
                               signature: str | None) -> None:
    """Raises `WebhookAuthError` unless `signature` is a valid
    X-Twilio-Signature for exactly this `url`/`params` under `auth_token`.

    `url` must be the FULL external URL Twilio itself requested (scheme +
    host + path + query) — reconstructing this wrong behind a proxy/tunnel
    (e.g. keeping `http` when Twilio actually hit `https`) is the most
    common way this check fails closed on a legitimate request; that is
    the safe failure direction, but it means the caller is responsible for
    passing the real external URL, not `request.url` from behind a
    misconfigured reverse proxy.

    `params` is the parsed form-encoded POST body Twilio sent (a plain
    `dict[str, str]`) — Twilio Voice webhooks are
    `application/x-www-form-urlencoded`, never JSON.
    """
    if not signature:
        raise WebhookAuthError("missing X-Twilio-Signature header")

    from twilio.request_validator import RequestValidator

    validator = RequestValidator(auth_token)
    if not validator.validate(url, params, signature):
        raise WebhookAuthError("X-Twilio-Signature did not match")
