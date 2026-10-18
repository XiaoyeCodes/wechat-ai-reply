import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store


@pytest.fixture
def engine():
    state={'posts':[],'polls':0,'phase':'completed','output':'任务完成','approval':None,'callback':None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,data,code=200):
            raw=json.dumps(data).encode();self.send_response(code)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if self.headers.get('Authorization')!='Bearer hermes-test-secret':return self.send({},401)
            if self.path=='/v1/capabilities':return self.send({'platform':'hermes-agent','features':{k:True for k in ('run_submission','run_status','run_stop','run_approval_response')}})
            if self.path.endswith('/events'):return self.send({})
            state['polls']+=1
            if state['callback']:state['callback']()
            self.send({'run_id':'run_test','status':state['phase'],'output':state['output'],
                       'approval':state['approval'],'error':state.get('error',''),'partial':state.get('partial',False)})
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['posts'].append((self.path,body,self.headers.get('Idempotency-Key')))
            if self.path=='/v1/runs':return self.send({'run_id':'run_test','status':'started'},202)
            if self.path.endswith('/approval'):
                if state.get('approval_sequence'):
                    state['approval']=state['approval_sequence'].pop(0);state['phase']='waiting_for_approval'
                else:state['phase']='completed'
            self.send({'status':'stopping' if self.path.endswith('/stop') else 'approved'})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}',state
    server.shutdown();server.server_close()


@pytest.fixture
def setup(tmp_path,engine):
    store=Store(tmp_path/'panel.sqlite3');ai=AIService(store)
    agent=ai.data.save('agents',None,{'name':'电脑助手','kind':'hermes','url':engine[0],'api_key':'hermes-test-secret','hermes_approval_scope':'operation'})
    yield store,ai,agent,engine[1]
    ai.stop()


def allow_task(ai,run):
    ai.execute(run['id'])
    row=ai.data.detail(run['id'])['approvals'][0]
    ai.approve(row['id'],True)


def test_no_computer_execution_before_task_confirmation(setup):
    store,ai,agent,remote=setup
    run=ai.create_run(agent['id'],'整理文件',dry_run=True)
    ai.execute(run['id'])
    assert not remote['posts']
    assert ai.data.run(run['id'])['status']=='waiting_approval'
    ai.approve(ai.data.detail(run['id'])['approvals'][0]['id'],True)
    ai.execute(run['id'])
    final=ai.data.detail(run['id'])
    assert final['status']=='completed' and final['reply']=='任务完成'
    assert len([p for p in remote['posts'] if p[0]=='/v1/runs'])==1
    assert remote['posts'][0][2]=='relay-'+run['id']
    assert not store.pending()  # Test bench never sends WeChat.
    assert 'hermes-test-secret' not in json.dumps(final)


def test_unapproved_contact_cannot_use_computer_agent(setup):
    store,ai,agent,remote=setup
    contact=store.add_contact({'lookup':'访客','nickname':'访客','wechat_id':'guest','account_id':'owner'})
    with pytest.raises(ValueError,match='没有电脑任务权限'):
        ai.create_run(agent['id'],'运行命令',contact['id'],1)
    assert remote['posts']==[]


def test_sessions_are_namespaced_by_agent_and_test_session(setup):
    _,ai,agent,remote=setup
    for sid in ('a','b','a'):
        run=ai.create_run(agent['id'],'读取文件',dry_run=True,session_id=sid)
        allow_task(ai,run);ai.execute(run['id'])
    ids=[p[1]['session_id'] for p in remote['posts'] if p[0]=='/v1/runs']
    assert ids[0]==ids[2] and ids[0]!=ids[1]


def test_exact_remote_tool_approval_waits_for_human(setup):
    _,ai,agent,remote=setup
    remote.update(phase='waiting_for_approval',approval={'request_id':'req_17','command':'reviewed operation'})
    run=ai.create_run(agent['id'],'运行任务',dry_run=True);allow_task(ai,run)
    entered=threading.Event()
    def on_poll():
        detail=ai.data.detail(run['id'])
        waiting=[a for a in detail['approvals'] if a['tool_id']=='hermes:tool']
        if waiting:
            assert not any(p[0].endswith('/approval') for p in remote['posts'])
            ai.approve(waiting[0]['id'],True);remote['callback']=None;entered.set()
    remote['callback']=on_poll
    ai.execute(run['id'])
    assert entered.is_set()
    sent=[p[1] for p in remote['posts'] if p[0].endswith('/approval')]
    assert sent==[{'request_id':'req_17','choice':'once'}]
    assert ai.data.run(run['id'])['status']=='completed'


def test_task_consent_covers_later_steps_but_not_a_new_task(setup):
    _,ai,agent,remote=setup
    agent=ai.data.save('agents',agent['id'],{**{k:v for k,v in agent.items() if k not in ('id','key_configured')},'hermes_task_approval':'auto','hermes_approval_scope':'task'})
    approved=[]
    for n in range(2):
        remote.update(phase='waiting_for_approval',approval={'request_id':f'first{n}','command':'first step'},
                      approval_sequence=[{'request_id':f'second{n}','command':'second step'}])
        run=ai.create_run(agent['id'],'创建文件夹并保存记录',dry_run=True)
        def approve_first():
            pending=[a for a in ai.data.detail(run['id'])['approvals'] if a['status']=='pending']
            if pending:
                assert pending[0]['call_id']==f'hermes:first{n}'
                approved.append(pending[0]['id']);ai.approve(pending[0]['id'],True)
        remote['callback']=approve_first
        ai.execute(run['id'])
        assert ai.data.run(run['id'])['status']=='completed'
    assert len(approved)==2  # exactly one confirmation per task, not per engine session
    sent=[p[1]['request_id'] for p in remote['posts'] if p[0].endswith('/approval')]
    assert sent==['first0','second0','first1','second1']


def test_full_access_skips_task_and_tool_approvals_without_affecting_other_modes(setup,monkeypatch):
    _,ai,agent,remote=setup
    from wechat_panel.hermes import HermesClient
    provider=ai.data.save('providers',None,{'name':'mock','base_url':'http://127.0.0.1:19999/v1','model':'mock'})
    agent=ai.data.save('agents',agent['id'],{**{k:v for k,v in agent.items() if k not in ('id','key_configured')},'hermes_managed':True,'provider_id':provider['id'],
        'hermes_tool_policy':'full','hermes_chat_routing':False,'hermes_task_approval':'confirm'})
    assert agent['hermes_task_approval']=='auto'
    monkeypatch.setattr(ai.hermes,'client',lambda _:HermesClient(agent['url'],'hermes-test-secret'))
    remote.update(phase='waiting_for_approval',approval={'request_id':'a','command':'write'},
                  approval_sequence=[{'request_id':'b','command':'delete'}])
    run=ai.create_run(agent['id'],'创建并删除测试文件',dry_run=True);ai.execute(run['id'])
    detail=ai.data.detail(run['id'])
    assert detail['status']=='completed'
    assert all(a['status']=='delivered' for a in detail['approvals'])
    assert not any(t['step'].startswith('等待') or t['step']=='Hermes 等待工具确认' for t in detail['trace'])
    from wechat_panel.hermes_runtime import approval_config
    assert approval_config(agent)['mode']=='off'
    assert approval_config({**agent,'hermes_tool_policy':'sensitive'})['mode']=='manual'


def test_ai_approved_read_does_not_authorize_later_sensitive_step(setup,monkeypatch):
    _,ai,agent,remote=setup
    from wechat_panel.hermes import HermesClient
    from wechat_panel import hermes_review
    provider=ai.data.save('providers',None,{'name':'mock','base_url':'http://127.0.0.1:19999/v1','model':'mock'})
    agent=ai.data.save('agents',agent['id'],{**{k:v for k,v in agent.items() if k not in ('id','key_configured')},
        'hermes_managed':True,'provider_id':provider['id'],'hermes_tool_policy':'ai_review',
        'hermes_chat_routing':False,'hermes_task_approval':'auto','hermes_approval_scope':'task'})
    monkeypatch.setattr(ai.hermes,'client',lambda _:HermesClient(agent['url'],'hermes-test-secret'))
    reviewed=[]
    def review(*args):
        request=args[-1]['request_id'];reviewed.append(request)
        return request=='read','按实际影响审核'
    monkeypatch.setattr(hermes_review,'review',review)
    remote.update(phase='waiting_for_approval',approval={'request_id':'read','command':'read'},
        approval_sequence=[{'request_id':'delete','command':'delete'}])
    run=ai.create_run(agent['id'],'先查找再删除指定文件',dry_run=True)
    manual=[]
    def callback():
        for a in ai.data.detail(run['id'])['approvals']:
            if a['status']=='pending':
                assert a['call_id']=='hermes:delete'
                manual.append(a['id']);ai.approve(a['id'],True)
    remote['callback']=callback;ai.execute(run['id'])
    assert ai.data.run(run['id'])['status']=='completed'
    assert reviewed==['read','delete'] and len(manual)==1


def test_cancel_running_job_stops_engine_and_does_not_send(setup):
    store,ai,agent,remote=setup
    remote['phase']='running'
    run=ai.create_run(agent['id'],'长任务',dry_run=True);allow_task(ai,run)
    remote['callback']=lambda:ai.data.update_run(run['id'],status='cancelled')
    ai.execute(run['id'])
    assert any(p[0].endswith('/stop') for p in remote['posts'])
    assert ai.data.run(run['id'])['status']=='cancelled' and not store.pending()


@pytest.mark.parametrize('phase,partial',[('failed',False),('completed',True),('interrupted',False)])
def test_incomplete_results_are_not_success_or_wechat_replies(setup,phase,partial):
    store,ai,agent,remote=setup
    remote.update(phase=phase,partial=partial,error='failed hermes-test-secret')
    run=ai.create_run(agent['id'],'任务',dry_run=True);allow_task(ai,run);ai.execute(run['id'])
    result=ai.data.detail(run['id'])
    assert result['status']=='failed' and not store.pending()
    assert 'hermes-test-secret' not in json.dumps(result)


def test_restart_recovery_stops_old_job_without_replaying(setup):
    _,ai,agent,remote=setup
    run=ai.create_run(agent['id'],'旧任务',dry_run=True)
    state=ai.data.run(run['id'])['state']
    state.update(hermes_run_id='run_old',hermes_connection={'url':agent['url'],'key':'hermes-test-secret'},hermes_submitted=True)
    ai.data.update_run(run['id'],status='uncertain',state=state)
    ai.hermes.recover()
    assert [p[0] for p in remote['posts']]==['/v1/runs/run_old/stop']
    assert ai.data.run(run['id'])['status']=='uncertain'


def test_submission_failure_is_uncertain_not_automatically_retried(setup,monkeypatch):
    _,ai,agent,remote=setup
    run=ai.create_run(agent['id'],'不要重复创建文件',dry_run=True);allow_task(ai,run)
    from wechat_panel.hermes import HermesClient
    original=HermesClient.request
    def request(self,method,path,*args,**kwargs):
        result=original(self,method,path,*args,**kwargs)
        if path=='/v1/runs':raise RuntimeError('connection lost after admission')
        return result
    monkeypatch.setattr(HermesClient,'request',request)
    ai.execute(run['id']);ai.execute(run['id'])
    assert ai.data.run(run['id'])['status']=='uncertain'
    assert len(remote['posts'])==1


@pytest.mark.parametrize('mode,expected',[('ai_auto','queued'),('ai_assist','draft')])
def test_authorized_wechat_tasks_use_existing_reply_queue(setup,mode,expected):
    store,ai,agent,remote=setup
    contact=store.add_contact({'lookup':'主人','nickname':'主人','wechat_id':'owner','account_id':'self'})
    agent=ai.data.save('agents',agent['id'],{**{k:v for k,v in agent.items() if k not in ('id','key_configured')},'hermes_contact_id':contact['id']})
    ai.data.settings({'enabled':True,'debounce_seconds':.3,'context_messages':20})
    store.update_contact(contact['id'],listening=True,mode=mode,ai_agent_id=agent['id'])
    old={'key':'old','text':'旧电脑配置任务','kind':'text','direction':'incoming','account_id':'self'}
    store.ingest(contact['id'],[old])
    store.ingest(contact['id'],[old,{'key':'task','text':'核对文件','kind':'text','direction':'incoming','account_id':'self'}])
    message=store.messages(contact['id'])[-1]
    run=ai.create_run(agent['id'],message['text'],contact['id'],message['id'])
    allow_task(ai,run);ai.execute(run['id'])
    assert ai.data.run(run['id'])['status']==expected
    payload=next(p[1] for p in remote['posts'] if p[0]=='/v1/runs')
    assert payload['input']=='核对文件'
    assert all(m['role']!='user' for m in payload['conversation_history'])
    background=payload['conversation_history'][0]['content']
    assert '旧电脑配置任务' in background and '核对文件' not in background
    if mode=='ai_auto':
        assert len(store.pending())==1 and store.ai_send_valid(store.pending()[0])
    else:assert not store.pending()


def test_reject_live_tool_keeps_real_contact_run_cancelled(setup):
    store,ai,agent,remote=setup
    contact=store.add_contact({'lookup':'主人','nickname':'主人','wechat_id':'owner','account_id':'self'})
    agent=ai.data.save('agents',agent['id'],{**{k:v for k,v in agent.items() if k not in ('id','key_configured')},'hermes_contact_id':contact['id']})
    ai.data.settings({'enabled':True,'debounce_seconds':.3,'context_messages':20})
    store.update_contact(contact['id'],listening=True,mode='ai_auto',ai_agent_id=agent['id'])
    store.ingest(contact['id'],[])
    store.ingest(contact['id'],[{'key':'reject','text':'任务','kind':'text','direction':'incoming','account_id':'self'}])
    message=store.messages(contact['id'])[-1]
    run=ai.create_run(agent['id'],message['text'],contact['id'],message['id']);allow_task(ai,run)
    remote.update(phase='waiting_for_approval',approval={'request_id':'req_no','command':'reject this'})
    def on_poll():
        rows=[a for a in ai.data.detail(run['id'])['approvals'] if a['tool_id']=='hermes:tool']
        if rows:ai.approve(rows[0]['id'],False);remote['callback']=None
    remote['callback']=on_poll
    ai.execute(run['id'])
    assert ai.data.run(run['id'])['status']=='cancelled' and not store.pending()
    assert any(p[0].endswith('/stop') for p in remote['posts'])
    assert not any(p[0].endswith('/approval') for p in remote['posts'])


def test_skill_update_removes_stale_active_files_and_retains_archive(tmp_path,setup):
    _,ai,agent,_=setup
    skill=ai.data.save('skills',None,{'name':'test-hermes-skill','description':'Test only',
        'instructions':'Use the approved tools.','files':[{'path':'scripts/old.py','content':'print(1)'}]})
    agent['skill_ids']=[skill['id']]
    home=tmp_path/'home';home.mkdir()
    first=ai.hermes.runtime._sync_skills(home,agent)
    folder=home/'skills'/'relay'/skill['id']
    assert (folder/'scripts/old.py').exists()
    ai.data.save('skills',skill['id'],{**{k:v for k,v in skill.items() if k not in ('id','key_configured')},'files':[{'path':'scripts/new.py','content':'print(2)'}]})
    second=ai.hermes.runtime._sync_skills(home,agent)
    assert first!=second and not (folder/'scripts/old.py').exists()
    assert (folder/'scripts/new.py').exists()
    assert list((home/'disabled-relay-skills').glob('*/scripts/old.py'))
