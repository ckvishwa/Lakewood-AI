"""
T-051 — the speakable-rendering layer between the deterministic readback
builder (`orders.py::_short_readback`/`_render_line`) and TTS.

This module maps known internal identifiers to how a person would actually
say them. It never decides WHAT is said — only HOW an already-chosen token
is pronounced. Content (which items, modifiers, half placement, total are
included) is decided entirely in `orders.py`; this module cannot add or
remove anything from a readback, only reword an individual token.

Every mapping here is evidence-based, not guessed: each raw menu token was
round-tripped through real Windows SAPI synthesis -> real faster-whisper
transcription during T-051's diagnosis (see docs/STATUS.md's T-051 entry)
to confirm it was actually being mispronounced before being added here —
several NON_PIZZA names (GRINDER, CALZONE, CHEESEBURGER, TIRAMISU, etc.)
round-tripped cleanly with NO abbreviation and are intentionally left
unmapped (identity passthrough) rather than "fixed" for a problem that
measurement showed didn't exist.
"""

from __future__ import annotations

# Confirmed-broken by real TTS->STT round trip (docs/STATUS.md T-051):
#   "PC"  read as the letters P-C, not "piece"           -> '6PC WINGS' -> "6 PC Wings"
#   "SM"/"LG" size suffixes read as letters/mumble        -> "Garden salad sm."
#   "CHZCAKE" abbreviation unintelligible                 -> "Strwbrych's a cake."
#   leading-digit SKU + adjacent quantity number ambiguous -> "1 20OZ" risks
#     being heard as "120 oz" (confirmed even for cleanly-spoken "one twelve")
# Every other NON_PIZZA key round-tripped clean and is intentionally absent
# below — `speakable_item_name` falls through to the raw name for those,
# which IS the correct, already-verified-safe behavior for them.
SPEAKABLE_ITEM_NAMES: dict[str, str] = {
    "6PC WINGS": "six-piece wings",
    "12PC WINGS": "twelve-piece wings",
    "GARDEN SALAD SM": "small garden salad",
    "GARDEN SALAD LG": "large garden salad",
    "SALAD SM": "small salad",
    "SALAD LG": "large salad",
    "STRWBRY CHZCAKE": "strawberry cheesecake",
    "20OZ": "twenty-ounce",
    "2LITER": "two-liter",
}


# Toppings are spoken directly too (`orders.py::_render_line`'s half-and-
# half clause), a second, separate vocabulary from NON_PIZZA items. Audited
# the same way (round-trip every real topping name). Only ONE confirmed
# structural defect: "RSTD RED PEPPR" — the STT round trip preserved "RSTD"
# as raw, un-normalized letters (the same class of signal as "6PC WINGS" ->
# "6 PC Wings" — a real abbreviation, not a word), unlike common single-word
# STT misses on otherwise-ordinary toppings (FETA/HAM/PEPPERONI/RICOTTA/
# STEAK each transcribed oddly in isolation — almost certainly the STT
# model's known weakness on a single word with no sentence context, not
# evidence SAPI mispronounced an ordinary English word; not treated as
# confirmed defects without stronger evidence than one ambiguous round trip).
SPEAKABLE_TOPPING_NAMES: dict[str, str] = {
    "RSTD RED PEPPR": "roasted red pepper",
}


def speakable_topping_name(raw_name: str) -> str:
    return SPEAKABLE_TOPPING_NAMES.get(raw_name, raw_name.lower())


def speakable_item_name(raw_name: str) -> str:
    """Never raises — an unmapped name falls back to the raw string (today's
    existing behavior, not a regression) rather than crashing a customer's
    confirmation over a missing map entry. The REAL gate against silently
    shipping a new unmapped abbreviation is `tests/test_speech.py`'s
    completeness test (`uncovered_names`) — fails loudly, per this task's
    own instruction, rather than a production-path exception."""
    return SPEAKABLE_ITEM_NAMES.get(raw_name, raw_name.lower())


def speakable_coupon_phrase(coupon_code: str) -> str:
    """The coupon's own `spoken` field (coupons.py — "how a caller is likely
    to ask for it") already existed for exactly this purpose and was simply
    never used by the readback, which spoke the raw CODE instead (measured:
    "with the OFF_3_AT_30 discount" -> SAPI reads underscores as the literal
    word "underscore" — see docs/STATUS.md T-051). Falls back to the raw
    code for an unknown coupon (should never happen in practice — every
    coupon a customer can actually have applied comes from `coupons.BY_CODE`
    by construction) rather than raising."""
    from . import coupons
    c = coupons.BY_CODE.get(coupon_code)
    return c.spoken if c is not None else coupon_code


def uncovered_names(real_names: set[str], mapping: dict[str, str],
                    verified_clean: set[str]) -> list[str]:
    """Returns any name in `real_names` that is neither in `mapping` (an
    explicit speakable rewrite) nor in the caller-supplied `verified_clean`
    set (names explicitly round-trip-tested and confirmed to already read
    naturally) — i.e. a genuinely uncovered token nobody has verified either
    way. Empty means fully accounted for. Used by the completeness test
    (`tests/test_speech.py`) so a NEW menu item/topping added later without
    updating either this module or the verified-clean list is a loud test
    failure, never a silent unknown."""
    return sorted(real_names - set(mapping) - verified_clean)
