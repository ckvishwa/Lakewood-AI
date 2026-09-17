"""
T-011: runtime `$ref` value binding in evals/runner.py. These test the
binding mechanism directly (resolve_args) and end-to-end through validate()
— not just via the golden YAML cases, per the task's own instruction not to
rely on YAML alone.
"""

import pytest

from evals.runner import RefError, resolve_args, validate


# --- resolve_args: the binding mechanism in isolation -----------------------

def test_literal_argument_remains_literal():
    args = resolve_args({"size": "large", "quantity": 2}, {})
    assert args == {"size": "large", "quantity": 2}


def test_top_level_captured_field_resolves():
    prior = {"quote": {"quote_id": "Q-abc123", "total": "22.54"}}
    args = resolve_args({"quote_id": {"$ref": "quote.quote_id"}}, prior)
    assert args == {"quote_id": "Q-abc123"}


def test_nested_captured_field_resolves():
    prior = {"add_item": {"line": {"id": "L1", "size": "LARGE"}}}
    args = resolve_args({"line_id": {"$ref": "add_item.line.id"}}, prior)
    assert args == {"line_id": "L1"}


def test_literal_and_ref_args_coexist_in_one_call():
    prior = {"quote": {"quote_id": "Q-xyz"}}
    args = resolve_args(
        {"quote_id": {"$ref": "quote.quote_id"}, "idempotency_key": "k1"}, prior)
    assert args == {"quote_id": "Q-xyz", "idempotency_key": "k1"}


def test_missing_step_fails():
    with pytest.raises(RefError, match="has not run yet"):
        resolve_args({"quote_id": {"$ref": "never_ran.quote_id"}}, {})


def test_missing_field_fails():
    prior = {"quote": {"total": "22.54"}}
    with pytest.raises(RefError, match="quote_id"):
        resolve_args({"quote_id": {"$ref": "quote.quote_id"}}, prior)


def test_field_lookup_on_non_dict_result_fails():
    """A referenced step's result isn't structured as expected (e.g. a bare
    string or list rather than a dict) — must fail, never silently None."""
    prior = {"quote": "not-a-dict"}
    with pytest.raises(RefError, match="not structured as a dict"):
        resolve_args({"quote_id": {"$ref": "quote.quote_id"}}, prior)


@pytest.mark.parametrize("bad_ref", [
    "no_dot_at_all",     # no '.' separator — can't split step from field
    ".leading_dot",
    "trailing_dot.",
    123,                 # not even a string
])
def test_malformed_reference_fails(bad_ref):
    with pytest.raises(RefError):
        resolve_args({"quote_id": {"$ref": bad_ref}}, {"quote": {"quote_id": "Q-1"}})


def test_ref_never_silently_resolves_to_none():
    """Every failure mode raises — resolve_args must never return a dict
    containing a resolved-to-None value for a bad reference."""
    for bad_args in (
        {"quote_id": {"$ref": "missing_step.quote_id"}},
        {"quote_id": {"$ref": "quote.missing_field"}},
    ):
        with pytest.raises(RefError):
            resolve_args(bad_args, {"quote": {"total": "1.00"}})


# --- end to end through validate() ------------------------------------------

def _case(turns, assert_final=None, setup=None):
    return {"id": "T", "setup": setup or {"order_type": "pickup"},
            "turns": turns, "assert_final": assert_final or {}}


def test_quote_id_capture_and_confirm_flow_works_end_to_end():
    # T-019/F14: begin_confirmation and confirm_order must land in separate
    # turns — see docs/decisions/ADR-011-confirmation-turn-gate.md.
    case = _case([
        {"calls": [{"tool": "add_item", "args": {"item": "CHEESE PIZZA", "size": "large"}}]},
        {"calls": [{"tool": "request_quote", "as": "quote", "args": {}}]},
        {"calls": [{"tool": "begin_confirmation", "args": {}}],
         "forbid": ["confirm_order"]},
        {"calls": [
            {"tool": "confirm_order",
             "args": {"quote_id": {"$ref": "quote.quote_id"}}},
        ]},
    ], assert_final={"state": "CONFIRMED", "subtotal": "15.00"})
    results = validate([case])
    assert results[0].passed, results[0].detail


def test_stale_captured_quote_id_still_fails_closed():
    """Same mutate-then-requote-then-present-the-old-id shape as the golden
    CONFIRM-005 case, kept here as a runner-level regression independent of
    the YAML file."""
    case = _case([
        {"calls": [{"tool": "add_item", "args": {"item": "CHEESE PIZZA", "size": "large"}}]},
        {"calls": [{"tool": "request_quote", "as": "first_quote", "args": {}}]},
        {"calls": [{"tool": "add_modifier",
                   "args": {"line_id": "L1", "modifier": "pepperoni"}}]},
        {"calls": [{"tool": "request_quote", "as": "second_quote", "args": {}}]},
        {"calls": [
            {"tool": "begin_confirmation", "args": {}},
            {"tool": "confirm_order",
             "args": {"quote_id": {"$ref": "first_quote.quote_id"}},
             "expect_error": "STALE_QUOTE"},
        ]},
    ], assert_final={"state": "AWAITING_CONFIRMATION", "subtotal": "18.00"})
    results = validate([case])
    assert results[0].passed, results[0].detail


def test_case_fails_deterministically_when_ref_step_missing():
    """A case with a broken $ref must fail the case, not crash the runner or
    silently pass."""
    case = _case([
        {"calls": [{"tool": "confirm_order",
                   "args": {"quote_id": {"$ref": "never_quoted.quote_id"}}}]},
    ])
    results = validate([case])
    assert results[0].passed is False
    assert "never_quoted" in results[0].detail


def test_refs_resolved_are_tracked_without_leaking_the_value():
    case = _case([
        {"calls": [{"tool": "add_item", "args": {"item": "CHEESE PIZZA", "size": "large"}}]},
        {"calls": [{"tool": "request_quote", "as": "quote", "args": {}}]},
        {"calls": [
            {"tool": "begin_confirmation", "args": {}},
            {"tool": "confirm_order",
             "args": {"quote_id": {"$ref": "quote.quote_id"}}},
        ]},
    ], assert_final={"state": "CONFIRMED"})
    results = validate([case])
    assert results[0].refs_resolved == ["quote_id<-quote.quote_id"]
    # the tracked string names the reference path, never the resolved UUID
    assert "Q-" not in results[0].refs_resolved[0]
