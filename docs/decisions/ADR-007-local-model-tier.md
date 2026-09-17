# ADR-007 — Local model tier for dev/eval: Ollama behind the existing provider interface

**Status:** Accepted · 2026-09-08

## Context

`LLMInterpreter` (T-013) works and is unit-tested, but its only live-verified
path is Anthropic, and that path is itself blocked — the only credential
available in the dev environment has no funded API balance (`docs/STATUS.md`
"T-013 milestone", reproducible HTTP 400). Every dev-loop iteration and every
eval run against a real model has been stuck behind that same blocker.

Independently, a concurrent session generalized `AnthropicProvider` into a
`LLMProvider` Protocol with `make_provider()` reading `LAKEWOOD_LLM_PROVIDER`,
and added `OpenAIProvider` — plus a proper bounded multi-round tool loop with
deepcopy-staged sessions (a failed/aborted turn never partially mutates real
order state) and explicit JSON-schema argument validation before any domain
call. That work landed before this task and is reused as-is.

## Decision

Add `OllamaProvider` as a third branch of the existing `make_provider()`
pattern. No new interface — the task's own instruction ("if it doesn't fit,
fix the provider, not the interface") didn't need invoking; the existing
`complete(system, messages, tools) -> ProviderResponse` contract fit Ollama's
`/api/chat` exactly once the wire-format translation was written.

**Discovery (A1), local Ollama install:**

| Model | Params | Quant | Context | Tools | Verdict |
|---|---|---|---|---|---|
| `gemma4:26b` | 25.2B | Q4_K_M | 262144 | yes (+thinking, vision) | general instruct — candidate |
| `llama3.1:8b` | 8.0B | Q4_K_M | 131072 | yes | general instruct — candidate |
| `qwen2.5-coder:7b` | 7.6B | Q4_K_M | 32768 | yes | **excluded** — code-tuned, worse at conversational intent per task guidance |
| `nomic-embed-text` | 137M | F16 | 2048 | embedding only | not a candidate; future menu-search work |
| `all-minilm` | 23M | F16 | 512 | embedding only | not a candidate; future menu-search work |

Pinned default: `LAKEWOOD_LLM_MODEL=gemma4:26b` (largest context, native tool
support, general instruct). **Not auto-selected at runtime** — `OllamaProvider`
fails loudly at construction if the configured model isn't installed, listing
what is, so the eval baseline never silently shifts between machines.

**Known pitfalls, handled (A3):**

1. **`num_ctx` defaults to 2048 regardless of model.** Verified via
   `/api/show gemma4:26b` — no `num_ctx` in reported parameters, confirming
   Ollama's silent-truncation default applies unless overridden. `options.num_ctx`
   is set on every single request (`tests/test_ollama_provider.py::
   test_num_ctx_always_sent_explicitly`), never conditionally omitted.
   Runtime confirmation: `GET /api/ps` while a request was in flight reported
   `context_length: 8192` — the configured value, not Ollama's 2048 default —
   the closest verification the API surface allows (there is no per-response
   field that echoes the context window used).
2. **Malformed tool calls from small models** — string-encoded JSON arguments,
   non-dict arguments, missing function name, wrong argument types (observed
   live: `llama3.1:8b` sent `quantity: "1"` as a string). Two independent
   layers: `OllamaProvider.complete()` skips any tool_call shape it can't
   parse into `{name: str, input: dict}` (never crashes, never invents a
   call); `LLMInterpreter._valid_tool_args()` (pre-existing, reused) then
   type-checks every surviving call against the real tool's JSON schema
   before it ever reaches `orders.py`. No unbounded loop — the existing
   12-round cap applies uniformly across providers.
3. **Determinism** — `temperature=0`, `seed=0` by default, both explicit on
   every request, both overridable via `LAKEWOOD_OLLAMA_TEMPERATURE`/
   `LAKEWOOD_OLLAMA_SEED` for anyone who wants to study variance later.

## Rationale

- Free, offline, unblocks the dev/eval loop immediately — no credential,
  no cost, no dependency on T-013b resolving first.
- Same interface, same safety boundary (schema-derived tools, hidden
  `quote_id`/`idempotency_key`, deepcopy-staged sessions) as Anthropic/OpenAI
  — provider swap is a config change, not a domain change, which is the
  actual point of the boundary this ADR reuses rather than reinvents.
- stdlib `urllib` only, matching the existing two providers — no new pip
  dependency for a single JSON endpoint.

## Consequences

- A local ~8B–26B model is **not** a production accuracy substitute — see
  `docs/EVALS.md` "Local model tier baselines" for the real, honest gap
  (both smoke-tested models failed to complete even the simplest required
  flow without a correctable error). The 98–99% target in `CLAUDE.md`
  belongs to whatever provider is used in production; passing on Ollama is
  not evidence toward it.
- Per-request latency on this hardware (~60–90s/turn observed) makes Ollama
  fine for correctness/safety testing and CI-adjacent smoke checks, not for
  interactive latency work — expected and out of scope for what this ADR
  needed to prove.
- Provider choice for **production** remains deferred (ADR-004) and is
  unaffected by this decision — this is dev/eval tooling only.

## Status

Accepted. `OllamaProvider` implemented, 26 tests passing offline
(`tests/test_ollama_provider.py`), live-verified against real local models
(see `docs/STATUS.md` "Local Model Tier milestone" for the actual manual
transcripts and benchmark numbers).
