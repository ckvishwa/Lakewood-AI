"""Customer text to the existing structured ToolCall contract.

RuleBasedInterpreter proposes calls for chat.py; LLMInterpreter executes the
same orders.TOOLS against a staged session and reports already_executed calls.
All menu, pricing and state validation remains in the deterministic engine.
The default rule-based mode needs no credentials or network access.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from . import orders as oe
from .llm_provider import LLMProvider, ProviderCallError, make_provider
from .menu import GOURMET_ROUND, SIZES

# The real, single tool surface every interpreter (rule-based or LLM) must go
# through — never a parallel/duplicated copy. Introspected once here so a
# tool schema generator (LLMInterpreter) and the executor (chat.py) share the
# exact same set orders.py itself defines.
TOOLS = {f.__name__: f for f in oe.TOOLS}


def substitute_last_line(args: dict, last_new_line_id):
    """Resolve the "$LAST" convention both interpreters use for a line_id
    that doesn't exist yet at interpretation time — the pizza this same
    batch of calls is about to create via add_item."""
    return {k: (last_new_line_id if v == "$LAST" else v) for k, v in args.items()}


@dataclass
class ToolCall:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class Interpretation:
    """What one turn produced. Exactly one of:
    - `calls`: tool calls the executor (chat.py) should run, in order. This
      is what RuleBasedInterpreter always uses — it never executes anything
      itself.
    - `already_executed`: `[{"tool","args","result"}]` calls an interpreter
      ran ITSELF (against the real tools, same as the executor would) and is
      reporting back. LLMInterpreter uses this — the Anthropic tool-use
      protocol requires seeing each call's real result before the turn is
      complete (to send back a matching tool_result block), so by the time
      `interpret()` returns, the calls already happened for real. The
      executor must not re-run them.
    - `say`: a message to show WITHOUT running a tool — a clarification
      question or plain acknowledgement. Never combined with a guessed call;
      if unsure, `calls`/`already_executed` are empty and `say` asks.
    """
    calls: list[ToolCall] = field(default_factory=list)
    already_executed: list[dict] = field(default_factory=list)
    say: Optional[str] = None


class TextInterpreter(Protocol):
    def interpret(self, chat: "ChatState", text: str) -> Interpretation: ...


# ---------------------------------------------------------------------------
# ChatState — the conversational bookkeeping that isn't already authoritative
# on orders.Session. Cart, quote_id, state, and total are NOT duplicated here
# — they live on `session` and nothing else. This only tracks what turn N
# needs to resolve turn N+1's pronouns ("it", "one half", "the pizza"), or
# (llm_history) what an LLM needs to remember its own prior turns.
# ---------------------------------------------------------------------------

@dataclass
class ChatState:
    session: oe.Session
    last_line_id: Optional[str] = None
    # Presentation/pronoun cache only. Session.pending_disambiguations is the
    # one authoritative persisted clarification state. PersistentChat restores
    # this cache from that state after reload; successful resolution and
    # deterministic narrowing keep both synchronized.
    pending_clarification: Optional[list[dict]] = None
    llm_history: list = field(default_factory=list)     # LLMInterpreter only
    tool_executor: object | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Vocabulary — built from the SAME constants orders.py resolves against, so
# this module can never recognize an item the domain engine wouldn't. It only
# ever narrows "does this utterance plausibly mention a size/topping", never
# decides an item is valid — add_item/add_modifier still do that for real.
# ---------------------------------------------------------------------------

_TOPPING_VOCAB = sorted(
    {t.lower() for t in oe.ALL_TOPPINGS} | set(oe.ALIASES.keys()),
    key=len, reverse=True,
)
_SIZE_VOCAB = sorted(
    {s.lower() for s in SIZES} | set(oe.SIZE_ALIASES.keys()),
    key=len, reverse=True,
)
# Bare "cheese" is a real alias for MOZZARELLA (a customer can ask to "add
# cheese"), but "large cheese [pizza]" means the base item, not an extra
# topping — CHEESE PIZZA is already the item add_item creates. Excluded from
# plain new-pizza clause scanning; "extra cheese" (a distinct, longer phrase)
# still works, and so does bare "cheese" when qualified by negation/lite
# ("no cheese on that half" is a real exclusion request, not a base-item
# mention) — see _new_pizza's clause loop.
_NEW_PIZZA_TOPPING_VOCAB = [p for p in _TOPPING_VOCAB if p != "cheese"]

# T-039: a bare "can" used to be its own trigger here (the ordinary modal
# verb in "can I get...", not a drink mention) and this dict was a second,
# independently-maintained copy of orders.py's NON_PIZZA_ALIASES that never
# got T-032's "two liter" > "coke" precedence fix, so "a two liter coke"
# silently resolved to a can. Both bugs are closed the same way: drink
# resolution now goes through oe.non_pizza_alias_hits — the one real alias
# table and precedence rule search_menu itself uses — so this fast path can
# never again drift out of sync with it.

# T-039A: T-039's own fix (a `_NON_PIZZA_HEAD_WORDS` denylist of 12 literal
# nouns) does not establish the general invariant — any product noun NOT on
# that list ("nachos", "soup", "tacos", "appetizer", "garlic bread", or any
# future menu item) still falls straight through to `_new_pizza`'s
# unconditional default, because the denylist encodes "these known bad
# words," never "positive evidence of pizza intent." Replaced entirely
# (never expanded) by fail-closed intent parsing below — see ADR-017.

# Ordinary connective/filler words that carry no product-identifying content
# in a pizza-shorthand utterance. Deliberately narrow and reused nowhere else
# a real ingredient/item word could hide — "please"/"can"/"i" etc. are never
# menu-relevant, unlike a word this list must NOT include (any real topping,
# size, or item name).
_PIZZA_SHORTHAND_FILLER_RE = re.compile(
    r"\b(a|an|the|and|with|please|i'd|id|like|get|want|order|of|only|"
    r"just|some|me|can|i|for|on|that|this|side|other|actually|make|really|"
    r"add|to|too)\b",
    re.I)
_QUANTITY_WORD_RE = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
# "pie" is common colloquial slang for pizza ("a plain pie, medium") — as
# strong an explicit-pizza signal as the word "pizza" itself, not a shorthand
# grammar token to be balanced against a residual.
_PIZZA_WORD_RE = re.compile(r"\bpizza\b|\bpie\b", re.I)


def _pizza_shorthand_residual(t: str) -> str:
    """What's left of an utterance after removing every token a tight pizza-
    shorthand grammar recognizes: sizes, toppings (the FULL vocabulary,
    including bare "cheese" — "small cheese" is a valid order even though
    `_new_pizza`'s own topping-scavenging loop deliberately excludes bare
    "cheese" for pricing purposes), half/portion phrasing, negation/lite
    modifiers, quantities, and ordinary connective filler. An empty residual
    means the utterance is FULLY explained as pizza shorthand — "large
    pepperoni" and "small cheese" both clear this with no "pizza" word ever
    said. A non-empty residual ("nachos", "soup", "garden salad" — none of
    them a recognized pizza token) is positive evidence of something ELSE,
    and pizza creation must not be assumed from it.
    """
    residual = t
    for pattern in (_HALF_A_HALF_B_RE, _HH_BY_NUMBER_RE, _ONE_HALF_RE,
                    _OTHER_HALF_RE, _BARE_HALF_RE, _NEGATION_RE, _LITE_RE,
                    _QUANTITY_WORD_RE, _PIZZA_SHORTHAND_FILLER_RE):
        residual = pattern.sub(" ", residual)
    for phrase in _TOPPING_VOCAB:  # includes bare "cheese"; longest-first
        residual = re.sub(rf"\b{re.escape(phrase)}\b", " ", residual)
    for phrase in _SIZE_VOCAB:
        residual = re.sub(rf"\b{re.escape(phrase)}\b", " ", residual)
    # Clause punctuation (commas from "pepperoni, no onions") is not itself
    # a product word — strip it before judging whether anything meaningful
    # is left, or a bare leftover comma would wrongly count as "unexplained."
    residual = re.sub(r"[^\w\s]", " ", residual)
    return re.sub(r"\s+", " ", residual).strip()


def _has_pizza_intent(text: str) -> bool:
    """T-039A invariant: a request may create a pizza only when the
    utterance provides positive, deterministic evidence the customer
    intended a pizza — either the explicit word "pizza"/a recognized
    gourmet pizza, or a fully-explained pizza-shorthand utterance (every
    meaningful token is a size/topping/modifier/quantity/filler, nothing
    left unaccounted for). Shared by BOTH interpreters' mutation boundary:
    `RuleBasedInterpreter` gates `_new_pizza`/the bare-topping graft with it
    directly; `LLMInterpreter` gates any model `add_item` call that would
    create a pizza with the SAME function (see `_authorize_item_creation`
    below) — one predicate, not two independently-drifting checks.
    """
    t = text.strip().lower()
    if _PIZZA_WORD_RE.search(t):
        return True
    return _pizza_shorthand_residual(t) == ""


# T-039B: stable, distinguishable authorization outcomes for `add_item` —
# a retrieved candidate is evidence for clarification, never customer
# consent by itself. Only the first three ever authorize a mutation.
AUTH_DIRECT_UTTERANCE_EVIDENCE = "DIRECT_UTTERANCE_EVIDENCE"
AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT = "UNIQUE_SUPPORTED_SEARCH_RESULT"
AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE = "CUSTOMER_CONFIRMED_PENDING_CANDIDATE"
AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED = "AMBIGUOUS_CANDIDATE_NOT_CONFIRMED"
AUTH_UNSUPPORTED_ITEM_SUBSTITUTION = "UNSUPPORTED_ITEM_SUBSTITUTION"

_AUTHORIZED_REASONS = {
    AUTH_DIRECT_UTTERANCE_EVIDENCE,
    AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT,
    AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE,
}


def _size_supported_by_utterance(text: str, proposed_size: str | None) -> bool:
    """A model may not manufacture a size attribute the customer never gave."""
    if not proposed_size:
        return True  # the domain tool will return SIZE_REQUIRED when applicable
    proposed = oe.SIZE_ALIASES.get(str(proposed_size).strip().lower(),
                                   str(proposed_size).strip().upper())
    spoken = _find_vocab(text.lower(), _SIZE_VOCAB)
    return bool(spoken and oe.SIZE_ALIASES.get(spoken, spoken.upper()) == proposed)


def _words_present(phrase: str, text: str) -> bool:
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    return bool(words) and all(re.search(rf"\b{re.escape(w)}\b", text.lower()) for w in words)


def _search_query_supported_by_utterance(query: str, text: str) -> bool:
    """The model's query is never evidence; every identifying query token
    must already be present in the customer's current utterance."""
    filler = {
        "a", "an", "the", "i", "id", "i'd", "want", "like", "get", "give",
        "me", "please", "order", "some", "one", "of", "menu", "item",
    }
    words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower())
             if w not in filler]
    return bool(words) and all(re.search(rf"\b{re.escape(w)}\b", text.lower()) for w in words)


def _item_hit_supported_by_utterance(hit: dict, text: str) -> bool:
    """Deterministic customer-language evidence for the exact retrieved SKU."""
    if hit.get("kind") == "gourmet":
        number = hit.get("number")
        numbered = bool(re.search(rf"(?:#|\bnumber\s*){number}\b", text.lower()))
        return numbered or _words_present(hit.get("name", ""), text)
    if hit.get("kind") != "item":
        return False
    name = hit.get("name", "")
    if name == "CHEESE PIZZA":
        return _has_pizza_intent(text)
    if any(canon == name for _, canon in oe.non_pizza_alias_hits(text.lower())):
        return True
    words = name.lower().replace("-", " ").split()
    encoded_size = None
    identifying = []
    for word in words:
        canon = oe.SIZE_ALIASES.get(word)
        if canon in ("SMALL", "LARGE"):
            encoded_size = canon
        else:
            identifying.append(word)
    if not identifying or not all(re.search(rf"\b{re.escape(w)}\b", text.lower())
                                   for w in identifying):
        return False
    if encoded_size is None:
        return True
    spoken = _find_vocab(text.lower(), _SIZE_VOCAB)
    return bool(spoken and oe.SIZE_ALIASES.get(spoken, spoken.upper()) == encoded_size)


def _matching_search_hit(item_key: str, gourmet_number, search_hits: list[dict]):
    for hit in search_hits:
        if hit.get("kind") == "item" and hit.get("name") == item_key:
            yield hit
        elif (hit.get("kind") == "gourmet" and gourmet_number is not None
              and hit.get("number") == gourmet_number):
            yield hit


def _select_pending_candidate(text: str, pending_candidates: list[dict]):
    """T-039B: deterministically match a customer utterance against an
    OUTSTANDING set of pending disambiguation candidates (item-kind hits
    with a real menu `name`, e.g. "GARDEN SALAD SM"/"GARDEN SALAD LG"/
    "SALAD SM"/"SALAD LG"). Never a guess: a candidate only counts as
    matched when its own distinguishing words are literally present in the
    utterance, and a trailing size abbreviation (SM/LG) only counts when it
    agrees with a size word the utterance itself states.

    Returns one of:
      ("SELECTED", candidate)   — exactly one candidate is uniquely
                                  supported ("the large garden salad" among
                                  the four above -> GARDEN SALAD LG).
      ("NARROWED", [candidates]) — the utterance narrows the set (e.g. names
                                  the GARDEN family) but more than one
                                  candidate remains equally supported,
                                  typically because a further attribute
                                  (size) is still unstated ("the garden
                                  one" -> both GARDEN SALAD SM/LG).
      ("NO_MATCH", None)        — nothing in the utterance matches any
                                  candidate at all.

    Prefers the MOST SPECIFIC candidate name a customer's words fully cover
    ("garden salad" beats bare "salad" for an utterance mentioning both)
    over a partial/generic one, so a customer who says "garden" is never
    silently routed to the plain SALAD SKU just because "salad" alone is
    also, trivially, a substring of their own words.

    A bare size-only reply ("large", with no type word at all) selects
    uniquely when EVERY remaining candidate already names the same item
    family (already narrowed to just a size choice by an earlier turn,
    e.g. "the garden one" -> GARDEN SALAD SM/LG -> "large") — otherwise a
    bare size alone is not evidence for which family it belongs to.
    """
    t = text.strip().lower()
    utterance_size = _find_vocab(t, _SIZE_VOCAB)
    utterance_size_canon = oe.SIZE_ALIASES.get(utterance_size) if utterance_size else None

    parsed = []  # (type_words, cand_size, hit)
    for h in pending_candidates:
        if h.get("kind") != "item" or not h.get("name"):
            continue
        words = h["name"].lower().replace("-", " ").split()
        cand_size, type_words = None, []
        for w in words:
            canon = oe.SIZE_ALIASES.get(w)
            if canon in ("SMALL", "LARGE"):
                cand_size = canon
            else:
                type_words.append(w)
        if not type_words:
            continue
        parsed.append((tuple(type_words), cand_size, h))

    if not parsed:
        return "NO_MATCH", None

    families = {p[0] for p in parsed}
    if len(families) == 1 and utterance_size_canon:
        same_size = [h for _, sz, h in parsed if sz == utterance_size_canon]
        if len(same_size) == 1:
            return "SELECTED", same_size[0]

    scored = [(sum(1 for w in tw if re.search(rf"\b{re.escape(w)}\b", t)), len(tw), sz, h)
             for tw, sz, h in parsed]
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        return "NO_MATCH", None

    max_hits = max(s[0] for s in scored)
    top = [s for s in scored if s[0] == max_hits]
    # A FULLY-named candidate (every one of its own words present) beats a
    # partial one at the same hit count.
    full = [s for s in top if s[0] == s[1]]
    pool = full if full else top

    if utterance_size_canon:
        size_filtered = [s for s in pool if s[2] is None or s[2] == utterance_size_canon]
        if size_filtered:
            pool = size_filtered

    candidates = [s[3] for s in pool]
    if len(candidates) == 1:
        return "SELECTED", candidates[0]
    return "NARROWED", candidates


def _authorize_item_creation(item_key: str, size, gourmet_number, second_gourmet_number,
                             text: str, search_hits: list[dict],
                             pending_disambiguations: list[dict]) -> str:
    """T-039A/T-039B: the LLM-path mutation boundary for `add_item` — covers
    a substituted PIZZA just as much as a substituted valid NON_PIZZA SKU
    (a model calling `add_item(item="CAN")` for "a small garden salad" is
    the same defect class as substituting CHEESE PIZZA, just a different
    valid item). Returns a stable reason code (`_AUTHORIZED_REASONS`
    membership decides whether the mutation proceeds), never a bare bool,
    so tests/traces can tell WHY.

    Pizza (`item_key` is PIZZA/CHEESE PIZZA, or a gourmet number given):
    `_has_pizza_intent` (the same predicate the rule-based path is gated
    by) is direct evidence; a matching gourmet/CHEESE-PIZZA search hit this
    turn is a unique search result — but ONLY if that hit's own search call
    was not itself `needs_disambiguation=True` (`h["_ambiguous"]`) AND both
    the model-controlled query and the exact returned SKU are independently
    supported by the customer's current words. A candidate retrieved under
    ambiguity, or by an unsupported query, is evidence for clarification,
    never customer consent. This is the exact bug T-039B closes: T-039A's
    guard flattened every hit into one list and treated retrieval itself as
    authorization.

    Any other (non-pizza) item: `oe.non_pizza_alias_hits` is direct
    evidence; an unambiguous search hit this turn naming that exact item
    is a unique result, same ambiguity check as above.

    If neither direct evidence nor an unambiguous hit authorizes the call,
    the last legitimate path is an EXPLICIT customer selection from
    whatever is actually outstanding — `pending_disambiguations` is
    server-owned F17 state (`chat.session.pending_disambiguations`), never
    anything the model supplies; `_select_pending_candidate` checks THIS
    utterance's own words against it. The model can request a selection,
    it can never manufacture one.
    """
    creates_pizza = item_key in ("PIZZA", "CHEESE PIZZA") \
        or gourmet_number is not None or second_gourmet_number is not None

    if creates_pizza:
        if (gourmet_number is None and second_gourmet_number is None
                and _has_pizza_intent(text)
                and _size_supported_by_utterance(text, size)):
            return AUTH_DIRECT_UTTERANCE_EVIDENCE
    else:
        if not item_key:
            return AUTH_DIRECT_UTTERANCE_EVIDENCE  # nothing to authorize; BAD_ARGS handles this
        if any(canon == item_key for _, canon in oe.non_pizza_alias_hits(text.lower())):
            return AUTH_DIRECT_UTTERANCE_EVIDENCE

    matching_hits = list(_matching_search_hit(item_key, gourmet_number, search_hits))
    for hit in matching_hits:
        if hit.get("_ambiguous"):
            continue
        if not _search_query_supported_by_utterance(hit.get("_search_query", ""), text):
            continue
        if not _item_hit_supported_by_utterance(hit, text):
            continue
        if creates_pizza and not _size_supported_by_utterance(text, size):
            continue
        if second_gourmet_number is not None:
            second = [h for h in search_hits if h.get("kind") == "gourmet"
                      and h.get("number") == second_gourmet_number
                      and not h.get("_ambiguous")
                      and _search_query_supported_by_utterance(h.get("_search_query", ""), text)
                      and _item_hit_supported_by_utterance(h, text)]
            if not second:
                continue
        return AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT

    for entry in pending_disambiguations:
        outcome, selected = _select_pending_candidate(text, entry.get("candidates", []))
        if outcome == "SELECTED" and selected.get("name") == item_key:
            return AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE

    ambiguous_hit_for_this_item = any(h.get("_ambiguous") for h in matching_hits)
    if ambiguous_hit_for_this_item:
        return AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED
    return AUTH_UNSUPPORTED_ITEM_SUBSTITUTION

_CONFIRM_RE = re.compile(
    r"\b(yes|yeah|yep|sure).{0,20}\b(place|confirm|go ahead)\b"
    r"|\bplace (my |the )?order\b|\bconfirm (it|my order)\b|\bgo ahead\b",
    re.I)
_DONE_RE = re.compile(
    r"\bthat'?s (it|all)\b|\bnothing else\b|\bthat'?ll be all\b", re.I)
_QUOTE_RE = re.compile(
    r"\bwhat'?s (my|the) total\b|\bhow much\b|\bwhat do i owe\b", re.I)
_REPLACE_RE = re.compile(
    r"\breplace (?:the )?(?P<old>[\w ]+?) with (?:a |an )?(?P<new>[\w ]+)\b", re.I)
_SWAP_RE = re.compile(
    r"\bswap (?:the )?(?P<old>[\w ]+?) for (?:a |an )?(?P<new>[\w ]+)\b", re.I)
_HH_BY_NUMBER_RE = re.compile(
    # \D (non-digit), not `.`, for the filler — `.` would greedily eat into
    # a two-digit number itself (e.g. "half number 10" -> n captured as "0").
    r"\bhalf\D{0,10}?(?P<n1>\d{1,2})\D{0,20}?half\D{0,10}?(?P<n2>\d{1,2})\b", re.I)
_HALF_A_HALF_B_RE = re.compile(
    r"\bhalf (?P<a>[\w ]+?) half (?P<b>[\w ]+)\b", re.I)
_ONE_HALF_RE = re.compile(
    r"\bon(?:ly)? (?:one|a) (?:half|side)\b|\bon (?:that|this) (?:half|side)\b", re.I)
_OTHER_HALF_RE = re.compile(r"\b(?:on )?the other (?:half|side)\b", re.I)
# Bare "half X" (no "on") — "half pepperoni, no cheese on that half" names
# the pepperoni's half without ever saying "on". Checked only when neither
# of the two patterns above already matched; _new_pizza assigns HALF_1 to
# the first such clause in an utterance and HALF_2 to the next, so "half
# pepperoni half sausage" (as two SEPARATE clauses, not the classic
# _HALF_A_HALF_B_RE single-clause form) still lands on opposite halves.
_BARE_HALF_RE = re.compile(r"\bhalf\b", re.I)

# T-015: negation/exclusion phrasing. Deliberately unified — "no X",
# "without X", "hold the X", "leave X off"/"leave off X", "take X off"/
# "take off X", and "minus X" are all treated as the SAME intent (fully
# exclude X, at $0), regardless of whether the customer's grammar is
# preventive ("no cheese") or retroactive ("take the cheese off"). See
# _resolve_topping_intensity for why: which of the two existing tool
# operations this becomes (remove_modifier vs. add_modifier NONE) depends on
# whether X is currently a real charged topping on the line, not on tense.
_NEGATION_RE = re.compile(
    r"\bno\b|\bwithout\b|\bhold the\b|\bhold\b|\bminus\b"
    r"|\bleave\b.*\boff\b|\btake\b.*\boff\b", re.I)
# "light X"/"easy on X" — a distinct, separately-priced-as-free intensity
# (LITE), not full exclusion. Never conflated with negation.
_LITE_RE = re.compile(r"\blight\b|\beasy on\b", re.I)


def _find_vocab(text: str, vocab: list[str]) -> Optional[str]:
    """First (longest-first) vocabulary phrase literally present in text."""
    for phrase in vocab:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return phrase
    return None


def _find_drink(text: str) -> Optional[str]:
    hits = oe.non_pizza_alias_hits(text)
    return hits[0][1] if hits else None


def _clean_query(t: str) -> str:
    """Strip conversational filler before handing text to search_menu — the
    real menu lookup, never a guess. Shared by every branch that falls back
    to a real lookup (the plain fallback and the pizza-intent gate) so there
    is one query-cleaning rule, not copies that could drift apart."""
    query = re.sub(r"\b(give me|i want|i'd like|can i get|a|an|the)\b", " ", t)
    return re.sub(r"\s+", " ", query).strip() or t


class RuleBasedInterpreter:
    """
    Deterministic, no-network, no-credentials interpreter. Handles the
    phrasings T-012 requires (see docs/STATUS.md T-012 milestone) and a
    reasonable spread of the golden corpus's phrasing. Not an NLU system —
    a fixed, ordered set of pattern checks, each of which either produces
    tool calls or a clarification, never both a guess and a hedge.
    """

    def interpret(self, chat: ChatState, text: str) -> Interpretation:
        # Strip sentence punctuation but keep commas — clause-splitting in
        # _new_pizza depends on them to scope a half-modifier to the right
        # topping ("pepperoni, mushroom only on one half" must not leak
        # "one half" onto pepperoni).
        t = re.sub(r"[.!?]+", "", text.strip().lower())
        sess = chat.session

        # 0. Answering a pending clarification from the previous turn.
        if chat.pending_clarification is not None:
            resolved = self._resolve_clarification(chat, t)
            if resolved is not None:
                return resolved
            # Didn't recognize the answer — keep asking rather than guess.
            return Interpretation(say="Sorry, which one did you mean?")

        # 1. Explicit confirmation.
        if _CONFIRM_RE.search(t):
            return self._confirm(sess)

        # 2. "that's it" — done adding, not yet a confirmation.
        if _DONE_RE.search(t):
            if not sess.order.lines:
                return Interpretation(say="You haven't added anything yet.")
            return Interpretation(calls=[ToolCall("request_quote")])

        # 3. Quote / total question.
        if _QUOTE_RE.search(t):
            if not sess.order.lines:
                return Interpretation(say="Your cart is empty right now.")
            return Interpretation(calls=[ToolCall("request_quote")])

        # 4. Correction: "replace X with Y" / "swap X for Y".
        m = _REPLACE_RE.search(t) or _SWAP_RE.search(t)
        if m:
            return self._replace_modifier(chat, m.group("old"), m.group("new"))

        # 5. HALF_AND_HALF SKU by specialty number — "half #8 half #10".
        m = _HH_BY_NUMBER_RE.search(t)
        if m:
            return self._half_and_half_by_number(
                chat, t, int(m.group("n1")), int(m.group("n2")))

        # 6. Classic "half A half B" phrasing at pizza creation.
        size = _find_vocab(t, _SIZE_VOCAB)
        m = _HALF_A_HALF_B_RE.search(t)
        if m and size:
            return self._new_pizza_half_a_half_b(chat, size, m.group("a"), m.group("b"))

        # 7. Generic drink mention.
        drink_key = _find_drink(t)
        if drink_key and not size:
            return Interpretation(calls=[ToolCall("add_item", {"item": drink_key})])

        # 8. Size (+ optional toppings) — start a new pizza, but ONLY when
        # the utterance carries positive, deterministic evidence of pizza
        # intent (T-039A — replaces T-039's finite noun denylist, which
        # could never cover an unlisted product noun like "nachos"/"soup").
        # "small" is also a real GARDEN SALAD SM size; a size word alone is
        # not evidence. Never guess: route to a real menu lookup instead.
        if size:
            if _has_pizza_intent(t):
                return self._new_pizza(chat, t, size)
            return Interpretation(calls=[
                ToolCall("search_menu", {"query": _clean_query(t)})])

        # 9. Bare topping mention on an already-open pizza line — including
        # negation/lite said as its own follow-up turn ("no onions" after
        # the pizza already exists), not just at creation time. Same
        # invariant: an utterance that also carries an unexplained product
        # noun ("one calzone with mozzarella and ricotta") must never graft
        # onto the open line just because it happens to contain a real
        # topping word too — route to a real menu lookup instead.
        topping = _find_vocab(t, _TOPPING_VOCAB)
        if topping and chat.last_line_id:
            if not _has_pizza_intent(t):
                return Interpretation(calls=[
                    ToolCall("search_menu", {"query": _clean_query(t)})])
            # Canonicalize BEFORE comparing against Topping.name (always
            # stored canonical/uppercase) — _find_vocab returns the raw
            # lowercase vocab phrase, which add_modifier alone would resolve
            # on its own, but _resolve_intensity_calls's own existing-
            # topping check needs the same canonical form to match at all.
            topping = oe.ALIASES.get(topping, topping.upper())
            portion = "HALF_1" if _ONE_HALF_RE.search(t) else (
                "HALF_2" if _OTHER_HALF_RE.search(t) else "WHOLE")
            intensity = "NONE" if _NEGATION_RE.search(t) else (
                "LITE" if _LITE_RE.search(t) else "NORMAL")
            return Interpretation(calls=self._resolve_intensity_calls(
                chat, chat.last_line_id, topping, portion, intensity))

        # 10. Fall through to a real menu lookup — never guess an item.
        return Interpretation(calls=[
            ToolCall("search_menu", {"query": _clean_query(t)})], say=None)

    # -- helpers --------------------------------------------------------

    def _confirm(self, sess: oe.Session) -> Interpretation:
        # T-019/F14: begin_confirmation and confirm_order must land in
        # separate turns — the customer has to hear the readback and
        # respond again before it's final. From QUOTED this call only opens
        # the window; the customer's NEXT affirmative (matched by this same
        # _CONFIRM_RE, now with the state AWAITING_CONFIRMATION) is what
        # actually confirms, via the branch directly below.
        if sess.state == "QUOTED":
            return Interpretation(calls=[ToolCall("begin_confirmation")])
        if sess.state == "AWAITING_CONFIRMATION":
            return Interpretation(calls=[
                ToolCall("confirm_order", {"quote_id": sess.quote_id})])
        if sess.state == "BUILDING":
            return Interpretation(say="Want me to get your total first?")
        return Interpretation(say="There's nothing pending to confirm.")

    def _replace_modifier(self, chat: ChatState, old: str, new: str) -> Interpretation:
        old, new = old.strip(), new.strip()
        old_name = oe.ALIASES.get(old, old.upper())
        new_name = oe.ALIASES.get(new, new.upper())
        line_id = chat.last_line_id
        if not line_id:
            return Interpretation(say="Which pizza is that on?")
        line = chat.session.lines.get(line_id)
        portion = "WHOLE"
        if line is not None:
            for top in getattr(line, "toppings", []):
                if top.name == old_name:
                    portion = top.portion
                    break
        return Interpretation(calls=[
            ToolCall("remove_modifier", {"line_id": line_id, "modifier": old_name,
                                         "portion": portion}),
            ToolCall("add_modifier", {"line_id": line_id, "modifier": new_name,
                                      "portion": portion}),
        ])

    def _half_and_half_by_number(self, chat: ChatState, t: str,
                                 n1: int, n2: int) -> Interpretation:
        size = _find_vocab(t, _SIZE_VOCAB)
        if not size:
            chat.pending_clarification = [{"kind": "size_needed", "n1": n1, "n2": n2}]
            return Interpretation(say="What size would you like?")
        return Interpretation(calls=[ToolCall("add_item", {
            "item": "PIZZA", "size": size,
            "gourmet_number": n1, "second_gourmet_number": n2,
        })])

    def _new_pizza_half_a_half_b(self, chat: ChatState, size: str,
                                 a: str, b: str) -> Interpretation:
        a_name = oe.ALIASES.get(a.strip(), a.strip().upper())
        b_name = oe.ALIASES.get(b.strip(), b.strip().upper())
        return Interpretation(calls=[
            ToolCall("add_item", {"item": "CHEESE PIZZA", "size": size}),
            ToolCall("add_modifier", {"line_id": "$LAST", "modifier": a_name,
                                      "portion": "HALF_1"}),
            ToolCall("add_modifier", {"line_id": "$LAST", "modifier": b_name,
                                      "portion": "HALF_2"}),
        ])

    def _new_pizza(self, chat: ChatState, t: str, size: str) -> Interpretation:
        """
        Toppings are parsed per clause (split on ',' / '.' / ' and '), not
        against the whole sentence — a half-modifier phrase must only apply
        to the topping it's actually next to. "large pepperoni, mushroom
        only on one half" must not let "one half" leak onto pepperoni.
        """
        calls = [ToolCall("add_item", {"item": "CHEESE PIZZA", "size": size})]
        rest = t.replace(size, "", 1)
        bare_halves_assigned = 0  # first bare "half X" clause -> HALF_1, next -> HALF_2
        for clause in re.split(r"[,.]| and ", rest):
            negated, lite = _NEGATION_RE.search(clause), _LITE_RE.search(clause)
            # Bare "cheese" normally means the base item (see
            # _NEW_PIZZA_TOPPING_VOCAB), but "no cheese"/"light cheese" on a
            # clause is a real exclusion/intensity request, not a base-item
            # mention — use the full vocabulary in that case.
            vocab = _TOPPING_VOCAB if (negated or lite) else _NEW_PIZZA_TOPPING_VOCAB
            phrase = _find_vocab(clause, vocab)
            if not phrase:
                continue
            name = oe.ALIASES.get(phrase, phrase.upper())
            if _OTHER_HALF_RE.search(clause):
                portion = "HALF_2"
            elif _ONE_HALF_RE.search(clause):
                portion = "HALF_1"
            elif _BARE_HALF_RE.search(clause):
                # "half pepperoni, no cheese on that half" — neither half
                # mention says "on", so fall back to first-seen-is-HALF_1.
                bare_halves_assigned += 1
                portion = "HALF_1" if bare_halves_assigned == 1 else "HALF_2"
            else:
                portion = "WHOLE"
            intensity = "NONE" if negated else ("LITE" if lite else "NORMAL")
            calls += self._resolve_intensity_calls(chat, "$LAST", name, portion, intensity)
        return Interpretation(calls=calls)

    def _resolve_intensity_calls(self, chat: ChatState, line_id: str, name: str,
                                 portion: str, intensity: str) -> list[ToolCall]:
        """
        T-015. `intensity` is NORMAL/NONE/LITE for one (name, portion) slot.
        Deterministic: which existing tool operation(s) this becomes depends
        only on real cart state, never on the grammar of what the customer
        said — retroactive ("take X off") and preventive ("no X") phrasing
        resolve identically once we know whether X is currently a real
        charged topping there.

        NORMAL: the plain add — unchanged from before this fix.

        NONE (full exclusion) + X already a real charged topping there:
            `remove_modifier` alone — deletes it cleanly. This is exactly
            the pre-existing, already-tested behavior for "actually take the
            mushrooms off" (MOD-034); T-015 does not change it.
        NONE + X not currently there:
            `add_modifier(intensity=NONE)` — records the $0 exclusion, the
            exact pattern GOURMET-005's "no pineapple" already uses and is
            verified against (kitchen sees "<no> X" on the ticket even
            though nothing was ever going to charge for it).

        LITE + X already a real charged topping there:
            remove it first, then re-add as LITE. The customer still wants
            the topping, just lighter — a plain `remove_modifier` would
            drop it entirely, and `add_modifier` never merges with an
            existing same-named topping on its own.
        LITE + X not currently there:
            `add_modifier(intensity=LITE)` directly — MOD-033's "light
            onions" pattern, unchanged.
        """
        if intensity == "NORMAL":
            return [ToolCall("add_modifier", {
                "line_id": line_id, "modifier": name, "portion": portion})]

        existing_full = False
        line = chat.session.lines.get(line_id)
        if line is not None:
            for top in getattr(line, "toppings", []):
                if (top.name == name and top.portion == portion
                        and not top.removed and not top.lite):
                    existing_full = True
                    break

        if intensity == "NONE":
            if existing_full:
                return [ToolCall("remove_modifier", {
                    "line_id": line_id, "modifier": name, "portion": portion})]
            return [ToolCall("add_modifier", {
                "line_id": line_id, "modifier": name, "portion": portion,
                "intensity": "NONE"})]

        calls = []
        if existing_full:
            calls.append(ToolCall("remove_modifier", {
                "line_id": line_id, "modifier": name, "portion": portion}))
        calls.append(ToolCall("add_modifier", {
            "line_id": line_id, "modifier": name, "portion": portion,
            "intensity": "LITE"}))
        return calls

    def _resolve_clarification(self, chat: ChatState, t: str) -> Optional[Interpretation]:
        cands = chat.pending_clarification
        chat.pending_clarification = None
        if cands and cands[0].get("kind") == "size_needed":
            size = _find_vocab(t, _SIZE_VOCAB)
            if not size:
                chat.pending_clarification = cands
                return None
            return Interpretation(calls=[ToolCall("add_item", {
                "item": "PIZZA", "size": size,
                "gourmet_number": cands[0]["n1"],
                "second_gourmet_number": cands[0]["n2"]})])
        num_m = re.search(r"\bnumber\s*(\d{1,2})\b|#\s*(\d{1,2})\b", t)
        if num_m:
            n = int(num_m.group(1) or num_m.group(2))
            for c in cands:
                if c.get("kind") == "gourmet" and c.get("number") == n:
                    return Interpretation(say=(
                        f"Got it, #{n} {GOURMET_ROUND.get(n, '')}. What size?"))

        # T-039B: item-kind candidates ("GARDEN SALAD SM"/"LG", "SALAD SM"/
        # "LG") resolve via deterministic word/size matching against THIS
        # utterance — never the old raw substring-of-the-canonical-name
        # check below, which could never match "the large garden salad"
        # against "garden salad lg" (the customer says "large," never
        # "lg"). Shared with the LLM path's own mutation boundary
        # (_select_pending_candidate) — one matcher, not two.
        item_cands = [c for c in cands if c.get("kind") == "item"]
        if item_cands:
            outcome, selected = _select_pending_candidate(t, item_cands)
            if outcome == "SELECTED":
                return Interpretation(calls=[ToolCall("add_item", {"item": selected["name"]})])
            if outcome == "NARROWED":
                # Still ambiguous, but narrowed (e.g. to the GARDEN family) —
                # keep ONLY the narrowed set open, and ask specifically for
                # size since that's what's left to distinguish among them.
                oe._narrow_disambiguation(chat.session, cands, selected)
                chat.pending_clarification = selected
                return Interpretation(say="What size would you like?")

        for c in cands:
            if c.get("kind") == "item":
                continue  # handled above; never fall through to a raw substring guess
            if c.get("kind") in t or (c.get("name", "").lower() in t):
                if c["kind"] == "gourmet":
                    return Interpretation(say="What size would you like?")
                if c["kind"] == "topping":
                    if chat.last_line_id:
                        return Interpretation(calls=[ToolCall("add_modifier", {
                            "line_id": chat.last_line_id, "modifier": c["name"]})])
                    return Interpretation(say="Which pizza should that go on?")
        chat.pending_clarification = cands
        return None


# ===========================================================================
# LLMInterpreter — real model-backed interpreter (T-013).
#
# The tool schema below is INTROSPECTED from the real orders.TOOLS functions
# (via inspect.signature), never hand-duplicated — the model can never be
# offered a parameter that doesn't exist on the real tool. `confirm_order`'s
# `quote_id`/`idempotency_key` are explicitly excluded from the schema the
# model sees and are injected here from `chat.session.quote_id` — the model
# can request confirmation, it can never supply the ID that authorizes it
# (see F5 in orders.py; a model-fabricated ID would be rejected by the real
# STALE_QUOTE check regardless, but it's excluded from the schema entirely
# so there is nothing to fabricate).
# ===========================================================================

_MODEL_HIDDEN_ARGS = {
    "confirm_order": {"quote_id", "idempotency_key"},
}

_TOOL_DESCRIPTIONS = {
    "get_store_info": "Look up store hours, delivery minimum/radius, phone, address. Never invent this info.",
    "search_menu": "Search the menu by free-text query. Use this instead of guessing whether an item/topping exists, or whenever a request could match more than one thing.",
    "decline_item": "Explicitly drop a request that is still awaiting clarification (e.g. the customer says \"forget the wings\" after being asked which size). Clears it so the system stops asking. Does not touch the cart.",
    "check_availability": "Check whether a specific menu item is currently available (not 86'd).",
    "set_order_type": "Set pickup or delivery. Delivery requires an address.",
    "add_item": ("Add a pizza (item='PIZZA' or 'CHEESE PIZZA', with size) or a non-pizza item "
                "(item = the exact menu item name). For a HALF_AND_HALF specialty pizza (two "
                "different named specialties split down the middle), pass BOTH gourmet_number "
                "and second_gourmet_number. In a LATER call in this SAME turn, you may use the "
                "literal string \"$LAST\" as line_id to refer to the pizza this call just created."),
    "add_modifier": ("Add a topping to an existing pizza line. portion is WHOLE, HALF_1, or "
                     "HALF_2. intensity is NORMAL, DOUBLE, TRIPLE, LITE, or NONE (NONE removes "
                     "it / means \"no X\")."),
    "remove_modifier": "Remove a topping from a pizza line.",
    "update_item": "Change an existing line's quantity or size.",
    "remove_item": "Remove an entire line from the cart.",
    "apply_coupon": "Apply a coupon by its exact code. Omit code to let the engine pick the best eligible offer.",
    "remove_coupon": "Remove any applied coupon.",
    "request_quote": "Get the current total. Call this when the customer asks for a total or says they're done adding items.",
    "begin_confirmation": ("Start the confirmation window, ONLY after the customer gives "
                          "EXPLICIT affirmative confirmation (\"yes, place it\", \"go ahead\", "
                          "\"confirm\"). A vague reply like \"that's it\" or \"sure, I guess\" is "
                          "NOT confirmation. This returns a readback — say it to the customer. "
                          "confirm_order cannot be called in this same turn; it fires only once "
                          "the customer responds again, in their next turn, after hearing it."),
    "confirm_order": ("Finalize the order. The system supplies quote_id automatically. Only "
                      "valid in a turn AFTER begin_confirmation's readback was already spoken "
                      "and the customer just gave another explicit affirmative in response to it."),
    "cancel_order": "Cancel the order.",
    "transfer_to_human": "Transfer the call to a human staff member.",
}

_SYSTEM_PROMPT = """You are the order-taking assistant for Lakewood Pizza. You interpret what a customer says and call the provided tools to build their order. You never compute prices or totals yourself — the tools do that, and you must never state a price or total that didn't come from a tool result.

Rules:
- Use only the provided tools. Never invent a menu item, topping, or price.
- If a request is ambiguous, or you are not sure an item/topping exists, call search_menu — never guess.
- Preserve whole/half modifier scope exactly as the customer stated it.
- A correction ("actually, replace X with Y") should modify the existing order, not start a new item, unless the customer is clearly starting over.
- Never call begin_confirmation or confirm_order without EXPLICIT affirmative confirmation from the customer this turn.
- Any cart change after a quote needs a fresh request_quote before confirming again.
- Never tell the customer their order is placed unless confirm_order actually succeeded.
- If nothing needs a tool call, reply in one short, friendly, restaurant-counter sentence. No price claims, no over-explaining.
"""


def _base_json_type(annotation: str) -> dict:
    ann = annotation.replace(" ", "")
    parts = [p for p in ann.split("|") if p != "None"]
    base = parts[0] if parts else "str"
    if base.startswith("list[") and base.endswith("]"):
        return {"type": "array", "items": _base_json_type(base[5:-1])}
    return {"type": {"str": "string", "int": "integer", "bool": "boolean",
                      "float": "number"}.get(base, "string")}


def _build_tool_schemas() -> list[dict]:
    schemas = []
    for name, fn in TOOLS.items():
        hidden = _MODEL_HIDDEN_ARGS.get(name, set())
        props, required = {}, []
        for pname, p in inspect.signature(fn).parameters.items():
            if pname == "sess" or pname in hidden:
                continue
            ann = p.annotation if isinstance(p.annotation, str) else "str"
            props[pname] = _base_json_type(ann)
            if p.default is inspect.Parameter.empty:
                required.append(pname)
        schemas.append({
            "name": name,
            "description": _TOOL_DESCRIPTIONS.get(name, name),
            "input_schema": {"type": "object", "properties": props, "required": required},
        })
    return schemas


_TOOL_SCHEMAS = _build_tool_schemas()


def _state_context(session: oe.Session) -> str:
    """Only what the model needs to resolve pronouns/corrections against —
    line_ids and their current toppings, order type, state. Never a price;
    never the whole codebase or session internals."""
    lines = []
    for lid, line in session.lines.items():
        if hasattr(line, "toppings"):
            tops = ", ".join(f"{t.name}({t.portion})" for t in line.toppings) or "no toppings"
            extra = f" #{line.gourmet}" if line.gourmet else (
                f" HALF_AND_HALF #{line.half_and_half[0]}/#{line.half_and_half[1]}"
                if line.half_and_half else "")
            lines.append(f"  {lid}: {line.size}{extra} pizza — {tops}")
        else:
            lines.append(f"  {lid}: {line.quantity}x {line.name}")
    cart = "\n".join(lines) if lines else "  (empty)"
    parts = [f"Order type: {session.order.order_type}",
             f"State: {session.state}", f"Cart:\n{cart}"]
    if session.state == "QUOTED":
        parts.append("A quote is active. If the customer confirms now, call "
                     "begin_confirmation ONLY — it returns a readback to speak. "
                     "confirm_order must wait for the customer's next explicit "
                     "yes, in a later turn, after they hear that readback.")
    if session.state == "AWAITING_CONFIRMATION":
        parts.append("The readback was already spoken. If the customer just gave "
                     "another explicit affirmative in response to it, call "
                     "confirm_order now.")
    return "\n".join(parts)


# T-039A Part 4: a provider/network failure or an internal tool-loop-
# exhaustion diagnostic must never be spoken verbatim (see `interpret()`'s
# except block below) — a stable, generic apology, with no interpolated
# exception text. The real detail stays available via `last_provider_error`
# for logs/traces.
_PROVIDER_TROUBLE_REPLY = "Sorry, I'm having trouble right now — could you repeat that?"


class LLMInterpreter:
    """Execute the shared tool contract against a staged in-memory session.

    Providers may request continuation after tool results. Only a successful
    bounded turn commits its staged session; network/response failures discard
    the whole turn, including history and quote/confirmation bookkeeping.
    Anthropic retains its existing single-round behavior.
    """

    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider if provider is not None else make_provider()
        self.last_provider_error = None
        self.responses = []

    def interpret(self, chat: ChatState, text: str) -> Interpretation:
        self.last_provider_error = None
        self.responses = []
        staged = copy.deepcopy(chat)
        staged.llm_history.append({"role": "user", "content": text})
        try:
            result = self._interpret_staged(staged, text)
        except ProviderCallError as e:
            # T-039A Part 4: the exception text (network/HTTP detail, or the
            # internal "tool loop exceeded N rounds" diagnostic below) is
            # kept for logs/traces via last_provider_error, but must never be
            # interpolated into what gets spoken — a customer hearing raw
            # provider/internal diagnostic text is exactly the leak class
            # this task closes.
            self.last_provider_error = str(e)
            return Interpretation(say=_PROVIDER_TROUBLE_REPLY)
        # Keep the authoritative Session identity used by chat/eval callers.
        chat.session.__dict__.update(staged.session.__dict__)
        chat.last_line_id = staged.last_line_id
        chat.pending_clarification = staged.pending_clarification
        chat.llm_history = staged.llm_history
        return result

    def _interpret_staged(self, chat: ChatState, text: str) -> Interpretation:
        executed = []
        last_new_line_id = None
        search_hits_this_turn: list[dict] = []
        for _ in range(12):
            system = _SYSTEM_PROMPT + "\n\nCurrent order state:\n" + _state_context(chat.session)
            resp = self.provider.complete(system, chat.llm_history, _TOOL_SCHEMAS)
            self.responses.append(resp)
            chat.llm_history.append({"role": "assistant", "content": resp.raw_content})
            if not resp.tool_uses:
                return Interpretation(already_executed=executed,
                                      say=resp.text or "Sorry, could you say that again?")

            tool_result_blocks = []
            for tu in resp.tool_uses:
                call = ToolCall(tu["name"], tu.get("input", {}))
                name, args = call.tool, call.args
                fn = TOOLS.get(name)
                schema = next((t["input_schema"] for t in _TOOL_SCHEMAS
                               if t["name"] == name), None)
                auth_reason = None
                if name == "add_item" and fn is not None and schema is not None \
                        and _valid_tool_args(args, schema, _MODEL_HIDDEN_ARGS.get(name, set())):
                    auth_reason = _authorize_item_creation(
                        (args.get("item") or "").strip().upper(), args.get("size"),
                        args.get("gourmet_number"), args.get("second_gourmet_number"),
                        text, search_hits_this_turn, chat.session.pending_disambiguations)
                if fn is None:
                    # T-039A Part 4: the raw tool name a model invented is
                    # diagnostic detail (kept in the structured result for
                    # logs/traces), never customer dialogue — masked at the
                    # single funnel, chat.py::_customer_safe_error_message.
                    r = {"status": "error", "code": "UNKNOWN_TOOL",
                         "message": f"{name!r} is not a real tool."}
                elif not _valid_tool_args(args, schema, _MODEL_HIDDEN_ARGS.get(name, set())):
                    r = {"status": "error", "code": "BAD_ARGS",
                         "message": "Tool arguments do not match the permitted schema."}
                elif name == "add_item" and auth_reason not in _AUTHORIZED_REASONS:
                    # T-039A/T-039B: the mutation boundary — a model calling
                    # add_item for an item the customer's own utterance gives
                    # no positive evidence for, that isn't an UNAMBIGUOUS
                    # candidate this turn's own search_menu already returned,
                    # and that this turn's own text doesn't deterministically
                    # select from whatever is actually outstanding, never
                    # reaches the real tool. Same predicate that gates
                    # RuleBasedInterpreter's _new_pizza; this is its LLM-path
                    # enforcement point (see _authorize_item_creation). The
                    # reason code IS the error code — stable for tests/traces.
                    r = {"status": "error", "code": auth_reason,
                         "message": ("Could you tell me which one you'd like?"
                                    if auth_reason == AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED
                                    else "That doesn't match what was asked for — "
                                         "could you clarify what you'd like?")}
                else:
                    args = {k: v for k, v in args.items()
                            if k not in _MODEL_HIDDEN_ARGS.get(name, set())}
                    if name == "confirm_order":
                        args["quote_id"] = chat.session.quote_id
                    args = substitute_last_line(args, last_new_line_id)
                    try:
                        r = (chat.tool_executor(name, args, fn, chat.session)
                             if chat.tool_executor is not None else fn(chat.session, **args))
                    except (TypeError, ValueError) as e:
                        # T-039A Part 4: raw Python exception text (e.g. an
                        # unexpected-keyword-argument message) is diagnostic
                        # detail, not customer dialogue — same masked funnel
                        # as UNKNOWN_TOOL above.
                        r = {"status": "error", "code": "BAD_ARGS", "message": str(e)}

                executed.append({"tool": name, "args": args, "result": r})
                if name == "add_item" and auth_reason is not None:
                    r["authorization_reason"] = auth_reason
                if name == "add_item" and r.get("status") == "ok":
                    last_new_line_id = r["line_id"]
                    chat.last_line_id = last_new_line_id
                if name in {"add_item", "add_modifier", "decline_item"} \
                        and r.get("status") == "ok":
                    chat.pending_clarification = (
                        list(chat.session.pending_disambiguations[-1]["candidates"])
                        if chat.session.pending_disambiguations else None)
                if name == "search_menu" and r.get("status") == "ok":
                    chat.pending_clarification = r.get("results", [])
                    # T-039B: tag provenance per hit — a candidate retrieved
                    # under needs_disambiguation=True must never authorize a
                    # mutation the same way a unique result does (the exact
                    # gap _authorize_item_creation closes). Copies each hit
                    # dict rather than mutating orders.py's own returned
                    # objects in place.
                    ambiguous = r.get("needs_disambiguation", False)
                    search_hits_this_turn.extend(
                        {**h, "_ambiguous": ambiguous, "_search_query": args.get("query", "")}
                        for h in r.get("results", []))
                tool_result_blocks.append({
                    "type": "tool_result", "tool_use_id": tu["id"],
                    "content": json.dumps(r, default=str),
                })
            chat.llm_history.append({"role": "user", "content": tool_result_blocks})
            if not resp.continue_turn:
                return Interpretation(already_executed=executed, say=resp.text)
        raise ProviderCallError("Provider tool loop exceeded 12 rounds; turn discarded")


def _valid_tool_args(args, schema, hidden) -> bool:
    """Validate primitive JSON types before domain code can mutate anything.

    Hidden confirmation values are ignored and injected by the server. Domain
    functions remain the authority on valid menu names, quantities and states.
    """
    if not isinstance(args, dict):
        return False
    props = schema["properties"]
    if set(args) - set(props) - hidden or not set(schema["required"]) <= set(args):
        return False

    def matches(value, spec):
        kind = spec["type"]
        if kind == "array":
            return isinstance(value, list) and all(matches(v, spec["items"]) for v in value)
        types = {"string": (str,), "integer": (int,), "boolean": (bool,),
                 "number": (int, float)}
        return type(value) in types[kind]

    return all(matches(v, props[k]) for k, v in args.items() if k in props)
