#!/usr/bin/env bash
# Full gate. Run before every commit.
#
# T-016: `pytest -q` below now carries TWO eval gates, not one —
# tests/test_evals.py::test_all_golden_labels_are_valid (label correctness,
# unchanged) and test_rule_based_interpreter_meets_baseline (a ratcheted,
# offline, deterministic check that RuleBasedInterpreter itself still
# produces the labeled carts — see docs/decisions/ADR-009). No new command
# needed here; both run because pytest already runs first. `validate` below
# remains label-correctness only — it has never run an interpreter and
# still doesn't (that's what ADR-009 fixed with a second gate, not by
# changing this one).
set -e
cd "$(dirname "$0")/.."
echo "== unit + acceptance (includes both eval gates: labels + interpreter regression) =="
python3 -m pytest -q
echo
echo "== eval label validation (label correctness only — see ADR-009) =="
python3 evals/runner.py validate
