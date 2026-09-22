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
# Raised to 50/81 on 2026-09-18 (T-039B: retrieval is not customer
# authorization). T-039A's `_item_creation_is_authorized` treated ANY
# search_menu hit returned this turn as authorization to add_item, even a
# hit returned under needs_disambiguation=True, and even when the model's
# own search query had no support in the customer's utterance — a model
# could say "I want a salad", silently search "wrap"/"coke"/a gourmet
# number, and add that unrelated valid SKU, all inside one authorized-
# looking turn. Replaced with `_authorize_item_creation`, which returns a
# stable reason code (AUTH_DIRECT_UTTERANCE_EVIDENCE /
# AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT / AUTH_CUSTOMER_CONFIRMED_PENDING_
# CANDIDATE authorize; AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED /
# AUTH_UNSUPPORTED_ITEM_SUBSTITUTION do not) and requires, per hit, that the
# hit was not itself ambiguous AND that both the search query and the exact
# returned SKU are independently supported by the customer's own words. A
# real explicit follow-up selection ("the large garden salad") now also
# resolves deterministically against the server-owned
# `session.pending_disambiguations` set via `_select_pending_candidate` —
# shared by both interpreters — including narrowing a family ("the garden
# one") before a later bare size ("large") resolves it. `search_menu`
# ambiguity registration/narrowing/clearing is also now correctly detected
# as a persistence-worthy mutation of authoritative clarification state
# (`PersistentChat._clarification_fingerprint`), so a dropped call between
# an ambiguous search and the customer's next turn no longer loses the
# pending candidates. 3 new corpus cases added
# (`evals/cases/non_pizza_items.yaml`: NONPIZZA-006/007/008 — family
# narrowing, the adversarial unsupported-search shape, and outside-pending-
# set rejection), all 3 pass under the real rule-based interpreter as
# authored. Net: 46 (T-039A baseline) + 1 genuine flip (NONPIZZA-005's
# second turn, "the small one," now resolves via `_select_pending_candidate`
# instead of the old raw substring check that could never match a spoken
# size word against a candidate's abbreviated SM/LG suffix) + 3 new passing
# cases = 50/81. Full mechanism and evidence:
# `docs/decisions/ADR-017-no-silent-item-substitution.md`'s T-039B amendment.
#
# Raised to 61/91, then corrected to 58/91, same day (T-041: the evidence
# check is a second retrieval system — unify it). See the full 61/91
# derivation immediately below, then the correction after it — both kept,
# not silently overwritten, per this file's own "never lower it" rule
# meaning something different: a corrected label is not a regressed
# interpreter.
#
# Raised to 61/91 on 2026-09-18 (T-041: the evidence check is a second
# retrieval system — unify it). The T-039 live N=3 gate found the
# mutation-boundary guard's own evidence vocabulary narrower than real,
# non-adversarial customer language — a plural ("pizzas"), an intensity
# word ("triple"), a spelled gourmet cardinal ("number ten"), and a
# spelled quantity phrase ("six piece wings" vs "6PC WINGS") were each
# wrongly refused, reproducibly, across all 3 live runs. Root-caused to
# search_menu having the IDENTICAL gaps (proven directly:
# `search_menu(sess, "number ten")`/`"six piece wings"`/`"large pizzas"`/
# `"sodas"` all returned NO_MATCH before this task) — fixed with ONE shared
# normalizer (`oe.normalize_menu_text`/`oe.normalize_spoken_numbers`),
# consumed by both `search_menu` and the interpreter's evidence-matching
# layer, not four independent patches. Also fixed the LLM/rule-based
# narrowing asymmetry the gate's own NONPIZZA-006 flakiness pointed at
# (`_narrow_pending_disambiguations`, interpreter.py) — a model's own
# clarifying reply with no tool call now deterministically narrows
# `pending_disambiguations` the same way `RuleBasedInterpreter` already
# does, instead of only by the accident of an extra `search_menu` call.
# 10 new corpus cases added (`evals/cases/t041_evidence_gaps.yaml`: 4 named
# gap regressions + the 6 "voice session noun" utterances from the
# ORIGINAL T-038 real-call P0 that found T-039 in the first place —
# calzone/chicken-caesar-wrap/appetizer/soup/tacos/garlic-bread — which had
# no live corpus coverage until this task). Net: 50 (T-039B baseline) + 1
# genuine flip (`GOURMET-013`, "half number ten Hawaiian, half number eight
# BBQ chicken" — the digit-only `_HH_BY_NUMBER_RE` half-by-number match now
# resolves after `RuleBasedInterpreter.interpret`'s own text is normalized
# at intake) + 10 new passing cases = 61/91. Zero regressions on the prior
# 81 cases (checked directly, case ID for case ID, not assumed). Full
# mechanism, the safety-line re-verification (all three T-039B adversarial
# cases still refuse; still exactly three `_AUTHORIZED_REASONS`), and
# evidence: `docs/decisions/ADR-017-no-silent-item-substitution.md`'s T-041
# amendment; `tests/test_t041_evidence_vocabulary.py`.
#
# Corrected to 58/91, same day (T-041, after the live N=3 gate): the live
# gate itself surfaced 3 LABEL DEFECTS in the 4 gap-regression cases just
# authored above — `QTY-PLURAL-001`, `INTENSITY-WORD-001`,
# `WINGS-QTY-WORD-001` each asserted a cart matching
# RuleBasedInterpreter's OWN separate, pre-existing limitations (no
# multi-item-from-one-utterance creation; text never maps to DOUBLE/TRIPLE
# intensity; T-040's single-non-pizza-hit-always-asks-size UX bug) rather
# than the objectively correct customer-facing answer. The real
# ExperientialProvider (gpt-5.6-luna) got all 3 right, identically, in all
# 3 live runs (3 medium cheese pizzas -> $39.00; TRIPLE-intensity pepperoni
# -> $17.00; 6PC WINGS added directly on a unique unambiguous hit ->
# $8.00) — proof the ORIGINAL labels were wrong, not the model. Relabeled
# to the correct carts (same category as T-039A's `ADV-002`/`ADV-004`/
# `COUPON-002`/`INVALID-002` relabeling — a corrected label is not an
# interpreter regression). RuleBasedInterpreter now honestly fails all 3
# (58/91, down from the mislabeled 61/91) — same documented-limitation
# shape as `MOD-020`/`GOURMET-005`'s own DOUBLE-intensity misses and the
# newly-filed T-040 UX issue. `GOURMET-CARDINAL-001` and the 6 voice-noun
# cases are unaffected (labels were correct; only these 3 required
# correction). Full live-gate evidence: `docs/STATUS.md`'s T-041 live-gate
# entry.
#
# Raised to 59/91 (T-044, ADR-017's fourth amendment): `_has_pizza_intent`
# required the ENTIRE utterance to be pizza-shorthand, which wrongly
# refused the single most common real order shape — a pizza combined with
# ANYTHING else in one breath (a drink, a side, ordinary filler like
# "I'll pick it up"). Replaced with two conditions: positive pizza evidence
# in some clause, AND no unresolved product-bearing word left over anywhere
# else in the utterance (a genuinely separate, fully-named menu item, e.g.
# "and a twelve piece wings", does not block; an unresolved/partial one,
# e.g. "and a chicken caesar wrap", still does). +1 genuine flip
# (`SLANG-001`, "gimme a lg pep" — needed both the new evidence logic and a
# real "pep" -> PEPPERONI alias, T-039-style spoken-form addition, not a
# grammar change). Checked directly, case ID for case ID, against the full
# prior 58-case pass set: zero regressions (two near-misses were found and
# fixed BEFORE this number was raised, not glossed over: `AVAIL-002`/
# `INVALID-003`, a single bare gourmet-number order like "small number
# five" — `RuleBasedInterpreter` has no branch for that shape at all, only
# half-and-half-by-number — briefly looked pizza-shaped when "number" was
# tried as general filler, which would have created a plain CHEESE PIZZA
# instead of correctly falling through to search_menu; fixed by scoping
# "number"/digit consumption to the specific gourmet number actually being
# authorized, never a blanket filler word. `CORRECT-002`/`NONPIZZA-003`
# — comma-based clause splitting let a bare size fragment in its own
# comma-separated clause ("a garden salad, MEDIUM, with grilled chicken")
# count as its own clean pizza clause, and let "garden salad" read as a
# separate, fully-resolved item standing next to it — reopening the
# original substitution shape. Fixed two ways: clause splitting narrowed to
# " and " only (a comma is routinely just a spoken pause inside ONE item's
# description, "and" is what customers actually use to join two distinct
# orders), and a bare size word alone, with nothing else in its clause, no
# longer counts as pizza evidence by itself ("'small' is also a real GARDEN
# SALAD SM size" — the same reasoning the ORIGINAL gate already relied on,
# now enforced per-clause too — "actually make it large" must not read as
# a request for a NEW pizza). Full mechanism, the gourmet-number direct-
# evidence gap (`CORRECT-004`/`GOURMET-013`, previously required a PRIOR
# search_menu hit even when the utterance plainly said "number ten"), and
# `_new_pizza_half_a_half_b` brought under the same evidence check as the
# other five `add_item` call sites: `docs/decisions/ADR-017-no-silent-item-
# substitution.md`'s T-044 amendment; `tests/test_t044_pizza_intent.py`.
#
# Raise this number ONLY after running the real command and confirming the
# new count:
#     python evals/runner.py score --adapter rule_based
# Never lower it to hide a real interpreter regression — a corrected,
# previously-wrong label is not that; never raise it to a number you
# haven't actually observed either.
RULE_BASED_BASELINE = 59


def test_all_golden_labels_are_valid():
    results = validate(_load(CASES))
    bad = [(r.case_id, r.detail) for r in results if not r.passed]
    assert not bad, f"mislabeled cases: {bad}"


def test_corpus_is_growing():
    """Guardrail against the corpus quietly rotting. Raise as it grows."""
    assert len(_load(CASES)) >= 91


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
