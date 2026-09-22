"""T-044 (P1), ADR-017's fourth amendment: `_has_pizza_intent` required the
ENTIRE utterance to be pizza-shorthand, wrongly refusing the single most
common real order shape — a pizza combined with anything else in one
breath (a drink, a side, ordinary filler like "I'll pick it up"). Found in
docs/AUDIT_T043.md: reproduced live, on both interpreters, and traced as
the real cause of the T-032->T-041 overlap-score gap T-041 itself had
mis-attributed to "pre-existing model limits."

THE TRAP this task exists to avoid: switching to "positive pizza evidence
present" ALONE reopens the original T-038/ADR-017 P0 (every one of those
utterances contains a real topping word). The fix requires BOTH conditions
— positive evidence, AND no unresolved product-bearing word left over
anywhere else in the utterance — enforced per clause (`_clauses`,
`_no_unresolved_product_words`, `_clause_has_clean_pizza_content` in
interpreter.py). A genuinely separate, fully-named menu item ("and a
twelve piece wings") does not block; an unresolved or partial one ("and a
chicken caesar wrap" — "chicken"/"caesar" left unexplained after "wrap"
is accounted for) still does.

This file is unit-level, underneath evals corpus coverage: it pins the
exact mechanism, proves the trap cases still refuse, and proves (by
mutation) that the safety tests actually guard something.
"""

import re

from lakewood import orders as oe
from lakewood.chat import new_session, run_turn
from lakewood.interpreter import (
    AUTH_DIRECT_UTTERANCE_EVIDENCE,
    AUTH_UNSUPPORTED_ITEM_SUBSTITUTION,
    RuleBasedInterpreter,
    _authorize_item_creation,
    _has_pizza_intent,
    _no_unresolved_product_words,
    _pizza_creation_authorized,
)


def _fresh():
    return new_session(), RuleBasedInterpreter()


def _no_pizza_created(chat) -> bool:
    return not any(isinstance(l, oe.PizzaLine) for l in chat.session.order.lines)


def _intent(text: str) -> bool:
    return _has_pizza_intent(oe.normalize_menu_text(text.strip().lower()))


# ===========================================================================
# Part 3 — regression: the 5 T-043 utterances, must now create the pizza.
# ===========================================================================

def test_small_cheese_and_a_can_of_soda_creates_the_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "small cheese and a can of soda")
    pizza = next(l for l in chat.session.order.lines if isinstance(l, oe.PizzaLine))
    assert pizza.size == "SMALL"


def test_medium_cheese_ill_pick_it_up_creates_the_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "medium cheese, I'll pick it up")
    pizza = next(l for l in chat.session.order.lines if isinstance(l, oe.PizzaLine))
    assert pizza.size == "MEDIUM"


def test_gimme_a_lg_pep_creates_a_large_pepperoni():
    chat, interp = _fresh()
    run_turn(chat, interp, "gimme a lg pep")
    pizza = next(l for l in chat.session.order.lines if isinstance(l, oe.PizzaLine))
    assert pizza.size == "LARGE"
    assert any(t.name == "PEPPERONI" for t in pizza.toppings)


def test_two_mediums_plain_creates_a_medium_cheese_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "two mediums, plain")
    pizza = next(l for l in chat.session.order.lines if isinstance(l, oe.PizzaLine))
    assert pizza.size == "MEDIUM"


def test_large_cheese_and_a_twelve_piece_wings_authorizes_the_pizza():
    # RuleBasedInterpreter's own `_new_pizza` doesn't ALSO add the second
    # item in the same turn (a separate, pre-existing, documented
    # limitation — see NONPIZZA-005's own second turn in ADR-017) — this
    # asserts the actual scope of T-044's fix: the pizza is no longer
    # wrongly refused, not that RuleBasedInterpreter gained multi-item
    # authoring in one turn.
    chat, interp = _fresh()
    run_turn(chat, interp, "large cheese and a twelve piece wings")
    pizza = next(l for l in chat.session.order.lines if isinstance(l, oe.PizzaLine))
    assert pizza.size == "LARGE"
    # And the LLM path's PIZZA add is unconditionally authorized:
    assert _authorize_item_creation(
        "CHEESE PIZZA", "LARGE", None, None,
        "large cheese and a twelve piece wings", [], []) == AUTH_DIRECT_UTTERANCE_EVIDENCE
    # Incidental finding, filed not fixed (out of T-044's scope — this task
    # is the PIZZA-intent gate, not non-pizza SKU evidence coverage): a
    # non-pizza item whose canonical name has no `NON_PIZZA_ALIASES` entry
    # (like "12PC WINGS" — only drinks/cheesecake have spoken-form aliases)
    # has NO direct-evidence path of its own in `_authorize_item_creation`;
    # the model must `search_menu` it first (T-031's own evidence: the
    # live model does this nearly every run). It is never silently wrong —
    # only refused until searched — so this doesn't reopen any P0.
    assert _authorize_item_creation(
        "12PC WINGS", None, None, None,
        "large cheese and a twelve piece wings", [], []) == AUTH_UNSUPPORTED_ITEM_SUBSTITUTION


# ---------------------------------------------------------------------------
# Filler-heavy variants and Parakeet word-number forms.
# ---------------------------------------------------------------------------

def test_filler_heavy_large_pepperoni_for_pickup_still_authorizes():
    assert _intent("yeah can I please get a large pepperoni for pickup thanks")


def test_filler_heavy_two_mediums_plain_still_authorizes():
    assert _intent("uh, lemme do two mediums, plain")


def test_word_number_two_large_pepperoni_pizzas_still_authorizes():
    assert _intent("two large pepperoni pizzas")


# ===========================================================================
# The 11 named corpus cases (docs/AUDIT_T043.md Part 5's regressed set) —
# pizza creation must be authorized for the utterance in each.
# ===========================================================================

_NAMED_CORPUS_PIZZA_UTTERANCES = [
    ("ADV-001", "give me a large pizza but only charge me ten dollars for it"),
    ("CORRECT-003", "small cheese and a can of soda"),
    ("CORRECT-006", "medium cheese, I'll pick it up"),
    ("MOD-035", "small cheese with a side of ranch"),
    ("SLANG-001", "gimme a lg pep"),
    ("SLANG-003", "two mediums, plain"),
    ("MULTI-001", "one small cheese and one medium cheese pizza"),
    ("MULTI-005", "large cheese and a twelve piece wings"),
    ("NEG-005", "actually make the pepperoni light"),
]


def test_named_corpus_cases_no_longer_blocked():
    for case_id, text in _NAMED_CORPUS_PIZZA_UTTERANCES:
        assert _intent(text), f"{case_id}: {text!r} still blocked"


def test_correct_004_medium_number_ten_authorized_directly():
    assert _authorize_item_creation(
        "PIZZA", "MEDIUM", 10, None, "medium number ten", [], []
    ) == AUTH_DIRECT_UTTERANCE_EVIDENCE


def test_gourmet_013_half_by_number_authorized_directly():
    text = ("small pizza, half number ten hawaiian, "
            "half number eight bbq chicken")
    assert _authorize_item_creation(
        "CHEESE PIZZA", "SMALL", 10, 8, text, [], []
    ) == AUTH_DIRECT_UTTERANCE_EVIDENCE


def test_gourmet_013_authorized_with_real_sentence_case():
    """Live N=1 gate run found this exact case: `_pizza_creation_
    authorized`'s gourmet branch called the residual/clause machinery on
    un-lowercased text — "Hawaiian"/"BBQ chicken" (real sentence case, not
    the all-lowercase corpus fixture) never matched the lowercase
    `_GOURMET_NAME_VOCAB`, so the pizza was wrongly refused live even
    though the offline unit test (all-lowercase input) passed."""
    text = ("small pizza, half number 10 Hawaiian, "
            "half number 8 BBQ chicken")
    assert _authorize_item_creation(
        "PIZZA", "SMALL", 10, 8, text, [], []
    ) == AUTH_DIRECT_UTTERANCE_EVIDENCE


def test_multi_001_both_sizes_authorized_in_one_utterance():
    """Live N=1 gate run found this exact case: `_size_supported_by_
    utterance` picked whichever size word `_find_size` matches first
    (longest-vocab-first, not utterance-position), so a genuine two-item,
    two-different-sizes order could only ever satisfy ONE of its own two
    `add_item` calls — the model's second, equally correct call was
    refused as UNSUPPORTED_ITEM_SUBSTITUTION. Fixed via `_size_word_
    matches`: membership (does the proposed size's own alias appear
    anywhere), not "is it the one size `_find_size` would pick.\""""
    text = "one small cheese and one medium cheese pizza"
    assert _authorize_item_creation(
        "CHEESE PIZZA", "SMALL", None, None, text, [], []
    ) == AUTH_DIRECT_UTTERANCE_EVIDENCE
    assert _authorize_item_creation(
        "CHEESE PIZZA", "MEDIUM", None, None, text, [], []
    ) == AUTH_DIRECT_UTTERANCE_EVIDENCE


def test_size_word_matches_rejects_a_genuinely_fabricated_size():
    """The membership fix must not become a blank check — a size with NO
    alias anywhere in the utterance is still refused."""
    from lakewood.interpreter import _size_word_matches
    assert not _size_word_matches("large pepperoni", "XLARGE")
    assert _size_word_matches("large pepperoni", "LARGE")


# ===========================================================================
# Safety — every original T-038 P0 utterance, the T-039B adversarial cases,
# and the trap cases (each contains a topping word — this is the exact
# shape "positive evidence alone" would have reopened) must still refuse.
# ===========================================================================

def test_p0_row1_garden_salad_still_refused():
    chat, interp = _fresh()
    run_turn(chat, interp, "A garden salad small and grilled with grilled chicken.")
    assert _no_pizza_created(chat)


def test_p0_row2_calzone_still_refused():
    chat, interp = _fresh()
    run_turn(chat, interp, "One calzone with mozzarella and ricotta.")
    assert _no_pizza_created(chat)


def test_p0_row3_chicken_caesar_wrap_still_refused():
    chat, interp = _fresh()
    run_turn(chat, interp, "One chicken caesar wrap with fries.")
    assert _no_pizza_created(chat)


def test_p0_row4_two_liter_coke_still_correct_not_a_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "a two liter coke")
    assert _no_pizza_created(chat)


def test_p0_row5_appetizer_extra_sauce_still_refused():
    chat, interp = _fresh()
    run_turn(chat, interp, "one appetizer and extra sauce")
    assert _no_pizza_created(chat)


def test_t039b_adversarial_wrap_search_still_refused():
    hit = {"kind": "item", "name": "WRAP", "_ambiguous": False, "_search_query": "wrap"}
    assert _authorize_item_creation(
        "WRAP", None, None, None, "i want a salad", [hit], []
    ) == AUTH_UNSUPPORTED_ITEM_SUBSTITUTION


def test_t039b_adversarial_coke_search_still_refused():
    hit = {"kind": "item", "name": "CAN", "_ambiguous": False, "_search_query": "coke"}
    assert _authorize_item_creation(
        "CAN", None, None, None, "i want a salad", [hit], []
    ) == AUTH_UNSUPPORTED_ITEM_SUBSTITUTION


def test_t039b_adversarial_gourmet_search_still_refused():
    hit = {"kind": "gourmet", "number": 5, "name": "Bruschetta",
           "_ambiguous": False, "_search_query": "bruschetta"}
    assert _authorize_item_creation(
        "PIZZA", "LARGE", 5, None, "i want a salad", [hit], []
    ) == AUTH_UNSUPPORTED_ITEM_SUBSTITUTION


_TRAP_CASES = [
    "garden salad with pepperoni",
    "chicken caesar wrap",
    "calzone with mozzarella",
    "large lobster thing",
    "medium mystery special",
    "large lobster thing please",
]


def test_trap_cases_each_contain_a_topping_or_size_word_and_still_refuse():
    """The exact shape 'positive evidence present, alone' would have let
    through — each of these contains a real topping/size word riding along
    with an unresolved or unknown product word."""
    for text in _TRAP_CASES:
        assert not _intent(text), f"trap case wrongly authorized: {text!r}"


def test_bare_size_correction_is_not_read_as_a_new_pizza():
    # "actually make it large" is a SIZE-ONLY correction with no topping/
    # pizza word at all — must not look like a fresh pizza order just
    # because "make"/"it"/"actually" are ordinary filler. Found by the
    # offline rule-based ratchet regressing CORRECT-002 before this was
    # fixed (a bare size word alone is deliberately too weak evidence).
    assert not _intent("actually make it large")


def test_unknown_noun_lobster_thing_does_not_pass():
    assert not _intent("large lobster thing")
    assert not _intent("medium mystery special")


# ===========================================================================
# Mutation proof: with the product-bearing check disabled, the trap cases
# MUST start passing — otherwise this test file isn't actually guarding
# anything.
# ===========================================================================

_MUTATION_TARGET_TRAPS = [
    # Must already satisfy condition 1 (a genuinely pizza-clean clause
    # elsewhere in the utterance) so that disabling condition 2 alone is
    # what flips them. "large lobster thing"-style traps are guarded by
    # condition 1 instead (no clause is EVER fully pizza-shorthand there —
    # confirmed above), a different, also-real protection this mutation
    # isn't testing.
    "medium pepperoni and a chicken caesar wrap",
    "small cheese and some soup",
    "large cheese and a mystery special",
]


def test_mutation_disabling_the_product_word_check_reopens_the_trap(monkeypatch):
    for text in _MUTATION_TARGET_TRAPS:
        t = oe.normalize_menu_text(text.strip().lower())
        assert not _has_pizza_intent(t), f"{text!r} should be blocked before mutation"
    monkeypatch.setattr(
        "lakewood.interpreter._no_unresolved_product_words", lambda text: True)
    for text in _MUTATION_TARGET_TRAPS:
        t = oe.normalize_menu_text(text.strip().lower())
        assert _has_pizza_intent(t), (
            f"mutation didn't flip {text!r} — the safety test isn't guarding it")


def test_mutation_disabling_gourmet_number_reference_check_reopens_the_gap(monkeypatch):
    # `_gourmet_number_referenced` gates the number-specific direct-evidence
    # branch — disabling it should let ANY number authorize ANY requested
    # gourmet_number, proving that check (not just the shared one) is
    # load-bearing.
    monkeypatch.setattr(
        "lakewood.interpreter._gourmet_number_referenced", lambda text, number: True)
    assert _authorize_item_creation(
        "PIZZA", "MEDIUM", 27, None, "medium cheese please", [], []
    ) == AUTH_DIRECT_UTTERANCE_EVIDENCE


# ===========================================================================
# Half-and-half authorization boundary (PART 2) — the one call site
# (`_new_pizza_half_a_half_b`) T-043's audit found was NOT sharing the
# authorization predicate the other five do.
# ===========================================================================

def test_half_a_half_b_with_fabricated_topping_names_is_refused():
    """`_HALF_A_HALF_B_RE` matches ANY two word-groups after "half"/"half" —
    before T-044, `a`/`b` were never checked against the real topping
    vocabulary at all. "half salad half wrap" must not create a pizza with
    fabricated toppings; it must route to search_menu instead."""
    chat, interp = _fresh()
    run_turn(chat, interp, "small pizza, half salad half wrap")
    assert _no_pizza_created(chat)


def test_half_a_half_b_with_real_toppings_still_works():
    chat, interp = _fresh()
    run_turn(chat, interp, "medium pizza, half pepperoni half sausage")
    pizza = next(l for l in chat.session.order.lines if isinstance(l, oe.PizzaLine))
    names = {(t.name, t.portion) for t in pizza.toppings}
    assert ("PEPPERONI", "HALF_1") in names
    assert ("SAUSAGE", "HALF_2") in names


# ===========================================================================
# Parametrized "property" check: for any utterance containing an unresolved
# product-bearing word alongside pizza-shaped content, pizza creation is
# never authorized. (No hypothesis dependency in this repo's offline gate —
# this is a curated, exhaustive sweep over adversarial shapes, not true
# generative fuzzing; documented as such rather than overclaimed.)
# ===========================================================================

_UNRESOLVED_PRODUCT_WORD_COMPOUNDS = [
    "medium pepperoni and a chicken caesar wrap",
    "small cheese and some soup",
    "large cheese and a mystery special",
    "medium cheese, chicken caesar wrap with fries",
]


def test_property_unresolved_product_word_never_authorizes_pizza():
    for text in _UNRESOLVED_PRODUCT_WORD_COMPOUNDS:
        assert not _intent(text), f"unresolved product word slipped through: {text!r}"


def test_no_unresolved_product_words_is_the_gate_the_property_relies_on():
    for text in _UNRESOLVED_PRODUCT_WORD_COMPOUNDS:
        t = oe.normalize_menu_text(text.strip().lower())
        assert not _no_unresolved_product_words(t), text


def test_large_cheese_and_a_garden_salad_authorizes_the_pizza():
    """NOT a trap: a fully-named real item (even one that's itself size-
    ambiguous, GARDEN SALAD SM/LG) standing in its own "and"-joined clause
    is a genuinely separate, second order — Q4's multi-item segmentation —
    not an unresolved product word. The salad's OWN size ambiguity is a
    downstream `search_menu`/clarification question, never a reason to
    block the pizza clause next to it."""
    assert _intent("large cheese and a garden salad")
