"""Experiential Labs ("Luna") wire-format and interpreter safety tests. No
live API calls — everything here runs offline against a scripted urlopen
fake, same pattern as tests/test_openai_provider.py.

Chat Completions shape (NOT OpenAI's Responses API — see the divergence
note in lakewood/llm_provider.py::ExperientialProvider): choices[0].message
with an optional tool_calls list, usage.prompt_tokens/completion_tokens.
"""
import copy
import io
import json
import urllib.error

import pytest

from lakewood import orders as oe
from lakewood.chat import new_session, run_turn, make_interpreter, llm_adapter
from lakewood.interpreter import LLMInterpreter, _TOOL_SCHEMAS
from lakewood.llm_provider import (
    ExperientialProvider, ProviderCallError, ProviderConfigError, make_provider,
)


def response(message, finish_reason='stop', usage=None):
    return {'choices': [{'message': message, 'finish_reason': finish_reason}],
            'usage': usage or {'prompt_tokens': 15, 'completion_tokens': 9,
                               'total_tokens': 24, 'cost': 0.000418, 'is_byok': False}}


def tool_call(name, args, cid='call_1'):
    return {'id': cid, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}


def assistant_with_calls(*calls):
    return {'role': 'assistant', 'content': None, 'tool_calls': list(calls)}


def assistant_text(text='Done.'):
    return {'role': 'assistant', 'content': text}


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv('EXPLABS_API_KEY', 'test-key-never-live')
    queued, requests = [], []

    def urlopen(req, timeout):
        requests.append((req, json.loads(req.data), timeout))
        assert queued, 'Unexpected extra HTTP call'
        r = queued.pop(0)
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(r if isinstance(r, bytes) else json.dumps(r).encode())

    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    return queued, requests


def test_wire_request_response_mapping(http):
    queued, requests = http
    queued.append(response(assistant_with_calls(
        tool_call('add_item', {'item': 'PIZZA', 'size': 'large'})),
        finish_reason='tool_calls'))
    provider = ExperientialProvider(timeout=9)
    r = provider.complete('instructions', [{'role': 'user', 'content': 'Pizza'}], _TOOL_SCHEMAS)
    assert r.tool_uses == [{'id': 'call_1', 'name': 'add_item',
                            'input': {'item': 'PIZZA', 'size': 'large'}}]
    assert (r.input_tokens, r.output_tokens) == (15, 9)
    assert r.continue_turn
    assert r.cost_usd == 0.000418  # captured verbatim from usage.cost, never estimated
    req, body, timeout = requests[0]
    assert req.full_url == 'https://api.experientiallabs.ai/v1/chat/completions'
    assert req.get_header('Authorization') == 'Bearer test-key-never-live'
    assert body['model'] == 'gpt-5.6-luna'
    assert body['messages'][0] == {'role': 'system', 'content': 'instructions'}
    assert body['messages'][1] == {'role': 'user', 'content': 'Pizza'}
    assert timeout == 9
    for tool in body['tools']:
        assert tool['type'] == 'function'
        props = tool['function']['parameters']['properties']
        assert not {'price', 'total', 'tax', 'store_id', 'quote_id', 'idempotency_key'} & set(props)


def test_missing_usage_cost_is_unavailable_not_zero(http):
    queued, requests = http
    queued.append(response(assistant_text(), usage={'prompt_tokens': 5, 'completion_tokens': 2}))
    provider = ExperientialProvider()
    r = provider.complete('instructions', [{'role': 'user', 'content': 'Pizza'}], _TOOL_SCHEMAS)
    assert r.cost_usd is None
    assert (r.input_tokens, r.output_tokens) == (5, 2)


def test_sequential_calls_results_round_trip(http):
    queued, requests = http
    queued.extend([
        response(assistant_with_calls(
            tool_call('add_item', {'item': 'PIZZA', 'size': 'large'})),
            finish_reason='tool_calls'),
        response(assistant_with_calls(
            tool_call('add_modifier', {'line_id': 'L1', 'modifier': 'pepperoni'}, 'call_2')),
            finish_reason='tool_calls'),
        response(assistant_text()), response(assistant_text()),
    ])
    chat = new_session()
    result = run_turn(chat, LLMInterpreter(ExperientialProvider()), 'Large pepperoni.')
    assert [c['tool'] for c in result.calls] == ['add_item', 'add_modifier']
    assert len(chat.session.order.lines) == 1
    assert chat.session.order.lines[0].toppings[0].name == 'PEPPERONI'
    second_messages = requests[1][1]['messages']
    # The assistant turn that proposed call_1 round-trips verbatim...
    assert any(m.get('role') == 'assistant' and m.get('tool_calls') for m in second_messages)
    # ...followed by its real tool result, addressed by the same call id.
    tool_msg = next(m for m in second_messages if m.get('role') == 'tool')
    assert tool_msg['tool_call_id'] == 'call_1'
    assert json.loads(tool_msg['content']) == result.calls[0]['result']
    run_turn(chat, LLMInterpreter(ExperientialProvider()), 'Thanks')
    assert requests[3][1]['messages'][-1] == {'role': 'user', 'content': 'Thanks'}


@pytest.mark.parametrize('name,args,code', [
    ('delete_all_orders', {}, 'UNKNOWN_TOOL'),
    ('add_item', {'item': 'PIZZA', 'price': 1}, 'BAD_ARGS'),
    ('add_item', {'item': 'PIZZA', 'store_id': 'other'}, 'BAD_ARGS'),
    ('add_item', {'item': 42}, 'BAD_ARGS'),
    ('add_item', {'item': 'PIZZA', 'quantity': True}, 'BAD_ARGS'),
    ('add_item', {}, 'BAD_ARGS'),
])
def test_unknown_or_invalid_tool_fails_closed(http, name, args, code):
    http[0].extend([
        response(assistant_with_calls(tool_call(name, args)), finish_reason='tool_calls'),
        response(assistant_text()),
    ])
    chat = new_session()
    r = run_turn(chat, LLMInterpreter(ExperientialProvider()), 'Pizza')
    assert r.calls[0]['result']['code'] == code
    assert chat.session.order.lines == []


@pytest.mark.parametrize('bad', [
    b'not-json', b'\xff', [], {},
    {'choices': []}, {'choices': [{}]},  # message key missing entirely
    response(assistant_with_calls(
        {'id': 'c1', 'type': 'function', 'function': {'arguments': '{}'}})),  # no function name
    response(assistant_with_calls(
        {'id': 'c1', 'type': 'function', 'function': {'name': 'add_item', 'arguments': '{'}})),  # malformed JSON args
    response(assistant_with_calls(tool_call('add_item', []))),
    response(assistant_with_calls(tool_call('add_item', None))),
    response(assistant_with_calls(tool_call('add_item', {}), tool_call('add_item', {}))),  # dup id
])
def test_malformed_response_fails_closed(http, bad):
    http[0].append(bad)
    chat = new_session()
    before = copy.deepcopy(chat)
    before.session.turn += 1  # T-019: run_turn counts the attempt even on failure
    interpreter = LLMInterpreter(ExperientialProvider())
    result = run_turn(chat, interpreter, 'Pizza')
    assert interpreter.last_provider_error
    assert result.calls == []
    assert chat == before


@pytest.mark.parametrize('after_mutation', [False, True])
@pytest.mark.parametrize('error', ['http', 'timeout'])
def test_provider_failure_preserves_cart_quote_history(http, after_mutation, error):
    chat = new_session()
    oe.add_item(chat.session, 'PIZZA', size='large')
    oe.request_quote(chat.session)
    before = copy.deepcopy(chat)
    before.session.turn += 1  # T-019: run_turn counts the attempt even on failure
    session = chat.session
    order = session.order
    if after_mutation:
        http[0].append(response(assistant_with_calls(
            tool_call('add_modifier', {'line_id': 'L1', 'modifier': 'mushroom'})),
            finish_reason='tool_calls'))
    failure = (urllib.error.HTTPError('url', 429, 'Too many requests', {},
               io.BytesIO(b'test-key-never-live')) if error == 'http' else TimeoutError('timed out'))
    http[0].append(failure)
    interp = LLMInterpreter(ExperientialProvider())
    r = run_turn(chat, interp, 'Add mushroom')
    assert r.calls == [] and interp.last_provider_error
    assert 'test-key-never-live' not in r.reply
    assert chat == before
    assert chat.session is session and chat.session.order is order


def test_fabricated_quote_and_idempotency_never_authorize(http):
    # T-019/F14: begin_confirmation and confirm_order now land in separate
    # turns, so this drives two run_turn calls instead of one.
    chat = new_session()
    oe.add_item(chat.session, 'PIZZA', size='large')
    quote = oe.request_quote(chat.session)['quote_id']
    interp = LLMInterpreter(ExperientialProvider())

    http[0].extend([
        response(assistant_with_calls(tool_call('begin_confirmation', {})), finish_reason='tool_calls'),
        response(assistant_text()),
    ])
    run_turn(chat, interp, 'Yes, place it.')
    assert chat.session.state == 'AWAITING_CONFIRMATION'

    http[0].extend([
        response(assistant_with_calls(tool_call(
            'confirm_order', {'quote_id': 'fabricated', 'idempotency_key': 'injected'}, 'c2')),
            finish_reason='tool_calls'),
        response(assistant_text()),
    ])
    result = run_turn(chat, interp, 'Go ahead.')
    assert chat.session.state == 'CONFIRMED'
    assert result.calls[-1]['args'] == {'quote_id': quote}
    assert chat.session.idempotency == {}


def test_bounded_tool_loop_discards_staged_changes(http):
    http[0].extend(response(assistant_with_calls(
        tool_call('add_item', {'item': 'PIZZA', 'size': 'large'}, str(i))),
        finish_reason='tool_calls') for i in range(12))
    chat = new_session()
    interp = LLMInterpreter(ExperientialProvider())
    run_turn(chat, interp, 'Pizza')
    assert '12 rounds' in interp.last_provider_error
    assert chat.session.order.lines == [] and chat.llm_history == []


def test_missing_key_and_config(monkeypatch):
    monkeypatch.delenv('EXPLABS_API_KEY', raising=False)
    monkeypatch.setenv('LAKEWOOD_LLM_PROVIDER', 'experiential')
    with pytest.raises(ProviderConfigError, match='EXPLABS_API_KEY'):
        make_provider()


def test_selection_and_model_override(http, monkeypatch):
    monkeypatch.setenv('LAKEWOOD_INTERPRETER', 'llm')
    monkeypatch.setenv('LAKEWOOD_LLM_PROVIDER', 'experiential')
    monkeypatch.setenv('LAKEWOOD_LLM_MODEL', 'configured-model')
    assert isinstance(make_interpreter().provider, ExperientialProvider)
    assert make_provider().model == 'configured-model'
    assert ExperientialProvider(model='explicit-model').model == 'explicit-model'


def test_eval_provider_failure_is_not_scored_as_success(http, monkeypatch):
    monkeypatch.setenv('LAKEWOOD_LLM_PROVIDER', 'experiential')
    http[0].append(TimeoutError('offline'))
    with pytest.raises(ProviderCallError, match='offline'):
        llm_adapter(new_session().session)('Pizza')


def test_no_type_leakage_in_domain_modules():
    """T-018 acceptance: no Experiential-specific type/name anywhere outside
    llm_provider.py — the same static check T-013 ran for OpenAI/Anthropic."""
    import lakewood.menu as menu_mod
    import lakewood.orders as orders_mod
    import lakewood.pricing as pricing_mod
    import inspect
    for mod in (menu_mod, orders_mod, pricing_mod):
        src = inspect.getsource(mod)
        assert 'experiential' not in src.lower()
        assert 'luna' not in src.lower()
        assert 'explabs' not in src.lower()
