"""
T-019 regression tests — the two real defects found running T-018's live
Experiential/Luna baseline (see docs/STATUS.md "T-018 milestone" and
docs/NEXT_TASKS.md T-019):

1. A confirmed order the customer did not place: `confirm_order` fired in
   the same interpreter turn as `begin_confirmation`, and a dropped
   `search_menu` request (never retried, never surfaced) let an incomplete
   cart get silently confirmed.
2. A correct cart reported as failed: `chat.py::_finish_turn` returned the
   FIRST error in a multi-round turn even when later calls in that same
   turn fixed it.

Each test here reproduces the exact mechanism, not just the symptom, and is
written to fail against the pre-T-019 code and pass against the fix — see
docs/decisions/ADR-011-confirmation-turn-gate.md for the design.
"""
import pytest

from lakewood import orders as oe
from lakewood.orders import (
    add_item, add_modifier, begin_confirmation, confirm_order, decline_item,
    request_quote, search_menu, set_order_type,
)
from lakewood.chat import ChatState, _finish_turn, new_session, run_turn
from lakewood.interpreter import RuleBasedInterpreter


@pytest.fixture
def pickup():
    s = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


# --- Defense 1: same-turn confirm_order is rejected -------------------------

def test_same_turn_confirm_order_is_rejected(pickup):
    """The exact CONFIRM-003/CONFIRM-005 mechanism: begin_confirmation and
    confirm_order called back to back, same turn. Before F14 this reached
    CONFIRMED; it must now be refused and the order must stay open."""
    add_item(pickup, "CHEESE PIZZA", size="large")
    q = request_quote(pickup)
    begin_confirmation(pickup)
    r = confirm_order(pickup, q["quote_id"])
    assert r["status"] == "error"
    assert r["code"] == "PREMATURE_CONFIRMATION"
    assert pickup.state == "AWAITING_CONFIRMATION"


def test_confirm_order_succeeds_in_a_later_turn(pickup):
    """The two-step path this gate requires still works end to end."""
    add_item(pickup, "CHEESE PIZZA", size="large")
    q = request_quote(pickup)
    begin_confirmation(pickup)
    pickup.turn += 1
    r = confirm_order(pickup, q["quote_id"])
    assert r["status"] == "ok"
    assert pickup.state == "CONFIRMED"


def test_cart_mutation_between_begin_and_confirm_clears_the_gate_too(pickup):
    """T7 (any mutation while QUOTED/AWAITING_CONFIRMATION) already forces
    BUILDING and nulls the quote; confirmation_turn must reset with it so a
    stale gate value can't leak into a brand new confirmation attempt."""
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    request_quote(pickup)
    begin_confirmation(pickup)
    add_modifier(pickup, lid, "pepperoni")     # T7: back to BUILDING
    assert pickup.confirmation_turn is None
    assert pickup.state == "BUILDING"


# --- Defense 2: a dropped search_menu request can't be silently confirmed --

def test_unresolved_search_miss_blocks_confirmation(pickup):
    """The exact live-flow-2-6 mechanism: the customer's request ('a Coke')
    never resolves — search_menu misses and nothing ever adds it — yet the
    interpreter goes on to request_quote/begin_confirmation anyway. Before
    F15 this quoted/confirmed a cart missing what the customer asked for."""
    # "root beer" isn't on this menu at all (unlike "Coke"/"soda", which
    # T-020 now correctly resolves via NON_PIZZA_ALIASES to CAN) — a real,
    # still-unresolvable miss, same shape as T-018's live repro.
    add_item(pickup, "CHEESE PIZZA", size="large")
    add_modifier(pickup, "L1", "pepperoni")
    request_quote(pickup)
    miss = search_menu(pickup, "root beer")     # customer asked for this
    assert miss["code"] == "NO_MATCH"
    r = begin_confirmation(pickup)
    assert r["status"] == "error"
    assert r["code"] == "UNRESOLVED_REQUEST"
    assert "root beer" in str(r["unresolved"])
    assert pickup.state == "QUOTED"            # never silently advanced


def test_resolving_the_miss_with_a_related_retry_clears_the_gate(pickup):
    """T-026: a genuinely related follow-up query that now succeeds clears
    the SAME miss it relates to (_query_relates) — the realistic 'let me
    look that up again, worded differently' case."""
    add_item(pickup, "CHEESE PIZZA", size="large")
    request_quote(pickup)
    search_menu(pickup, "sausage top")         # miss — filler word untrimmed
    assert search_menu(pickup, "sausage")["status"] == "ok"   # related retry, hits
    r = begin_confirmation(pickup)
    assert r["status"] == "ok"
    assert pickup.state == "AWAITING_CONFIRMATION"


def test_declining_the_miss_also_clears_the_gate(pickup):
    """T-026: root beer genuinely isn't on this menu — no search will ever
    resolve it. The only realistic real-world resolution is the customer
    explicitly moving on, via decline_item."""
    add_item(pickup, "CHEESE PIZZA", size="large")
    request_quote(pickup)
    search_menu(pickup, "root beer")           # miss, and stays one forever
    r = decline_item(pickup, "root beer")
    assert r["status"] == "ok" and r["cleared"] == 1
    r = begin_confirmation(pickup)
    assert r["status"] == "ok"
    assert pickup.state == "AWAITING_CONFIRMATION"


def test_unrelated_success_does_not_clear_the_gate(pickup):
    """T-026 named regression (was T-024 Part 0's P0 finding, proven with a
    strict xfail before this fix): a real multi-item order where the
    customer asks for root beer (misses) AND a pepperoni pizza (succeeds).
    The pizza search/add has nothing to do with the root beer request and
    must NOT clear it — before this fix, F15's unconditional .clear() on
    every mutation site wiped it anyway, silently reopening the exact
    T-018 dropped-request defect ADR-011 was written to close."""
    search_menu(pickup, "root beer")                    # customer's real miss
    add_item(pickup, "CHEESE PIZZA", size="large")       # unrelated item
    add_modifier(pickup, "L1", "pepperoni")              # unrelated, must not clear
    request_quote(pickup)
    r = begin_confirmation(pickup)
    assert r["status"] == "error"
    assert r["code"] == "UNRESOLVED_REQUEST"
    assert "root beer" in str(r.get("unresolved", ""))


def test_flow_2_to_6_scenario_no_longer_falsely_confirms():
    """Reproduces T-018's actual live transcript end to end through the real
    tool surface: pepperoni pizza ordered, a sausage-half swap and a drink
    add-on both silently miss, then the interpreter tries to place the
    order. The live model reached CONFIRMED at $19.32 missing both; that
    must now be structurally impossible. (T-020 note: "sausage pizza
    topping" and "Coke" — the live model's own queries — now both resolve
    on their own; "root beer" stands in here as the still-genuinely-
    unresolvable request T-018's Defense 2 exists for.)"""
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    set_order_type(sess, "pickup")
    lid = add_item(sess, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(sess, lid, "pepperoni")
    request_quote(sess)
    # the customer's drink request misses and is never retried to success —
    # exactly the shape of the live model's own dropped Coke request
    search_menu(sess, "root beer")
    r = begin_confirmation(sess)
    assert r["status"] == "error"
    assert sess.state == "QUOTED"
    # the wrong-but-plausible outcome T-018 actually observed must not occur
    assert sess.state != "CONFIRMED"


# --- Defense 3: begin_confirmation always returns a real cart-diff readback

def test_begin_confirmation_readback_reflects_the_actual_cart(pickup):
    lid = add_item(pickup, "CHEESE PIZZA", size="large")["line_id"]
    add_modifier(pickup, lid, "pepperoni")
    request_quote(pickup)
    r = begin_confirmation(pickup)
    assert r["status"] == "ok"
    assert "readback" in r and r["readback"]
    assert "pepperoni" in r["readback"].lower()
    assert "$" in r["readback"]


def test_readback_is_spoken_to_the_customer():
    """Defense 3's whole point: the customer must actually HEAR it. Before
    this fix chat.py's reply for begin_confirmation was a fixed placeholder
    ('One moment...') regardless of what got confirmed."""
    chat = new_session()
    interp = RuleBasedInterpreter()
    run_turn(chat, interp, "Large pepperoni.")
    run_turn(chat, interp, "What's my total?")
    reply = run_turn(chat, interp, "Yes, place it.").reply
    assert reply != "One moment..."
    assert "pepperoni" in reply.lower()
    assert "$" in reply


# --- P2: _finish_turn reports the LAST result, not the first error ---------

def test_finish_turn_reflects_last_result_not_first_error():
    """The exact T-018 Step 1/flow-1 bug: an early NO_MATCH that the
    interpreter fully recovers from within the same turn must not be what
    the customer hears — the cart ended up correct."""
    chat = ChatState(session=oe.Session(
        call_id="C1", store_id="STORE-001", from_number="+12035551234"))
    set_order_type(chat.session, "pickup")
    made = [
        {"tool": "search_menu", "args": {"query": "large pepperoni pizza"},
         "result": {"status": "error", "code": "NO_MATCH",
                    "message": "Nothing on the menu matches 'large pepperoni pizza'."}},
        {"tool": "add_item", "args": {"item": "CHEESE PIZZA", "size": "LARGE"},
         "result": add_item(chat.session, "CHEESE PIZZA", size="LARGE")},
        {"tool": "add_modifier", "args": {"line_id": "L1", "modifier": "PEPPERONI"},
         "result": add_modifier(chat.session, "L1", "PEPPERONI")},
    ]
    result = _finish_turn(chat, made, debug=False)
    assert "nothing on the menu matches" not in result.reply.lower()
    assert "pepperoni" in result.reply.lower()


def test_finish_turn_still_reports_a_true_terminal_error():
    """Not a blanket 'always succeed' — if the LAST thing that happened this
    turn really was an error, that's still what the customer should hear."""
    chat = ChatState(session=oe.Session(
        call_id="C1", store_id="STORE-001", from_number="+12035551234"))
    set_order_type(chat.session, "pickup")
    made = [
        {"tool": "add_item", "args": {"item": "CHEESE PIZZA", "size": "LARGE"},
         "result": add_item(chat.session, "CHEESE PIZZA", size="LARGE")},
        {"tool": "search_menu", "args": {"query": "truffle lobster"},
         "result": {"status": "error", "code": "NO_MATCH",
                    "message": "Nothing on the menu matches 'truffle lobster'."}},
    ]
    result = _finish_turn(chat, made, debug=False)
    assert "nothing on the menu matches" in result.reply.lower()
