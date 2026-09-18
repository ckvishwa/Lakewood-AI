"""T-041: retrieval is a second matcher, and it drifted from search_menu's.

Found by the T-039 live N=3 acceptance gate (docs/STATUS.md's T-041 entry):
the mutation-boundary guard's own evidence-matching vocabulary (plural
words, intensity words, spelled-out numbers) was narrower than what real,
non-adversarial customer language actually produces — and search_menu had
the IDENTICAL gaps, proven directly against the real function, not assumed.
Fixed with one shared normalizer (oe.normalize_menu_text /
oe.normalize_spoken_numbers) rather than four independent patches — see
docs/decisions/ADR-017's T-041 amendment for the full architectural
reasoning (Part 1 of the task this closes).

These tests are unit-level, underneath evals/cases/t041_evidence_gaps.yaml's
corpus coverage: they pin the exact mechanism (the normalizer, the
authorization reason for each gap, the narrowing symmetry fix), not just
the end-to-end cart outcome.
"""

import lakewood.orders as oe
from lakewood.chat import new_session, run_turn, run_turn_traced
from lakewood.interpreter import (
    AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE,
    AUTH_DIRECT_UTTERANCE_EVIDENCE,
    AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT,
    AUTH_UNSUPPORTED_ITEM_SUBSTITUTION,
    LLMInterpreter,
    RuleBasedInterpreter,
    _authorize_item_creation,
    _has_pizza_intent,
)
from lakewood.llm_provider import ProviderResponse


# --- the shared normalizer itself -------------------------------------------

def test_normalize_spoken_numbers_converts_only_in_number_reference_context():
    assert oe.normalize_spoken_numbers("number ten") == "number 10"
    assert oe.normalize_spoken_numbers("no. eleven") == "no. 11"
    assert oe.normalize_spoken_numbers("number twenty seven") == "number 27"


def test_normalize_spoken_numbers_never_touches_a_bare_quantity_word():
    """The FIRST version of this function converted every standalone
    spelled number anywhere in the text and broke _ONE_HALF_RE's "on one
    half"/"a half" idiom outright — found by the full test suite, not
    designed for up front. This is the regression pin for that."""
    assert oe.normalize_spoken_numbers("no cheese on one half") == "no cheese on one half"
    assert oe.normalize_spoken_numbers("half pepperoni, half sausage") == \
        "half pepperoni, half sausage"


def test_normalize_menu_text_collapses_spelled_piece_quantity():
    assert oe.normalize_menu_text("six piece wings") == "6pc wings"
    assert oe.normalize_menu_text("twelve pieces") == "12pc"


def test_normalize_menu_text_is_idempotent():
    once = oe.normalize_menu_text("number ten, six piece wings")
    assert oe.normalize_menu_text(once) == once


# --- search_menu had the identical gaps, proven directly --------------------

def test_search_menu_resolves_spelled_gourmet_cardinal():
    sess = oe.Session(call_id="t", store_id="STORE-001", from_number="+1")
    r = oe.search_menu(sess, "number ten")
    assert r["status"] == "ok"
    assert r["results"] == [{"kind": "gourmet", "number": 10, "name": "Hawaiian"}]


def test_search_menu_resolves_spelled_piece_quantity():
    sess = oe.Session(call_id="t", store_id="STORE-001", from_number="+1")
    r = oe.search_menu(sess, "six piece wings")
    assert r["status"] == "ok"
    assert r["results"] == [{"kind": "item", "name": "6PC WINGS"}]


def test_search_menu_resolves_plural_pizza():
    sess = oe.Session(call_id="t", store_id="STORE-001", from_number="+1")
    r = oe.search_menu(sess, "large pizzas")
    assert r["status"] == "ok"
    assert {"kind": "item", "name": "CHEESE PIZZA"} in r["results"]


def test_search_menu_resolves_plural_generic_drink():
    sess = oe.Session(call_id="t", store_id="STORE-001", from_number="+1")
    r = oe.search_menu(sess, "sodas")
    assert r["status"] == "ok"
    assert {"kind": "item", "name": "CAN"} in r["results"]


# --- _has_pizza_intent: plural + intensity vocabulary ------------------------

def test_has_pizza_intent_recognizes_plural_pizzas():
    assert _has_pizza_intent("three medium cheese pizzas") is True


def test_has_pizza_intent_consumes_intensity_words():
    for word in ("extra", "double", "triple", "quadruple"):
        assert _has_pizza_intent(f"small cheese with {word} pepperoni") is True, word


def test_has_pizza_intent_still_refuses_a_genuinely_unrelated_noun():
    """The fix widens what counts as pizza EVIDENCE; it must not widen what
    counts as a pizza-shaped utterance in general — an unrelated menu noun
    still blocks, exactly as ADR-017 requires."""
    assert _has_pizza_intent("large soup with bacon") is False
    assert _has_pizza_intent("medium tacos with onions") is False


# --- _authorize_item_creation: all four named gaps, at the mutation boundary

def test_authorizes_plural_quantity_pizza_order():
    reason = _authorize_item_creation(
        "CHEESE PIZZA", "MEDIUM", None, None, "three medium cheese pizzas", [], [])
    assert reason == AUTH_DIRECT_UTTERANCE_EVIDENCE


def test_authorizes_triple_intensity_pizza_order():
    reason = _authorize_item_creation(
        "CHEESE PIZZA", "SMALL", None, None,
        "small cheese with triple pepperoni", [], [])
    assert reason == AUTH_DIRECT_UTTERANCE_EVIDENCE


def test_authorizes_gourmet_spelled_cardinal_via_unique_search_hit():
    hit = {"kind": "gourmet", "number": 10, "name": "Hawaiian",
          "_ambiguous": False, "_search_query": "number 10"}
    reason = _authorize_item_creation(
        "PIZZA", "MEDIUM", 10, None,
        "medium number ten, extra pepperoni just on one half", [hit], [])
    assert reason == AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT


def test_authorizes_spelled_wing_quantity_via_pending_candidate():
    pending = [{"query": "wings", "candidates": [
        {"kind": "item", "name": "6PC WINGS"}, {"kind": "item", "name": "12PC WINGS"}],
        "key": frozenset(), "ask_count": 1}]
    reason = _authorize_item_creation(
        "6PC WINGS", None, None, None,
        "the six piece wings, and go ahead with the coupon", [], pending)
    assert reason == AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE


# --- Part 5 safety line: still exactly three allow-paths, adversarial cases
# still refuse. Model-controlled query is never its own authorization.

def test_exactly_three_authorized_reasons_exist():
    from lakewood.interpreter import _AUTHORIZED_REASONS
    assert _AUTHORIZED_REASONS == {
        AUTH_DIRECT_UTTERANCE_EVIDENCE,
        AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT,
        AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE,
    }


def test_model_controlled_unique_wrap_search_still_refused():
    hit = {"kind": "item", "name": "WRAP", "_ambiguous": False, "_search_query": "wrap"}
    reason = _authorize_item_creation("WRAP", None, None, None, "I want a salad.", [hit], [])
    assert reason == AUTH_UNSUPPORTED_ITEM_SUBSTITUTION


def test_model_controlled_unique_coke_search_still_refused():
    hit = {"kind": "item", "name": "CAN", "_ambiguous": False, "_search_query": "coke"}
    reason = _authorize_item_creation("CAN", None, None, None, "I want a salad.", [hit], [])
    assert reason == AUTH_UNSUPPORTED_ITEM_SUBSTITUTION


def test_model_controlled_unique_gourmet_search_still_refused():
    hit = {"kind": "gourmet", "number": 5, "name": "Bruschetta",
          "_ambiguous": False, "_search_query": "bruschetta"}
    reason = _authorize_item_creation(
        "PIZZA", "LARGE", 5, None, "I want a large salad.", [hit], [])
    assert reason == AUTH_UNSUPPORTED_ITEM_SUBSTITUTION


def test_t039b_full_corpus_still_passes():
    """Not a proxy — actually re-run the T-039B adversarial suite in-process
    so a T-041 regression there fails THIS file too, not just on a separate
    CI line someone might skip."""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_t039b_candidate_authorization.py",
         "-q", "--no-header"],
        capture_output=True, text=True, cwd=".")
    assert result.returncode == 0, result.stdout + result.stderr


# --- Part 3: LLM/rule-based narrowing asymmetry -----------------------------

class _FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, system, messages, tools):
        return self.responses.pop(0)


def _response(*calls, text=""):
    uses = [{"id": str(i), "name": name, "input": args}
            for i, (name, args) in enumerate(calls)]
    return ProviderResponse(tool_uses=uses, text=text, stop_reason="tool_use",
                            continue_turn=False, raw_content=uses)


def _llm(*responses):
    return LLMInterpreter(_FakeProvider(responses))


def test_llm_path_narrows_pending_disambiguations_from_a_reply_with_no_tool_call():
    """T-041 Part 3: before this fix, a model turn that only replies with
    its own clarifying text (no tool call at all) left
    session.pending_disambiguations completely untouched — this is the
    exact NONPIZZA-006 flakiness the live N=3 gate found (passed 2 of 3
    runs only by the accident of an extra search_menu call). Now
    deterministic: the customer's own utterance narrows the pending set
    every turn, regardless of what the model does."""
    chat = new_session()
    run_turn(chat, _llm(_response(("search_menu", {"query": "salad"}))), "I want a salad.")
    assert len(chat.session.pending_disambiguations[0]["candidates"]) == 4

    # The model's turn produces ONLY a clarifying reply, no tool call.
    run_turn(chat, _llm(_response(text="Would you like the small or large Garden Salad?")),
             "The garden one.")
    narrowed = chat.session.pending_disambiguations[0]["candidates"]
    assert [c["name"] for c in narrowed] == ["GARDEN SALAD SM", "GARDEN SALAD LG"]

    turn = run_turn(chat, _llm(_response(("add_item", {"item": "GARDEN SALAD LG"}))), "Large.")
    added = next(c["result"] for c in turn.calls if c["tool"] == "add_item")
    assert added["authorization_reason"] == AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE
    assert [line.name for line in chat.session.order.lines] == ["GARDEN SALAD LG"]
    assert chat.session.order.quote()["subtotal"] == "8.00"


def test_narrowing_never_selects_or_authorizes_by_itself():
    """The narrowing step must only ever narrow an ambiguous set to a
    smaller ambiguous set — it must never itself add an item or resolve a
    single winner; that stays _authorize_item_creation's job alone."""
    chat = new_session()
    run_turn(chat, _llm(_response(("search_menu", {"query": "salad"}))), "I want a salad.")
    run_turn(chat, _llm(_response(text="Which one?")), "The large garden salad.")
    # Even though "the large garden salad" uniquely SELECTS one candidate,
    # the narrowing-only step must not add it to the cart by itself.
    assert chat.session.order.lines == []
    assert chat.session.pending_disambiguations
