"""T-039B: retrieval is not customer authorization.

These tests exercise the real LLM staged path, completion guard, deterministic
rule-based follow-up resolver, and PersistentChat repository orchestration.
Every blocked path asserts the final priced cart, not merely tool-call shape.
"""

from lakewood.chat import PersistentChat, new_session, run_turn, run_turn_traced
from lakewood.interpreter import (
    AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED,
    AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE,
    AUTH_DIRECT_UTTERANCE_EVIDENCE,
    AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT,
    AUTH_UNSUPPORTED_ITEM_SUBSTITUTION,
    LLMInterpreter,
    RuleBasedInterpreter,
)
from lakewood.llm_provider import ProviderResponse
from lakewood.persistence.memory_repository import InMemorySessionRepository


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, system, messages, tools):
        return self.responses.pop(0)


def response(*calls, continue_turn=False, text=""):
    uses = [{"id": str(i), "name": name, "input": args}
            for i, (name, args) in enumerate(calls)]
    return ProviderResponse(tool_uses=uses, text=text, stop_reason="tool_use",
                            continue_turn=continue_turn, raw_content=uses)


def llm(*responses):
    return LLMInterpreter(FakeProvider(responses))


def quote(chat):
    return chat.session.order.quote()


def result_for(turn, tool):
    return next(entry["result"] for entry in turn.calls if entry["tool"] == tool)


def test_ambiguous_search_cannot_authorize_add_item_in_the_same_model_response():
    chat = new_session()
    turn = run_turn(chat, llm(response(
        ("search_menu", {"query": "salad"}),
        ("add_item", {"item": "GARDEN SALAD LG"}),
    )), "I want a salad.")

    blocked = result_for(turn, "add_item")
    assert blocked["code"] == AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED
    assert blocked["authorization_reason"] == AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED
    assert chat.session.order.lines == []
    assert quote(chat)["subtotal"] == "0.00"
    assert len(chat.session.pending_disambiguations) == 1
    assert AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED not in turn.reply


def test_ambiguous_search_cannot_authorize_add_item_in_a_later_internal_round():
    chat = new_session()
    turn = run_turn(chat, llm(
        response(("search_menu", {"query": "salad"}), continue_turn=True),
        response(("add_item", {"item": "GARDEN SALAD LG"})),
    ), "I want a salad.")

    assert result_for(turn, "add_item")["authorization_reason"] == \
        AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED
    assert chat.session.order.lines == []
    assert quote(chat)["subtotal"] == "0.00"


def test_explicit_next_turn_selection_chooses_large_garden_and_clears_pending():
    chat = new_session()
    run_turn(chat, llm(response(("search_menu", {"query": "salad"}))),
             "I want a salad.")
    turn = run_turn(chat, llm(response(("add_item", {"item": "GARDEN SALAD LG"}))),
                    "The large garden salad.")

    added = result_for(turn, "add_item")
    assert added["authorization_reason"] == AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE
    assert [line.name for line in chat.session.order.lines] == ["GARDEN SALAD LG"]
    assert quote(chat)["subtotal"] == "8.00"
    assert chat.session.pending_disambiguations == []
    assert chat.pending_clarification is None


def test_garden_one_narrows_persisted_set_and_large_then_resolves():
    chat = new_session()
    rule = RuleBasedInterpreter()
    first = run_turn(chat, rule, "I want a salad.")
    assert "garden salad" in first.reply.lower()

    narrowed = run_turn(chat, rule, "The garden one.")
    assert chat.session.order.lines == []
    assert quote(chat)["subtotal"] == "0.00"
    assert "size" in narrowed.reply.lower()
    names = [c["name"] for c in chat.session.pending_disambiguations[0]["candidates"]]
    assert names == ["GARDEN SALAD SM", "GARDEN SALAD LG"]

    selected = run_turn(chat, rule, "Large.")
    assert [line.name for line in chat.session.order.lines] == ["GARDEN SALAD LG"]
    assert quote(chat)["subtotal"] == "8.00"
    assert chat.session.pending_disambiguations == []
    assert "got it" in selected.reply.lower()


def test_bare_large_does_not_choose_a_family_before_the_set_is_narrowed():
    chat = new_session()
    rule = RuleBasedInterpreter()
    run_turn(chat, rule, "I want a salad.")
    turn = run_turn(chat, rule, "Large.")
    assert chat.session.order.lines == []
    assert quote(chat)["subtotal"] == "0.00"
    assert chat.session.pending_disambiguations
    assert "?" in turn.reply


def test_sku_outside_pending_set_is_rejected():
    chat = new_session()
    run_turn(chat, llm(response(("search_menu", {"query": "salad"}))),
             "I want a salad.")
    turn = run_turn(chat, llm(response(("add_item", {"item": "WRAP"}))),
                    "The large garden salad.")
    assert result_for(turn, "add_item")["authorization_reason"] == \
        AUTH_UNSUPPORTED_ITEM_SUBSTITUTION
    assert chat.session.order.lines == []
    assert quote(chat)["subtotal"] == "0.00"


def test_unique_exact_customer_supported_search_result_remains_functional():
    chat = new_session()
    turn = run_turn(chat, llm(response(
        ("search_menu", {"query": "wrap"}),
        ("add_item", {"item": "WRAP"}),
    )), "I'd like a wrap.")
    assert result_for(turn, "add_item")["authorization_reason"] == \
        AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT
    assert [line.name for line in chat.session.order.lines] == ["WRAP"]
    assert quote(chat)["subtotal"] == "12.00"
    assert AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT not in turn.reply


def test_authorization_reason_is_visible_in_trace_but_not_customer_reply():
    chat = new_session()
    turn, trace = run_turn_traced(chat, llm(response(
        ("search_menu", {"query": "wrap"}),
        ("add_item", {"item": "WRAP"}),
    )), "I'd like a wrap.")
    traced = next(c["result"] for c in trace["calls"] if c["tool"] == "add_item")
    assert traced["authorization_reason"] == AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT
    assert AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT not in turn.reply


def test_model_controlled_unique_wrap_search_is_not_customer_evidence():
    chat = new_session()
    turn = run_turn(chat, llm(response(
        ("search_menu", {"query": "wrap"}),
        ("add_item", {"item": "WRAP"}),
    )), "I want a salad.")
    assert result_for(turn, "add_item")["authorization_reason"] == \
        AUTH_UNSUPPORTED_ITEM_SUBSTITUTION
    assert chat.session.order.lines == []
    assert quote(chat)["subtotal"] == "0.00"
    assert "clarify" in turn.reply.lower() or "?" in turn.reply


def test_model_controlled_unique_coke_search_is_not_customer_evidence():
    chat = new_session()
    turn = run_turn(chat, llm(response(
        ("search_menu", {"query": "coke"}),
        ("add_item", {"item": "CAN"}),
    )), "I want a salad.")
    assert result_for(turn, "add_item")["authorization_reason"] == \
        AUTH_UNSUPPORTED_ITEM_SUBSTITUTION
    assert chat.session.order.lines == []
    assert quote(chat)["subtotal"] == "0.00"


def test_model_controlled_unique_gourmet_search_is_not_customer_evidence():
    chat = new_session()
    turn = run_turn(chat, llm(response(
        ("search_menu", {"query": "bruschetta"}),
        ("add_item", {"item": "PIZZA", "size": "large", "gourmet_number": 5}),
    )), "I want a large salad.")
    assert result_for(turn, "add_item")["authorization_reason"] == \
        AUTH_UNSUPPORTED_ITEM_SUBSTITUTION
    assert chat.session.order.lines == []
    assert quote(chat)["subtotal"] == "0.00"


def test_direct_pizza_evidence_does_not_authorize_an_unspoken_size_or_gourmet():
    sized = new_session()
    size_turn = run_turn(sized, llm(response(
        ("add_item", {"item": "CHEESE PIZZA", "size": "large"}),
    )), "A cheese pizza.")
    assert result_for(size_turn, "add_item")["authorization_reason"] == \
        AUTH_UNSUPPORTED_ITEM_SUBSTITUTION
    assert sized.session.order.lines == []

    gourmet = new_session()
    gourmet_turn = run_turn(gourmet, llm(response(
        ("add_item", {"item": "PIZZA", "size": "large", "gourmet_number": 5}),
    )), "A large pizza.")
    assert result_for(gourmet_turn, "add_item")["authorization_reason"] == \
        AUTH_UNSUPPORTED_ITEM_SUBSTITUTION
    assert gourmet.session.order.lines == []


def test_direct_pizza_shorthand_and_non_pizza_alias_still_work_with_reasons():
    pizza = new_session()
    pturn = run_turn(pizza, llm(response(
        ("add_item", {"item": "CHEESE PIZZA", "size": "large"}),
    )), "Large cheese.")
    assert result_for(pturn, "add_item")["authorization_reason"] == \
        AUTH_DIRECT_UTTERANCE_EVIDENCE
    assert pizza.session.order.lines[0].size == "LARGE"

    drink = new_session()
    dturn = run_turn(drink, llm(response(("add_item", {"item": "CAN"}))),
                     "A Coke, please.")
    assert result_for(dturn, "add_item")["authorization_reason"] == \
        AUTH_DIRECT_UTTERANCE_EVIDENCE
    assert [line.name for line in drink.session.order.lines] == ["CAN"]


def test_pending_candidates_persist_through_real_persistent_chat_recovery():
    repo = InMemorySessionRepository()
    first = PersistentChat.start(repo, "+12037588880", "T039B-1", "+12035550123")
    repo.save_session(first.chat.session)  # the persistent call already exists

    first.run_turn(llm(response(("search_menu", {"query": "salad"}))),
                   "I want a salad.")
    stored = repo.load_session("STORE-001", "T039B-1")
    assert stored is not None and len(stored.pending_disambiguations) == 1
    assert stored.order.lines == []

    recovered = PersistentChat.start(
        repo, "+12037588880", "T039B-2", "+12035550123")
    assert recovered.chat is None and recovered.resume_offer is not None
    assert recovered.resume_offer.pending_disambiguations
    recovered.accept_resume()
    turn = recovered.run_turn(RuleBasedInterpreter(), "The large garden salad.")

    assert [line.name for line in recovered.chat.session.order.lines] == ["GARDEN SALAD LG"]
    assert recovered.chat.session.order.quote()["subtotal"] == "8.00"
    assert recovered.chat.session.pending_disambiguations == []
    assert repo.load_session("STORE-001", "T039B-1").pending_disambiguations == []
    assert "got it" in turn.reply.lower()


def test_narrowed_candidate_family_persists_and_bare_size_resolves_after_recovery():
    repo = InMemorySessionRepository()
    first = PersistentChat.start(repo, "+12037588880", "T039B-N1", "+12035550456")
    repo.save_session(first.chat.session)
    rule = RuleBasedInterpreter()
    first.run_turn(rule, "I want a salad.")
    first.run_turn(rule, "The garden one.")

    narrowed = repo.load_session("STORE-001", "T039B-N1")
    assert [c["name"] for c in narrowed.pending_disambiguations[0]["candidates"]] == [
        "GARDEN SALAD SM", "GARDEN SALAD LG"]
    assert narrowed.order.lines == []

    recovered = PersistentChat.start(
        repo, "+12037588880", "T039B-N2", "+12035550456")
    assert recovered.resume_offer is not None
    recovered.accept_resume()
    recovered.run_turn(RuleBasedInterpreter(), "Large.")

    assert [line.name for line in recovered.chat.session.order.lines] == ["GARDEN SALAD LG"]
    assert recovered.chat.session.order.quote()["subtotal"] == "8.00"
    assert recovered.chat.session.pending_disambiguations == []
