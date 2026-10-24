"""Weather provider coexistence using fake model/providers only, without Runtime."""
import json

import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store
from wechat_panel import mcp_client,mcp_policy
from wechat_panel.failure_replies import REPLY


FORECAST={'city':'上海','country':'中国','source':'https://open-meteo.com/',
    'weather':{'time':'2026-10-23T22:00','temperature_2m':20.5,'weather_code':3},
    'daily':{'time':['2026-10-23','2026-10-24'],'temperature_2m_max':[24,23],
        'temperature_2m_min':[17,18],'precipitation_probability_max':[10,80]},
    'timezone':'Asia/Shanghai'}
ANSWER='上海明天降雨概率80%，建议带伞。'


@pytest.fixture
def env(tmp_path,monkeypatch):
    ai=AIService(Store(tmp_path/'panel.sqlite3'))
    provider=ai.data.save('providers',None,{'name':'fake','model':'fake','base_url':'http://127.0.0.1:9/v1'})
    weather=next(t for t in ai.data.list('tools') if t.get('builtin')=='weather')
    connection=next(c for c in ai.data.list('mcp') if c['profile']=='amap')
    amap=ai.data.save('tools',None,dict(name='fake_amap_weather',description='Fake read-only weather',kind='mcp',connection_id=connection['id'],
        remote_name='maps_weather',parameters={'type':'object','properties':{'city':{'type':'string'}},
            'required':['city'],'additionalProperties':False},approval='auto'))
    agent=ai.data.save('agents',None,dict(name='weather-coexistence',provider_id=provider['id'],
        tool_ids=[weather['id'],amap['id']],web_policy='model',max_tool_rounds=4,
        companion={'memory':{'enabled':False},'safety':{'emotion_enabled':False,'crisis_detection':False}}))
    calls=[]
    def builtin(city):
        calls.append(('weather',city))
        return json.dumps(FORECAST,ensure_ascii=False)
    def remote(*args):
        calls.append(('amap',args[-1]))
        return json.dumps({'ok':True,'result':{'structuredContent':{'status':'1','weather':'小雨'}}})
    monkeypatch.setattr(ai.companion.services,'weather',builtin)
    monkeypatch.setattr(mcp_client,'execute',remote)
    yield ai,agent,weather,amap,calls
    ai.stop()


def perform(env,request,chosen='weather',answer=ANSWER):
    ai,agent,weather,amap,calls=env
    selected=weather if chosen=='weather' else amap
    def model(provider,messages,tools):
        if any(m['role']=='tool' for m in messages):return {'role':'assistant','content':answer},{}
        return {'role':'assistant','content':'','tool_calls':[{'id':'one-weather-read','type':'function','function':{
            'name':selected['name'],'arguments':json.dumps({'city':'上海'})}}]},{}
    ai.model_call=model
    run=ai.create_run(agent['id'],request,dry_run=True)
    ai.execute(run['id'])
    return ai.data.run(run['id'])


@pytest.mark.parametrize('chosen',['weather','amap'])
def test_either_authorized_weather_provider_can_complete_weather_only_request(env,chosen):
    ai,_,_,_,calls=env
    run=perform(env,'查一下上海明天天气，告诉我带不带伞',chosen)
    assert run['status']=='completed' and run['reply']==ANSWER,(run['status'],run['error'],run['reply'])
    assert len(calls)==1 and calls[0][0]==chosen
    if chosen=='weather':
        assert run['state']['weather_results'][0]['ok'] is True
        assert not run['state'].get('mcp_results') and not run['state'].get('mcp_repair')
    else:assert run['state']['mcp_results'][0]['ok'] is True
    ai.execute(run['id'])
    assert len(calls)==1 and not ai.store.pending(), 'Completed queries are not replayed'


@pytest.mark.parametrize('query',[
    '请用高德查询上海明天天气',
    'Use AMap，查一下上海明天天气',
    '上海明天天气怎么样，顺便从虹桥到外滩怎么走？',
    '查上海明天天气，再帮我找附近咖啡店',
    '上海明天天气和交通路况怎么样？',
])
def test_builtin_weather_cannot_replace_explicit_amap_or_mixed_location_request(env,query):
    ai,_,_,_,calls=env
    run=perform(env,query)
    assert run['status']=='failed' and run['reply']==REPLY
    assert run['state']['reply_failure_reason']
    assert len(calls)==1 and not ai.store.pending()


@pytest.mark.parametrize('failure',['exception','empty','error_payload'])
def test_failed_weather_returns_one_short_failure_without_forced_requery(env,monkeypatch,failure):
    ai,_,_,_,calls=env
    def unavailable(city):
        calls.append(('weather',city))
        if failure=='exception':raise RuntimeError('fixture connection config missing')
        if failure=='error_payload':return json.dumps({'ok':False,'error':'HTTP 403','source':'https://open-meteo.com/'})
        return json.dumps({'city':'上海','source':'https://open-meteo.com/','weather':{},'daily':{}})
    monkeypatch.setattr(ai.companion.services,'weather',unavailable)
    run=perform(env,'查一下上海明天天气')
    assert run['status']=='failed' and run['reply']==REPLY
    assert len(calls)==1 and not run['state'].get('mcp_repair')
    ai.execute(run['id'])
    assert len(calls)==1 and not ai.store.pending()


@pytest.mark.parametrize('capability',['amap','mindmap'])
def test_failed_professional_tool_uses_uniform_failure(capability):
    state={'mcp_route':capability,'mcp_results':[{'capability':capability,'ok':False}]}
    answer,repair=mcp_policy.check({'input':'上海天气'},state,'服务错误，请检查API配置')
    assert answer==REPLY and repair is False and state['reply_failure_reason']


def test_arbitrary_web_evidence_does_not_satisfy_weather_or_amap_proof():
    state={'mcp_route':'amap','mcp_repair':True,'web_evidence':[{
        'status':'ok','sources':['https://open-meteo.com/']}],'snapshot':{'tools':{}}}
    answer,repair=mcp_policy.check({'input':'上海明天天气'},state,ANSWER)
    assert answer==REPLY and not repair and state['reply_failure_reason']


def test_prompt_allows_weather_choice_and_identifies_actual_source(env):
    ai,agent,_,_,_=env
    state={'snapshot':ai.snapshot(agent['id']),'context':[]}
    prompt=mcp_policy.prompt({'input':'上海明天天气'},state)
    assert 'Open-Meteo' in prompt and 'weather' in prompt
    assert '重复' in prompt and '来源' in prompt


def test_confirmed_weather_approval_is_not_bypassed_or_replayed(env):
    ai,agent,weather,_,calls=env
    ai.data.save('tools',weather['id'],{k:v for k,v in weather.items() if k not in ('id','key_configured')}|{'approval':'confirm'})
    run=perform(env,'上海明天天气')
    assert run['status']=='waiting_approval' and calls==[] and not run['state'].get('weather_results')
    approval=ai.data.detail(run['id'])['approvals'][0]
    ai.approve(approval['id'],True);ai.execute(run['id'])
    completed=ai.data.run(run['id'])
    assert completed['status']=='completed' and completed['reply']==ANSWER and len(calls)==1
    ai.execute(run['id'])
    assert len(calls)==1 and not ai.store.pending()
