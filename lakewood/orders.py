"""
Lakewood Pizza — order engine + agent tool surface.

This is the ONLY thing the voice model can touch. Design rule: the model emits
intent, the server owns every fact. If a tool can't do something safely it
returns a structured refusal — it never raises into the model's context and
never guesses.

FAIL-SAFES (each is enforced here, not in the prompt):
  F1  No tool accepts or returns a writable price. Pricing comes from the engine.
  F2  store_id is bound to the inbound call, never a model parameter.
  F3  Every tool is guarded by the order state machine.
  F4  Any cart mutation invalidates the outstanding quote.
  F5  confirm requires a matching quote_id AND cart_hash AND an unexpired quote.
  F6  Idempotency keys — a replayed confirm returns the original, never a 2nd order.
  F7  Unknown item/topping is NEVER guessed. Returns NOT_FOUND with candidates.
  F8  Card-number-shaped input triggers immediate transfer and is not persisted.
  F9  Allergy mention triggers transfer. No allergen assurances, ever.
  F10 Two consecutive parse failures trigger transfer.
  F11 86'd items cannot be added.
  F12 Confirmed orders are immutable; changes create a new linked order.
  F13 Every transition is logged with actor and cart hash.
  F14 confirm_order rejects a call arriving in the same interpreter turn as
      its begin_confirmation — the customer must hear the readback and
      respond again before it can finalize. T-019.
  F15 begin_confirmation refuses to open while a search_menu miss is still
      unresolved — cleared ONLY by a later related query that succeeds or by
      explicit decline_item, never by an unrelated success elsewhere in the
      order (T-026 fix; the original T-019 version cleared on ANY later
      success anywhere, which silently reopened this exact hole — see
      ADR-013). A dropped request can no longer be silently confirmed away.
  F16 begin_confirmation always returns a cart-diff readback of what it is
      about to confirm — there is no path to AWAITING_CONFIRMATION without
      one. T-019.
  F17 begin_confirmation also refuses to open while a search_menu result the
      model itself flagged needs_disambiguation is still outstanding (never
      resolved by a later add_item/add_modifier, never explicitly declined).
      The parallel case to F15 — that one is a search miss, this one is a
      search hit nobody acted on. T-023.
  F18 A turn cannot end silently while an outstanding disambiguation (F17)
      remains open — the system re-asks about every such item, batched, at
      the end of every turn, regardless of what else that turn did. Capped:
      after MAX_DISAMBIGUATION_ASKS unanswered asks, transfers to a human
      rather than looping forever. T-023.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from datetime import datetime, timedelta

from . import coupons as cp
from . import speech
from .menu import HOURS, DELIVERY_MINIMUM, DELIVERY_RADIUS_MILES
from .timefmt import format_12h
from .pricing import (
    SIZES, GOURMET_ROUND, GOURMET_SICILIAN_OVERRIDES, NON_PIZZA,
    REGULAR_TOPPINGS, PREMIUM_TOPPINGS, FREE_MODIFIERS, FLAT_MODIFIERS,
    UNKNOWN_TIER, DELIVERY_SERVICE_CHARGE, Order, PizzaLine, SimpleLine,
    Topping, render_ticket, money,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QUOTE_TTL_SECONDS = 300          # 5 min
CONFIRM_WAIT_SECONDS = 45
MAX_PARSE_FAILURES = 2
MAX_CALL_SECONDS = 480           # 8 min — cost + UX guard
MAX_DISAMBIGUATION_ASKS = 2      # F18 — unanswered asks before transfer

STATES = ("DRAFT", "BUILDING", "QUOTED", "AWAITING_CONFIRMATION",
          "CONFIRMED", "HELD_FOR_OPEN", "SENT_TO_STORE", "STORE_ACKED",
          "CANCELLED", "TRANSFERRED", "EXPIRED", "FAILED_DISPATCH")

TERMINAL = {"CONFIRMED", "HELD_FOR_OPEN", "SENT_TO_STORE", "STORE_ACKED",
            "CANCELLED", "TRANSFERRED", "EXPIRED", "FAILED_DISPATCH"}

TRANSFER_REASONS = {
    "customer_request", "allergy", "payment_card", "refund_or_complaint",
    "repeated_misunderstanding", "unsupported_request", "system_error",
    "price_uncertain", "confirm_timeout",
}

# F8 — card-shaped input. Deliberately loose: false positives just transfer.
_CARD_RE = re.compile(r"(?:\d[ \-]?){13,19}")
# F9 — allergy triggers. Never attempt to reason about allergens.
_ALLERGY_RE = re.compile(
    r"\b(allerg\w*|anaphyla\w*|epipen|celiac|gluten[\s-]?free|intoleran\w*)\b", re.I)

ALL_TOPPINGS = REGULAR_TOPPINGS | PREMIUM_TOPPINGS | set(FREE_MODIFIERS) \
    | set(FLAT_MODIFIERS) | UNKNOWN_TIER

# Spoken forms -> POS names. Extend from real transcripts; never guess at runtime.
ALIASES = {
    "extra cheese": "MOZZARELLA", "cheese": "MOZZARELLA", "mozz": "MOZZARELLA",
    "peppers": "PEPPERS", "green peppers": "PEPPERS", "bell peppers": "PEPPERS",
    "hot peppers": "JALAPENO", "jalapenos": "JALAPENO",
    "olives": "BLACK OLIVES", "onion": "ONIONS", "mushroom": "MUSHROOMS",
    "sundried tomatoes": "SUNDRIED TOMATO", "roasted red peppers": "RSTD RED PEPPR",
    "grilled chicken": "CHICKEN", "buffalo chicken": "CHICKEN",
    "meatball": "MEATBALLS", "tomato": "TOMATOES", "anchovy": "ANCHOVIES",
    "parmesan": "PARMESAN CHEESE", "parm": "PARMESAN CHEESE",
    "pep": "PEPPERONI",  # T-044: real counter/phone slang ("a lg pep")
    "ranch": "SIDE RANCH", "blue cheese": "SIDE BLUE CHEESE",
    "marinara": "RED SAUCE", "tomato sauce": "RED SAUCE",
    "white": "WHITE SAUCE", "red": "RED SAUCE",
}

SIZE_ALIASES = {
    "small": "SMALL", "sm": "SMALL", "12": "SMALL", "12 inch": "SMALL",
    "medium": "MEDIUM", "med": "MEDIUM", "14": "MEDIUM", "14 inch": "MEDIUM",
    "large": "LARGE", "lg": "LARGE", "16": "LARGE", "16 inch": "LARGE",
    "extra large": "XLARGE", "x large": "XLARGE", "xl": "XLARGE", "18": "XLARGE",
    "party": "PARTY", "the beast": "PARTY",
    "sicilian": "SICILIAN", "square": "SICILIAN", "old fashion": "SICILIAN",
}

# T-020: NON_PIZZA spoken forms -> POS names, same purpose/shape as ALIASES
# above but for non-pizza items rather than toppings. `search_menu` is the
# model's ONLY way to confirm an item name before calling add_item, and
# before this it only matched a query that was a literal substring of the
# exact POS name — "strawberry cheesecake" was never going to find
# "STRWBRY CHZCAKE" that way, no matter how the model phrased it. Diagnosed
# from real T-018/T-019 live failures (docs/EVALS.md "search_menu recall
# baseline" — T-020), not invented.
NON_PIZZA_ALIASES = {
    "strawberry cheesecake": "STRWBRY CHZCAKE", "strawberry cheese cake": "STRWBRY CHZCAKE",
    "coke": "CAN", "pepsi": "CAN", "soda": "CAN", "pop": "CAN", "can of soda": "CAN",
    "two liter": "2LITER", "2 liter": "2LITER", "2-liter": "2LITER",
    "bottle": "20OZ", "20 oz": "20OZ", "20oz": "20OZ",
}

# T-032: generic drink-category words that should lose to a specific size
# mention in the same query ("two liter soda" means 2LITER, not "2LITER
# and also CAN") — see search_menu's NON_PIZZA_ALIASES precedence pass.
# "can of soda" is deliberately excluded: it names the container, not a
# generic catch-all, so it never competes with a size alias the way the
# bare words below do.
#
# Public (not `_`-prefixed): interpreter.py's RuleBasedInterpreter._find_drink
# resolves the SAME precedence via non_pizza_alias_hits() below — T-039 found
# it had its own second, never-updated copy of this exact alias table, which
# silently reintroduced the "two liter coke" -> CAN collision T-032 fixed
# here alone, plus its own worse bug (a bare "can", i.e. the modal verb in
# "can I get...", as a false drink signal). One table, one precedence rule,
# two callers — that bug class cannot recur by construction.
GENERIC_DRINK_ALIASES = {"coke", "pepsi", "soda", "pop"}


def non_pizza_alias_hits(raw_q: str) -> list[tuple[str, str]]:
    """NON_PIZZA_ALIASES matches in `raw_q` that survive the generic-vs-
    specific precedence rule (T-032): a specific drink size ("two liter")
    always wins over a co-occurring generic catch-all ("soda"/"coke"/"pepsi"/
    "pop") in the SAME query. Returns `(alias, canonical_name)` pairs in
    `NON_PIZZA_ALIASES` order; a caller wanting a single best answer takes
    the first entry, a caller wanting every real candidate (search_menu) uses
    them all.
    """
    # T-041: plural tolerance ("sodas", "bottles") — a customer ordering for
    # a group says the plural at least as often as the singular; the alias
    # itself still identifies the same one canonical item either way.
    matched = [(a, c) for a, c in NON_PIZZA_ALIASES.items()
               if re.search(rf"\b{re.escape(a)}s?\b", raw_q)]
    specific = {c for a, c in matched if a not in GENERIC_DRINK_ALIASES}
    return [(a, c) for a, c in matched
            if not (a in GENERIC_DRINK_ALIASES and specific and c not in specific)]

# T-044: structural, POS-label-only tokens inside a NON_PIZZA key (size
# suffixes, the "PC" in "6PC WINGS", generic modifier-row nouns like "ITEM"
# in "CALZONE ITEM") — never a word a customer would actually say to name
# the item, so they're dropped when deriving what a real mention of that
# item looks like. This is NOT a denylist of customer-facing product nouns
# (the thing ADR-017/T-039A already ruled out) — it only strips SKU-table
# formatting artifacts before the real identifying words are computed below.
_NON_PRODUCT_NAME_NOISE = {"sm", "lg", "pc", "extra", "item"}


def _menu_item_identifying_words(name: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z]+", name.lower())
                      if w not in _NON_PRODUCT_NAME_NOISE)


# T-044: every real NON_PIZZA item's identifying words, computed once from
# the menu itself (`data/menu.json` via `pricing.NON_PIZZA`) — never a hand-
# maintained noun list. `interpreter.py`'s pizza-intent gate uses this to
# tell "a genuinely different, fully-named menu item is also in this
# utterance" (must not block the pizza) apart from "an unresolved word is
# here" (must). See `non_pizza_full_name_match` below and ADR-017's T-044
# amendment.
NON_PIZZA_IDENTIFYING_WORDS: dict[str, frozenset[str]] = {
    name: _menu_item_identifying_words(name) for name in NON_PIZZA
}


def non_pizza_full_name_match(text: str) -> bool:
    """True when EVERY word in `text` (letters only, trivial articles
    dropped) is exactly the identifying-word set of some one real NON_PIZZA
    item — a complete, unambiguous mention of that item with nothing left
    unexplained. A PARTIAL match ("chicken caesar wrap" against WRAP's
    {"wrap"} — "chicken"/"caesar" left over) is deliberately NOT a match:
    this function exists to let a genuinely separate, fully-named item
    stand alongside a pizza in the same utterance without blocking pizza
    creation, never to wave through an ambiguous or partially-explained
    product mention. Caller is expected to have already stripped filler/
    quantity words from `text` (see `interpreter.py`'s
    `_clause_resolves_to_separate_item`)."""
    words = (frozenset(re.findall(r"[a-z]+", text.lower()))
             - {"a", "an", "the"} - _NON_PRODUCT_NAME_NOISE)
    if not words:
        return False
    return any(words == ids for ids in NON_PIZZA_IDENTIFYING_WORDS.values() if ids)


# T-020: words that carry no menu-identifying content but routinely appear
# around a real item/topping name in a natural request and previously broke
# the substring match outright — "pepperoni topping" was never going to
# match "PEPPERONI" while it stayed a strict substring-of-name check in
# either direction with these words still attached. Stripped before
# matching only; never affects what's actually added (add_item/add_modifier
# still resolve the real name through their own existing paths).
_SEARCH_FILLER_WORDS = {
    "topping", "toppings", "sauce", "dressing", "dipping", "side", "of",
    "piece", "pieces", "pc",
}

# "number 10" / "num 10" / "no. 10" / "#10" -> "10", so it matches the exact
# gourmet-number check below the same way a bare "10" already did.
_NUMBER_PREFIX_RE = re.compile(r"^(?:number|num|no\.?|#)\s*#?\s*(\d{1,2})$", re.I)


_QTY_PIECE_RE = re.compile(r"\b(\d+)\s*(?:pc|piece|pieces)\b")

# T-041: real speech (STT transcripts) gives numbers as words
# ("number ten", "six piece wings"), never guaranteed as digits. search_menu
# and the interpreter's evidence-matching layer were both found to fail
# identically on spelled-out numbers (see docs/STATUS.md's T-041 entry) —
# the SAME gap in two places is exactly the drift ADR-017 already warned
# about for alias tables (T-039's duplicated _DRINK_WORDS). One normalizer
# here, consumed by both callers, closes it by construction rather than by
# convention. Covers 1-39 (single words plus compound tens, hyphenated or
# spaced: "twenty seven"/"twenty-seven") — comfortably past the current
# 27-item GOURMET_ROUND range, with headroom for menu growth.
_ONES_WORDS = ["", "one", "two", "three", "four", "five", "six", "seven",
              "eight", "nine", "ten", "eleven", "twelve", "thirteen",
              "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
              "nineteen"]
_TENS_WORDS = ["", "", "twenty", "thirty"]

WORD_TO_NUMBER: dict[str, int] = {}
for _i, _w in enumerate(_ONES_WORDS):
    if _w:
        WORD_TO_NUMBER[_w] = _i
for _ti, _tw in enumerate(_TENS_WORDS):
    if not _tw:
        continue
    WORD_TO_NUMBER[_tw] = _ti * 10
    for _oi, _ow in enumerate(_ONES_WORDS[1:10], start=1):
        WORD_TO_NUMBER[f"{_tw}-{_ow}"] = _ti * 10 + _oi
        WORD_TO_NUMBER[f"{_tw} {_ow}"] = _ti * 10 + _oi
del _i, _w, _ti, _tw, _oi, _ow

# Longest key first: "twenty seven" must not partially match "twenty" and
# leave "seven" for a second, independent substitution (which would produce
# "20 7" instead of "27").
_NUMBER_WORDS_ALT = "|".join(re.escape(k) for k in
                             sorted(WORD_TO_NUMBER, key=len, reverse=True))
# T-041 (found during implementation, not the original design): a FIRST
# version of this function replaced every standalone spelled number
# anywhere in the text ("one" -> "1" unconditionally) and broke
# `_ONE_HALF_RE`'s "on one half"/"a half" half-portion idiom outright —
# "one" means something completely different there than it does in
# "number one" or "six piece wings". Anchored to the two contexts a real
# order actually spells a number in: right after "number"/"num"/"no."/"#"
# (a gourmet-number reference), or right before "piece(s)"/"pc" (a wing-
# count reference). Deliberately NOT a general-purpose word-to-number
# converter — see docs/decisions/ADR-017's T-041 amendment.
_NUMBER_PREFIX_WORD_RE = re.compile(
    rf"\b(number|num|no\.?|#)\s*#?\s*({_NUMBER_WORDS_ALT})\b", re.I)
_PIECE_QTY_WORD_RE = re.compile(
    rf"\b({_NUMBER_WORDS_ALT})\s*(pc|piece|pieces)\b", re.I)


def normalize_spoken_numbers(text: str) -> str:
    """Replace a spelled-out cardinal with its digit form, but ONLY right
    after a number-reference word ('number ten' -> 'number 10') or right
    before a piece-count word ('six piece' -> '6 piece'). Idempotent — safe
    to call more than once on the same text."""
    text = _NUMBER_PREFIX_WORD_RE.sub(
        lambda m: f"{m.group(1)} {WORD_TO_NUMBER[m.group(2).lower()]}", text)
    text = _PIECE_QTY_WORD_RE.sub(
        lambda m: f"{WORD_TO_NUMBER[m.group(1).lower()]} {m.group(2)}", text)
    return text


def normalize_menu_text(text: str) -> str:
    """The one shared normalizer for 'do these words denote this SKU' —
    used by search_menu's own query handling AND by the interpreter's
    evidence-matching layer (lakewood/interpreter.py's mutation-boundary
    guard) on the customer's utterance. Spelled numbers -> digits, then
    digit+piece-word -> the abbreviated 'Npc' form real menu keys use
    ('six piece wings' -> '6 piece wings' -> '6pc wings', matching
    '6PC WINGS'). Idempotent. See docs/decisions/ADR-017's T-041 amendment
    for why this must be ONE function, not two independently-drifting
    copies."""
    t = normalize_spoken_numbers(text)
    return _QTY_PIECE_RE.sub(r"\1pc", t)

# Words that describe "the base pizza" itself rather than naming a specific
# specialty/topping — see the CHEESE PIZZA pseudo-hit below. Anything else
# left over in the query has to be a real, already-matched topping/gourmet
# keyword for the pseudo-hit to fire; an unrecognized word means the
# customer likely asked for something that doesn't exist, and F7 says stay
# a miss, not a guessed substitute.
_GENERIC_PIZZA_WORDS = {w for key in SIZE_ALIASES for w in key.split()} | {
    "pizza", "pizzas", "cheese", "plain", "regular", "a", "the", "an",
    "order", "get", "want", "like", "please", "some", "just", "i'd", "id",
    "me",
}


def _strip_search_filler(q: str) -> str:
    q = normalize_menu_text(q)   # "six piece"/"12 pieces" -> "6pc"/"12pc"
    words = [w for w in q.split() if w not in _SEARCH_FILLER_WORDS]
    return " ".join(words)

# 86'd items — staff-settable, checked on every add. F11.
UNAVAILABLE: set[str] = set()


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday"]


def store_status(now: datetime | None = None) -> dict:
    """
    Owner-confirmed hours. Closed Mondays.

    After-hours calls currently just go unanswered, which is pure lost revenue.
    The agent takes the order anyway and holds it — but it MUST say when the
    food will be ready. Never let a 10:30pm caller believe it's coming tonight.
    """
    now = now or datetime.now()
    today = HOURS.get(now.weekday())
    if today and today[0] <= now.hour < today[1]:
        return {"open": True, "closes_at": f"{today[1] % 12 or 12} PM"}

    probe = now
    for _ in range(8):
        probe = (probe + timedelta(days=1)).replace(hour=0, minute=0)
        win = HOURS.get(probe.weekday())
        if win:
            hh = win[0]
            label = f"{DAYS[probe.weekday()]} at {hh % 12 or 12} " \
                    f"{'AM' if hh < 12 else 'PM'}"
            return {"open": False, "next_open": label,
                    "next_open_iso": probe.replace(hour=hh).isoformat()}
    return {"open": False, "next_open": None}


def ok(**kw):
    return {"status": "ok", **kw}


def err(code: str, message: str, **kw):
    """Structured refusal. The model reads this and speaks; it never sees a stack."""
    return {"status": "error", "code": code, "message": message, **kw}


# ---------------------------------------------------------------------------
# Session — one per phone call. Holds everything the model must not control.
# ---------------------------------------------------------------------------

@dataclass
class Session:
    call_id: str
    store_id: str                      # F2: bound from the inbound DID
    from_number: str
    started_at: float = field(default_factory=time.time)

    state: str = "DRAFT"
    order_id: str = field(default_factory=lambda: f"AI-{uuid.uuid4().hex[:6].upper()}")
    order: Order = field(default_factory=Order)
    lines: dict = field(default_factory=dict)          # line_id -> object
    _next_line: int = 1

    customer_name: Optional[str] = None
    address: Optional[str] = None
    delivery_note: Optional[str] = None

    quote_id: Optional[str] = None
    quote_hash: Optional[str] = None
    quote_at: Optional[float] = None

    scheduled_for: Optional[str] = None      # set when the order is taken after hours
    # T-057 (docs/SECURITY_AUDIT_T054.md, finding T054-04): the recording/
    # AI-disclosure notice must play before transcription begins on a real
    # call, and the fact it played must be provable, not assumed — this is
    # the persisted proof. Set once, deterministically, by
    # mark_disclosure_played() below; never by a caller guessing. Lives in
    # the same session_json JSONB blob every other Session field already
    # round-trips through (ADR-014 Decision 3 — not a query target, so no
    # new indexed column).
    disclosure_played_at: Optional[float] = None
    parse_failures: int = 0
    transfer_reason: Optional[str] = None
    idempotency: dict = field(default_factory=dict)
    events: list = field(default_factory=list)

    # -- T-019: confirmation-gate + dropped-request tracking ---------------
    # `turn` is incremented once per customer utterance by whichever caller
    # drives the conversation (lakewood/chat.py::run_turn, or evals/runner.py
    # replaying a labeled case turn-by-turn) — never by orders.py itself.
    # It is what "same turn" means for F14, and it is interpreter/provider-
    # agnostic: whether one customer utterance produced one tool call or
    # twelve internal rounds, they all share this one turn number.
    turn: int = 0
    confirmation_turn: Optional[int] = None      # F14: set by begin_confirmation
    unresolved_lookups: list = field(default_factory=list)  # F15

    # -- T-023: outstanding disambiguations (F17/F18) -----------------------
    # The parallel case to unresolved_lookups: not a search MISS, but a
    # search HIT the model itself flagged ambiguous (needs_disambiguation)
    # and never followed with a resolving add_item/add_modifier. Each entry:
    # {"query": str, "candidates": [hit, ...], "key": frozenset, "ask_count": int}.
    # Survives across turns on purpose — same reasoning as F15's own
    # docstring: the customer's request doesn't expire just because the
    # interpreter moved on to something else this turn.
    pending_disambiguations: list = field(default_factory=list)

    # -- internals ---------------------------------------------------------

    def log(self, event: str, **kw):                    # F13
        self.events.append({
            "ts": round(time.time() - self.started_at, 2),
            "event": event, "state": self.state,
            "cart_hash": self.cart_hash(), **kw,
        })

    def cart_hash(self) -> str:
        payload = json.dumps([
            self._describe(l) for l in self.order.lines
        ] + [self.order.order_type, self.address or ""], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    @staticmethod
    def _describe(l) -> dict:
        if isinstance(l, PizzaLine):
            return {"t": "pizza", "size": l.size, "g": l.gourmet,
                    "hh": list(l.half_and_half) if l.half_and_half else None,
                    "q": l.quantity,
                    "tops": sorted(f"{t.name}|{t.portion}|{t.qty}|{t.removed}|{t.lite}"
                                   for t in l.toppings)}
        return {"t": "item", "name": l.name, "q": l.quantity}

    def invalidate_quote(self):                          # F4
        if self.quote_id:
            self.log("quote_invalidated", quote_id=self.quote_id)
        self.quote_id = self.quote_hash = self.quote_at = None
        self.confirmation_turn = None                     # F14: T7 also resets the gate

    def to(self, new_state: str):
        if new_state not in STATES:
            raise AssertionError(f"bad state {new_state}")
        self.state = new_state
        self.log("transition", to=new_state)

    def new_line_id(self) -> str:
        lid = f"L{self._next_line}"
        self._next_line += 1
        return lid

    def guard(self, *allowed: str):                      # F3
        if self.state in TERMINAL:
            return err("ORDER_CLOSED",
                       f"This order is {self.state.lower()} and can no longer change.")
        if self.state not in allowed:
            return err("BAD_STATE",
                       f"Not allowed while the order is {self.state}.")
        if time.time() - self.started_at > MAX_CALL_SECONDS:
            return _do_transfer(self, "unsupported_request")
        return None

    def note_parse_failure(self):                        # F10
        self.parse_failures += 1
        if self.parse_failures >= MAX_PARSE_FAILURES:
            return _do_transfer(self, "repeated_misunderstanding")
        return None

    def clear_failures(self):
        self.parse_failures = 0


# ---------------------------------------------------------------------------
# Input screening — runs on every raw customer utterance, before the model acts
# ---------------------------------------------------------------------------

def screen_utterance(sess: Session, text: str):
    """F8 / F9. Returns a transfer result, or None to continue."""
    if _ALLERGY_RE.search(text):
        return _do_transfer(sess, "allergy")
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 13 and _CARD_RE.search(text):
        sess.log("card_data_detected_discarded")       # never persist the audio/text
        return _do_transfer(sess, "payment_card")
    return None


def _do_transfer(sess: Session, reason: str):
    assert reason in TRANSFER_REASONS
    sess.transfer_reason = reason
    sess.to("TRANSFERRED")
    return ok(action="transfer", reason=reason,
              say="Let me get someone from the store on the line for you.")


# T-057 (docs/SECURITY_AUDIT_T054.md, finding T054-04): fixed disclosure
# text a real call-handling layer (LocalVoiceLoop today; T-053's real
# phone loop tomorrow, same call-start hook) must speak before the FIRST
# transcription of a call. Wording is deliberately conservative and
# generic — a real go-live still needs the actual legal requirement
# confirmed for the restaurant's specific jurisdiction (one-party vs.
# two-party consent varies by state); this is not that confirmation.
DISCLOSURE_TEXT = (
    "This call may be recorded, and you're speaking with an automated "
    "assistant."
)


def mark_disclosure_played(sess: Session) -> dict:
    """T-057: record that the disclosure was actually spoken, once,
    deterministically — never inferred from "a TTS call happened" or
    assumed by a caller. Idempotent: a call-loop retry or a duplicate
    invocation never overwrites the original timestamp (the first real
    playback is the fact that matters, not the latest attempt)."""
    if sess.disclosure_played_at is None:
        sess.disclosure_played_at = time.time()
        sess.log("disclosure_played", at=sess.disclosure_played_at)
    return ok(disclosure_played_at=sess.disclosure_played_at)


# ---------------------------------------------------------------------------
# Resolution helpers — F7: never guess
# ---------------------------------------------------------------------------

def _resolve_size(raw: str):
    k = (raw or "").strip().lower()
    if k.upper() in SIZES:
        return k.upper(), None
    if k in SIZE_ALIASES:
        return SIZE_ALIASES[k], None
    return None, err("SIZE_NOT_FOUND", f"No size matching {raw!r}.",
                     candidates=sorted(SIZES))


def _resolve_topping(raw: str):
    k = (raw or "").strip()
    if k.upper() in ALL_TOPPINGS:
        return k.upper(), None
    if k.lower() in ALIASES:
        return ALIASES[k.lower()], None
    close = [t for t in sorted(ALL_TOPPINGS) if k.lower() in t.lower()][:5]
    return None, err("TOPPING_NOT_FOUND",
                     f"No topping matching {raw!r}. Do not substitute — ask.",
                     candidates=close)


def _gourmet_names(size: str) -> dict:
    names = dict(GOURMET_ROUND)
    if size == "SICILIAN":
        names.update(GOURMET_SICILIAN_OVERRIDES)
    return names


# ---------------------------------------------------------------------------
# T-023 (F17/F18): outstanding-disambiguation bookkeeping. Registration
# happens in search_menu (below); clearing happens at every successful
# add_item/add_modifier call site that could plausibly answer one — matching
# is by candidate identity (kind + name/number), never by raw text, so a
# rephrased follow-up query still resolves the same entry.
# ---------------------------------------------------------------------------

def _disambiguation_key(hits: list[dict]) -> frozenset:
    return frozenset((h.get("kind"), h.get("name"), h.get("number")) for h in hits)


def _query_relates(a: str, b: str) -> bool:
    """T-026/F15: bidirectional, case-insensitive substring check — the same
    loose-but-scoped matching decline_item already uses for disambiguations.
    A genuine NO_MATCH has no candidate identity to match against (nothing
    matched at all), so this is deliberately looser than F17's exact
    candidate-identity match; it only exists to catch a customer/interpreter
    literally re-trying the same or a closely related phrase that happens to
    succeed this time, not to guess that an unrelated success "counts."""
    a, b = a.lower().strip(), b.lower().strip()
    return bool(a) and bool(b) and (a in b or b in a)


def _register_disambiguation(sess: Session, query: str, hits: list[dict]) -> None:
    key = _disambiguation_key(hits)
    for entry in sess.pending_disambiguations:
        if entry["key"] == key:
            return  # same ambiguity already outstanding — don't duplicate/reset it
    sess.pending_disambiguations.append(
        {"query": query, "candidates": hits, "key": key, "ask_count": 0})


def _narrow_disambiguation(sess: Session, current: list[dict], narrowed: list[dict]) -> None:
    """Replace one outstanding candidate set with a deterministic subset.

    ``Session.pending_disambiguations`` is the authoritative, persisted
    clarification state.  ``ChatState.pending_clarification`` may mirror it
    for presentation/pronoun handling, but narrowing only that transient list
    would be lost on reload and would incorrectly let a later size-only reply
    choose across the original, wider set.
    """
    current_key = _disambiguation_key(current)
    narrowed_key = _disambiguation_key(narrowed)
    for entry in sess.pending_disambiguations:
        if entry["key"] != current_key:
            continue
        entry["candidates"] = list(narrowed)
        entry["key"] = narrowed_key
        return


def _clear_disambiguations_matching(sess: Session, *, item_name: str | None = None,
                                    gourmet_number: int | None = None,
                                    topping_name: str | None = None) -> None:
    def _matches(entry: dict) -> bool:
        for h in entry["candidates"]:
            if item_name and h.get("kind") == "item" and h.get("name") == item_name:
                return True
            if gourmet_number is not None and h.get("kind") == "gourmet" \
                    and h.get("number") == gourmet_number:
                return True
            if topping_name and h.get("kind") == "topping" and h.get("name") == topping_name:
                return True
        return False
    sess.pending_disambiguations[:] = [
        e for e in sess.pending_disambiguations if not _matches(e)]


# ===========================================================================
# TOOL SURFACE — the only functions exposed to the model.
# Note: no tool takes store_id, and no tool takes a price. F1, F2.
# ===========================================================================

def get_store_info(sess: Session, fields: list[str] | None = None):
    st = store_status()
    data = {
        "name": "Lakewood Pizza",
        "address": "562 Lakewood Rd, Waterbury, CT 06704",
        "phone": "(203) 758-8880",
        "order_types": ["pickup", "delivery"],
        "hours": "Tuesday to Saturday 11 AM to 10 PM, Sunday 12 PM to 9 PM, "
                 "closed Mondays.",
        "open_now": st["open"],
        "next_open": st.get("next_open"),
        "delivery_radius_miles": str(DELIVERY_RADIUS_MILES),
        "delivery_minimum": money(DELIVERY_MINIMUM),
        "delivery_fee": money(DELIVERY_SERVICE_CHARGE),
        "payment": "Pay at pickup or on delivery. We cannot take card numbers by phone.",
        # coupons deliberately absent — see docs/OPEN-QUESTIONS.md item 3.
    }
    missing = [f for f in (fields or []) if f not in data]
    if missing:
        return err("INFO_NOT_AVAILABLE",
                   "I don't have that on file — transfer rather than guess.",
                   missing=missing)
    return ok(info={k: v for k, v in data.items() if not fields or k in fields})


def search_menu(sess: Session, query: str, category: str | None = None):
    """
    T-020: recall fixes, all still F7-safe — every hit here is a *candidate
    for disambiguation*, never an auto-selected item; add_item/add_modifier
    still resolve the authoritative name themselves and can still refuse.
    Diagnosed from real T-018/T-019 live failures before any change (see
    docs/EVALS.md "search_menu recall baseline — T-020"):

    - The single biggest failure class was the base pizza itself: nothing
      here ever matched "pizza"/"cheese pizza"/"pepperoni pizza" because
      CHEESE PIZZA was never a searchable name at all (it's a special case
      inside add_item, invisible to search_menu) — see the pseudo-hit below.
    - A second class was a strict, one-directional substring check that a
      filler word anywhere ("pepperoni TOPPING", "SIDE OF ranch",
      "12 PIECE wings") or a format mismatch ("NUMBER 10" vs the bare "10"
      the code compared against) broke outright, on menu names/numbers that
      otherwise matched exactly.
    - A third: this function never consulted ALIASES (topping spoken-forms
      already used by add_modifier/remove_modifier) or any equivalent table
      for non-pizza items, so "extra cheese" and "strawberry cheesecake"
      could never resolve even though the item they mean is real.
    """
    # T-041: normalize spelled-out numbers ("number ten" -> "number 10")
    # before anything else touches raw_q — every check below (gourmet
    # number, filler-stripped q, the CHEESE PIZZA pseudo-hit) reads from
    # this one normalized value, so a real spoken form can never resolve
    # here but not there.
    raw_q = normalize_spoken_numbers((query or "").lower().strip())
    q = _strip_search_filler(raw_q)
    num_match = _NUMBER_PREFIX_RE.match(raw_q) or _NUMBER_PREFIX_RE.match(q)
    num_q = num_match.group(1) if num_match else (raw_q if raw_q.isdigit() else None)

    hits = []
    for n, name in GOURMET_ROUND.items():
        if (q and q in name.lower()) or (num_q is not None and num_q == str(n)):
            hits.append({"kind": "gourmet", "number": n, "name": name})

    for t in sorted(ALL_TOPPINGS):
        # T-020: forward-only (query must be a substring of the real name),
        # same direction as the original code. A reverse check (name inside
        # a longer query) was tried and reverted — it matched "CHICKEN"
        # inside "chicken tenders" and "SHRIMP" inside "shrimp scampi",
        # both real menu words used to mean a genuinely different, real-
        # world, non-menu thing. A wrong candidate is worse than a miss
        # (F7); filler-word stripping already recovers the suffix-word
        # cases ("pepperoni topping" -> "pepperoni") without this risk.
        if q and q in t.lower():
            hits.append({"kind": "topping", "name": t})
    topping_names = {h["name"] for h in hits if h["kind"] == "topping"}
    # T-032: word-boundary, not raw substring. A raw `alias in raw_q` check
    # matched "cheese" inside "cheesecake" — T-031 traced this to a real
    # live failure (`search('cheesecake strawberry cheesecake')` returning
    # MOZZARELLA as a candidate). Strictly more precise than substring
    # containment for every existing alias (none of them were ever meant to
    # match as a fragment inside an unrelated word), so this cannot regress
    # a case that relied on the old behavior — see
    # `test_search_menu_alias_collision.py`.
    _pizza_mentioned = bool(re.search(r"\bpizzas?\b", raw_q))
    _cheese_alias_suppressed = False
    for alias, canon in ALIASES.items():
        # T-032: "cheese" (bare) + "pizza" names the base ITEM ("cheese
        # pizza"), not a request to add mozzarella — `_GENERIC_PIZZA_WORDS`
        # below already treats "cheese" as generic in a pizza context for
        # exactly this reason; this alias loop just wasn't consistent with
        # it. Narrow: only the bare "cheese" key is affected. "extra
        # cheese"/"mozz" are separate keys and still resolve normally, so a
        # customer who wants extra mozzarella on their cheese pizza is
        # unaffected. The suppression flag feeds the CHEESE PIZZA pseudo-hit
        # guard below — without it, a query like "party size cheese pizza"
        # (residual "size" after generic-word stripping) would lose the
        # only signal that ever proved it wasn't gibberish and fall through
        # to a bare NO_MATCH instead of resolving to the pizza.
        if not re.search(rf"\b{re.escape(alias)}\b", raw_q):
            continue
        if alias == "cheese" and _pizza_mentioned:
            _cheese_alias_suppressed = True
            continue
        if canon not in topping_names:
            hits.append({"kind": "topping", "name": canon})
            topping_names.add(canon)

    for name in NON_PIZZA:
        if q and q in name.lower():
            hits.append({"kind": "item", "name": name})
    item_names = {h["name"] for h in hits if h["kind"] == "item"}
    # T-032: same word-boundary fix, plus a precedence rule for the sibling
    # collision T-031 found — "two liter soda" matched BOTH "two liter"
    # (->2LITER) and the generic "soda" (->CAN) alias at once. A specific
    # size mention beats the generic drink-category word, same principle as
    # the cheese/pizza fix above: a more specific thing the customer
    # actually named outranks a generic word that happens to trail it.
    # "soda"/"coke"/"pepsi"/"pop" alone (no specific size) are unaffected.
    for alias, canon in non_pizza_alias_hits(raw_q):
        if canon not in item_names:
            hits.append({"kind": "item", "name": canon})
            item_names.add(canon)

    # The base pizza has no name of its own to match against — add_item
    # special-cases 'PIZZA'/'CHEESE PIZZA' rather than looking it up here.
    # Surface it as a real candidate whenever "pizza" is mentioned and no
    # specific numbered gourmet was already found — but ONLY when the rest
    # of the query is either empty/generic ("cheese pizza", "large cheese
    # pizza", "party size cheese pizza") or already grounded in a real
    # topping this same query matched ("pepperoni pizza" -> PEPPERONI was
    # already found above). A query with neither ("truffle lobster pizza")
    # gets no pseudo-hit and stays a clean refusal — offering "well, at
    # least a plain pizza exists" for a fabricated specialty is exactly the
    # kind of confident-but-wrong suggestion F7 exists to prevent; a miss
    # here is the correct, safe outcome. Regression-tested:
    # `test_nonexistent_item_is_refused_not_invented`.
    _pizza_residual = " ".join(w for w in raw_q.split() if w not in _GENERIC_PIZZA_WORDS)
    _residual_topping = ALIASES.get(_pizza_residual) or next(
        (t for t in ALL_TOPPINGS if _pizza_residual and _pizza_residual in t.lower()), None)
    if _residual_topping and _residual_topping not in topping_names:
        hits.append({"kind": "topping", "name": _residual_topping})
        topping_names.add(_residual_topping)
    if re.search(r"\bpizzas?\b", raw_q) and not any(h["kind"] == "gourmet" for h in hits) \
            and "CHEESE PIZZA" not in item_names \
            and (not _pizza_residual or hits or _residual_topping or _cheese_alias_suppressed):
        hits.append({"kind": "item", "name": "CHEESE PIZZA"})

    # A generic drink mention ("drink", "beverage(s)", "something to drink")
    # genuinely doesn't pick one of CAN/2LITER/20OZ — that's real ambiguity,
    # not a miss to paper over (F7), so hand back all three as candidates to
    # disambiguate from rather than NO_MATCH on a request that IS on the menu.
    if not item_names and re.search(r"\bdrinks?\b|\bbeverages?\b", raw_q):
        for drink_name in ("CAN", "2LITER", "20OZ"):
            hits.append({"kind": "item", "name": drink_name})

    if not hits:
        # F15: a miss is tracked, not dropped. Persists across turns on
        # purpose: the customer's request doesn't expire just because the
        # interpreter moved on. T-026: cleared ONLY by a later query that is
        # textually related to THIS one and succeeds (_query_relates), or by
        # explicit decline_item — never by an unrelated success. Before
        # T-026 this cleared on ANY later successful search/mutation
        # anywhere, which silently reopened the exact T-018 dropped-request
        # hole via any ordinary multi-item order (see ADR-013).
        sess.unresolved_lookups.append(query)
        return err("NO_MATCH", f"Nothing on the menu matches {query!r}.")
    sess.unresolved_lookups[:] = [
        q for q in sess.unresolved_lookups if not _query_relates(q, query)]
    # Ambiguity across categories is the #1 failure mode on this menu.
    kinds = {h["kind"] for h in hits}
    needs_disambiguation = len(kinds) > 1 or len(hits) > 1
    if needs_disambiguation:
        # F17: a hit isn't a miss, but an ambiguous hit nobody ever resolves
        # is the same customer-facing failure — see T-022's MULTI-002
        # diagnosis. Cleared by _clear_disambiguations_matching below, or by
        # decline_item, never by silence.
        _register_disambiguation(sess, query, hits[:8])
    return ok(results=hits[:8], needs_disambiguation=needs_disambiguation)


def decline_item(sess: Session, query: str):
    """T-023/F17 + T-026/F15: explicit abandonment. The customer can drop a
    request that is still outstanding — either an unresolved disambiguation
    ("forget the wings") or a genuine search miss ("never mind the root
    beer") — and this is the ONLY way either clears without being resolved,
    so the system stops asking/blocking on something the customer no longer
    wants. Matches loosely (case-insensitive substring, either direction)
    against the original query text, since that's the only text a caller
    has to refer to it by — same matching `_query_relates` uses for F15's
    own resolution path. Never touches the cart — declining something that
    was never added is a no-op there by construction. Always ok, even if
    nothing matched: declining something not currently pending is harmless,
    not an error worth failing a turn over.
    """
    before = len(sess.pending_disambiguations) + len(sess.unresolved_lookups)
    sess.pending_disambiguations[:] = [
        e for e in sess.pending_disambiguations if not _query_relates(e["query"], query)
    ]
    sess.unresolved_lookups[:] = [
        q for q in sess.unresolved_lookups if not _query_relates(q, query)
    ]
    after = len(sess.pending_disambiguations) + len(sess.unresolved_lookups)
    return ok(declined=query, cleared=before - after)


def check_availability(sess: Session, item: str):
    return ok(item=item.upper(), available=item.upper() not in UNAVAILABLE)


def set_order_type(sess: Session, order_type: str,
                   address: str | None = None, note: str | None = None):
    if (g := sess.guard("DRAFT", "BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    ot = (order_type or "").lower()
    if ot not in ("pickup", "delivery"):
        return err("BAD_ORDER_TYPE", "Order type must be pickup or delivery.")
    if ot == "delivery" and not address:
        return err("ADDRESS_REQUIRED", "Delivery needs a street address.")
    sess.order.order_type = "DELIVERY" if ot == "delivery" else "CARRY_OUT"
    sess.address, sess.delivery_note = address, note
    sess.invalidate_quote()
    if sess.state == "DRAFT":
        sess.to("BUILDING")
    else:
        sess.to("BUILDING")
    sess.clear_failures()
    return ok(order_type=ot, address=address)


def add_item(sess: Session, item: str, size: str | None = None,
             gourmet_number: int | None = None,
             second_gourmet_number: int | None = None, quantity: int = 1):
    if (g := sess.guard("DRAFT", "BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    if quantity < 1 or quantity > 20:
        return err("BAD_QUANTITY", "Quantity must be between 1 and 20.")

    key = (item or "").strip().upper()
    _resolved_item_name = _resolved_gourmet_numbers = None  # T-023/F17 clearing

    if key in NON_PIZZA:
        if key in UNAVAILABLE:
            return err("UNAVAILABLE", f"{key} is 86'd right now.")
        line = SimpleLine(key, NON_PIZZA[key], quantity)
        _resolved_item_name = key
    elif (key in ("PIZZA", "CHEESE PIZZA") or gourmet_number is not None
          or second_gourmet_number is not None):
        sz, e = _resolve_size(size or "")
        if e:
            return e
        if second_gourmet_number is not None:
            # HALF_AND_HALF SKU — two named specialties, larger-half-only
            # pricing (lakewood/pricing.py PizzaLine.half_and_half). Owner-
            # confirmed: this SKU is for specialty/specialty only.
            if gourmet_number is None:
                return err("BAD_HALF_AND_HALF",
                           "A half-and-half needs a specialty number for both halves.")
            names = _gourmet_names(sz)
            for n in (gourmet_number, second_gourmet_number):
                if n not in names:
                    return err("GOURMET_NOT_FOUND",
                               f"No #{n} on a {sz} pizza.", candidates=sorted(names))
                if f"#{n}" in UNAVAILABLE:
                    return err("UNAVAILABLE", f"#{n} is 86'd right now.")
            line = PizzaLine(size=sz, half_and_half=(gourmet_number, second_gourmet_number),
                             quantity=quantity)
            _resolved_gourmet_numbers = (gourmet_number, second_gourmet_number)
        else:
            if gourmet_number is not None:
                names = _gourmet_names(sz)
                if gourmet_number not in names:
                    return err("GOURMET_NOT_FOUND",
                               f"No #{gourmet_number} on a {sz} pizza.",
                               candidates=sorted(names))
                if f"#{gourmet_number}" in UNAVAILABLE:
                    return err("UNAVAILABLE", f"#{gourmet_number} is 86'd right now.")
                _resolved_gourmet_numbers = (gourmet_number,)
            else:
                _resolved_item_name = "CHEESE PIZZA"
            line = PizzaLine(size=sz, gourmet=gourmet_number, quantity=quantity)
    else:
        return err("ITEM_NOT_FOUND", f"Nothing on the menu called {item!r}.",
                   hint="Call search_menu — do not substitute.")

    lid = sess.new_line_id()
    sess.lines[lid] = line
    sess.order.lines.append(line)
    sess.invalidate_quote()
    sess.to("BUILDING")
    sess.clear_failures()
    if _resolved_item_name:
        _clear_disambiguations_matching(sess, item_name=_resolved_item_name)  # F17
    for n in (_resolved_gourmet_numbers or ()):
        _clear_disambiguations_matching(sess, gourmet_number=n)               # F17
    return ok(line_id=lid, description=_render_line(line))


def add_modifier(sess: Session, line_id: str, modifier: str,
                 portion: str = "WHOLE", intensity: str = "NORMAL"):
    if (g := sess.guard("BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    line = sess.lines.get(line_id)
    if not isinstance(line, PizzaLine):
        return err("BAD_LINE", f"{line_id} is not a pizza.")
    if portion not in ("WHOLE", "HALF_1", "HALF_2"):
        return err("BAD_PORTION", "Portion must be WHOLE, HALF_1 or HALF_2.")
    if intensity not in ("NORMAL", "DOUBLE", "TRIPLE", "LITE", "NONE"):
        return err("BAD_INTENSITY", "Intensity must be NORMAL/DOUBLE/TRIPLE/LITE/NONE.")

    name, e = _resolve_topping(modifier)
    if e:
        return e
    if name in UNAVAILABLE:
        return err("UNAVAILABLE", f"{name} is 86'd right now.")
    if name in UNKNOWN_TIER and intensity not in ("NONE", "LITE"):
        # F7 — the button exists but its price isn't confirmed. Never invent one.
        # Removing it or going light is free, so those stay allowed.
        return err("PRICE_UNKNOWN",
                   f"{name} is on the menu but its price isn't confirmed.",
                   action="transfer")

    t = Topping(name=name, portion=portion,
                qty={"DOUBLE": 2, "TRIPLE": 3}.get(intensity, 1),
                removed=(intensity == "NONE"), lite=(intensity == "LITE"))
    line.toppings.append(t)
    sess.invalidate_quote()
    sess.to("BUILDING")
    sess.clear_failures()
    _clear_disambiguations_matching(sess, topping_name=name)  # F17
    return ok(line_id=line_id, description=_render_line(line))


def remove_modifier(sess: Session, line_id: str, modifier: str,
                    portion: str | None = None):
    if (g := sess.guard("BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    line = sess.lines.get(line_id)
    if not isinstance(line, PizzaLine):
        return err("BAD_LINE", f"{line_id} is not a pizza.")
    name, e = _resolve_topping(modifier)
    if e:
        return e
    before = len(line.toppings)
    line.toppings = [t for t in line.toppings
                     if not (t.name == name and (portion is None or t.portion == portion))]
    if len(line.toppings) == before:
        return err("NOT_ON_PIZZA", f"{name} isn't on {line_id}.")
    sess.invalidate_quote()
    sess.to("BUILDING")
    sess.clear_failures()
    return ok(line_id=line_id, description=_render_line(line))


def update_item(sess: Session, line_id: str, quantity: int | None = None,
                size: str | None = None):
    if (g := sess.guard("BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    line = sess.lines.get(line_id)
    if line is None:
        return err("BAD_LINE", f"No line {line_id}.")
    if quantity is not None:
        if quantity < 1 or quantity > 20:
            return err("BAD_QUANTITY", "Quantity must be between 1 and 20.")
        line.quantity = quantity
    if size is not None:
        if not isinstance(line, PizzaLine):
            return err("BAD_LINE", f"{line_id} has no size.")
        sz, e = _resolve_size(size)
        if e:
            return e
        line.size = sz
    sess.invalidate_quote()
    sess.to("BUILDING")
    sess.clear_failures()
    return ok(line_id=line_id, description=_render_line(line))


def remove_item(sess: Session, line_id: str):
    if (g := sess.guard("BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    line = sess.lines.pop(line_id, None)
    if line is None:
        return err("BAD_LINE", f"No line {line_id}.")
    sess.order.lines.remove(line)
    sess.invalidate_quote()
    sess.to("BUILDING")
    sess.clear_failures()
    return ok(removed=line_id, remaining=len(sess.order.lines))


def apply_coupon(sess: Session, code: str | None = None):
    """
    Attach one coupon. Never stacks — the printed terms say offers cannot be
    combined, so a second call replaces the first.

    The AI does not compute the discount from the customer's description; it
    names a code and the engine decides whether it applies. If the caller just
    says "I have a coupon", pass code=None and we suggest the best eligible one.
    """
    if (g := sess.guard("BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    sub = sess.order.subtotal()

    if code is None:
        best = cp.best_coupon(sess.order.lines, sub)
        if not best:
            return err("NO_COUPON_APPLIES",
                       "Nothing on this order qualifies for a current offer.",
                       offers=[c.description for c in cp.COUPONS])
        coupon, amount = best
    else:
        amount, e = cp.apply(code, sess.order.lines, sub)
        if e:
            return err("COUPON_NOT_APPLICABLE", e,
                       offers=[c.description for c in cp.COUPONS])
        coupon = cp.BY_CODE[code.upper()]

    sess.order.coupon_code = coupon.code
    sess.order.coupon_discount = amount
    sess.invalidate_quote()
    sess.to("BUILDING")
    sess.clear_failures()
    return ok(coupon=coupon.code, description=coupon.description,
              discount=money(amount),
              note="Staff must key this code in PrISM. It is on the ticket.")


def remove_coupon(sess: Session):
    if (g := sess.guard("BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    sess.order.coupon_code = None
    sess.order.coupon_discount = 0
    sess.invalidate_quote()
    sess.to("BUILDING")
    return ok(removed=True)


def request_quote(sess: Session):
    if (g := sess.guard("BUILDING", "QUOTED", "AWAITING_CONFIRMATION")):
        return g
    if not sess.order.lines:
        return err("EMPTY_CART", "Nothing in the order yet.")
    if sess.order.order_type == "DELIVERY" and not sess.address:
        return err("ADDRESS_REQUIRED", "Delivery needs a street address.")
    if sess.order.order_type == "DELIVERY" \
            and sess.order.subtotal() < DELIVERY_MINIMUM:
        short = money(DELIVERY_MINIMUM - sess.order.subtotal())
        return err("BELOW_DELIVERY_MINIMUM",
                   f"Delivery has an ${money(DELIVERY_MINIMUM)} minimum — "
                   f"${short} short. Offer pickup or suggest adding to the order.",
                   minimum=money(DELIVERY_MINIMUM), short_by=short)
    try:
        q = sess.order.quote()
    except ValueError as e:
        # engine refused rather than guessed — F1/F7
        return _do_transfer(sess, "price_uncertain")
    sess.quote_id = f"Q-{uuid.uuid4().hex[:8]}"
    sess.quote_hash = sess.cart_hash()
    sess.quote_at = time.time()
    st = store_status()
    if not st["open"]:
        sess.scheduled_for = st.get("next_open")
    sess.to("QUOTED")
    sess.log("quoted", quote_id=sess.quote_id, total=q["total"])
    return ok(quote_id=sess.quote_id, **q,
              scheduled_for=sess.scheduled_for,
              readback=_short_readback(sess, q))


def begin_confirmation(sess: Session):
    """
    Opens the 45s confirm window AND delivers the readback the customer must
    hear before they can confirm (F16) — there is no code path to
    AWAITING_CONFIRMATION that skips building one. Refuses to open at all
    while a menu lookup this call already failed is still unresolved (F15),
    or while a menu lookup that DID succeed but came back ambiguous is still
    outstanding — nobody ever picked one of the candidates or explicitly
    declined it (F17, T-023). `confirm_order` will refuse to fire in this
    same interpreter turn (F14) — the customer's next real utterance is what
    actually confirms.
    """
    if (g := sess.guard("QUOTED")):
        return g
    if sess.unresolved_lookups or sess.pending_disambiguations:
        outstanding = list(sess.unresolved_lookups) + [
            e["query"] for e in sess.pending_disambiguations]
        return err("UNRESOLVED_REQUEST",
                   "Something the customer asked for hasn't been resolved "
                   "yet (" + ", ".join(repr(q) for q in outstanding) +
                   ") — find it, ask the customer if they'd like something "
                   "else, or explicitly tell them it isn't available before "
                   "confirming. Never confirm while a request is still "
                   "outstanding.",
                   unresolved=list(sess.unresolved_lookups),
                   outstanding_disambiguations=[
                       e["query"] for e in sess.pending_disambiguations])
    q = sess.order.quote()
    readback = _short_readback(sess, q) + " Place it?"          # T-051
    sess.to("AWAITING_CONFIRMATION")
    sess.confirmation_turn = sess.turn                     # F14
    return ok(expires_in=CONFIRM_WAIT_SECONDS, readback=readback)


def confirm_order(sess: Session, quote_id: str, idempotency_key: str | None = None):
    if idempotency_key and idempotency_key in sess.idempotency:   # F6 — before guard
        return sess.idempotency[idempotency_key]
    if (g := sess.guard("AWAITING_CONFIRMATION")):
        return g
    if not sess.quote_id or quote_id != sess.quote_id:            # F5
        return err("STALE_QUOTE", "That quote is no longer valid. Re-quote first.")
    if sess.cart_hash() != sess.quote_hash:
        return err("CART_CHANGED", "The order changed after the quote. Re-quote first.")
    if time.time() - (sess.quote_at or 0) > QUOTE_TTL_SECONDS:
        sess.invalidate_quote()
        return err("QUOTE_EXPIRED", "That quote expired. Re-quote first.")
    if sess.confirmation_turn is not None and sess.confirmation_turn == sess.turn:  # F14
        return err("PREMATURE_CONFIRMATION",
                   "The readback must be spoken and the customer must respond "
                   "again, in their own next turn, before this can be "
                   "confirmed — do not call confirm_order in the same turn "
                   "as begin_confirmation.")

    q = sess.order.quote()
    sess.to("CONFIRMED")                                          # F12
    result = ok(order_id=sess.order_id, total=q["total"],
                ticket=render_ticket(sess.order, sess.order_id, format_12h()))
    if idempotency_key:
        sess.idempotency[idempotency_key] = result
    sess.log("confirmed", order_id=sess.order_id, total=q["total"])
    return result


def cancel_order(sess: Session, reason: str = "customer_request"):
    if sess.state in TERMINAL and sess.state != "CONFIRMED":
        return err("ORDER_CLOSED", f"Already {sess.state.lower()}.")
    sess.to("CANCELLED")
    return ok(cancelled=sess.order_id, reason=reason)


def transfer_to_human(sess: Session, reason: str):
    if reason not in TRANSFER_REASONS:
        reason = "unsupported_request"
    return _do_transfer(sess, reason)


TOOLS = [get_store_info, search_menu, decline_item, check_availability, set_order_type,
         add_item, add_modifier, remove_modifier, update_item, remove_item,
         apply_coupon, remove_coupon, request_quote, begin_confirmation,
         confirm_order, cancel_order, transfer_to_human]


# ---------------------------------------------------------------------------
# Readback — cart-diff style: items + total, not a 40-second recital
#
# T-051: tightened for real spoken latency (docs/STATUS.md's T-051 entry has
# the measured before/after) WITHOUT dropping any content — every line, every
# modifier, every half placement, the coupon, and the total are still here.
# Only two things changed: (1) HOW a token is pronounced, via
# `lakewood.speech`'s speakable-rendering layer, built from real TTS->STT
# round-trip evidence, never a guess; (2) filler words around that same
# content ("That's pickup:" -> "Pickup:", "Should I go ahead and place it?"
# -> "Place it?" — the latter appended by `begin_confirmation`, not here).
# The readback remains 100% deterministic — no LLM call anywhere in this
# function or `_render_line` — that is what makes it the one utterance in
# this system that cannot hallucinate; see ADR-018's amendment and
# `tests/test_speech.py::test_readback_is_produced_without_any_model_call`.
# ---------------------------------------------------------------------------

def _render_line(l) -> str:
    if not isinstance(l, PizzaLine):
        name = speech.speakable_item_name(l.name)
        # T-051: quantity shown only when >1 (matches PizzaLine's own
        # convention below), and separated by a comma when it IS shown — a
        # real, measured ambiguity: "1 twelve-piece wings" spoken with no
        # pause risks being heard as "one hundred twelve" (confirmed even
        # for cleanly-spoken "one twelve" with no SKU involved at all; a
        # comma reliably disambiguates it). See docs/STATUS.md T-051.
        return f"{l.quantity}, {name}" if l.quantity > 1 else name
    parts = [l.display_name()]
    for label, p in (("", "WHOLE"), ("first half", "HALF_1"), ("second half", "HALF_2")):
        g = [t for t in l.toppings if t.portion == p]
        if not g:
            continue
        names = []
        for t in g:
            pre = "no " if t.removed else ("light " if t.lite else
                                           ("double " if t.qty == 2 else
                                            ("triple " if t.qty == 3 else "")))
            names.append(pre + speech.speakable_topping_name(t.name))
        parts.append((f"{label}: " if label else "") + ", ".join(names))
    s = " — ".join(parts)
    return f"{l.quantity} × {s}" if l.quantity > 1 else s


def _speak_list(items: list[str]) -> str:
    """Natural spoken list join — 'A', 'A and B', 'A, B, and C' — instead of
    semicolon-joining every line regardless of count. Purely a join-style
    choice; never drops an item."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _short_readback(sess: Session, q: dict) -> str:
    kind = "Delivery" if sess.order.order_type == "DELIVERY" else "Pickup"
    items = _speak_list([_render_line(l) for l in sess.order.lines])
    line = f"{kind}: {items}."
    if sess.order.coupon_code:
        # T-051: the coupon's own `spoken` field (coupons.py — "how a
        # caller is likely to ask for it") existed for exactly this and was
        # simply never used here; the raw CODE was spoken instead, which
        # SAPI reads with literal "underscore" words — measured real, see
        # docs/STATUS.md T-051.
        phrase = speech.speakable_coupon_phrase(sess.order.coupon_code)
        line += f" With {phrase}, ${q['discount']} off."
    line += f" Total ${q['total']}."
    if sess.scheduled_for:
        # Non-negotiable disclosure. A caller must never think an after-hours
        # order is coming tonight.
        line += (f" We're closed right now, so this is held for "
                 f"{sess.scheduled_for} — not tonight.")
    return line
