"""Runtime LLM adapters: Anthropic Messages, OpenAI Responses, Ollama chat,
Experiential Labs ("Luna") Chat Completions.

All implement the same narrow complete(system, messages, tools) contract.
Vendor wire formats stay here; runtime HTTP uses only stdlib urllib.
Credentials come from provider-specific environment variables, never Codex.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
OPENAI_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-6-astra"
EXPLABS_API_URL = "https://api.experientiallabs.ai/v1/chat/completions"
DEFAULT_EXPLABS_MODEL = "gpt-5.6-luna"


class ProviderConfigError(Exception):
    """Missing/invalid configuration (e.g. no API key). Callers must fail
    closed on this — never silently substitute a different provider."""


class ProviderCallError(Exception):
    """The call itself failed: network, timeout, non-2xx, malformed JSON.
    An infrastructure failure — distinct from the model producing a
    correctness mistake, and must be reported/counted separately."""


@dataclass
class ProviderResponse:
    tool_uses: list = field(default_factory=list)   # [{"id","name","input"}]
    text: str | None = None
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_seconds: float = 0.0
    raw_content: list = field(default_factory=list)  # for conversation history
    continue_turn: bool = False  # request another round after executing tools
    # T-018: None means "not reported for this request," never a guess.
    # Populated from a provider's OWN returned cost figure when it sends one
    # (Experiential's usage.cost); no adapter in this file computes cost from
    # a hardcoded $/token rate table — see docs/EVALS.md "No cost estimate
    # without a verified applicable rate."
    cost_usd: float | None = None


class LLMProvider(Protocol):
    """Existing narrow contract; history uses role/content envelopes.

    Assistant content is opaque to the interpreter. Tool results use
    type/tool_use_id/content blocks; adapters translate these on the wire.
    """

    def complete(self, system: str, messages: list, tools: list) -> ProviderResponse: ...


def make_provider() -> LLMProvider:
    name = os.environ.get("LAKEWOOD_LLM_PROVIDER", "anthropic")
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openai":
        return OpenAIProvider()
    if name == "ollama":
        return OllamaProvider()
    if name == "experiential":
        return ExperientialProvider()
    raise ProviderConfigError(
        f"Unknown LAKEWOOD_LLM_PROVIDER={name!r}; choose 'anthropic', 'openai', "
        f"'ollama', or 'experiential'.")


class OpenAIProvider:
    """Responses API via stdlib HTTP. No SDK, retries, or provider fallback."""

    def __init__(self, model: str | None = None, timeout: float = 30.0):
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self.api_key:
            raise ProviderConfigError(
                "OPENAI_API_KEY is not set. LAKEWOOD_LLM_PROVIDER=openai "
                "requires an application API key in the environment; "
                "Codex session access is not an application API key. No fallback is used.")
        self.model = model or os.environ.get("LAKEWOOD_LLM_MODEL", DEFAULT_OPENAI_MODEL)
        self.timeout = timeout

    def complete(self, system: str, messages: list, tools: list) -> ProviderResponse:
        inputs = []
        for message in messages:
            content = message["content"]
            if message["role"] == "assistant":
                # Preserve ALL output items, including reasoning/encrypted state.
                inputs.extend(content)
            elif isinstance(content, list):
                inputs.extend({"type": "function_call_output",
                               "call_id": block["tool_use_id"],
                               "output": block["content"]} for block in content)
            else:
                inputs.append({"role": message["role"], "content": content})
        body = {
            "model": self.model, "instructions": system, "input": inputs,
            "tools": [{"type": "function", "name": t["name"],
                       "description": t["description"],
                       "parameters": t["input_schema"], "strict": False} for t in tools],
            "parallel_tool_calls": False, "max_output_tokens": 4096,
            "store": False, "include": ["reasoning.encrypted_content"],
        }
        req = urllib.request.Request(
            OPENAI_API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace").replace(self.api_key, "[REDACTED]")[:300]
            raise ProviderCallError(f"OpenAI API HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            detail = str(e).replace(self.api_key, "[REDACTED]")
            raise ProviderCallError(f"OpenAI API call failed: {detail}") from None
        except (ValueError, UnicodeError):
            raise ProviderCallError("OpenAI API returned malformed JSON") from None

        try:
            if raw.get("status") != "completed":
                raise ValueError("response was not completed")
            content = raw["output"]
            if not isinstance(content, list):
                raise ValueError("output is not a list")
            uses, texts, ids = [], [], set()
            for block in content:
                if block["type"] == "function_call":
                    cid, name = block["call_id"], block["name"]
                    if not isinstance(cid, str) or not cid or cid in ids or not isinstance(name, str):
                        raise ValueError("invalid function call identity")
                    ids.add(cid)
                    args = json.loads(block["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError("function arguments must be an object")
                    uses.append({"id": cid, "name": name, "input": args})
                elif block["type"] == "message":
                    texts.extend(b["text"] for b in block["content"] if b["type"] == "output_text")
                elif block["type"] != "reasoning":
                    raise ValueError("unsupported output item")
            usage = raw.get("usage") or {}
            return ProviderResponse(
                tool_uses=uses, text="\n".join(texts) or None,
                stop_reason=raw["status"], raw_content=content,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                latency_seconds=time.monotonic() - start,
                continue_turn=bool(uses))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderCallError("OpenAI API returned malformed or incomplete response") from None


class ExperientialProvider:
    """Experiential Labs' "Luna" model via stdlib HTTP. T-018.

    Marketed as "OpenAI-compatible," but the actual wire shape is the
    standard **Chat Completions** API (POST {base}/chat/completions,
    messages[] with assistant tool_calls / role="tool" results,
    choices[0].message, usage.prompt_tokens/completion_tokens) — NOT the
    Responses API OpenAIProvider above implements (POST {base}/responses,
    input[]/output[] items, reasoning/encrypted_content, function_call_output
    blocks). Those are two different real wire formats that both happen to
    call themselves "OpenAI-compatible"; reusing OpenAIProvider's translation
    verbatim would silently send the wrong body shape. Per this task's own
    instruction ("if it diverges, write a real adapter and say where"), this
    is a separate class — divergence noted above — not a subclass or a
    branch inside OpenAIProvider. What IS reused: the same stdlib-only HTTP
    plumbing, the same config/credential/error-handling shape as every other
    provider in this module, and message-translation logic that mirrors
    OllamaProvider's Chat-Completions-shaped dialect below rather than
    inventing new translation logic a third time.
    """

    def __init__(self, model: str | None = None, timeout: float = 30.0):
        self.api_key = os.environ.get("EXPLABS_API_KEY", "").strip()
        if not self.api_key:
            raise ProviderConfigError(
                "EXPLABS_API_KEY is not set. LAKEWOOD_LLM_PROVIDER=experiential "
                "requires an Experiential Labs application API key in the "
                "environment. No fallback to a different provider is used.")
        self.model = model or os.environ.get("LAKEWOOD_LLM_MODEL", DEFAULT_EXPLABS_MODEL)
        self.timeout = timeout

    def complete(self, system: str, messages: list, tools: list) -> ProviderResponse:
        chat_messages = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if m["role"] == "assistant":
                # Opaque, provider-owned shape round-tripped verbatim (see
                # module docstring) — this provider stores a plain Chat-
                # Completions-shaped assistant message dict here (see
                # raw_content below), including the exact tool_call ids the
                # matching role="tool" messages must reference.
                chat_messages.append(content)
            elif isinstance(content, list):
                for block in content:
                    chat_messages.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block["content"],
                    })
            else:
                chat_messages.append({"role": m["role"], "content": content})

        body = {
            "model": self.model,
            "messages": chat_messages,
            "tools": [{"type": "function", "function": {
                "name": t["name"], "description": t["description"],
                "parameters": t["input_schema"]}} for t in tools],
            "tool_choice": "auto",
            "temperature": 0,
        }
        req = urllib.request.Request(
            EXPLABS_API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace").replace(self.api_key, "[REDACTED]")[:300]
            raise ProviderCallError(f"Experiential API HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            detail = str(e).replace(self.api_key, "[REDACTED]")
            raise ProviderCallError(f"Experiential API call failed: {detail}") from None
        except (ValueError, UnicodeError):
            raise ProviderCallError("Experiential API returned malformed JSON") from None
        latency = time.monotonic() - start

        try:
            choices = raw["choices"]
            if not isinstance(choices, list) or not choices:
                raise ValueError("no choices in response")
            msg = choices[0]["message"]
            finish_reason = choices[0].get("finish_reason")

            tool_uses, ids = [], set()
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    raise ValueError("malformed tool_call entry")
                fn = tc.get("function")
                cid = tc.get("id")
                if (not isinstance(fn, dict) or not isinstance(cid, str)
                        or not cid or cid in ids):
                    raise ValueError("invalid tool_call identity")
                name = fn.get("name")
                if not isinstance(name, str):
                    raise ValueError("tool_call missing function name")
                args_raw = fn.get("arguments", "{}")
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                if not isinstance(args, dict):
                    raise ValueError("function arguments must be an object")
                ids.add(cid)
                tool_uses.append({"id": cid, "name": name, "input": args})

            # Stored verbatim (content + tool_calls only) so it round-trips
            # into the NEXT request exactly as this API needs it back,
            # including the same tool_call ids referenced above.
            assistant_msg = {"role": "assistant", "content": msg.get("content"),
                             "tool_calls": msg.get("tool_calls") or []}
            usage = raw.get("usage") or {}
            # Experiential's own usage block includes a provider-computed
            # `cost` (and `is_byok`) — captured verbatim when present, never
            # estimated from a hardcoded rate table (see ProviderResponse.
            # cost_usd's own docstring). A response that omits it leaves
            # cost_usd None, meaning "not reported this request," not $0.
            raw_cost = usage.get("cost")
            cost_usd = float(raw_cost) if isinstance(raw_cost, (int, float)) else None
            return ProviderResponse(
                tool_uses=tool_uses, text=msg.get("content") or None,
                stop_reason=finish_reason, raw_content=assistant_msg,
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                latency_seconds=latency,
                continue_turn=bool(tool_uses),
                cost_usd=cost_usd)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderCallError(
                "Experiential API returned malformed or incomplete response") from None


DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:26b"
# Ollama's own default is 2048 regardless of what the model actually supports
# (see docs/decisions/ADR-006-local-model-tier.md A3.1) — a silent truncation
# that looks exactly like a reasoning failure. Never omit this from a request.
DEFAULT_OLLAMA_NUM_CTX = 8192


class OllamaProvider:
    """
    Local Ollama /api/chat, stdlib HTTP only. No SDK — Ollama's REST API is a
    single JSON endpoint, same rationale as Anthropic/OpenAI above.

    Config (all explicit, nothing auto-detected at request time — see A1):
      OLLAMA_HOST              default http://localhost:11434
      LAKEWOOD_LLM_MODEL       default 'gemma4:26b' — must already be pulled;
                                fails loudly at construction with the list of
                                what IS installed if not, never silently picks
                                a different one
      LAKEWOOD_OLLAMA_NUM_CTX  default 8192 — always sent explicitly (A3.1)
      LAKEWOOD_OLLAMA_TEMPERATURE  default 0 (determinism, A3.3)
      LAKEWOOD_OLLAMA_SEED         default 0 (determinism, A3.3)
    """

    def __init__(self, model: str | None = None, timeout: float = 120.0):
        self.host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/")
        self.model = model or os.environ.get("LAKEWOOD_LLM_MODEL", DEFAULT_OLLAMA_MODEL)
        self.num_ctx = int(os.environ.get("LAKEWOOD_OLLAMA_NUM_CTX", DEFAULT_OLLAMA_NUM_CTX))
        self.temperature = float(os.environ.get("LAKEWOOD_OLLAMA_TEMPERATURE", "0"))
        self.seed = int(os.environ.get("LAKEWOOD_OLLAMA_SEED", "0"))
        self.timeout = timeout
        self._check_model_installed()

    def _check_model_installed(self) -> None:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise ProviderConfigError(
                f"Could not reach Ollama at {self.host} ({e}). Is `ollama serve` "
                f"running? Set OLLAMA_HOST to override the default "
                f"({DEFAULT_OLLAMA_HOST}).") from e
        installed = [m["name"] for m in raw.get("models", [])]
        if self.model not in installed:
            raise ProviderConfigError(
                f"Configured model {self.model!r} (LAKEWOOD_LLM_MODEL, default "
                f"{DEFAULT_OLLAMA_MODEL!r}) is not installed on {self.host}. "
                f"Installed models: {installed or '(none)'}. Run "
                f"`ollama pull {self.model}`, or point LAKEWOOD_LLM_MODEL at one "
                f"of the installed models above. Never auto-selects a different "
                f"model — that would silently shift the eval baseline.")

    def complete(self, system: str, messages: list, tools: list) -> ProviderResponse:
        ollama_messages = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if m["role"] == "assistant":
                # Opaque, round-tripped verbatim — see module docstring.
                ollama_messages.append(content)
            elif isinstance(content, list):
                for block in content:
                    ollama_messages.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id"),
                        "content": block["content"],
                    })
            else:
                ollama_messages.append({"role": m["role"], "content": content})

        body = {
            "model": self.model,
            "messages": ollama_messages,
            "tools": [{"type": "function", "function": {
                "name": t["name"], "description": t["description"],
                "parameters": t["input_schema"]}} for t in tools],
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,       # A3.1 — never omitted
                "temperature": self.temperature,  # A3.3 — determinism
                "seed": self.seed,
            },
        }
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=json.dumps(body).encode("utf-8"),
            method="POST", headers={"content-type": "application/json"})
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise ProviderCallError(f"Ollama API HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderCallError(f"Ollama API call failed: {e}") from e
        except json.JSONDecodeError as e:
            raise ProviderCallError(f"Ollama API returned malformed JSON: {e}") from e
        latency = time.monotonic() - start

        if raw.get("done") is False or "message" not in raw:
            raise ProviderCallError(f"Ollama response incomplete: {raw!r}"[:300])
        msg = raw["message"]

        # A3.2 — small models emit malformed tool calls: missing function
        # name/arguments, arguments as a JSON *string* instead of an object,
        # or a non-list tool_calls field. Every shape is caught here and
        # turned into a skipped call, never a crash and never something that
        # reaches domain state — LLMInterpreter's own schema validation is
        # the second, independent layer of defense on top of this one.
        tool_uses = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
                continue
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            if not isinstance(args, dict):
                continue
            tool_uses.append({"id": tc.get("id") or f"call_{i}",
                              "name": fn["name"], "input": args})

        text = msg.get("content") or None
        assistant_msg = {"role": "assistant", "content": msg.get("content", ""),
                         "tool_calls": msg.get("tool_calls") or []}
        return ProviderResponse(
            tool_uses=tool_uses,
            text=text,
            stop_reason="tool_use" if tool_uses else "end_turn",
            input_tokens=raw.get("prompt_eval_count", 0),
            output_tokens=raw.get("eval_count", 0),
            latency_seconds=latency,
            raw_content=assistant_msg,
            continue_turn=bool(tool_uses),
        )


class AnthropicProvider:
    def __init__(self, model: str | None = None, timeout: float = 30.0):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ProviderConfigError(
                "ANTHROPIC_API_KEY is not set. Real LLM mode "
                "(LAKEWOOD_INTERPRETER=llm) requires it in the environment. "
                "This never falls back to a different provider or to the "
                "rule-based interpreter automatically — set the key or use "
                "LAKEWOOD_INTERPRETER=rule_based explicitly.")
        self.model = model or os.environ.get("LAKEWOOD_LLM_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def complete(self, system: str, messages: list, tools: list) -> ProviderResponse:
        body = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            ANTHROPIC_API_URL, data=data, method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            })
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise ProviderCallError(f"Anthropic API HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderCallError(f"Anthropic API call failed: {e}") from e
        except json.JSONDecodeError as e:
            raise ProviderCallError(f"Anthropic API returned malformed JSON: {e}") from e
        latency = time.monotonic() - start

        content = raw.get("content", [])
        tool_uses = [{"id": b.get("id", ""), "name": b.get("name", ""),
                      "input": b.get("input", {})}
                     for b in content if b.get("type") == "tool_use"]
        texts = [b["text"] for b in content if b.get("type") == "text"]
        usage = raw.get("usage", {})
        return ProviderResponse(
            tool_uses=tool_uses,
            text=texts[0] if texts else None,
            stop_reason=raw.get("stop_reason"),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            latency_seconds=latency,
            raw_content=content,
        )
