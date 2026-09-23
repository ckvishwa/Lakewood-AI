"""OpenAI wire-format and interpreter safety tests. No live API calls."""
import copy
import io
import json
import urllib.error

import pytest

from lakewood import orders as oe
from lakewood.chat import new_session, run_turn, make_interpreter, llm_adapter
from lakewood.interpreter import LLMInterpreter, _TOOL_SCHEMAS
from lakewood.llm_provider import (
    AnthropicProvider, OpenAIProvider, ProviderCallError, ProviderConfigError,
    make_provider,
)


def response(*items, status='completed'):
    return {'status': status, 'output': list(items),
            'usage': {'input_tokens': 12, 'output_tokens': 7}}


def call(name, args, cid='call_1'):
    return {'type': 'function_call', 'id': 'fc_' + cid, 'call_id': cid,
            'name': name, 'arguments': json.dumps(args)}


def text():
    return response({'type': 'message', 'role': 'assistant',
                     'content': [{'type': 'output_text', 'text': 'Done.'}]})


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key-never-live')
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
    queued.append(response(call('add_item', {'item': 'PIZZA', 'size': 'large'})))
    provider = OpenAIProvider(timeout=9)
    r = provider.complete('instructions', [{'role': 'user', 'content': 'Pizza'}], _TOOL_SCHEMAS)
    assert r.tool_uses == [{'id': 'call_1', 'name': 'add_item',
                            'input': {'item': 'PIZZA', 'size': 'large'}}]
    assert (r.input_tokens, r.output_tokens) == (12, 7)
    assert r.continue_turn
    req, body, timeout = requests[0]
    assert req.full_url == 'https://api.openai.com/v1/responses'
    assert req.get_header('Authorization') == 'Bearer test-key-never-live'
    assert body['model'] == 'gpt-6-astra'
    assert body['instructions'] == 'instructions'
    assert body['parallel_tool_calls'] is False
    assert body['store'] is False
    assert timeout == 9
    for tool in body['tools']:
        assert tool['type'] == 'function' and tool['strict'] is False
        assert not {'price', 'total', 'tax', 'store_id', 'quote_id', 'idempotency_key'} & set(tool['parameters']['properties'])


def test_sequential_calls_results_and_reasoning_replay(http):
    queued, requests = http
    reasoning = {'type': 'reasoning', 'id': 'rs_1', 'summary': [], 'encrypted_content': 'opaque'}
    queued.extend([
        response(reasoning, call('add_item', {'item': 'PIZZA', 'size': 'large'})),
        response(call('add_modifier', {'line_id': 'L1', 'modifier': 'pepperoni'}, 'call_2')),
        text(), text(),
    ])
    chat = new_session()
    result = run_turn(chat, LLMInterpreter(OpenAIProvider()), 'Large pepperoni.')
    assert [c['tool'] for c in result.calls] == ['add_item', 'add_modifier']
    assert len(chat.session.order.lines) == 1
    assert chat.session.order.lines[0].toppings[0].name == 'PEPPERONI'
    second_input = requests[1][1]['input']
    assert reasoning in second_input
    output = next(x for x in second_input if x.get('type') == 'function_call_output')
    assert output['call_id'] == 'call_1'
    assert json.loads(output['output']) == result.calls[0]['result']
    run_turn(chat, LLMInterpreter(OpenAIProvider()), 'Thanks')
    assert requests[3][1]['input'][-1] == {'role': 'user', 'content': 'Thanks'}


@pytest.mark.parametrize('name,args,code', [
    ('delete_all_orders', {}, 'UNKNOWN_TOOL'),
    ('add_item', {'item': 'PIZZA', 'price': 1}, 'BAD_ARGS'),
    ('add_item', {'item': 'PIZZA', 'tax': 1}, 'BAD_ARGS'),
    ('add_item', {'item': 'PIZZA', 'store_id': 'other'}, 'BAD_ARGS'),
    ('add_item', {'item': 42}, 'BAD_ARGS'),
    ('add_item', {'item': 'PIZZA', 'quantity': True}, 'BAD_ARGS'),
    ('add_item', {}, 'BAD_ARGS'),
])
def test_unknown_or_invalid_tool_fails_closed(http, name, args, code):
    http[0].extend([response(call(name, args)), text()])
    chat = new_session()
    r = run_turn(chat, LLMInterpreter(OpenAIProvider()), 'Pizza')
    assert r.calls[0]['result']['code'] == code
    assert chat.session.order.lines == []


@pytest.mark.parametrize('bad', [b'not-json', b'\xff', [], {},
    response(status='incomplete'), {'status': 'completed', 'output': None},
    response(call('add_item', [])), response(call('add_item', None)),
    response({'type': 'function_call', 'call_id': 'a', 'name': 'add_item', 'arguments': '{'}),
    response(call('add_item', {}), call('add_item', {})),
])
def test_malformed_response_fails_closed(http, bad):
    http[0].append(bad)
    chat = new_session()
    before = copy.deepcopy(chat)
    before.session.turn += 1  # T-019: run_turn counts the attempt even on failure
    interpreter = LLMInterpreter(OpenAIProvider())
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
        http[0].append(response(call('add_modifier', {'line_id': 'L1', 'modifier': 'mushroom'})))
    failure = (urllib.error.HTTPError('url', 429, 'Too many requests', {},
               io.BytesIO(b'test-key-never-live')) if error == 'http' else TimeoutError('timed out'))
    http[0].append(failure)
    interp = LLMInterpreter(OpenAIProvider())
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
    http[0].extend([response(call('begin_confirmation', {})), text()])
    run_turn(chat, LLMInterpreter(OpenAIProvider()), 'Yes, place it.')
    assert chat.session.state == 'AWAITING_CONFIRMATION'

    http[0].extend([
        response(call('confirm_order', {'quote_id': 'fabricated', 'idempotency_key': 'injected'}, 'c2')),
        text(),
    ])
    result = run_turn(chat, LLMInterpreter(OpenAIProvider()), 'Go ahead.')
    assert chat.session.state == 'CONFIRMED'
    assert result.calls[-1]['args'] == {'quote_id': quote}
    assert chat.session.idempotency == {}


def test_fabricated_quote_without_real_quote_is_refused(http):
    http[0].extend([response(call('confirm_order', {'quote_id': 'fake'})), text()])
    chat = new_session()
    result = run_turn(chat, LLMInterpreter(OpenAIProvider()), 'Place it')
    assert chat.session.state != 'CONFIRMED'
    assert result.calls[0]['result']['status'] == 'error'


def test_bounded_tool_loop_discards_staged_changes(http):
    http[0].extend(response(call('add_item', {'item': 'PIZZA', 'size': 'large'}, str(i))) for i in range(12))
    chat = new_session()
    interp = LLMInterpreter(OpenAIProvider())
    run_turn(chat, interp, 'Pizza')
    assert '12 rounds' in interp.last_provider_error
    assert chat.session.order.lines == [] and chat.llm_history == []


def test_missing_key_and_config(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('LAKEWOOD_LLM_PROVIDER', 'openai')
    with pytest.raises(ProviderConfigError, match='OPENAI_API_KEY'):
        make_provider()
    monkeypatch.setenv('LAKEWOOD_LLM_PROVIDER', 'typo')
    with pytest.raises(ProviderConfigError, match='LAKEWOOD_LLM_PROVIDER'):
        make_provider()


def test_selection_and_model_override(http, monkeypatch):
    monkeypatch.setenv('LAKEWOOD_INTERPRETER', 'llm')
    monkeypatch.setenv('LAKEWOOD_LLM_PROVIDER', 'openai')
    monkeypatch.setenv('LAKEWOOD_LLM_MODEL', 'configured-model')
    assert isinstance(make_interpreter().provider, OpenAIProvider)
    assert make_provider().model == 'configured-model'
    assert OpenAIProvider(model='explicit-model').model == 'explicit-model'


def test_anthropic_default_wire_path_still_works(http, monkeypatch):
    monkeypatch.delenv('LAKEWOOD_LLM_PROVIDER', raising=False)
    monkeypatch.delenv('LAKEWOOD_LLM_MODEL', raising=False)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fake-anthropic')
    http[0].append({'content': [{'type': 'tool_use', 'id': 'tu1', 'name': 'add_item',
                               'input': {'item': 'PIZZA', 'size': 'large'}}],
                    'stop_reason': 'tool_use'})
    provider = make_provider()
    assert isinstance(provider, AnthropicProvider)
    chat = new_session()
    result = run_turn(chat, LLMInterpreter(provider), 'Large pizza')
    assert result.calls[0]['result']['status'] == 'ok'
    req, body, _ = http[1][0]
    assert req.full_url == 'https://api.anthropic.com/v1/messages'
    assert body['model'] == 'claude-haiku-4-5-20251001'
    assert 'input_schema' in body['tools'][0]


def test_eval_provider_failure_is_not_scored_as_success(http, monkeypatch):
    monkeypatch.setenv('LAKEWOOD_LLM_PROVIDER', 'openai')
    http[0].append(TimeoutError('offline'))
    with pytest.raises(ProviderCallError, match='offline'):
        llm_adapter(new_session().session)('Pizza')
