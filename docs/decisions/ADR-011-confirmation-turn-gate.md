# ADR-011 — Confirmation is a two-turn handshake, enforced in the domain, not the prompt

**Status:** Accepted · 2026-09-15

## Context

T-018's live baseline against a real provider (Experiential/Luna) surfaced a
P0 finding (`docs/STATUS.md` "T-018 milestone", `docs/NEXT_TASKS.md` T-019):
a customer asked for a topping swap and an add-on drink; both requests hit a
`search_menu` miss that was never retried or surfaced; the interpreter then
called `begin_confirmation` and `confirm_order` back to back, in the same
turn, and the order was confirmed — "You're all set" — missing both things
the customer actually asked for. The same mechanism (same-turn
`begin_confirmation` + `confirm_order`) independently reproduced in two
golden cases, `CONFIRM-003` and `CONFIRM-005`, once `evals/runner.py::score`
was run against the live provider.

Two things made this possible, and both were true by design, not accident:

1. `docs/order-state-machine.md`'s own FSM already specifies confirmation as
   **two transitions** — T5 (`readback_delivered`: QUOTED → AWAITING_
   CONFIRMATION) and T6 (`customer_confirms`: AWAITING_CONFIRMATION →
   CONFIRMED) — but neither `orders.py` nor either interpreter ever enforced
   the boundary between them. `confirm_order`'s only gate was `state ==
   AWAITING_CONFIRMATION`, and `begin_confirmation` sets that state
   immediately, so nothing stopped both firing in the same breath.
2. `RuleBasedInterpreter._confirm()` and the LLM system prompt
   (`lakewood/interpreter.py::_state_context`, the `begin_confirmation` tool
   description) both **explicitly encoded the shortcut** — "call
   begin_confirmation then confirm_order" — as a deliberate simplification
   for the text sandbox, on the reasoning that a single "yes, place it."
   utterance already carries the customer's affirmative. That reasoning
   assumes the cart is right. It does not hold when something silently
   dropped along the way, which is exactly the failure this task found.

`CLAUDE.md`'s own rule: **"Routing and confidence never bypass validation."**
A prompt instruction telling the model not to do this is not a fix — the
live model had almost the identical instruction and did it anyway. This has
to be a domain invariant that holds no matter which interpreter or provider
is driving, enforced where the FSM already lives: `orders.py`.

## Decision

**`confirm_order` deterministically rejects a call arriving in the same
interpreter turn as its own `begin_confirmation` call, regardless of
whether the quote_id/cart_hash it's given are otherwise valid.**

- `Session` gains `turn: int` — incremented exactly once per customer
  utterance, by whichever caller drives the conversation
  (`lakewood/chat.py::run_turn`, or `evals/runner.py::validate()`/`score()`
  replaying a case turn-by-turn). It is **provider- and interpreter-
  agnostic by construction**: whether one utterance took one tool call or
  twelve internal rounds (as `LLMInterpreter`'s multi-round tool-use loop
  can produce), they all share one turn number.
- `begin_confirmation` records `sess.confirmation_turn = sess.turn` when it
  opens the window.
- `confirm_order` checks `sess.confirmation_turn == sess.turn` **after** its
  existing F5 checks (quote_id match, cart_hash match, TTL) — ordered last
  on purpose, so an already-correct rejection (e.g. `STALE_QUOTE` on a
  genuinely stale ref, as `CONFIRM-005`'s hand-authored label deliberately
  exercises) still fires with its own, more specific code. Only a call that
  would otherwise succeed gets the new `PREMATURE_CONFIRMATION` refusal.
- Any cart mutation while `QUOTED`/`AWAITING_CONFIRMATION` already resets to
  `BUILDING` and nulls the quote (T7); `confirmation_turn` resets with it,
  so a stale gate value can never leak into a later, unrelated confirmation
  attempt.
- `RuleBasedInterpreter._confirm()`'s `QUOTED` branch now calls only
  `begin_confirmation`; its existing `AWAITING_CONFIRMATION` branch (already
  present, already calling only `confirm_order`) is what completes the
  second turn once the customer responds again. The LLM system prompt and
  the `begin_confirmation`/`confirm_order` tool descriptions were corrected
  to stop instructing the now-rejected same-turn shortcut — this is a
  factual correction, not the fix itself; the domain guard is what actually
  enforces the invariant, and holds even if a future prompt regresses.

This is paired with two more changes from the same finding, since one alone
was not enough (see `docs/NEXT_TASKS.md` T-019 for the full defense-in-depth
argument):

- **`begin_confirmation` refuses to open at all while a `search_menu` miss
  is still unresolved** (`sess.unresolved_lookups`, cleared by any later
  successful lookup or cart mutation) — the dropped-request half of the
  live failure, not just the turn-boundary half.
- **`begin_confirmation` always returns a real cart-diff readback** (reusing
  `_short_readback`) — there is no path to `AWAITING_CONFIRMATION` without
  one, and `chat.py` now speaks it verbatim instead of a fixed placeholder.

## Alternatives considered

**Prompt-only fix (reword the system prompt / tool descriptions to forbid
the same-turn call).** Rejected outright — the live model already had a
similar instruction ("Never call begin_confirmation or confirm_order
without EXPLICIT affirmative confirmation from the customer this turn") and
violated the shortcut instruction anyway. `CLAUDE.md`: a prompt rule the
model can ignore is not a fix.

**Gate on cart correctness instead of turn boundary** (e.g., only reject
same-turn confirm if `unresolved_lookups` is non-empty). Rejected as the
*sole* mechanism — it would have caught the live flow-2-6 failure but not
`CONFIRM-003`/`CONFIRM-005`, which have a fully valid, complete cart and
still must not confirm same-turn per the FSM's own T5/T6 split. The two
checks are complementary, not substitutes; both are implemented.

**Do nothing to `RuleBasedInterpreter`/`CONFIRM-004`, accept the regression.**
Rejected — `RuleBasedInterpreter._confirm()`'s `QUOTED` branch used the exact
same same-turn shortcut, and `CONFIRM-004`'s golden label was itself
authored around it (`begin_confirmation` + `confirm_order` in one YAML
turn). Leaving these unchanged with the new domain guard in place would have
made `CONFIRM-004` fail and the rule-based interpreter's own confirmation
flow (`test_full_flow_reaches_confirmed_with_real_runtime_quote_id`, plus
`test_f6_confirm_is_idempotent`, `test_f12_confirmed_order_is_immutable`,
and three provider-level fabricated-quote-id tests) regress across the
board. Both were corrected to the genuine two-turn shape instead — the
happy path still reaches `CONFIRMED`, just over two turns, matching the
FSM's own documented design rather than working around it. Net effect on
the rule-based ratchet gate (`tests/test_evals.py::RULE_BASED_BASELINE`):
**35/71 → 36/71 — an improvement**, since `CONFIRM-003` (which was already
failing for exactly this reason) now passes.

## Consequences

- A real customer conversation that used to confirm in one turn
  ("Yes, place it.") now takes two: the assistant reads back what it is
  about to place and asks to proceed, and the customer's next reply is what
  actually confirms. This is a deliberate correctness-over-latency tradeoff
  consistent with `CLAUDE.md`'s priority order (order correctness >
  reliability > ... > latency), and it is also what
  `docs/order-state-machine.md` already specified.
- Every existing test/golden case that drove `begin_confirmation` and
  `confirm_order` in one shot was audited and updated to two turns/rounds;
  none were weakened — see the "Alternatives considered" section above for
  the exact list. `tests/test_confirmation_gate.py` is new: named regression
  tests reproducing the same-turn bypass, the dropped-search-miss bypass,
  and the exact T-018 live scenario, each verified to fail against the
  pre-fix code and pass against this change.
- `chat.py::_finish_turn` was also fixed in the same task (a related but
  separate defect: it reported the *first* error in a multi-round turn even
  when later calls in that same turn succeeded, so a correct cart could be
  reported to the customer as failed — see T-018 Step 1). Grouped here
  because both defects came from the same live run and both are about the
  system's report of what happened matching what actually happened.
- Not fixed by this task, filed separately: `search_menu` phrasing/recall
  quality itself (the root cause of most of the *individual* misses that
  Defense 2 now makes safe rather than making disappear) — see
  `docs/NEXT_TASKS.md`.
