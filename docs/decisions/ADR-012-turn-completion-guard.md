# ADR-012 — A turn cannot end silently while a disambiguation it raised is unresolved

**Status:** Accepted (Phase 1 only — structural guard) · 2026-09-15

## Context

T-022's live diagnosis (`docs/STATUS.md` "T-022 diagnosis") reproduced one
finding at true consistency — 5/5 across two independent live runs against
the real provider, `MULTI-002`: a customer names three items in one
utterance ("a large pepperoni pizza, an order of wings, and a two liter").
Two of the three return `search_menu(... needs_disambiguation=true)` — wings
(6PC/12PC) and the drink (CAN/2LITER) are both real, distinct SKUs on this
menu and the request genuinely doesn't say which. The model resolves the
pizza, then ends the turn: *"Got it — large cheese pizza — pepperoni.
Anything else?"* The wings and the drink are gone. The searches
**succeeded** — the system itself asked "which one?" internally and the
answer was never surfaced to the customer or acted on.

This is the same class of finding T-019/ADR-011 already fixed once, in a
different shape: a request the system knew was unresolved reached a
terminal, customer-facing outcome anyway. T-019's Defense 2 (F15) already
covers a search **miss** (`NO_MATCH`) that's never retried. It does not
cover a search **hit** that came back ambiguous and was never followed by a
resolving action — a gap in the same mechanism, not a new mechanism.

`CLAUDE.md`'s own principle, the same one ADR-011 already invoked: **"prompt
for quality, structure for correctness, never the reverse."** A system-prompt
rule telling the model to resolve every named item is real UX value (Phase 2
of T-023) but is not a correctness guarantee — the live model in this exact
diagnosis already had a general "call search_menu whenever ambiguous, never
guess" instruction and the specific failure still occurred. The structural
layer has to exist first, because it's the only part provable offline.

## Decision

**Extend `Session.unresolved_lookups` (F15) with a parallel, separately
tracked concept: `Session.pending_disambiguations` (F17), and a
turn-completion guard that acts on it (F18).**

- `search_menu` now registers an entry in `sess.pending_disambiguations`
  whenever it returns `needs_disambiguation=True` — `{"query", "candidates",
  "key", "ask_count"}`. Deduplicated by candidate identity (`key`, a
  frozenset of `(kind, name, number)` tuples), not by query text, so a
  rephrased follow-up ("wings" vs. "an order of wings") doesn't duplicate the
  same open question.
- `add_item`/`add_modifier` clear any entry whose candidates include the
  item/gourmet-number/topping that call just resolved — matched by
  canonical identity, checked at every successful mutation, not just the
  last call of a turn. This is why a same-turn self-correction (the model
  searches ambiguously, then immediately adds the right thing) never
  surfaces a stale question — it's already gone by the time the turn's
  reply is built.
- A new tool, `decline_item(query)`, is the **only** other way an entry
  clears. Always `ok`, even if nothing matched — declining something not
  actually pending is harmless. This exists because Phase 1 is deterministic
  code, not prompt wording: without a typed tool, there is no way for a
  customer's "forget the wings" to reach domain state at all, per
  `CLAUDE.md`'s own AI/agent rule that no free-form text may mutate
  authoritative state. Auto-exposed to `LLMInterpreter` via the existing
  `orders.TOOLS`-introspection mechanism (T-013) — no schema hand-written,
  same as every other tool.
- `begin_confirmation` (F17) refuses to open while `pending_disambiguations`
  is non-empty, using the same `UNRESOLVED_REQUEST` code and message shape
  F15 already uses — this is the same customer-facing concept ("something
  asked for hasn't been resolved"), not a new one.
- `chat.py::_apply_completion_guard` (F18) is the turn-completion guard
  itself: a single function every `run_turn` exit path is funneled through
  (including the two direct-return branches that predate `_finish_turn`,
  and — harmlessly, since it's a no-op when nothing is pending —
  `_finish_turn`'s existing direct callers in `tests/test_confirmation_gate.py`).
  If `pending_disambiguations` is non-empty when a turn ends, it batches
  every outstanding item into one combined question
  (`chat.py::_build_batched_clarification`, reusing the exact hit-formatting
  `_reply_for_search_menu` already had — extracted to `_format_hit_name`,
  not duplicated) and appends it to whatever else the turn's reply already
  said.
- **Batched, not one-at-a-time, on purpose.** Asking about all outstanding
  items in one reply is normally risky (a customer answers one and the
  system forgets the other) — safe here specifically because the guard
  holds any unanswered item open across turns and re-asks it, so a partial
  answer loses nothing. Fewer turns on a real phone call.
- **Capped** (`MAX_DISAMBIGUATION_ASKS = 2`, same value class as the
  existing `MAX_PARSE_FAILURES` cap and the same shape of consequence): if
  any outstanding item has been asked about `MAX_DISAMBIGUATION_ASKS` times
  with no resolution, the next turn transfers to a human
  (`transfer_to_human(..., "repeated_misunderstanding")`, an existing
  reason) instead of asking again — never a silent drop, never an infinite
  loop.

## Alternatives considered

**Prompt-only fix.** Rejected for the same reason ADR-011 rejected it: this
exact failure occurred under a model that already had a general
never-guess/ask-when-ambiguous instruction. A rule the model can silently
skip is not a correctness guarantee. (This is Phase 2 of T-023, kept
separate and measured separately — a real UX improvement, never sold as the
fix.)

**Reuse `unresolved_lookups` directly instead of a parallel list.** Rejected:
`search_menu` already unconditionally `.clear()`s `unresolved_lookups` on
any hit — including the very ambiguous hit that would need to register
itself — so the same list would self-erase on write. The two concepts (a
miss vs. an unresolved hit) are also genuinely different customer-facing
situations worth being able to inspect/test separately; T-022's own report
explicitly asked for "the parallel case," not a merge.

**One-at-a-time clarification (ask about the first outstanding item, then
the next after it's answered).** Rejected — more turns on a real phone call
for no correctness benefit, since the guard already tolerates a partial
answer safely. Batching is strictly better once the "never silently forget
an unanswered one" guarantee exists.

**No cap (ask forever until resolved).** Rejected — indistinguishable from a
stuck call to a real customer if their answer is consistently unusable
(bad connection, wrong department). `MAX_PARSE_FAILURES` already established
the "cap, then transfer" pattern in this codebase for the same class of
problem; reusing it rather than inventing a different shape.

## Consequences

- A multi-item order with more than one genuine ambiguity now costs the
  customer one extra turn where it previously (incorrectly) cost nothing —
  because "nothing" meant the items were silently dropped. This is the same
  deliberate correctness-over-latency tradeoff ADR-011 made for
  confirmation, consistent with `CLAUDE.md`'s priority order.
- `evals/runner.py score --adapter llm` may score flat or slightly lower on
  the current 71-case corpus purely from this — the corpus does not reward
  a system for asking a question it's now required to ask instead of
  guessing/dropping. That is an accepted, expected tradeoff, not a
  regression signal; see the T-023 report for the actual N=3 numbers.
  `validate` (71/71), the rule-based ratchet gate (36/71), and pricing
  parity (50/50) are all unchanged — this is a live-provider-only surface.
- One existing test, `tests/test_confirmation_gate.py::
  test_resolving_the_miss_clears_the_gate`, used a probe query ("cheese")
  that is itself genuinely ambiguous under the real `search_menu` (matches
  both MOZZARELLA and CHEESE PIZZA) purely to demonstrate F15's own
  miss-clearing mechanism — unrelated to disambiguation. Updated to an
  unambiguous resolving query ("sausage") so it tests exactly what it was
  written to test; F17's own ambiguous-hit interaction is covered by the new
  `tests/test_disambiguation_guard.py` instead. Not weakened — the new,
  stricter behavior it incidentally tripped is correct and is now tested
  directly.
- Phase 2 (a system-prompt rule: "resolve or explicitly ask about every
  named item before ending the turn") is explicitly **not** part of this
  ADR — it is a separate change, measured separately against Phase 1's own
  numbers, not against pre-T-023, per `CLAUDE.md`'s eval-change policy.
- Out of scope, filed separately: `search_menu`'s alias-collision gap
  (T-022's ADV-002 finding — different root cause, search precision, not
  turn sequencing) and the `_HALLUCINATION_CODES` metric-naming issue.
