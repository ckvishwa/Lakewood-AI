# ADR-017 — An unresolved item must never silently become a different item

**Status:** Accepted · 2026-09-17. **Amended 2026-09-17 (T-039A, same day,
reopened).** The invariant this ADR establishes is unchanged; the
enforcement mechanism described in the original "Decision" section below
(`_NON_PIZZA_HEAD_WORDS`, a 12-word denylist) has been REPLACED, not
extended, by fail-closed intent parsing. See "Amendment: fail-closed intent
parsing replaces the denylist (T-039A)" after "Consequences" for the
current mechanism — read that section for what the code actually does
today; the original "Decision" section is kept for historical diagnosis
context only and is marked accordingly.

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

## Decision (superseded 2026-09-17 by the T-039A amendment below — kept as historical record of the original mechanism)

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

## Amendment: fail-closed intent parsing replaces the denylist (T-039A)

**T-039 was closed with `_NON_PIZZA_HEAD_WORDS`, a 12-word denylist
(`salad`, `calzone`, `wrap`, `grinder`, `stromboli`, `cheesecake`,
`tiramisu`, `hamburger`, `cheeseburger`, `burger`, `dinner`, `roll`). That
does not establish the invariant this ADR claims — it only special-cases 12
specific nouns.** Any product noun NOT on that list still fell straight
through to `_new_pizza`'s unconditional default. Confirmed directly, same
day, on the commit that closed T-039 (`765fe1f`):

| Customer said | System added |
|---|---|
| "A medium nachos with chicken." | medium cheese pizza — chicken — $16.00 |
| "A small appetizer with chicken." | small cheese pizza — chicken — $13.50 |
| "A large soup with bacon." | large cheese pizza — bacon — $18.00 |
| "A medium tacos with onions." | medium cheese pizza — onions — $15.50 |
| "A small garlic bread with chicken." | small cheese pizza — chicken — $13.50 |

None of these five nouns was ever on the denylist — the mechanism was
structurally incapable of generalizing, by construction, regardless of list
length.

### Decision (current)

**A pizza may be created only when the utterance provides positive,
deterministic evidence of pizza intent** — never on the absence of a
recognized non-pizza noun.

`lakewood/interpreter.py`:

- `_has_pizza_intent(text)`: true when either (a) the literal word "pizza"
  or its common slang "pie" appears, or (b) `_pizza_shorthand_residual(text)`
  is empty — every meaningful token in the utterance is consumed by a real
  size, topping (the FULL vocabulary, including bare "cheese" — "small
  cheese" is a valid order), half/portion phrasing, negation/lite modifier,
  quantity word, or a narrow, curated set of ordinary connective filler
  (articles, "and"/"with", "please", "add"/"to"/"too", etc. — never a real
  menu word). A non-empty residual ("nachos", "soup", "garden salad" — none
  of them a recognized token) is positive evidence of something else, and
  blocks pizza creation. This subsumes the old denylist with no enumeration
  at all: the 12 words it covered are simply never consumable by the
  residual, the same as any other unlisted noun.
- `_new_pizza`'s trigger (any size word) and the bare-topping graft onto an
  already-open line are both now gated by `_has_pizza_intent` — the SAME
  predicate, not two copies. Topping scavenging (`_new_pizza`'s own clause
  loop) only ever runs after this gate passes, satisfying the task's own
  requirement that scavenging happen only once pizza intent is established.
- **LLM-path mutation boundary** (`_item_creation_is_authorized`, in
  `LLMInterpreter._interpret_staged`'s tool-dispatch loop): generalizes past
  pizza. A model's `add_item` call — for a pizza OR a valid non-pizza SKU —
  is authorized only when `_has_pizza_intent` holds for a pizza-creating
  call, or (for any item) the model is selecting a specific candidate an
  authoritative `search_menu` call already returned THIS turn, or (for a
  non-pizza item, e.g. a drink) `oe.non_pizza_alias_hits` finds real textual
  evidence for that specific item. Proven both directions: the original
  T-039 LLM test only ever scripted a fabricated *unknown* SKU
  (`"garden salad"`), which trivially failed `add_item`'s own
  `ITEM_NOT_FOUND` check — false confidence, since a model substituting a
  *valid* SKU (`CHEESE PIZZA`, or a different valid item like `CAN`) for an
  unresolved request was never blocked by anything but the system prompt.
  Both shapes are now rejected before the real tool ever runs, while a
  direct pizza order with no prior `search_menu` call, and a real
  `search_menu` → gourmet-selection flow, both still work unchanged.
- **Customer-safe failures extended** (Part 4, same task): unknown tool
  names (both interpreters' mutation loops), raw Python exception text from
  a caught `TypeError`/`ValueError`, provider-call failures, and the
  internal "tool loop exceeded N rounds; turn discarded" diagnostic are all
  masked to a stable, generic apology at the shared funnel
  (`chat.py::_customer_safe_error_message`, mask set extended from
  `{BAD_LINE, NOT_ON_PIZZA}` to include `UNKNOWN_TOOL`/`BAD_ARGS`) or, for
  the LLM provider-exception path, by no longer interpolating the exception
  into `say=`. Diagnostic detail remains available via
  `last_provider_error`/`made`/traces for logs.

### Known, accepted tradeoff

An utterance combining a clear pizza base with ONE unresolvable modifier or
unrelated clause ("I know you have a truffle topping, just put it on my
medium cheese"; "large cheese, and apply the half off everything code") now
refuses the WHOLE utterance rather than creating the pizza and separately
failing the unresolvable part. The old, more permissive behavior was
arguably more convenient for a genuinely confused customer in that specific
shape; it is also the exact reasoning ("a recognized topping word is
present, so it's pizza") that made the original defect possible, and cannot
be special-cased back in without reopening it. Four pre-existing corpus
labels (`ADV-002`, `ADV-004`, `COUPON-002`, `INVALID-002`) asserted the old
behavior and were corrected to the new one, each with its own comment
explaining why.

### Consequences (T-039A)

- `lakewood/interpreter.py`: `_NON_PIZZA_HEAD_WORDS` deleted entirely (not
  deprecated in place); `_has_pizza_intent`/`_pizza_shorthand_residual`/
  `_item_creation_is_authorized` added.
- `lakewood/chat.py`: `_INTERNAL_ID_ERROR_CODES`/`_INTERNAL_ID_FALLBACK`
  renamed and broadened to `_INTERNAL_DETAIL_ERROR_CODES` (now 4 codes)/
  `_CUSTOMER_SAFE_FALLBACK`; the rule-based executor's unknown-tool path no
  longer builds a raw `f"(internal error: ...)"` string.
- `tests/test_item_substitution_guard_generalized.py`: 25 new tests — the
  five original T-039 rows re-asserted against the new mechanism, the five
  new adversarial phrases (verified to fail on `765fe1f`), an arbitrary
  unseen noun, all four required valid-shorthand orders, both LLM
  substitution shapes (pizza and non-pizza SKU), both legitimate LLM flows
  (direct order, search-then-select), five Part 4 safe-failure proofs, and
  the voice-loop-survives-unusable-audio proof.
- Two real, narrow code gaps found and fixed while investigating corpus
  flips (not scope creep — both are direct requirements of the same
  intent-parsing mechanism): "add"/"to"/"too" added to the pizza-shorthand
  filler set (natural follow-up phrasing like "add pepperoni too"/"actually
  take the mushrooms off" was being wrongly blocked without them); "pie"
  recognized as pizza-word evidence alongside "pizza" itself ("a plain pie,
  medium").
- `evals/cases/adversarial.yaml`, `store_info_and_coupons.yaml`,
  `invalid_and_ambiguous.yaml`: `ADV-002`, `ADV-004`, `COUPON-002`,
  `INVALID-002` relabeled to the new correct behavior (see "Known, accepted
  tradeoff" above), each with an explanatory comment.
- `validate`: 78/78, unchanged (label growth only, no new cases this task).
- Rule-based ratchet: 46/78 (was 41/78 from T-039). +5 genuine flips
  (`AVAIL-002`, `CORRECT-001`, `CORRECT-002`, `CORRECT-005`, `INVALID-003`)
  from the more accurate intent gate; −4 from the 4 relabeled adversarial
  cases; +3 recovered (`MOD-014`, `CONFIRM-002`, `SLANG-002`) by the two
  filler/pie fixes above that the flip investigation surfaced. Full
  arithmetic and evidence: `tests/test_evals.py`'s own baseline comment.
- Pricing parity: 50/50, unchanged.
- No change to `orders.py`'s FSM or any pricing logic; `orders.py` gained
  no new code this task (only the T-039 `non_pizza_alias_hits` promotion,
  already in place before T-039A started).
