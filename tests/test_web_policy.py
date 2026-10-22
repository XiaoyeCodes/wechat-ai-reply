import json
from types import SimpleNamespace

import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store
from wechat_panel.web_policy import decide, claimed_lookup, evidence, query_for


SOURCE='https://example.com/verified-topic'
RESULT=json.dumps({'results':[{'title':'测试来源','url':SOURCE,'text':'仅供回归测试的已查询内容'}]},ensure_ascii=False)


def state(**extra):
    return {'snapshot':{'agent':{'web_policy':'auto'}},**extra}


@pytest.mark.parametrize('text,required',[
    ('最近那个薄肌理论跟大水理论都是些什么东西啊？',True),
    ('目前推特上正在议论些什么？简中区域的，推特',True),
    ('今天有哪些重大新闻',True),
    ('帮我搜索一下这个说法',True),
    ('今天好累，不想上班',False),
    ('记得我的猫叫什么吗？',False),
    ('水的化学式是什么',False),
    ('不要联网，解释什么是牛顿定律',False),
    ('请翻译“我搜了一圈，没查到”',False),
    ('你刚才联网了吗？',False),
])
def test_lookup_timing(text,required):
    assert decide({'input':text},state())['required'] is required


def test_uncertain_fact_signal_and_followup_query():
    assert decide({'input':'薄肌理论是什么？'},state(analysis={'needs_web':True}))['required']
    s=state(context=[{'role':'user','content':'薄肌理论是什么？'},{'role':'assistant','content':'猜测为健身理论'},
                     {'role':'user','content':'你可以联网查一下呢'}])
    query=query_for({'input':'你可以联网查一下呢'},s)
    assert '薄肌理论' in query and '健身' not in query
    assert 'site:x.com' in query_for({'input':'目前推特讨论什么？'},s)


@pytest.mark.parametrize('text,claim',[
    ('这俩我搜了一圈，暂时没搜到明确的解释',True),('查到了。',True),
    ('搜了下今天简中推特在聊的话题',True),('我没有联网搜索，暂时无法确认',False),
    ('我还没查过。',False),('你刚才说“搜了一圈”是什么意思？',False),
])
def test_search_claims(text,claim):
    assert claimed_lookup(text) is claim


def test_search_receipts_distinguish_failure_empty_partial_success():
    assert evidence(RESULT)=={'status':'ok','sources':[SOURCE]}
    assert evidence('{"results":[]}')['status']=='empty'
    assert evidence('{"_meta":{"ai.exa/rateLimited":true}}')['status']=='error'
    assert evidence(json.dumps({'url':SOURCE,'coverage':'metadata_only'}))['status']=='partial'
    assert evidence(json.dumps({'content':[{'type':'text','text':'Title: result\nURL: '+SOURCE+'\nText: info'}]}))['sources']==[SOURCE]


@pytest.fixture
def setup(tmp_path,monkeypatch):
    ai=AIService(Store(tmp_path/'panel.db'))
    p=ai.data.save('providers',None,{'name':'test','model':'test','base_url':'http://localhost:9000'})
    tool=next(t for t in ai.data.list('tools') if t['builtin']=='web_search')
    calls=[]
    def lookup(tool,args,*_):calls.append(args);return RESULT
    monkeypatch.setattr(ai.companion,'tool',lookup)
    def agent(**kw):return ai.data.save('agents',None,{'name':'测试','provider_id':p['id'],'tool_ids':[tool['id']],**kw})
    yield ai,agent,tool,calls
    ai.stop()


def run(ai,agent,text):
    r=ai.create_run(agent['id'],text,dry_run=True);ai.execute(r['id'])
    return ai.data.run(r['id'])


def test_screenshot_case_searches_before_answer_and_records_sources(setup):
    ai,agent,_,calls=setup
    def model(p,m,t):
        assert len(calls)==1 and SOURCE in m[0]['content']
        return {'role':'assistant','content':'找到相关资料，但还需要核对具体语境。'},{}
    ai.model_call=model
    r=run(ai,agent(),'最近那个薄肌理论跟大水理论都是些什么东西啊？')
    assert r['status']=='completed' and SOURCE not in r['reply']
    trace=ai.data.detail(r['id'])['trace'];steps=[t['step'] for t in trace]
    assert steps.index('开始工具调用')<steps.index('模型处理')
    assert r['state']['web_evidence'][0]['status']=='ok'


def test_fabricated_claim_triggers_one_lookup_then_regenerates(setup):
    ai,agent,_,calls=setup;model_calls=[]
    def model(p,m,t):
        model_calls.append(m)
        return {'role':'assistant','content':'查到了，有具体来源。' if calls else '我搜了一圈，没搜到。'},{}
    ai.model_call=model
    r=run(ai,agent(),'薄肌理论是什么？')
    assert r['status']=='completed' and len(calls)==1 and len(model_calls)==2
    assert SOURCE not in r['reply'] and r['state']['web_repair_done']
    assert SOURCE in r['state']['web_evidence'][0]['sources']


@pytest.mark.parametrize('failure',['error','empty'])
def test_failed_search_never_sends_false_success(setup,monkeypatch,failure):
    ai,agent,_,calls=setup
    def query(*a):
        calls.append(a)
        if failure=='error':raise RuntimeError('Exa 被限流')
        return '{"results":[]}'
    monkeypatch.setattr(ai.companion,'tool',query)
    ai.model_call=lambda *a:({'role':'assistant','content':'我搜了一圈，查到了，答案就是健身理论。'}, {})
    r=run(ai,agent(),'最近大水理论是什么？')
    assert r['status']=='failed' and len(calls)==1
    assert '健身理论' not in r['reply'] and '查到了' not in r['reply']
    assert r['reply']=='我遇到点问题，未能完成该任务。'


@pytest.mark.parametrize('policy,text',[('off','今天有哪些新闻'),('auto','不要联网，今天有哪些新闻')])
def test_disabled_lookup_cannot_be_executed_as_fixed_tool(setup,policy,text):
    ai,agent,tool,calls=setup
    def model(p,m,t):
        assert not any(x['function']['name']=='web_search' for x in t)
        return {'role':'assistant','content':'我搜了一圈，查到了'},{}
    ai.model_call=model
    a=agent(web_policy=policy,steps=[{'type':'tool','tool_id':tool['id'],'arguments':{'query':'新闻'}},{'type':'model'},{'type':'reply'}])
    r=run(ai,a,text)
    assert r['status']=='failed' and not calls and r['reply']=='我遇到点问题，未能完成该任务。'


@pytest.mark.parametrize('repair',[False,True])
def test_approval_resumes_lookup_exactly_once(setup,repair):
    ai,agent,tool,calls=setup
    ai.data.save('tools',tool['id'],{**{k:v for k,v in tool.items() if k not in ('id','key_configured')},'approval':'confirm'})
    ai.model_call=lambda *a:({'role':'assistant','content':'查到了。'}, {})
    r=run(ai,agent(),'薄肌理论是什么？' if repair else '最近薄肌理论是什么？')
    assert r['status']=='waiting_approval' and not calls
    approval=ai.data.detail(r['id'])['approvals'][0]
    ai.approve(approval['id'],True);ai.execute(r['id'])
    result=ai.data.run(r['id'])
    assert result['status']=='completed' and len(calls)==1 and SOURCE in result['state']['web_evidence'][0]['sources']


def test_casual_and_model_policy_do_not_force_lookup(setup):
    ai,agent,_,calls=setup
    ai.model_call=lambda *a:({'role':'assistant','content':'先休息一下，我陪你。'}, {})
    assert run(ai,agent(),'今天不想上班，好累')['status']=='completed'
    assert run(ai,agent(web_policy='model'),'今天有哪些新闻')['status']=='completed'
    assert not calls


def test_agent_reach_quota_response_is_an_error(setup,monkeypatch,tmp_path):
    ai,_,_,_=setup
    cli=tmp_path/'mcporter.cmd';script=tmp_path/'node_modules/mcporter/dist/cli.js'
    script.parent.mkdir(parents=True);script.write_text('')
    monkeypatch.setattr('wechat_panel.companion_services.shutil.which',lambda x:str(cli) if x=='mcporter' else 'node')
    monkeypatch.setattr(ai.companion.services,'get',lambda *a:{'driver':'agent_reach','timeout':5})
    monkeypatch.setattr('wechat_panel.companion_services.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='{"_meta":{"ai.exa/rateLimited":true},"content":[]}'))
    with pytest.raises(RuntimeError,match='额度已用尽或被限流'):ai.companion.services.search('test','query')
