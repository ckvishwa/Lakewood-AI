"""T-038 Phase 1: the normal text application path uses T-037 persistence."""

from lakewood.chat import PersistentChat
from lakewood.interpreter import LLMInterpreter, RuleBasedInterpreter
from lakewood.llm_provider import ProviderResponse
from lakewood.persistence.memory_repository import InMemorySessionRepository


def _start(repo, call_id, phone="+12035551234"):
    return PersistentChat.start(repo, "+12037588880", call_id, phone)


def test_callback_offer_accepts_revalidated_cart_and_continues():
    """T-039A note: the original fixture used "I want a large pepperoni and
    wings." / "12 piece." — under the pre-T-039A interpreter, "wings" was
    silently dropped from turn 1 and "12 piece." on its own turn silently
    fabricated a SECOND pizza (the numeral "12" collides with the 12-inch
    SMALL size alias) instead of resolving to 12PC WINGS. The test still
    passed, but only because it was inadvertently exercising the same
    silent-substitution defect class T-039A closes — a real, independently
    found instance, reported per CLAUDE.md's incidental-finding rule rather
    than buried here. This test's actual job is persistence plumbing (does a
    mutation survive a resume boundary), not interpreter accuracy, so it now
    uses two unambiguous single-item pizza orders instead."""
    repo = InMemorySessionRepository()
    first = _start(repo, "CALL-1")
    first.run_turn(RuleBasedInterpreter(), "I want a large pepperoni.")
    assert repo.load_session("STORE-001", "CALL-1") is not None

    callback = _start(repo, "CALL-2")
    assert callback.chat is None                 # never silently attached
    assert callback.resume_offer is not None
    assert callback.resume_offer.quote_id is None # recovery invalidates stale authority
    callback.accept_resume()
    callback.run_turn(RuleBasedInterpreter(), "small cheese.")
    assert len(callback.chat.session.order.lines) == 2
    assert callback.chat.session.call_id == "CALL-1"


def test_callback_decline_discards_prior_cart_and_starts_clean():
    repo = InMemorySessionRepository()
    first = _start(repo, "CALL-1")
    first.run_turn(RuleBasedInterpreter(), "large pepperoni")

    callback = _start(repo, "CALL-2")
    assert callback.resume_offer is not None
    callback.decline_resume()
    assert callback.chat.session.order.lines == []
    assert callback.chat.session.call_id == "CALL-2"
    assert repo.load_session("STORE-001", "CALL-1") is None


def test_persisted_confirmation_replays_same_order_after_restart():
    repo = InMemorySessionRepository()
    call = _start(repo, "CALL-1")
    i = RuleBasedInterpreter()
    call.run_turn(i, "large pepperoni")
    call.run_turn(i, "What's my total?")
    call.run_turn(i, "yes place it")
    call.run_turn(i, "go ahead")
    assert repo.get_confirmed_order("STORE-001", call.chat.session.order_id) is not None


class _FakeProvider:
    def __init__(self, responses): self.responses = list(responses)
    def complete(self, system, messages, tools): return self.responses.pop(0)


def _tool(name, args):
    return ProviderResponse(tool_uses=[{"id": "t", "name": name, "input": args}],
                            text="", stop_reason="tool_use", raw_content=[])


def _tools(*calls):
    return ProviderResponse(tool_uses=[{"id": str(i), "name": name, "input": args}
                                       for i, (name, args) in enumerate(calls)],
                            text="", stop_reason="tool_use", raw_content=[])


def test_llm_staged_mutation_is_saved_through_the_same_executor():
    repo = InMemorySessionRepository()
    call = _start(repo, "LLM-1")
    call.run_turn(LLMInterpreter(_FakeProvider([_tool("add_item", {"item": "CHEESE PIZZA", "size": "large"})])), "large cheese")
    reloaded = repo.load_session("STORE-001", "LLM-1")
    assert reloaded is not None and len(reloaded.order.lines) == 1


def test_llm_read_only_tool_does_not_write_a_session():
    repo = InMemorySessionRepository()
    call = _start(repo, "LLM-READ")
    call.run_turn(LLMInterpreter(_FakeProvider([_tool("search_menu", {"query": "wings"})])), "wings")
    assert repo.load_session("STORE-001", "LLM-READ") is None


def test_llm_confirmation_is_durable_and_f14_still_applies():
    repo = InMemorySessionRepository()
    call = _start(repo, "LLM-CONFIRM")
    rule = RuleBasedInterpreter()
    call.run_turn(rule, "large pepperoni")
    call.run_turn(rule, "What's my total?")
    # Same-turn begin+confirm remains refused by the real F14 gate.
    same_turn = LLMInterpreter(_FakeProvider([_tools(("begin_confirmation", {}), ("confirm_order", {}))]))
    call.run_turn(same_turn, "yes place it")
    assert call.chat.session.state != "CONFIRMED"
    # A fresh turn through the staged LLM path persists a confirmed order.
    call.run_turn(LLMInterpreter(_FakeProvider([_tool("confirm_order", {})])), "go ahead")
    order_id = call.chat.session.order_id
    record = repo.get_confirmed_order("STORE-001", order_id)
    assert record is not None
    # Restart: the server-derived idempotency key resolves the exact old order.
    from lakewood.persistence.service import confirm_and_persist
    replay = confirm_and_persist(repo, call.chat.session, call.chat.session.quote_id,
                                 f"confirm:LLM-CONFIRM:{call.chat.session.quote_id}")
    assert replay["order_id"] == order_id
    assert len(repo._confirmed) == 1
