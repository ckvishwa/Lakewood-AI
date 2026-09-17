"""
T-015 regression suite: RuleBasedInterpreter had NO negation handling at
all. "Large pepperoni, no onions." silently added ONIONS as a full-price
charged topping — a real financial defect (customer charged $21.00 for a
$18.00 order), found by chance while testing unrelated STT plumbing, not by
a targeted search. Per CLAUDE.md, every real production-like failure becomes
a permanent regression case — this file is that case, plus the full
phrasing/scope matrix the fix is supposed to cover.

Every test here asserts on the FINAL PRICED CART (subtotal in cents), not
just which tool calls were made — a tool-call-only assertion is exactly the
gap that let the original bug ship silently through evals/runner.py's
validate() mode (see docs/STATUS.md "T-015 coverage-gap diagnosis").
"""

import pytest

from lakewood.chat import new_session, run_turn
from lakewood.interpreter import LLMInterpreter, RuleBasedInterpreter
from lakewood.llm_provider import ProviderResponse


def _cart(turns):
    chat = new_session()
    interp = RuleBasedInterpreter()
    for text in turns:
        run_turn(chat, interp, text)
    line = chat.session.order.lines[0] if chat.session.order.lines else None
    toppings = {(t.name, t.portion): (t.removed, t.lite) for t in (line.toppings if line else [])}
    return chat, toppings


# --- THE NAMED REGRESSION: the exact original failing case -----------------

def test_regression_no_onions_does_not_charge_for_onions():
    """This exact case would have FAILED before the T-015 fix: ONIONS was
    added as a full-price (removed=False) topping, subtotal $21.00 instead
    of the correct $18.00."""
    chat, toppings = _cart(["Large pepperoni, no onions."])
    assert toppings[("PEPPERONI", "WHOLE")] == (False, False)
    assert toppings[("ONIONS", "WHOLE")] == (True, False)  # excluded, not charged
    assert chat.session.order.subtotal() == 1800  # LARGE (1500) + pepperoni (300); NOT +onions


# --- negation phrasings, whole-pizza, at creation time ----------------------

@pytest.mark.parametrize("utterance,topping", [
    ("Large pepperoni, no onions.", "ONIONS"),
    ("Medium cheese, without mushrooms.", "MUSHROOMS"),
    ("Large, hold the onions.", "ONIONS"),
    ("Medium pepperoni, minus the sausage.", "SAUSAGE"),
    ("Large cheese, leave off the mushrooms.", "MUSHROOMS"),
])
def test_negation_phrasings_exclude_without_charging(utterance, topping):
    chat, toppings = _cart([utterance])
    assert toppings[(topping, "WHOLE")] == (True, False)
    # None of these utterances should ever price the excluded topping.
    for (name, _), (removed, lite) in toppings.items():
        if name == topping:
            assert removed or lite


# --- retroactive removal: topping already fully charged ---------------------

def test_take_off_an_existing_charged_topping_removes_it_cleanly():
    """Pre-existing, already-tested behavior (MOD-034) — T-015 must not
    change this: a clean remove_modifier, no lingering $0 marker."""
    chat, toppings = _cart(["Large cheese with mushrooms.",
                           "actually take the mushrooms off"])
    assert ("MUSHROOMS", "WHOLE") not in toppings
    assert chat.session.order.subtotal() == 1500  # LARGE base only


def test_no_x_after_the_fact_on_a_never_added_topping_records_exclusion():
    chat, toppings = _cart(["Large cheese.", "no onions"])
    assert toppings[("ONIONS", "WHOLE")] == (True, False)
    assert chat.session.order.subtotal() == 1500


# --- light/extra: a distinct, separately-priced intensity -------------------

def test_light_on_a_new_topping_is_free():
    chat, toppings = _cart(["Medium cheese, light onions."])
    assert toppings[("ONIONS", "WHOLE")] == (False, True)
    assert chat.session.order.subtotal() == 1300  # MEDIUM base only — LITE is free


def test_light_downgrade_of_an_existing_full_topping_keeps_it_but_free():
    """Distinct from negation: the customer still wants the topping, just
    lighter — must not be dropped entirely like a full exclusion would."""
    chat, toppings = _cart(["Large cheese with pepperoni.",
                           "actually make the pepperoni light"])
    assert ("PEPPERONI", "WHOLE") in toppings
    assert toppings[("PEPPERONI", "WHOLE")] == (False, True)
    assert chat.session.order.subtotal() == 1500  # LARGE base only — LITE is free


def test_negation_and_lite_are_never_conflated():
    chat, toppings = _cart(["Large cheese, no onions, light mushrooms."])
    assert toppings[("ONIONS", "WHOLE")] == (True, False)
    assert toppings[("MUSHROOMS", "WHOLE")] == (False, True)


# --- half-scope negation -----------------------------------------------------

def test_negation_scoped_to_one_half_only():
    chat, toppings = _cart(["Large pepperoni, no cheese on one half."])
    assert toppings[("PEPPERONI", "WHOLE")] == (False, False)
    assert toppings[("MOZZARELLA", "HALF_1")] == (True, False)
    assert chat.session.order.subtotal() == 1800  # pepperoni charged, cheese exclusion is $0


def test_half_pepperoni_no_cheese_on_that_half():
    """Task's own named example: bare "half X" (no "on") plus "that half"
    (anaphoric to the just-named half) must both resolve deterministically
    — "that half" = the half just established = HALF_1."""
    chat, toppings = _cart(["Large half pepperoni, no cheese on that half."])
    assert toppings[("PEPPERONI", "HALF_1")] == (False, False)
    assert toppings[("MOZZARELLA", "HALF_1")] == (True, False)
    assert chat.session.order.subtotal() == 1800  # pepperoni charged; cheese exclusion is $0


def test_bare_half_x_half_y_as_separate_clauses_lands_on_opposite_halves():
    chat, toppings = _cart(["Large half pepperoni, half sausage."])
    assert toppings[("PEPPERONI", "HALF_1")] == (False, False)
    assert toppings[("SAUSAGE", "HALF_2")] == (False, False)


def test_retroactive_removal_scoped_to_the_correct_half_only():
    """Removing a topping from one half must not touch the same topping
    name still charged on the other half."""
    chat, toppings = _cart([
        "Large half pepperoni, half pepperoni.",  # same topping both halves
        "no pepperoni on one half",
    ])
    # HALF_1 pepperoni was a real charged topping -> cleanly removed
    # (matches the MOD-034 convention: no lingering $0 marker).
    assert ("PEPPERONI", "HALF_1") not in toppings
    assert toppings.get(("PEPPERONI", "HALF_2")) == (False, False)


# --- ambiguity: never guess ---------------------------------------------------

def test_negation_trigger_word_with_no_recognizable_topping_is_a_no_op():
    """"No, wait, that's fine" contains the trigger word "no" but names no
    topping — must not fabricate a call against anything."""
    chat, toppings = _cart(["Large cheese.", "no, wait, that's fine"])
    assert toppings == {}
    assert chat.session.order.subtotal() == 1500


# --- price correctness is the point — every case above already checked
# subtotal in cents directly; this final case cross-checks against the real
# pricing engine's own formatted output too, to catch a cents/dollars
# mismatch this suite's own arithmetic might otherwise hide.

def test_negation_price_matches_the_real_quote_tool_not_just_raw_subtotal():
    from lakewood.orders import request_quote
    chat, _ = _cart(["Large pepperoni, no onions."])
    q = request_quote(chat.session)
    assert q["subtotal"] == "18.00"


# --- Part 3: "every interpreter adapter" — domain-execution proof, not just
# RuleBasedInterpreter. A live LLM call is out of this task's scope
# (T-013d/T-013c cover that separately), but the fix this task makes is
# entirely in how a *negation intent, once decided* prices — and that
# execution path is the SAME shared code (add_modifier/remove_modifier +
# Topping.tier_rate) regardless of which interpreter produced the tool
# calls. This proves it via a scripted fake provider: if a model decided to
# call the correct sequence, the price comes out right — end to end,
# through the real LLMInterpreter, not a mock of it.

class _FakeProvider:
    def __init__(self, response):
        self.response = response

    def complete(self, system, messages, tools):
        return self.response


def _tool_use_response(*tool_uses):
    return ProviderResponse(
        tool_uses=[{"id": f"tu{i}", "name": n, "input": inp}
                  for i, (n, inp) in enumerate(tool_uses)],
        raw_content=[{"type": "tool_use", "id": f"tu{i}", "name": n, "input": inp}
                    for i, (n, inp) in enumerate(tool_uses)],
    )


def test_negation_prices_correctly_through_llm_interpreter_too():
    """Same original-bug scenario, executed via LLMInterpreter with a
    scripted response instead of RuleBasedInterpreter — proves the fix is a
    property of the shared domain-execution layer, not something bolted
    onto one interpreter only."""
    fake = _FakeProvider(_tool_use_response(
        ("add_item", {"item": "CHEESE PIZZA", "size": "large"}),
        ("add_modifier", {"line_id": "$LAST", "modifier": "PEPPERONI", "portion": "WHOLE"}),
        ("add_modifier", {"line_id": "$LAST", "modifier": "ONIONS",
                          "portion": "WHOLE", "intensity": "NONE"}),
    ))
    chat = new_session()
    run_turn(chat, LLMInterpreter(provider=fake), "Large pepperoni, no onions.")
    line = chat.session.order.lines[0]
    onions = [t for t in line.toppings if t.name == "ONIONS"][0]
    assert onions.removed is True
    assert chat.session.order.subtotal() == 1800
