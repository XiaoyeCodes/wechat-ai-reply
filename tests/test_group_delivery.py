from datetime import datetime, timedelta, timezone
from io import BytesIO
import base64
from types import SimpleNamespace

import pytest
from PIL import Image

from test_group_chats import groups, group_api, incoming, group_runs
from wechat_panel.bridge import DemoBridge, SendUncertain
from wechat_panel.voice_reply import deliver, recording_interrupted
import json


def schedule(ai, agent, **changes):
    return ai.agent_scheduler.save({'agent_id':agent['id'],'title':'群发布测试',
        'instruction':'返回简短的公共消息，不包含私人信息。','kind':'once',
        'at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'enabled':True,**changes})


def test_scheduled_group_publication_is_explicit_and_uses_each_targets_voice_setting(groups):
    store, ai, agent, contacts, calls = groups
    assert {c['id'] for c in ai.agent_scheduler.eligible(agent['id'])} == {contacts[2]['id']}
    store.update_contact(contacts[0]['id'], reply_format='voice')
    ids = [contacts[0]['id'], contacts[2]['id']]
    task = schedule(ai, agent, recipient_ids=ids)
    assert task['eligible_count'] == 2
    occurrence = ai.agent_scheduler.run_now(task['id'])
    ai.execute(occurrence['run_id'])
    pending = store.pending()
    assert len(calls) == 1 and {m['contact_id'] for m in pending} == set(ids)
    bridge = DemoBridge()
    for message in pending:
        contact = store.contact(message['contact_id'])
        result = deliver(bridge, store, message, contact|{'account_id':'demo-account'}, demo=True)
        assert result['delivery'] == ('voice' if contact['chat_type']=='group' else 'text')
        assert store.ai_send_valid(message)
    assert not store.messages(contacts[1]['id'])
    assert not ai.data.run(occurrence['run_id'])['state'].get('group_sender')


@pytest.mark.parametrize('change', ['unlisten','rule','task','identity','binding'])
def test_group_scheduled_send_rechecks_target_after_generation(groups, change):
    store, ai, agent, contacts, _ = groups
    cid = contacts[0]['id']
    task = schedule(ai, agent, recipient_ids=[cid])
    occurrence = ai.agent_scheduler.run_now(task['id'])
    ai.execute(occurrence['run_id'])
    message = store.pending()[0]
    assert store.ai_send_valid(message)
    if change=='unlisten':store.update_contact(cid,listening=False)
    elif change=='rule':store.update_contact(cid,group_members=['李四'])
    elif change=='identity':store.update_contact(cid,group_identity_verified=False)
    elif change=='binding':store.update_contact(cid,ai_agent_id='')
    else:ai.agent_scheduler.save({'recipient_ids':[contacts[1]['id']]},task['id'])
    assert not store.ai_send_valid(message)


def test_group_schedule_cannot_select_an_unbound_target_or_empty_selection(groups):
    store, ai, agent, contacts, _ = groups
    with pytest.raises(ValueError):schedule(ai,agent,recipient_ids=[])
    store.update_contact(contacts[0]['id'],ai_agent_id='')
    with pytest.raises(ValueError,match='绑定'):schedule(ai,agent,recipient_ids=[contacts[0]['id']])


def test_group_voice_delivers_to_original_group_and_keeps_transcript(groups, monkeypatch):
    from wechat_panel.group_chats import context
    store, ai, agent, contacts, _ = groups
    cid = contacts[0]['id']
    store.update_contact(cid, reply_format='voice')
    incoming(groups,contacts[0],'@机器人 晚上好');ai._collect()
    run = group_runs(groups,contacts[0])[0];ai.execute(run['id'])
    message = store.pending()[0];calls = []
    monkeypatch.setattr('wechat_panel.voice_synthesis.synthesize',lambda *a,**kw:{'voice':'test','name':'测试音色'})
    monkeypatch.setattr('wechat_panel.wechat_voice.load_pcm',lambda path:(None,4))

    def send_voice(contact, path, before_send, interruption_guard):
        before_send();calls.append(contact['id'])
        assert not interruption_guard([{'direction':'incoming','sender':'李四','sender_verified':True,
                                        'mention_me':True,'mention_verified':True,'kind':'text'}])
        return {'key':'group-voice-receipt','text':'4秒语音'}

    receipt = deliver(SimpleNamespace(send_voice=send_voice),store,message,store.contact(cid))
    assert receipt['delivery']=='voice' and calls==[cid]
    store.set_message_state(message['id'],'confirmed',source_key=receipt['source_key'])
    incoming(groups,contacts[0],'@机器人 接着聊')
    history = context(ai,store.contact(cid),agent,None,'张三')
    assert {'role':'assistant','content':'[已通过微信原生语音发送] '+message['text']} in history


def test_group_voice_uncertain_send_does_not_repeat_as_text(groups,monkeypatch):
    store, ai, _, contacts, _ = groups
    store.update_contact(contacts[0]['id'],reply_format='voice')
    incoming(groups,contacts[0]);ai._collect();run=group_runs(groups,contacts[0])[0];ai.execute(run['id'])
    monkeypatch.setattr('wechat_panel.voice_synthesis.synthesize',lambda *a,**kw:{'voice':'test','name':'测试音色'})
    monkeypatch.setattr('wechat_panel.wechat_voice.load_pcm',lambda path:(None,4))
    calls=[]
    def send(*args,**kwargs):calls.append('voice');raise SendUncertain('发送待核对')
    with pytest.raises(SendUncertain):deliver(SimpleNamespace(send_voice=send,send=lambda *args:calls.append('text')),
        store,store.pending()[0],store.contact(contacts[0]['id']))
    assert calls==['voice']


def test_recording_ignores_other_verified_members_and_public_schedule_chat_activity():
    contact={'chat_type':'group','group_members':['张三','李四'],'group_mention_only':True}
    item={'direction':'incoming','sender':'李四','sender_verified':True,'kind':'text','mention_me':True,'mention_verified':True}
    assert not recording_interrupted(contact,{'group_sender':'张三'},[item])
    assert recording_interrupted(contact,{'group_sender':'张三'},[item|{'sender':'张三'}])
    assert recording_interrupted(contact,{'group_sender':'张三'},[item|{'sender_verified':False}])
    assert not recording_interrupted(contact,{'agent_schedule':{'role':'delivery'}},[item,{'direction':'outgoing'}])


def test_group_member_avatar_is_isolated_from_group_and_private_chat(group_api):
    client=group_api;store=client.app.state.store;headers={'X-Panel-Request':'1'}
    group=client.post('/api/contacts',headers=headers,json={'lookup':'头像测试群','chat_type':'group',
        'group_members':['张/三'],'group_self_name':'机器人'}).json()
    def png(color):
        buf=BytesIO();Image.new('RGB',(64,64),color).save(buf,format='PNG');return buf.getvalue()
    group_image,member_image=png('navy'),png('orange')
    store.set_avatar(group['account_id'],group['wechat_id'],group_image)
    store.ingest(group['id'],[{'key':'member-photo','kind':'text','direction':'incoming','text':'你好',
        'sender':'张/三','sender_verified':True,'sender_avatar_data':base64.b64encode(member_image).decode()}])
    message=client.get('/api/contacts/'+group['id']+'/messages').json()[0]
    assert client.get(message['sender_avatar_url']).content==member_image
    assert client.get(store.contact(group['id'])['avatar_url']).content==group_image
    assert client.get('/api/contacts/'+group['id']+'/member-avatar',params={'sender':'其他人'}).status_code==404
    client.delete('/api/contacts/'+group['id'],headers=headers)
    assert client.get(message['sender_avatar_url']).status_code==404


def test_late_native_sender_evidence_enriches_the_fresh_message_and_triggers_once(groups):
    store,ai,_,contacts,_=groups;cid=contacts[0]['id'];body='@机器人 你好'
    unknown={'key':'unknown-row','native_key':'window:runtime','sender_header':'unchanged-header',
        'kind':'text','direction':'unknown','text':body,'sender':'','sender_verified':False,
        'identity_text':json.dumps(['',False,body])}
    store.ingest(cid,[unknown]);original=store.messages(cid)[-1]
    assert not original['historical'] and not group_runs(groups,contacts[0])
    known=unknown|{'key':'verified-row','sender':'张三','sender_verified':True,'direction':'incoming',
        'direction_verified':True,'sender_source':'uia_selection','mention_me':True,'mention_verified':True,
        'identity_text':json.dumps(['张三',True,body])}
    store.ingest(cid,[known]);store.ingest(cid,[known]);ai._collect()
    messages=store.messages(cid)
    assert len(messages)==1 and messages[0]['id']==original['id']
    assert messages[0]['sender']=='张三' and messages[0]['direction']=='incoming' and not messages[0]['historical']
    assert len(group_runs(groups,contacts[0]))==1


def test_same_runtime_and_body_cannot_enrich_a_changed_member_header():
    from wechat_panel.snapshots import align
    old={'key':'old','native_key':'window:runtime','sender_header':'member-a',
        'text':'你好','kind':'text','direction':'incoming','sender_verified':False}
    new=old|{'key':'new','sender_header':'member-b','sender':'张三','sender_verified':True,'sender_source':'uia_selection'}
    assert align([old],[new])[0]['key']=='new'


def test_model_receives_verified_member_and_own_alias_as_different_identities(groups):
    _,ai,_,contacts,calls=groups
    incoming(groups,contacts[0],'@机器人 你好');ai._collect()
    ai.execute(group_runs(groups,contacts[0])[0]['id'])
    system='\n'.join(m['content'] for m in calls[0] if m['role']=='system')
    assert '"本轮发言成员": "张三"' in system
    assert '"当前账号的群昵称": "机器人"' in system
