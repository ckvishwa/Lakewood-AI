# ADR-013 — F15's "unresolved lookup" must clear per-request, not globally

**Status:** Accepted · 2026-09-15

## Context

T-024 (corpus measurement work) opened with an instruction to verify a
suspected scoping gap in F15 before touching anything else, prompted by
`tests/test_confirmation_gate.py::test_resolving_the_miss_clears_the_gate`
using an unrelated resolving query ("sausage" resolving a "root beer" miss)
and still passing.

Reading every call site of `Session.unresolved_lookups` in `orders.py`
confirmed it: `search_menu` (on any hit), `add_item`, `add_modifier`,
`remove_modifier`, `update_item`, and `remove_item` each called
`sess.unresolved_lookups.clear()` unconditionally — a full wipe, never
filtered by whether the new success had anything to do with the specific
request that missed.

**Concrete, common failure shape:** a customer orders "a root beer and a
large pepperoni." `search_menu("root beer")` misses (recorded). The
interpreter continues with the rest of the order: `search_menu("pepperoni")`
succeeds — for a completely unrelated item — and wipes the *entire*
`unresolved_lookups` list, root beer included. `add_item`/`add_modifier` for
the pizza clear it again regardless of relevance. By the time
`begin_confirmation` runs, the root-beer miss is gone and confirmation
proceeds as if it was never asked for.

This is the **exact T-018 dropped-request defect** F15/ADR-011 exists to
close, reachable again through any ordinary multi-item order where one item
misses and a later, unrelated item succeeds — not a rare edge case, the
default shape of a real phone order. `CLAUDE.md`'s own rule: **"routing and
confidence never bypass validation"** — a guard that can be silently
defeated by unrelated activity is not a guard.

The regression predates this task; T-023's own F17 (built in the immediately
preceding session) was scoped correctly from the start — dedupe/clear by
candidate identity, never by "some success happened elsewhere" — which is
what made the contrast visible enough to go looking for it in F15.

## Decision

**F15 clears an entry only when a later request that is textually related to
it succeeds, or when it is explicitly declined — never on an unrelated
success anywhere else in the order.**

- New helper `_query_relates(a, b)`: case-insensitive, bidirectional
  substring check. Deliberately looser than F17's exact candidate-identity
  match, because a genuine `NO_MATCH` has no candidate identity to match
  against at all (nothing matched) — it exists only to catch a customer or
  interpreter literally re-trying the same or a closely worded phrase that
  happens to succeed, not to guess that an unrelated success "counts."
- `search_menu`'s hit branch now removes only the `unresolved_lookups`
  entries related to the *current* query, instead of `.clear()`-ing
  everything.
- The five blanket `.clear()` calls in `add_item`, `add_modifier`,
  `remove_modifier`, `update_item`, and `remove_item` are removed entirely.
  A cart mutation for one item was never a meaningful signal that a
  *different*, unrelated missed request had been resolved — it only ever
  looked like one because nothing was checking.
- `decline_item` (T-023) is extended to also clear matching
  `unresolved_lookups` entries, not just `pending_disambiguations` — the
  same tool, the same matching, the same customer-facing concept
  ("something the customer no longer wants"), now covering both of F15/F17's
  outstanding-request lists instead of just one.

**Consequence accepted on purpose:** a genuine menu miss (something that
truly isn't on the menu, like "root beer" on this one) can no longer resolve
itself through unrelated activity. The interpreter must either find a
textually related way to re-resolve it, or explicitly call `decline_item`
once the customer moves on. This is a real behavior change, not just a bug
fix — it requires an explicit act to drop a genuinely unresolvable request,
matching the exact discipline F17 already established for disambiguations.
An interpreter/model that never learns to call `decline_item` will now see
`begin_confirmation` correctly blocked more often than before — that is the
intended, safer failure mode per `CLAUDE.md`'s own priority order (order
correctness over latency), not a defect to route around.

## Alternatives considered

**Scope by "was any cart mutation the direct result of resolving this
query" (track query→line_id provenance).** Rejected as unnecessary
complexity for this project's current size: `search_menu` never tells the
caller which line, if any, a later mutation was "for," so building that
link would mean threading new state through every tool call for a benefit
`_query_relates` + `decline_item` already covers for the realistic cases
(rephrase-and-retry, or explicit decline). Revisit only if real transcripts
show a resolution shape neither covers.

**Leave F15 as a global clear, document the limitation instead.** Rejected
outright — this is the same defect class ADR-011 already fixed once, found
reachable again through a documented, common conversational shape. A
documented hole is still a hole; `CLAUDE.md` requires a fix, not a caveat,
for a P0 order-correctness defect.

**Make `_query_relates` stricter (exact match only).** Rejected — real
customers and interpreters rephrase ("a Coke" → "just a soda"); an
exact-match-only rule would leave many genuinely-resolved misses stuck
open, trading a silent-drop bug for a false-block bug. The loose,
bidirectional substring check is a deliberate middle ground, with
`decline_item` as the deterministic escape hatch for anything it doesn't
catch.

## Consequences

- `tests/test_confirmation_gate.py::test_resolving_the_miss_clears_the_gate`
  is replaced by two tests reflecting the new, correct semantics: a related
  retry that succeeds (`test_resolving_the_miss_with_a_related_retry_clears_the_gate`)
  and an explicit decline (`test_declining_the_miss_also_clears_the_gate`) —
  "root beer" genuinely isn't on this menu, so no search can ever resolve it
  by design; decline is the only realistic real-world path for that specific
  case. A new named regression,
  `test_unrelated_success_does_not_clear_the_gate`, reproduces the exact
  live-order shape this ADR fixes — proven first as a `strict=True` `xfail`
  against the pre-fix code (per T-024 Part 0's own instruction: file it with
  a failing test before deciding how/when to fix it), now a normal passing
  test against the fix.
- No change to `validate` (71/71), the rule-based ratchet gate (36/71), or
  pricing parity (50/50) — this is domain-layer-only and none of those
  exercise the specific unrelated-success-clears-an-unrelated-miss shape.
- `decline_item`'s docstring/behavior is now dual-purpose (F15 + F17) rather
  than F17-only — a single tool, a single matching rule, covering both
  outstanding-request lists.
- Not addressed here, filed separately (T-024, resumed after this fix):
  auditing the eval corpus for cases whose correct behavior depends on
  asking a clarifying question and giving them a follow-up turn to answer
  it — the reason T-024 was in progress when this finding surfaced.
