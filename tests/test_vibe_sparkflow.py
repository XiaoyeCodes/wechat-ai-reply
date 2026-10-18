"""Exercise the copied SparkFlow loop and its real native HTTP model client."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from wechat_panel.vibe import VibeService, installation


def test_native_settings_survive_legacy_form_save(tmp_path):
    from wechat_panel.store import Store
    from wechat_panel.ai_store import AIStore
    data = AIStore(Store(tmp_path / 'panel.sqlite3'))
    p = data.save('providers', None, dict(name='test', base_url='http://localhost:19999/v1', model='test'))
    a = data.save('agents', None, dict(name='native', kind='vibe', provider_id=p['id'], vibe_engine='sparkflow'))
    # A cached old page knows neither the engine selector nor the native limits.
    saved = data.save('agents', a['id'], dict(name='native', kind='vibe', provider_id=p['id'],
        prompt='只回答300字', style_id='deleted-style', vibe_max_iterations=12,
        vibe_task_timeout=1800, vibe_tools=[], vibe_skills=[]))
    assert saved['vibe_engine'] == 'sparkflow'
    assert saved['vibe_max_iterations'] == 50
    assert saved['vibe_task_timeout'] is None
    assert saved['prompt'] == saved['style_id'] == ''
    assert saved['vibe_tools'] == saved['vibe_skills'] == []


@pytest.fixture
def service(tmp_path, monkeypatch):
    try:
        install = installation('sparkflow')
    except (ValueError, OSError):
        pytest.skip('Install the SparkFlow snapshot first')
    preferences = tmp_path / 'preference-seed'
    preferences.mkdir()
    (preferences / 'MEMORY.md').write_text('- [美股市场报告偏好](report.md)\n', encoding='utf-8')
    (preferences / 'report.md').write_text('---\nname: 美股市场报告偏好\ndescription: 美股市场汇总\ntype: user\n---\n保留原生偏好测试标记。', encoding='utf-8')
    monkeypatch.setattr('wechat_panel.vibe.installation', lambda engine='github': {**install, 'preferences': str(preferences)})
    return VibeService(SimpleNamespace(store=SimpleNamespace(path=tmp_path / 'panel.sqlite3'),
                                      stopping=threading.Event()))


def test_sparkflow_native_model_protocol_and_unmodified_reply(service):
    requests = []
    reply = '## 完整研究报告\n\n' + '保留原文、段落和 Markdown。\n' * 180

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            assert body['stream'] is True
            first = len(requests) == 1
            delta = dict(role='assistant', content=reply)
            if first:
                delta = dict(role='assistant', content='', tool_calls=[dict(index=0, id='native-pricing', type='function',
                    function=dict(name='options_pricing', arguments=json.dumps(dict(spot=100, strike=100,
                        expiry_days=30, volatility=.2, risk_free_rate=.05, option_type='call'))))])
            chunk = dict(id='native-test', object='chat.completion.chunk', model='native-test',
                choices=[dict(index=0, finish_reason='tool_calls' if first else 'stop', delta=delta)],
                usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20))
            payload = ('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events = []
    try:
        result = service.worker(dict(action='run', engine='sparkflow', session='parity',
            message='昨天美股市场的情况怎么样？帮我汇总一下。',
            history=[dict(role='user', content='应由原生历史预算排除' * 2000),
                     dict(role='user', content='前文标记'), dict(role='assistant', content='已收到')],
            prompt='不应注入：通常不超过300字', tools=[], skills=[], online=True,
            native_model=dict(provider='deepseek', model='native-test', api_key='local-test',
                              base_url=f'http://127.0.0.1:{server.server_port}/v1')),
            timeout=None, on_event=events.append)
        assert result['status'] == 'success'
        assert result['content'] == reply
        assert len(requests) == 2
        tool_results = [m for m in requests[1]['messages'] if m['role'] == 'tool']
        assert len(tool_results) == 1
        receipt = json.loads(tool_results[0]['content'])
        assert receipt['price'] == pytest.approx(2.4933768)
        body = requests[0]
        assert body['model'] == 'native-test'
        assert body['temperature'] == 0
        assert 'max_tokens' not in body and 'max_completion_tokens' not in body
        assert body.get('thinking') != {'type': 'disabled'}
        assert '不应注入' not in json.dumps(body, ensure_ascii=False)
        assert '前文标记' in json.dumps(body, ensure_ascii=False)
        assert '应由原生历史预算排除' not in json.dumps(body, ensure_ascii=False)
        assert body['messages'][-1]['content'].endswith('昨天美股市场的情况怎么样？帮我汇总一下。')
        assert '保留原生偏好测试标记。' in body['messages'][-1]['content']
        names = {t['function']['name'] for t in body['tools']}
        assert {'web_search', 'read_url', 'load_skill', 'run_swarm', 'get_market_data'} <= names
        assert len(names) > 60 and 'bash' not in names
        assert len(next(e for e in events if e['type'] == 'ready')['skills']) > 80
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_sparkflow_offline_has_no_network_tools(service):
    result = service.worker(dict(action='check', engine='sparkflow', online=False), timeout=90)
    assert set(result['tools']) <= {'load_skill', 'options_pricing', 'options_payoff', 'financial_rigor'}
    assert 'load_skill' in result['tools']


def test_sparkflow_worker_cancellable(service):
    assert service.worker(dict(action='check', engine='sparkflow'), timeout=90, active=lambda: False) is None


@pytest.mark.parametrize('mode,outcome', [('ai_auto', 'confirmed'), ('ai_auto', 'uncertain'), ('ai_assist', 'failed')])
def test_long_native_reply_delivery_preserves_text_and_stops_on_failure(tmp_path, mode, outcome):
    from wechat_panel.ai import AIService
    from wechat_panel.store import Store
    store = Store(tmp_path / 'panel.sqlite3')
    ai = AIService(store)
    try:
        p = ai.data.save('providers', None, dict(name='test', base_url='http://localhost:19999/v1', model='test'))
        a = ai.data.save('agents', None, dict(name='native', kind='vibe', provider_id=p['id'], vibe_engine='sparkflow'))
        ai.data.settings({'enabled': True})
        c = store.add_contact(dict(lookup='test', nickname='test', wechat_id='test', account_id='self'))
        store.update_contact(c['id'], listening=True, mode=mode, ai_agent_id=a['id'])
        store.ingest(c['id'], [])
        store.ingest(c['id'], [dict(key='question', text='汇总', kind='text', direction='incoming', account_id='self')])
        m = store.messages(c['id'])[-1]
        r = ai.create_run(a['id'], m['text'], c['id'], m['id'])
        reply = '  ## 报告\n\n' + ('原样保留，包含表格。📊\n' * 400) + '\n'
        ai.finish(r['id'], reply)
        assert ai.data.run(r['id'])['reply'] == reply
        if mode == 'ai_assist':
            assert not store.pending()
            assert ai.data.run(r['id'])['status'] == 'draft'
            ai.send_draft(r['id'], reply)
        parts = store.pending()
        assert len(parts) > 2
        assert ''.join(p['text'] for p in parts) == reply
        assert all(len(p['text']) <= 1999 for p in parts)
        assert store.ai_send_valid(parts[0])
        store.set_message_state(parts[0]['id'], outcome)
        if outcome == 'confirmed':
            assert ai.data.run(r['id'])['status'] == 'queued'
            for part in parts[1:]:
                assert store.ai_send_valid(part)
                store.set_message_state(part['id'], 'confirmed')
            assert ai.data.run(r['id'])['status'] == 'sent'
        else:
            assert not store.pending()
            assert ai.data.run(r['id'])['status'] == outcome
            assert all(store.message(p['id'])['status'] == 'cancelled' for p in parts[1:])
    finally:
        ai.stop()
