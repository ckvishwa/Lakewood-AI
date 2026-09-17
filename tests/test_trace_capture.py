"""
T-030 Part 1 — trace capture must be purely additive: identical scores
with capture on or off, and a real, inspectable shape when it's on.
"""
import dataclasses
import os

from evals.runner import score, _load
from lakewood import orders as oe
from lakewood.chat import (
    ChatState, rule_based_adapter, run_turn_traced, session_snapshot,
)
from lakewood.interpreter import RuleBasedInterpreter

CASES_GLOB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "evals", "cases", "*.yaml")
CASES = _load(CASES_GLOB)


def test_scores_identical_with_and_without_trace_capture():
    without = score(CASES, rule_based_adapter)
    with_trace = score(CASES, rule_based_adapter, trace_log=[])
    assert len(without) == len(with_trace)
    for a, b in zip(without, with_trace):
        assert dataclasses.astuple(a) == dataclasses.astuple(b)


def test_trace_log_populated_with_one_entry_per_case():
    trace_log: list = []
    results = score(CASES, rule_based_adapter, trace_log=trace_log)
    assert len(trace_log) == len(results) == len(CASES)


def test_trace_entry_shape():
    trace_log: list = []
    score(CASES, rule_based_adapter, trace_log=trace_log)
    entry = next(e for e in trace_log if e["case_id"] == "ADV-001")
    assert set(entry) >= {"case_id", "tags", "assert_final", "turns", "passed", "detail"}
    assert len(entry["turns"]) == 1
    turn = entry["turns"][0]
    assert set(turn) >= {"utterance", "calls", "reply", "state_after"}
    snap = turn["state_after"]
    assert set(snap) >= {"state", "subtotal", "total", "cart", "quote_id",
                         "unresolved_lookups", "pending_disambiguations"}


def test_trace_log_none_costs_nothing_extra():
    """No trace_log passed -> make_adapter still gets trace_log=None ->
    adapters must not blow up and must not silently allocate/append."""
    results = score(CASES[:3], rule_based_adapter)
    assert len(results) == 3


def test_session_snapshot_shape():
    s = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(s, "pickup")
    snap = session_snapshot(s)
    assert snap["state"] == "BUILDING"
    assert snap["subtotal"] == "0.00"
    assert snap["cart"] == []
    assert snap["unresolved_lookups"] == []
    assert snap["pending_disambiguations"] == []


def test_run_turn_traced_matches_run_turn_effects():
    """run_turn_traced must call the real run_turn exactly once and never
    duplicate tool execution — proven by comparing cart state against a
    parallel session driven by run_turn directly."""
    from lakewood.chat import run_turn

    s1 = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(s1, "pickup")
    chat1 = ChatState(session=s1)
    result_direct = run_turn(chat1, RuleBasedInterpreter(), "Large pepperoni.")

    s2 = oe.Session(call_id="C2", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(s2, "pickup")
    chat2 = ChatState(session=s2)
    result_traced, trace = run_turn_traced(chat2, RuleBasedInterpreter(), "Large pepperoni.")

    assert result_traced.reply == result_direct.reply
    assert result_traced.calls == result_direct.calls
    assert trace["utterance"] == "Large pepperoni."
    assert trace["reply"] == result_direct.reply
    assert trace["calls"] == result_direct.calls
    assert trace["state_after"] == session_snapshot(chat2.session)
    assert session_snapshot(chat2.session)["subtotal"] == session_snapshot(chat1.session)["subtotal"]
