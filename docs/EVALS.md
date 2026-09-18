# Evaluation strategy

Supersedes the earlier `docs/eval-harness-spec.md` (deleted).

The accuracy number is the product's only defensible claim. It has to be
measured, gated, and store-specific — a competitor can copy a prompt in an
afternoon and cannot copy 250 labeled failure modes from this menu.

## Current runtime provider baseline status — 2026-09-08

`score --adapter llm` uses `LAKEWOOD_LLM_PROVIDER` (`anthropic` default,
`openai` or `ollama` explicitly). Paid-provider status (Anthropic, OpenAI):
**no live AI baseline exists** — Anthropic is blocked on a funded API
credential (T-013 milestone, `docs/STATUS.md`), OpenAI on `OPENAI_API_KEY`
being absent (T-013c). All requested live metrics for those two are **not
measured, not zero**. The 63 golden cases were not modified for either;
validation is 63/63.

**Local (Ollama) has a live baseline — see "Local model tier baselines"
below.** It unblocks dev/eval iteration; it does not resolve the paid-
provider question, which stays exactly as blocked as before.

The score adapter propagates infrastructure failures, but the current CLI
only reports labeled final-state matches. It does not yet compute all
safety/category metrics described below; live baseline reporting must use
actual tool/state traces for those, and must not interpret label validation
metrics as model scores. Earlier Anthropic-only descriptions below record
the previous T-013 attempt. Current execution steps are in NEXT_TASKS.md,
T-013c (paid provider) and T-013d (local tier's remaining full-corpus run).

## Local model tier baselines (T-013, Ollama) — 2026-09-08

Full reasoning and pitfall handling: `docs/decisions/ADR-007-local-model-tier.md`.

**Manual smoke test (real, live, N=1 utterance per model — not the full
corpus)**, "Large pepperoni." through `python -m lakewood.chat --debug`:

| Model | Result | Category |
|---|---|---|
| `llama3.1:8b` | `add_item(item="pepperoni pizza", size="large", quantity="1")` → `BAD_ARGS` (quantity sent as string, not int; item not a real name) | schema-violation, caught |
| `gemma4:26b` | Called `search_menu("pepperoni")` correctly, got an unambiguous hit, then asked "what size?" again — ignoring "Large" already given | tool-sequencing / context-loss |

Neither model completed the simplest required flow without an error. Full
transcripts in `docs/STATUS.md` "Local Model Tier milestone."

**63-case `score --adapter llm` (`LAKEWOOD_LLM_PROVIDER=ollama`,
`llama3.1:8b`): finished mid-session, real result 11/63.** This ran against
the original 63-case corpus, before T-015's negation fix and its 8 new
cases — stale as a current number, not re-run against the now-71-case
corpus in T-015 (out of that task's scope). See `docs/NEXT_TASKS.md` T-013d
for the up-to-date rerun.

**Promotion criteria for the local tier (A5) — defined, not yet met:**
1. Zero schema violations reaching domain state across the full eval run.
2. Zero cart-state corruption; every malformed tool call caught and handled.
3. Simple single-item and single-modifier cases at high accuracy.
4. Provider swap verified: same eval suite runs against fake, Ollama, OpenAI,
   and Anthropic adapters with no domain code changes — **met**, the same
   `LLMInterpreter`/`run_turn`/`_valid_tool_args` ran unmodified against all
   three real providers, only config changed.

Criteria 1–3 are **not yet verified at corpus scale** — only N=1 live per
model exists, and it showed a schema-violation attempt (correctly caught,
so criterion 1 held for that one case) and a tool-sequencing failure on the
simplest possible flow (criterion 3 not met). A local ~8B–26B model is
*expected* to fall well short on hard cases per the task that created this
section — record the gap honestly, don't soften the 98–99% target, and
don't treat "passes on Ollama" as production readiness.

**STT domain-weighted accuracy** (`python -m lakewood.stt.eval`,
`faster_whisper` `small`, 10 SAPI-synthesized fixtures — see
`docs/decisions/ADR-008-local-stt-runtime.md` for the honest caveat that
these are synthesized, not human-recorded): sizes 100%, toppings 100%,
negations 100%, scope 100%, **quantities 20%** (Whisper normalizes spoken
numbers to digits; the manifest expected spelled-out forms — a real,
actionable STT→NLU interface finding, not a scoring bug).

**Unrelated but serious finding from this testing:** feeding a
word-perfect transcript ("Large pepperoni, no onions.") through
`RuleBasedInterpreter` revealed it had no negation handling — "no onions"
silently added a charged ONIONS topping. Not fixed in that task (out of its
provider-adapter scope) but not hidden either — **fixed in T-015**, next
section.

## Negation/removal/lite handling baselines (T-015) — 2026-09-08

Full diagnosis and fix detail: `docs/STATUS.md` "T-015 milestone".

**Corpus:** 63 → 71 cases (`evals/cases/negation_and_removal.yaml`, 8 new:
`NEG-001`–`NEG-008`). `validate`: 71/71. `score --adapter rule_based`:
**35/71** (was 24/63 before this task touched anything; 27/71 with the
interpreter fix alone before the new cases were added; all 8 new cases pass,
zero regressions on the pre-existing 63).

**The finding that matters most isn't the bug — it's that `validate`
couldn't have caught it regardless of how it was fixed.** See "`validate`
mode" above. `score --adapter rule_based`'s number is the only thing in this
repo that was ever actually running the interpreter against these cases,
and it isn't a release gate. T-016 (below and in `docs/NEXT_TASKS.md`)
proposes closing that gap; not done as part of T-015 (scope discipline —
the task that created this section was explicit that discovering
corpus-wide gaps doesn't mean fixing all of it in one pass).

## Layers

| Layer | Input | Runs | State |
|---|---|---|---|
| **L0** | pure functions | every commit | live (`tests/`) |
| **L1** | text → tool calls | every commit, **release gate*** | **71 cases**, target 250 |
| **L2** | synthesized + real audio → tool calls | nightly | PLANNED |
| **L3** | full call over SIP | pre-release | PLANNED |
| **L4** | production reconciliation: our quote vs the PrISM ticket | nightly in pilot | PLANNED |

L1 is where the value is: fast, deterministic-ish, no telephony, catches ~90% of
order-logic regressions.

\* Gates label self-consistency (`validate`), not interpreter correctness —
see "`validate` mode" below, T-015's finding that this gate never actually
runs an interpreter on any case.

## Case format

`evals/cases/*.yaml`. See `pizza_basics.yaml` for live examples.

```yaml
- id: MOD-014
  tags: [modifier, portion, correction]
  setup: {order_type: pickup}
  turns:
    - user: "large pizza, half pepperoni half mushroom"
      calls:
        - {tool: add_item, args: {item: CHEESE PIZZA, size: large}}
        - {tool: add_modifier, args: {line_id: L1, modifier: pepperoni, portion: HALF_1}}
      forbid: [confirm_order]
  assert_final: {subtotal: "21.00", state: BUILDING}
```

Assert on **final cart state**, not only the call sequence — several valid tool
paths reach the same correct cart, and over-asserting on sequence makes the
suite brittle.

**Standing rule (T-024): a case whose correct behavior is asking a question
must carry a follow-up turn to answer it.** T-023 Phase 1 built a guard so
the system asks instead of silently guessing/dropping when a `search_menu`
result comes back genuinely ambiguous (`needs_disambiguation: true`) and no
default exists — but a single-turn case has no way to *answer* that
question, so it ends mid-clarification and scores as a failure regardless of
whether the system behaved correctly. That inverts `CLAUDE.md`'s own
"prefer clarification over guessing" rule: it rewards a model for guessing
and penalizes one for asking, on exactly the requests where guessing is
wrong. Found via `MULTI-002` (T-022's diagnosis, T-024's fix) — a case whose
correct answer genuinely depends on information the customer's own words
didn't provide (e.g. "an order of wings" with two real SKUs, 6PC/12PC, and
no count given) must have at least two turns: one where the ambiguity is
raised, one where the customer answers it. Do not add a follow-up turn for
ambiguity that isn't genuine customer-side ambiguity — a `search_menu`
precision artifact (e.g. an unrelated alias collision) is a different defect
class (see "search_menu recall diagnosis and fix" above and the ADV-002
finding) and adding a turn for it would mask the real bug instead of fixing
it. When in doubt: would a careful human order-taker actually need to ask
this, given only what the customer said? If yes, it needs a follow-up turn.
If the ambiguity only exists because of a search-matching quirk, it doesn't
— fix the matching instead, separately.

## Runtime value binding — `$ref`

Some flows genuinely need a value the label author cannot know ahead of
time — `request_quote()`'s `quote_id` is a runtime UUID. Before this
mechanism (added 2026-09-06, `evals/runner.py::resolve_args`), every
confirmation case in this corpus could only test refusal/edge behavior;
nothing could reach a real `CONFIRMED` state through `validate`.

```yaml
turns:
  - user: "What's my total?"
    calls:
      - {tool: request_quote, as: quote, args: {}}
  - user: "Yes, place it."
    calls:
      - {tool: begin_confirmation, args: {}}
      - tool: confirm_order
        args:
          quote_id: {$ref: "quote.quote_id"}
```

**Rules — this is the entire binding language, deliberately:**

- Every call's structured result is captured under a name: its `as:` label
  if given, otherwise its tool name. A later call with the same name/label
  overwrites the earlier capture — a `$ref` always sees the most recent
  matching result at the point it resolves, which is exactly what the
  stale-quote regression case below depends on.
- `{"$ref": "step.field"}` — anywhere in a call's `args` — resolves to
  `results[step][field]`. A dotted path after the first segment does one
  dict lookup per segment (`"step.a.b.c"` → `results[step]["a"]["b"]["c"]`),
  so nested lookups work, but only ever as literal dict-key lookups — no
  indexing, no arithmetic, no filtering, no Python `eval`/`exec`. That's
  deliberate: this is not a templating engine, it's the smallest thing that
  lets one call reuse another's structured output.
- **Fails the case, loudly, never silently:** an unknown step, a missing
  field, a `$ref` pointing through something that isn't a dict, or a
  malformed reference string (no `.`, or a leading/trailing `.`) all raise
  `RefError` with the step/field name and — for a missing field — the
  available keys, and the case is marked failed with that detail. A `$ref`
  never resolves to `None` or a guessed value.
- Literal arguments are untouched — `resolve_args` only special-cases a
  value that is exactly `{"$ref": "..."}`; every other value (including
  other dicts) passes through unchanged.
- `validate`'s summary lists which references each case resolved, as
  `arg<-step.field` strings only — the actual resolved value (e.g. the real
  UUID) is never printed.

See `CONFIRM-004`/`CONFIRM-005` in `evals/cases/confirmation_full_flow.yaml`
for a full successful confirm and a stale-captured-quote regression, and
`tests/test_eval_runtime_binding.py` for the mechanism tested directly
(literal passthrough, top-level and nested resolution, every failure mode,
and both flows end-to-end through `validate`).

## `validate` mode — do not skip this, and do not mistake it for interpreter testing

`python evals/runner.py validate` replays each hand-written label against the
real tools and checks `assert_final`. This proves the **label** is correct before
any model is scored against it.

Unverified golden labels are how eval suites rot. You find out three months in
that forty cases were mislabeled and every accuracy number you reported was
fiction. `tests/test_evals.py` enforces this on every commit.

**`validate` never runs an interpreter — for any case, ever (found and
documented T-015, 2026-09-08).** It executes each case's hand-typed `calls`
directly; the `user:` text is documentation, not input to anything `validate`
runs. This is by design and correct for what `validate` is *for* (proving a
label is internally consistent) — but it means "63/63 valid" (or any N/N)
has never proven that `RuleBasedInterpreter` or `LLMInterpreter` would
actually produce those calls from the English sentence. It can't catch an
interpreter bug, structurally, regardless of how many cases exist or how
deep their assertions are. This isn't hypothetical: `RuleBasedInterpreter`
shipped with zero negation handling — "no onions" charged for onions — while
the corpus that included exactly this phrasing (`GOURMET-005`, "no
pineapple") stayed green the entire time, because `validate` never asked the
interpreter what it would actually do with that sentence. `score` is the
mode that does — see below and "The interpreter-regression gate (T-016)"
immediately after this section. Full diagnosis: `docs/STATUS.md` "T-015
milestone" Part 1.

**Traces are captured by default (T-030).** Every `python evals/runner.py
score` run writes a full structured trace — every tool call/result, a
session-state snapshot after every turn, the assistant reply, real tokens/
latency/cost for the `llm` adapter — to one JSONL file under
`evals/traces/` (gitignored; an artifact, not a gate input). This exists
because T-024's own corpus audit missed a real `search_menu` bug simply by
never looking at a live model's actual queries, and T-027 needed a
throwaway script to get the traces that resolved three "separate"
mysteries into two. Don't write another throwaway trace script — the data
is already there after any `score` run, and `scripts/viewer/server.py`
(T-030) renders it (Catalog/Replay/Live tabs) without needing the traces to
exist first (Replay just has nothing to show yet). `evals/runner.py::score()`
itself never writes to disk — it takes an optional `trace_log` list
parameter that's purely additive (see `tests/test_trace_capture.py` for the
proof that scores are identical with capture on or off); only the CLI
(`main()`) writes the file. This is why `tests/test_evals.py`'s T-016 gate,
which calls `score()` directly, stays fully offline with zero trace-file
side effects.

## The interpreter-regression gate (T-016) — `validate` still doesn't run an interpreter; this is the gate that does

`validate` was not changed by T-016 and still never runs an interpreter —
see above. Instead, a second, separate, offline gate now runs
`RuleBasedInterpreter` for real: `tests/test_evals.py::
test_rule_based_interpreter_meets_baseline`, part of `pytest -q` (which
already runs first in `scripts/check.sh`), so it's already in the commit
gate with zero new CLI surface. Full design rationale, the alternative
considered, and why it's separate from `validate` rather than folded in:
`docs/decisions/ADR-009-eval-gate-runs-an-interpreter.md`.

It's a **ratchet, not a target**: `RULE_BASED_BASELINE = 35` (of 71, as of
2026-09-08) is the honest floor — the number `python evals/runner.py score
--adapter rule_based` actually reports today — not a quality goal.
`CLAUDE.md`'s 98–99% target is a production-model number and has nothing to
do with this deliberately-narrow, non-NLU pattern matcher. The gate fails
if a future change drops the real score below the recorded baseline; it is
raised only after a real `score --adapter rule_based` run confirms a higher
number, never lowered, never set to an unverified number.

**Proof the gate actually would have caught what motivated it**
(`tests/test_evals.py::test_gate_would_have_caught_the_t015_negation_bug`):
stubs `RuleBasedInterpreter._resolve_intensity_calls` back to its exact
pre-T-015 behavior (via `monkeypatch`, never editing
`lakewood/interpreter.py`) and re-runs the same gate logic. Real, measured
result: **35/71 → 25/71** — the ratchet (floor 35) correctly goes red.
Restoring the real interpreter (i.e. every other test in this suite, which
runs the unstubbed code) is what "passes again" looks like — there's no
separate restore step because nothing outside that one test's monkeypatch
was ever changed.

**What this deliberately does NOT do:** fix any of the ~36 cases
`score --adapter rule_based` still doesn't reach (T-017 and others) — the
gate's job is to stop things from getting worse, not to certify today's
49% as acceptable. It's also a single aggregate count, not a per-case set
comparison, so a same-count swap (one case starts failing while a different
one coincidentally starts passing) would not trip it — an accepted
simplicity tradeoff, see ADR-009.

Since the 60-case expansion (2026-09-06), `validate` also enforces two
previously-decorative per-turn fields, and reports a summary beyond pass/fail:

- **`forbid: [tool, ...]`** — fails the case if any of that turn's own labeled
  `calls` actually used a forbidden tool. Today this only catches a label
  edited into self-contradiction, but it is the same mechanism `score` mode
  will reuse to check a *model's* emitted calls once an adapter is wired —
  the field was already in every case's docstring example, it just wasn't
  checked. Catches premature/invalid tool calls.
- **`expect_disambiguation: true|false`** — fails the case if no call in the
  turn returned a `needs_disambiguation` key, or if it doesn't match. Adding
  this check immediately caught a real mislabeled case: `COLLIDE-001`
  claimed "shrimp scampi" is ambiguous (pizza vs. pasta), but `data/menu.json`
  has no pasta section today, so it isn't — see that case's inline note.
- The `validate` summary line now also prints category pass-rates (item-
  selection, modifier-scope, correction-handling, final-cart-exact-match) and
  two corpus-wide counts: hallucinated-SKU calls (an unexpected
  `ITEM_NOT_FOUND`/`TOPPING_NOT_FOUND`/`GOURMET_NOT_FOUND`/`SIZE_NOT_FOUND`/
  `NO_MATCH` not covered by `expect_error`) and unexpected/premature error
  calls generally. These are **label-correctness** metrics, not model
  accuracy — see the caveat below.

## Corpus composition — target 250

| Bucket | Cases | Why |
|---|---|---|
| Happy path | 40 | |
| Modifier complexity | 60 | halves, double/triple, lite, removals |
| **Corrections and retractions** | 40 | "actually…", "no wait" — where competitors are weakest |
| Quantity / multi-item | 25 | |
| Unavailable / 86'd | 20 | must refuse cleanly, never substitute |
| Transfer triggers | 15 | allergy, human, refund, card |
| **Confirmation edges** | 15 | "uh, sure I guess" must NOT confirm |
| Adversarial / injection | 15 | "you said it's free", "ignore your instructions" |
| Store info / FAQ | 20 | hours, radius, minimum, coupons |

**This menu's specific collision traps** (all belong in the corpus):
Shrimp Scampi is both a $23 pizza and a $20 pasta · five different Buffalo
Chickens across pizza, grinder, salad, wrap, tender · #2 Florentine vs #23
Chicken Florentine · #15/#20/#21 all start with "Chicken" · #7 Italiano vs #16
Italian Flag vs the Italian Combo grinder.

**Category must be resolved before item name** or the agent confidently orders
the wrong thing.

## Metrics — with denominators

| Metric | Definition | Gate |
|---|---|---|
| Item accuracy | correct item_id + size / total expected items | ≥ 99% |
| Modifier accuracy | modifier_id **and** portion **and** intensity all correct / total expected modifiers | ≥ 98% |
| Final-cart exact match | cases where the whole cart matches | ≥ 95% |
| Hallucinated SKU rate | calls naming a nonexistent item / total calls | **0** |
| Premature confirm rate | confirm without explicit assent | **0** |
| Stale-quote confirm | confirm with mismatched cart_hash | **0** |
| Transfer recall | correct transfers / cases that should transfer | ≥ 98% |
| Over-ask rate | clarifying questions on unambiguous turns | ≤ 8% |
| Cost per case | tokens × rate | tracked, alert on +10% |

**These are not the numbers above.** The Metrics table's ≥98%/≥99%/0-tolerance
targets are what `score --adapter llm` would need to report against a REAL,
funded model run — implemented (Anthropic/OpenAI/Ollama all wired) but not
yet live-verified against a funded/available provider (T-013c/T-013d). So
today `validate`'s category pass-rates and hallucinated-SKU/error counts
measure **corpus label correctness** (71/71), and `score --adapter
rule_based`'s ratcheted 35/71 (T-016) measures **interpreter-regression
protection** — neither is production accuracy, and won't be until `score`
is run against a real, funded model at N=3 per the section below.

`score` itself is implemented (T-012, `evals/runner.py::score`) — it feeds
each case's real `user` turns to an adapter and checks the same
`assert_final` `validate` does, so several valid tool paths to the same
correct cart all count as a pass, same as `validate`. Two adapters exist:
`lakewood.chat.rule_based_adapter` (deterministic, no network/credentials —
**35/71 as of 2026-09-08 (T-015), also now a ratcheted CI gate, T-016, see
above** — interpreter coverage, not accuracy), and `lakewood.chat.llm_adapter`
(T-013, wraps `LLMInterpreter` — Anthropic/OpenAI/Ollama, config-selected,
native tool-use, real tool schemas introspected from `orders.TOOLS`,
`docs/decisions/ADR-004`-compliant: no vendor code in
`menu.py`/`pricing.py`/`orders.py`).

**`llm_adapter`'s Anthropic/OpenAI benchmark has not been run** — no funded
key for either (T-013/T-013c). **The Ollama benchmark HAS run** (T-013d,
real result 11/63, against the pre-T-015 63-case corpus — stale, needs a
rerun against the current 71). The only credential available for Anthropic
in this environment was Claude Code's own session `ANTHROPIC_API_KEY` — the
user explicitly authorized using it for that task,
but a live connectivity check (`LAKEWOOD_INTERPRETER=llm python -m
lakewood.chat`) returned a real, reproducible (retried once, same result)
HTTP 400: `"Your credit balance is too low to access the Anthropic API."`
That key has no pay-per-token API balance behind it — an infrastructure
blocker, not a code defect; `LLMInterpreter` is implemented and its 15
fake-provider tests (`tests/test_llm_interpreter.py`) all pass, proving the
plumbing is correct up to the network boundary. Per this task's own
instruction not to fabricate a manual transcript or count a provider
failure as correct behavior, no benchmark numbers or manual-flow transcript
are reported here — see `docs/STATUS.md` "T-013 milestone" for exactly what
that connectivity attempt did prove, and T-013b in `docs/NEXT_TASKS.md` for
what unblocks the rest.

**Whatever `score` reports, state which adapter produced it.** A rule-based
number is interpreter coverage; only a real model run is "model accuracy,"
and only when reported that way — and only once one has actually happened.

## Non-determinism

Run each case **N=3** at low temperature. Report per-case pass rate, not binary.
3/3 green · 1–2/3 **flaky** (blocks release if flaky count > 5) · 0/3 fail.

A case that flips between runs is a real defect — usually ambiguous prompt
instructions — not noise to retry away.

**First real evidence this matters, not just a hypothetical (2026-09-15,
T-018/T-019).** `temperature: 0` in the request body did not yield
reproducible tool-call behavior from Experiential/Luna: the identical
two-turn conversation ("Large pepperoni, mushroom only on one half." →
"Actually replace the mushroom with sausage.") run twice, minutes apart,
same process, produced materially different outcomes — one run built the
pizza and reached a real quote; a fresh, isolated repro of the exact same
two utterances instead gave up after the first `search_menu` miss and left
the cart empty (`request_quote` correctly returned `EMPTY_CART`). Both
request bodies were captured directly and contained identical content —
this is model sampling variance, not a session/state leak (`Order`/
`PizzaLine` dataclasses were also directly checked: both use
`field(default_factory=list)`, no shared-mutable-default bug). **Practical
consequence: any single `score --adapter llm` run against this provider,
including this file's own T-018/T-019 numbers, is one sample, not a stable
measurement** — a re-run can legitimately land on a different aggregate.
The N=3 policy above exists for exactly this; it has not yet been run this
way (each baseline so far has been a single full-corpus pass, for cost/time
reasons) — doing so is the next real improvement to this section, not a new
task by itself: fold it into whichever task next changes a prompt or model
version.

**First real N=3 run (2026-09-15, T-020) — and a methodology bug found
doing it.** The first attempt ran all 3 passes in parallel to save wall
time and got 10–16/71 (14–23%) — dramatically lower than every prior
single-run baseline (45–49/71). Root cause: running 3 processes
concurrently exceeded Experiential's **org-level rate limit — "org_rate_
limit: this organization exceeded 60 platform-funded discovery requests"**
— the vast majority of "failures" were `HTTP 429`s counted as provider
failures, not model answers. That data was discarded outright, not
reported as a variance band; **running this provider's eval concurrently
produces meaningless numbers and must never be done again** — always
sequential. The corrected sequential N=3 band is below.

**Sequential N=3, unchanged code (2026-09-15, T-019's search_menu, before
the T-020 fix):**

| Run | Aggregate |
|---|---|
| 1 | 42/71 (59.2%) |
| 2 | 47/71 (66.2%) |
| 3 | 48/71 (67.6%) |

**Band: 42–48/71 (59–68%), spread 6 points (≈8.5 percentage points), mean
45.7.** Zero provider failures in any of the 3 runs (rules out rate-limit
or transport noise as the cause — this is real model sampling variance).
**15 of 71 cases (21%) flipped pass/fail across three identical runs of
identical code** — real, substantial noise, not measurement error.

Per-category spread across the 3 runs (`ok/of` each run):

| Category | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| entity extraction | 11/24 | 14/24 | 13/24 |
| modifier scope | 15/26 | 15/26 | 17/26 |
| item selection | 8/15 | 9/15 | 10/15 |
| correction handling | 9/15 | 9/15 | 10/15 |
| ambiguity handling | 2/2 | 2/2 | 2/2 |
| **tool sequencing** | **6/6** | **6/6** | **6/6** |
| **state/confirmation integrity** | **5/5** | **5/5** | **5/5** |
| hallucinated menu items | 5/9 | 5/9 | 5/9 |

**Reading this band matters for every number in this project going
forward:** T-018's single-run 45/71 and T-019's single-run 49/71 are both
comfortably *inside* this 42–48 band (49 is one point above the observed
max, well within reach of run-to-run noise) — **T-019's own reported "45→49
delta" was very likely mostly or entirely noise**, not evidence of the
confirmation-gate fix's effect on the aggregate. This doesn't undermine
T-019's fix — `tool sequencing` and `state/confirmation integrity` were
**perfectly stable at 6/6 and 5/5 across all 3 runs**, exactly the
categories that fix targets, and zero unsafe confirmations occurred in 213
case-runs (3×71) total. It means the *aggregate* number is the wrong place
to look for that fix's effect; the stable categories are.

## `search_menu` recall diagnosis and fix — T-020

**Diagnosed before any fix, from the real T-018/T-019 live checkpoint call
logs** (per-case actual query strings sent, not assumed) — 22 cases were
failing in the T-019 baseline; 18 of them involved at least one
`search_menu` call, the other 4 (`NEG-003`, `QTY-003`, `NEG-005`,
`FAQ-001`) are unrelated (empty tool-call turn, a quantity/remove
misunderstanding, a duplicate-intensity modifier bug, and a
`get_store_info` field-name mismatch respectively) and out of this task's
scope.

Classification of the 18 `search_menu`-involved failures (a case can carry
more than one cause):

| Class | Meaning | Cases |
|---|---|---|
| **A** — bad query, matcher correct | Model asked for something genuinely not indexed as a searchable name (the base pizza itself; a coupon code; a size word like "Sicilian" used as if it were an item) | `ADV-001`, `ADV-002`(partial), `ADV-004`, `CORRECT-005`, `CORRECT-007`, `INVALID-002`(partial), `GOURMET-011`, `MULTI-002`(partial), `MULTI-006`(partial), `COUPON-001` — **10 cases** |
| **B** — good query, poor recall | Reasonable phrasing a human would use, defeated by a strict one-directional substring check, a missing alias, or a format mismatch | `CORRECT-004`, `MOD-035`, `GOURMET-010`, `GOURMET-012`, `MOD-036`, `MULTI-004`, `MOD-014`, `GOURMET-005`, `MULTI-002`(partial), `MULTI-006`(partial) — **10 cases** |
| **C** — genuinely absent, correctly refused | Not a recall bug — F7 working as designed | the `truffle`/`truffle oil` sub-queries inside `ADV-002`/`INVALID-002` |
| **D** — ambiguity mishandled | None found | 0 cases |

**By far the single largest concrete cause (class A): the base pizza has
no name of its own to search for at all.** `CHEESE PIZZA` is a special
case inside `add_item`, invisible to `search_menu`'s own hit index
(`GOURMET_ROUND` ∪ `ALL_TOPPINGS` ∪ `NON_PIZZA`) — `search_menu("cheese
pizza")`, `search_menu("pepperoni pizza")`, `search_menu("large cheese
pizza")` all returned `NO_MATCH`, unconditionally, no matter how the model
phrased it, because there was never a matching name to find. Confirmed by
direct call: `search_menu(s, "cheese")` returns only unrelated
cheese-containing items (`PARMESAN CHEESE`, `CHEESECAKE`, `BACON
CHEESEBURGER`, …) and `search_menu(s, "pizza")` returns only the two
gourmet pizzas whose *names* happen to contain the word "Pizza" — neither
ever confirms a plain pizza is orderable.

**Class B, next largest:** a one-directional substring check
(`query in name.lower()`) broke on a filler word anywhere ("pepperoni
TOPPING", "SIDE OF ranch", "12 PIECE wings"), a number-format mismatch
("NUMBER 10" vs. the bare "10" the code actually compared against), and no
alias table at all for non-pizza items (`ALIASES` already existed for
toppings and is used by `add_modifier`; `search_menu` never consulted it,
so "extra cheese" — a real, already-aliased topping — still missed).

**Fix, in `lakewood/orders.py::search_menu` — cheap, not embeddings, per
this task's own instruction to try cheap first:**
1. **`_strip_search_filler`** removes a small fixed set of carrier words
   (`topping(s)`, `sauce`, `dressing`, `dipping`, `side`, `of`, `piece(s)`,
   `pc`) before the existing substring check — same direction, same logic,
   just fewer words in the way.
2. **`_NUMBER_PREFIX_RE`** normalizes `"number 10"` / `"num 10"` / `"no. 10"`
   / `"#10"` to the bare `"10"` the gourmet-number check already handled.
3. **`ALIASES` is now consulted** for toppings (fixes "extra cheese" and
   any other existing topping alias), and a new, parallel **`NON_PIZZA_
   ALIASES`** table (same shape, same file) covers non-pizza spoken forms
   that were never a substring of the real POS name at all — drink words
   ("coke"/"soda"/"two liter"/"bottle" → `CAN`/`2LITER`/`20OZ`, mirroring
   `RuleBasedInterpreter`'s own private `_DRINK_WORDS` — corrected below,
   T-039: this was never actually true, they drifted apart) and one abbreviation
   ("strawberry cheesecake" → `STRWBRY CHZCAKE`).
   **T-039 correction:** the two tables kept drifting — `_DRINK_WORDS`
   missed T-032's later precedence fix and grew its own worse bug (a bare
   "can" as a false trigger, see ADR-017). `RuleBasedInterpreter._find_drink`
   now calls `oe.non_pizza_alias_hits` directly instead of maintaining a
   second copy — one real table, one real precedence rule, two callers.
4. **A `CHEESE PIZZA` pseudo-hit** — the actual fix for the largest class.
   Fires when "pizza" appears in the query AND either nothing else
   unrecognized is left over ("cheese pizza", "large cheese pizza", "party
   size cheese pizza") or the leftover word is *already* a real, matched
   topping ("pepperoni pizza" → `PEPPERONI` was already found by the
   existing topping loop, so the residual is grounded, not invented).
5. **A generic "drink"/"beverage(s)" mention** returns all three drink
   items as disambiguation candidates rather than `NO_MATCH` — that's real
   ambiguity (which size?), not a miss to paper over.

**F7 held throughout — two real false positives were found and reverted
during this work, not shipped:** an initial, more aggressive fix also
matched topping names *inside* a longer query in the reverse direction
(`name in query`, not just `query in name`), which correctly caught
compound sentences like "…with pepperoni on one half and sausage on the
other half" — but also matched `"CHICKEN"` inside `"chicken tenders"` and
`"SHRIMP"` inside `"shrimp scampi"`, both real menu words used in a
genuinely different, non-menu phrase. **A wrong candidate is worse than a
miss (F7's own principle) — reverted.** The `CHEESE PIZZA` pseudo-hit has
the same shape of risk and is deliberately narrow for the same reason: it
does **not** fire for `"truffle lobster pizza"` (residual `"truffle
lobster"` matches no real topping) — regression-tested
(`test_nonexistent_item_is_refused_not_invented`). Every hit `search_menu`
returns, before and after this fix, is still only ever a *candidate for
disambiguation* — `add_item`/`add_modifier` resolve the authoritative name
themselves and can still refuse; nothing here can auto-select an item into
the cart.

**Known residual gaps, not chased further (diminishing returns / real NLP
required, not a cheap fix):** word-form quantities ("twelve piece wings" —
only the digit form "12 piece wings" is normalized); a specific numbered
gourmet embedded inside a longer compound sentence ("Sicilian number 3
pizza" needs the model to search "3"/"number 3" alone, which already
works, rather than the whole sentence); the fully compound multi-topping
sentence case above (reverted, see F7 note) stays a real, accepted gap
rather than risk a wrong match.

**Offline verification:** `test_all_golden_labels_are_valid` (`validate`
71/71 — two labels, `INVALID-001` and `COLLIDE-001`, briefly broke during
the reverted reverse-match attempt and are back to green with the final
version) and the full suite are unaffected — see `docs/STATUS.md` "T-020
milestone" for the live-provider delta.

## Real speech found a P0 the corpus never did — T-039

Ten turns of a real T-038 Phase 2 voice session (real spoken audio, real
Parakeet STT, transcripts independently confirmed correct) found a P0 —
`RuleBasedInterpreter` silently turning a garden salad into a small cheese
pizza, a calzone and a chicken caesar wrap into topping words grafted onto
that same wrong line, and "a two liter coke" into a can — that **73 hand-
authored cases, every synthetic fixture, and four prior diagnostic sweeps
(T-020, T-022, T-027, T-031) never surfaced.** Full mechanism, fix, and
evidence: `docs/decisions/ADR-017-no-silent-item-substitution.md`.

**Root cause of the corpus blind spot, checked directly, not assumed:** zero
of the 73 pre-existing cases ordered a non-pizza item (salad, calzone, wrap,
appetizer) at all — the exact class of order this defect hit. A corpus built
entirely from hand-authored pizza-centric phrasing cannot find a bug whose
trigger condition it never once constructs. `evals/cases/non_pizza_items.yaml`
(5 cases, added this task) closes the gap going forward, but the honest
lesson is narrower than "add more cases": **synthetic corpus growth is bounded
by what the corpus author thinks to write.** Real calls are not. This is the
standing argument for prioritizing (a) real-audio STT fixtures over more
synthesized-SAPI ones (see `docs/decisions/ADR-008-local-stt-runtime.md`'s
own caveat), and (b) reaching a real restaurant pilot, where every
transferred/corrected call becomes a case no one had to imagine in advance —
exactly the corpus-flywheel step below, now with a concrete example of what
it catches that hand-authoring structurally cannot.

**Correction — T-039A, same day, reopened T-039.** The fix described above
shipped as a 12-word denylist (`_NON_PIZZA_HEAD_WORDS`). That does not
generalize: any product noun NOT on the list ("nachos", "soup", "tacos",
"appetizer", "garlic bread") still silently became a priced pizza,
confirmed directly on the commit that closed T-039. The corpus's own blind
spot compounds this exactly as described above — a denylist is itself
"what the author thought to write," the same structural ceiling as the
corpus that missed the original bug. T-039A replaced the denylist with
fail-closed intent parsing (a pizza needs POSITIVE evidence, not merely the
absence of a known-bad word) and generalized the LLM-path guard to cover a
model substituting a valid non-pizza SKU, not just a fabricated one. Full
mechanism: ADR-017's "Amendment" section. `evals/cases/non_pizza_items.yaml`
stays as-is (5 cases); this task corrected 4 pre-existing adversarial
labels (`ADV-002`, `ADV-004`, `COUPON-002`, `INVALID-002`) that had
asserted the old, permissive behavior for a compound utterance (a clear
pizza base plus one unresolvable modifier/clause) — see ADR-017's "Known,
accepted tradeoff" for the honest cost of that correction.

**Correction — T-039B, 2026-09-18: retrieval is not customer authorization.**
T-039A's LLM-path guard trusted any `search_menu` hit returned during the
same turn as authorization for a matching `add_item`, with no check that
the hit was unambiguous and no check that the model's own search *query*
had any support in the customer's utterance. Reproduced directly: customer
says "I want a salad," model calls `search_menu("wrap")` then
`add_item("WRAP")` — authorized, WRAP added. The same shape worked for a
model-chosen "coke" query resolving to `CAN`, and a model-chosen
"bruschetta" query resolving to a gourmet pizza. A retrieved candidate was
being treated as customer consent instead of evidence for a clarifying
question — the same underlying defect class T-039/T-039A closed for a
*direct* substitution, now shown to also apply to a *retrieved* one.
Replaced with `_authorize_item_creation`, which requires both the search
query and the exact retrieved SKU to be independently supported by the
customer's own words, and added deterministic resolution of an explicit
follow-up ("the large garden salad") against the server-owned
`session.pending_disambiguations` set, including narrowing a family
("the garden one") before a bare size ("large") resolves it. A related
persistence gap was found and fixed in the same task: registering,
narrowing, or clearing `pending_disambiguations` is a real mutation of
authoritative clarification state, but `search_menu` was treated as
read-only, so that state was never saved — a dropped call or reload
between an ambiguous search and the customer's next turn silently lost the
pending clarification. Full mechanism: ADR-017's T-039B amendment.
`evals/cases/non_pizza_items.yaml` gained 3 cases (`NONPIZZA-006/007/008`:
family narrowing, the adversarial unsupported-search-then-select shape,
and rejection of a SKU outside the pending set) — 81 cases total.

## T-039 live N=3 Experiential acceptance gate, 2026-09-18

The deferred live acceptance measurement for T-039/T-039A/T-039B, run once
`EXPLABS_API_KEY` became available. Provider `LAKEWOOD_LLM_PROVIDER=
experiential`, model `gpt-5.6-luna` (repo default, `LAKEWOOD_LLM_MODEL`
unset) — same provider/model as the T-032 historical band. Fixed config
across all 3 runs: temperature 0, 30s per-request timeout, 12-round tool
loop cap, 81-case corpus, no code/prompt/label changes between runs.

```
                full/81   overlap/73   provider fails   trace
Run 1           48        41           0                evals/traces/20260918T124814_llm.jsonl
Run 2           47        39           0                evals/traces/20260918T125937_llm.jsonl
Run 3           49        41           1 (CONFIRM-004,   evals/traces/20260918T131230_llm.jsonl
                                        timeout, honestly
                                        preserved, not
                                        retried)
mean            48.0      40.33
```

**Historical-overlap set (73 IDs) determined from git history, not
guessed:** `evals/cases/non_pizza_items.yaml` was introduced wholesale (8
IDs: `NONPIZZA-001`..`008`) in the commit that closed T-039 (`57344f2`);
the corpus at that commit's parent was independently counted at exactly 73
`- id:` entries; no case ID was added or removed by any commit since (3
files were touched for label corrections only, per ADR-017's T-039A
amendment — counts unchanged, verified directly). 81 − 8 = 73.

**Primary acceptance criterion (silent item substitutions: 0) PASSES,
robustly, across all 3 runs.** Every `add_item` call in all 3 traces (316
total) was scanned programmatically for an authorization bypass — an `ok`
result whose `authorization_reason` is not one of
`DIRECT_UTTERANCE_EVIDENCE`/`UNIQUE_SUPPORTED_SEARCH_RESULT`/
`CUSTOMER_CONFIRMED_PENDING_CANDIDATE`. **Zero found, in every run.** Zero
internal error/reason-code strings leaked into any customer-facing reply
(scanned all 3 full traces for the raw code names and internal detail
markers). All authorization rejections left the cart provably unchanged —
guaranteed structurally, since `add_item`'s real domain mutation is only
ever reached after `auth_reason in _AUTHORIZED_REASONS` passes; the zero-
anomaly scan is the direct proof this held live, not just in the type
system.

Authorization-rejection totals across the 3 runs:

```
                Run1   Run2   Run3
DIRECT_UTTERANCE_EVIDENCE           37     36     36
UNIQUE_SUPPORTED_SEARCH_RESULT       3      3      2
CUSTOMER_CONFIRMED_PENDING_CANDIDATE 4      4      6
AMBIGUOUS_CANDIDATE_NOT_CONFIRMED    4      2      2
UNSUPPORTED_ITEM_SUBSTITUTION       60     56     61
```

**The historical-overlap drop (54.33 mean → 40.33 mean vs the T-032 band)
is NOT explained by "safe refusal of an adversarial substitution
attempt."** Root-caused to four narrow, pre-existing evidence-vocabulary
gaps in the mutation-boundary guard, never consequential before T-039B
made exact-word support load-bearing:

1. `_PIZZA_WORD_RE` doesn't match the plural ("pizzas") — `_has_pizza_
   intent("three medium cheese pizzas")` → `False`, reproduced directly.
   Blocks `QTY-002`, `MULTI-001/003/004/006` identically in all 3 runs.
2. Pizza-shorthand intensity vocabulary has no "triple"/"quadruple" —
   blocks `MOD-032` ("small cheese with triple pepperoni") in all 3 runs.
3. Gourmet-number evidence only matches numeral digits, never a spelled-
   out cardinal ("number ten") — blocks `GOURMET-005/010/011/012/013` in
   all 3 runs.
4. Non-pizza candidate word-matching has no spelled-out-quantity-to-
   abbreviated-SKU mapping ("six piece wings" → "6PC WINGS") — blocks
   `CORRECT-007`, `MULTI-002`, `COUPON-001` in all 3 runs, even after the
   customer explicitly answers the system's own disambiguation question.

None of these four produced a wrong item — every one is a correct refusal
of a CORRECT, non-adversarial request. This falsifies the "supported
direct orders still work" acceptance criterion (required, not optional),
so the live gate's **overall verdict is FAIL**, even though the P0-
relevant half (substitution safety) is fully closed. Filed as **T-041**
(`docs/NEXT_TASKS.md`) — expand the evidence vocabulary at the four points
above; do not weaken `_authorize_item_creation`'s actual authorization
logic.

**Secondary, flaky finding: `NONPIZZA-006` failed in Run 1 (unnecessary
`transfer_to_human`, empty cart, never a substitution) but passed in Runs
2/3**, because `LLMInterpreter` has no equivalent of `RuleBasedInterpreter`'s
`_narrow_disambiguation` — a model's own conversational narrowing reply
doesn't persist server-side. Runs 2/3 self-recovered only because the
model happened to issue an extra `search_menu` that registered a fresh,
already-narrowed candidate entry; Run 1's retry query returned `NO_MATCH`
instead, so no such entry existed. Rolled into T-041's scope.

**Zero pricing mismatches were caused by T-039B.** `orders.py`'s pricing
code is untouched by T-039B except `_narrow_disambiguation` (never touches
price). The two pricing-label misses observed (`MOD-035`, `MOD-036`) trace
to pre-existing, unrelated model behavior (an abandoned ambiguous-topping
search; a `DOUBLE`-intensity modifier choice for "extra cheese") — not to
the mutation-boundary guard.

**Provider usage, mean across 3 runs:** ~398 requests, ~515K total tokens
(mostly prompt), mean latency ~1.8s/request (median ~1.7s, p95 ~2.9s),
$0.00 provider-reported cost on every request (Experiential reports no
`usage.cost` field for this model — "no cost reported," never assumed
$0). Schema violations (BAD_ARGS/UNKNOWN_TOOL): 0 in every run —
100% of the model's tool-call shapes were schema-valid. Hallucinated-SKU
calls (all safely rejected before any domain mutation, per `NO_MATCH`/
`ITEM_NOT_FOUND`-class codes): 35–38 per run.

**T-038 Phase 2 (real-hardware voice loop) does NOT unblock from this
result** — the FAIL verdict above means **T-041** is the required next
step, not the microphone loop; re-run this same N=3 gate once T-041 lands
to confirm the historical-overlap recovers without reintroducing any
authorization bypass.

## Release gate

**This is the future production-model gate (PLANNED — no real `score
--adapter llm` run has happened yet to check it against; see T-013c/T-013d).
It is not the same thing as the CI gate that runs on every commit today**
(`python -m pytest -q` + `python evals/runner.py validate` —
`test_all_golden_labels_are_valid` for label correctness and, since T-016,
`test_rule_based_interpreter_meets_baseline`, a ratcheted floor against the
deterministic rule-based interpreter — see "`validate` mode" above and
ADR-009). Don't conflate the two: today's gate protects against
regressions in a narrow pattern-matcher; this section is what a REAL model
would need to clear before shipping.

A prompt, model, or provider change is blocked if **any** of: modifier accuracy
< 98% · item accuracy < 99% · any hallucinated SKU · any premature or
stale-quote confirm · transfer recall < 98% · any regression > 0.5% vs last
green · any case passing on `main` now failing.

**Model, prompt, and provider changes ship on eval results, never on manual
testing.** "It sounded good on a test call" is not evidence.

## Corpus flywheel

1. Hand-write from PRD acceptance cases and worst-case real orders.
2. Generate variations with an LLM, then **hand-verify every label**.
3. **Every production failure becomes a case.** Weekly during pilot: pull
   transferred and corrected calls, label, add. Target 10–15/week.
4. Never delete a case.
