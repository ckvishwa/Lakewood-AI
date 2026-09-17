# ADR-010 — Experiential Labs ("Luna") is a fourth `LLMProvider`, wired as a separate adapter, not a config toggle on `OpenAIProvider`

**Status:** Accepted · 2026-09-15

## Context

T-018 asked for a fourth branch of `make_provider()` for a provider referred
to only as "Experiential/Luna" in the task brief. That name, and any
connection details for it, did not exist anywhere in this repository —
`lakewood/`, `docs/`, `tests/`, env, `requirements.txt` — before this task.
Per `CLAUDE.md`'s own anti-fabrication rules ("never claim unverified
behavior works," credentials/config "from env only," "pin the exact model
string" — a codename like "Luna" is not one), guessing a base URL, auth
scheme, or model string for a live external API would have been exactly the
kind of fabrication this project explicitly forbids. Connection details were
obtained directly from the user before any adapter code was written:

- Base URL: `https://api.experientiallabs.ai/v1`
- Credential env var: `EXPLABS_API_KEY`
- Exact model string to pin: `gpt-5.6-luna`
- Wire protocol: "standard OpenAI Chat Completions request/response shape
  with tool calling" (the user's own words)

The task's own instruction anticipated the key decision this ADR records:
*"If Experiential is OpenAI-wire-compatible, reuse `OpenAIProvider`'s
translation rather than duplicating it... If it diverges, write a real
adapter and say where it diverges."*

## The divergence

`lakewood/llm_provider.py::OpenAIProvider` does not implement the Chat
Completions API. It implements OpenAI's newer **Responses** API:
`POST {base}/responses`, a flat `input`/`output` item list, `function_call`/
`function_call_output` block types, `reasoning`/`encrypted_content` replay,
and `usage.input_tokens`/`output_tokens`. The user described Experiential's
actual wire shape as the **Chat Completions** API:
`POST {base}/chat/completions`, a `messages` array, an assistant message
carrying a `tool_calls` list (function name + JSON-string arguments), tool
results as `{"role": "tool", "tool_call_id": ..., "content": ...}` entries,
`choices[0].message`/`finish_reason`, and `usage.prompt_tokens`/
`completion_tokens`. Both APIs are real, both are commonly described as
"OpenAI-compatible" by the industry, and they are **not the same wire
format** — sending one shape's body to the other's endpoint fails outright.

## Decision

**A separate `ExperientialProvider` class**, not a subclass or config flag on
`OpenAIProvider`. Divergence is documented in the class's own docstring so
the next person touching this file doesn't try to collapse the two later
without re-deriving this reasoning. What IS reused, per the task's own
"don't duplicate for no reason" instruction:

- The exact same stdlib-`urllib`-only HTTP plumbing, timeout handling, and
  `ProviderConfigError`/`ProviderCallError` split every other provider in
  this module already uses.
- Message-translation logic that mirrors `OllamaProvider`'s Chat-Completions-
  shaped dialect (already implemented in this file, since Ollama's
  `/api/chat` is also messages/tool_calls/role="tool" shaped) rather than
  inventing a third independent translation from scratch.
- The identical `LLMInterpreter`/`run_turn`/`_valid_tool_args` execution path
  every other provider goes through — zero domain code changes, exactly as
  required. `ExperientialProvider`/`EXPLABS_API_KEY`/`gpt-5.6-luna` appear
  nowhere in `menu.py`, `orders.py`, or `pricing.py` (asserted by
  `tests/test_experiential_provider.py::test_no_type_leakage_in_domain_modules`).

## Consequences

- `LAKEWOOD_LLM_PROVIDER=experiential` selects it; `EXPLABS_API_KEY` missing
  fails closed with a message naming exactly that variable — no silent
  fallback to `openai`, `anthropic`, `ollama`, or the rule-based interpreter.
- `LAKEWOOD_LLM_MODEL` overrides the pinned default (`gpt-5.6-luna`), same as
  every other provider.
- `evals/runner.py score --adapter llm` and
  `LAKEWOOD_INTERPRETER=llm python -m lakewood.chat` work against it with no
  code changes beyond `make_provider()`'s new branch — the eval harness and
  chat sandbox are provider-agnostic by construction (T-013).
- **Not part of the blocking gate.** `tests/test_evals.py::
  test_rule_based_interpreter_meets_baseline` (T-016/ADR-009) is unaffected
  — it only ever exercises `rule_based_adapter`. A live Experiential run
  stays exactly what every other `score --adapter llm` result is: a
  separately reported, non-blocking, honestly-labeled baseline (see
  `docs/STATUS.md`/`docs/EVALS.md` T-018 sections).
- No cost-per-token figure is computed or reported for this provider (or any
  other) — `ProviderResponse.cost_usd` stays `None` until a real, sourced
  rate is added; see the field's own docstring and `docs/EVALS.md`'s existing
  "no cost estimate without a verified applicable rate" rule. Token counts
  and latency ARE captured and reported (T-018 Part 2).
