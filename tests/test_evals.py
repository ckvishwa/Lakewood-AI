"""
Golden labels must be self-consistent. A mislabeled case is worse than none.

T-016: `test_all_golden_labels_are_valid` above proves LABEL correctness —
it replays hand-authored `calls`, it never runs an interpreter, and it never
did (see docs/decisions/ADR-009-eval-gate-runs-an-interpreter.md, the T-015
negation bug this repo shipped through a "63/63 valid" corpus for that exact
reason). `test_rule_based_interpreter_meets_baseline` below is the second,
separate gate that actually runs `RuleBasedInterpreter` against every case's
`user:` text and checks the resulting cart — the one that would have caught
that bug, and the one that catches the next one.
"""

import os

from evals.runner import score, validate, _load

CASES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "evals", "cases", "*.yaml")

# Ratchet, not a target (ADR-009). 35/71 as measured 2026-09-08; raised to
# 36/71 on 2026-09-15 (T-019) — CONFIRM-003 now passes because F14's
# same-turn confirmation gate stops RuleBasedInterpreter's own QUOTED-state
# shortcut from reaching CONFIRMED where the label expects
# AWAITING_CONFIRMATION. Raised to 36/73 on 2026-09-16 (T-024/T-032 corpus
# growth, absolute count unchanged). Raised to 37/73 on 2026-09-17 (T-039) —
# DELIVERY-003 ("actually can you deliver") now passes: a bare "can" used to
# be its own drink-word trigger in interpreter.py's own copy of the drink
# alias table, so this utterance silently added a CAN to the cart before the
# label's `forbid: [add_item]`/`subtotal: 0.00` assertion ever got a chance.
# T-039 replaced that copy with orders.py's real, precedence-correct
# NON_PIZZA_ALIASES table (see docs/decisions/ADR-017), which never treated
# bare "can" as a drink signal to begin with. Real, observed, reproducible;
# not a target — this is the honest floor of what RuleBasedInterpreter, a
# deliberately narrow, non-NLU pattern matcher, actually gets right today.
# CLAUDE.md's 98-99% target is a PRODUCTION MODEL number and has nothing to
# do with this value.
#
# Raised to 41/78 on 2026-09-17 (T-039, same task): 5 new non_pizza_items.yaml
# cases added (corpus growth per Part 4 — the corpus had ZERO non-pizza item
# orders before this task, which is exactly why 73 cases never caught the
# defect a real call did). 4/5 pass under the real rule-based interpreter as
# authored (NONPIZZA-001/002/003/004); NONPIZZA-005's second turn ("the small
# one") is a genuine, pre-existing RuleBasedInterpreter limitation — it has
# no logic to match a bare size phrase against an open item-kind
# disambiguation, unrelated to this task's fix — so it fails, honestly, not
# tuned to pass. 37 (prior baseline) + 4 = 41.
#
# Raised to 46/78 on 2026-09-17 (T-039A, reopening T-039). T-039's fix used
# a finite `_NON_PIZZA_HEAD_WORDS` denylist (12 literal nouns) — replaced
# entirely by fail-closed intent parsing (`_has_pizza_intent`, ADR-017
# superseding section). Net effect on this gate, checked directly:
#   +5 genuine flips (AVAIL-002, CORRECT-001, CORRECT-002, CORRECT-005,
#     INVALID-003) — cases whose utterance has NO real pizza evidence and
#     previously failed because the old code either wrongly created a pizza
#     or (for the inverse shape) wrongly refused a legitimate one; the
#     intent-gate resolves both directions correctly now.
#   -4 corrected labels (ADV-002, ADV-004, COUPON-002, INVALID-002) — each
#     previously asserted the OLD, permissive behavior (create the pizza
#     regardless, silently skip an unrecognized topping/coupon mention
#     riding along with it). Per the task's own invariant ("if unexplained
#     product words remain... do not mutate the cart"), a real unresolved
#     word (truffle, a fabricated coupon description) now correctly blocks
#     pizza creation instead of being silently dropped — the SAME defect
#     class this task closes, just embedded in the compound-utterance shape
#     rather than a bare unknown noun. Relabeled with full justification in
#     each case file, not tuned to pass; see ADR-017's "Consequences".
# 41 (T-039 baseline) + 5 flips - 4 relabeled = 42, but two further real
# code gaps were found and fixed while investigating those flips (adding
# "add"/"to"/"too" to the pizza-shorthand filler set for legitimate
# follow-up phrasing like "add pepperoni too", and recognizing "pie" as
# pizza-word evidence for "a plain pie, medium") which recovered MOD-014,
# CONFIRM-002, and SLANG-002 — net 46/78.
#
# Raise this number ONLY after running the real command and confirming the
# new count:
#     python evals/runner.py score --adapter rule_based
# Never lower it, and never raise it to a number you haven't actually
# observed — either of those defeats the entire point of this gate.
RULE_BASED_BASELINE = 46


def test_all_golden_labels_are_valid():
    results = validate(_load(CASES))
    bad = [(r.case_id, r.detail) for r in results if not r.passed]
    assert not bad, f"mislabeled cases: {bad}"


def test_corpus_is_growing():
    """Guardrail against the corpus quietly rotting. Raise as it grows."""
    assert len(_load(CASES)) >= 78


def test_rule_based_interpreter_meets_baseline():
    """
    T-016's actual gate: runs RuleBasedInterpreter for real against every
    case's `user:` text (via evals.runner.score, the mode `validate` never
    invokes) and fails if fewer cases reach their labeled final cart than
    the recorded baseline. Offline, deterministic, no API key or Ollama —
    qualifies as a blocking CI gate where `score --adapter llm` never could.

    This is a single aggregate count, not a per-case set comparison — a
    same-count swap (one case starts failing while another coincidentally
    starts passing) would not trip it. Accepted tradeoff for simplicity;
    see ADR-009.
    """
    from lakewood.chat import rule_based_adapter
    results = score(_load(CASES), rule_based_adapter)
    passed = sum(1 for r in results if r.passed)
    assert passed >= RULE_BASED_BASELINE, (
        f"rule-based interpreter regression: {passed}/{len(results)} passed "
        f"the real interpreter run, baseline is {RULE_BASED_BASELINE}. If "
        f"this is a genuine interpreter improvement, verify with "
        f"`python evals/runner.py score --adapter rule_based` and raise "
        f"RULE_BASED_BASELINE in this file to match the real number — never "
        f"lower it or raise it to an unverified number.")


def test_gate_would_have_caught_the_t015_negation_bug(monkeypatch):
    """
    T-016 Part 3 — the actual acceptance test for this task. Not "the gate
    runs": would THIS gate have caught the T-015 negation bug? Stubs
    RuleBasedInterpreter back to its pre-T-015 behavior (intensity was never
    inspected at all — always a plain, fully-charged add_modifier) via
    monkeypatch, in this test only; lakewood/interpreter.py is never edited.
    Then runs the exact same gate logic as
    test_rule_based_interpreter_meets_baseline and asserts it goes red.

    If this test doesn't fail before the patch is applied and doesn't
    fail-correctly after, the mechanism T-016 built is the wrong one — see
    docs/decisions/ADR-009's "Alternative considered" section for what to
    reconsider.
    """
    from lakewood.chat import rule_based_adapter
    from lakewood.interpreter import RuleBasedInterpreter, ToolCall

    def _pre_t015_resolve_intensity_calls(self, chat, line_id, name, portion, intensity):
        # The exact pre-fix behavior: intensity was never looked at, so NONE
        # and LITE requests silently became a normal, fully-charged add.
        return [ToolCall("add_modifier", {
            "line_id": line_id, "modifier": name, "portion": portion})]

    monkeypatch.setattr(RuleBasedInterpreter, "_resolve_intensity_calls",
                        _pre_t015_resolve_intensity_calls)

    results = score(_load(CASES), rule_based_adapter)
    passed = sum(1 for r in results if r.passed)
    assert passed < RULE_BASED_BASELINE, (
        f"the ratchet gate did NOT detect the reintroduced T-015 negation "
        f"bug ({passed}/{len(results)} still >= baseline {RULE_BASED_BASELINE}) "
        f"— the mechanism is wrong; revisit ADR-009 Part 1, not this test.")
