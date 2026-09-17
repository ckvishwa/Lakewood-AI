"""Ollama wire-format and interpreter safety tests. No live model call, no
network — everything here runs offline against a scripted urlopen fake."""
import io
import json

import pytest

from lakewood import orders as oe
from lakewood.chat import new_session, run_turn, make_interpreter, llm_adapter
from lakewood.interpreter import LLMInterpreter
from lakewood.llm_provider import (
    OllamaProvider, ProviderCallError, ProviderConfigError, make_provider,
)

INSTALLED = {"models": [{"name": "gemma4:26b"}, {"name": "llama3.1:8b"},
                        {"name": "qwen2.5-coder:7b"}]}


def tags_response():
    return io.BytesIO(json.dumps(INSTALLED).encode())


def chat_response(tool_calls=None, content=""):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"model": "gemma4:26b", "done": True, "message": msg,
            "prompt_eval_count": 100, "eval_count": 20}


def call(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


@pytest.fixture
def http(monkeypatch):
    """First call every test makes is always the /api/tags model check inside
    OllamaProvider.__init__ — auto-answered from INSTALLED unless a test
    queues its own ("tags", ...) response first."""
    queued, requests = [], []

    def urlopen(req, timeout=None):
        body = json.loads(req.data) if req.data else None
        requests.append((req, timeout, body))
        if req.full_url.endswith("/api/tags"):
            if queued and queued[0][0] == "tags":
                _, r = queued.pop(0)
                if isinstance(r, Exception):
                    raise r
                return io.BytesIO(json.dumps(r).encode())
            return tags_response()
        assert queued, "Unexpected extra HTTP call"
        kind, r = queued.pop(0)
        assert kind == "chat"
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(json.dumps(r).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return queued, requests


def _queue_chat(http, response):
    http[0].append(("chat", response))


def _queue_tags(http, response):
    http[0].append(("tags", response))


# --- A1/A2: config, model pinning, startup fail-loud ------------------------

def test_missing_model_fails_loud_with_installed_list(http, monkeypatch):
    monkeypatch.setenv("LAKEWOOD_LLM_MODEL", "not-installed:99b")
    with pytest.raises(ProviderConfigError, match="not-installed:99b") as exc:
        OllamaProvider()
    assert "gemma4:26b" in str(exc.value)
    assert "llama3.1:8b" in str(exc.value)


def test_unreachable_host_fails_loud(http, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:1")

    def broken(*a, **k):
        raise ConnectionRefusedError("nobody home")
    monkeypatch.setattr("urllib.request.urlopen", broken)
    with pytest.raises(ProviderConfigError, match="Could not reach Ollama"):
        OllamaProvider()


def test_default_model_and_host(http):
    p = OllamaProvider()
    assert p.model == "gemma4:26b"
    assert p.host == "http://localhost:11434"


def test_model_override_via_env(http, monkeypatch):
    monkeypatch.setenv("LAKEWOOD_LLM_MODEL", "llama3.1:8b")
    assert OllamaProvider().model == "llama3.1:8b"


def test_make_provider_selects_ollama(http, monkeypatch):
    monkeypatch.setenv("LAKEWOOD_LLM_PROVIDER", "ollama")
    assert isinstance(make_provider(), OllamaProvider)


# --- A3.1: num_ctx always explicit, never omitted ---------------------------

def test_num_ctx_always_sent_explicitly(http):
    _queue_chat(http, chat_response(content="hi"))
    provider = OllamaProvider()
    provider.complete("sys", [{"role": "user", "content": "hi"}], [])
    req, timeout, body = http[1][-1]
    assert body["options"]["num_ctx"] == 8192  # default, never absent


def test_num_ctx_configurable_via_env(http, monkeypatch):
    monkeypatch.setenv("LAKEWOOD_OLLAMA_NUM_CTX", "32768")
    _queue_chat(http, chat_response(content="hi"))
    provider = OllamaProvider()
    provider.complete("sys", [{"role": "user", "content": "hi"}], [])
    _, _, body = http[1][-1]
    assert body["options"]["num_ctx"] == 32768


# --- A3.3: determinism — temperature 0 + fixed seed by default -------------

def test_temperature_and_seed_default_to_deterministic(http):
    _queue_chat(http, chat_response(content="hi"))
    OllamaProvider().complete("sys", [{"role": "user", "content": "hi"}], [])
    _, _, body = http[1][-1]
    assert body["options"]["temperature"] == 0
    assert body["options"]["seed"] == 0


# --- A3.2: malformed small-model tool calls fail closed, never crash -------

@pytest.mark.parametrize("tool_calls", [
    [{"function": {"name": "add_item", "arguments": "{not valid json"}}],
    [{"function": {"name": "add_item", "arguments": "[1,2,3]"}}],
    [{"function": {"arguments": {"item": "PIZZA"}}}],       # missing name
    [{"function": {"name": 42, "arguments": {}}}],           # name not a string
    [{"no_function_key": True}],
    ["just a string, not a dict"],
    None,
])
def test_malformed_tool_call_shapes_are_skipped_not_crashed(http, tool_calls):
    _queue_chat(http, chat_response(tool_calls=tool_calls, content="hmm"))
    provider = OllamaProvider()
    r = provider.complete("sys", [{"role": "user", "content": "hi"}], [])
    assert r.tool_uses == []
    assert r.text == "hmm"


def test_valid_string_encoded_arguments_are_parsed(http):
    _queue_chat(http, chat_response(tool_calls=[
        call("add_item", '{"item": "CHEESE PIZZA", "size": "large"}')]))
    r = OllamaProvider().complete("sys", [{"role": "user", "content": "hi"}], [])
    assert r.tool_uses == [{"id": "call_0", "name": "add_item",
                            "input": {"item": "CHEESE PIZZA", "size": "large"}}]


def test_dict_encoded_arguments_pass_through(http):
    _queue_chat(http, chat_response(tool_calls=[
        call("add_item", {"item": "CHEESE PIZZA", "size": "large"})]))
    r = OllamaProvider().complete("sys", [{"role": "user", "content": "hi"}], [])
    assert r.tool_uses[0]["input"] == {"item": "CHEESE PIZZA", "size": "large"}


def test_incomplete_response_raises_provider_call_error(http):
    _queue_chat(http, {"done": False})
    with pytest.raises(ProviderCallError):
        OllamaProvider().complete("sys", [{"role": "user", "content": "hi"}], [])


# --- End-to-end through the real interpreter/executor -----------------------

def test_full_add_item_reaches_real_domain_state(http):
    _queue_chat(http, chat_response(tool_calls=[
        call("add_item", {"item": "CHEESE PIZZA", "size": "large"})]))
    _queue_chat(http, chat_response(content="Anything else?"))
    chat = new_session()
    run_turn(chat, LLMInterpreter(OllamaProvider()), "large pizza")
    assert chat.session.order.lines[0].size == "LARGE"


def test_hallucinated_tool_name_never_reaches_domain_state(http):
    _queue_chat(http, chat_response(tool_calls=[call("delete_everything", {})]))
    _queue_chat(http, chat_response(content="oops"))
    chat = new_session()
    result = run_turn(chat, LLMInterpreter(OllamaProvider()), "hi")
    assert chat.session.order.lines == []
    assert result.calls[0]["result"]["code"] == "UNKNOWN_TOOL"


def test_price_argument_from_a_small_model_is_rejected(http):
    _queue_chat(http, chat_response(tool_calls=[
        call("add_item", {"item": "CHEESE PIZZA", "size": "large", "price": 500})]))
    _queue_chat(http, chat_response(content="ok"))
    chat = new_session()
    result = run_turn(chat, LLMInterpreter(OllamaProvider()), "cheap pizza")
    assert chat.session.order.lines == []
    assert result.calls[0]["result"]["code"] == "BAD_ARGS"


def test_fabricated_quote_id_from_local_model_is_ignored(http):
    # T-019/F14: begin_confirmation and confirm_order now land in separate
    # turns, so this drives two run_turn calls instead of one.
    chat = new_session()
    oe.add_item(chat.session, "PIZZA", size="large")
    real_quote_id = oe.request_quote(chat.session)["quote_id"]
    interp = LLMInterpreter(OllamaProvider())

    _queue_chat(http, chat_response(tool_calls=[call("begin_confirmation", {})]))
    _queue_chat(http, chat_response(content="ok"))
    run_turn(chat, interp, "yes place it")
    assert chat.session.state == "AWAITING_CONFIRMATION"

    _queue_chat(http, chat_response(tool_calls=[
        call("confirm_order", {"quote_id": "totally-made-up"})]))
    _queue_chat(http, chat_response(content="done"))
    result = run_turn(chat, interp, "go ahead")
    assert chat.session.state == "CONFIRMED"
    confirm = [c for c in result.calls if c["tool"] == "confirm_order"][0]
    assert confirm["args"]["quote_id"] == real_quote_id


def test_provider_call_failure_leaves_state_untouched(http):
    chat = new_session()
    oe.add_item(chat.session, "PIZZA", size="large")
    before_lines = len(chat.session.order.lines)
    _queue_chat(http, TimeoutError("model took too long"))
    interp = LLMInterpreter(OllamaProvider())
    result = run_turn(chat, interp, "add pepperoni")
    assert len(chat.session.order.lines) == before_lines
    assert interp.last_provider_error
    assert chat.llm_history == []


def test_no_ollama_types_leak_past_the_provider_module():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "lakewood"
    for name in ("menu.py", "pricing.py", "orders.py", "interpreter.py"):
        src = (root / name).read_text()
        assert "ollama" not in src.lower()


def test_selection_via_make_interpreter(http, monkeypatch):
    monkeypatch.setenv("LAKEWOOD_INTERPRETER", "llm")
    monkeypatch.setenv("LAKEWOOD_LLM_PROVIDER", "ollama")
    assert isinstance(make_interpreter().provider, OllamaProvider)


def test_eval_adapter_propagates_ollama_infra_failure(http, monkeypatch):
    monkeypatch.setenv("LAKEWOOD_LLM_PROVIDER", "ollama")
    _queue_chat(http, ConnectionResetError("model server dropped connection"))
    with pytest.raises(ProviderCallError):
        llm_adapter(new_session().session)("Pizza")
