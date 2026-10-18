import json
import time

import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store
from wechat_panel.hermes_plugin import policy_decision, readonly_terminal, readonly_tool


@pytest.mark.parametrize('tool,args',[
    ('read_file',{'path':'notes.txt'}),('relay_system_info',{}),
    ('relay_web_read',{'action':'search','value':'Hermes GitHub'}),
    ('relay_web_read',{'action':'open','value':'https://github.com/NousResearch/hermes-agent'}),
    ('computer_use',{'action':'list_windows'}),('terminal',{'command':'cat notes.txt'})])
def test_sensitive_policy_allows_only_proven_reads(tool,args):
    assert policy_decision(tool,args,'sensitive') is None
    assert policy_decision(tool,args,'all')['action']=='approve'


@pytest.mark.parametrize('tool,args',[
    ('write_file',{'path':'notes.txt','content':'hello'}),('patch',{'path':'notes.txt'}),
    ('terminal',{'command':'cat notes.txt; rm notes.txt'}),
    ('terminal',{'command':'ls > notes.txt'}),('terminal',{'command':'python -c "print(1)"'}),
    ('browser_exec',{'code':'import os; os.remove("notes.txt")'}),
    ('browser_click',{'ref':'submit'}),('computer_use',{'action':'click'}),
    ('read_file',{'path':'.env'}),('new_unrecognized_tool',{}),
    ('relay_web_read',{'action':'open','value':'http://localhost:8765/api/status'}),
    ('relay_web_read',{'action':'open','value':'https://example.com/delete?id=1'})])
def test_mutations_and_unknown_actions_always_need_confirmation(tool,args):
    assert policy_decision(tool,args,'sensitive')['action']=='approve'


def test_confirmation_is_bound_to_exact_arguments_and_call():
    a=policy_decision('write_file',{'path':'a','content':'one'},'all','call1')
    b=policy_decision('write_file',{'path':'a','content':'two'},'all','call1')
    c=policy_decision('write_file',{'path':'a','content':'one'},'all','call2')
    assert len({x['rule_key'] for x in (a,b,c)})==3


def test_full_access_policy_does_not_request_approval_for_writes_or_commands():
    assert policy_decision('write_file',{'path':'test.txt','content':'test'},'full') is None
    assert policy_decision('terminal',{'command':'rm test.txt'},'full') is None


def test_desktop_listing_is_readonly_but_mixed_commands_still_need_confirmation():
    listing='ls -d "$HOME/Desktop" "$HOME/OneDrive/Desktop" 2>/dev/null; echo "---"; ls "$HOME/Desktop" | head -30'
    assert readonly_terminal(listing)
    assert readonly_terminal('cd "C:/test" && ls; cat notes.txt')
    for command in [listing+'; rm notes.txt','ls | python -c "print(1)"','ls > notes.txt','cat $(whoami)',
                    'ls & rm notes.txt','cat "$OTHER_ENV"','ls; curl https://example.com']:
        assert not readonly_terminal(command)


def test_tool_schema_discovery_does_not_require_desktop_permission():
    for mode in ('all','sensitive'):
        assert policy_decision('tool_describe',{'names':['relay_system_info']},mode) is None


@pytest.fixture
def system(tmp_path):
    store=Store(tmp_path/'panel.sqlite3');ai=AIService(store)
    contact=store.add_contact({'lookup':'Aria','nickname':'Aria','wechat_id':'test-aria','account_id':'test-self'})
    agent=ai.data.save('agents',None,{'name':'Hermes','kind':'hermes','url':'http://127.0.0.1:19999',
        'api_key':'test-only','hermes_contact_id':contact['id'],'hermes_approval_channel':'wechat'})
    ai.data.settings({'enabled':True,'debounce_seconds':.3,'context_messages':20})
    store.update_contact(contact['id'],mode='ai_auto',ai_agent_id=agent['id'],listening=True)
    rows=[];store.ingest(contact['id'],rows)
    def incoming(text,cid=None):
        cid=cid or contact['id']
        row={'key':str(len(rows)),'text':text,'direction':'incoming','kind':'text','direction_verified':True,'account_id':'test-self'}
        rows.append(row);store.ingest(cid,rows)
        return store.messages(cid)[-1]
    source=incoming('在工作目录写入测试文件')
    run=ai.create_run(agent['id'],source['text'],contact['id'],source['id'])
    with store.connect() as db:db.execute("UPDATE settings SET value=? WHERE key='ai_cursor'",(str(store.latest_event()),))
    ai.execute(run['id'])
    approval=ai.data.detail(run['id'])['approvals'][0]
    with store.connect() as db:receipt=dict(db.execute('SELECT * FROM hermes_confirmations').fetchone())
    yield store,ai,contact,run,approval,receipt,incoming
    ai.stop()


def test_wechat_confirmation_resumes_original_run_and_notifies_once(system):
    store,ai,contact,run,approval,receipt,incoming=system
    notice=store.message(receipt['notice_id'])
    assert notice['origin']=='approval' and store.ai_send_valid(notice)
    assert '任务' in notice['text'] and '参数' not in notice['text'] and '不再逐步询问' in notice['text']
    ai.hermes.confirmations.notify(ai.data.run(run['id']),'hermes-task')
    assert len(store.pending())==1
    store.set_message_state(notice['id'],'confirmed')
    assert ai.data.run(run['id'])['status']=='waiting_approval'
    source=incoming('确认 '+receipt['code'])
    assert ai.data.valid(ai.data.run(run['id']))  # race before event collector
    ai._collect()
    resumed=ai.data.run(run['id'])
    assert resumed['status']=='pending' and resumed['resume_message_id']==source['id']
    assert len(ai.data.runs(contact['id']))==1
    ai.data.update_run(run['id'],status='running')
    ai.finish(run['id'],'测试完成')
    reply=store.pending()[0]
    assert reply['origin']=='ai' and store.ai_send_valid(reply)
    incoming('确认 '+receipt['code']);ai._collect()
    assert len(ai.data.runs(contact['id']))==1 and store.ai_send_valid(reply)


@pytest.mark.parametrize('word,expected',[('确认','pending'),('取消','cancelled')])
def test_plain_confirmation_or_cancel_needs_no_code(system,word,expected):
    store,ai,contact,run,approval,receipt,incoming=system
    text=store.message(receipt['notice_id'])['text']
    assert '回复「确认」' in text and receipt['code'] not in text
    store.set_message_state(receipt['notice_id'],'confirmed')
    source=incoming(word)
    assert ai.data.valid(ai.data.run(run['id']))
    ai._collect()
    assert ai.data.run(run['id'])['status']==expected
    assert ai.data.run(run['id'])['resume_message_id']==source['id']
    assert len(ai.data.runs(contact['id']))==1


def test_ambiguous_plain_confirmation_cannot_approve_multiple_requests(system):
    store,ai,contact,run,approval,receipt,incoming=system
    with store.connect() as db:
        db.execute("INSERT INTO ai_approvals(id,run_id,call_id,tool_id,arguments,status,created_at) VALUES('second',?,'second','hermes:tool','{}','pending','')",(run['id'],))
        db.execute("INSERT INTO hermes_confirmations(id,run_id,contact_id,code,expires,notice_id) VALUES('second',?,?,'FFFFFF',?,?)",(run['id'],contact['id'],time.time()+60,receipt['notice_id']))
    store.set_message_state(receipt['notice_id'],'confirmed')
    source=incoming('确认')
    assert not ai.hermes.confirmations.consume(contact,source)
    assert all(a['status']=='pending' for a in ai.data.detail(run['id'])['approvals'])


def test_plain_cancel_can_stop_a_task_after_confirmation(system):
    store,ai,contact,run,approval,receipt,incoming=system
    store.set_message_state(receipt['notice_id'],'confirmed')
    incoming('确认');ai._collect()
    ai.data.update_run(run['id'],status='running')
    incoming('取消');ai._collect()
    assert ai.data.run(run['id'])['status']=='cancelled'
    assert any('已停止继续执行' in m['text'] and store.ai_send_valid(m) for m in store.pending())


def test_confirmation_waits_for_send_receipt(system):
    store,ai,contact,run,approval,receipt,incoming=system
    incoming('确认 '+receipt['code']);ai._collect()
    assert ai.data.detail(run['id'])['approvals'][0]['status']=='pending'
    store.set_message_state(receipt['notice_id'],'confirmed')
    ai.hermes.confirmations.tick()
    assert ai.data.run(run['id'])['status']=='pending'


def test_confirmation_retries_only_failed_pre_input_delivery_and_has_limit(system):
    store,ai,contact,run,approval,receipt,incoming=system
    for attempt in range(3):
        store.set_message_state(receipt['notice_id'],'failed','目标控件被其他窗口遮挡，已停止操作')
        ai.hermes.confirmations.tick()
        assert store.message(receipt['notice_id'])['status']=='failed'
        with store.connect() as db:db.execute('UPDATE hermes_confirmations SET retry_after=?',(time.time()-1,))
        ai.hermes.confirmations.tick()
        assert store.message(receipt['notice_id'])['status']==('queued' if attempt<2 else 'failed')
    assert len(store.messages(contact['id']))==2  # same notification/code, no duplicates


@pytest.mark.parametrize('status,error',[
    ('uncertain','目标控件被其他窗口遮挡，已停止操作'),
    ('failed','微信输入框已有草稿，请先在微信处理草稿后发送'),
    ('confirmed','')])
def test_confirmation_does_not_retry_uncertain_or_draft_or_confirmed_delivery(system,status,error):
    store,ai,contact,run,approval,receipt,incoming=system
    store.set_message_state(receipt['notice_id'],status,error)
    with store.connect() as db:db.execute('UPDATE hermes_confirmations SET retry_after=?',(time.time()-1,))
    ai.hermes.confirmations.tick()
    assert store.message(receipt['notice_id'])['status']==status


def test_confirmation_retry_is_cancelled_when_contact_configuration_changes(system):
    store,ai,contact,run,approval,receipt,incoming=system
    store.set_message_state(receipt['notice_id'],'failed','目标控件被其他窗口遮挡，已停止操作')
    store.update_contact(contact['id'],listening=False)
    with store.connect() as db:db.execute('UPDATE hermes_confirmations SET retry_after=?',(time.time()-1,))
    ai.hermes.confirmations.tick()
    assert store.message(receipt['notice_id'])['status']=='failed'


def test_cancel_from_wechat_stops_task_and_sends_ack(system):
    store,ai,contact,run,approval,receipt,incoming=system
    store.set_message_state(receipt['notice_id'],'confirmed')
    incoming('取消 '+receipt['code']);ai._collect()
    assert ai.data.run(run['id'])['status']=='cancelled'
    feedback=store.pending()[0]
    assert store.ai_send_valid(feedback) and '已取消' in feedback['text']
    store.set_message_state(feedback['id'],'confirmed')
    assert ai.data.run(run['id'])['status']=='cancelled'


def test_other_contact_and_unverified_or_old_messages_cannot_approve(system):
    store,ai,contact,run,approval,receipt,incoming=system
    store.set_message_state(receipt['notice_id'],'confirmed')
    source=incoming('确认 '+receipt['code'])
    for changed in ({'contact_id':'other'},{'historical':1},{'direction_verified':0},{'direction':'outgoing'}):
        c={**contact,'id':changed.get('contact_id',contact['id'])}
        assert not ai.hermes.confirmations.consume(c,{**source,**changed})
    assert ai.data.detail(run['id'])['approvals'][0]['status']=='pending'


def test_expiration_and_restart_invalidate_old_confirmations(system):
    store,ai,contact,run,approval,receipt,incoming=system
    with store.connect() as db:db.execute('UPDATE hermes_confirmations SET expires=?',(time.time()-1,))
    ai.hermes.confirmations.tick()
    assert ai.data.run(run['id'])['status']=='cancelled'
    assert not store.ai_send_valid(store.message(receipt['notice_id']))
    incoming('确认 '+receipt['code']);ai._collect()
    assert ai.data.run(run['id'])['status']=='cancelled'


def test_recovery_keeps_unfinished_computer_task_unreplayed(system):
    store,ai,contact,run,approval,receipt,incoming=system
    ai.hermes.confirmations.recover()
    assert ai.data.run(run['id'])['status']=='uncertain'
    incoming('确认 '+receipt['code']);ai._collect()
    assert ai.data.run(run['id'])['status']=='uncertain' and len(ai.data.runs(contact['id']))==1


def test_normal_chat_has_no_computer_tools_and_no_approval(tmp_path):
    store=Store(tmp_path/'panel.sqlite3');calls=[]
    def model(provider,messages,tools):
        calls.append(tools)
        return {'content':json.dumps({'kind':'chat','reply':'哈哈，先用着现有的也挺好。'},ensure_ascii=False)},{}
    ai=AIService(store,model_call=model)
    provider=ai.data.save('providers',None,{'name':'test','base_url':'http://127.0.0.1:19999/v1','model':'test'})
    agent=ai.data.save('agents',None,{'name':'Hermes','kind':'hermes','hermes_managed':True,'provider_id':provider['id']})
    run=ai.create_run(agent['id'],'我没钱买了哈哈',dry_run=True)
    ai.execute(run['id']);result=ai.data.detail(run['id'])
    assert result['status']=='completed' and result['reply'].startswith('哈哈')
    assert not result['approvals'] and calls==[[]]
    ai.stop()
