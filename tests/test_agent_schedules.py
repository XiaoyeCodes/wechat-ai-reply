"""Independent schedule checks: temporary SQLite, fake model/media, no WeChat."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from wechat_panel.ai import AIService
from wechat_panel.app import create_app
from wechat_panel.store import Store, now


HEADERS = {'X-Panel-Request': '1'}
BASE = '/api/ai/agent-schedules'


class NoDesktopRuntime:
    """Use the actual API lifespan without starting any desktop worker."""
    def __init__(self, store, demo=False):
        self.store = store

    def start(self):
        pass

    def stop(self):
        pass

    def configure(self):
        pass

    def health(self):
        return {'state': 'ready', 'account_id': 'test-account'}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(AIService, 'start', lambda self: None)
    with TestClient(create_app(tmp_path, runtime_factory=NoDesktopRuntime,
                               ai_model_call=lambda *args: pytest.fail('CRUD must not call a model'))) as client:
        ai = client.app.state.ai
        p = ai.data.save('providers', None, {'name': 'fake', 'model': 'fake', 'base_url': 'http://localhost:1'})
        agent = ai.data.save('agents', None, {'name': 'news', 'provider_id': p['id']})
        yield client, ai, agent


def schedule_body(agent, **changes):
    return {'agent_id': agent['id'], 'title': '每日新闻', 'instruction': '执行每日新闻技能',
            'kind': 'once', 'at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), **changes}


def test_schedule_api_crud_defaults_and_partial_edit(api):
    client, ai, agent = api
    response = client.post(BASE, headers=HEADERS, json=schedule_body(agent))
    assert response.status_code in (200, 201), response.text
    task = response.json()
    assert task['enabled'] is False and task['timezone'] == 'Asia/Shanghai'
    assert task['eligible_count'] == 0 and not task['next_at']
    assert [row['id'] for row in client.get(BASE).json()] == [task['id']]
    edited = client.put(BASE + '/' + task['id'], headers=HEADERS, json={'title': '新的标题'})
    assert edited.status_code == 200, edited.text
    assert edited.json()['instruction'] == task['instruction']
    assert edited.json()['title'] == '新的标题'
    assert client.get(BASE + '/' + task['id'] + '/runs').json() == []
    assert client.delete(BASE + '/' + task['id'], headers=HEADERS).status_code in (200, 204)
    assert client.get(BASE).json() == []
    assert client.put(BASE + '/' + task['id'], headers=HEADERS, json={'enabled': True}).status_code == 404


@pytest.mark.parametrize('changes', [
    {'timezone': 'Moon/Tranquility'}, {'at': 'next Tuesday'}, {'at': ''},
    {'kind': 'cron', 'cron': '60 25 * * *'}, {'kind': 'cron', 'cron': '* * * * * *'},
    {'kind': 'cron', 'cron': ''}, {'kind': 'weekly'}, {'title': '   '},
    {'instruction': '\n  '}, {'agent_id': 'missing-agent'}, {'unexpected': True},
])
def test_schedule_api_rejects_invalid_configuration(api, changes):
    client, _, agent = api
    response = client.post(BASE, headers=HEADERS, json=schedule_body(agent, **changes))
    assert 400 <= response.status_code < 500, response.text
    assert client.get(BASE).json() == []


def test_schedule_api_requires_panel_request_header(api):
    client, _, agent = api
    assert client.post(BASE, json=schedule_body(agent)).status_code == 403


def test_immediate_run_explains_disabled_ai_and_no_recipients_without_side_effects(api):
    client, ai, agent = api
    task = client.post(BASE, headers=HEADERS, json=schedule_body(agent)).json()
    endpoint = BASE + '/' + task['id']
    response = client.post(endpoint + '/run', headers=HEADERS)
    assert response.status_code == 409 and '启用' in response.json()['detail']
    client.put(endpoint, headers=HEADERS, json={'enabled': True})
    response = client.post(endpoint + '/run', headers=HEADERS)
    assert response.status_code == 409
    ai.data.settings({'enabled': True})
    response = client.post(endpoint + '/run', headers=HEADERS)
    assert response.status_code == 409 and '联系人' in response.json()['detail']
    assert client.get(endpoint + '/runs').json() == []


@pytest.fixture
def env(tmp_path):
    calls = []
    store = Store(tmp_path / 'panel.sqlite3')

    def model(provider, messages, tools):
        calls.append(messages)
        return {'role': 'assistant', 'content': '共享新闻正文'}, {}

    ai = AIService(store, model_call=model)
    provider = ai.data.save('providers', None,
                            {'name': 'fake', 'model': 'fake', 'base_url': 'http://localhost:1'})
    agent = ai.data.save('agents', None, {
        'name': '新闻 Agent', 'provider_id': provider['id'], 'web_policy': 'off',
        'companion': {'memory': {'enabled': True, 'auto_extract': False},
                      'safety': {'emotion_enabled': False, 'crisis_detection': False}},
    })
    ai.data.settings({'enabled': True})
    contacts = []
    for index in range(2):
        c = store.add_contact({'lookup': f'收件人{index}', 'nickname': f'收件人{index}',
                               'wechat_id': f'recipient-{index}', 'account_id': 'self'})
        store.update_contact(c['id'], listening=True, mode='ai_auto', ai_agent_id=agent['id'])
        store.ingest(c['id'], [])
        contacts.append(store.contact(c['id']))
    yield store, ai, provider, agent, contacts, calls
    ai.stop()


def edit_agent(ai, agent, **changes):
    body = {key: value for key, value in ai.data.get('agents', agent['id']).items()
            if key not in ('id', 'key_configured')}
    body.update(changes)
    return ai.data.save('agents', agent['id'], body)


def task(ai, agent, **changes):
    return ai.agent_scheduler.save(schedule_body(agent, enabled=True, **changes))


def generate(ai, schedule):
    occurrence = ai.agent_scheduler.run_now(schedule['id'])
    ai.execute(occurrence['run_id'])
    return ai.agent_scheduler.history(schedule['id'])[0]


def test_one_generation_is_anonymous_and_reused_by_exact_eligible_contacts(env):
    store, ai, provider, agent, contacts, calls = env
    other = ai.data.save('agents', None, {'name': 'other', 'provider_id': provider['id']})
    for index, changes in enumerate([
        {'listening': False}, {'mode': 'manual'}, {'mode': 'ai_assist'},
        {'mode': 'listen_only'}, {'ai_agent_id': other['id']},
    ]):
        c = store.add_contact({'lookup': f'excluded-{index}', 'nickname': f'excluded-{index}',
                               'wechat_id': f'excluded-{index}', 'account_id': 'self'})
        store.update_contact(c['id'], listening=True, mode='ai_auto', ai_agent_id=agent['id'])
        store.update_contact(c['id'], **changes)
    schedule = task(ai, agent)
    occurrence = generate(ai, schedule)
    assert len(calls) == 1
    generation = ai.data.run(occurrence['run_id'])
    assert generation['contact_id'] is None and not generation['dry_run']
    targets = {c['id'] for c in contacts}
    assert {r['contact_id'] for r in occurrence['recipients']} == targets
    assert {m['contact_id'] for m in store.pending()} == targets
    assert len({m['ai_run_id'] for m in store.pending()}) == 2
    assert all(m['text'] == '共享新闻正文' and store.ai_send_valid(m) for m in store.pending())
    # Re-entering finish cannot enqueue another copy per recipient.
    ai.finish(generation['id'], generation['reply'])
    assert len(store.pending()) == 2 and len(calls) == 1


def test_financial_task_retains_the_original_pdf_for_each_eligible_recipient(env):
    # Temporary database only. No Runtime or desktop sender exists in this fixture.
    from pypdf import PdfWriter
    import hashlib
    from wechat_panel import financial_daily
    from wechat_panel.financial_pdf import report_path
    store,ai,_,agent,contacts,_=env
    occurrence=ai.agent_scheduler.run_now(task(ai,agent)['id'])
    run=ai.data.run(occurrence['run_id']);state=run['state']
    pdf=report_path(store.path.parent,run['id']);pdf.parent.mkdir(parents=True)
    writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(pdf)
    workspace=pdf.parent/'financial-daily';workspace.mkdir()
    preview=workspace/'daily-1.png';Image.new('RGB',(10,10)).save(preview)
    state.update(output_format='pdf',pdf={'name':'每日财经.pdf','url':f"/api/ai/runs/{run['id']}/pdf"},
        financial_daily={'status':'ready','artifact_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
                         'workspace':str(workspace),'previews':[str(preview)]})
    ai.data.update_run(run['id'],state=state)
    ai.finish(run['id'],'财经日报已生成')
    pending=store.pending()
    assert {m['contact_id'] for m in pending}=={c['id'] for c in contacts}
    assert all(m['kind']=='file' and store.ai_send_valid(m) for m in pending)
    for message in pending:
        delivery=ai.data.run(message['ai_run_id'])
        assert financial_daily.verified_pdf(store.path.parent,delivery).read_bytes()==pdf.read_bytes()
        assert delivery['id'] in str(financial_daily.preview_path(store.path.parent,delivery,1))


def test_failed_shared_result_stays_in_panel_without_broadcast(env):
    store,ai,_,agent,_,_=env
    ai.model_call=lambda *a:({'content':'我现在没有执行脚本的权限，本次任务未能完成。'}, {})
    occurrence=generate(ai,task(ai,agent))
    run=ai.data.run(occurrence['run_id'])
    assert run['status']=='failed' and run['reply']=='我遇到点问题，未能完成该任务。'
    assert not store.pending() and not occurrence['recipients']


def test_shared_generation_never_reads_private_chat_profiles_or_memories(env):
    store, ai, _, agent, contacts, calls = env
    agent = edit_agent(ai, agent, prompt='PUBLIC_NEWS_STYLE_MARKER 使用简洁新闻风格，当前时间 {{current_time}}。')
    for index, c in enumerate(contacts):
        secret = f'PRIVATE_RECIPIENT_{index}'
        scope = ai.companion.memory.scope(agent['id'], c['id'])
        profile = ai.companion.memory.profile(scope, agent['id'], c['id'])
        ai.companion.memory.set_profile(scope, {**profile['data'], 'display_name': secret})
        ai.companion.memory.save_memory(scope, {'key': 'secret', 'content': secret})
        with store.connect() as db:
            db.execute('INSERT INTO messages(contact_id,direction,text,status,created_at) VALUES(?,?,?,?,?)',
                       (c['id'], 'incoming', secret, 'observed', now()))
    occurrence = generate(ai, task(ai, agent))
    generation = ai.data.run(occurrence['run_id'])
    assert 'PRIVATE_RECIPIENT_' not in json.dumps(calls, ensure_ascii=False)
    assert 'PUBLIC_NEWS_STYLE_MARKER' in json.dumps(calls, ensure_ascii=False)
    assert 'PRIVATE_RECIPIENT_' not in json.dumps(generation['state'], ensure_ascii=False)
    assert generation['state']['scope'] not in {
        ai.companion.memory.scope(agent['id'], c['id']) for c in contacts}


@pytest.mark.parametrize('change', ['manual', 'assist', 'unlisten', 'agent', 'pause', 'ai_off',
                                  'agent_off', 'task_off', 'task_delete', 'task_edit'])
def test_send_guard_rechecks_current_configuration_after_generation(env, change):
    store, ai, provider, agent, contacts, _ = env
    schedule = task(ai, agent)
    generate(ai, schedule)
    queued = store.pending()[0]
    assert store.ai_send_valid(queued)
    cid = queued['contact_id']
    if change == 'manual':store.update_contact(cid, mode='manual')
    elif change == 'assist':store.update_contact(cid, mode='ai_assist')
    elif change == 'unlisten':store.update_contact(cid, listening=False)
    elif change == 'agent':
        other = ai.data.save('agents', None, {'name': 'other', 'provider_id': provider['id']})
        store.update_contact(cid, ai_agent_id=other['id'])
    elif change == 'pause':store.pause(True)
    elif change == 'ai_off':ai.data.settings({'enabled': False})
    elif change == 'agent_off':edit_agent(ai, agent, enabled=False)
    elif change == 'task_off':ai.agent_scheduler.save({'enabled': False}, schedule['id'])
    elif change == 'task_edit':ai.agent_scheduler.save({'instruction': '改为新的新闻任务'}, schedule['id'])
    else:ai.agent_scheduler.delete(schedule['id'])
    assert not store.ai_send_valid(queued)


def test_recipients_are_intersection_of_start_and_finish_eligibility(env):
    store, ai, _, agent, contacts, _ = env
    occurrence = ai.agent_scheduler.run_now(task(ai, agent)['id'])
    store.update_contact(contacts[1]['id'], listening=False)
    new = store.add_contact({'lookup': '生成中新加入', 'nickname': '生成中新加入',
                             'wechat_id': 'late-joiner', 'account_id': 'self'})
    store.update_contact(new['id'], listening=True, mode='ai_auto', ai_agent_id=agent['id'])
    ai.execute(occurrence['run_id'])
    assert {m['contact_id'] for m in store.pending()} == {contacts[0]['id']}


def test_long_text_is_complete_and_delivery_receipts_are_independent(env):
    store, ai, _, agent, contacts, calls = env
    text = '\n'.join(f'{i}、完整新闻内容。' * 25 for i in range(30))
    ai.model_call = lambda *args: ({'role': 'assistant', 'content': text}, {})
    occurrence = generate(ai, task(ai, agent))
    assert len(occurrence['recipients']) == 2
    grouped = {}
    for m in store.pending():grouped.setdefault(m['contact_id'], []).append(m)
    for messages in grouped.values():
        assert len(messages) > 1
        assert ''.join(m['text'] for m in messages) == text
        assert all(len(m['text']) < 2000 for m in messages)
    first, second = [grouped[c['id']] for c in contacts]
    for m in first:store.set_message_state(m['id'], 'confirmed')
    store.set_message_state(second[0]['id'], 'uncertain', '发送结果不确定')
    history = ai.agent_scheduler.history(occurrence['schedule_id'])[0]
    statuses = {r['contact_id']: r['status'] for r in history['recipients']}
    assert statuses == {contacts[0]['id']: 'sent', contacts[1]['id']: 'uncertain'}
    assert history['status'] == 'uncertain'


@pytest.mark.parametrize('condition', ['no_recipients', 'pause', 'ai_off', 'agent_off'])
def test_due_tasks_skip_unrunnable_conditions_without_model_work(env, condition):
    store, ai, _, agent, contacts, calls = env
    schedule = task(ai, agent, at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    if condition == 'no_recipients':
        for c in contacts:store.update_contact(c['id'], mode='ai_assist')
    elif condition == 'pause':store.pause(True)
    elif condition == 'ai_off':ai.data.settings({'enabled': False})
    else:edit_agent(ai, agent, enabled=False)
    ai.agent_scheduler.tick(datetime.now(timezone.utc))
    history = ai.agent_scheduler.history(schedule['id'])
    assert len(history) == 1 and history[0]['status'] == 'skipped'
    assert history[0]['error'] and not history[0]['run_id']
    assert calls == [] and store.pending() == []


def test_scheduled_occurrence_is_claimed_once_even_concurrently(env):
    store, ai, _, agent, contacts, _ = env
    schedule = task(ai, agent, at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    stamp = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: ai.agent_scheduler.tick(stamp), range(3)))
    ai.agent_scheduler.tick(stamp)
    history = ai.agent_scheduler.history(schedule['id'])
    assert len(history) == 1 and history[0]['run_id']
    assert len(ai.data.runs()) == 1


def test_immediate_double_click_cannot_create_second_active_generation(env):
    _, ai, _, agent, _, calls = env
    schedule = task(ai, agent)
    ai.agent_scheduler.run_now(schedule['id'])
    with pytest.raises((ValueError, HTTPException)):
        ai.agent_scheduler.run_now(schedule['id'])
    assert len(ai.agent_scheduler.history(schedule['id'])) == 1
    assert len(ai.data.runs()) == 1 and not calls


def test_old_once_and_cron_are_skipped_without_catchup_burst(env):
    store, ai, _, agent, contacts, calls = env
    old = datetime.now(timezone.utc) - timedelta(days=2)
    one = task(ai, agent, at=old.isoformat())
    repeat = task(ai, agent, kind='cron', cron='0 9 * * *')
    with store.connect() as db:
        db.execute('UPDATE agent_schedules SET next_at=? WHERE id=?', (old.isoformat(), repeat['id']))
    stamp = datetime.now(timezone.utc)
    ai.agent_scheduler.tick(stamp)
    ai.agent_scheduler.tick(stamp)
    for schedule in (one, repeat):
        history = ai.agent_scheduler.history(schedule['id'])
        assert len(history) == 1 and history[0]['status'] == 'skipped'
        assert '过期' in history[0]['error']
    assert datetime.fromisoformat(ai.agent_scheduler.get(repeat['id'])['next_at']) > stamp
    assert not ai.agent_scheduler.get(one['id'])['next_at']
    assert not calls and not store.pending()


def test_timezone_conversion_and_nonexistent_dst_local_time_are_explicit():
    from wechat_panel.agent_schedules import AgentSchedule, AgentScheduler
    value = AgentSchedule(agent_id='test', title='早间新闻', instruction='新闻',
                           at='2026-10-20T09:00:00', timezone='Asia/Shanghai').model_dump()
    assert AgentScheduler.next_time(value) == '2026-10-20T01:00:00+00:00'
    value.update(kind='cron', cron='0 9 * * *')
    assert AgentScheduler.next_time(value, datetime(2026, 10, 20, tzinfo=timezone.utc)) == '2026-10-20T01:00:00+00:00'
    with pytest.raises(ValueError):
        AgentSchedule(agent_id='test', title='news', instruction='news',
                      at='2026-03-08T02:30:00', timezone='America/New_York')


def test_restart_cancels_queued_deliveries_and_never_regenerates(env):
    store, ai, _, agent, contacts, calls = env
    schedule = task(ai, agent)
    occurrence = generate(ai, schedule)
    messages = store.pending()
    store.set_message_state(messages[0]['id'], 'sending')
    store.send_receipt(messages[0]['id'], 'prepared', [])
    store.send_receipt(messages[0]['id'], 'attempted')
    store.recover()
    restarted = AIService(store, model_call=lambda *args: pytest.fail('restart may not replay generation'))
    try:
        restarted.agent_scheduler.recover()
        restarted.agent_scheduler.tick(datetime.now(timezone.utc))
        assert not store.pending()
        assert store.message(messages[0]['id'])['status'] == 'uncertain'
        assert all(not store.ai_send_valid(m) for m in messages)
        assert len(restarted.agent_scheduler.history(schedule['id'])) == 1
        assert len(calls) == 1
    finally:restarted.stop()


@pytest.mark.parametrize('status', ['running', 'waiting_approval'])
def test_restart_stops_unfinished_generation_without_replaying_tools(env, monkeypatch, status):
    store, ai, _, agent, _, calls = env
    schedule = task(ai, agent)
    occurrence = ai.agent_scheduler.run_now(schedule['id'])
    run = ai.data.run(occurrence['run_id'])
    state = run['state']
    state['executing_tool'] = 'external-effect-was-started'
    ai.data.update_run(run['id'], status=status, state=state)
    restarted = AIService(store, model_call=lambda *args: pytest.fail('old effects may not replay'))
    monkeypatch.setattr(restarted, '_dispatch', lambda: None)
    try:
        restarted.start()
        result = restarted.data.run(run['id'])
        assert result['status'] == ('uncertain' if status == 'running' else 'cancelled')
        restarted.execute(run['id'])
        assert len(restarted.agent_scheduler.history(schedule['id'])) == 1
        assert not calls and not store.pending()
    finally:restarted.stop()


def test_new_chat_turn_does_not_cancel_broadcast_but_reenabled_listening_does(env):
    store, ai, _, agent, contacts, _ = env
    generate(ai, task(ai, agent))
    queued = next(m for m in store.pending() if m['contact_id'] == contacts[0]['id'])
    store.ingest(contacts[0]['id'], [{'key': 'new-question', 'text': '今天好呀',
                                     'kind': 'text', 'direction': 'incoming'}])
    ai._collect()
    assert store.ai_send_valid(queued)
    store.update_contact(contacts[0]['id'], listening=False)
    store.update_contact(contacts[0]['id'], listening=True)
    assert not store.ai_send_valid(queued)


@pytest.mark.parametrize('phase', ['generation', 'delivery'])
def test_expired_content_is_not_generated_or_sent(env, phase):
    store, ai, _, agent, _, calls = env
    schedule = task(ai, agent)
    occurrence = ai.agent_scheduler.run_now(schedule['id'])
    if phase == 'delivery':
        ai.execute(occurrence['run_id'])
        queued = store.pending()[0]
        run = ai.data.run(queued['ai_run_id'])
    else:run = ai.data.run(occurrence['run_id'])
    state = run['state']
    state['agent_schedule']['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    ai.data.update_run(run['id'], state=state)
    if phase == 'generation':
        ai.execute(run['id'])
        assert not calls and not store.pending()
        assert ai.data.run(run['id'])['status'] == 'cancelled'
    else:assert not store.ai_send_valid(queued)


@pytest.mark.parametrize('approved', [True, False])
def test_task_tool_confirmation_executes_exactly_once_and_never_bypasses_review(env, monkeypatch, approved):
    store, ai, _, agent, _, _ = env
    tool = ai.data.save('tools', None, {'name': 'read_news_fixture', 'description': 'Fake news request',
        'url': 'http://localhost:1/news', 'approval': 'confirm',
        'parameters': {'type': 'object', 'properties': {'day': {'type': 'string'}}, 'required': ['day']}})
    agent = edit_agent(ai, agent, tool_ids=[tool['id']], steps=[
        {'type': 'tool', 'tool_id': tool['id'], 'arguments': {'day': 'today'}},
        {'type': 'model'}, {'type': 'reply'},
    ])
    effects = []
    monkeypatch.setattr('wechat_panel.ai.execute_tool', lambda *args: effects.append(args) or 'fake news')
    schedule = task(ai, agent)
    occurrence = ai.agent_scheduler.run_now(schedule['id'])
    ai.execute(occurrence['run_id'])
    detail = ai.data.detail(occurrence['run_id'])
    assert detail['status'] == 'waiting_approval' and not effects and not store.pending()
    approval = detail['approvals'][0]
    assert approval['arguments'] == {'day': 'today'}
    ai.approve(approval['id'], approved)
    ai.execute(occurrence['run_id'])
    if approved:
        assert len(effects) == 1 and len(store.pending()) == 2
        ai.execute(occurrence['run_id'])
        assert len(effects) == 1 and len(store.pending()) == 2
    else:assert not effects and not store.pending()


def test_daily_news_skill_fetches_before_single_image_and_reuses_it_for_recipients(env, monkeypatch):
    from wechat_panel.companion_services import Services
    from wechat_panel.image_delivery import deliver

    store, ai, _, agent, _, _ = env
    instructions = Path('skills/daily-news-60s/SKILL.md').read_text(encoding='utf-8')
    skill = ai.data.save('skills', None, {'name': 'daily-news-60s',
        'description': '每日新闻·60秒', 'instructions': instructions})
    tools = {t['builtin']: t for t in ai.data.list('tools') if t.get('builtin') in ('read_web', 'generate_image')}
    for tool in tools.values():
        value = {k: v for k, v in tool.items() if k not in ('id', 'key_configured')}
        value['approval'] = 'auto'
        ai.data.save('tools', tool['id'], value)
    service = next(s for s in ai.data.list('services') if s['driver'] == 'codex_image')
    companion = agent['companion']
    companion['media']['image_service_id'] = service['id']
    agent = edit_agent(ai, agent, tool_ids=[t['id'] for t in tools.values()], skill_ids=[skill['id']],
                       companion=companion, web_policy='model', max_tool_rounds=5)
    events = []
    buf = BytesIO()
    Image.new('RGB', (90, 160), 'navy').save(buf, format='PNG')
    png = buf.getvalue()

    def fetch(self, sid, url):
        assert events == ['skill']
        events.append('fetch')
        return json.dumps({'date': '2026-10-19', 'news': ['MOCK_VERIFIED_NEWS'],
                           'url': url}, ensure_ascii=False)

    def image(self, sid, prompt, *args, **kwargs):
        assert events == ['skill', 'fetch'] and 'MOCK_VERIFIED_NEWS' in prompt
        events.append('image')
        return png

    monkeypatch.setattr(Services, 'fetch', fetch)
    monkeypatch.setattr(Services, 'generate_image', image)
    rounds = []

    def model(provider, messages, specs):
        rounds.append(messages)
        count = len(rounds)
        if count == 1:
            assert not events
            events.append('skill')
            name, arguments = 'use_skill', {'name': 'daily-news-60s'}
        elif count == 2:
            assert 'daily-news-60s' in messages[-1]['content']
            name, arguments = 'read_web', {'url': 'https://60s.viki.moe/v2/60s'}
        elif count == 3:
            assert 'MOCK_VERIFIED_NEWS' in messages[-1]['content']
            name, arguments = 'generate_image', {'prompt': '9:16 简约风景新闻卡 MOCK_VERIFIED_NEWS', 'mode': 'generate'}
        else:
            assert events == ['skill', 'fetch', 'image']
            return {'role': 'assistant', 'content': '新闻卡已生成'}, {}
        assert name in {s['function']['name'] for s in specs}
        return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': f'call-{count}', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments)}}]}, {}

    ai.model_call = model
    occurrence = generate(ai, task(ai, agent, instruction='生成今天的每日新闻·60秒9:16新闻卡，先加载daily-news-60s技能'))
    assert ai.data.run(occurrence['run_id'])['status'] == 'completed', ai.data.run(occurrence['run_id'])['error']
    queued = store.pending()
    assert len(rounds) == 4 and events == ['skill', 'fetch', 'image']
    assert len(queued) == 2 and all(m['kind'] == 'image' for m in queued)
    assert len({m['media_id'] for m in queued}) == 1
    delivered = []

    class ImageBridge:
        def send_image(self, contact, data, before_send):
            before_send()
            delivered.append((contact['id'], data))
            return 'fake-image-receipt-' + contact['id']

    for message in queued:
        receipt = deliver(ImageBridge(), store, message, store.contact(message['contact_id']))
        assert receipt['delivery'] == 'image'
    assert len(delivered) == 2 and all(data == png for _, data in delivered)


def test_failed_news_sources_do_not_broadcast_error_or_unverified_news(env):
    store, ai, _, agent, _, _ = env
    schedule = task(ai, agent)
    occurrence = ai.agent_scheduler.run_now(schedule['id'])
    run = ai.data.run(occurrence['run_id'])
    state = run['state']
    state['web_evidence'] = [{'status': 'error', 'sources': []}]
    ai.data.update_run(run['id'], state=state)
    ai.execute(run['id'])
    result = ai.agent_scheduler.history(schedule['id'])[0]
    assert result['status'] == 'failed' and result['error']
    assert not result['recipients'] and not store.pending()
