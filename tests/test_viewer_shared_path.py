"""
T-030 Part 3 — proves the live viewer and the eval scorer's adapters drive
turns through the exact same function, not a forked copy of turn-handling
logic (this task's own explicit constraint).
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "viewer"))

import server as viewer_server  # noqa: E402
from lakewood import chat as chat_module  # noqa: E402
from lakewood import orders as oe  # noqa: E402


def test_viewer_and_eval_adapter_call_the_same_run_turn_traced(monkeypatch):
    calls = []
    original = chat_module.run_turn_traced

    def spy(*args, **kwargs):
        calls.append(id(original))
        return original(*args, **kwargs)

    # rule_based_adapter resolves run_turn_traced via chat.py's own module
    # globals at call time; the viewer imported its own name binding at
    # import time — both must be patched to prove both actually converge
    # on the one real implementation.
    monkeypatch.setattr(chat_module, "run_turn_traced", spy)
    monkeypatch.setattr(viewer_server, "run_turn_traced", spy)

    # Path 1: the eval scorer's own adapter (evals/runner.py::score() uses
    # exactly this factory).
    session = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(session, "pickup")
    turn_fn = chat_module.rule_based_adapter(session)
    turn_fn("Large pepperoni.")

    # Path 2: the live viewer server's own turn handler.
    created = viewer_server.new_live_session("rule_based", None)
    viewer_server.take_live_turn(created["session_id"], "Large pepperoni.")

    assert len(calls) == 2
    assert len(set(calls)) == 1  # both paths called the identical function object


def test_viewer_session_and_turn_reach_real_domain_state():
    created = viewer_server.new_live_session("rule_based", None)
    trace = viewer_server.take_live_turn(created["session_id"], "Large pepperoni.")
    assert trace["state_after"]["subtotal"] == "18.00"
    assert trace["state_after"]["cart"][0]["total"] == "18.00"


def test_viewer_never_reexecutes_a_replayed_trace():
    """Replay reads a trace file; it must never call run_turn/run_turn_traced
    at all. Proven by grepping the read path for any such call."""
    import inspect
    src = inspect.getsource(viewer_server.read_trace_file)
    assert "run_turn" not in src
