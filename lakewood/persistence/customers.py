"""
Phone -> tenant-scoped `customer_id`.

**Never use a raw phone number as a universal key** (V2 architecture note,
restated as an acceptance criterion for T-037). A phone number is personal
data, gets reassigned between people, and — if used directly as a primary
key — would leak across every table that ever needs to reference "this
customer" instead of living in exactly one place. `get_or_create_customer`
(see `repository.py`) is the only thing that mints a `customer_id`; every
other table references that opaque id, never the phone number itself.

NANP-only (the restaurant's own service area is a single US area code) —
matches every phone number already hardcoded in this repo's tests and
`chat.py`'s own default (`+10000000000`). Extending to international numbers
is a real future requirement, not attempted here since it isn't yet a real
need (CLAUDE.md: don't build for a hypothetical requirement).
"""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D")


def normalize_phone(raw: str) -> str:
    """`"(203) 755-8880"`, `"12037558880"`, `"+1 203 755 8880"` all normalize
    to `"+12037558880"`. Raises `ValueError` on anything that isn't a
    plausible 10 or 11-digit NANP number — F7's own "never guess" rule,
    applied to our own stored identity data instead of a customer's words."""
    digits = _DIGITS_RE.sub("", raw or "")
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or not digits.startswith("1"):
        raise ValueError(f"cannot normalize phone number: {raw!r}")
    return f"+{digits}"


def mask_phone(normalized: str) -> str:
    """Last 4 digits only — for logs/prints. Never write a full phone number
    to a log line (CLAUDE.md: don't log sensitive info unnecessarily)."""
    return f"***-***-{normalized[-4:]}"
