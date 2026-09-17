# Next tasks

The execution queue. Keep this to the next 5–10 executable tasks.

**T-039 is DONE (2026-09-17)** — closed a P0: `RuleBasedInterpreter` was
silently substituting a different, real, priced item for one it couldn't
resolve (a garden salad became a small cheese pizza; "a two liter coke"
became a can), found by a real 10-turn T-038 Phase 2 voice session. Fix: a
shared non-pizza-head-word veto in `interpreter.py`, plus promoting
`orders.py`'s real `NON_PIZZA_ALIASES` precedence logic
(`oe.non_pizza_alias_hits`) so `RuleBasedInterpreter` and `search_menu` share
one table instead of two that silently drift apart. Also fixed: an internal
line_id reaching the customer verbatim (`chat.py::_customer_safe_error_message`)
and an unusable recording crashing the whole voice-loop process
(`voice.py::turn()` now degrades gracefully). 16 new regression tests (one
per real transcript row, verified to fail pre-fix), 5 new
`evals/cases/non_pizza_items.yaml` cases — the corpus's first non-pizza item
orders. Full reasoning: `docs/decisions/ADR-017-no-silent-item-substitution.md`.
`validate` 78/78, rule-based ratchet 41/78 (real, observed growth — see
STATUS.md "T-039 done" for the honest per-case breakdown), pricing parity
50/50. **Live N=3 BLOCKED** — no `EXPLABS_API_KEY` in this environment;
owner action needed. Recommended next task: **T-038 Phase 2's remaining
item** below (real hardware voice loop) — explicitly paused for this task,
now unblocked.

**T-040 · `search_menu`'s single-hit reply always asks "What size would you
like?", even for a topping or non-pizza item hit** — **Priority:** 3 ·
**Status:** Not started. Found while verifying T-039: a `search_menu` hit
of kind `topping` or `item` (e.g. "garden salad small and grilled with
grilled chicken" resolving to a single `CHICKEN` topping candidate) still
gets `chat.py::_reply_for_search_menu`'s generic "Did you mean Chicken?
What size would you like?" — confusing for a hit that has no size at all.
Not a correctness/substitution defect (T-039 already proved no cart
mutation happens here) — a clarification-wording bug. Scope: make the
follow-up question depend on the hit's `kind` (gourmet/item needing size →
ask size; topping → ask which pizza/whether to add it; non-pizza item with
no size variants → just confirm). Low priority (UX polish, not order
correctness) but real and reproducible.

**T-037 is DONE (2026-09-16)** — persistence + customer identity, the
blocker STATUS.md has flagged High severity since early on. New
`lakewood/persistence/` package: `SessionRepository` interface with
`InMemorySessionRepository` (what the full suite/T-016 gate run against) and
`PostgresSessionRepository` (real Postgres, `psycopg2` lazy-imported).
Multi-tenant schema (`store_id` on every table, structurally — 13 new tests
prove cross-tenant reads are impossible), every F-series fail-safe (F5/F14/
F15/F16/F17) proven across a save/reload boundary including mid-confirmation,
recovery via revalidate-and-reprice (30-min resume window, offer-not-
silently-continue), phone->customer_id identity, and retention/deletion
paths. Full reasoning: `docs/decisions/ADR-014-persistence-postgres-
multitenant.md` (supersedes ADR-005). 472/472 offline tests pass with no
Postgres driver installed and no database server. No domain file touched.
Full detail: `docs/STATUS.md` "T-037 done". Recommended next task: **T-038**
below (voice loop) — this was T-037's own "NEXT TASK" recommendation.
Superseded by T-037's actual implementation: **T-005** ("Persistence design
spike," design-only, never implemented) — removed below, its job is done.

## T-038 Phase 1 · Persistent text call path — **DONE (2026-09-16)**

`PersistentChat` now wires the text path to T-037 persistence: server-bound
store resolution, explicit recovery offer/accept/decline, revalidation and
stale-quote invalidation, and one executor shared by rule-based and staged
LLM calls. Fake-LLM tests prove durable confirmation and restart-safe F6.

## T-038 Phase 2 · Local voice loop

**Priority:** 1 · **Status:** In progress · **Phase:** 6 (voice layer, per
CLAUDE.md's MVP build order — comes after the eval gate, which is done, and
before persistence's remaining layers: customer memory/reorder fast path
depend on this existing first)

T-037 built and tested the persistence machinery
(`lakewood/persistence/service.py::resume_or_create`/`confirm_and_persist`/
`save_progress`) but explicitly did not wire it into any live call path —
`chat.py`'s text sandbox still uses `new_session()` (always fresh, never
persisted) and no telephony/voice loop exists yet. This is the first task
that actually needs the STT/TTS/telephony provider decision ADR-004 deferred
to this phase.

**2026-09-17 provider increment:** Parakeet is selected for the local pilot
after a same-file hardware benchmark: 0.082 s warm median for 2.586 s audio
(31.5x realtime, 2.60 GB GPU peak) versus faster-whisper `small` CPU at
2.748 s (0.9x realtime, 0.95 GB RSS). ADR-016 records the decision. A warm,
localhost-only WSL server (`scripts/parakeet_server.py`) and Windows-side
`ParakeetProvider` are implemented; faster-whisper remains a fallback. Offline
provider/error-path tests and the full repository suite are green. **Still
required before Phase 2 is DONE:** run the real microphone -> Parakeet ->
PersistentChat -> Windows SAPI loop on the owner's hardware, capture at least
10 human turns (including negation, quantities, corrections and half scope),
and report per-stage median/p95 plus transcript/order failures.

**Scope:** pick/confirm the voice provider (ADR-004a, if not already
resolved by the time this starts), build the call-handling loop that: (1)
resolves `store_id` from the inbound DID (`config.py::store_for_did`,
unchanged), (2) calls `resume_or_create` to check for a resumable dropped
call and offer it back if found, (3) calls `save_progress` after every tool
mutation so a mid-call drop is actually recoverable, (4) calls
`confirm_and_persist` instead of `orders.confirm_order` directly so F6
survives a restart. **Do not re-implement any of T-037's logic** — this task
is integration, not a second persistence implementation.

**Acceptance:** a real (or realistically simulated) dropped-call-then-
callback scenario recovers the cart and re-quotes it correctly; eval suite
re-run if any prompt/tool-description/provider surface changed; STATUS.md/
ARCHITECTURE.md updated to move "wired into a live call loop" from PLANNED to
CURRENT.

---

**T-031 is DONE (2026-09-16)** — full N=3 live trace sweep (T-030's capture,
no custom script), all 73 cases, all 3 runs clean (0 provider failures, 0
schema violations). 25 distinct cases failed in >=1 run; clustered into
~7-8 root causes, not 25 independent problems. Top cause (`search_menu`'s
"cheese"-substring alias collision) explains 9 cases alone. Three-way split
of the 25: 10 system defects, 8 model capability limits, 3 label defects
(one already filed, T-029), 4 inconclusive (N=3 too small). Two new causes
filed below (T-032 through T-036). Phase 2 before-picture recorded: the
model already attempts every named item nearly always — T-023's structural
guard (F18) already guarantees the batched ask regardless of model
behavior — so Phase 2's own expected impact is narrower than the aggregate
number alone would suggest. Full ranked clusters, evidence, and the
three-way split: `docs/STATUS.md` "T-031 done".

**T-032 is DONE (2026-09-16)** — fixed `search_menu`'s "cheese"-substring
alias collision: word-boundary matching (fixes `"cheesecake"`) plus a
query-shape precedence rule (bare `"cheese"` alias suppressed specifically
when `"pizza"` is also mentioned, since together they name the base item,
not a topping) plus a sibling precedence fix for `NON_PIZZA_ALIASES`
(a specific drink size beats the generic "soda"/"coke" catch-all). Verified
directly: **zero collision recurrence across 286 live `search_menu` calls**
in a clean N=3 live re-run (53/73, 54/73, 56/73, mean 54.33 — flat vs.
T-031's 53.67, doesn't clear the noise floor). Of the 9 named cases: 2
clean flips (`INVALID-002`, `MOD-030`), 1 mostly-flipped (`ADV-002`, 2/3), 1
inconclusive (`NEG-007`, 1/3), and **5 confirmed fixed at the mechanism
level but still failing for an independent second cause** (`AVAIL-001` — a
genuine separate `clams` ambiguity; `COUPON-001`/`CORRECT-007` — `T-028`'s
coupon-by-description gap; `MULTI-004`/`MULTI-006` — `T-033`'s proactive-quote
pattern, plus a new narrow entity-extraction observation on `MULTI-004`
noted but not filed separately). NO_MATCH rate moved 23.6%→27.6%, checked
directly and traced to unrelated new query attempts that round (coupon-code
guessing, `"plain cheesecake"`), not a recall regression. 16 new tests
(`tests/test_search_menu_alias_collision.py`), full suite green, `validate`
73/73, rule-based 36/73, parity 50/50 all unchanged. Full detail:
`docs/STATUS.md` "T-032 done".

---

## T-033 · Model proactively calls `request_quote()` when nobody asked

**Priority:** 1 · **Status:** Not started

**Found in T-022 (`CONFIRM-002`, 1/3, inconclusive) and now reproduced 4
more times in T-031** with real trace evidence: `MULTI-004` (2/3,
`"one cheesecake and two strawberry cheesecakes"` → unprompted
`request_quote()`), `NEG-008` (1/3, `"no, wait, that's fine"` → unprompted
`request_quote()`), `CORRECT-007` and `COUPON-001` (1/3 each, right after
applying/discussing a coupon). Tips state to `QUOTED` when the customer
never asked for a total, failing labels that expect `BUILDING`.

**Scope.** Diagnosis first (same discipline as T-022/T-027) — is this
consistent enough across a dedicated N=3-per-case re-run to call it
systematic, or still genuinely mixed? If systematic, this is prompt-shape
territory (a system-prompt clarification that `request_quote` is only for
an explicit ask), not a domain-layer fix.

**Acceptance.** A classification with real evidence, and a decision on
whether it's worth a Phase-2-style prompt addition or stays documented as a
known limitation.

---

## T-034 · "Extra X" topping intensity has no stable mapping — sometimes DOUBLE (over), sometimes NORMAL (under)

**Priority:** 2 · **Status:** Not started

**Found in T-031.** `MOD-036`, `MOD-014` (3/3 each): `"extra cheese"` maps
to `intensity=DOUBLE`, overcharging against the label. `GOURMET-010` (2/3):
`"extra pepperoni"` maps to plain `NORMAL`, undercharging. No consistent
rule; both directions observed on real traces.

**Scope.** Determine what "extra X" should actually price as per the real
menu/PrISM rules (is there a real intermediate tier, or is one of these
labels wrong?) before touching the tool description or prompt. Domain
question first, code second.

**Acceptance.** A documented, verified answer for what "extra X" should
mean, plus whichever side (labels or model guidance) needs to change.

---

## T-035 · Downgrading an existing topping to LITE doesn't remove the old entry first

**Priority:** 2 · **Status:** Not started

**Found in T-031, `NEG-005`, 3/3 identical.** `"actually make the pepperoni
light"` on a pizza that already has `PEPPERONI` at `NORMAL` intensity calls
`add_modifier(PEPPERONI, LITE)` again instead of `remove_modifier` first —
ends with two topping entries and an unchanged (wrong) total. Note:
`RuleBasedInterpreter` already has the correct logic for this exact shape
(`_resolve_intensity_calls`'s LITE-downgrade-of-existing branch, from
T-015) — the live model has no equivalent guidance.

**Scope.** Likely a small, targeted system-prompt or tool-description
clarification once diagnosed further — confirm the mechanism holds at
N=3-per-case before deciding the fix shape.

**Acceptance.** Regression test reproducing the exact mechanism; N=3 proof
the fix (whatever form it takes) actually changes it.

---

## T-036 · `get_store_info`'s strict field-matching turns a naming near-miss into an unwarranted human transfer

**Priority:** 2 · **Status:** Not started

**Found in T-031, `FAQ-001`, 3/3 identical.** Model asks for fields
`['hours', 'delivery_minimum', 'delivery_radius', 'delivery_available']`;
real keys are `delivery_radius_miles` (not `delivery_radius`) and there is
no `delivery_available` key at all. `get_store_info`'s `missing = [f for f
in fields if f not in data]` fails the *entire* call on any single
mismatch, triggering `INFO_NOT_AVAILABLE` and a transfer for an ordinary
hours/delivery question — a real customer asking simple store-info
questions would get unnecessarily transferred.

**Scope.** Either return the fields it *does* have instead of failing
wholesale on a partial miss, or normalize/alias the field names the tool
accepts. Small, contained, `get_store_info`-only change.

**Acceptance.** `FAQ-001`-shaped questions no longer transfer; regression
test for the exact observed field-name mismatch.

---

**T-030 is DONE (2026-09-16)** — supersedes T-025's GUI framing: the real
need was trace capture first, rendering second. `evals/runner.py::score()`
now emits a full structured JSONL trace by default under `evals/traces/`
(every tool call/result, a session-state snapshot after every turn, real
tokens/latency/cost) — provably identical scores with capture on or off
(`tests/test_trace_capture.py`), zero dependency added to the T-016 gate.
`evals/catalog.py` generates a derived, always-fresh case index
(`evals/catalog.json`) — one-sentence description per case, never hand-
written. `scripts/viewer/server.py` (stdlib `http.server` only, localhost-
only, no framework) + one static page render Catalog/Replay/Live through
the exact same `lakewood/chat.py::run_turn_traced` function proven shared
across both call paths (`tests/test_viewer_shared_path.py`). Manually
verified end to end in a real browser (Catalog, Replay of a passing case,
Live session against `rule_based`) plus one real replayed failing case
(`MULTI-002`) showing its actual root cause at a glance. `validate` 73/73,
rule-based 36/73, parity 50/50 all unchanged. See `docs/STATUS.md` "T-030
done".

## T-028 · `apply_coupon()` can silently pick a coupon the customer didn't ask for

**Priority:** 1 · **Status:** Not started

**Found while diagnosing T-027's `COUPON-001` live failure.** The customer
said "I have the three dollars off thirty coupon" (describing `OFF_3_AT_30`
by its terms, not its code). `search_menu` doesn't know about coupons at
all (they aren't a topping/item/gourmet), so nothing resolves the
description to a code. The live model, unable to resolve it, called
`apply_coupon()` with no `code` at all — letting the domain engine's own
default "any eligible coupon" selection pick `FREE_2L` ("buy 2 large
pizzas, get a 2-liter free") instead, in one observed rep with **zero large
pizzas in the cart at the time**. A real order-correctness risk: a
different discount than the one requested could reach a real ticket.

**Scope.** Decide whether `apply_coupon(code=None)`'s default-selection
behavior is intentional product behavior needing a customer-facing
description-to-code lookup (a `search`-shaped fix, likely on
`search_menu` or a sibling), or whether it should refuse/ask instead of
silently picking something when no code is given and more than one coupon
is eligible. Diagnose with real traces before deciding, same discipline as
T-022/T-027 — don't assume the mechanism from this one observation alone.

**Acceptance.** A decision, documented (ADR if it changes coupon-resolution
behavior), plus a regression test reproducing the exact observed mechanism.

---

## T-029 · `DECLINE-001`'s label is invalid — needs correction

**Priority:** 2 · **Status:** Not started

**Found while diagnosing T-027.** `DECLINE-001` (`evals/cases/
invalid_and_ambiguous.yaml`) authors turn 2's calls as `decline_item` +
`request_quote` + `begin_confirmation`, but the turn's own utterance
("actually forget the wings, just the pizza") never asks for a total or
confirmation. The system prompt correctly forbids `begin_confirmation`
without explicit affirmative confirmation — no well-behaved model can pass
this case as authored, confirmed directly: even a rep where the live model
called `decline_item` exactly right still failed on this account.

**Scope.** Split into a clean decline-only turn (assert the disambiguation
clears and the cart is correct, `state: BUILDING`) and, if confirmation
coverage is still wanted, a separate turn/case with an actual explicit
"yes, place it"-shaped utterance. Corpus edit only — no source changes.

**Acceptance.** `validate` still green; the corrected case's `assert_final`
is reachable by a model that behaves exactly as `CLAUDE.md`'s own
confirmation rule requires.

---

**T-024 is DONE (2026-09-15)** — the corpus couldn't credit correct
clarify-then-resolve behavior (a single-turn case has no way to answer the
question T-023's guard raises), inverting `CLAUDE.md`'s own "prefer
clarification over guessing" rule. Audited all 71 original cases: exactly 3
(~4%) had genuine single-turn ambiguity (`MULTI-002`, `COUPON-001`,
`CORRECT-007`, all "wings, no count given"), each given an unrelabeled
follow-up turn. Added `DECLINE-001`/`DISAMBIG-CAP-001` for `decline_item`
and cap-then-transfer coverage. `validate` 73/73, rule-based 36/73 (same
absolute count, ratchet unchanged), live N=3: 55/73, 53/73, 56/73 (mean
54.67) — `MULTI-002` flipped 0/3→3/3, a measurement-honesty fix, not a
model-capability claim. `COUPON-001`/`CORRECT-007`/`DECLINE-001` still fail
consistently for reasons not fully traced — see below. Full detail:
`docs/STATUS.md` "T-024 done", standing rule added to `docs/EVALS.md`.

**T-026 is DONE (2026-09-15)** — P0 fix, found while starting T-024: F15
cleared `unresolved_lookups` globally on ANY successful search/mutation
anywhere in the order, not just a resolution of the specific missed
request — silently reopening the exact T-018 dropped-request defect via any
ordinary multi-item order. Fixed: clears only via a textually-related later
success or explicit `decline_item`. See `docs/STATUS.md` "T-026" and
`docs/decisions/ADR-013-f15-scoped-clearing.md`.

---

**T-027 is DONE (2026-09-16)** — diagnosed all three T-024 live failures with
real per-call/per-turn traces (3 cases × 3 reps, live). **Found fewer root
causes than symptoms, as the task itself warned might happen:**
`COUPON-001` and `CORRECT-007` share the exact same root cause — both trip
the identical `search_menu` alias collision already diagnosed as T-022's
`ADV-002` finding ("party size cheese pizza" spuriously matches MOZZARELLA),
which T-024's audit failed to anticipate since it only checked the original
label's calls, never a live model's actual search queries. `CORRECT-007`'s
transfer is `F18`'s cap firing *correctly* on that same incidental,
unrelated ambiguity — not a defect in `MAX_DISAMBIGUATION_ASKS`, confirmed
not too tight. `DECLINE-001` fails for an unrelated reason entirely: **the
case's own label is a corpus defect** — turn 2 bundles `begin_confirmation`
onto an utterance that never asks for confirmation, which the system prompt
correctly forbids without explicit affirmative confirmation. No well-behaved
model could pass it as authored. Real signal: `decline_item` was called
correctly 1/3 unprompted reps (no system-prompt mention exists) — the
"customer can only exit via the cap" hypothesis is refuted, though
reliability is still an open, inconclusive question (2/3 reps: zero tool
call at all, not a wrong one). **Phase 2 scoping decision: one rule, not
two** — ship the already-scoped "resolve/ask about every named item" rule;
defer a second explicit-abandonment rule until a corrected `DECLINE-001`
produces a valid measurement. Two new findings filed below (T-028, T-029).
Full detail: `docs/STATUS.md` "T-027 done".

**T-016 is DONE (2026-09-08)** — see `docs/STATUS.md` "T-016 milestone" and
`docs/decisions/ADR-009-eval-gate-runs-an-interpreter.md` for the mechanism
decision, implementation, and the Part 3 proof (real numbers: gate passes at
35/71 today, correctly fails at 25/71 when the T-015 bug is monkeypatched
back in).

**T-018 Parts 3 & 4 are DONE (2026-09-15)** — first live baseline against a
real provider (Experiential/Luna): 45/71 (63%), full category/cost/latency
breakdown in `docs/STATUS.md` "T-018 milestone". Found the two findings T-019
below files.

**T-019 is DONE (2026-09-15)** — three deterministic defenses against the
confirmation-gate bypass and silent request drop found in T-018, plus the
stale-turn readback fix. See `docs/STATUS.md` "T-019 milestone",
`docs/decisions/ADR-011-confirmation-turn-gate.md`, and
`tests/test_confirmation_gate.py`. Filed two follow-ups below (T-020,
T-021) that this task deliberately did not fix.

**T-020 is DONE (2026-09-15)** — diagnosed 18 real `search_menu` failures
from actual checkpoint call logs before writing any fix (10 class-A "base
pizza has no searchable name," 10 class-B "reasonable phrasing, matcher too
strict/no alias table"), fixed both cheaply (filler-word stripping, number
normalization, an `ALIASES`/new `NON_PIZZA_ALIASES` lookup, a narrow
`CHEESE PIZZA` pseudo-hit) with **no embeddings** — `nomic-embed-text` was
never needed once the diagnosis was done first. Also ran the first real 3x
sequential variance measurement (42–48/71 band, 21% of cases flip run to
run on unchanged code) and found T-018/T-019's single-run deltas were
mostly noise. Post-fix live score: 51/71, 3 points above the band's max — a
real improvement. `search_menu` NO_MATCH rate: ~60–64% → 26% of calls. Full
detail: `docs/STATUS.md` "T-020 milestone", `docs/EVALS.md` "search_menu
recall diagnosis and fix" / "Non-determinism". No ADR — matching strategy
changed in degree, not architecture.

**T-022 is DONE (2026-09-15)** — diagnosed the "search succeeds, model
doesn't act" question with 19 live case-runs (5 cases × 3 reps + follow-up
reply-text capture), not assumed. Two of the five original candidates
(`CORRECT-003`, `NEG-006`) didn't reproduce at all in isolation — ordinary
run-to-run variance, not a context-dependent bug (score() gives every case a
fresh session regardless). Of the two that did reproduce: `ADV-002` turned
out to be `chat.py`'s own designed clarification reply firing correctly on
a spurious `search_menu` alias collision (search-precision gap, filed as
its own separate item below), not model abandonment. `MULTI-002` reproduced
5/5 across two independent runs — a real, consistent model/prompt-
sequencing gap: given ≥2 ambiguous items in one utterance, the model
resolves one and silently drops the rest. Also found the
`_HALLUCINATION_CODES` metric is counting honest `NO_MATCH` search misses,
not invented item names — a measurement-naming defect, not a new safety
finding. Full diagnosis: `docs/STATUS.md` "T-022 diagnosis". `MULTI-002`'s
finding is now fixed structurally by **T-023 Phase 1** below.

**Filed, not yet scheduled, from T-022:** `search_menu`'s `ADV-002`-shaped
alias collision (a compound "topping + item" query spuriously surfaces an
unrelated generic alias — e.g. MOZZARELLA on the word "cheese" — instead of
resolving the clearly-named item); the two low-frequency residual gaps
(word-form quantities, a gourmet number embedded in a longer compound
sentence); the `_HALLUCINATION_CODES` metric rename/reclassification.

---

## T-023 · Turn-completion guard for outstanding disambiguations

**Priority:** 1 · **Phase 1 (structural guard) is DONE (2026-09-15)** ·
**Phase 2 (prompt rule) is NOT STARTED**

**Phase 1 done.** `Session.pending_disambiguations` (F17) + `chat.py`'s
turn-completion guard (F18) — a turn can no longer end silently while a
`search_menu` hit the model flagged `needs_disambiguation` remains
unresolved; batched into one clarification, cleared by a matching
resolution or the new `decline_item` tool, capped at
`MAX_DISAMBIGUATION_ASKS = 2` before transfer. Full design:
`docs/decisions/ADR-012-turn-completion-guard.md`. 17 new offline tests
(`tests/test_disambiguation_guard.py`), all offline gates unchanged
(`validate` 71/71, rule-based 36/71, parity 50/50). Live N=3
regression-detection: 55/71, 50/71, 50/71 (mean 51.67) — flat-to-
slightly-above the post-T-020 49–53 range, no regression. Full report:
`docs/STATUS.md` "T-023 Phase 1" (current "Current phase" entry).

**Phase 2 — not started.** Add a system-prompt rule (separate change from
Phase 1, per `CLAUDE.md`'s "prompt for quality, structure for correctness"
principle and this task's own explicit instruction not to bundle the two):
when an utterance names more than one item, resolve or explicitly ask about
every named item before ending the turn. A UX optimization only — one turn
instead of two on the happy path — never the correctness guarantee, since
Phase 1 already holds that regardless of what the prompt says.

**Scope for Phase 2.** System-prompt text only
(`lakewood/interpreter.py::_SYSTEM_PROMPT`); no changes to Phase 1's
structural code, `search_menu`, the corpus, or the scorer. Its own separate
N=3 sequential live measurement, compared against Phase 1's own numbers
(55/50/50, mean 51.67), not against pre-T-023. If it shows no measurable
effect above the noise band, that is a legitimate, reportable outcome — the
guard still holds either way.

**Acceptance.** N=3 sequential live run post-change; report against Phase
1's numbers specifically; note whether `MULTI-002` itself now passes within
the eval harness's single-turn structure (it structurally cannot with
Phase 1 alone, per the corpus limitation noted in `docs/STATUS.md`).

---

**T-021 is DONE (2026-09-15) — no code/content fix needed; root cause was a
stale local `.venv`, not `requirements.txt`.** Found while running T-018's
live baseline: the repo's pre-existing `.venv` (created 2026-09-06) predated
`pyyaml`/`pytest` being added to `requirements.txt` and had never had
`pip install -r requirements.txt` re-run against it, so `tests/test_evals.py`
and `tests/test_eval_runtime_binding.py` failed to collect
(`ModuleNotFoundError: No module named 'yaml'`) in THAT venv specifically.
Verified with a genuinely fresh venv (`python -m venv` + `pip install -r
requirements.txt`, zero manual extras): `pyyaml 6.0.3` and `pytest 9.1.1`
install cleanly, and `python -m pytest -q` (365 passed, 2 xfailed) plus
`python evals/runner.py validate` (71/71) both run clean with no network, no
Ollama. `requirements.txt` needed no change. The stale repo `.venv` was
fixed in the T-018 session by installing the missing packages into it
directly; this task just confirmed there was never a repo-level defect to
fix, and documents the corrected diagnosis so it isn't repeated.

**Acceptance.** A fresh `python -m venv` + `pip install -r requirements.txt`
+ `scripts/check.sh` succeeds with no manual intervention.

---

## T-017 · `RuleBasedInterpreter` can't parse a bare "number N" as a single gourmet selection

**Priority:** 0 · **Status:** Not started

**Found 2026-09-08** while testing T-015's negation fix against
`GOURMET-005`'s own utterance ("gimme a medium number ten, no pineapple").
Unrelated to negation — the negation half now resolves correctly, but
"number ten" alone is never recognized as `gourmet_number=10`:
`RuleBasedInterpreter` only extracts a gourmet number in the "half #A half
#B" pattern (`_HH_BY_NUMBER_RE`) or while resolving a pending
disambiguation. A bare "number ten" (no "half", no prior `search_menu`
clarification) falls through to a plain `CHEESE PIZZA`, silently ordering
the wrong item — a medium cheese ($13.00) instead of a medium #10 Hawaiian
($19.00).

**Scope.** Add a single-gourmet-number pattern (e.g. `\bnumber\s*#?\s*(\d{1,2})\b`
or `\b#\s*(\d{1,2})\b`) to `_new_pizza`'s / `interpret()`'s rule ordering,
validated the same way `add_item(gourmet_number=...)` already validates
range via `GOURMET_NOT_FOUND` — never guess a name for a number that isn't
on the menu for that size.

**Tests required.** "medium number ten" → `gourmet_number=10`, MD gourmet
price · "medium number ten, no pineapple" → same, with the exclusion
correctly free · an out-of-range number → clarification/refusal, not a
guessed item.

**Acceptance.** `GOURMET-005`'s utterance produces the fully correct label
end to end (item **and** negation) under `score --adapter rule_based`.

---

## T-013d · Rerun the Ollama full-corpus benchmark against the current (71-case) corpus

**Priority:** 1 · **Status:** Stale result exists, needs rerunning

`python evals/runner.py score --adapter llm` (`LAKEWOOD_LLM_PROVIDER=ollama`,
`LAKEWOOD_LLM_MODEL=llama3.1:8b`) finished mid-session 2026-09-08: **real
result 11/63.** That run predates T-015's negation fix and its 8 new
`evals/cases/negation_and_removal.yaml` cases — the corpus and the
interpreter it's measuring have both changed since, so 11/63 is stale as a
current number (still real, still worth keeping as a historical baseline —
see `docs/STATUS.md`/`docs/EVALS.md`). `OllamaProvider` and 26 offline tests
are done and passing; two real manual smoke-test turns also ran live and
behaved exactly as designed (a schema violation from each model, both
caught before domain state — see `docs/STATUS.md` "Local Model Tier
milestone").

**To do:** rerun the same command against the current 71-case corpus;
consider a smaller `--cases` subset first for faster iteration (~60-90s
observed per turn on this hardware), or `LAKEWOOD_LLM_MODEL=gemma4:26b` for
the pinned-default comparison once `llama3.1:8b`'s current-corpus run is in.
Report per the same category breakdown T-013c asks for on the paid-provider
side: item-selection, modifier-scope, correction, ambiguity, hallucinated
SKUs, premature confirmation, stale-quote violations, invalid tool calls,
total calls, token usage (Ollama reports `prompt_eval_count`/`eval_count`),
latency (median/p95 — Ollama has none of the cost concerns a paid API run
would). Update `docs/STATUS.md`/`docs/EVALS.md` with the fresh numbers.

**Do not** re-run this by hard-coding fixes for individual failed phrasings
into the prompt/schema just to raise the number — the task that created
this line was explicit: generalizable improvements are fine if reported
with a full rerun showing improvements AND regressions against this
baseline; anything else is target-gaming.

---

## T-013c · Complete live Astra baseline — BLOCKED on application API access

**Priority:** 2. Provider code verified on 2026-09-08: 30 new fake-HTTP tests,
15 unchanged LLM tests; 261 passed / 2 xfailed overall. OPENAI_API_KEY absent.
This supersedes T-013b's Anthropic-only live plan. No real AI baseline exists.

Configure `LAKEWOOD_INTERPRETER=llm`, `LAKEWOOD_LLM_PROVIDER=openai`,
`LAKEWOOD_LLM_MODEL=gpt-6-astra`, and a funded application `OPENAI_API_KEY`.
Never use Codex session credentials or print/commit the key.

1. Send ONE cheap real provider request for "Large pepperoni." using the
   repository adapter and restricted schemas. Verify structured tool behavior.
   Stop on connectivity/resource failure; record the exact sanitized error.
2. Only after success, run `python evals/runner.py score --adapter llm` over
   all 63 unchanged cases. Capture traces for the full metric breakdown below.
3. Run `python -m lakewood.chat --debug` with the actual provider: large
   pepperoni; mushroom on one half; replace mushroom with sausage; total;
   Coke after quote; new total; "Yes, place it." Then test "Give me chicken.",
   "Give me a truffle lobster pizza.", and half #8 / half #10 in fresh sessions
   where necessary (confirmed orders are immutable).

**Report:** passed/63; final-cart exact match against available labels;
item-selection, modifier-scope, correction and ambiguity failures; hallucinated
SKUs; premature confirmation; stale-quote violations; invalid tool calls;
provider failures/timeouts; available token usage/cost; median/p95 latency.
The score CLI currently checks labeled final state, not all these metrics;
collect tool/state traces and analyze the missing metrics without editing cases
or treating absent measurements as zero. Latency and tokens are available per
provider response. No cost estimate without a verified applicable rate.

**Acceptance:** honest first AI baseline (not production accuracy), actual
manual trace reaching CONFIRMED, and updated STATUS/EVALS evidence.

---

## T-003 · Verify coupon tax ordering at the register — **owner action**

**Priority:** 3 · **Status:** Blocked on a store visit

The one open item keeping the pricing-parity milestone from being fully
DONE rather than PARTIALLY VERIFIED. Three orders in
`docs/COUPON-VERIFICATION.md`. Test 1 decides it: tax reads $2.28 → current
model is right; $2.50 → `pricing.taxable_base` changes and both
`tests/test_coupons.py` and the coupon category of
`tests/test_pricing_parity.py` need new expected values.

Also ask: are coupons honored by phone at all? If not, `COUPONS = []`.

---

## T-001 · Tool API layer (Phase 2)

**Priority:** 4 · **Status:** Not started

**Purpose.** Expose `orders.TOOLS` over HTTP so a voice provider can call them.
Every Phase 5 provider option needs this, and it needs no vendor decision.

**Dependencies.** Pricing/menu domain engine (met, including T-008's
`add_item(second_gourmet_number=...)` — generate the route off the full
current signature, not a stale copy of it).

**Scope — exactly this, nothing more.**
- `lakewood/api.py`: FastAPI app with one route per entry in `orders.TOOLS`,
  **generated by introspecting the list** so routes and tools cannot drift.
- Session resolution: `call_id` → in-memory registry for now (persistence is
  T-005/Phase 3, not this task).
- `store_id` derived from the inbound DID in the call payload. Reject any
  request body containing `store_id` or a price field.
- Shared-secret auth via `Authorization` header, secret from env.
- `GET /healthz` returning version and test-suite-verified menu hash.
- Emit `schemas/tools.json` for provider registration.

**Out of scope.** Prompts, provider SDKs, persistence, audio, dashboards,
business logic of any kind in the route layer.

**Files.** `lakewood/api.py` (new) · `lakewood/config.py` · `requirements.txt`
· `tests/test_api.py` (new) · `schemas/tools.json` (new)

**Tests required.**
- Every tool in `orders.TOOLS` has a reachable route (generated, so assert parity)
- Unauthenticated request → 401
- Body containing `store_id` → 400
- Body containing `price`/`total`/`amount` → 400
- Full order completed via HTTP only, ending in a confirmed order
- `/healthz` → 200

**Acceptance.** A complete pickup order — add, modify, quote, confirm — runs
end to end over HTTP with correct totals, no Python imports by the caller.

---

## T-002 · Tests for `search_menu` and `check_availability`

**Priority:** 5 · **Status:** Not started

Both are model-facing and untested. `search_menu` is the disambiguation path
for this menu's collisions — "chicken" (6 gourmet pizzas + the CHICKEN
topping + CHICKEN DINNER) is a real one today; see `evals/cases/
invalid_and_ambiguous.yaml::COLLIDE-002` for a worked example.

**Tests.** Collision queries return `needs_disambiguation: true` · numeric query
("number 10") resolves · unknown query returns `NO_MATCH` and never a guess ·
86'd item reports unavailable.

**Files.** `tests/test_orders.py`.

**Acceptance.** Both functions covered; no behavior change.

---

## T-005 · Persistence design spike — **DONE, superseded by T-037's actual implementation**

T-037 (2026-09-16) implemented persistence directly — Postgres, multi-tenant
schema, repository interface, recovery, retention, customer identity — per
`docs/decisions/ADR-014-persistence-postgres-multitenant.md`, which
supersedes ADR-005 (the SQLite proposal this spike would have detailed).
Nothing left for a separate design-only spike to produce.

---

## T-007 · Make `scripts/check.sh` portable to Windows

**Priority:** 7 · **Status:** Not started (partially done)

Found during a prior session on a Windows dev machine, tracked as two items:
- ~~`lakewood/orders.py:602` — `time.strftime("%-I:%M %p")` is a glibc-only
  format code and raises `ValueError: Invalid format string` on Windows'
  CRT.~~ **DONE** — fixed at all three call sites via the shared
  `lakewood/timefmt.format_12h()` helper. Covered by `tests/test_timefmt.py`.
- `scripts/check.sh` calls `python3`, which resolves to a non-functional
  Windows Store alias on at least one dev machine. Still open — detect
  `python` vs `python3` in the script (e.g. `command -v python3 || alias
  python3=python`, or a small shim).

**Acceptance.** `./scripts/check.sh` runs clean on both Windows and the
original Linux/Mac dev environment.

---

## T-009 · Digitize the restaurant's pasta menu, if one exists

**Priority:** 8 · **Status:** Not started — needs the owner/menu to confirm scope first

**Found by:** writing T-006's eval corpus. `docs/EVALS.md` documents "Shrimp
Scampi is both a $23 pizza and a $20 pasta dish" as a collision trap, but
`data/menu.json` has no pasta section at all — `search_menu("shrimp scampi")`
returns exactly one hit today. Either the pasta menu was never digitized, or
it doesn't exist and the EVALS.md note is aspirational/stale.

**First step is a question, not code.** Ask the owner: does this location
sell pasta dishes (Shrimp Scampi, or others)? If yes, get PrISM
screenshots/receipts the same way the pizza menu was verified — no price
goes into `data/menu.json` without one. If no, correct `docs/EVALS.md`'s
collision-trap note instead (it's describing a menu that doesn't exist here).

**Acceptance.** Either `data/menu.json` gains a verified pasta section with
its own parity tests, or `docs/EVALS.md` is corrected — not both left
contradicting each other.

---

## T-010 · Wire `scripts/check.sh` into CI

**Priority:** 9 · **Status:** Not started

GitHub Actions on push and PR. Fail on red. No coverage gate — see
`docs/TESTING_STRATEGY.md` on why coverage is not the metric here.
