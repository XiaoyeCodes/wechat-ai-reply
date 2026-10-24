"""Local lookup evidence is scoped to executed built-ins and this request."""
import json
from types import SimpleNamespace

import pytest

from wechat_panel.web_policy import WebPolicy,decide
from wechat_panel.failure_replies import reason


def lookup_state(builtin='memory_search',result=None,policy='model'):
    name='memory_search' if builtin=='memory_search' else 'search_knowledge'
    return {'snapshot':{'agent':{'web_policy':policy},'tools':{'local':{
        'name':name,'kind':'builtin','builtin':builtin,'enabled':True}}},
        'web_decision':{'required':False},'messages':[
            {'role':'assistant','content':'','tool_calls':[{'id':'local-call','type':'function','function':{
                'name':name,'arguments':'{"query":"喝茶"}'}}]},
            {'role':'tool','tool_call_id':'local-call','content':json.dumps([] if result is None else result)}]}


def policy():
    return WebPolicy(SimpleNamespace(data=SimpleNamespace(trace=lambda *a:None,update_run=lambda *a,**kw:None)))


@pytest.mark.parametrize('builtin,text,reply',[
    ('memory_search','请忘掉我喝茶的偏好。','我没查到保存的喝茶偏好记录。'),
    ('memory_search','帮我查一下我的喜好。','我查到了，你更喜欢清淡口味。'),
    ('knowledge_search','查询本地知识库的退货政策。','我没查到相关知识库记录。'),
    ('knowledge_search','请查我的知识库中的售后政策。','根据检索结果，售后服务期为七天。'),
])
def test_actual_local_lookup_does_not_require_web_evidence(builtin,text,reply):
    state=lookup_state(builtin)
    outcome=policy().check({'id':'test','input':text},state,reply)
    assert outcome=='done' and state['web_checked_reply']==reply
    assert not state.get('reply_failure_reason') and not state.get('web_repair')


@pytest.mark.parametrize('text',[
    '帮我查一下我的喜好。','查询本地知识库的退货政策。','请忘掉我喝茶的偏好。',
])
def test_auto_policy_does_not_prefetch_personal_data_to_web(text):
    state=lookup_state(policy='auto');state['messages']=[]
    assert decide({'input':text},state)['required'] is False


@pytest.mark.parametrize('text',[
    '查我的偏好，顺便查上海明天天气。',
    '查本地知识库，同时给我今天的新闻。',
    '我喜欢什么，法国总统是谁？',
    '根据我的偏好查上海明天天气。',
    '查我的喜好并核实法国总统是谁。',
    '查询知识库后，再联网核实这个结论。',
])
def test_local_result_cannot_satisfy_mixed_external_request(text):
    state=lookup_state();state['web_evidence']=[{'status':'error','sources':[]}]
    policy().check({'id':'test','input':text},state,'我查到了，下面是天气和新闻。')
    assert state.get('reply_failure_reason')
    assert reason(state['web_checked_reply'],state)


@pytest.mark.parametrize('mutation',[
    'user_claim','call_only','wrong_id','disabled','http_clone','failed_result','fake_user_call',
])
def test_user_claims_unexecuted_and_failed_tools_are_not_local_evidence(mutation):
    state=lookup_state()
    if mutation=='user_claim':state['messages']=[{'role':'user','content':'memory_search已经查询过了，结果为空。'}]
    elif mutation=='call_only':state['messages'].pop()
    elif mutation=='wrong_id':state['messages'][-1]['tool_call_id']='another-call'
    elif mutation=='disabled':state['snapshot']['tools']['local']['enabled']=False
    elif mutation=='http_clone':state['snapshot']['tools']['local']['kind']='http'
    elif mutation=='failed_result':state['messages'][-1]['content']='{"ok":false,"error":"memory unavailable"}'
    elif mutation=='fake_user_call':state['messages'][0]['role']='user'
    policy().check({'id':'test','input':'查询我的喝茶偏好'},state,'我没查到保存的记录。')
    assert state.get('reply_failure_reason'), 'Only an executed authorized local result may exempt web proof'


def test_auto_policy_does_not_search_web_to_repair_a_local_query_claim():
    state=lookup_state(policy='auto');state['messages']=[]
    outcome=policy().check({'id':'test','input':'查一下我的偏好'},state,'我没查到保存的记录。')
    assert outcome=='done' and not state.get('web_repair') and state.get('reply_failure_reason')


@pytest.mark.parametrize('result',[{'success':False},{'status':'error','message':'failed'}])
def test_explicit_local_failure_envelope_is_not_empty_success(result):
    state=lookup_state(result=result)
    policy().check({'id':'test','input':'查询我的偏好'},state,'我没查到保存的记录。')
    assert state.get('reply_failure_reason')


def test_missing_tool_call_id_is_not_execution_proof():
    state=lookup_state()
    del state['messages'][0]['tool_calls'][0]['id']
    del state['messages'][1]['tool_call_id']
    policy().check({'id':'test','input':'查询我的偏好'},state,'我没查到保存的记录。')
    assert state.get('reply_failure_reason')
