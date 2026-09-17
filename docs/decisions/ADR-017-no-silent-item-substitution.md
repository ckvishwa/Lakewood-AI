# ADR-017 — An unresolved item must never silently become a different item

**Status:** Accepted · 2026-09-17

## Context

A real 10-turn voice session (T-038 Phase 2 Parakeet increment) surfaced a
P0 that 73 corpus cases, every synthetic fixture, and four prior diagnostic
sweeps never found:

| Customer said | System added | Reply |
|---|---|---|
| "A garden salad small and grilled with grilled chicken" | small cheese pizza — chicken | "Got it — small cheese pizza — chicken. Anything else?" |
| "One calzone with mozzarella and ricotta" | (same pizza) — chicken, mozzarella | "Got it…" |
| "One chicken caesar wrap with fries" | (same pizza) — chicken, mozzarella, chicken | "Got it…" |
| "…a two liter coke" | 1 can | "Got it — 1 can." |
| "one appetizer and… extra sauce" | 1 can | "Got it — 1 can." |

STT was correct for all of these — "garden salad," "calzone," "mozzarella
and ricotta," "two liter" all transcribed cleanly. This is not a T-018-class
silent *drop*; the request didn't vanish, it became a *different, real,
priced* item, confirmed back to the customer as if correct.

### Diagnosis (mechanism, per row)

All three pizza rows trace to `RuleBasedInterpreter` alone
(`lakewood/interpreter.py`), never `add_item`'s own domain logic and never
`LLMInterpreter`:

1. **Rows 1–3.** `_new_pizza` triggers on ANY recognized size word anywhere
   in the utterance (`if size:`) and unconditionally calls
   `add_item(item="CHEESE PIZZA", size=...)` — it never checks whether the
   utterance actually named a pizza, and never calls `search_menu` to find
   out. "small" is also a real `GARDEN SALAD SM` size; the interpreter had
   no way to know that. Once that wrong pizza line exists, the bare-topping
   fallback (`if topping and chat.last_line_id`) makes it worse across
   turns: ANY recognized topping word anywhere in ANY later utterance grafts
   onto that line unconditionally, with no check that the utterance is even
   about a pizza — "mozzarella and ricotta" (naming a calzone) and "chicken"
   (naming a wrap) both got scavenged this way, compounding three distinct,
   real, on-menu items (`CALZONE`, `WRAP`, and the salad itself) into one
   increasingly wrong pizza line.
2. **Row 4.** `_find_drink` used a second, independently-maintained copy of
   the drink alias table (`_DRINK_WORDS`) that never received T-032's
   "specific size beats generic word" precedence fix — dict-iteration order
   checked "coke" before "two liter," so "two liter coke" always resolved
   to `CAN`. **A distinct gap, not a T-032 regression**: T-032 fixed
   `orders.py::search_menu`'s `NON_PIZZA_ALIASES` precedence; it never
   touched this second table, which the rule-based fast path used instead
   of `search_menu` for any drink mention.
3. **Row 5.** The same table's worse bug: bare `"can"` — the ordinary modal
   verb in "can I get…" — was its own trigger, unrelated to any actual
   drink mention. `DELIVERY-003`'s existing corpus label ("actually can you
   deliver") was independently found to trip this same collision once
   traced (see Consequences).

`RuleBasedInterpreter._new_pizza` never went through T-020's guarded
`CHEESE PIZZA` pseudo-hit in `search_menu` (which requires the query's
residual to be empty/generic or already topping-grounded) — it bypassed
`search_menu` entirely with its own unconditional default. T-020's guard was
never "abused"; a second, unguarded path existed alongside it.

`add_item`'s own domain layer was checked directly and is NOT the source:
any `item` that isn't a real `NON_PIZZA` key, `"PIZZA"`/`"CHEESE PIZZA"`, or
a valid gourmet number already returns `ITEM_NOT_FOUND` and mutates nothing
(`test_add_item_refuses_an_unresolved_item_name_rather_than_substituting`).
The domain layer was already fail-closed; the interpreter simply never
asked it the real question.

**LLMInterpreter does not share this defect.** It has no `_new_pizza`-style
heuristic — every cart mutation is whatever the model itself calls, through
the real `orders.TOOLS`, governed by the system prompt ("never invent a menu
item... call search_menu — never guess"). Verified two ways, not assumed:
(a) a scripted model that follows the prompt (`search_menu` on a miss, no
`add_item`) leaves the cart untouched, same as the fixed rule-based path;
(b) a scripted model that hallucinates the most naive way — calling
`add_item` with the customer's own words as a literal item name, never
calling `search_menu` at all — still fails closed at `add_item`'s
`ITEM_NOT_FOUND` check, the same domain-layer defense that protects the
rule-based path. T-031's own live trace evidence (real model, `experiential`/
`gpt-5.6-luna`) already showed the live model calls `search_menu` for named
items nearly every run. The one thing this ADR does NOT claim: that a
misbehaving model could never call `add_item(item="CHEESE PIZZA")`
inappropriately — nothing but the system prompt stops that. That residual
risk is prompt-governed, not interpreter-heuristic-governed, and is judged
out of scope here (`CLAUDE.md`: "do not chase this into STT or the model").

**Corpus gap, filed as a finding (EVALS.md):** none of the 73 pre-existing
cases ordered a non-pizza item at all (salad/calzone/wrap/appetizer) — the
exact class of order this defect hits. Ten turns of real speech found a P0
that number of synthetic cases never could.

## Decision

**An utterance naming an item the system cannot resolve must never silently
become a different item. Refuse or clarify. Never substitute.**

`lakewood/interpreter.py`:

- A new veto check (`_NON_PIZZA_HEAD_WORDS`) runs before drink detection,
  new-pizza creation, and the bare-topping graft — the one place, not three
  separate patches, since all three shared the same missing check. If the
  utterance contains a real non-pizza item noun (`salad`, `calzone`, `wrap`,
  `grinder`, `stromboli`, `cheesecake`, `tiramisu`, `hamburger`,
  `cheeseburger`, `burger`, `dinner`, `roll` — each traced to a real
  `oe.NON_PIZZA` key), it routes to a real `search_menu` lookup instead,
  regardless of a co-occurring size/topping/drink word or an already-open
  pizza line. Deliberately excludes shared ingredient words (`chicken`,
  `bacon`, `mozzarella`, `extra`, `double`) that legitimately appear on a
  real pizza order too — this is a veto on ASSUMING pizza, not an attempt to
  resolve the item itself.
- `_find_drink` and `orders.py::search_menu` now share ONE alias table and
  ONE precedence rule (`oe.non_pizza_alias_hits`, promoted from a private
  helper): a specific drink size always wins over a co-occurring generic
  catch-all word in the same query. The second, drifting copy
  (`_DRINK_WORDS`) is deleted entirely — this bug class (a fix landing in
  one of two duplicate tables) cannot recur by construction. Bare `"can"` is
  gone as a standalone trigger; it was never a member of the real,
  `search_menu`-verified alias table to begin with.

This is a **routing/detection fix, not a new guess**: every branch this ADR
touches already only ever produced a `search_menu` call or a real, evidenced
`add_item` — the fix is entirely about which utterances reach which branch.

### Two supporting bugs, same live session, same "fail closed" principle

- **Internal error text must never be spoken.** `"L5 is not a pizza"`
  reached a live customer — `BAD_LINE`'s and `NOT_ON_PIZZA`'s messages are
  built from a raw internal `line_id`, and `chat.py::_finish_turn` spoke any
  domain error's `message` verbatim. Grep-verified against every `err()`
  call site in `orders.py`: these two codes are the only ones whose message
  embeds a raw line_id. `chat.py::_customer_safe_error_message` masks only
  those two codes to a generic, safe apology; every other code's message
  passes through unchanged (already written to be customer-safe).
- **An unusable recording must never kill the call.** `LocalVoiceLoop.turn()`
  had nothing catching `STTCallError`/`UnusableAudioError` (a real HTTP 422
  from the Parakeet server on silence/noise/a hesitation) — it propagated
  straight out of the loop with no handler, killing the whole process. On a
  phone line that's a dropped call, not a retryable turn. `voice.py::turn()`
  now catches `STTCallError`, speaks "Sorry, I didn't catch that — could you
  say that again?", and returns normally so the call continues — the
  interpreter is never invoked for a turn nothing was heard on.

## Alternatives considered

**Add a full non-pizza item resolver inside the interpreter (parse "garden
salad small" directly to `GARDEN SALAD SM`).** Rejected: `search_menu`
already owns real, tested, precedence-aware non-pizza item resolution
(`NON_PIZZA_ALIASES`, filler-stripping, ambiguity handling). Duplicating
that logic in the interpreter is exactly the two-copies-drift bug this ADR
already found and fixed for drinks (`_DRINK_WORDS`) — building a second,
parallel resolver for every other non-pizza item would just plant the next
version of the same defect. The veto only needs to detect "this isn't a
pizza," not resolve what it actually is; resolution stays `search_menu`'s
job, via the existing fallback branch.

**Block `add_item(item="CHEESE PIZZA")` at the domain layer whenever the
query doesn't literally contain "pizza."** Rejected: `add_item` has no
access to the original utterance, only a resolved `item`/`size` — pushing
intent-detection into the domain layer would require threading raw
conversational text into a function whose entire design purpose is staying
provider/voice-agnostic (`CLAUDE.md`: "voice is a frontend... must never
require rewriting the core ordering domain"). Intent detection belongs to
the interpreter layer, which is exactly where this fix landed.

**Leave `_DRINK_WORDS` as its own table with a manually-ported precedence
fix.** Rejected — this is the same "two copies, one gets fixed" shape that
caused row 4/5 in the first place. Sharing the real table via
`oe.non_pizza_alias_hits` makes the bug class structurally impossible to
reintroduce, not just fixed once more.

## Consequences

- `tests/test_item_substitution_guard.py`: 14 tests — a named regression per
  table row (each verified to fail against the pre-fix `interpreter.py`/
  `orders.py`/`chat.py`/`voice.py`, not just written to pass), the shared
  domain-layer fail-closed proof, both LLMInterpreter structural checks, and
  the internal-error-masking proofs.
- `tests/test_voice.py`: 2 new tests proving the STT-crash fix (verified to
  crash pre-fix with the exact same `UnusableAudioError`) and that the call
  survives past the bad turn into the next real one.
- `evals/cases/non_pizza_items.yaml`: 5 new cases — a full non-pizza item
  order (`WRAP`, 2-turn), a genuinely-missing item (`nachos`, clean refusal),
  this ADR's own compound unresolved-head-plus-topping-word shape, the
  two-liter-vs-can distinction, and a genuinely ambiguous non-pizza item
  (`GARDEN SALAD SM`/`LG`) requiring a real clarifying question — the first
  non-pizza item orders in this corpus's history.
- `validate`: 78/78 (was 73/73 — pure corpus growth, no regressions).
- Rule-based ratchet: 41/78 (was 36/73). Broken down: +1 (36→37) from
  `DELIVERY-003` ("actually can you deliver"), previously failing on the
  exact bare-`"can"` collision this ADR fixes — a real, observed,
  independently-discovered instance of the same bug in the PRE-EXISTING
  corpus, not a new case. +4 from 4 of the 5 new `non_pizza_items.yaml`
  cases passing under the real interpreter as authored (`NONPIZZA-005`'s
  second turn is a genuine, unrelated, pre-existing
  `RuleBasedInterpreter` limitation — no logic to match a bare size phrase
  against an open item-kind disambiguation — left honestly failing, not
  tuned to pass).
- Pricing parity: 50/50, unchanged — no pricing/domain-layer change.
- No change to `LLMInterpreter`, `orders.py`'s FSM, or any pricing logic.
