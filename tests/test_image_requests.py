import json
from io import BytesIO

from PIL import Image
import pytest

from wechat_panel.ai import AIService
from wechat_panel.image_requests import requested
from wechat_panel.store import Store


@pytest.mark.parametrize('text',[
    '帮我生成一张富春山居图','帮我生成一张黄山的山水图吧。山水水墨画',
    '请画一只猫','给我画一幅山水画','生成一个头像','请生成一张海报',
    'Generate an image of a cat',
])
def test_explicit_generation(text):assert requested(text)=='generate'


@pytest.mark.parametrize('text',['把这张照片改成水彩','帮我修改这张图片','把背景换成蓝色'])
def test_explicit_editing(text):assert requested(text)=='edit'


@pytest.mark.parametrize('text',[
    '不要生成图片','先别画图','你会画画吗','怎么生成图片','帮我写一段生图提示词',
    '帮我生成一张图片的提示词','他让我生成一张图','我之前让你画一只猫',
    '生成一张表格','生成一个思维导图','今天好累',
])
def test_other_requests_do_not_generate(text):assert requested(text) is None


@pytest.fixture
def setup(tmp_path,monkeypatch):
    calls=[]
    def model(*args):
        calls.append('model')
        return {'content':'我之前说过没有画图功能。那本书也看到了。'},{}
    ai=AIService(Store(tmp_path/'test.db'),model_call=model)
    provider=ai.data.save('providers',None,{'name':'test','model':'mock','base_url':'http://localhost:9999'})
    tool=next(t for t in ai.data.list('tools') if t.get('builtin')=='generate_image')
    body={k:v for k,v in tool.items() if k not in ('id','key_configured')};body['approval']='auto'
    tool=ai.data.save('tools',tool['id'],body)
    service=next(s for s in ai.data.list('services') if s['driver']=='codex_image')
    agent=ai.data.save('agents',None,{'name':'Aria','provider_id':provider['id'],'tool_ids':[tool['id']],
        'web_policy':'off','companion':{'media':{'image_service_id':service['id']},
        'safety':{'emotion_enabled':False,'crisis_detection':False}}})
    def generate(sid,prompt,media,reference=None,**kw):
        calls.append({'prompt':prompt,'reference':reference})
        buf=BytesIO();Image.new('RGB',(24,24),'blue').save(buf,format='PNG');return buf.getvalue()
    monkeypatch.setattr(ai.companion.services,'generate_image',generate)
    yield ai,agent,tool,calls
    ai.stop()


def test_stale_real_chat_cannot_override_authorized_image_request(setup):
    ai,agent,_,calls=setup
    contact=ai.store.add_contact({'lookup':'test','nickname':'test','wechat_id':'test','account_id':'self'})
    ai.store.update_contact(contact['id'],ai_agent_id=agent['id'],mode='ai_auto',listening=True)
    # Reproduce the real conversation's stale assistant claim and unrelated photo.
    ai.store.ingest(contact['id'],[
        {'key':'old1','text':'我没有画图功能','kind':'text','direction':'outgoing'},
        {'key':'old2','text':'[图片] 半小时漫画中国史','kind':'image','direction':'incoming'}])
    run=ai.create_run(agent['id'],'帮我生成一张富春山居图',contact_id=contact['id'],dry_run=True)
    ai.execute(run['id']);saved=ai.data.run(run['id'])
    assert saved['status']=='completed',saved['error']
    assert len(calls)==1 and calls[0]['reference'] is None
    assert '富春山居图' in calls[0]['prompt'] and '漫画' not in calls[0]['prompt']
    assert saved['state']['artifacts'][0]['kind']=='image'
    assert '没有画图功能' not in saved['reply'] and '还没有发送到微信' in saved['reply']
    assert ai.store.pending()==[]


def test_approval_resume_generates_once(setup):
    ai,agent,tool,calls=setup
    body={k:v for k,v in tool.items() if k not in ('id','key_configured')};body['approval']='confirm'
    ai.data.save('tools',tool['id'],body)
    run=ai.create_run(agent['id'],'帮我画一幅山水画',dry_run=True)
    ai.execute(run['id']);assert ai.data.run(run['id'])['status']=='waiting_approval' and not calls
    with ai.store.connect() as db:aid=db.execute('SELECT id FROM ai_approvals WHERE run_id=?',(run['id'],)).fetchone()[0]
    ai.approve(aid,True);ai.execute(run['id']);ai.execute(run['id'])
    assert ai.data.run(run['id'])['status']=='completed' and len(calls)==1


def test_unbound_tool_cannot_be_invoked_by_route(setup):
    ai,agent,_,calls=setup
    body={k:v for k,v in agent.items() if k not in ('id','key_configured')};body['tool_ids']=[]
    ai.data.save('agents',agent['id'],body)
    run=ai.create_run(agent['id'],'画一幅山水画',dry_run=True);ai.execute(run['id'])
    assert all(c=='model' for c in calls)


def live_run(setup, mode='ai_auto'):
    ai,agent,_,calls=setup
    ai.data.settings({'enabled':True})
    c=ai.store.add_contact(dict(lookup='image-test',nickname='image-test',wechat_id='image-test',account_id='self'))
    ai.store.update_contact(c['id'],listening=True,mode=mode,ai_agent_id=agent['id'],reply_format='voice')
    ai.store.ingest(c['id'],[])
    question=dict(key='q',text='帮我生成一张富春山居图',kind='text',direction='incoming')
    ai.store.ingest(c['id'],[question])
    msg=ai.store.messages(c['id'])[-1]
    run=ai.create_run(agent['id'],msg['text'],c['id'],msg['id'])
    return ai,c,run,question


def test_generated_image_queued_once_and_echo_merged(setup):
    ai,c,run,question=live_run(setup)
    ai.execute(run['id'])
    saved=ai.data.run(run['id'])
    assert saved['status']=='queued',saved['error']
    rows=ai.store.pending()
    assert len(rows)==1 and rows[0]['kind']=='image' and rows[0]['contact_id']==c['id']
    assert rows[0]['media_id']==saved['state']['artifacts'][0]['id']
    assert ai.store.ai_send_valid(rows[0])
    ai.store.enqueue_ai_images(c['id'],run['id'],[rows[0]['media_id']])
    assert len(ai.store.pending())==1
    ai.store.ingest(c['id'],[question,dict(key='image-echo',text='图片',kind='image',direction='outgoing')])
    ai.store.set_message_state(rows[0]['id'],'confirmed',source_key='image-echo')
    assert ai.data.run(run['id'])['status']=='sent'
    outgoing=[m for m in ai.store.messages(c['id']) if m['direction']=='outgoing']
    assert len(outgoing)==1 and outgoing[0]['media_id']==rows[0]['media_id']


def test_assist_image_waits_for_draft_then_sends_image(setup):
    ai,c,run,_=live_run(setup,'ai_assist')
    ai.execute(run['id'])
    saved=ai.data.run(run['id'])
    assert saved['status']=='draft' and not ai.store.pending()
    row=ai.send_draft(run['id'],saved['reply'])
    assert row['kind']=='image' and ai.store.ai_send_valid(row)


def test_delivery_checks_scope_and_cancellation_before_send(setup):
    from wechat_panel.image_delivery import deliver
    from wechat_panel.voice_reply import ReplySuperseded
    from types import SimpleNamespace
    ai,c,run,_=live_run(setup)
    ai.execute(run['id']);message=ai.store.pending()[0]
    bridge=SimpleNamespace(send_image=lambda *args,**kw:pytest.fail('must not send'))
    with pytest.raises(ValueError):deliver(bridge,ai.store,message,dict(c,id='someone-else'))
    foreign=ai.companion.memory.media('other-scope',ai.companion.memory.read_media(message['media_id'])['data'])
    state=ai.data.run(run['id'])['state'];state['artifacts'].append(foreign)
    ai.data.update_run(run['id'],state=state)
    with pytest.raises(KeyError):deliver(bridge,ai.store,message,c)
    state['artifacts'].pop();ai.data.update_run(run['id'],state=state)
    ai.store.update_contact(c['id'],mode='manual')
    with pytest.raises(ReplySuperseded):deliver(bridge,ai.store,message,c)


def test_multiple_images_wait_for_all_receipts(setup):
    ai,c,run,_=live_run(setup)
    state=ai.data.run(run['id'])['state']
    buf=BytesIO();Image.new('RGB',(10,10)).save(buf,format='PNG')
    state['artifacts']=[ai.companion.memory.media(state['scope'],buf.getvalue()) for _ in range(2)]
    ai.data.update_run(run['id'],state=state)
    ai.finish(run['id'],'画好了')
    rows=ai.store.pending();assert len(rows)==2
    ai.store.set_message_state(rows[0]['id'],'confirmed',source_key='first')
    assert ai.data.run(run['id'])['status']=='queued'
    ai.store.set_message_state(rows[1]['id'],'uncertain',error='未核对送达')
    assert ai.data.run(run['id'])['status']=='uncertain' and not ai.store.pending()
