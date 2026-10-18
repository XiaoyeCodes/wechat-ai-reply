from types import SimpleNamespace
import pytest

from test_ai import setup, incoming
from wechat_panel.voice_reply import requested_format, speech_text, deliver, ReplySuperseded, switch_only
from wechat_panel.bridge import DemoBridge, SendUncertain, DesktopBusy
from wechat_panel.conversation_context import contact_context


@pytest.mark.parametrize('text,expected',[
    ('以后用语音回复我吧','voice'),('改回文字回复','text'),('别发语音了','text'),
    ('你可以发送语音吗','voice'),('开启语音回复','voice'),('以后语音回复吧','voice'),
    ('不要用文字回复我，请用语音回复','voice'),('不要每条都发语音了','text'),
    ('我刚发语音给你了',None),('他说要用语音回复',None),('“用语音回复”是什么意思',None),
    ('如果开启语音回复会怎么样',None),('怎么实现发送语音的功能',None),
    ('不要关闭语音',None),('我喜欢听音乐',None),('可以用语音回复我吗？顺便说句晚安','voice'),
])
def test_explicit_chat_preferences_only(text,expected):
    assert requested_format(text)==expected


def test_speech_keeps_text_meaning_but_skips_unreadable_or_long_output():
    assert speech_text('（偷笑）好呀～ 😊')=='好呀～'
    assert '含税' in speech_text('总价是五百元（含税）。')
    for text in ('见 https://example.com', '你好'*110, '1. 第一条\n2. 第二条', '```print(1)```'):
        assert speech_text(text) is None


def queued(setup):
    store,ai,_,agent,contact=setup
    store.update_contact(contact['id'],mode='ai_auto',reply_format='voice')
    message=incoming(store,contact['id'])
    run=ai.create_run(agent['id'],message['text'],contact['id'],message['id'])
    ai.execute(run['id'])
    return store,ai,contact,run,store.pending()[0]


def test_chat_preference_is_per_contact_and_does_not_reset_context(setup):
    store,ai,_,_,contact=setup
    before=store.contact(contact['id'])
    other=store.add_contact({'lookup':'乙','nickname':'乙','wechat_id':'b','account_id':'self'})
    incoming(store,contact['id'],'以后用语音回复我吧');ai._collect()
    after=store.contact(contact['id'])
    assert after['reply_format']=='voice'
    assert after['ai_revision']==before['ai_revision']+1  # Normal incoming turn cancellation only.
    assert after['ai_start_message']==before['ai_start_message']
    assert after['ai_context_start_message']==before['ai_context_start_message']
    assert store.contact(other['id'])['reply_format']=='text'
    store.ingest(contact['id'],[
        {'key':'one','text':'以后用语音回复我吧','kind':'text','direction':'incoming'},
        {'key':'two','text':'改回文字回复','kind':'text','direction':'incoming'}]);ai._collect()
    assert store.contact(contact['id'])['reply_format']=='text'


def test_demo_voice_keeps_transcript_in_context_and_merges_observed_echo(setup):
    store,ai,contact,run,message=queued(setup)
    bridge=DemoBridge();contact=dict(contact,account_id='demo-account')
    result=deliver(bridge,store,message,contact,demo=True)
    assert result['delivery']=='voice'
    # Simulate the passive snapshot racing ahead of the receipt.
    echo={'key':result['source_key'],'text':'语音5"秒','kind':'voice','direction':'outgoing','text_pending':False}
    store.ingest(contact['id'],[echo])
    store.set_message_state(message['id'],'confirmed',source_key=result['source_key'])
    store.ingest(contact['id'],[echo])
    stored=store.message(message['id'])
    assert stored['text']==message['text'] and stored['kind']=='voice'
    assert len([m for m in store.messages(contact['id']) if m['source_key']==result['source_key']])==1
    context=contact_context(ai,store.contact(contact['id']),ai.data.get('agents',run['agent_id']))
    assert {'role':'assistant','content':'[已通过微信原生语音发送] '+message['text']} in context


def test_preference_is_rechecked_at_delivery_and_does_not_change_manual_messages(setup):
    store,ai,contact,run,message=queued(setup)
    store.update_contact(contact['id'],reply_format='text')
    bridge=DemoBridge();contact=dict(contact,account_id='demo-account')
    assert deliver(bridge,store,message,contact,demo=True)['delivery']=='text'
    store.update_contact(contact['id'],reply_format='voice')
    manual,_=store.enqueue(contact['id'],'人工文字','manual-test')
    assert deliver(bridge,store,manual,contact,demo=True)['delivery']=='text'


def fake_audio(monkeypatch):
    monkeypatch.setattr('wechat_panel.wechat_voice.synthesize_windows',lambda text,path:path.write_bytes(b'fake'))
    monkeypatch.setattr('wechat_panel.wechat_voice.load_pcm',lambda path:(None,4))


@pytest.mark.parametrize('failure',[SendUncertain('可能已发'),DesktopBusy('电脑正在使用')])
def test_native_send_failure_never_resends_as_text(setup,monkeypatch,failure):
    store,ai,contact,run,message=queued(setup);fake_audio(monkeypatch)
    calls=[]
    def fail(*args,**kwargs):calls.append('voice');raise failure
    bridge=SimpleNamespace(send_voice=fail,send=lambda *args:calls.append('text'))
    with pytest.raises(type(failure)):deliver(bridge,store,message,contact)
    assert calls==['voice']
    assert not list((store.path.parent/'voice-tmp').rglob('*.wav'))


def test_synthesis_failure_before_any_recording_safely_falls_back_to_text(setup,monkeypatch):
    store,ai,contact,run,message=queued(setup)
    def fail(*args):raise OSError('no Chinese voice')
    monkeypatch.setattr('wechat_panel.wechat_voice.synthesize_windows',fail)
    bridge=DemoBridge();contact=dict(contact,account_id='demo-account')
    result=deliver(bridge,store,message,contact)
    assert result['delivery']=='text' and result['voice_fallback']
    assert store.message(message['id'])['text']==message['text']


def test_new_turn_or_preference_during_recording_cancels_old_voice(setup,monkeypatch):
    store,ai,contact,run,message=queued(setup);fake_audio(monkeypatch)
    def record(*args,before_send):
        store.update_contact(contact['id'],reply_format='text')
        before_send()
        pytest.fail('must not send')
    bridge=SimpleNamespace(send_voice=record)
    with pytest.raises(ReplySuperseded):deliver(bridge,store,message,contact)


def test_voice_mode_is_in_model_prompt(setup):
    store,ai,_,agent,contact=setup
    store.update_contact(contact['id'],reply_format='voice')
    source=incoming(store,contact['id'])
    run=ai.create_run(agent['id'],source['text'],contact['id'],source['id'])
    seen=[]
    def model(p,m,t):seen.extend(m);return {'role':'assistant','content':'好呀，我在呢。'},{}
    ai.model_call=model;ai.execute(run['id'])
    assert any('真实微信原生语音' in m.get('content','') for m in seen if m['role']=='system')


def test_switch_confirmation_is_short_and_does_not_invent_delivery_facts(setup):
    store,ai,_,agent,contact=setup
    assert switch_only('以后用语音回复我')
    assert not switch_only('用语音给我说句晚上好')
    source=incoming(store,contact['id'],'改回文字回复')
    run=ai.create_run(agent['id'],source['text'],contact['id'],source['id'])
    def model(*args):pytest.fail('setting confirmation does not need a model')
    ai.model_call=model;ai.execute(run['id'])
    assert ai.data.run(run['id'])['reply']=='好呀，之后用文字回复你。'
