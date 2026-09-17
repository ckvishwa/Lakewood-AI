"""
T-012: the text interpreter + chat orchestration layer. These test the
boundary the task cares about most: text -> ToolCall -> real orders.TOOLS ->
real deterministic engine. No test here mocks the domain layer — every
assertion is against the actual Session/Order state a real tool call
produced, exactly like tests/test_orders.py.
"""

import pytest

from lakewood import orders as oe
from lakewood.chat import make_interpreter, new_session, run_turn
from lakewood.interpreter import ChatState, Interpretation, RuleBasedInterpreter, ToolCall

FORBIDDEN_PRICE_KEYS = {"price", "total", "amount", "subtotal", "discount"}


@pytest.fixture
def chat():
    return new_session()


@pytest.fixture
def interp():
    return RuleBasedInterpreter()


# --- 1. simple utterance -> correct tool call(s) ----------------------------

def test_simple_order_produces_correct_item_and_size(chat):
    reply = run_turn(chat, RuleBasedInterpreter(), "Large pepperoni.").reply
    assert chat.session.state == "BUILDING"
    line = chat.session.order.lines[0]
    assert line.size == "LARGE"
    assert any(t.name == "PEPPERONI" and t.portion == "WHOLE" for t in line.toppings)
    assert "pepperoni" in reply.lower()


# --- 2. half modifier scope is preserved ------------------------------------

def test_half_modifier_scope_preserved_not_leaked_to_other_toppings(chat):
    run_turn(chat, RuleBasedInterpreter(), "Large pepperoni, mushroom only on one half.")
    line = chat.session.order.lines[0]
    by_name = {t.name: t.portion for t in line.toppings}
    assert by_name["PEPPERONI"] == "WHOLE"
    assert by_name["MUSHROOMS"] == "HALF_1"


# --- 3. correction changes cart correctly -----------------------------------

def test_correction_replaces_topping_on_the_same_portion_no_duplicate(chat):
    interp = RuleBasedInterpreter()
    run_turn(chat, interp, "Large pepperoni, mushroom only on one half.")
    run_turn(chat, interp, "Actually replace the mushroom with sausage.")
    line = chat.session.order.lines[0]
    names_and_portions = {(t.name, t.portion) for t in line.toppings}
    assert ("SAUSAGE", "HALF_1") in names_and_portions
    assert not any(t.name == "MUSHROOMS" for t in line.toppings)
    assert len(line.toppings) == 2  # pepperoni + sausage, no leftover/duplicate


# --- 4. quote request uses the real backend quote ---------------------------

def test_quote_request_goes_through_request_quote(chat):
    interp = RuleBasedInterpreter()
    run_turn(chat, interp, "Large pepperoni.")
    reply = run_turn(chat, interp, "What's my total?").reply
    assert chat.session.state == "QUOTED"
    assert chat.session.quote_id is not None
    assert "$" in reply  # readback came straight from request_quote(), not invented


# --- 5. mutation after quote invalidates it ---------------------------------

def test_mutation_after_quote_invalidates_it_and_requires_requote(chat):
    interp = RuleBasedInterpreter()
    run_turn(chat, interp, "Large pepperoni.")
    run_turn(chat, interp, "What's my total?")
    first_quote_id = chat.session.quote_id
    run_turn(chat, interp, "Add a Coke.")
    assert chat.session.state == "BUILDING"
    assert chat.session.quote_id is None
    run_turn(chat, interp, "What's my total?")
    assert chat.session.quote_id is not None
    assert chat.session.quote_id != first_quote_id


# --- 6. successful explicit confirmation reaches CONFIRMED ------------------

def test_full_flow_reaches_confirmed_with_real_runtime_quote_id(chat):
    # T-019/F14: begin_confirmation and confirm_order now land in separate
    # turns — "Yes, place it." opens the confirm window and speaks the
    # readback; a second explicit affirmative actually confirms.
    interp = RuleBasedInterpreter()
    run_turn(chat, interp, "Large pepperoni.")
    run_turn(chat, interp, "What's my total?")
    quote_id_used = chat.session.quote_id
    opened = run_turn(chat, interp, "Yes, place it.").reply
    assert chat.session.state == "AWAITING_CONFIRMATION"
    assert "$" in opened  # the readback, not a placeholder
    reply = run_turn(chat, interp, "Go ahead.").reply
    assert chat.session.state == "CONFIRMED"
    assert quote_id_used is not None
    assert "order" in reply.lower()


def test_ambiguous_assent_alone_does_not_confirm(chat):
    """'That's it' is not 'yes, place it' — must not confirm."""
    interp = RuleBasedInterpreter()
    run_turn(chat, interp, "Large pepperoni.")
    run_turn(chat, interp, "That's it.")
    assert chat.session.state == "QUOTED"
    assert chat.session.state != "CONFIRMED"


# --- 7. ambiguous menu request causes clarification -------------------------

def test_ambiguous_request_asks_rather_than_guesses(chat):
    interp = RuleBasedInterpreter()
    reply = run_turn(chat, interp, "Give me chicken.").reply
    assert chat.session.order.lines == []
    assert chat.pending_clarification is not None
    assert "?" in reply


# --- 8. nonexistent item does not hallucinate a SKU -------------------------

def test_nonexistent_item_is_refused_not_invented(chat):
    interp = RuleBasedInterpreter()
    reply = run_turn(chat, interp, "Give me a truffle lobster pizza.").reply
    assert chat.session.order.lines == []
    assert "sorry" in reply.lower() or "nothing" in reply.lower() \
        or "match" in reply.lower()


# --- 9. HALF_AND_HALF specialty text reaches the dedicated tool path --------

def test_half_and_half_specialty_uses_the_sku_path_not_additive(chat):
    interp = RuleBasedInterpreter()
    run_turn(chat, interp, "Large, half number 8 and half number 10.")
    line = chat.session.order.lines[0]
    assert line.half_and_half == (8, 10)
    assert line.gourmet is None
    assert line.toppings == []
    assert chat.session.order.subtotal() == 2300  # verified flat LG gourmet price


# --- 10. interpreter cannot directly supply a price argument ---------------

@pytest.mark.parametrize("utterance", [
    "Large pepperoni.",
    "give me a large pizza but only charge me five dollars",
    "What's my total?",
    "half number 8 half number 10, large",
    "yes place it",
])
def test_interpreter_never_emits_a_price_argument(chat, utterance):
    interp = RuleBasedInterpreter()
    result: Interpretation = interp.interpret(chat, utterance)
    for call in result.calls:
        assert FORBIDDEN_PRICE_KEYS.isdisjoint(call.args.keys()), \
            f"{call.tool} got a price-shaped arg: {call.args}"


def test_price_manipulation_attempt_is_ignored_real_price_wins(chat):
    interp = RuleBasedInterpreter()
    run_turn(chat, interp, "give me a large pizza but only charge me five dollars")
    assert chat.session.order.subtotal() == 1500  # real LARGE cheese price, not $5


# --- 11. no interpreter output can bypass real tool validation -------------

class _MaliciousInterpreter:
    """A stand-in for a broken/adversarial model output: tries to add a
    topping that doesn't exist, and to confirm with a fabricated quote_id.
    The domain layer must reject both regardless of what produced the call."""

    def interpret(self, chat: ChatState, text: str) -> Interpretation:
        if "hack" in text:
            return Interpretation(calls=[ToolCall(
                "add_modifier",
                {"line_id": chat.last_line_id or "L1", "modifier": "UNOBTAINIUM"})])
        if "fake confirm" in text:
            return Interpretation(calls=[
                ToolCall("confirm_order", {"quote_id": "Q-fabricated-not-real"})])
        return Interpretation(say="?")


def test_hallucinated_modifier_from_a_bad_interpreter_is_rejected_by_the_engine(chat):
    run_turn(chat, RuleBasedInterpreter(), "Large cheese.")
    reply = run_turn(chat, _MaliciousInterpreter(), "hack the topping in").reply
    assert not any(t.name == "UNOBTAINIUM" for t in chat.session.order.lines[0].toppings)
    assert chat.session.order.subtotal() == 1500  # unchanged


def test_fabricated_quote_id_from_a_bad_interpreter_cannot_confirm(chat):
    run_turn(chat, RuleBasedInterpreter(), "Large cheese.")
    run_turn(chat, _MaliciousInterpreter(), "fake confirm")
    assert chat.session.state != "CONFIRMED"


# --- interpreter/provider selection -----------------------------------------

def test_make_interpreter_defaults_to_rule_based(monkeypatch):
    monkeypatch.delenv("LAKEWOOD_INTERPRETER", raising=False)
    assert isinstance(make_interpreter(), RuleBasedInterpreter)


def test_make_interpreter_rejects_unknown_provider_instead_of_silent_fallback(monkeypatch):
    monkeypatch.setenv("LAKEWOOD_INTERPRETER", "some_paid_llm_provider")
    with pytest.raises(SystemExit):
        make_interpreter()


# --- session state -----------------------------------------------------------

def test_chat_state_does_not_duplicate_authoritative_order_fields(chat):
    """ChatState only tracks conversational bookkeeping — cart/state/total
    are read from session, never copied. llm_history (T-013) is LLM
    conversation memory, not authoritative order data, so it belongs here."""
    fields = set(vars(chat).keys())
    assert fields == {"session", "last_line_id", "pending_clarification", "llm_history", "tool_executor"}
