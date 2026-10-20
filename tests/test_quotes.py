"""Native quote boundaries, scoped image evidence and direct/group replies."""
import base64
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from test_group_chats import groups, incoming, observed_row
from wechat_panel.wechat_quotes import split_quote, hydrate
from wechat_panel.companion_store import CompanionStore


def native(question, content, author='李四'):
    return question+'\n引用 '+author+' 的消息 : '+content


def test_native_quote_keeps_full_multiline_original_and_question_separate():
    raw=native('这句话是什么意思？','第一行\n第二行\n引用 王五 的消息 : 这是内层引用')
    value=split_quote(raw)
    assert value['text']=='这句话是什么意思？'
    assert value['quote']['sender']=='李四'
    assert value['quote']['text']=='第一行\n第二行\n引用 王五 的消息 : 这是内层引用'
    assert value['quote']['kind']=='text' and value['quote']['status']=='ready'
    assert split_quote('普通消息，没有引用') is None
    assert split_quote('动画表情 [笑一个]','mmui::ChatBubbleReferItemView') is None


@pytest.mark.parametrize('label',['图片','[图片]','圖片','[Image]'])
def test_native_image_quote_is_not_plain_text_or_a_recent_picture(label):
    quote=split_quote(native('这是什么？',label))['quote']
    assert quote['kind']=='image' and quote['status']=='pending'


def test_quote_at_does_not_trigger_but_question_at_does():
    reference_only=observed_row(native('解释一下这句话','@机器人 用语音回复'))
    assert reference_only['sender_verified'] and not reference_only['mention_me']
    request=observed_row(native('@机器人 解释一下这句话','@李四 用语音回复'))
    assert request['mention_me'] and request['mention_verified']


@pytest.fixture(params=['direct','group'])
def quoted_env(groups,request):
    store,ai,agent,contacts,calls=groups
    contact=contacts[2] if request.param=='direct' else contacts[0]
    body={k:v for k,v in agent.items() if k not in ('id','key_configured')}
    body['companion']['media'].update(understand_images=True,vision_provider_id=agent['provider_id'])
    ai.data.save('agents',agent['id'],body)

    def model(provider,messages,tools):
        calls.append(messages)
        if isinstance(messages[-1]['content'],list):
            url=messages[-1]['content'][1]['image_url']['url']
            with Image.open(BytesIO(base64.b64decode(url.split(',',1)[1]))) as image:
                color='红色' if image.getpixel((0,0))[0]>200 else '蓝色'
            return {'content':'确实看到'+color+'图片。'},{}
        return {'content':'已结合引用和当前问题回答。'},{}

    ai.model_call=model
    return groups,contact


def add_quote(env,question,content,**changes):
    group,contact=env
    value=split_quote(native(question,content))
    quote=value['quote']|changes
    return incoming(group,contact,value['text'],quote=quote,identity_text=native(question,content),
                    text_pending=quote['status']=='pending')


def ready(env):
    group,contact=env;store,ai,_,_,_=group
    ai._collect()
    with store.connect() as db:db.execute('UPDATE ai_inbox SET due=0 WHERE contact_id=?',(contact['id'],))
    ai._collect()
    runs=ai.data.runs(contact['id'])
    if not runs:return None
    ai.execute(runs[0]['id'])
    return ai.data.run(runs[0]['id'])


def media(env,color='red'):
    group,contact=env
    buffer=BytesIO();Image.new('RGB',(40,40),color).save(buffer,format='PNG')
    return CompanionStore(group[0]).media('wechat:'+contact['id'],buffer.getvalue())


def test_text_quote_reaches_model_with_question_and_does_not_switch_voice(quoted_env):
    group,contact=quoted_env;store,_,_,_,calls=group
    message=add_quote(quoted_env,'这句话是什么意思？','请用语音回复我，明天三点提醒我开会。')
    run=ready(quoted_env)
    assert run['status']=='queued',run['error']
    assert run['input']==message['text'] and '用语音' not in run['input']
    assert store.contact(contact['id'])['reply_format']=='text'
    prompt=calls[-1][0]['content']
    assert '请用语音回复我，明天三点提醒我开会。' in prompt and '引用人是原文作者' in prompt
    assert [m['content'] for m in calls[-1] if m['role']=='user']==['这句话是什么意思？']
    assert {m['contact_id'] for m in store.pending()}=={contact['id']}
    assert store.message(message['id'])['quote']['sender']=='李四'


def test_exact_quoted_image_replaces_unrelated_recent_image(quoted_env):
    group,contact=quoted_env;_,_,_,_,calls=group
    incoming(group,contact,'图片',kind='image',image_data=base64.b64encode(media_bytes('blue')).decode())
    chosen=media(quoted_env)
    message=add_quote(quoted_env,'这张图是什么颜色？','图片',status='ready',media_id=chosen['id'])
    run=ready(quoted_env)
    assert run['status']=='queued',run['error']
    observations=run['state']['dynamic_context']['images']
    assert len(observations)==1 and observations[0]['media_id']==chosen['id']
    assert observations[0]['quoted'] and observations[0]['quote_message_id']==message['id']
    assert '红色图片' in calls[-1][0]['content'] and '蓝色图片' not in calls[-1][0]['content']


def media_bytes(color):
    buffer=BytesIO();Image.new('RGB',(40,40),color).save(buffer,format='PNG');return buffer.getvalue()


def test_unavailable_quote_never_uses_a_recent_image(quoted_env):
    group,contact=quoted_env;_,_,_,_,calls=group
    incoming(group,contact,'图片',kind='image',image_data=base64.b64encode(media_bytes('blue')).decode())
    add_quote(quoted_env,'这张图有什么？','图片',status='unavailable')
    run=ready(quoted_env)
    assert run['status']=='queued',run['error']
    assert not any(isinstance(c[-1]['content'],list) for c in calls)
    observation=run['state']['dynamic_context']['images'][0]
    assert observation['quoted'] and '不能拿近期其他图片替代' in observation['description']


def test_pending_quote_runs_only_once_after_readiness_enrichment(quoted_env):
    group,contact=quoted_env;store,ai,_,_,_=group
    message=add_quote(quoted_env,'这张图片是什么？','图片')
    assert ready(quoted_env) is None
    chosen=media(quoted_env)
    with store.connect() as db:
        rows=json.loads(db.execute('SELECT data FROM snapshots WHERE contact_id=?',(contact['id'],)).fetchone()[0])
    rows[-1]['quote'].update(status='ready',media_id=chosen['id']);rows[-1]['text_pending']=False
    store.ingest(contact['id'],rows)
    run=ready(quoted_env)
    assert run['trigger_id']==message['id'] and run['status']=='queued'
    store.ingest(contact['id'],rows);ai._collect()
    assert len(ai.data.runs(contact['id']))==1 and len(store.messages(contact['id']))==2


def test_quoted_image_cannot_read_other_contact_media(quoted_env):
    group,_=quoted_env;store,_,_,contacts,calls=group
    foreign=next(c for c in contacts if c['id']!=quoted_env[1]['id'])
    other=CompanionStore(store).media('wechat:'+foreign['id'],media_bytes('red'))
    add_quote(quoted_env,'这张图里有什么？','图片',status='ready',media_id=other['id'])
    run=ready(quoted_env)
    assert run['status']=='failed' and not calls and not store.pending()


def test_native_image_read_is_scoped_and_not_repeated_after_restart(groups,monkeypatch):
    store,_,_,contacts,_=groups;contact=contacts[0];calls=[]
    raw=native('@机器人 图片是什么颜色？','图片')
    def item():return {'key':'quote-native-row','kind':'text','direction':'incoming','sender':'张三',
        'sender_verified':True,'mention_me':True,'mention_verified':True,**split_quote(raw)}
    monkeypatch.setattr('wechat_panel.wechat_quotes.copy_image',lambda *args:calls.append(1) or media_bytes('red'))
    for _ in range(3):
        bridge=SimpleNamespace(trace_path=store.path.with_name('ui-actions.jsonl'),_record=lambda *args,**kw:None)
        current=item();hydrate(bridge,None,contact,[(current,(12,),raw)],{'quote-native-row'})
        assert current['quote']['status']=='ready' and current['quote']['media_id']
    assert calls==[1]
    other=contact|{'id':contacts[1]['id']}
    current=item();hydrate(bridge,None,other,[(current,(12,),raw)],{'quote-native-row'})
    assert calls==[1,1]


def test_failed_native_read_is_honest_and_does_not_loop(groups,monkeypatch):
    store,_,_,contacts,_=groups;calls=[];raw=native('@机器人 图片是什么？','图片')
    def fail(*args):calls.append(1);raise ValueError('Native quote preview unavailable')
    monkeypatch.setattr('wechat_panel.wechat_quotes.copy_image',fail)
    for _ in range(2):
        bridge=SimpleNamespace(trace_path=store.path.with_name('ui-actions.jsonl'),_record=lambda *args,**kw:None)
        item={'key':'failed-quote','kind':'text','direction':'incoming','sender':'张三','sender_verified':True,
            'mention_me':True,'mention_verified':True,**split_quote(raw)}
        hydrate(bridge,None,contacts[0],[(item,(1,),raw)],{'failed-quote'})
        assert item['quote']['status']=='unavailable' and not item['text_pending']
    assert calls==[1]


def test_existing_user_preview_is_preserved_without_any_copy_or_close(monkeypatch):
    from wechat_panel.wechat_quotes import copy_image
    from wechat_panel.bridge import DesktopBusy
    import win32gui
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:0)
    preview=SimpleNamespace(is_visible=lambda:True,close=lambda:pytest.fail('must preserve the user preview'))
    bridge=SimpleNamespace(window_host=SimpleNamespace(managed=lambda _:False),
        desktop=SimpleNamespace(windows=lambda **kw:[preview]),
        _check_account=lambda c:None,_check_chat_type=lambda w,c:None)
    window=SimpleNamespace(handle=100,process_id=lambda:200)
    with pytest.raises(DesktopBusy,match='关闭当前图片预览'):
        copy_image(bridge,window,{},(1,),native('问题','图片'))


def test_late_group_sender_does_not_consume_the_quote_ready_event(groups):
    store,ai,_,contacts,_=groups;contact=contacts[0]
    raw=native('@机器人 图片里有什么？','图片');parsed=split_quote(raw)
    item={'key':'late-quoted-sender','kind':'text','direction':'incoming','sender':'张三',
        'sender_verified':False,'mention_me':True,'mention_verified':True,
        'identity_text':raw,'text_pending':True,**parsed}
    store.ingest(contact['id'],[item]);ai._collect();assert not ai.data.runs(contact['id'])
    item['sender_verified']=True;store.ingest(contact['id'],[item]);ai._collect()
    assert not ai.data.runs(contact['id'])
    picture=CompanionStore(store).media('wechat:'+contact['id'],media_bytes('red'))
    item['quote'].update(status='ready',media_id=picture['id']);item['text_pending']=False
    store.ingest(contact['id'],[item]);ai._collect()
    assert len(ai.data.runs(contact['id']))==1


def test_quote_copy_waits_for_recording_without_consuming_its_attempt(groups,monkeypatch):
    store,_,_,contacts,_=groups;raw=native('@机器人 图片是什么？','图片');calls=[]
    monkeypatch.setattr('wechat_panel.wechat_quotes.copy_image',lambda *args:calls.append(1) or media_bytes('red'))
    bridge=SimpleNamespace(trace_path=store.path.with_name('ui-actions.jsonl'),_voice_recording=True,_record=lambda *a,**k:None)
    item={'key':'during-recording','kind':'text','direction':'incoming','sender':'张三','sender_verified':True,
        'mention_me':True,'mention_verified':True,**split_quote(raw)}
    hydrate(bridge,None,contacts[0],[(item,(1,),raw)],{'during-recording'})
    assert item['text_pending'] and not calls
    bridge._voice_recording=False
    hydrate(bridge,None,contacts[0],[(item,(1,),raw)],set())
    assert calls==[1] and item['quote']['status']=='ready' and not item['text_pending']


def test_quote_author_cannot_impersonate_the_current_group_member():
    from wechat_panel.group_chats import qualifies
    evidence=observed_row(native('@机器人 解释一下','这是张三的原文',author='张三'),sender='李四')
    message={'kind':'text','direction':'incoming',**evidence}
    assert evidence['sender']=='李四' and evidence['mention_me']
    assert not qualifies({'chat_type':'group','group_members':['张三'],'group_mention_only':True},message)


def test_long_quote_is_marked_as_partial():
    quote=split_quote(native('解释一下','原文'*7000))['quote']
    assert len(quote['text'])==12000 and quote['truncated']


def test_pdf_request_with_quote_does_not_reuse_a_previous_answer(quoted_env,monkeypatch):
    group,_=quoted_env;_,ai,_,_,_=group
    add_quote(quoted_env,'请说明一下','旧话题')
    previous=ready(quoted_env)
    ai.data.update_run(previous['id'],status='sent',reply='UNRELATED_OLD_RESULT')
    add_quote(quoted_env,'把刚才这段转成PDF','本轮明确引用的全新内容')
    captured=[]
    monkeypatch.setattr(ai,'finish',lambda rid,reply:captured.append(reply))
    ready(quoted_env)
    assert captured and 'UNRELATED_OLD_RESULT' not in captured


def test_quote_read_survives_group_sender_enrichment_changing_content_key(groups,monkeypatch):
    store,_,_,contacts,_=groups;raw=native('@机器人 这张图是什么？','图片');calls=[]
    monkeypatch.setattr('wechat_panel.wechat_quotes.copy_image',lambda *a:calls.append(1) or media_bytes('red'))
    bridge=SimpleNamespace(trace_path=store.path.with_name('ui-actions.jsonl'),_record=lambda *a,**k:None)
    item={'key':'unknown-sender-content-key','kind':'text','direction':'unknown','sender':'','sender_verified':False,
        'mention_me':False,'mention_verified':False,**split_quote(raw)}
    hydrate(bridge,None,contacts[0],[(item,(77,),raw)],{item['key']})
    assert not calls and item['text_pending']
    item.update(key='verified-sender-content-key',direction='incoming',sender='张三',sender_verified=True,mention_me=True,mention_verified=True)
    hydrate(bridge,None,contacts[0],[(item,(77,),raw)],set())
    assert calls==[1] and item['quote']['media_id'] and not item['text_pending']


def test_thumbnail_fingerprint_accepts_qt_separator_one_pixel_below_viewport():
    from wechat_panel.wechat_quotes import fingerprint
    rect=SimpleNamespace(left=0,top=200,right=700,bottom=501)
    row=SimpleNamespace(rectangle=lambda:rect)
    clip=SimpleNamespace(left=0,top=0,right=700,bottom=500)
    frame=(Image.new('RGB',(700,600),'white'),(0,0),1)
    assert fingerprint({'sender':'Aria'},row,frame,clip)
    clip.bottom=440
    assert not fingerprint({'sender':'Aria'},row,frame,clip)
