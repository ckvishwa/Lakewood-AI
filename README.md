# Lakewood Voice

Phone-ordering agent for Lakewood Pizza (562 Lakewood Rd, Waterbury CT), running
against a MicroWorks PrISM 8.1.153 POS.

**Design rule: the model emits intent, the server owns every fact.** The LLM
cannot express a price, reach another store, or confirm an order the customer
did not agree to. Those are code properties, not prompt instructions.

## Status

| Layer | State |
|---|---|
| Pricing engine | **Done** — 50 real register totals reproduced exactly |
| Order engine + tool surface | **Done** — 10 PRD acceptance cases + 13 fail-safes |
| Ticket printer (ESC/POS) | **Written**, untested against hardware |
| L1 eval harness | **Done** — 71 golden cases, label validator + ratcheted interpreter-regression gate (ADR-009) |
| Store rules (hours, delivery, tiers) | **Done** — owner-confirmed |
| Coupons | **Done** — 4 offers, never stack, code printed on the ticket |
| Text order sandbox (`lakewood.chat`), rule-based mode | **Done** — headless, deterministic, no LLM/credentials |
| Real LLM interpreter (Anthropic / OpenAI) | **Built, unit-tested — live benchmark blocked** on a funded API key, see below |
| Local voice loop | **Built, offline-tested; Parakeet hardware E2E pending** |
| Production telephony | Not started |
| Reconciliation report | Not started |

```
pip install -r requirements.txt   # dev/eval deps only — lakewood/ itself stays stdlib-only
python -m pytest -q               # 323 passed, 2 xfail (documented anomalies) —
                                   #   includes the T-016 interpreter-regression gate, see below
python evals/runner.py validate   # 71/71 — LABEL correctness only, no interpreter runs (ADR-009)
./scripts/check.sh                # both — run before every commit (needs `python3` on PATH)
```

**Every eval number below states which of three different things it
proves — they are not interchangeable, and conflating them is exactly how
a real pricing bug once shipped through a "63/63" green corpus (T-015):**
1. **`validate` ("N/N valid")** — hand-authored tool calls, replayed against
   the real engine, produce the labeled cart. Proves the *label* is
   internally correct. **Never runs any interpreter, for any case.**
2. **`score --adapter rule_based` ("N/M")** — the real, deterministic
   `RuleBasedInterpreter` run against the actual customer-language text, a
   ratcheted regression gate (`tests/test_evals.py`, offline, no
   credentials, part of `pytest -q` above). Proves the *interpreter* didn't
   regress. Not a model-accuracy number — `RuleBasedInterpreter` is a fixed
   pattern matcher, not an NLU system.
3. **`score --adapter llm` ("N/M")** — a real model (Anthropic/OpenAI/
   Ollama). The only one of the three that is ever an actual accuracy
   claim, and only when it has actually been run against a live provider —
   see `docs/EVALS.md` for current status per provider.

## Try it — text order sandbox

```
python -m lakewood.chat            # interactive
python -m lakewood.chat --debug    # also prints [TOOL]/[STATE]/[CART]/[TOTAL] traces
```

Type ordinary customer language — "large pepperoni", "mushroom only on one
half", "actually replace the mushroom with sausage", "what's my total",
"yes place it". Every reply is driven by the real `orders.TOOLS` and the
real deterministic engine; nothing here computes a price or invents a menu
item. Type `quit` to exit.

`LAKEWOOD_INTERPRETER` selects the interpreter:

- **`rule_based`** (default) — deterministic, no network, no credentials.
  Good enough for the required demo flows, not a general NLU system.
- **`llm`** — uses `LAKEWOOD_LLM_PROVIDER`: `anthropic` (default;
  `ANTHROPIC_API_KEY`, model `claude-haiku-4-5-20251001`), `openai`
  (`OPENAI_API_KEY`, model `gpt-6-astra`), `experiential` (`EXPLABS_API_KEY`,
  model `gpt-5.6-luna` — Experiential Labs' Chat Completions API, T-018; see
  `docs/decisions/ADR-010-experiential-provider-adapter.md` for why it's a
  separate adapter from `openai` despite both calling themselves "OpenAI-
  compatible"), or `ollama` (**free, local, no API key** — see below).
  Override any model with `LAKEWOOD_LLM_MODEL`. Missing keys/models or
  invalid provider names fail clearly; there is no automatic fallback.

### Local model tier (free, offline — no API key)

```bash
LAKEWOOD_INTERPRETER=llm LAKEWOOD_LLM_PROVIDER=ollama python -m lakewood.chat --debug
```

Requires [Ollama](https://ollama.com) running locally
(`ollama serve`, default `http://localhost:11434`, override with
`OLLAMA_HOST`) with the pinned model already pulled:
`LAKEWOOD_LLM_MODEL` (default `gemma4:26b`) must be `ollama pull`-ed first —
this never auto-selects a different installed model, since that would
silently shift the eval baseline between machines. Config knobs:
`LAKEWOOD_OLLAMA_NUM_CTX` (default `8192` — Ollama's own default is 2048
regardless of model, a silent-truncation trap; always sent explicitly),
`LAKEWOOD_OLLAMA_TEMPERATURE`/`LAKEWOOD_OLLAMA_SEED` (both default `0`, for
determinism). Reasoning and known local-model gaps:
`docs/decisions/ADR-007-local-model-tier.md`, `docs/EVALS.md` "Local model
tier baselines" — **a local ~8B–26B model is not a production-accuracy
substitute; real smoke-test transcripts there show both candidate models
failing the simplest required flow.**

Local speech-to-text (`lakewood/stt/`, feeds text into this same
interpreter layer, nothing downstream changes):

```bash
LAKEWOOD_STT_PROVIDER=parakeet python -m lakewood.stt.eval
```

Parakeet is the measured local-pilot choice (ADR-016); faster-whisper remains
a CPU fallback (ADR-008). Start the already-installed NeMo environment in WSL
first, keeping the model warm:

```bash
cd ~/lakewood-stt/NeMo
./.venv/bin/python /mnt/d/Projects/Ai/scripts/parakeet_server.py \
  --warmup-audio /home/ckvis/lakewood-stt/lakewood_test.wav
```

Then, in Windows PowerShell:

```powershell
cd D:\Projects\Ai
curl.exe http://127.0.0.1:8765/healthz
$env:LAKEWOOD_STT_PROVIDER = 'parakeet'
$env:LAKEWOOD_PARAKEET_URL = 'http://127.0.0.1:8765'
$env:LAKEWOOD_INTERPRETER = 'rule_based'
python -m lakewood.voice
```

The server binds to localhost only. It must not be exposed to another machine
without authentication and TLS. `faster-whisper` still requires
`pip install faster-whisper`; the default `fake` provider needs nothing and is
used for offline tests. The STT eval reports
domain-weighted accuracy (topping names, sizes, quantities, negations,
half/left/right scope) per category, not generic word-error-rate — see
`docs/decisions/ADR-016-parakeet-local-stt.md`.

PowerShell configuration (supply the API key through your environment securely):

```powershell
$env:LAKEWOOD_INTERPRETER = 'llm'
$env:LAKEWOOD_LLM_PROVIDER = 'openai'
$env:LAKEWOOD_LLM_MODEL = 'gpt-6-astra'
python -m lakewood.chat --debug
```

OpenAI uses the [Responses function-calling API](https://developers.openai.com/api/docs/guides/function-calling)
with stdlib HTTP and the same restricted tool schemas as Anthropic. See the
[official Astra model documentation](https://developers.openai.com/api/docs/models/gpt-6-astra).
No model can supply an authoritative price or confirmation credential. Tool
rounds are bounded, and provider failures discard the staged turn.
**Live Astra verification is BLOCKED: OPENAI_API_KEY was absent on 2026-09-08.**
Codex access does not supply application API credentials. Follow the single
connectivity gate before any benchmark in `docs/NEXT_TASKS.md` T-013c.

`python evals/runner.py score --adapter rule_based` (default) or `--adapter
llm` runs the corresponding interpreter against the 71-case golden corpus
and reports how many it reaches the labeled final cart for. `rule_based`'s
number is also a live pytest gate now (`tests/test_evals.py::
test_rule_based_interpreter_meets_baseline`, ADR-009) — a ratcheted floor
(35/71 as of 2026-09-08), not a target; only the `llm` adapter's result is
ever an actual model-accuracy number. State which one produced a given
number (see `docs/EVALS.md`).

Pricing/menu parity: `tests/test_pricing_parity.py` is 50/50 across single-item,
modifier, half-and-half, multi-item, delivery, and coupon categories — see
`docs/STATUS.md` for what's fully verified vs. still pending a register check.
Golden NL eval corpus: 71 cases across `evals/cases/*.yaml`. `validate`:
100% label-valid (never runs an interpreter — see the eval-numbers note
above). `score --adapter rule_based`: 35/71, gated against regression.
Includes a genuine two-specialty `HALF_AND_HALF` SKU case, a full
runtime-bound `CONFIRMED` flow, and negation/removal/lite handling
(T-015) — see `docs/EVALS.md` for what each number does and doesn't prove.

## For agents

Start at [`CLAUDE.md`](./CLAUDE.md), then [`docs/STATUS.md`](./docs/STATUS.md)
and [`docs/NEXT_TASKS.md`](./docs/NEXT_TASKS.md). The repository is the source of
truth; conversation history is not.

## Layout

```
data/menu.json  single source of truth for every price — integer cents, stable
                item IDs, every number verified against a screenshot
lakewood/
  menu.py      loads data/menu.json. Nothing else defines a price.
  pricing.py   deterministic totals in integer cents. The LLM never touches this.
  orders.py    session, state machine, and the 16 tools the model may call
  coupons.py   four offers, mutually exclusive, engine picks the best one
  printer.py   Epson TM-T88V ESC/POS + real print confirmation
  config.py    store_id resolved from the inbound DID, never from the model
  interpreter.py  text -> structured ToolCall(s). RuleBasedInterpreter + LLMInterpreter.
  llm_provider.py Anthropic / OpenAI / Ollama adapters; stdlib HTTP, no SDKs.
  chat.py      headless text sandbox: `python -m lakewood.chat`
  stt/         local STT: interface, fake, Parakeet client, faster-whisper fallback
tests/         regression suite — every case is an observed POS total
  test_pricing_parity.py   the release-gate suite: 50 cases across 6 categories
evals/         L1 golden transcripts (text in, tool calls out)
docs/          state machine, eval spec, OPEN-QUESTIONS
```

## The parts that are easy to get wrong

**Two half-and-half pricing modes.** A regular SKU (`CHEESE PIZZA` + Half/Half 2)
charges every topping. The `HALF & HALF` SKU charges only the larger half. The
same pizza is $21.00 or $18.00 depending on which button staff press. Owner
confirmed: HALF & HALF is for specialty/specialty only.

**Unknown prices fail loud.** Every tier is confirmed today, but the guard stays
wired: a POS button with no price raises rather than guessing, and
`add_modifier` returns `PRICE_UNKNOWN`. A crash in the logs beats a wrong total
on a customer's phone.

**After-hours orders are captured, not promised.** The store is closed Mondays
and after 10 PM. Those calls currently go unanswered — pure lost revenue. The
agent takes the order, discloses the hold in the readback ("held for Tuesday at
11 AM — not tonight"), and the ticket is queued rather than printed to a dark
kitchen.

**Confirmation is guarded three ways.** `confirm_order` validates the quote id,
the cart hash, and the quote TTL. Any cart mutation drops the order back to
BUILDING and kills the quote, so a customer can never be charged for a cart they
did not hear read back.

**CONFIRMED is not terminal.** The order continues to SENT_TO_STORE and
STORE_ACKED. The printer answers real-time status queries, so a ticket that
did not print becomes a FAILED_DISPATCH that pages a human rather than an order
nobody knows about.

## Next

1. Test `printer.py` against the new printer when it arrives — **not during service**
2. Grow the eval corpus to ~250 cases; every production failure becomes a case
3. Voice stack (buy, don't build) + telephony
4. Nightly reconciliation: AI quote vs the PrISM ticket total

See `docs/OPEN-QUESTIONS.md` — five items, all answerable by a person.
# Lakewood-AI
