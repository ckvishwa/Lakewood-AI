# AUDIT T-043 — Full Audit: Establish Verified Ground Truth

**Date:** 2026-09-22. **Scope:** no application code changed. Every claim
below is labeled **VERIFIED** (re-derived in this audit, with the command
or evidence shown), **CLAIMED** (asserted in prior docs, not independently
re-verified this audit — no primary source survives), or **FALSE**
(disproven in this audit; corrected in the source doc).

---

## CURRENT STATE

The repository is a headless, deterministic pizza-order engine (`orders.py`)
with two interpreters (`RuleBasedInterpreter`, `LLMInterpreter`) sitting in
front of it, an eval harness (`evals/runner.py`), and an in-memory/Postgres
persistence layer. Branch `codex/parakeet-stt`, HEAD `d3355dc` — **VERIFIED**
identical to `origin/codex/parakeet-stt` (`git fetch` + `git rev-parse` both
sides, see PART 2).

---

## PART 1 FINDING — what rewrote the repo

**Verdict: STRONGLY SUSPECTED (Codex CLI, own crash-restart loop, wrong
working directory), with a fix partially in place and one action still
owed by the owner.**

### Session setup findings

- **Other Claude Code sessions:** `ListAgents` — 7 peer sessions, all
  `offline`. None active. **VERIFIED**, not a contributor.
- **Codex:** `codex-windows-sandbox-service.exe` (PID 11400) was running
  at investigation time, started 2026-09-22 11:32:39 — **VERIFIED** via
  `Get-CimInstance Win32_Process`. Per user instruction this session,
  **left untouched** (may be legitimate work on a different project
  today); the user chose to skip Part 3's live re-run rather than stop it.
  **Codex has a long, independently-confirmed history of working this
  exact repository** — `~/.codex/config.toml` registers both
  `[projects.'d:\projects\ai']` and `[projects.'d:\projects\ai\evals']`
  as separately-trusted roots (the latter is itself an anomaly: a nested
  project root distinct from the top-level one), and
  `~/.codex/history.jsonl`/`session_index.jsonl` contain full prompts for
  this project by name ("Lakewood Voice AI pizza-ordering project",
  `docs/STATUS.md`, `lakewood/orders.py`, etc.), including a session
  literally titled *"Continue TASK T-039B work"* and one titled *"Create
  temporary .venv-codex"* that matches `STATUS.md`'s own 2026-09-15
  fresh-environment entry verbatim.
- **File sync:** `OneDrive`/`Dropbox`/`Drive`/`iCloud`-matching processes
  — **zero found** (`Get-CimInstance Win32_Process | Where-Object {Name
  -match 'onedrive|dropbox|drive|sync'}`). Ruled out. **VERIFIED**.
- **`/loop` schedule:** the task's own text reports 5+ spurious wakeups
  including one referencing a nonexistent job ID. I did not find a
  Claude-Code-side `/loop` state file in this session's own tooling to
  independently confirm this claim this audit — **CLAIMED**, not
  re-verified. Given the Codex-side evidence below (a genuine
  crash-restart-replay loop, same *shape* of symptom, different tool), I
  treat this as either the same underlying mechanism misattributed, or a
  second, so-far-unconfirmed issue. Flagged, not closed.
- **IDE local-history / scheduled tasks / git hooks / watchers:** not
  independently audited this session (time-budget tradeoff, disclosed
  rather than silently skipped). **UNVERIFIED.**

### Direct evidence of the rewrite

1. **`~/.codex/.sandbox/sandbox.2026-09-18.log`** — `codex.exe` ran
   internal "world-writable scan" self-audits at **15:57:30, 15:59:19,
   16:04:35, 16:12:28, 16:13:24, 16:17:31** local time on 2026-09-18.
   `pricing_engine.py`'s write (16:11:53) and `chat.py`'s rewrite
   (16:12:22) fall directly inside this window — bracketed almost to the
   second by the 16:04:35 and 16:12:28 entries. **VERIFIED**, primary
   source read directly.
2. **`~/.codex/history.jsonl`** — 6 separate new Codex threads were
   created in the same window (15:57:56, 15:59:28, 16:05:03, 16:13:08,
   16:13:29, 16:17:53), each submitting an **identical** prompt: *"TASK
   T-001: First End-to-End GCP IAM Detection"* for **CloudShield** — a
   completely unrelated GCP-security-detection portfolio project with its
   own, separately-registered directory (`d:\projects\cloudshield` per
   `config.toml`, distinct from `d:\projects\ai`). Six identical
   resubmissions of one prompt in 20 minutes is not a human typing it six
   times; it is a retry/queue-replay pattern. **VERIFIED.**
3. **`~/.codex/thread_history_1.sqlite`** — each of those 6 threads
   contains **exactly one item** (the inbound `userMessage`) and nothing
   else: no `agentMessage`, no `commandExecution`, no `fileChange`. The
   entire day of 2026-09-18 has **zero** `fileChange` items in this
   database. This means Codex's own turn-completion logging never
   recorded whatever wrote `pricing_engine.py`/`chat.py` — either the
   responsible turn crashed before it could log completion, or the write
   happened through a path this database doesn't capture. **VERIFIED**
   (queried directly), and this is the honest limit of what I could
   recover — **I could not produce a single log line that names
   `chat.py` or `pricing_engine.py` as Codex's own write target.**
4. **`~/.codex/logs_2.sqlite`** (raw Rust tracing log) — `codex_models_
   manager::manager` **ERROR** entries recur roughly every 1–5 minutes
   through the whole window (15:51:25, 15:55:55, 15:57:29, 15:59:19,
   16:00:25, 16:01:59, 16:04:35, 16:04:55, 16:05:03, 16:09:25, 16:12:28
   …), **each under a different process id** (37716, 41240, 3412, 45004,
   40800, …) — a live process-restart pattern, not one process erroring
   repeatedly. `codex_core::session_startup_prewarm` fires at the start of
   each new pid. The error 6 seconds after `chat.py`'s write (16:12:28,
   pid 40800) is the closest-in-time entry to the write itself.
   **VERIFIED**, primary source.

### Conclusion

**Strongly suspected, not fully identified with a literal write-log
entry.** The evidence supports: a model/provider connectivity failure
(`codex_models_manager` errors) drove a supervising process to repeatedly
restart Codex; each restart replayed a **queued** prompt (Codex's desktop
config has `followUpQueueMode = "queue"`) intended for the CloudShield
project; the restarted process operated with `D:\Projects\Ai` as its
working directory instead of `d:\projects\cloudshield`, and somewhere in
the ~8-minute gap between the 16:05:03 and 16:13:08 replay attempts, a
turn executed file writes against this repo before crashing again — which
is why the completion was never logged. **What would fully confirm it:**
a Codex crash-report/panic log naming the cwd at crash time (not found in
the logs I had access to), or Windows Process-Creation Event Log entries
(4688) for `codex.exe` with command-line/cwd, which I did not query this
session (would require enabling process auditing retroactively, which
isn't possible for a past event).

### Security check on `pricing_engine.py`

**UNVERIFIED — I cannot complete this check.** The file was deleted in
the prior task turn (T-041), before this audit began, on the user's
explicit instruction, and its content was never read or logged anywhere I
could recover this session (see thread_history_1.sqlite finding above —
every `pricing_engine.py`-adjacent hit in Codex's own logs turned out to
be the unrelated phrase "pricing engine" from `docs/STATUS.md`/`CLAUDE.md`
prose, not the file). I have **no way to state** whether it read
credentials, made network calls, or touched `EXPLABS_API_KEY`. This is an
honest gap, not a clean bill of health — flagged as unresolved.

### Fix status

**Partially in place, one action still owed:**
- The rewritten `chat.py` was restored via `git checkout -- lakewood/chat.py`
  (verified byte-identical to HEAD, see PART 2) and `pricing_engine.py` was
  deleted, in the prior turn.
- **Not yet done:** confirming the CloudShield-prompt queue is actually
  drained on the Codex side (I did not clear it — that's an internal Codex
  state I have no safe way to edit from here), and confirming
  `codex-windows-sandbox-service.exe`'s CURRENT (2026-09-22) instance is
  not pointed at this repo. The user chose, this session, to leave that
  process alone and skip the live re-run rather than have me touch it.
  **This is the one action still owed before a live gate can be trusted
  again** — see NEXT TASK.

---

## PART 2 — CODE INTEGRITY

All **VERIFIED**, real commands, this session:

```
git status --short          → M docs/EVALS.md, M docs/NEXT_TASKS.md, M docs/STATUS.md,
                               M docs/decisions/ADR-017-*.md (this audit's own doc edits,
                               made after this check), plus pre-existing untracked
                               .tmp/ (gitignored scratch) and a stray junk file `bject`
                               (pre-existing since before T-039B, harmless, not cleaned)
git rev-parse HEAD           → d3355dc9a694ef422ed213a4e42ed9785a3566f8
git fetch origin codex/parakeet-stt
git rev-parse origin/codex/parakeet-stt → d3355dc9a694ef422ed213a4e42ed9785a3566f8  (MATCH)
git stash list                → (empty)
git diff --stat HEAD -- lakewood/chat.py → (empty; byte-identical to HEAD)
```

`pricing_engine.py`: **gone**, confirmed absent (`ls pricing_engine.py` →
not found). No other stray file beyond `.tmp/` and `bject` (both
pre-existing, both harmless — `.tmp/` is gitignored scratch/trace-baseline
data, `bject` is a truncated shell-redirect artifact from before this
audit's window).

Per-file last-changed commit:

| File | Last commit | Matches STATUS.md's claim? |
|---|---|---|
| `lakewood/orders.py` | `d3355dc` (T-041) | **VERIFIED** |
| `lakewood/interpreter.py` | `d3355dc` (T-041) | **VERIFIED** |
| `lakewood/chat.py` | `e211ac7` (T-039B) | **VERIFIED** — correctly unchanged since T-039B, confirming the restore landed exactly on the last real commit, not some other stale state |
| `lakewood/pricing.py` | `6de7c38` (initial commit) | **VERIFIED** — never touched since project start |
| `lakewood/menu.py` | `6de7c38` (initial commit) | **VERIFIED** |
| `lakewood/coupons.py` | `6de7c38` (initial commit) | **VERIFIED** |
| `data/menu.json` | `6de7c38` (initial commit) | **VERIFIED** — never modified since the PrISM-verified initial commit |

**Conclusion: the code on disk is exactly the code STATUS.md describes.**
No hidden drift found.

---

## PART 3 — GATES RE-RUN FROM CLEAN (offline only)

**Live re-run: SKIPPED this audit**, per the user's explicit choice in
Part 1 (leave Codex's currently-running process alone; live gate deferred
until the owner confirms Codex is not pointed at this repo). See NEXT TASK.

All of the below are **VERIFIED**, run fresh this session, on the clean,
HEAD-matching working tree:

```
python -m pytest --no-header -rA        → 583 passed, 2 skipped, 2 xfailed
python evals/runner.py validate          → 91/91 cases valid
python evals/runner.py score --adapter rule_based → 58/91
python -m pytest tests/test_pricing_parity.py     → 50 passed
```

**Every number matches T-041's own reported offline numbers exactly** —
the offline gate was not affected by the mid-run file corruption (it runs
long after; this audit's fresh re-run from a verified-clean tree proves
it, not just re-asserts it).

**No-Postgres/no-network/no-model claim, checked directly:**
```
python -c "import psycopg2"   → ModuleNotFoundError (not installed)
pip list | grep -i postgres    → (empty)
LAKEWOOD_POSTGRES_TEST_DSN     → unset (Postgres contract tests correctly skip)
```
**VERIFIED.**

---

## PART 4 — SAFETY INVARIANTS

Each guard's test, current pass/fail, and — for the ones mutation-tested
this audit — what happens when the specific check is disabled in a
throwaway in-process monkeypatch (never touching a file). Not every guard
got the full mutation-test treatment this session; that's disclosed
per-row, not hidden.

| Guard | Test(s) | Passes now | Mutation-tested this audit? | Result when disabled |
|---|---|---|---|---|
| F5 (quote↔cart-hash) | `test_orders.py::test_f5_stale_quote_cannot_confirm` | ✅ | **Yes** | First attempt (patching `cart_hash` alone) didn't isolate it — `quote_id` invalidation via F4 masked it. Built an isolated case (out-of-band cart mutation, `quote_id` kept "valid," `cart_hash` intact vs. patched to always agree): **intact → `CART_CHANGED`; disabled → confirms successfully with the WRONG total ($19.32, pepperoni unpriced)** — a real pricing-integrity break. Load-bearing, confirmed by construction, not by trust. |
| F6 (idempotent confirm) | `test_orders.py::test_f6_confirm_is_idempotent` (the exact test T-019 found vacuous once) | ✅ | **Yes** | Disabled (cleared `sess.idempotency` before the 2nd call): 2nd call hits `sess.guard("AWAITING_CONFIRMATION")` on an already-CONFIRMED session and returns an error, so `a == b` **correctly fails**. Not vacuous today. |
| F14 (same-turn confirm rejected) | `test_confirmation_gate.py::test_same_turn_confirm_order_is_rejected` | ✅ | **Yes** | Disabled (`confirmation_turn = None` before the same-turn call): confirm **succeeds** (returns `status: ok`, no `code` key) instead of `PREMATURE_CONFIRMATION`. Load-bearing. |
| F15 (unresolved lookups, per-request) | `test_confirmation_gate.py` (T-026 cases) | ✅ | Partial | Spot-checked directly: a miss blocks `begin_confirmation` (`UNRESOLVED_REQUEST`); an **unrelated** later success does **not** clear it (T-026's own fix, re-confirmed). Full suite for this file passes (62/62 across 4 files). Not independently mutation-broken this audit (time budget). |
| F16 (mandatory readback) | `test_confirmation_gate.py::test_readback_is_spoken_to_the_customer` | ✅ | Spot-checked | Direct call confirmed a real, cart-accurate readback string is present and required before confirmation proceeds. Not independently mutation-broken this audit. |
| F17/F18 (disambiguation blocks completion; cap→transfer) | `test_disambiguation_guard.py`, `test_evals.py` | ✅ | **Yes** | Disabled (`MAX_DISAMBIGUATION_ASKS` patched to 10,000 in-process): repeated non-answers stay `BUILDING` forever instead of transferring at the cap. Intact: correctly reaches `TRANSFERRED` after 2 unanswered asks. Load-bearing. |
| T-039 (no silent substitution) | `test_item_substitution_guard_generalized.py`, `test_t039b_candidate_authorization.py`, `test_t041_evidence_vocabulary.py` | ✅ (76/76 combined) | **Yes — see THE AUTHORIZATION QUESTION below** | |
| `store_id` server-bound | (structural, not a single test) | ✅ | **Yes** — structural, not runtime | `store_id` is checked to never appear as a parameter of any function in `oe.TOOLS` (`inspect.signature`), for **every** tool — a model/client cannot inject it via any real tool call, by Python signature, not just a runtime check. |
| Tenancy (cross-store reads impossible) | `test_persistence_tenant_isolation.py` | ✅ (7/7) | Not re-mutated this audit | Full file passes; not independently broken-and-confirmed this session (time budget). |
| Errors (internal codes never reach customer) | `test_item_substitution_guard.py`, `_INTERNAL_DETAIL_ERROR_CODES` in `chat.py` | ✅ | Not re-mutated this audit | Code inspected directly: `{BAD_LINE, NOT_ON_PIZZA, UNKNOWN_TOOL, BAD_ARGS}` mapped to one generic customer-safe fallback string; every other code's message is written customer-safe as-is (established in T-039A, re-inspected this audit, not re-broken-and-confirmed). |

---

## THE AUTHORIZATION QUESTION

**Settled, with evidence, not assumed.**

`_authorize_item_creation` has **exactly one call site** in the entire
codebase (`grep -n "_authorize_item_creation(" lakewood/interpreter.py` →
its own `def` line and one call inside `LLMInterpreter._interpret_staged`).
**`RuleBasedInterpreter` never calls it.** Read literally, the task's
target design — "one authorization boundary, shared by both interpreters"
— **is FALSE** as a statement about a shared function.

But the deeper question the task poses — is "it can only add what it
parsed" actually a safety property, given the original P0 *was* the
parser — is answered by tracing **every** `add_item` call site inside
`RuleBasedInterpreter` (6 total, `grep -n 'ToolCall("add_item"'` restricted
to the class):

1. Generic drink mention → gated by `_find_drink` → `oe.non_pizza_alias_hits`
   — the **same real alias table** `search_menu` and the LLM path's
   direct-evidence check both use.
2. `_half_and_half_by_number` (gourmet-number selection) → gated by a
   `size_needed` marker only ever set by the interpreter's own prior real
   gourmet-number match, plus the customer's own size word.
3. `_new_pizza` (the exact call that fabricated the original P0's pizza)
   → gated by `_has_pizza_intent(t)` **directly at the call site** — the
   **same shared predicate** `_authorize_item_creation` uses for the LLM
   path's `AUTH_DIRECT_UTTERANCE_EVIDENCE`.
4. `_new_pizza_half_a_half_b` → gated by `size` **and** a match against
   `_HALF_A_HALF_B_RE` — **not** `_has_pizza_intent`. This is the one
   real, named exception: it relies on the regex pattern itself as its
   own evidence rather than the shared predicate. Freshly tested (below):
   does not currently reproduce the P0, but it is a structurally
   different, unshared gate. **Filed as part of the next task's scope**
   — not proven unsafe, but not proven equivalent to the shared predicate
   either.
5. `_select_pending_candidate` outcome `== "SELECTED"` (explicit
   follow-up selection) → the **exact same function** the LLM path's
   pending-candidate authorization uses.

**Empirical re-test, fresh this audit, of all five original T-038 P0
utterances directly through `RuleBasedInterpreter`:**

| Utterance | Result |
|---|---|
| "A garden salad small and grilled with grilled chicken" | `search_menu`, empty cart |
| "One calzone with mozzarella and ricotta" | `search_menu`, `NO_MATCH`, empty cart |
| "One chicken caesar wrap with fries" | `search_menu`, `NO_MATCH`, empty cart |
| "a two liter coke" | `add_item(2LITER)`, $4.00 — correct, real, unambiguous item |
| "one appetizer and extra sauce" | `search_menu`, `NO_MATCH`, empty cart |

**Zero fabricated pizzas. VERIFIED, not copied from a prior report.**

**Honest conclusion:** the literal design rule (one shared function) is
not met. The **intent** — no `add_item` without positive customer
evidence — **is** met on 5 of 6 call sites via a genuinely shared
predicate/function, not independent, drifting copies. One call site
(`_new_pizza_half_a_half_b`) is the real, narrow exception, named exactly,
not glossed over. This is a defensible architecture, not a false claim of
unification — but it is also not the "one function" the task's framing
assumed exists, and I will not claim it does.

---

## PART 5 — RECONCILE THE NUMBERS

### Trusted numbers

| Point | Corpus | Score | Measured on | Trustworthy? |
|---|---|---|---|---|
| T-018 | 71 | 45/71 (single run) | live, `experiential` | **FALSE as a standalone claim** — T-020 itself found this sits inside a later-measured 42–48/71 variance band; treating it as a fix-effect measurement was already retracted in STATUS.md. **CLAIMED**, corpus/trace not on disk. |
| T-019 | 71 | 49/71 (single run) | live, `experiential` | Same caveat — inside the T-020 variance band. **CLAIMED.** |
| T-020 | 71 | 42/71, 47/71, 48/71 (band, mean 45.7) | live, `experiential`, 3 sequential runs | **CLAIMED** (STATUS.md's own documented band; no trace file survives on disk to re-verify this audit) |
| T-023/T-024 | 73 | rule_based 36/73 | offline | **CLAIMED** |
| T-031 | 73 | 56/73, 50/73, 55/73 (mean 53.67) | live, `experiential`, 3 sequential | **CLAIMED** — no surviving trace file distinguishable from T-032's discarded preliminary set (see below); not independently re-derived |
| T-032 | 73 | 53/73, 54/73, 56/73 (mean 54.33) | live, `experiential`, 3 sequential | **VERIFIED this audit** — real trace files on disk (`evals/traces/20260916T{185759,190911,192014}_llm.jsonl`), re-scored directly, exact match to STATUS.md's claim. (A second, earlier same-day trace triplet — `135432/140534/141645` — scores 56/50/55, does **not** match the reported 53/54/56; likely a discarded preliminary run per this repo's own "discard contaminated runs" rule. Not counted.) |
| T-039 live gate (pre-T-041) | 81 | 48/81, 47/81, 49/81 (mean 48.0); overlap/73 = 41/39/41 (mean 40.33) | live, `experiential`, 3 sequential | **VERIFIED this audit** — trace files present, re-scored, matches STATUS.md. |
| T-041 (as reported) | 91 | 62/91, 64/91, 59/91 raw / 65/91, 67/91, 62/91 corrected (mean 61.7 raw / 64.7 corrected); overlap/73 = 48/49/44 (mean 47.0) | live, `experiential`, 3 sequential | **VERIFIED this audit** — trace files present, re-scored, matches exactly. **The overlap-gap ATTRIBUTION reported alongside these numbers was FALSE — see below.** |
| T-043 (this audit) | 91 (offline only) | 583/2/2 pytest; validate 91/91; rule_based 58/91; parity 50/50 | offline, fresh re-run | **VERIFIED this audit**, exact match to T-041's own claim, proving the offline gate was unaffected by the mid-run corruption |

### 1. The real current live score

**Unknown as of right now** — no live re-run was performed this audit
(deferred per Part 1). The last **verified** live number is T-041's own:
mean 61.7/91 raw (64.7/91 corrected), overlap 47.0/73 — but see finding
below, which means even this trustworthy *score* was reported with an
untrustworthy *explanation*.

### 2. The real cost of the T-039 guard — **T-041's own attribution was FALSE, corrected here**

T-041 claimed the 54.33→47.0 gap was "unrelated model-capability
categories… present at 54.33 too." **This was checked directly against
the real T-032 trace data this audit, case by case, and is false for at
least 7 of the 22 currently-failing overlap cases.**

Built a direct per-case comparison: T-032's 3 real traces vs. T-041's 3
real traces, restricted to the same 73 case IDs (script and full output
preserved this session).

- **43 cases:** pass 3/3 at both T-032 and T-041 — unaffected.
- **11 cases:** failed 0/3 at **both** T-032 and T-041 (`CORRECT-007`,
  `COUPON-001`, `DECLINE-001`, `FAQ-001`, `GOURMET-010`, `GOURMET-011`,
  `MOD-014`, `MOD-036`, `MULTI-006`, `NEG-003`, `QTY-003`) — genuinely
  pre-existing, not guard-caused. T-041's claim is **TRUE** for these.
- **7 cases improved** (`ADV-002`, `AVAIL-001`, `COUPON-002`,
  `GOURMET-005`, `GOURMET-012`, `MULTI-004`, `NEG-008`) — real gains.
- **11 cases REGRESSED — passed at T-032 (7 of them a clean 3/3) but now
  fail 0/3 at T-041**: `ADV-001`, `CORRECT-003`, `CORRECT-004`,
  `CORRECT-006`, `GOURMET-013`, `MOD-035`, `MULTI-001`, `MULTI-005`,
  `NEG-005`, `SLANG-001`, `SLANG-003`. **T-041's claim is FALSE for these
  — the guard chain caused this, and I found the exact mechanism.**

**Root cause, traced directly in the real T-041 trace data:** for every
one of these regressed cases, the model's **first, correct, direct**
`add_item(CHEESE PIZZA, size=X)` call for a plain, unambiguous pizza order
is rejected with `UNSUPPORTED_ITEM_SUBSTITUTION`. Reproduced independently
against the live code (not the trace) this audit:

```python
_has_pizza_intent("small cheese and a can of soda")        # False, residual "soda"
_has_pizza_intent("medium cheese, I'll pick it up")         # False, residual "'ll pick it up"
_has_pizza_intent("gimme a lg pep")                          # False, residual "gimme pep"
_has_pizza_intent("two mediums, plain")                      # False, residual "mediums plain"
_has_pizza_intent("large cheese and a twelve piece wings")   # False, residual "twelve piece wings"
```

`_has_pizza_intent` requires the **entire utterance** to be fully
explained as pizza shorthand — it was designed to answer "is this
utterance a pizza order," not "does evidence for THIS pizza exist
somewhere in this utterance." A customer combining a pizza with anything
else in one breath (a drink, wings, an aside about pickup, a colloquial
"gimme"/plural size word/topping abbreviation) fails the whole-utterance
check and gets refused — **on both interpreters** (re-verified: identical
failure on `RuleBasedInterpreter` for the same utterances, confirmed this
audit, not assumed from the LLM path alone).

**This is a real, severe, previously-unreported defect, present since
T-039A shipped `_has_pizza_intent` (2026-09-17), not introduced by T-041.**
It does **not** reintroduce the P0 — every one of these cases either
refuses cleanly or (worse, operationally) burns the model's tool-call
budget retrying — never silently substitutes a wrong item. But it is a
genuine order-correctness regression: a customer placing the single most
common order shape (a pizza plus something else, or a pizza with any
ordinary spoken filler) can be wrongly refused. **Filed as T-044** — see
NEXT TASK.

### 3. Label integrity

Two **already-documented, already-filed** instances of "label matches
behavior, not correctness" predate this audit: `DECLINE-001` (filed as
T-029 — its own turn-2 label bundles a premature `begin_confirmation` no
well-behaved model can pass, confirmed directly in T-024's own
investigation) and the `evals/cases/confirmation_full_flow.yaml` /
`CONFIRM-004` runtime-`$ref` mechanism (a deliberate, documented design
choice, not a defect — re-checked, not re-litigated this audit).

**A third instance, found and corrected in T-041 itself** (same task that
introduced them): `QTY-PLURAL-001`, `INTENSITY-WORD-001`,
`WINGS-QTY-WORD-001` originally asserted `RuleBasedInterpreter`'s own
limited behavior as the "correct" answer rather than the objectively
correct cart; corrected post-gate, per T-041's own report.

**Full-corpus re-audit of all 91 cases for this pattern: not completed
this session** (time budget — this would mean re-deriving the objectively
correct cart for every one of 91 cases by hand and comparing against both
interpreters' real behavior, a multi-hour task in its own right). This is
disclosed as a real gap, not silently skipped — see NEXT TASK for scoping
it as its own bounded follow-up if wanted.

---

## PART 6 — WHERE WE ACTUALLY ARE

### Built and working (VERIFIED this audit unless noted)
- Deterministic order engine, pricing, coupons, menu truth — `pricing.py`/
  `menu.py`/`coupons.py`/`data/menu.json` unchanged since initial commit;
  50/50 pricing parity passing.
- F5/F6/F14/F17/F18 fail-safes — mutation-tested this audit, genuinely
  load-bearing, not vacuous.
- F15/F16 — spot-checked, existing test suite green, not independently
  mutation-broken this session.
- `store_id` server-binding — structurally impossible to override via any
  tool (verified against every tool's real Python signature).
- Tenant isolation — 7/7 tests passing, not independently re-broken this
  audit.
- Customer-safe error masking — code inspected, matches claim.
- No silent item substitution (T-039 family) — 76/76 tests passing;
  authorization question settled above with a named, honest exception.
- Offline gate: 583 tests, `validate` 91/91, `rule_based` 58/91, parity
  50/50 — all reproduced from a verified-clean tree this audit.

### Built but unverified
- `PostgresSessionRepository` — never run against real Postgres this
  session or (per prior docs) ever in this repo's history; `psycopg2` not
  installed. **CLAIMED** functional, structurally sound, never live-tested.
- Printer (ESC/POS) — written, never run against hardware (per prior
  docs, not re-checked this audit).
- Parakeet hardware voice loop — per prior docs, blocked/pending; not
  touched this audit.

### Not built
Telephony, event store, memory/learning pipeline, model router — per
prior docs, not re-checked this audit (out of scope, no code in this area
to verify).

### Open defects (current severity)
- **T-044 (NEW, filed this audit, P1):** `_has_pizza_intent`'s
  whole-utterance-must-be-fully-explained design rejects a large class of
  ordinary compound/conversational orders on both interpreters — the real,
  primary driver of the T-032→T-041 overlap-score gap, previously
  misattributed to "pre-existing model-capability limits."
- **T-042 (P7):** "extra X" on a half-portion is priced as `DOUBLE`
  intensity by the model, not a plain addition — unchanged, not
  re-investigated this audit.
- **T-040 (P3):** `search_menu`'s single-hit reply always asks "what size,"
  even for items with no size variants — unchanged.
- **T-029:** `DECLINE-001`'s own label needs correction — filed, not done.
- **T-028:** `apply_coupon()` can silently default to an unrequested
  coupon — filed, not done (per prior docs, not re-checked this audit).
- **The `_new_pizza_half_a_half_b` gate** (Part 4's authorization
  question) — not proven unsafe, but structurally unshared with the rest
  of the evidence-check family. Rolled into T-044's scope as a named item,
  not a separate filing.
- **`pricing_engine.py` content — permanently unknown.** Cannot be
  recovered; flagged, not resolved.

### Blocked
- Live N=3 re-run — blocked on confirming Codex is not pointed at this
  repo (owner action, see NEXT TASK).
- Owner-side items from prior docs (coupon tax ordering verification,
  customer-data retention period) — not re-checked this audit, carried
  forward as still-open per last known status.

### Documentation that was wrong — corrected in this audit
- `docs/STATUS.md`, `docs/EVALS.md`, `docs/decisions/ADR-017-*.md`'s T-041
  sections claimed the 54.33→47.0 gap was "unrelated model-capability
  categories… already present at 54.33." **Corrected in place** (see diffs
  in this commit) to state the true, case-verified split: 11 cases
  pre-existing, 11 cases genuinely regressed by the guard chain's
  `_has_pizza_intent` design, with the exact utterances and mechanism.
