"""
T-032 — search_menu's "cheese"-substring alias collision.

T-031's full N=3 live trace sweep found `ALIASES = {"cheese": "MOZZARELLA",
...}` matching as a raw substring (`alias in raw_q`), firing on any query
containing "cheese" anywhere — including inside unrelated words like
"cheesecake" — and spuriously offering MOZZARELLA as a candidate whenever a
customer named the base pizza item itself ("party size cheese pizza").
Explained 9 cases in that sweep: AVAIL-001, NEG-007 (sole cause, 3/3 each),
COUPON-001, CORRECT-007 (contributing, 3/3 each), ADV-002, INVALID-002,
MOD-030, MULTI-004, MULTI-006 (contributing, various run counts). A sibling
collision (CAN vs. a customer-specified "two liter") touched MULTI-002.

Two fixes, each covering a different query shape (real trace evidence, not
assumed — see docs/STATUS.md "T-032"):
  1. Word-boundary matching instead of raw substring — fixes "cheesecake"
     (fix 1 alone is NOT sufficient for "party size cheese pizza": "cheese"
     is a real whole word there).
  2. Query-shape precedence — "cheese" + "pizza" together name the base
     ITEM, not a topping request; the bare "cheese" alias is suppressed
     specifically (not "extra cheese"/"mozz") when "pizza" is mentioned.
A parallel precedence fix for NON_PIZZA_ALIASES: a specific drink size
("two liter", "20 oz") beats the generic catch-all ("soda", "coke") in the
same query.

F7 discipline held throughout: no new guessing, only removing a candidate
that was never a real choice, and never breaking a query that genuinely IS
ambiguous (bare "cheese" alone, "clams", "chicken" — all untouched).
"""
import pytest

from lakewood import orders as oe
from lakewood.orders import search_menu, set_order_type


@pytest.fixture
def pickup():
    s = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


# --- The exact T-031 trace queries — real evidence, not invented ------------

def test_cheesecake_no_longer_collides_with_mozzarella(pickup):
    """Fix 1 (word-boundary) alone resolves this — no 'pizza' word present,
    so fix 2 never engages. MULTI-004's real live failure."""
    r = search_menu(pickup, "cheesecake strawberry cheesecake")
    assert r["status"] == "ok"
    names = {h["name"] for h in r["results"]}
    assert names == {"STRWBRY CHZCAKE"}
    assert not r["needs_disambiguation"]


def test_party_size_cheese_pizza_resolves_to_the_item_only(pickup):
    """Fix 2 (query-shape precedence) — "cheese" is a real whole word here,
    so fix 1 alone would not help. COUPON-001/CORRECT-007's real failure."""
    r = search_menu(pickup, "party size cheese pizza")
    assert r["status"] == "ok"
    assert r["results"] == [{"kind": "item", "name": "CHEESE PIZZA"}]
    assert not r["needs_disambiguation"]


def test_small_cheese_pizza_with_clams_drops_mozzarella_keeps_real_ambiguity(pickup):
    """AVAIL-001's real failure. The cheese/pizza collision must be gone,
    but "clams" is a GENUINE collision (Clams Casino gourmet vs. CLAMS
    topping, two real different menu items) and must still surface."""
    r = search_menu(pickup, "small cheese pizza with clams")
    kinds_names = {(h["kind"], h.get("name")) for h in r["results"]}
    assert ("topping", "MOZZARELLA") not in kinds_names
    assert ("item", "CHEESE PIZZA") in kinds_names


def test_neg_007_phrasing_resolves_cleanly(pickup):
    """NEG-007's real failure — the sole blocker for that case (3/3)."""
    r = search_menu(pickup, "large half pepperoni pizza, cheese, no cheese on one half")
    names = {h["name"] for h in r["results"] if h["kind"] == "topping"}
    assert "MOZZARELLA" not in names
    assert {"kind": "item", "name": "CHEESE PIZZA"} in r["results"]


# --- Sibling collision: drink size vs. generic catch-all --------------------

def test_two_liter_soda_resolves_to_2liter_only(pickup):
    """MULTI-002's real failure: 'two liter soda' matched BOTH 'two liter'
    (->2LITER) and the generic 'soda' (->CAN) alias at once."""
    r = search_menu(pickup, "two liter soda")
    assert r["results"] == [{"kind": "item", "name": "2LITER"}]
    assert not r["needs_disambiguation"]


def test_two_liter_2_liter_soda_still_resolves_cleanly(pickup):
    r = search_menu(pickup, "two liter 2 liter soda")
    assert r["results"] == [{"kind": "item", "name": "2LITER"}]


def test_bare_soda_alone_is_unaffected(pickup):
    """No specific size mentioned — the generic alias must still work."""
    r = search_menu(pickup, "soda")
    assert r["results"] == [{"kind": "item", "name": "CAN"}]


def test_can_of_soda_is_unaffected(pickup):
    """Explicitly names the container — never competed with a size alias,
    must not be touched by the precedence rule."""
    r = search_menu(pickup, "can of soda")
    assert r["results"] == [{"kind": "item", "name": "CAN"}]


# --- Must NOT regress: genuine ambiguity stays ambiguous (F7) ---------------

def test_bare_cheese_alone_is_still_a_real_collision(pickup):
    """No 'pizza' word — fix 2 never engages; this is a real menu collision
    (MOZZARELLA among several CHEESE-named items) and must stay surfaced."""
    r = search_menu(pickup, "cheese")
    names = {h["name"] for h in r["results"]}
    assert "MOZZARELLA" in names
    assert r["needs_disambiguation"]


def test_clams_alone_is_still_a_real_collision(pickup):
    r = search_menu(pickup, "clams")
    kinds = {h["kind"] for h in r["results"]}
    assert kinds == {"gourmet", "topping"}
    assert r["needs_disambiguation"]


def test_extra_cheese_still_resolves_to_mozzarella(pickup):
    """A DIFFERENT alias key ("extra cheese", not bare "cheese") — must
    keep working even when "pizza" is mentioned in the same query."""
    r = search_menu(pickup, "large pizza with extra cheese")
    names = {h["name"] for h in r["results"] if h["kind"] == "topping"}
    assert "MOZZARELLA" in names


# --- Must NOT regress: T-020's own hallucination-refusal test ---------------

def test_truffle_lobster_pizza_still_refused_not_invented(pickup):
    """T-020's own named regression (test_nonexistent_item_is_refused_not_
    invented, tests/test_chat_interpreter.py) — a fabricated specialty with
    'pizza' in it must still get a clean NO_MATCH, never a CHEESE PIZZA
    consolation candidate. This is the exact case my suppression-flag fix
    (`_cheese_alias_suppressed`) could have silently broken by firing
    whenever "pizza" was mentioned regardless of whether "cheese" actually
    matched — regression-tested here directly."""
    r = search_menu(pickup, "truffle lobster pizza")
    assert r["status"] == "error"
    assert r["code"] == "NO_MATCH"


def test_pepperoni_pizza_still_resolves_item_and_topping(pickup):
    """Unaffected by T-032 — a real, already-correctly-handled combination
    (item + topping together, not a choose-one collision)."""
    r = search_menu(pickup, "pepperoni pizza")
    kinds_names = {(h["kind"], h.get("name")) for h in r["results"]}
    assert ("topping", "PEPPERONI") in kinds_names
    assert ("item", "CHEESE PIZZA") in kinds_names


# --- Must NOT regress: T-020's own fixed recall queries ---------------------

def test_t020_filler_word_stripping_still_works(pickup):
    r = search_menu(pickup, "pepperoni topping")
    assert {"kind": "topping", "name": "PEPPERONI"} in r["results"]


def test_t020_number_format_normalization_still_works(pickup):
    r = search_menu(pickup, "NUMBER 10")
    assert any(h["kind"] == "gourmet" and h["number"] == 10 for h in r["results"])


def test_t020_strawberry_cheesecake_alias_still_works(pickup):
    r = search_menu(pickup, "strawberry cheesecake")
    assert r["results"] == [{"kind": "item", "name": "STRWBRY CHZCAKE"}]
