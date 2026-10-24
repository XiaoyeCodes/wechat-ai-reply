import json
from pathlib import Path

import pytest

from wechat_panel import news_native as native
from wechat_panel.ai import AIService
from wechat_panel.ai_store import AIStore
from wechat_panel.skills import import_package
from wechat_panel.store import Store

STAMP = '2026-10-18T05:00:00+00:00'
ITEMS = [
    {'source':'Hacker News','title':'An open research tool','url':'https://example.org/research',
     'time':'Today','content':'A team published an open research tool.','heat':'100'},
    {'source':'WallStreetCN','title':'经济报道','url':'https://example.net/economy',
     'time':'2026-10-18 10:00','content':'新的经济数据已经发布。'},
]


def package():
    manifest=native.verify_package()
    return import_package([{'path':name,'content':(native.ROOT/name).read_text(encoding='utf8')}
                           for name in manifest['sha256']])


def state():
    return {'snapshot':{'agent':{'web_policy':'model','companion':{'news_verify':True}},
                       'skills':{'native':{'name':native.NAME,'enabled':True}}}}


@pytest.fixture
def scripts(monkeypatch):
    calls=[]
    def execute(script,args,timeout=210):
        calls.append((script,args))
        if args==['--list-sources']:return 'hackernews\nwallstreetcn\ninternational\n',''
        data={'global_scan':ITEMS} if script=='daily_briefing.py' else ITEMS
        return json.dumps(data,ensure_ascii=False),'one upstream fetcher timed out'
    monkeypatch.setattr(native,'run_script',execute)
    clear_sources=native.sources.cache_clear
    clear_sources()
    yield calls
    clear_sources()


def test_native_files_unmodified_and_importable():
    manifest=native.verify_package()
    assert manifest['commit']=='b639b622a79ec242669d6111708efbf863592de4'
    p=package()
    assert p['name']==native.NAME and len(p['files'])==17
    assert 'Universal Workflow' in p['instructions']
    assert not (native.ROOT/'references/provenance.md').exists()


def test_display_time_converts_explicit_zone_without_changing_original():
    items=[{'url':'a','time':'Sat, 17 Oct 2026 22:09:50 GMT'},
           {'url':'b','time':'Sat, 17 Oct 2026 12:54:47 GMT'},
           {'url':'c','time':'Today'},{'url':'d','time':'2026-10-18 10:00'}]
    assert native.time_hints(items)==[
        {'url':'a','source_time':items[0]['time'],'source_time_beijing':'2026-10-18T06:09:50+08:00'},
        {'url':'b','source_time':items[1]['time'],'source_time_beijing':'2026-10-17T20:54:47+08:00'}]
    assert items[0]['time']=='Sat, 17 Oct 2026 22:09:50 GMT'


def test_collect_passes_original_parameters_and_json_without_publisher_filter(scripts,tmp_path):
    s=state();r={'created_at':STAMP}
    payload=json.loads(native.collect({'source':'hackernews,wallstreetcn','keyword':'AI,经济','limit':3,'deep':True},r,s,tmp_path))
    assert scripts[-1]==('fetch_news.py',['--source','hackernews,wallstreetcn','--limit','3','--no-save','--keyword','AI,经济','--deep'])
    assert payload['data']==ITEMS and payload['engine']==native.NAME+'/native'
    assert payload['receipt']['items']==2 and payload['receipt']['deep_items']==2
    assert json.loads(Path(payload['receipt']['raw_file']).read_text(encoding='utf8'))==ITEMS
    assert '2026-10-18' in payload['receipt']['raw_file']


def test_native_profile_keeps_grouped_result_and_instructions(scripts,tmp_path):
    result=json.loads(native.collect({'profile':'finance'},{'created_at':STAMP},state(),tmp_path))
    script,args=scripts[-1]
    assert script=='daily_briefing.py' and args[:3]==['--profile','finance','--no-save']
    assert result['data']=={'global_scan':ITEMS}
    assert (native.ROOT/'instructions/briefing_finance.md').read_text(encoding='utf8') in result['instructions']


@pytest.mark.parametrize('args',[
    {'source':'../../evil'}, {'source':'hackernews;echo hello'}, {'profile':'finance','limit':3},
    {'profile':'unknown'}, {'limit':True}, {'limit':100}, {'deep':'true'}, {'keyword':[]}, {'command':'ls'},
])
def test_no_arbitrary_commands_or_invalid_native_arguments(scripts,tmp_path,args):
    with pytest.raises(ValueError):native.collect(args,{'created_at':STAMP},state(),tmp_path)
    assert all(a==['--list-sources'] for _,a in scripts)


def test_unbound_skill_and_changed_installation_are_rejected(scripts,tmp_path,monkeypatch):
    s=state();s['snapshot']['skills']={}
    with pytest.raises(ValueError,match='绑定'):native.collect({}, {'created_at':STAMP},s,tmp_path)
    monkeypatch.setattr(native,'ROOT',tmp_path)
    (tmp_path/'upstream.json').write_text(json.dumps({'sha256':{'script.py':'wrong'}}))
    (tmp_path/'script.py').write_text('changed')
    with pytest.raises(ValueError,match='变化'):native.verify_package()
    assert not scripts


@pytest.mark.parametrize('text',[
    '汇总5条今日热点新闻','今天只看民生新闻','我只想看关于国际的','整理3条财经新闻',
    '最近有什么新闻','来一份科技早报','如意如意','今天的 GitHub 开源项目','Summarize today news',
])
def test_native_routing(text):
    assert native.requested({'input':text},state())


@pytest.mark.parametrize('text',[
    '今天好累','新闻编辑是做什么的','你看国际新闻吗','不要联网，汇总今日新闻',
    '帮我翻译国际新闻','经济是什么','今天不想看新闻','我想看国际电影',
])
def test_conversation_transform_and_no_search_are_not_news_tasks(text):
    assert not native.requested({'input':text},state())


@pytest.fixture
def runtime(tmp_path,scripts):
    ai=AIService(Store(tmp_path/'panel.db'))
    provider=ai.data.save('providers',None,{'name':'test','model':'test','base_url':'http://localhost:9000'})
    skill=ai.data.save('skills',None,package())
    tools=[t for t in ai.data.list('tools') if t.get('builtin') in ('news_collect','use_skill','read_skill_file')]
    agent=ai.data.save('agents',None,{'name':'Aria','provider_id':provider['id'],'tool_ids':[t['id'] for t in tools],
        'skill_ids':[skill['id']],'prompt':'像平时一样聊天，不使用列表。','web_policy':'model',
        'companion':{'news_verify':True},'steps':[{'type':'model'},{'type':'reply'}]})
    def model(p,m,t):
        if '本轮原生新闻技能' not in m[0]['content']:return {'content':'我在呢。'},{}
        assert 'Universal Workflow' in m[0]['content'] and '澎湃' not in m[0]['content']
        result=next((x for x in m if x['role']=='tool'),None)
        if result:
            assert json.loads(result['content'])['data']==ITEMS
            return {'content':'## 新闻简报\n\n1. [研究工具](https://example.org/research)\n\n来源：Hacker News | 时间：Today\n\n2. [经济报道](https://example.net/economy)\n\n来源：WallStreetCN | 时间：2026-10-18 10:00'},{}
        return {'role':'assistant','content':None,'tool_calls':[{'id':'collect-1','type':'function','function':{
            'name':'news_collect','arguments':json.dumps({'source':'hackernews,wallstreetcn','limit':2,'deep':True})}}]},{}
    ai.model_call=model
    yield ai,agent,scripts,tools
    ai.stop()


def run(ai,agent,text='汇总5条今日热点新闻'):
    r=ai.create_run(agent['id'],text,dry_run=True)
    ai.execute(r['id'])
    return ai.data.run(r['id'])


def test_complete_native_workflow_saves_report_without_legacy_pipeline(runtime):
    ai,agent,calls,_=runtime;r=run(ai,agent)
    assert r['status']=='completed',r['error']
    assert len([x for x in calls if '--no-save' in x[1]])==1
    assert 'news_work' not in r['state'] and 'news_packet' not in r['state']
    assert r['state']['active_skills']==[native.NAME]
    report=Path(r['state']['native_news_report'])
    assert report.read_text(encoding='utf8')==r['reply']
    assert 'example.org' in r['reply'] and 'example.net' in r['reply']
    steps=[t['step'] for t in ai.data.detail(r['id'])['trace']]
    assert '原生新闻脚本执行' in steps and '新闻日期与来源核验' not in steps
    assert run(ai,agent,'今天好累')['reply']=='我在呢。'
    assert len([x for x in calls if '--no-save' in x[1]])==1


@pytest.mark.parametrize('builtin',['use_skill','news_collect'])
def test_approval_resume_does_not_repeat_collection(runtime,builtin):
    ai,agent,calls,tools=runtime
    tool=next(t for t in tools if t['builtin']==builtin)
    ai.data.save('tools',tool['id'],{k:v for k,v in {**tool,'approval':'confirm'}.items() if k not in ('id','key_configured')})
    r=run(ai,agent)
    assert r['status']=='waiting_approval' and calls==[]
    approval=ai.data.detail(r['id'])['approvals'][0]
    ai.approve(approval['id'],True);ai.execute(r['id'])
    assert ai.data.run(r['id'])['status']=='completed'
    assert len([x for x in calls if '--no-save' in x[1]])==1


def test_unexecuted_collection_cannot_claim_news(runtime):
    ai,agent,calls,_=runtime
    ai.model_call=lambda *args:({'content':'我查到今天五条新闻'}, {})
    r=run(ai,agent)
    assert r['status']=='completed' and '未能执行' in r['reply'] and calls==[]
    assert r['state']['native_news_repair']


def test_empty_fetch_does_not_return_model_inventions(runtime,monkeypatch):
    ai,agent,_,_=runtime
    monkeypatch.setattr(native,'run_script',lambda *a,**k:('[]',''))
    monkeypatch.setattr(native,'sources',lambda:{'hackernews','wallstreetcn'})
    original=ai.model_call
    ai.model_call=lambda p,m,t:({'content':'我找到五条重要新闻'}, {}) if any(x['role']=='tool' for x in m) else original(p,m,t)
    r=run(ai,agent)
    assert r['status']=='failed' and r['reply']=='我遇到点问题，未能完成该任务。'


def test_tool_schema_migration_preserves_user_permissions(tmp_path):
    store=Store(tmp_path/'panel.db');data=AIStore(store)
    t=next(t for t in data.list('tools') if t.get('builtin')=='news_collect')
    data.save('tools',t['id'],{k:v for k,v in {**t,'description':'旧采集器','enabled':False,'approval':'confirm',
        'parameters':{'type':'object','properties':{},'required':[],'additionalProperties':False}}.items() if k not in ('id','key_configured')})
    migrated=AIStore(store).get('tools',t['id'])
    assert not migrated['enabled'] and migrated['approval']=='confirm'
    assert migrated['parameters']['properties']==native.PROPERTIES and migrated['parameters']['required']==[]


@pytest.mark.parametrize('policy,text,links',[
    ('requested','汇总今天的新闻',False),
    ('requested','汇总今天的新闻，附上原文链接',True),
    ('requested','汇总新闻，不要链接',False),
    ('auto','汇总今天的新闻',True),
])
def test_news_respects_agent_source_preference(tmp_path,policy,text,links):
    from types import SimpleNamespace
    import uuid
    s=state();s['snapshot']['agent']['source_policy']=policy
    s['native_news']={'instructions':'报告模板要求附原文链接。','menu':True}
    run={'id':str(uuid.uuid4()),'input':text,'created_at':STAMP}
    prompt=native.prompt(run,s)
    assert ('当前回复不展示原文、参考链接' in prompt) is (not links)
    ai=SimpleNamespace(store=SimpleNamespace(path=tmp_path/'panel.db'),data=SimpleNamespace(trace=lambda *args:None))
    reply='新闻摘要。\n\n原文：https://example.org/news'
    assert native.finish(ai,run,s,reply)=='done'
    assert ('https://example.org/news' in s['web_checked_reply']) is links
    assert Path(s['native_news_report']).read_text(encoding='utf-8')==s['web_checked_reply']
