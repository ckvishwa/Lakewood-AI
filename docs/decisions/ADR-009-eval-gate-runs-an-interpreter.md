# ADR-009 — The eval release gate must run a real interpreter: a ratcheted `score` gate, kept separate from `validate`

**Status:** Accepted · 2026-09-08

## Context

`evals/runner.py::validate()` — documented as the L1 release gate since this
project's earliest eval docs — never calls `RuleBasedInterpreter.interpret()`
or `LLMInterpreter.interpret()` on any case's `user:` text. It replays each
case's hand-authored `turns[].calls` directly against the real tools and
checks `assert_final`. This proves a label is internally self-consistent; it
proves nothing about whether any interpreter would actually produce those
calls from the English sentence.

T-015 made this concrete: `GOURMET-005` ("gimme a medium number ten, no
pineapple") had a correct label (`intensity: NONE`) and passed `validate` for
its entire existence, while `RuleBasedInterpreter` on that exact sentence
produced `add_modifier(pineapple, WHOLE)` — a full, wrong charge. The gate
and the interpreter it was meant to protect were never connected. This is
structural, not specific to negation: any interpreter defect, in any
category, has always been invisible to `validate`.

`score --adapter rule_based` **does** run the real interpreter and already
reported 24/63 before T-015 (35/71 now, after T-015's fix and 8 new cases).
It has been documented as "interpreter coverage, not a release gate" since
T-012 — nothing was ever wired to enforce it.

## Decision

**Approach B: a separate, ratcheted `score --adapter rule_based` gate,
implemented as a pytest test (`tests/test_evals.py::
test_rule_based_interpreter_meets_baseline`), kept distinct from
`validate`'s label-correctness gate.**

```python
RULE_BASED_BASELINE = 35  # of 71 as of 2026-09-08 — see the comment above
                          # this constant in tests/test_evals.py for the
                          # ratchet rule before changing it.

def test_rule_based_interpreter_meets_baseline():
    results = score(_load(CASES), rule_based_adapter)
    passed = sum(1 for r in results if r.passed)
    assert passed >= RULE_BASED_BASELINE
```

`python -m pytest -q` already runs first in `scripts/check.sh`, before
`validate` — this test is now automatically part of that same gate with zero
new CLI surface, zero new script changes, and the same "a red test blocks
the commit" semantics every other test in this suite already has.

## Alternative considered: Approach A (fold interpreter-checking into `validate`)

Make `validate` also run the configured interpreter on `user:` text — for
every case, or for cases tagged to opt in — and check the same
`assert_final` against the interpreter's own output.

**Rejected.** It conflates two genuinely different questions:

1. Is the business-logic label correct? (`validate`'s actual job)
2. Does *today's* rule-based interpreter — documented everywhere in this
   repo as "a fixed, ordered set of pattern checks... not an NLU system,"
   never meant to cover the whole corpus — happen to parse this exact
   phrasing?

Many existing cases (adversarial phrasing, precise half-and-half-by-number
wording, several corrections) are correct, valid labels proving real
business logic, for utterances the rule-based interpreter was never
expected to handle. Folding (1) and (2) into one gate means either: every
new case must be phrased within the interpreter's current pattern coverage
(actively distorting the corpus toward "what the crude interpreter already
handles" instead of "what customers actually say" — the opposite of
`CLAUDE.md`'s eval philosophy), or a tagging scheme decides which cases opt
in, which is materially the same mechanism as Approach B's separate gate,
just entangled into `validate`'s existing, simpler contract instead of
sitting beside it.

Also decisive: **the gate must run offline and deterministically, in CI,
without a funded API key or a running Ollama instance.** A live LLM adapter
cannot be part of a blocking gate at all — `LAKEWOOD_INTERPRETER=llm`
results (T-013/T-013c/T-013d) stay exactly what they already are: a
separate, non-blocking, honestly-labeled report. Approach A would need a
special case for "which interpreter does `validate` use for this check,"
since only the rule-based one can run in CI; Approach B sidesteps this
entirely by never touching `validate` and only ever exercising
`rule_based_adapter`.

## The threshold is a ratchet, not a target

35/71 (49%) is `CLAUDE.md`'s eval philosophy's honest floor, not its goal —
the 98–99% target in that file is a production-model number, unrelated to
what a documented-as-limited rule-based interpreter should be expected to
hit. `RULE_BASED_BASELINE` may only move **up**, and only after a real
`python evals/runner.py score --adapter rule_based` run confirms the new
number — never lowered to paper over a regression, and never set to the
literal current score without that verification step (that would be
target-gaming the gate the moment it's created). Known limitation, accepted
for simplicity: this is a single aggregate count, not a per-case set
comparison — a same-count swap (one previously-passing case starts failing
while a previously-failing one coincidentally starts passing) would not
trip it. `docs/NEXT_TASKS.md` T-017 and the ~36 other known misses are
explicitly not fixed by this task; the gate makes their existence visible
and permanent, not zero.

## Consequences

- `validate`'s existing behavior — label correctness, `forbid`,
  `expect_disambiguation`, `$ref` binding — is completely unchanged.
- CI gains real interpreter-regression protection with no new
  infrastructure: one pytest assertion, reusing `evals.runner.score` and
  `lakewood.chat.rule_based_adapter`, both already implemented (T-012/T-013).
- Every future PASS/FAIL narrative for "the eval suite" must now
  distinguish `validate` (label correctness) from this ratchet gate
  (interpreter regression protection) from a live `score --adapter llm` run
  (actual model accuracy, still separately unblocked/blocked per T-013's
  own tracking) — `docs/EVALS.md` states this explicitly at every number.
