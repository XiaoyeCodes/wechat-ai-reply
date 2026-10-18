"""Window rebuilds must not turn answered requests into new work."""
import json

import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store


VIDEO = 'https://www.bilibili.com/video/example 帮我总结一下这个视频的内容'
SUMMARY = '视频已经总结：这里是视频的主要观点。'
NEWS = '最近有哪些新闻？'


def observation(key, text, direction='incoming'):
    return dict(key=key, text=text, direction=direction, kind='text')


@pytest.fixture(params=[False, True], ids=['without-memory', 'with-memory'])
def env(tmp_path, request):
    store = Store(tmp_path / 'panel.sqlite3')
    calls = []

    def model(provider, messages, tools):
        if provider.get('_structured'):
            return {'content': json.dumps({'risk': 'none', 'needs_web': False})}, {}
        calls.append(messages)
        return {'content': '本轮答复'}, {}

    ai = AIService(store, model_call=model)
    provider = ai.data.save('providers', None, dict(name='test', model='test', base_url='http://localhost:9999'))
    agent = ai.data.save('agents', None, dict(name='test', provider_id=provider['id'], web_policy='off',
        companion={'memory': {'enabled': request.param}, 'safety': {'require_adult': False}}))
    contact = store.add_contact(dict(lookup='test', nickname='test', wechat_id='test', account_id='self'))
    store.update_contact(contact['id'], ai_agent_id=agent['id'], listening=True, mode='ai_assist')
    ai.data.settings({'enabled': True})
    store.ingest(contact['id'], [])
    yield store, ai, agent, contact['id'], calls
    ai.stop()


def rebuilt_dialogue(store, cid):
    old = [observation('old-question', VIDEO), observation('answer', SUMMARY, 'outgoing')]
    store.ingest(cid, old)
    # The UI recreates the incoming row after the answer has been sent.
    rebuilt = [observation('rebuilt-question', VIDEO), old[1]]
    store.ingest(cid, rebuilt)
    duplicate = store.messages(cid)[-1]
    # Stable snapshot alignment can reuse the original key without inserting a
    # duplicate. Older snapshots may instead import a historical copy.
    assert (duplicate['historical'] and duplicate['text']==VIDEO) or (
        len(store.messages(cid))==2 and duplicate['text']==SUMMARY)
    return rebuilt


def execute(env, visible, text):
    store, ai, agent, cid, calls = env
    store.ingest(cid, visible + [observation('current', text)])
    trigger = store.messages(cid)[-1]
    assert not trigger['historical']
    run = ai.create_run(agent['id'], text, cid, trigger['id'])
    ai.execute(run['id'])
    result = ai.data.run(run['id'])
    assert result['status'] == 'draft', result['error']
    return result, calls[-1]


@pytest.mark.parametrize('text', [NEWS, '刚才视频里第二个例子是什么意思？'])
def test_rebuilt_video_request_is_not_replayed_but_followup_context_survives(env, text):
    store, _, _, cid, _ = env
    visible = rebuilt_dialogue(store, cid)
    result, messages = execute(env, visible, text)
    assert result['state']['context'] == [
        {'role': 'user', 'content': VIDEO},
        {'role': 'assistant', 'content': SUMMARY},
        {'role': 'user', 'content': text},
    ]
    # Only the actual trigger is an active user request. Old turns are quoted data.
    assert [m['content'] for m in messages if m['role'] == 'user'] == [text]
    background = messages[0]['content']
    assert VIDEO in background and SUMMARY in background
    assert '历史' in background and '本轮' in background


def test_a_genuine_repeat_is_still_a_new_request(env):
    store, _, _, cid, _ = env
    visible = rebuilt_dialogue(store, cid)
    result, messages = execute(env, visible, VIDEO)
    assert [m['content'] for m in result['state']['context']].count(VIDEO) == 2
    assert messages[-1] == {'role': 'user', 'content': VIDEO}


def test_unique_initial_history_and_repeated_live_messages_are_preserved(env):
    store, _, _, cid, _ = env
    # A reset baseline discovers two real identical messages; keep both.
    with store.connect() as db:
        db.execute('DELETE FROM snapshots WHERE contact_id=?', (cid,))
    visible = [observation('h1', '好的'), observation('h2', '好的')]
    store.ingest(cid, visible)
    result, _ = execute(env, visible, '我们接着聊')
    assert [m['content'] for m in result['state']['context']] == ['好的', '好的', '我们接着聊']


def test_current_request_is_explicit_even_when_context_does_not_contain_it(env):
    _, ai, agent, _, _ = env
    run = ai.create_run(agent['id'], NEWS, dry_run=True)
    state = ai.data.run(run['id'])['state']
    state['context'] = [{'role': 'user', 'content': VIDEO}, {'role': 'assistant', 'content': SUMMARY}]
    messages = ai._base_messages(run, state, '')
    assert messages[-1] == {'role': 'user', 'content': NEWS}
    assert [m['content'] for m in messages if m['role'] == 'user'] == [NEWS]


def test_reference_material_does_not_replace_current_request(env):
    _, ai, agent, _, _ = env
    run = ai.create_run(agent['id'], NEWS, dry_run=True)
    state = ai.data.run(run['id'])['state']
    state['knowledge'] = [{'content': '参考新闻资料'}]
    state['last'] = '上一流程结果'
    messages = ai._base_messages(run, state, '')
    assert [m['content'] for m in messages if m['role'] == 'user'] == [NEWS]
    assert '参考新闻资料' in messages[0]['content']
    assert '上一流程结果' in messages[0]['content']


def test_image_and_scheduled_turns_have_one_explicit_current_request(env):
    _, ai, agent, _, _ = env
    run = ai.create_run(agent['id'], NEWS, dry_run=True)
    state = ai.data.run(run['id'])['state']
    state['context'] = [{'role': 'user', 'content': VIDEO}, {'role': 'user', 'content': NEWS}]
    state['input_media_id'] = 'test-image'
    state['dynamic_context'] = {'image_description': '图片观察内容'}
    messages = ai._base_messages(run, state, '')
    assert len([m for m in messages if m['role'] == 'user']) == 1
    assert '图片观察内容' in messages[-1]['content']
    assert VIDEO in messages[0]['content']
    state['input_media_id'] = None
    run['schedule_id'] = 'test-schedule'
    messages = ai._base_messages(run, state, '')
    assert messages[-1] == {'role': 'user', 'content': NEWS}
    # Scheduled text matching a prior user message must not erase that history.
    assert NEWS in messages[0]['content']
