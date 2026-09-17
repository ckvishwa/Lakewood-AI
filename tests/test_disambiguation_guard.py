"""
T-023 Phase 1 — structural guard against a silently-ended turn while an
outstanding disambiguation (F17/F18) remains open.

Named regression: T-022's MULTI-002 diagnosis (docs/STATUS.md "T-022"),
reproduced 5/5 across two independent live runs against a real provider —
a customer names three items, two return `needs_disambiguation: true`
(wings 6PC/12PC, drink CAN/2LITER), the model resolves only the pizza and
ends the turn. The searches succeeded; nobody ever acted on two of them.
See docs/decisions/ADR-012-turn-completion-guard.md for the design.

Each test reproduces the exact mechanism through the real tool functions
(never faked result dicts) — same style as tests/test_confirmation_gate.py.
"""
import pytest

from lakewood import orders as oe
from lakewood.orders import (
    add_item, add_modifier, begin_confirmation, decline_item,
    request_quote, search_menu, set_order_type,
)
from lakewood.chat import ChatState, _finish_turn, run_turn
from lakewood.interpreter import RuleBasedInterpreter


@pytest.fixture
def pickup():
    s = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


@pytest.fixture
def chat(pickup):
    return ChatState(session=pickup)


# --- F17: registration and precise clearing ---------------------------------

def test_ambiguous_hit_is_registered_as_outstanding(pickup):
    r = search_menu(pickup, "wings")
    assert r["status"] == "ok" and r["needs_disambiguation"]
    assert len(pickup.pending_disambiguations) == 1
    assert pickup.pending_disambiguations[0]["query"] == "wings"


def test_unambiguous_hit_registers_nothing(pickup):
    search_menu(pickup, "sausage")
    assert pickup.pending_disambiguations == []


def test_resolving_add_item_clears_the_matching_entry(pickup):
    search_menu(pickup, "wings")
    add_item(pickup, "6PC WINGS")
    assert pickup.pending_disambiguations == []


def test_resolving_topping_clears_via_add_modifier(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="LARGE")["line_id"]
    search_menu(pickup, "large pepperoni pizza")   # PEPPERONI + CHEESE PIZZA
    assert len(pickup.pending_disambiguations) == 1
    add_modifier(pickup, lid, "PEPPERONI")
    assert pickup.pending_disambiguations == []


def test_partial_answer_clears_only_the_resolved_item(pickup):
    """Two outstanding items; the customer's next action only answers one —
    the other must stay open, not get wiped along with it. "clams" is a
    genuine, still-ambiguous collision (Clams Casino gourmet vs. CLAMS
    topping) — unlike "two liter soda", which T-032 correctly resolves."""
    search_menu(pickup, "wings")
    search_menu(pickup, "clams")
    assert len(pickup.pending_disambiguations) == 2
    add_item(pickup, "6PC WINGS")
    assert len(pickup.pending_disambiguations) == 1
    assert pickup.pending_disambiguations[0]["query"] == "clams"


def test_new_item_introduced_mid_clarification_does_not_erase_the_outstanding_one(pickup):
    """An unrelated new item added while a disambiguation is open must not
    silently clear it — only a matching resolution or explicit decline can."""
    search_menu(pickup, "wings")
    add_item(pickup, "CHEESE PIZZA", size="LARGE")
    assert len(pickup.pending_disambiguations) == 1
    assert pickup.pending_disambiguations[0]["query"] == "wings"


def test_explicit_abandonment_clears_without_resolving(pickup):
    search_menu(pickup, "wings")
    assert len(pickup.pending_disambiguations) == 1
    r = decline_item(pickup, "wings")
    assert r["status"] == "ok"
    assert r["cleared"] == 1
    assert pickup.pending_disambiguations == []


def test_declining_something_not_pending_is_a_harmless_no_op(pickup):
    r = decline_item(pickup, "anchovies")
    assert r["status"] == "ok"
    assert r["cleared"] == 0


def test_rephrased_query_with_the_same_candidates_does_not_duplicate(pickup):
    search_menu(pickup, "wings")
    search_menu(pickup, "an order of wings please")
    assert len(pickup.pending_disambiguations) == 1


# --- F17 blocks begin_confirmation, parallel to F15 -------------------------

def test_outstanding_disambiguation_blocks_confirmation(pickup):
    add_item(pickup, "CHEESE PIZZA", size="LARGE")
    request_quote(pickup)
    search_menu(pickup, "wings")               # ambiguous, never resolved
    r = begin_confirmation(pickup)
    assert r["status"] == "error"
    assert r["code"] == "UNRESOLVED_REQUEST"
    assert "wings" in str(r["outstanding_disambiguations"])
    assert pickup.state == "QUOTED"


def test_resolving_the_disambiguation_clears_the_gate(pickup):
    add_item(pickup, "CHEESE PIZZA", size="LARGE")
    search_menu(pickup, "wings")
    add_item(pickup, "6PC WINGS")   # F4: this mutation is what needs (re-)quoting
    request_quote(pickup)
    r = begin_confirmation(pickup)
    assert r["status"] == "ok"
    assert pickup.state == "AWAITING_CONFIRMATION"


def test_declining_the_disambiguation_also_clears_the_gate(pickup):
    add_item(pickup, "CHEESE PIZZA", size="LARGE")
    request_quote(pickup)
    search_menu(pickup, "wings")
    decline_item(pickup, "wings")
    r = begin_confirmation(pickup)
    assert r["status"] == "ok"
    assert pickup.state == "AWAITING_CONFIRMATION"


# --- Named regression: MULTI-002 exactly, through chat.py's real tail ------

def test_multi_002_both_outstanding_items_surface_in_one_reply(chat):
    """Reproduces T-022's MULTI-002 trace: three items named, two ambiguous
    (wings, a drink), only the pizza resolved. Before this guard the turn
    ended with 'Got it — ... Anything else?' and both outstanding items
    were gone for good. Fails before F17/F18, passes after. Uses "a drink"
    rather than the original "two liter soda" for the second ambiguous
    item — T-032 correctly resolves "two liter soda" now (a specific size
    beats the generic "soda" alias), so it's no longer ambiguous; "a drink"
    (genuinely, intentionally 3-way ambiguous — CAN/2LITER/20OZ, untouched
    by T-032) preserves the guard-batching scenario this test is actually
    for."""
    s1 = search_menu(chat.session, "large pepperoni pizza")
    s2 = search_menu(chat.session, "wings")
    s3 = search_menu(chat.session, "a drink")
    a1 = add_item(chat.session, "CHEESE PIZZA", size="LARGE")
    a2 = add_modifier(chat.session, a1["line_id"], "PEPPERONI")
    made = [
        {"tool": "search_menu", "args": {"query": "large pepperoni pizza"}, "result": s1},
        {"tool": "search_menu", "args": {"query": "wings"}, "result": s2},
        {"tool": "search_menu", "args": {"query": "a drink"}, "result": s3},
        {"tool": "add_item", "args": {"item": "CHEESE PIZZA", "size": "LARGE"}, "result": a1},
        {"tool": "add_modifier",
         "args": {"line_id": a1["line_id"], "modifier": "PEPPERONI"}, "result": a2},
    ]
    result = _finish_turn(chat, made, debug=False)

    # the pizza+pepperoni ambiguity resolved within the same turn and must
    # NOT still be asked about
    assert "pepperoni" not in result.reply.lower() or "6pc" in result.reply.lower()
    # both outstanding items must be surfaced in this SAME reply, batched
    reply = result.reply.lower()
    assert "6pc wings" in reply and "12pc wings" in reply
    assert "can" in reply and "2liter" in reply
    assert len(chat.session.pending_disambiguations) == 2

    # and the turn-completion guard must also block confirmation downstream
    request_quote(chat.session)
    r = begin_confirmation(chat.session)
    assert r["status"] == "error"
    assert r["code"] == "UNRESOLVED_REQUEST"


def test_multi_002_scenario_fully_resolves_once_both_items_are_answered(chat):
    """The happy continuation: the customer answers both follow-up
    questions in their next turn, and the order can then confirm normally."""
    search_menu(chat.session, "large pepperoni pizza")
    search_menu(chat.session, "wings")
    search_menu(chat.session, "a drink")
    a1 = add_item(chat.session, "CHEESE PIZZA", size="LARGE")
    add_modifier(chat.session, a1["line_id"], "PEPPERONI")
    _finish_turn(chat, [], debug=False)  # turn ends, both still outstanding

    add_item(chat.session, "6PC WINGS")
    add_item(chat.session, "2LITER")
    assert chat.session.pending_disambiguations == []

    request_quote(chat.session)
    r = begin_confirmation(chat.session)
    assert r["status"] == "ok"


# --- F18: the repeat cap -----------------------------------------------------

def test_repeat_cap_transfers_to_human_instead_of_looping_forever(chat):
    search_menu(chat.session, "wings")
    for _ in range(oe.MAX_DISAMBIGUATION_ASKS):
        result = _finish_turn(chat, [], debug=False)
        assert chat.session.state != "TRANSFERRED"
        assert "wings" in result.reply.lower()
    result = _finish_turn(chat, [], debug=False)
    assert chat.session.state == "TRANSFERRED"
    assert chat.session.pending_disambiguations == []
    assert chat.session.transfer_reason == "repeated_misunderstanding"


def test_answering_before_the_cap_never_transfers(chat):
    search_menu(chat.session, "wings")
    _finish_turn(chat, [], debug=False)   # one unanswered ask, below the cap
    add_item(chat.session, "6PC WINGS")   # then resolved
    result = _finish_turn(chat, [], debug=False)
    assert chat.session.state != "TRANSFERRED"
    assert chat.session.pending_disambiguations == []


# --- Interpreter/provider-agnostic: same guard through run_turn ------------

def test_guard_fires_identically_through_run_turn_with_rule_based_interpreter():
    """The guard must apply regardless of which interpreter is driving —
    exercised here through the real run_turn/RuleBasedInterpreter path
    rather than a direct _finish_turn call."""
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(sess, "pickup")
    chat = ChatState(session=sess)
    search_menu(sess, "wings")   # simulate an outstanding item raised earlier
    result = run_turn(chat, RuleBasedInterpreter(), "What's my total?")
    assert "wings" in result.reply.lower()
