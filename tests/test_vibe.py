"""Native upstream loop integration; real skills/tools, deterministic model replies."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from wechat_panel.ai_models import Agent
from wechat_panel.app import create_app
from wechat_panel.vibe import VibeService, installation
from wechat_panel.vibe_catalog import REVISION, TOOLS, SKILLS

HEADERS = {'X-Panel-Request': '1'}


@pytest.fixture
def native():
    try:
        return installation()
    except (ValueError, OSError):
        pytest.skip('Install the pinned Vibe-Trading runtime for native integration tests')


def wait(client, rid, target='completed'):
    end = time.monotonic() + 45
    while time.monotonic() < end:
        r = client.get('/api/ai/runs/' + rid).json()
        if r['status'] == target:
            return r
        if r['status'] == 'failed':
            pytest.fail(r['error'])
        time.sleep(.1)
    pytest.fail(str(r))


def post(client, path, data):
    r = client.post(path, json=data, headers=HEADERS)
    assert r.status_code < 300, r.text
    return r.json()


def make_agent(client, **kwargs):
    p = post(client, '/api/ai/config/providers', dict(name='Vibe test model', base_url='http://127.0.0.1:19999/v1', model='native-test'))
    return post(client, '/api/ai/config/agents', dict(name='Vibe test', kind='vibe', provider_id=p['id'],
                vibe_tools=['options_pricing'], vibe_skills=['technical-basic'], **kwargs))


def test_vibe_config_rejects_unadapted_capabilities():
    for field, values in [('vibe_tools', ['bash']), ('vibe_tools', ['place_order']),
                          ('vibe_skills', ['../../private']), ('tool_ids', ['arbitrary'])]:
        with pytest.raises(ValueError):
            Agent(name='Vibe', kind='vibe', provider_id='p', **{field: values})
    with pytest.raises(ValueError):
        Agent(name='Vibe', kind='vibe')


def test_native_engine_loads_skill_calls_real_pricing_and_replies(tmp_path, native):
    turns = []
    def model(provider, messages, tools):
        turns.append(messages)
        specs = {t['function']['name'] for t in tools}
        assert not specs & {'bash', 'place_order', 'write_file', 'run_swarm'}
        if len(turns) == 1:
            name, args = 'load_skill', {'name': 'technical-basic'}
        elif len(turns) == 2:
            assert any('Core Technical' in str(m.get('content')) for m in messages if m['role'] == 'tool')
            name, args = 'options_pricing', dict(spot=100, strike=100, expiry_days=30, volatility=.2, option_type='call')
        else:
            assert any('delta' in str(m.get('content')) for m in messages if m['role'] == 'tool')
            return {'content': '理论价值2.493377，Delta为0.539964。这是根据输入假设得到的理论模型结果。\n'
                    '```figures\n2.493377 | observed | Black-Scholes theoretical value | call-2::theoretical_value\n'
                    '0.539964 | observed | Delta | call-2::delta\n```'}, {'total_tokens': 30}
        return {'content': '', 'tool_calls': [{'id': 'call-' + str(len(turns)), 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(args)}}]}, {'total_tokens': 20}
    with TestClient(create_app(tmp_path, demo=True, ai_model_call=model)) as client:
        a = make_agent(client)
        run = post(client, '/api/ai/test', {'agent_id': a['id'], 'text': '加载技术分析技能并计算指定期权的理论价格'})
        result = wait(client, run['id'])
        assert '理论价值2.493377' in result['reply'] and '拒绝' not in result['reply']
        text = json.dumps(result, ensure_ascii=False)
        assert 'options_pricing' in text and 'technical-basic' in text and REVISION in text
        assert len(turns) == 3
        assert client.get('/api/contacts').json() == []


def test_native_skills_cannot_escape_or_load_unselected(tmp_path, native):
    ai = SimpleNamespace(store=SimpleNamespace(path=tmp_path / 'panel.sqlite3'), stopping=threading.Event())
    service = VibeService(ai)
    for name in ['valuation-model', '../../secret']:
        r = service.worker(dict(action='tool', tools=[], skills=['technical-basic'], name='load_skill',
                                arguments={'name': name}), timeout=30)
        assert json.loads(r['result'])['status'] == 'error'
    with pytest.raises(RuntimeError):
        service.worker(dict(action='check', tools=['bash'], skills=[]), timeout=30)


def test_native_offline_and_actual_capability_check(tmp_path, native):
    ai = SimpleNamespace(store=SimpleNamespace(path=tmp_path / 'panel.sqlite3'), stopping=threading.Event())
    result = VibeService(ai).worker(dict(action='check', tools=list(TOOLS), skills=list(SKILLS), online=False), timeout=40)
    assert set(result['tools']) == {'load_skill', 'options_pricing', 'options_payoff', 'financial_rigor'}
    assert set(result['skills']) == set(SKILLS)


def test_native_api_cancel_does_not_release_late_answer(tmp_path, native):
    entered, release = threading.Event(), threading.Event()
    def model(*args):
        entered.set()
        release.wait(15)
        return {'content': '不应送达的旧回复'}, {}
    with TestClient(create_app(tmp_path, demo=True, ai_model_call=model)) as client:
        a = make_agent(client)
        run = post(client, '/api/ai/test', {'agent_id': a['id'], 'text': '你好'})
        try:
            assert entered.wait(20)
            post(client, '/api/ai/runs/' + run['id'] + '/cancel', {})
            end = time.monotonic() + 5
            while client.app.state.ai.futures and time.monotonic() < end:
                time.sleep(.1)
            assert not client.app.state.ai.futures, 'Cancellation must release the worker before the model returns'
        finally:
            release.set()
        result = wait(client, run['id'], 'cancelled')
        assert not result['reply']
        end = time.monotonic() + 5
        while client.app.state.ai.futures and time.monotonic() < end:
            time.sleep(.1)
        assert client.get('/api/ai/runs/' + run['id']).json()['status'] == 'cancelled'


def test_native_contact_context_isolation_and_reply_modes(tmp_path, native):
    from wechat_panel.ai import AIService
    from wechat_panel.store import Store
    seen = []
    def model(provider, messages, tools):
        seen.append(messages)
        return {'content': '你好，消息已收到。'}, {}
    store = Store(tmp_path / 'panel.sqlite3')
    ai = AIService(store, model_call=model)
    try:
        p = ai.data.save('providers', None, dict(name='test', base_url='http://localhost:19999/v1', model='test'))
        a = ai.data.save('agents', None, dict(name='Vibe', kind='vibe', provider_id=p['id'], vibe_tools=[], vibe_skills=[]))
        ai.data.settings({'enabled': True, 'debounce_seconds': .3, 'context_messages': 20})
        for marker, mode, expected in [('PRIVATE_A', 'ai_assist', 'draft'), ('PRIVATE_B', 'ai_auto', 'queued')]:
            c = store.add_contact(dict(lookup=marker, nickname=marker, wechat_id=marker, account_id='self'))
            store.update_contact(c['id'], listening=True, mode=mode, ai_agent_id=a['id'])
            store.ingest(c['id'], [])
            store.ingest(c['id'], [{'key': marker, 'text': marker + ' hello', 'kind': 'text', 'direction': 'incoming', 'account_id': 'self'}])
            m = store.messages(c['id'])[-1]
            r = ai.create_run(a['id'], m['text'], c['id'], m['id'])
            ai.execute(r['id'])
            assert ai.data.run(r['id'])['status'] == expected
        assert 'PRIVATE_B' not in json.dumps(seen[0])
        assert 'PRIVATE_A' not in json.dumps(seen[1])
        assert len(store.pending()) == 1
    finally:
        ai.stop()


def test_native_test_conversation_context_and_new_session(tmp_path, native):
    seen = []
    def model(provider, messages, tools):
        seen.append(messages)
        return {'content': '收到。'}, {}
    with TestClient(create_app(tmp_path, demo=True, ai_model_call=model)) as client:
        a = make_agent(client)
        for text, session in [('CONTEXT_ONE', 'session-a'), ('follow up', 'session-a'), ('new chat', 'session-b')]:
            run = post(client, '/api/ai/test', dict(agent_id=a['id'], text=text, session_id=session))
            wait(client, run['id'])
        assert 'CONTEXT_ONE' in json.dumps(seen[1])
        assert 'CONTEXT_ONE' not in json.dumps(seen[2])


def test_native_worker_timeout(tmp_path, native):
    ai = SimpleNamespace(store=SimpleNamespace(path=tmp_path / 'panel.sqlite3'), stopping=threading.Event())
    with pytest.raises(RuntimeError, match='超过时限'):
        VibeService(ai).worker(dict(action='check', tools=[], skills=[]), timeout=.01)


@pytest.mark.parametrize('proxy_source', ['environment', 'windows_settings'])
def test_native_ccxt_routes_public_requests_through_existing_proxy(native, proxy_source):
    """A real CCXT request must reach the proxy; .invalid cannot work directly."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import os
    import subprocess
    seen = []

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            body = b'{"symbols":[{"symbol":"BTCUSDT"}]}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy = 'http://127.0.0.1:' + str(server.server_port)
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith('_PROXY')}
    if proxy_source == 'environment':
        env['HTTP_PROXY'] = proxy
    code = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from wechat_panel.vibe_network import configure_ccxt_network
import requests.utils
if sys.argv[2] == 'windows_settings':
    # requests obtains Windows Internet Settings through this hook when
    # conventional HTTP(S)_PROXY variables are absent.
    requests.utils.getproxies = lambda: {'http': sys.argv[3]}
configure_ccxt_network()
import ccxt
exchange = ccxt.binance({'timeout': 1500})
print(json.dumps(exchange.fetch('http://vibe-market.invalid/api/v3/exchangeInfo')))
'''
    try:
        result = subprocess.run([native['python'], '-c', code, str(Path(__file__).resolve().parents[1]), proxy_source, proxy],
                                env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['symbols'][0]['symbol'] == 'BTCUSDT'
        assert seen == ['http://vibe-market.invalid/api/v3/exchangeInfo']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
