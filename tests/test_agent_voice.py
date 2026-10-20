from types import SimpleNamespace

import pytest

from test_ai import setup, incoming
from wechat_panel.ai_models import Agent
from wechat_panel.voice_reply import preferences, preference_prompt, deliver, ReplySuperseded


def profile(ai, agent, **voice):
    current={k:v for k,v in ai.data.get('agents',agent['id']).items() if k in Agent.model_fields}
    return ai.data.save('agents',agent['id'],current|{'companion':current['companion']|{'voice':voice}})


def test_follow_bound_agent_and_preserve_contact_overrides(setup):
    store,ai,_,agent,contact=setup
    profile(ai,agent,reply_format='voice',preset='xiaoyi',model='speech-2.8-hd')
    assert preferences(store,store.contact(contact['id']))['reply_format']=='text'
    c=store.update_contact(contact['id'],reply_format='agent')
    assert preferences(store,c)=={'reply_format':'voice','voice_preset':'xiaoyi','voice_model':'speech-2.8-hd'}
    assert '已启用真实微信原生语音' in preference_prompt(c,store)
    other=ai.data.save('agents',None,{'name':'另一位 Agent','provider_id':agent['provider_id']})
    c=store.update_contact(c['id'],ai_agent_id=other['id'])
    assert preferences(store,c)['reply_format']=='text'
    c=store.update_contact(c['id'],ai_agent_id=agent['id'],reply_format='voice',voice_preset='xiaoxiao')
    assert preferences(store,c)['voice_preset']=='xiaoxiao'


@pytest.mark.parametrize('model',['speech-2.8-turbo','speech-2.8-hd'])
def test_native_delivery_uses_agent_voice_and_model(setup,monkeypatch,model):
    store,ai,_,agent,contact=setup
    voice='minimax:Chinese (Mandarin)_ExplorativeGirl'
    profile(ai,agent,reply_format='voice',preset=voice,model=model)
    store.update_contact(contact['id'],reply_format='agent',mode='ai_auto')
    source=incoming(store,contact['id'])
    run=ai.create_run(agent['id'],source['text'],contact['id'],source['id']);ai.execute(run['id'])
    message=store.pending()[0];calls=[]
    def synthesize(text,path,voice_id,**kwargs):
        calls.append((voice_id,kwargs['model']));path.write_bytes(b'audio')
        return {'voice':'test','name':'测试音色','model':kwargs['model']}
    monkeypatch.setattr('wechat_panel.voice_synthesis.synthesize',synthesize)
    monkeypatch.setattr('wechat_panel.wechat_voice.load_pcm',lambda path:(None,4))
    def send_voice(c,path,before_send):
        before_send();return {'key':'voice-receipt','text':'4秒语音'}
    result=deliver(SimpleNamespace(send_voice=send_voice),store,message,contact)
    assert result['delivery']=='voice' and calls==[(voice,model)]


def test_changed_agent_voice_cancels_old_recording(setup,monkeypatch):
    store,ai,_,agent,contact=setup
    profile(ai,agent,reply_format='voice',preset='xiaoxiao')
    store.update_contact(contact['id'],reply_format='agent',mode='ai_auto')
    source=incoming(store,contact['id'])
    run=ai.create_run(agent['id'],source['text'],contact['id'],source['id']);ai.execute(run['id'])
    monkeypatch.setattr('wechat_panel.voice_synthesis.synthesize',lambda *a,**kw:{'voice':'test','name':'测试音色'})
    monkeypatch.setattr('wechat_panel.wechat_voice.load_pcm',lambda path:(None,4))
    def send_voice(c,path,before_send):
        profile(ai,agent,reply_format='voice',preset='xiaoyi');before_send()
        pytest.fail('must cancel the previous voice')
    with pytest.raises(ReplySuperseded):deliver(SimpleNamespace(send_voice=send_voice),store,store.pending()[0],contact)


def test_explicit_wechat_switch_overrides_following_agent(setup):
    store,ai,_,agent,contact=setup
    profile(ai,agent,reply_format='voice',preset='xiaoyi')
    store.update_contact(contact['id'],reply_format='agent',mode='ai_auto')
    source=incoming(store,contact['id'],'改回文字回复')
    ai._collect()
    assert store.contact(contact['id'])['reply_format']=='text'
    assert preferences(store,store.contact(contact['id']))['reply_format']=='text'


def test_invalid_agent_voice_is_rejected_and_group_can_follow_agent(setup):
    store,ai,_,agent,contact=setup
    with pytest.raises(ValueError):profile(ai,agent,preset='unknown-voice')
    profile(ai,agent,reply_format='voice',preset='xiaoyi')
    assert preferences(store,store.contact(contact['id'])|{'chat_type':'group','reply_format':'agent'})['reply_format']=='voice'
