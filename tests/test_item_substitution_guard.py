"""
T-039 (P0): a real 10-turn voice session had the system silently turn a
named item the interpreter couldn't resolve into a DIFFERENT real item
instead of asking — a garden salad became a small cheese pizza, a calzone
and a chicken caesar wrap were scavenged for topping words and grafted onto
that same wrong pizza line, and "a two liter coke" became "1 can". Every
reply was confident and cheerful; the customer never heard anything was
wrong. This is the T-018/T-019 defect class again (silent DROP), but worse:
silent SUBSTITUTION into a different, real, priced item.

Root causes, all in `lakewood/interpreter.py::RuleBasedInterpreter` (see
docs/decisions/ADR-017 for the full diagnosis):

1. `_new_pizza` (triggered by ANY recognized size word, e.g. "small") never
   checked whether the utterance actually named a pizza before defaulting
   to `add_item(item="CHEESE PIZZA")` and scavenging topping-vocabulary
   words out of the rest of the sentence.
2. The bare-topping fallback (any recognized topping word + an already-open
   pizza line) applied to ANY later utterance regardless of whether it was
   about that pizza at all, grafting modifiers from an unrelated request
   (a calzone, a wrap) onto whatever line happened to exist.
3. `_find_drink` used its own private, never-updated copy of the drink
   alias table (`_DRINK_WORDS`), which (a) never got T-032's "two liter"-
   beats-"coke" precedence fix, and (b) treated the bare word "can" — the
   ordinary modal verb in "can I get..." — as a drink signal on its own.

The fix (T-039): a non-pizza head-word veto that runs before drink
detection, new-pizza creation, and the bare-topping graft, plus routing
drink detection through orders.py's own real, precedence-correct
NON_PIZZA_ALIASES table instead of a second copy. Every test here asserts
on the FINAL PRICED CART, not just which tool calls were made (EVALS.md's
standing rule) — a tool-call-only assertion is exactly the gap that let a
different interpreter bug ship silently through validate() before (T-015).
"""

from lakewood import orders as oe
from lakewood.chat import (
    _customer_safe_error_message, new_session, run_turn, ChatState,
)
from lakewood.interpreter import LLMInterpreter, RuleBasedInterpreter
from lakewood.llm_provider import ProviderResponse
from lakewood.pricing import NON_PIZZA, money


def _fresh():
    chat = new_session()
    return chat, RuleBasedInterpreter()


# --- Row 1: "A garden salad small and grilled with grilled chicken" --------

def test_regression_garden_salad_does_not_become_a_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "A garden salad small and grilled with grilled chicken.")

    # The one thing that must never be true: a fabricated CHEESE PIZZA line.
    assert not any(isinstance(l, oe.PizzaLine) for l in chat.session.order.lines)
    assert chat.session.order.quote()["subtotal"] == "0.00"


# --- Row 2: "One calzone with mozzarella and ricotta" ----------------------

def test_regression_calzone_does_not_graft_toppings_onto_an_open_pizza_line():
    chat, interp = _fresh()
    run_turn(chat, interp, "Large pepperoni.")  # a real, legitimate open pizza line
    line = chat.session.order.lines[0]
    toppings_before = {(t.name, t.portion) for t in line.toppings}

    run_turn(chat, interp, "One calzone with mozzarella and ricotta.")

    # The pre-existing pizza must be completely unchanged — no mozzarella,
    # no ricotta grafted onto it just because a pizza line happened to be
    # open when the customer named a different item entirely.
    assert {(t.name, t.portion) for t in line.toppings} == toppings_before
    # And no fabricated second CHEESE PIZZA line either.
    assert sum(1 for l in chat.session.order.lines if isinstance(l, oe.PizzaLine)) == 1


# --- Row 3: "One chicken caesar wrap with fries" ----------------------------

def test_regression_chicken_wrap_does_not_graft_chicken_onto_an_open_pizza_line():
    chat, interp = _fresh()
    run_turn(chat, interp, "Large pepperoni.")
    line = chat.session.order.lines[0]
    toppings_before = {(t.name, t.portion) for t in line.toppings}

    run_turn(chat, interp, "One chicken caesar wrap with fries.")

    assert {(t.name, t.portion) for t in line.toppings} == toppings_before
    assert not any(t.name == "CHICKEN" for t in line.toppings)


# --- Row 4: "...a two liter coke" ------------------------------------------

def test_regression_two_liter_coke_is_a_2liter_not_a_can():
    chat, interp = _fresh()
    run_turn(chat, interp, "A two liter coke.")

    assert len(chat.session.order.lines) == 1
    line = chat.session.order.lines[0]
    assert line.name == "2LITER"
    assert chat.session.order.quote()["subtotal"] == money(NON_PIZZA["2LITER"])


# --- Row 5: "one appetizer and... extra sauce" / bare "can" as filler ------

def test_regression_bare_can_filler_word_does_not_add_a_can():
    """The real session's transcript included ordinary "can I get..."
    phrasing around an unresolved request — the exact modal-verb collision
    `_find_drink`'s own private alias copy fell into: bare "can" was treated
    as a drink signal on its own, unrelated to any actual drink mention."""
    chat, interp = _fresh()
    run_turn(chat, interp, "One appetizer, and can I get extra sauce.")

    assert chat.session.order.lines == []
    assert chat.session.order.quote()["subtotal"] == "0.00"


def test_regression_can_i_get_a_pizza_is_not_intercepted_by_the_word_can():
    """Same collision, ordinary phrasing: "can I get" must not itself
    resolve to a can before the real request (a pizza) is even considered."""
    chat, interp = _fresh()
    run_turn(chat, interp, "Can I get a large pepperoni.")

    line = chat.session.order.lines[0]
    assert line.size == "LARGE"
    assert any(t.name == "PEPPERONI" for t in line.toppings)
    assert not any(l.name == "CAN" for l in chat.session.order.lines
                  if not isinstance(l, oe.PizzaLine))


# --- Two-liter still beats the generic word when both are named together ---

def test_two_liter_beats_generic_soda_word_in_the_same_query():
    from lakewood.interpreter import _find_drink
    assert _find_drink("two liter soda") == "2LITER"
    assert _find_drink("coke") == "CAN"
    assert _find_drink("can of soda") == "CAN"


# --- Part 1: the domain layer already fails closed on a truly unknown item -

def test_add_item_refuses_an_unresolved_item_name_rather_than_substituting():
    """T-020's CHEESE PIZZA pseudo-hit is guarded and narrow; add_item's own
    fallback for anything else is ITEM_NOT_FOUND, never a silent default —
    proves the invariant the interpreter fix relies on actually holds at the
    layer that can't be bypassed."""
    chat = new_session()
    r = oe.add_item(chat.session, item="GARDEN SALAD")
    assert r["status"] == "error"
    assert r["code"] == "ITEM_NOT_FOUND"
    assert chat.session.order.lines == []


# --- Part 1 Q2: does LLMInterpreter share the defect? ----------------------

class _FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, system, messages, tools):
        return self.responses.pop(0)


def _tool_use(name, args, stop_reason="tool_use"):
    return ProviderResponse(
        tool_uses=[{"id": "tu0", "name": name, "input": args}],
        text=None, stop_reason=stop_reason,
        raw_content=[{"type": "tool_use", "id": "tu0", "name": name, "input": args}])


def _text(t):
    return ProviderResponse(tool_uses=[], text=t, stop_reason="end_turn",
                            raw_content=[{"type": "text", "text": t}])


def test_llm_interpreter_has_no_new_pizza_heuristic_a_well_behaved_model_asks():
    """LLMInterpreter has no equivalent of RuleBasedInterpreter's _new_pizza
    — it only ever mutates the cart through whatever the model itself calls.
    A model that follows the system prompt (call search_menu, don't invent
    an item on a miss) leaves the cart untouched, exactly like the fixed
    rule-based path — proving this defect class is specific to the rule-
    based heuristics T-039 patched, not shared interpreter machinery."""
    chat = new_session()
    fake = _FakeProvider([
        _tool_use("search_menu", {"query": "garden salad"}),
        _text("Sorry, we don't have that — anything else?"),
    ])
    run_turn(chat, LLMInterpreter(provider=fake), "A garden salad small.")
    assert chat.session.order.lines == []


def test_llm_interpreter_add_item_hallucination_still_fails_closed_at_the_domain():
    """The residual risk on the LLM side isn't a heuristic bug — it's that
    nothing but the system prompt stops a misbehaving model from calling
    add_item directly. Proves the SAME domain-layer defense
    (add_item's ITEM_NOT_FOUND) that protects the rule-based path also
    protects this one: even a naive hallucination (using the customer's own
    words as a literal item name, never calling search_menu at all) cannot
    mutate the cart."""
    chat = new_session()
    fake = _FakeProvider([
        _tool_use("add_item", {"item": "garden salad", "size": "small"}),
        _text("Sorry, we don't have that — anything else?"),
    ])
    run_turn(chat, LLMInterpreter(provider=fake), "A garden salad small.")
    assert chat.session.order.lines == []


# --- Part 3, supporting bug A: internal error text must never be spoken ----

def test_internal_line_id_never_reaches_the_customer_facing_reply():
    """Real live finding: 'L5 is not a pizza.' was spoken aloud verbatim —
    BAD_LINE's message is built from a raw internal line_id. Every code path
    from a structured error to spoken text funnels through
    chat.py::_finish_turn -> _customer_safe_error_message; this is the one
    place that needs the guard."""
    r = oe.err("BAD_LINE", "L5 is not a pizza.")
    message = _customer_safe_error_message(r)
    assert "L5" not in message
    assert "line" not in message.lower() or "didn't" in message.lower()


def test_not_on_pizza_error_also_masks_the_internal_line_id():
    r = oe.err("NOT_ON_PIZZA", "MUSHROOMS isn't on L3.")
    message = _customer_safe_error_message(r)
    assert "L3" not in message


def test_ordinary_customer_facing_error_messages_pass_through_unchanged():
    """The masking must be narrow — every OTHER error code's message is
    already written to be customer-safe and must not be replaced."""
    r = oe.err("BAD_QUANTITY", "Quantity must be between 1 and 20.")
    assert _customer_safe_error_message(r) == "Quantity must be between 1 and 20."


def test_bad_line_error_reaching_run_turn_end_to_end_never_speaks_the_line_id():
    """End-to-end, through the real chat.py funnel: force a BAD_LINE by
    trying to modify a non-pizza (CAN) line as if it were a pizza."""
    chat = new_session()
    r = oe.add_item(chat.session, item="CAN")
    line_id = r["line_id"]
    made = [{"tool": "add_modifier",
             "args": {"line_id": line_id, "modifier": "PEPPERONI"},
             "result": oe.add_modifier(chat.session, line_id, "PEPPERONI")}]
    from lakewood.chat import _finish_turn
    result = _finish_turn(ChatState(session=chat.session), made, debug=False)
    assert line_id not in result.reply
