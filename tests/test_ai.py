import json
import threading

import pytest

from wechat_panel.ai import AIService
from wechat_panel.ai_store import unprotect
from wechat_panel.ai_tools import calculate
from wechat_panel.store import Store


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'panel.sqlite3')
    ai=AIService(store,model_call=lambda p,m,t:({'role':'assistant','content':'你好，这是 AI 回复'},{}))
    provider=ai.data.save('providers',None,{'name':'本地模型','base_url':'http://127.0.0.1:8000/v1','model':'test-model','api_key':'secret-test-key'})
    agent=ai.data.save('agents',None,{'name':'电商 Agent','provider_id':provider['id']})
    ai.data.settings({'enabled':True,'debounce_seconds':.3,'context_messages':20})
    contact=store.add_contact({'lookup':'客户甲','nickname':'客户甲','wechat_id':'a','account_id':'self'})
    store.update_contact(contact['id'],listening=True,mode='ai_assist',ai_agent_id=agent['id'])
    store.ingest(contact['id'],[])
    yield store,ai,provider,agent,contact
    ai.stop()


def incoming(store,cid,text='你好',key='one'):
    store.ingest(cid,[{'key':key,'text':text,'kind':'text','direction':'incoming','account_id':'self'}])
    return store.messages(cid)[-1]


def test_secret_encrypted_and_not_returned_or_lost_on_edit(setup):
    store,ai,provider,_,_=setup
    private=ai.data.get('providers',provider['id'],private=True)
    assert unprotect(private['secret'])=='secret-test-key'
    assert 'secret-test-key' not in json.dumps(provider) and 'secret' not in provider
    with store.connect() as db:
        assert 'secret-test-key' not in db.execute("SELECT data FROM ai_resources WHERE kind='providers'").fetchone()[0]
    ai.data.save('providers',provider['id'],{'name':'更名','base_url':provider['base_url'],'model':provider['model'],'api_key':''})
    assert unprotect(ai.data.get('providers',provider['id'],private=True)['secret'])=='secret-test-key'


def test_knowledge_chinese_retrieval_is_limited_to_assigned_libraries(setup):
    _,ai,*_=setup
    a=ai.data.save('knowledge',None,{'name':'商品知识','documents':[{'title':'售后','content':'本店保温杯支持七天退货，运费规则以订单为准。'}]})
    b=ai.data.save('knowledge',None,{'name':'内部财务','documents':[{'title':'工资','content':'内部退货成本和员工工资不可对外分享。'}]})
    found=ai.data.search([a['id']],'退货规则')
    assert found and all(row['knowledge_id']==a['id'] for row in found)
    assert not ai.data.search([],'退货')


@pytest.mark.parametrize('mode,expected',[('ai_assist','draft'),('ai_auto','queued')])
def test_reply_modes_and_only_one_queued_send(setup,mode,expected):
    store,ai,_,agent,contact=setup
    cid=contact['id'];store.update_contact(cid,mode=mode)
    message=incoming(store,cid)
    run=ai.create_run(agent['id'],message['text'],cid,message['id'])
    ai.execute(run['id'])
    assert ai.data.run(run['id'])['status']==expected
    if expected=='draft':ai.send_draft(run['id'],'人工编辑后的回复')
    assert len(store.pending())==1
    assert store.ai_send_valid(store.pending()[0])
    with pytest.raises(ValueError):ai.send_draft(run['id'],'第二次')


def test_switching_agent_during_generation_discards_late_reply(setup):
    store,ai,provider,agent,contact=setup
    other=ai.data.save('agents',None,{'name':'财务 Agent','provider_id':provider['id']})
    store.update_contact(contact['id'],mode='ai_auto')
    message=incoming(store,contact['id'])
    run=ai.create_run(agent['id'],message['text'],contact['id'],message['id'])
    def slow_model(*args):
        store.update_contact(contact['id'],ai_agent_id=other['id'])
        return {'role':'assistant','content':'旧电商回复'},{}
    ai.model_call=slow_model;ai.execute(run['id'])
    assert ai.data.run(run['id'])['status']=='cancelled' and not store.pending()


def test_pause_then_resume_cannot_send_old_draft_or_queue(setup):
    store,ai,_,agent,contact=setup
    message=incoming(store,contact['id'])
    run=ai.create_run(agent['id'],message['text'],contact['id'],message['id']);ai.execute(run['id'])
    ai.data.cancel_all('暂停');store.pause(True);store.pause(False)
    with pytest.raises(ValueError):ai.send_draft(run['id'],'旧回复')
    assert not store.pending()


def test_tool_approval_resumes_checkpoint_without_repeating_prior_tool(setup,monkeypatch):
    store,ai,provider,_,contact=setup
    clock=next(t for t in ai.data.list('tools') if t['builtin']=='clock')
    http=ai.data.save('tools',None,{'name':'order_lookup','description':'查询订单','url':'http://127.0.0.1:8123/order',
        'parameters':{'type':'object','properties':{'id':{'type':'string'}},'required':['id']}})
    agent=ai.data.save('agents',None,{'name':'订单助手','provider_id':provider['id'],'tool_ids':[clock['id'],http['id']],
        'steps':[{'type':'tool','tool_id':clock['id']},{'type':'tool','tool_id':http['id'],'arguments':{'id':'{{message}}'}},{'type':'model'},{'type':'reply'}]})
    store.update_contact(contact['id'],ai_agent_id=agent['id'])
    message=incoming(store,contact['id'],'ORDER-42');run=ai.create_run(agent['id'],message['text'],contact['id'],message['id'])
    calls=[]
    def execute(tool,args,*rest):calls.append(tool['name']);return '订单已发货'
    monkeypatch.setattr('wechat_panel.ai.execute_tool',execute)
    ai.execute(run['id'])
    assert calls==[clock['name']]
    detail=ai.data.detail(run['id']);assert detail['status']=='waiting_approval'
    approval=detail['approvals'][0];assert approval['arguments']=={'id':'ORDER-42'}
    ai.approve(approval['id'],True);ai.execute(run['id'])
    assert calls==[clock['name'],'order_lookup']
    assert ai.data.run(run['id'])['status']=='draft'
    with pytest.raises(ValueError):ai.approve(approval['id'],True)


def test_model_function_call_and_whitelist(setup):
    store,ai,provider,_,contact=setup
    calc=next(t for t in ai.data.list('tools') if t['builtin']=='calculator')
    agent=ai.data.save('agents',None,{'name':'计算助手','provider_id':provider['id'],'tool_ids':[calc['id']]})
    store.update_contact(contact['id'],ai_agent_id=agent['id'])
    message=incoming(store,contact['id'],'20*3');run=ai.create_run(agent['id'],message['text'],contact['id'],message['id'])
    seen=[]
    def model(p,m,t):
        seen.append(m.copy())
        if len(seen)==1:return {'role':'assistant','content':'','tool_calls':[{'id':'c1','type':'function','function':{'name':'calculate','arguments':'{"expression":"20*3"}'}}]},{}
        assert m[-1]=={'role':'tool','tool_call_id':'c1','content':'60'}
        return {'role':'assistant','content':'结果是 60'},{}
    ai.model_call=model;ai.execute(run['id'])
    assert ai.data.run(run['id'])['reply']=='结果是 60'
    assert len(seen)==2


def test_external_agent_gets_isolated_contact_and_never_sends_directly(setup,monkeypatch):
    store,ai,_,_,contact=setup
    agent=ai.data.save('agents',None,{'name':'自建财务 Agent','kind':'external','url':'http://127.0.0.1:9000/agent'})
    store.update_contact(contact['id'],ai_agent_id=agent['id'])
    message=incoming(store,contact['id'],'账单核对');run=ai.create_run(agent['id'],message['text'],contact['id'],message['id'])
    def external(url,payload,**kw):
        assert payload['contact']['wechat_id']=='a' and payload['message']=='账单核对'
        return {'reply':'请提供本月账单'}
    monkeypatch.setattr('wechat_panel.ai.request_json',external);ai.execute(run['id'])
    assert ai.data.run(run['id'])['status']=='draft' and not store.pending()


def test_calculator_never_executes_code():
    assert calculate('(10+20)*3/2')=='45.0'
    for expression in ['__import__("os").system("echo bad")','10**10000000','[0]*100000000']:
        with pytest.raises((ValueError,SyntaxError)):calculate(expression)


def test_only_new_incoming_after_enabling_ai_is_admitted(setup):
    store,ai,_,agent,contact=setup;cid=contact['id']
    ai.data.cancel_contact(cid,'重设基线')
    # History, self messages and unknown direction never generate agent events.
    with store.connect() as db:db.execute('DELETE FROM snapshots WHERE contact_id=?',(cid,))
    store.ingest(cid,[{'key':'history','text':'旧消息','kind':'text','direction':'incoming'}])
    items=[{'key':'history','text':'旧消息','kind':'text','direction':'incoming'},
           {'key':'self','text':'手机本人发送','kind':'text','direction':'outgoing'},
           {'key':'unknown','text':'方向未知','kind':'text','direction':'unknown'}]
    store.ingest(cid,items);ai._collect()
    assert ai.data.runs(cid)==[]
    items.append({'key':'old-turn','text':'尚未处理的旧消息','kind':'text','direction':'incoming'})
    store.ingest(cid,items)
    # Switching modes advances the admission watermark even if polling was late.
    store.update_contact(cid,mode='ai_auto');ai._collect()
    with store.connect() as db:assert not db.execute('SELECT * FROM ai_inbox').fetchall()
    items.append({'key':'new-turn','text':'新请求','kind':'text','direction':'incoming'})
    store.ingest(cid,items);ai._collect()
    with store.connect() as db:db.execute('UPDATE ai_inbox SET due=0')
    ai._collect();assert len(ai.data.runs(cid))==1
    run=ai.data.runs(cid)[0];assert run['input']=='新请求'
    ai.execute(run['id']);pending=store.pending()[0]
    assert store.ai_send_valid(pending)
    store.update_contact(cid,mode='manual')
    assert not store.ai_send_valid(pending)


def test_restart_never_replays_started_tool(setup):
    store,ai,_,agent,contact=setup
    message=incoming(store,contact['id']);run=ai.create_run(agent['id'],message['text'],contact['id'],message['id'])
    state=ai.data.run(run['id'])['state'];state['executing_tool']='in-flight'
    ai.data.update_run(run['id'],status='running',state=state)
    ai.start()
    assert ai.data.run(run['id'])['status']=='uncertain' and not store.pending()


def test_resolved_new_incoming_enters_auto_reply_once(setup):
    store,ai,_,_,contact=setup;cid=contact['id']
    store.update_contact(cid,mode='ai_auto')
    item=dict(key='clipped-new',text='自动回复测试',kind='text',direction='unknown')
    store.ingest(cid,[item]);ai._collect()
    assert not ai.data.runs(cid)
    resolved={**item,'direction':'incoming','direction_verified':True}
    store.ingest(cid,[resolved]);ai._collect()
    with store.connect() as db:db.execute('UPDATE ai_inbox SET due=0')
    ai._collect()
    run=ai.data.runs(cid)[0];ai.execute(run['id'])
    assert run['input']=='自动回复测试' and len(store.pending())==1
    store.ingest(cid,[resolved]);ai._collect()
    assert len(ai.data.runs(cid))==1 and len(store.pending())==1


@pytest.mark.parametrize('collected',[False,True])
def test_corrected_own_message_never_generates_auto_reply(setup,collected):
    store,ai,_,_,contact=setup;cid=contact['id']
    store.update_contact(cid,mode='ai_auto')
    item=dict(key='old-dpi',text='本人发送',kind='text',direction='incoming')
    store.ingest(cid,[item])
    if collected:ai._collect()
    store.ingest(cid,[{**item,'direction':'outgoing','direction_verified':True}])
    ai._collect()
    with store.connect() as db:db.execute('UPDATE ai_inbox SET due=0')
    ai._collect()
    assert not ai.data.runs(cid) and not store.pending()


def test_worker_cancels_old_ai_after_slow_chat_reconnection(setup,monkeypatch):
    from queue import Queue
    from wechat_panel import runtime
    store,ai,_,agent,contact=setup;cid=contact['id']
    store.update_contact(cid,mode='ai_auto');message=incoming(store,cid)
    run=ai.create_run(agent['id'],message['text'],cid,message['id']);ai.execute(run['id'])
    queued=store.pending()[0];sent=[]
    class Bridge:
        def health(self):return {'state':'ready','account_id':'self'}
        def attach(self,contact,for_send=False):
            store.update_contact(cid,mode='manual')
            return []
        def send(self,contact,text):sent.append(text)
    monkeypatch.setattr(runtime,'WeixinBridge',Bridge)
    commands,output=Queue(),Queue()
    commands.put({'op':'configure','contacts':[store.contact(cid)],'paused':False})
    commands.put({'op':'send','request_id':'stale','message_id':queued['id'],'ai_run_id':run['id'],'contact_id':cid,'text':queued['text']})
    worker=threading.Thread(target=runtime.worker_main,args=(commands,output,False,store.path),daemon=True);worker.start()
    try:
        while True:
            result=output.get(timeout=5)
            if result['kind']=='result' and result['request_id']=='stale':break
        assert result['cancelled'] and sent==[]
    finally:
        commands.put({'op':'stop'});worker.join(timeout=2)
