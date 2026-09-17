"""
T-013: LLMInterpreter, tested against a deterministic FAKE provider — no
live API call, no credentials, no network in CI. These prove the SAME
safety boundary the manual/benchmark runs exercise against the real
Anthropic API: a model's output only ever reaches business state through
the real orders.TOOLS functions, and a model can never supply a price or a
confirming quote_id.
"""

import pytest

from lakewood import orders as oe
from lakewood.chat import new_session, run_turn
from lakewood.interpreter import (
    ChatState, LLMInterpreter, RuleBasedInterpreter, _TOOL_SCHEMAS,
)
from lakewood.llm_provider import ProviderCallError, ProviderResponse

FORBIDDEN_PRICE_KEYS = {"price", "total", "amount", "subtotal", "discount"}


class FakeProvider:
    """Scripted `.complete()` — pop the next canned ProviderResponse (or
    exception) per call, matching AnthropicProvider's real interface."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, messages, tools):
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _tool_use_response(*tool_uses, text=None, stop_reason="tool_use"):
    return ProviderResponse(
        tool_uses=[{"id": f"tu{i}", "name": n, "input": inp}
                  for i, (n, inp) in enumerate(tool_uses)],
        text=text, stop_reason=stop_reason,
        raw_content=[{"type": "tool_use", "id": f"tu{i}", "name": n, "input": inp}
                    for i, (n, inp) in enumerate(tool_uses)],
    )


def _text_response(text):
    return ProviderResponse(tool_uses=[], text=text, stop_reason="end_turn",
                            raw_content=[{"type": "text", "text": text}])


@pytest.fixture
def chat():
    return new_session()


# --- 1. provider response -> valid ToolCall (executed for real) ------------

def test_provider_tool_use_becomes_a_real_executed_call(chat):
    fake = FakeProvider([_tool_use_response(
        ("add_item", {"item": "CHEESE PIZZA", "size": "large"}))])
    interp = LLMInterpreter(provider=fake)
    result = run_turn(chat, interp, "large pepperoni")
    assert chat.session.order.lines[0].size == "LARGE"
    assert chat.session.state == "BUILDING"


# --- 2. malformed provider response fails closed ----------------------------

def test_malformed_tool_use_name_fails_closed_not_silently(chat):
    fake = FakeProvider([_tool_use_response(("not_a_real_tool", {"x": 1}))])
    interp = LLMInterpreter(provider=fake)
    result = run_turn(chat, interp, "gibberish")
    assert chat.session.order.lines == []
    assert "sorry" in result.reply.lower() or "didn" in result.reply.lower() \
        or result.calls  # a structured error was reported, not silently ignored
    assert chat.session.state != "CONFIRMED"


def test_tool_use_with_wrong_argument_shape_fails_closed(chat):
    """Model supplies an arg the real tool signature doesn't have — must be
    reported as a structured error, never crash the process or silently
    proceed with a corrupted call."""
    fake = FakeProvider([_tool_use_response(
        ("add_item", {"item": "CHEESE PIZZA", "size": "large",
                      "made_up_argument_xyz": 123}))])
    interp = LLMInterpreter(provider=fake)
    run_turn(chat, interp, "large pizza")
    assert chat.session.order.lines == []


# --- 3. unknown tool rejected ------------------------------------------------

def test_unknown_tool_name_never_reaches_a_real_function(chat):
    fake = FakeProvider([_tool_use_response(("delete_all_orders", {}))])
    interp = LLMInterpreter(provider=fake)
    run_turn(chat, interp, "anything")
    assert chat.session.order.lines == []
    assert chat.session.state != "CONFIRMED"


# --- 4. price argument cannot bypass the tool schema ------------------------

def test_no_tool_schema_exposes_a_price_shaped_parameter():
    for schema in _TOOL_SCHEMAS:
        props = set(schema["input_schema"]["properties"].keys())
        assert FORBIDDEN_PRICE_KEYS.isdisjoint(props), \
            f"{schema['name']} schema exposes a price-shaped arg: {props}"


def test_confirm_order_schema_hides_quote_id_from_the_model():
    confirm_schema = next(s for s in _TOOL_SCHEMAS if s["name"] == "confirm_order")
    assert "quote_id" not in confirm_schema["input_schema"]["properties"]
    assert "idempotency_key" not in confirm_schema["input_schema"]["properties"]


def test_a_model_supplied_price_argument_is_rejected_by_the_real_tool(chat):
    """Even if a broken/adversarial model output includes a price-shaped
    arg not in the schema, the real tool signature rejects it (TypeError ->
    structured BAD_ARGS), never silently accepting it."""
    fake = FakeProvider([_tool_use_response(
        ("add_item", {"item": "CHEESE PIZZA", "size": "large", "price": 100}))])
    interp = LLMInterpreter(provider=fake)
    run_turn(chat, interp, "large pizza for a dollar")
    assert chat.session.order.lines == []


# --- 5. fabricated SKU rejected by actual domain validation -----------------

def test_fabricated_topping_rejected_by_real_menu_validation(chat):
    run_turn(chat, RuleBasedInterpreter(), "large cheese")
    fake = FakeProvider([_tool_use_response(
        ("add_modifier", {"line_id": "L1", "modifier": "UNOBTAINIUM"}))])
    interp = LLMInterpreter(provider=fake)
    run_turn(chat, interp, "add unobtainium")
    assert not any(t.name == "UNOBTAINIUM" for t in chat.session.order.lines[0].toppings)
    assert chat.session.order.subtotal() == 1500  # unchanged


# --- 6. ambiguous result can produce clarification --------------------------

def test_search_menu_ambiguous_result_produces_clarification(chat):
    fake = FakeProvider([_tool_use_response(("search_menu", {"query": "chicken"}))])
    interp = LLMInterpreter(provider=fake)
    result = run_turn(chat, interp, "give me chicken")
    assert chat.session.order.lines == []
    assert chat.pending_clarification is not None
    assert "?" in result.reply


# --- 7. multiple tool calls preserve order -----------------------------------

def test_multiple_tool_calls_in_one_turn_execute_in_order_with_last_ref(chat):
    fake = FakeProvider([_tool_use_response(
        ("add_item", {"item": "CHEESE PIZZA", "size": "large"}),
        ("add_modifier", {"line_id": "$LAST", "modifier": "PEPPERONI", "portion": "WHOLE"}),
        ("add_modifier", {"line_id": "$LAST", "modifier": "MUSHROOMS", "portion": "HALF_1"}),
    )])
    interp = LLMInterpreter(provider=fake)
    run_turn(chat, interp, "large pepperoni, mushroom on one half")
    line = chat.session.order.lines[0]
    by_name = {t.name: t.portion for t in line.toppings}
    assert by_name == {"PEPPERONI": "WHOLE", "MUSHROOMS": "HALF_1"}


# --- 8. provider exception leaves authoritative state safe ------------------

def test_provider_exception_does_not_mutate_order_state(chat):
    run_turn(chat, RuleBasedInterpreter(), "large cheese")
    before_lines = len(chat.session.order.lines)
    before_state = chat.session.state

    fake = FakeProvider([ProviderCallError("simulated network timeout")])
    interp = LLMInterpreter(provider=fake)
    result = run_turn(chat, interp, "add pepperoni")

    assert len(chat.session.order.lines) == before_lines
    assert chat.session.state == before_state
    assert "trouble" in result.reply.lower() or "sorry" in result.reply.lower()


def test_provider_exception_does_not_poison_conversation_history(chat):
    fake = FakeProvider([ProviderCallError("boom")])
    interp = LLMInterpreter(provider=fake)
    run_turn(chat, interp, "hello")
    # the failed user turn must not be left dangling in history
    assert chat.llm_history == []


# --- 9. confirmation still requires the real runtime quote_id --------------

def test_model_supplied_quote_id_is_ignored_real_session_id_used(chat):
    # T-019/F14: begin_confirmation and confirm_order now land in separate
    # turns, so this drives two run_turn calls against the same fake
    # provider instead of bundling both tool calls into one round.
    run_turn(chat, RuleBasedInterpreter(), "large cheese")
    quote_result = run_turn(chat, RuleBasedInterpreter(), "what's my total")
    real_quote_id = chat.session.quote_id
    assert real_quote_id is not None

    fake = FakeProvider([
        _tool_use_response(("begin_confirmation", {})),
        _tool_use_response(("confirm_order", {"quote_id": "Q-TOTALLY-FABRICATED"})),
    ])
    interp = LLMInterpreter(provider=fake)
    run_turn(chat, interp, "yes place it")
    assert chat.session.state == "AWAITING_CONFIRMATION"
    result = run_turn(chat, interp, "go ahead")

    assert chat.session.state == "CONFIRMED"
    # prove it was the REAL id that confirmed, not the fabricated one, by
    # checking the executed call's actual args
    confirm_call = [c for c in result.calls if c["tool"] == "confirm_order"][0]
    assert confirm_call["args"]["quote_id"] == real_quote_id
    assert confirm_call["args"]["quote_id"] != "Q-TOTALLY-FABRICATED"


def test_fabricated_quote_id_cannot_confirm_a_never_quoted_order(chat):
    """No request_quote ever happened, so session.quote_id is None — even
    though the model's schema can't supply quote_id at all, prove the
    injected value is the real (absent) one, and confirm fails via the
    real state guard."""
    run_turn(chat, RuleBasedInterpreter(), "large cheese")
    fake = FakeProvider([_tool_use_response(("confirm_order", {}))])
    interp = LLMInterpreter(provider=fake)
    run_turn(chat, interp, "yes place it")
    assert chat.session.state != "CONFIRMED"


# --- 10. provider-specific code does not leak into pricing/order modules ---

def test_no_provider_import_in_domain_modules():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "lakewood"
    for name in ("menu.py", "pricing.py", "orders.py"):
        src = (root / name).read_text()
        assert "llm_provider" not in src
        assert "anthropic" not in src.lower()
        assert "urllib" not in src
