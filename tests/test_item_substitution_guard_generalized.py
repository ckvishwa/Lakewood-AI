"""
T-039A (P0, reopening T-039): T-039's fix used a finite `_NON_PIZZA_HEAD_WORDS`
denylist of 12 literal nouns — any product noun NOT on that list ("nachos",
"soup", "tacos", "appetizer", "garlic bread", or any future menu item) still
fell straight through to `_new_pizza`'s unconditional default, since the
denylist encoded "these known bad words," never "positive evidence of pizza
intent." Confirmed directly on commit 765fe1f: all five adversarial phrases
below produced a priced CHEESE PIZZA line.

This file proves the REPLACEMENT — fail-closed intent parsing
(`_has_pizza_intent`/`_pizza_shorthand_residual` in interpreter.py, full
diagnosis and design in docs/decisions/ADR-017) — generalizes correctly:
it blocks arbitrary unseen product nouns without ever naming them, while
every documented pizza-shorthand order keeps working. It also proves the
LLM-path mutation boundary (`_item_creation_is_authorized`) rejects an
`add_item` substitution — for a fabricated pizza OR another valid non-pizza
SKU — that the customer's own utterance and this turn's own `search_menu`
calls give no evidence for, and that internal error/exception detail never
reaches customer dialogue anywhere in that path.

Every test asserts the FINAL PRICED CART and/or the customer-facing reply
text, not just which tool calls were made (EVALS.md's standing rule).
"""

from lakewood import orders as oe
from lakewood.chat import (
    ChatState, _customer_safe_error_message, new_session, run_turn,
)
from lakewood.interpreter import LLMInterpreter, RuleBasedInterpreter
from lakewood.llm_provider import ProviderCallError, ProviderResponse
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.chat import PersistentChat
from lakewood.pricing import NON_PIZZA, money
from lakewood.stt.fake import FakeSTTProvider
from lakewood.tts.fake import FakeTTSProvider
from lakewood.voice import LocalVoiceLoop


def _fresh():
    return new_session(), RuleBasedInterpreter()


def _no_pizza_created(chat) -> bool:
    return not any(isinstance(l, oe.PizzaLine) for l in chat.session.order.lines)


# ===========================================================================
# Part 5, bullet 1 — the five original T-039 transcript rows. Already
# covered by tests/test_item_substitution_guard.py (still green under this
# task's generalized fix, not re-tuned to pass); re-asserted here, tersely,
# so this task's own regression evidence is self-contained.
# ===========================================================================

def test_row1_garden_salad_still_refused():
    chat, interp = _fresh()
    run_turn(chat, interp, "A garden salad small and grilled with grilled chicken.")
    assert _no_pizza_created(chat)
    assert chat.session.order.quote()["subtotal"] == "0.00"


def test_row2_calzone_still_does_not_graft_onto_open_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "Large pepperoni.")
    toppings_before = {(t.name, t.portion) for t in chat.session.order.lines[0].toppings}
    run_turn(chat, interp, "One calzone with mozzarella and ricotta.")
    assert {(t.name, t.portion) for t in chat.session.order.lines[0].toppings} == toppings_before


def test_row3_chicken_wrap_still_does_not_graft_onto_open_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "Large pepperoni.")
    toppings_before = {(t.name, t.portion) for t in chat.session.order.lines[0].toppings}
    run_turn(chat, interp, "One chicken caesar wrap with fries.")
    assert {(t.name, t.portion) for t in chat.session.order.lines[0].toppings} == toppings_before


def test_row4_two_liter_coke_still_correct():
    chat, interp = _fresh()
    run_turn(chat, interp, "A two liter coke.")
    line = chat.session.order.lines[0]
    assert line.name == "2LITER"
    assert chat.session.order.quote()["subtotal"] == money(NON_PIZZA["2LITER"])


def test_row5_bare_can_filler_still_does_not_add_a_can():
    chat, interp = _fresh()
    run_turn(chat, interp, "One appetizer, and can I get extra sauce.")
    assert chat.session.order.lines == []


# ===========================================================================
# Part 5, bullets 2-6 — the five new adversarial phrases from the T-039A
# task itself, verified to produce a priced pizza on commit 765fe1f. None of
# these five nouns is named anywhere in interpreter.py — the fix is the
# generalized intent invariant, not a bigger denylist.
# ===========================================================================

def test_medium_nachos_with_chicken_is_refused_not_a_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "A medium nachos with chicken.")
    assert _no_pizza_created(chat)
    assert chat.session.order.quote()["subtotal"] == "0.00"


def test_small_appetizer_with_chicken_is_refused_not_a_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "A small appetizer with chicken.")
    assert _no_pizza_created(chat)
    assert chat.session.order.quote()["subtotal"] == "0.00"


def test_large_soup_with_bacon_is_refused_not_a_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "A large soup with bacon.")
    assert _no_pizza_created(chat)
    assert chat.session.order.quote()["subtotal"] == "0.00"


def test_medium_tacos_with_onions_is_refused_not_a_pizza():
    chat, interp = _fresh()
    run_turn(chat, interp, "A medium tacos with onions.")
    assert _no_pizza_created(chat)
    assert chat.session.order.quote()["subtotal"] == "0.00"


def test_small_garlic_bread_with_chicken_is_refused_not_a_pizza():
    """The closest call of the five: "garlic" IS a real topping name on
    this menu, so this only refuses correctly because "bread" itself is
    still an unrecognized, unexplained product word — proving the guard
    doesn't get fooled by a real ingredient word appearing alongside an
    unrelated dish name."""
    chat, interp = _fresh()
    run_turn(chat, interp, "A small garlic bread with chicken.")
    assert _no_pizza_created(chat)
    assert chat.session.order.quote()["subtotal"] == "0.00"


# ---------------------------------------------------------------------------
# An arbitrary, never-seen-before product noun — proves generalization, not
# just correct handling of this task's own five hand-picked examples.
# ---------------------------------------------------------------------------

def test_arbitrary_unseen_noun_lasagna_with_size_and_topping_is_refused():
    chat, interp = _fresh()
    run_turn(chat, interp, "A large lasagna with pepperoni.")
    assert _no_pizza_created(chat)
    assert chat.session.order.quote()["subtotal"] == "0.00"


# ---------------------------------------------------------------------------
# Valid shorthand orders (task's own required examples) must keep working —
# the whole point of replacing a denylist with an intent invariant instead
# of, say, requiring the literal word "pizza" every time.
# ---------------------------------------------------------------------------

def test_shorthand_large_pepperoni_still_works():
    chat, interp = _fresh()
    run_turn(chat, interp, "large pepperoni")
    line = chat.session.order.lines[0]
    assert line.size == "LARGE"
    assert any(t.name == "PEPPERONI" for t in line.toppings)


def test_shorthand_small_cheese_still_works():
    chat, interp = _fresh()
    run_turn(chat, interp, "small cheese")
    line = chat.session.order.lines[0]
    assert line.size == "SMALL"
    assert chat.session.order.quote()["subtotal"] == "11.00"


def test_shorthand_medium_pepperoni_with_onions_still_works():
    """`_new_pizza`'s clause-splitting only extracts one topping per comma-
    free clause — a pre-existing, unrelated RuleBasedInterpreter limitation
    (identical on commit 765fe1f, before this task touched anything), not
    something T-039A's intent-gate changes. What this task's invariant
    actually governs is whether a pizza gets created AT ALL for this
    shorthand — it does, correctly, with the recognized topping present."""
    chat, interp = _fresh()
    run_turn(chat, interp, "medium pepperoni with onions")
    line = chat.session.order.lines[0]
    assert line.size == "MEDIUM"
    assert any(t.name == "PEPPERONI" for t in line.toppings)


def test_shorthand_large_half_pepperoni_half_bacon_still_works():
    chat, interp = _fresh()
    run_turn(chat, interp, "large half pepperoni half bacon")
    line = chat.session.order.lines[0]
    by_portion = {t.portion: t.name for t in line.toppings}
    assert by_portion == {"HALF_1": "PEPPERONI", "HALF_2": "BACON"}


# ===========================================================================
# Part 5, bullets — LLM structural guard: a valid CHEESE PIZZA substitution
# and a valid non-pizza SKU substitution, both for an unresolved request.
# ===========================================================================

class _FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, system, messages, tools):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _tool_use(name, args, continue_turn=False):
    return ProviderResponse(
        tool_uses=[{"id": "tu0", "name": name, "input": args}],
        text=None, stop_reason="tool_use", continue_turn=continue_turn,
        raw_content=[{"type": "tool_use", "id": "tu0", "name": name, "input": args}])


def test_llm_substituting_a_valid_cheese_pizza_sku_for_a_salad_is_refused():
    """Task's own adversarial shape: the model calls add_item with a VALID
    SKU (CHEESE PIZZA), not a fabricated unknown string — the exact false-
    confidence gap in the original T-039 LLM test."""
    chat = new_session()
    fake = _FakeProvider([_tool_use("add_item", {"item": "CHEESE PIZZA", "size": "SMALL"})])
    result = run_turn(chat, LLMInterpreter(provider=fake), "A small garden salad with chicken.")
    assert chat.session.order.lines == []
    assert "clarify" in result.reply.lower() or "?" in result.reply


def test_llm_substituting_a_different_valid_non_pizza_sku_is_also_refused():
    """Generalization proof: the guard is not pizza-specific — a model
    calling add_item for a DIFFERENT real, valid SKU (a can of soda) for a
    request that never asked for one is rejected the same way."""
    chat = new_session()
    fake = _FakeProvider([_tool_use("add_item", {"item": "CAN"})])
    result = run_turn(chat, LLMInterpreter(provider=fake), "A small garden salad with chicken.")
    assert chat.session.order.lines == []
    assert "clarify" in result.reply.lower() or "?" in result.reply


def test_llm_direct_pizza_order_without_search_menu_still_works():
    """Must not break a legitimate direct order: the utterance itself is
    positive evidence, no search_menu call needed first."""
    chat = new_session()
    fake = _FakeProvider([_tool_use("add_item", {"item": "CHEESE PIZZA", "size": "large"})])
    run_turn(chat, LLMInterpreter(provider=fake), "large pepperoni")
    assert chat.session.order.lines[0].size == "LARGE"


def test_llm_search_menu_then_gourmet_selection_flow_still_works():
    """Must not break a legitimate search_menu -> selection flow: the
    utterance alone gives no pizza evidence ("bruschetta" isn't "pizza" or
    shorthand), but the model's own prior search_menu hit this turn
    authorizes the matching add_item call."""
    chat = new_session()
    fake = _FakeProvider([
        _tool_use("search_menu", {"query": "bruschetta"}, continue_turn=True),
        _tool_use("add_item", {"item": "PIZZA", "size": "large", "gourmet_number": 5}),
    ])
    run_turn(chat, LLMInterpreter(provider=fake), "i want the bruschetta, large please")
    line = chat.session.order.lines[0]
    assert line.gourmet == 5 and line.size == "LARGE"


# ===========================================================================
# Part 4 — customer-safe failures. Unknown tool, invalid args, provider
# failure, and provider tool-loop exhaustion must never reach dialogue.
# ===========================================================================

def test_rule_based_unknown_tool_never_reaches_customer_dialogue():
    """Defensive proof for chat.py's own executor loop — a call naming a
    tool that doesn't exist must produce a stable, safe reply, never the
    raw '(internal error: ... unknown tool ...)' string."""
    from lakewood.interpreter import Interpretation, ToolCall

    class _StaticInterpreter:
        def interpret(self, chat, text):
            return Interpretation(calls=[ToolCall("debug_admin_tool", {})])

    chat = new_session()
    result = run_turn(chat, _StaticInterpreter(), "anything")
    assert "internal error" not in result.reply.lower()
    assert "debug_admin_tool" not in result.reply


def test_llm_unknown_tool_never_reaches_customer_dialogue():
    chat = new_session()
    fake = _FakeProvider([_tool_use("delete_all_orders", {})])
    result = run_turn(chat, LLMInterpreter(provider=fake), "anything")
    assert "delete_all_orders" not in result.reply
    assert "not a real tool" not in result.reply.lower()


def test_llm_bad_args_end_to_end_never_leaks_the_offending_arg_name():
    """This specific shape (an extra unschematized arg) is rejected by
    `_valid_tool_args`'s own already-safe wording before the real function
    is ever called, so it passes both before and after this task — a
    sanity check for this path, not a distinguishing regression proof.
    `test_domain_error_masking_covers_unknown_tool_and_bad_args_codes`
    below is the actual regression proof for the raw-exception-text shape
    (the `except (TypeError, ValueError): message = str(e)` branch)."""
    chat = new_session()
    fake = _FakeProvider([_tool_use(
        "add_item", {"item": "CHEESE PIZZA", "size": "large", "made_up_argument_xyz": 123})])
    result = run_turn(chat, LLMInterpreter(provider=fake), "large pizza")
    assert "made_up_argument_xyz" not in result.reply
    assert "traceback" not in result.reply.lower()


def test_llm_provider_failure_exception_text_never_reaches_customer_dialogue():
    chat = new_session()
    fake = _FakeProvider([ProviderCallError(
        "HTTP 503 from https://internal-provider.example/v1/complete: upstream timeout")])
    result = run_turn(chat, LLMInterpreter(provider=fake), "large pepperoni")
    assert "internal-provider.example" not in result.reply
    assert "503" not in result.reply
    assert "sorry" in result.reply.lower()


def test_llm_provider_tool_loop_exhaustion_never_reaches_customer_dialogue():
    """13 rounds of tool_use (the loop's own 12-round cap plus one) trips
    the internal 'Provider tool loop exceeded N rounds; turn discarded'
    diagnostic — must never be spoken verbatim."""
    responses = [_tool_use("search_menu", {"query": "wings"}, continue_turn=True)
                for _ in range(13)]
    chat = new_session()
    result = run_turn(chat, LLMInterpreter(provider=_FakeProvider(responses)), "wings")
    assert "discarded" not in result.reply.lower()
    assert "12 rounds" not in result.reply.lower()
    assert "sorry" in result.reply.lower()


def test_domain_error_masking_covers_unknown_tool_and_bad_args_codes():
    """Direct unit proof the shared masking funnel covers the two NEW codes
    this task adds to the mask set, not just T-039's original two."""
    assert "not a real tool" not in _customer_safe_error_message(
        {"code": "UNKNOWN_TOOL", "message": "'debug_admin_tool' is not a real tool."}).lower()
    assert "unexpected keyword" not in _customer_safe_error_message(
        {"code": "BAD_ARGS", "message": "add_item() got an unexpected keyword argument 'xyz'"})


# ===========================================================================
# Part 5 — voice loop surviving unusable audio (T-039's own fix; re-asserted
# here as this task's own required coverage, alongside a reply-content check
# that no internal detail leaks through the degraded turn either).
# ===========================================================================

class _FakeMic:
    def capture(self, path, seconds):
        open(path, "wb").close()
        return seconds


def test_voice_loop_survives_unusable_audio_with_a_safe_reply():
    repo = InMemorySessionRepository()
    call = PersistentChat.start(repo, "+12037588880", "VOICE-T039A", "+12035551234")
    stt = FakeSTTProvider(default_transcript="")  # empty -> UnusableAudioError
    loop = LocalVoiceLoop(call, stt, FakeTTSProvider(), _FakeMic())

    result = loop.turn()  # must not raise

    assert result.transcript == ""
    assert "sorry" in result.reply.lower()
    assert "error" not in result.reply.lower()
    assert "exception" not in result.reply.lower()
    assert call.chat.session.order.lines == []
