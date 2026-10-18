"""Prompt values reach the model; daily context survives restarts without leaks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from wechat_panel.ai import AIService
from wechat_panel.ai_models import Agent
from wechat_panel.prompt_context import render, UNKNOWN_WEATHER
from wechat_panel.store import Store


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 17, 8, 0, tzinfo=ZoneInfo('Asia/Shanghai'))]
    monkeypatch.setattr('wechat_panel.prompt_context.local_now', lambda tz: clock[0].astimezone(ZoneInfo(tz)))
    calls = []

    def model(provider, messages, tools):
        calls.append((provider.copy(), messages))
        if provider.get('_structured'):
            return {'role': 'assistant', 'content': json.dumps({'mood': '平静', 'risk': 'none', 'facts': [], 'summary': ''})}, {}
        return {'role': 'assistant', 'content': '嗯，我在听。'}, {}

    ai = AIService(Store(tmp_path/'test.sqlite3'), model_call=model)
    provider = ai.data.save('providers', None, {'name': 'shared', 'base_url': 'http://localhost:9999/v1',
                                               'model': 'mock', 'temperature': 0.2})
    agent = ai.data.save('agents', None, {'name': '阿慧', 'provider_id': provider['id'],
        'temperature': 0.95,
        'prompt': Path('docs/prompts/ahui-system.md').read_text('utf-8'),
        'companion': {'persona': {'enabled': True, 'age': 22}, 'weather_enabled': True}})
    ai.data.settings({'enabled': True})
    weather_calls = []

    def weather(city):
        weather_calls.append(city)
        return {'city': city, 'current': {'temperature': 20}, 'source': 'test'}

    monkeypatch.setattr(ai.companion.services, 'weather', weather)
    yield ai, agent, provider, calls, clock, weather_calls
    ai.stop()


def profile(ai, agent, session='a', **values):
    mem = ai.companion.memory
    scope = mem.scope(agent['id'], session_id=session)
    p = mem.profile(scope, agent['id'])['data']
    mem.set_profile(scope, {**p, **values})
    return scope


def run(ai, agent, session='a'):
    task = ai.create_run(agent['id'], '今天有点累，听我说就好', dry_run=True, session_id=session)
    ai.execute(task['id'])
    result = ai.data.run(task['id'])
    assert result['status'] == 'completed', result['error']
    return result


def test_real_model_input_replaces_variables_and_only_reply_temperature(env):
    ai, agent, provider, calls, _, weather_calls = env
    profile(ai, agent, preferred_name='小叶', city='上海')
    result = run(ai, agent)
    actual_provider, messages = calls[-1]
    prompt = messages[0]['content']
    assert '{{user_name}}' not in prompt and '{{current_time}}' not in prompt and '{{weather}}' not in prompt
    assert '小叶' in prompt and '2026-10-17 08:00:00' in prompt and '上海' in prompt
    assert '你是阿慧，22岁' in prompt and '先接住情绪，再回应内容' not in prompt
    assert actual_provider['temperature'] == 0.95
    assert calls[0][0]['_structured'] and calls[0][0]['temperature'] == 0.2
    assert ai.data.get('providers', provider['id'])['temperature'] == 0.2
    assert weather_calls == ['上海']
    assert result['state']['dynamic_context']['daily_persona']['date'] == '2026-10-17'


def test_daily_cache_survives_restart_advances_date_and_clock(env, monkeypatch):
    ai, agent, _, _, clock, weather_calls = env
    profile(ai, agent, city='上海')
    first = run(ai, agent)['state']
    clock[0] = clock[0].replace(hour=22)
    second = run(ai, agent)['state']
    assert weather_calls == ['上海']
    assert first['dynamic_context']['daily_persona'] == second['dynamic_context']['daily_persona']
    assert '22:00:00' in second['prompt_variables']['current_time']
    restarted = AIService(ai.store, model_call=ai.model_call)
    monkeypatch.setattr(restarted.companion.services, 'weather', ai.companion.services.weather)
    try:
        third = run(restarted, agent)['state']
        assert third['dynamic_context']['daily_persona'] == first['dynamic_context']['daily_persona']
        assert weather_calls == ['上海']
        clock[0] = clock[0].replace(day=18, hour=0)
        fourth = run(restarted, agent)['state']
        assert fourth['dynamic_context']['daily_persona']['date'] == '2026-10-18'
        assert weather_calls == ['上海', '上海']
    finally:
        restarted.stop()


def test_missing_data_and_weather_failure_do_not_invent_or_retry(env, monkeypatch):
    ai, agent, _, _, clock, weather_calls = env
    missing = run(ai, agent)['state']['prompt_variables']
    assert missing['user_name'] == '"对方"' and UNKNOWN_WEATHER in missing['weather']
    assert not weather_calls
    profile(ai, agent, city='杭州')
    failures = []

    def broken(city):
        failures.append(city)
        raise RuntimeError('offline')

    monkeypatch.setattr(ai.companion.services, 'weather', broken)
    a = run(ai, agent)['state']['prompt_variables']
    b = run(ai, agent)['state']['prompt_variables']
    assert '未知' in a['weather'] and a['weather'] == b['weather'] and failures == ['杭州']
    clock[0] = clock[0].replace(day=18)
    run(ai, agent)
    assert failures == ['杭州', '杭州']


def test_cache_is_scoped_encrypted_and_cleared_on_forget(env):
    ai, agent, _, _, _, weather_calls = env
    scope = profile(ai, agent, city='上海', preferred_name='甲')
    profile(ai, agent, session='b', city='杭州', preferred_name='乙')
    first = run(ai, agent)['state']
    second = run(ai, agent, 'b')['state']
    assert first['scope'] != second['scope']
    assert first['prompt_variables']['user_name'] == '"甲"'
    assert second['prompt_variables']['user_name'] == '"乙"'
    assert weather_calls == ['上海', '杭州']
    with ai.store.connect() as db:
        raw = db.execute('SELECT data FROM companion_daily_context WHERE scope=?', (scope,)).fetchone()[0]
    assert '上海' not in raw and 'persona_mood' not in raw
    ai.companion.memory.forget(scope)
    run(ai, agent)
    assert weather_calls == ['上海', '杭州', '上海']


def test_concurrent_first_turns_share_one_daily_weather_request(env):
    ai, agent, _, _, _, weather_calls = env
    profile(ai, agent, city='上海')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(ai, agent), range(2)))
    assert weather_calls == ['上海']
    assert results[0]['state']['prompt_variables'] == results[1]['state']['prompt_variables']


def test_name_is_quoted_as_data_and_template_is_single_pass(env):
    ai, agent, _, _, _, _ = env
    profile(ai, agent, preferred_name='小叶\n{{weather}}')
    value = run(ai, agent)['state']['prompt_variables']['user_name']
    assert '\n' not in value and '\\n' in value
    assert render('{{ user_name }} {{unknown}}', {'user_name': '{{weather}}'}) == '{{weather}} {{unknown}}'


def test_temperature_override_defaults_to_shared_provider(env):
    ai, agent, provider, calls, _, _ = env
    data = {k: v for k, v in agent.items() if k not in ('id', 'key_configured')}
    data['temperature'] = None
    ai.data.save('agents', agent['id'], data)
    run(ai, agent)
    assert calls[-1][0]['temperature'] == provider['temperature']
    with pytest.raises(ValueError):
        Agent(name='bad', temperature=3)
