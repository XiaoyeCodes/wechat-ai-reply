"""Private chat FIFO, including approval and delivery checkpoints."""
import threading

import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'panel.db')
    ai=AIService(store,model_call=lambda p,m,t:({'content':'已完成：'+m[-1]['content']},{}))
    p=ai.data.save('providers',None,dict(name='test',model='test',base_url='http://localhost:9999'))
    agent=ai.data.save('agents',None,dict(name='Aria',provider_id=p['id'],web_policy='off',
        companion={'safety':{'emotion_enabled':False,'crisis_detection':False}}))
    c=store.add_contact(dict(lookup='用户甲',nickname='用户甲',wechat_id='a',account_id='self'))
    store.update_contact(c['id'],ai_agent_id=agent['id'],mode='ai_auto',listening=True)
    ai.data.settings({'enabled':True,'debounce_seconds':.3})
    store.ingest(c['id'],[])
    yield store,ai,agent,c['id']
    ai.stop()


def incoming(env,text,cid=None):
    store,ai,_,default=env;cid=cid or default
    before=store.messages(cid)
    rows=[dict(key=m['source_key'],text=m['text'],kind=m['kind'],direction=m['direction'])
          for m in before if m['source_key']]
    rows.append(dict(key='msg-'+str(len(before)),text=text,kind='text',direction='incoming'))
    store.ingest(cid,rows)
    ai._collect()
    return store.messages(cid)[-1]


def flush(env):
    store,ai,*_=env
    with store.connect() as db:db.execute('UPDATE ai_inbox SET due=0')
    ai._collect()
    return sorted(ai.data.runs(),key=lambda r:r['created_at'])


def confirm(env,rid):
    store,ai,*_=env
    for m in store.pending():
        if m['ai_run_id']==rid:
            assert store.ai_send_valid(m)
            store.set_message_state(m['id'],'confirmed',source_key='sent-'+str(m['id']))
    assert ai.data.run(rid)['status']=='sent'


def test_running_turn_survives_new_messages_and_later_batches_wait_for_delivery(env):
    store,ai,_,cid=env
    incoming(env,'任务一');incoming(env,'任务一的补充')
    first=flush(env)[0];started=threading.Event();release=threading.Event();calls=[]
    def model(p,m,t):
        calls.append(m)
        if len(calls)==1:started.set();assert release.wait(5)
        return {'content':'完成任务 '+str(len(calls))},{}
    ai.model_call=model
    worker=threading.Thread(target=ai.execute,args=(first['id'],));worker.start()
    try:
        assert started.wait(5)
        incoming(env,'任务二');incoming(env,'任务二的补充');second=flush(env)[1]
        incoming(env,'任务三');third=flush(env)[2]
        assert ai.data.run(first['id'])['status']=='running'
        assert ai.data.valid(ai.data.run(first['id']))
        assert '任务一' not in second['input'] and '任务二的补充' in second['input']
        assert third['input']=='任务三'
        ai.execute(third['id']);ai.execute(second['id'])
        assert len(calls)==1
    finally:release.set();worker.join(5)
    assert not worker.is_alive()
    ai.execute(second['id']);assert len(calls)==1
    assert store.ai_send_valid(store.pending()[0])
    confirm(env,first['id'])
    ai.execute(third['id']);assert len(calls)==1
    ai.execute(second['id']);assert len(calls)==2
    assert '完成任务 1' in calls[1][0]['content']
    assert '任务三' not in str(calls[1])
    confirm(env,second['id']);ai.execute(third['id']);confirm(env,third['id'])
    assert len(calls)==3
    # Duplicate ingestion or a late ready event cannot create another run.
    ai._collect();assert len(ai.data.runs(cid))==3


def test_cancel_one_turn_preserves_later_turns_and_manual_takeover_clears_all(env):
    store,ai,_,cid=env
    incoming(env,'第一项');first=flush(env)[0]
    incoming(env,'第二项');second=flush(env)[1]
    revision=store.contact(cid)['ai_revision']
    ai.data.cancel_run(first['id'])
    assert ai.data.run(second['id'])['status']=='pending'
    assert store.contact(cid)['ai_revision']==revision
    ai.execute(second['id']);assert ai.data.run(second['id'])['status']=='queued'
    incoming(env,'第三项');third=flush(env)[2]
    incoming(env,'还没出缓冲区的第四项')
    ai.data.cancel_contact(cid,'人工接管')
    assert all(r['status']=='cancelled' for r in ai.data.runs(cid))
    assert not store.pending()
    with store.connect() as db:assert not db.execute('SELECT 1 FROM ai_inbox').fetchone()


def test_failure_response_finishes_before_next_task_starts(env):
    store,ai,_,cid=env
    incoming(env,'失败的任务');first=flush(env)[0]
    incoming(env,'后续任务');second=flush(env)[1]
    ai.model_call=lambda *a:(_ for _ in ()).throw(RuntimeError('模拟模型故障'))
    ai.execute(first['id'])
    assert ai.data.run(first['id'])['state']['task_failure']
    ai.execute(second['id']);assert ai.data.run(second['id'])['status']=='pending'
    confirm(env,first['id'])
    ai.model_call=lambda p,m,t:({'content':'后续任务完成'}, {})
    ai.execute(second['id']);confirm(env,second['id'])


def test_waiting_approval_and_draft_hold_the_queue(env):
    store,ai,agent,cid=env
    tool=ai.data.save('tools',None,dict(name='confirm_write',description='需确认的操作',kind='http',url='http://localhost:9999/tool',approval='confirm'))
    agent=ai.data.save('agents',agent['id'],{**{k:v for k,v in agent.items() if k not in ('id','key_configured')},'tool_ids':[tool['id']],
        'steps':[{'type':'tool','tool_id':tool['id']},{'type':'reply'}]})
    incoming(env,'先确认的任务');first=flush(env)[0];ai.execute(first['id'])
    assert ai.data.run(first['id'])['status']=='waiting_approval'
    incoming(env,'后续任务');second=flush(env)[1]
    ai.execute(second['id']);assert ai.data.run(second['id'])['status']=='pending'
    approval=ai.data.detail(first['id'])['approvals'][0]
    ai.approve(approval['id'],False)
    assert ai.data.run(first['id'])['status']=='cancelled'
    ai.data.update_run(second['id'],status='draft',reply='待确认的草稿')
    incoming(env,'第三项');third=flush(env)[2];ai.execute(third['id'])
    assert ai.data.run(third['id'])['status']=='pending'
    ai.data.cancel_run(second['id']);ai.execute(third['id'])
    assert ai.data.run(third['id'])['status']=='waiting_approval'


def test_another_contact_does_not_wait_for_the_first_contact(env):
    store,ai,agent,cid=env
    incoming(env,'甲的任务');first=flush(env)[0]
    ai.data.update_run(first['id'],status='running')
    c=store.add_contact(dict(lookup='用户乙',nickname='用户乙',wechat_id='b',account_id='self'))
    store.update_contact(c['id'],ai_agent_id=agent['id'],mode='ai_auto',listening=True)
    store.ingest(c['id'],[])
    incoming(env,'乙的独立任务',c['id']);second=flush(env)[1]
    ai.execute(second['id']);assert ai.data.run(second['id'])['status']=='queued'
    assert ai.data.run(first['id'])['status']=='running'



def test_dispatch_and_cancel_api_keep_fifo_in_demo_worker(tmp_path):
    import time
    from fastapi.testclient import TestClient
    from wechat_panel.app import create_app
    started=threading.Event();release=threading.Event();calls=[]
    def wait(call,predicate):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            value=call()
            if predicate(value):return value
            time.sleep(.05)
        raise AssertionError(str(value))
    app=create_app(tmp_path,demo=True)
    with TestClient(app) as client:
        headers={'X-Panel-Request':'1'}
        def post(path,body=None):
            r=client.post(path,headers=headers,json=body or {});assert r.status_code<300,r.text
            return r.json()
        wait(lambda:client.get('/api/status').json(),lambda x:x['state']=='ready')
        p=post('/api/ai/config/providers',dict(name='test',model='test',base_url='http://localhost:9999'))
        a=post('/api/ai/config/agents',dict(name='Aria',provider_id=p['id'],web_policy='off',companion={'safety':{'emotion_enabled':False,'crisis_detection':False}}))
        c=post('/api/contacts',dict(lookup='任务排队测试',wechat_id='fifo-demo'))
        cid=c['id']
        r=client.patch('/api/contacts/'+cid,headers=headers,json=dict(listening=True,mode='ai_auto',ai_agent_id=a['id']));assert r.status_code==200
        client.put('/api/ai/settings',headers=headers,json=dict(enabled=True,debounce_seconds=.3,context_messages=20))
        wait(lambda:client.get('/api/contacts').json(),lambda cs:next(x for x in cs if x['id']==cid)['listener_state']=='listening')
        def model(p,m,t):
            calls.append(m[-1]['content'])
            if len(calls)==1:started.set();assert release.wait(12)
            return {'content':'完成：'+m[-1]['content']},{}
        app.state.ai.model_call=model
        runs=lambda:client.get('/api/ai/runs',params={'contact_id':cid}).json()
        try:
            post('/api/demo/'+cid+'/incoming',{'text':'第一项'})
            assert started.wait(5)
            post('/api/demo/'+cid+'/incoming',{'text':'第二项'})
            rows=wait(runs,lambda rs:len(rs)==2);second=next(r for r in rows if r['status']=='pending')
            assert second['queue_position']==1
            post('/api/demo/'+cid+'/incoming',{'text':'第三项'})
            rows=wait(runs,lambda rs:len(rs)==3)
            post('/api/ai/runs/'+second['id']+'/cancel')
            assert next(r for r in runs() if r['input']=='第一项')['status']=='running'
            third=next(r for r in runs() if r['input']=='第三项')
            assert third['status']=='pending' and third['queue_position']==1
            release.set()
            rows=wait(runs,lambda rs:sum(r['status']=='sent' for r in rs)==2)
            assert calls==['第一项','第三项']
            messages=client.get('/api/contacts/'+cid+'/messages').json()
            assert [m['text'] for m in messages if m['origin']=='ai' and m['status']=='confirmed']==['完成：第一项','完成：第三项']
        finally:release.set()
