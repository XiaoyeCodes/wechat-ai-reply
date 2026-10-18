from datetime import datetime, timedelta, timezone
import json
from queue import Queue
from threading import Thread
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from wechat_panel.snapshots import align, compare
from wechat_panel.send_receipts import match
from wechat_panel.store import Store
from wechat_panel.bridge import clipped_text_direction, image_direction, SendUncertain


def item(key,text,direction='incoming'):
    return dict(key=key,text=text,kind='text',direction=direction,direction_verified=direction!='unknown')


def baseline():
    return [item('old1','这里是前面已经核对过的完整回复内容。','outgoing'),item('old2','这是对方刚刚提出的新问题，请帮我查询。')]


def rebuilt():
    return [{**x,'key':'new'+str(i),'direction':'unknown','direction_verified':False} for i,x in enumerate(baseline())]


def test_recycled_controls_align_context_keep_direction_without_replaying():
    old=baseline();new=rebuilt()+[item('reply','本次实际回复','outgoing')]
    current=align(old,new);delta=compare(old,current)
    assert [x['key'] for x in current]==['old1','old2','reply']
    assert [x['direction'] for x in current]==['outgoing','incoming','outgoing']
    assert delta.items==[current[-1]] and not delta.historical and not delta.gap
    # The next observation may contain just one existing anchor: raw IDs retain
    # its canonical identity even when a large reply clips earlier rows.
    next_frame=align(current,new[1:])
    assert next_frame[0]['key']=='old2' and compare(current,next_frame).items==[]


def test_repeated_single_text_never_used_to_join_recreated_window():
    for text in ['你好','相同的很长的句子。'*40]:
        old=[item('old',text)];new=[item('different',text)]
        assert align(old,new)[0]['key']=='different'
        assert compare(old,align(old,new)).historical
    old=[item('a','你好'),item('b','你好')]
    assert [x['key'] for x in align(old,[item('c','你好'),item('d','你好')])]==['c','d']
    assert compare(old,old+[item('e','你好')]).items==[item('e','你好')]


@pytest.fixture
def store_contact(tmp_path):
    store=Store(tmp_path/'panel.db')
    c=store.add_contact(dict(lookup='friend',nickname='friend',wechat_id='friend',account_id='self'))
    store.ingest(c['id'],baseline())
    return store,c


def test_scroll_up_and_repaint_keep_high_water_mark(store_contact):
    store,c=store_contact;cid=c['id']
    latest=baseline()+[item('latest','最后一个已接收的消息内容。')]
    store.ingest(cid,latest)
    events=len(store.events(agent_only=True))
    store.ingest(cid,baseline())  # user scrolls up
    with store.connect() as db:cursor=json.loads(db.execute('SELECT data FROM snapshots').fetchone()[0])
    assert cursor==latest
    store.ingest(cid,[]);store.ingest(cid,[])  # transient hidden/undrawn frames
    store.ingest(cid,latest+[item('fresh','真正刚收到的新消息')])
    assert len(store.events(agent_only=True))==events+1
    assert not [e for e in store.events() if e['type']=='listener.gap']
    assert len(store.messages(cid))==4


def test_disjoint_history_requires_stable_observations_and_is_not_replayed(store_contact):
    store,c=store_contact;cid=c['id'];new=[item('unrelated','完全没有锚点的历史')]
    store.ingest(cid,new);store.ingest(cid,new)
    assert len(store.messages(cid))==2
    store.ingest(cid,new)
    assert store.messages(cid)[-1]['historical'] and not store.events(agent_only=True)
    assert any(e['type']=='listener.gap' for e in store.events())
    store.ingest(cid,new+[item('after','恢复后的真正新消息')])
    assert len(store.events(agent_only=True))==1


def prepared(store,c,text='本次发送内容',attempted=True):
    message,_=store.enqueue(c['id'],text,'send-one')
    store.set_message_state(message['id'],'sending')
    store.send_receipt(message['id'],'prepared',baseline(),0)
    if attempted:store.send_receipt(message['id'],'attempted')
    store.set_message_state(message['id'],'uncertain','暂未核对')
    return message


def test_delayed_receipt_repairs_status_without_send_and_merges_echo(store_contact):
    store,c=store_contact;m=prepared(store,c)
    current=rebuilt()+[item('echo',m['text'],'unknown')]
    store.ingest(c['id'],current)
    assert store.message(m['id'])['status']=='uncertain'
    current[-1]=item('echo',m['text'],'outgoing')
    store.ingest(c['id'],current);store.ingest(c['id'],current)
    assert store.message(m['id'])['status']=='confirmed' and not store.pending()
    assert len(store.messages(c['id']))==3
    assert len([e for e in store.events() if e['type']=='send.recovered'])==1
    assert not store.events(agent_only=True)


@pytest.mark.parametrize('case',['incoming','unknown','changed','old','not_attempted','expired','other_contact'])
def test_unproven_or_stale_receipts_never_become_delivered(store_contact,case):
    store,c=store_contact;m=prepared(store,c,attempted=case!='not_attempted')
    current=baseline()+[item('echo',m['text'],'outgoing')]
    if case in ('incoming','unknown'):current[-1]=item('echo',m['text'],case)
    if case=='changed':current[-1]['text']+='不同的内容'
    if case=='old':
        store.send_receipt(m['id'],'prepared',current,1);store.send_receipt(m['id'],'attempted')
    if case=='expired':
        with store.connect() as db:db.execute('UPDATE send_receipts SET attempted_at=?',((datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat(),))
    if case=='other_contact':
        c=store.add_contact(dict(lookup='other',nickname='other',wechat_id='other',account_id='self'))
    store.ingest(c['id'],current)
    assert store.message(m['id'])['status']=='uncertain'


def test_receipt_survives_worker_restart(store_contact):
    store,c=store_contact;m=prepared(store,c)
    store.set_message_state(m['id'],'sending');store.recover()
    reopened=Store(store.path)
    reopened.ingest(c['id'],baseline()+[item('sent-after-restart',m['text'],'outgoing')])
    assert reopened.message(m['id'])['status']=='confirmed'
    assert not reopened.pending()


def test_clipped_bubble_uses_padding_when_text_obscures_old_sample_column():
    image=Image.new('RGB',(668,420),'#fafafa');draw=ImageDraw.Draw(image)
    draw.rectangle((155,80,595,275),fill=(157,242,159))
    draw.rectangle((580,80,588,275),fill='black')
    assert clipped_text_direction(image,(0,-220,668,285),1,(0,80,668,300))=='outgoing'


@pytest.mark.parametrize('scale',[1,1.25,1.75,2])
def test_long_bubble_below_viewport_keeps_its_visible_avatar(scale):
    image=Image.new('RGB',(round(600*scale),round(300*scale)),'#fafafa')
    x,y,size=round(20*scale),round(88*scale),round(36*scale)
    draw=ImageDraw.Draw(image)
    for offset in range(size):
        draw.line((x+offset,y,x+offset,y+size-1),fill='#254480' if offset%2 else '#df8f9e')
    box=lambda coords:tuple(round(v*scale) for v in coords)
    bounds=box((0,80,600,1000));clip=box((0,50,600,250))
    assert image_direction(image,bounds,scale,clip)=='incoming'
    assert image_direction(image.transpose(Image.Transpose.FLIP_LEFT_RIGHT),bounds,scale,clip)=='outgoing'
    assert image_direction(image,bounds,scale,box((0,100,600,250)))=='unknown'
    assert image_direction(image,bounds,scale,box((0,50,600,110)))=='unknown'


def test_busy_send_does_not_allow_later_parts_to_overtake(store_contact):
    from wechat_panel.runtime import Runtime
    store,c=store_contact
    first,_=store.enqueue(c['id'],'第一段','first');second,_=store.enqueue(c['id'],'第二段','second')
    other=store.add_contact(dict(lookup='other',nickname='other',wechat_id='other',account_id='self'))
    third,_=store.enqueue(other['id'],'其它联系人','third')
    runtime=Runtime(store);runtime.status={'state':'ready'}
    runtime.output=Queue();runtime.commands=Queue();runtime.process=SimpleNamespace(is_alive=lambda:True)
    thread=Thread(target=runtime._pump,daemon=True);thread.start()
    try:
        command=runtime.commands.get(timeout=3);assert command['message_id']==first['id']
        runtime.output.put(dict(kind='result',request_id=command['request_id'],message_id=first['id'],deferred=True,error='遮挡'))
        command=runtime.commands.get(timeout=2);assert command['message_id']==third['id']
        runtime.output.put(dict(kind='result',request_id=command['request_id'],message_id=third['id'],result={'source_key':'third-key'}))
        command=runtime.commands.get(timeout=4);assert command['message_id']==first['id']
        assert store.message(second['id'])['status']=='queued'
    finally:
        runtime.stopping.set();thread.join(timeout=2)


def test_paste_focus_loss_never_types_into_other_window(monkeypatch,tmp_path):
    from test_wechat_draft import make_bridge
    import win32gui
    obj,c,state,_=make_bridge(monkeypatch,tmp_path)
    state['text']=''  # no preexisting draft
    original_snapshot=obj.snapshot
    def snapshot(contact):
        # User switches windows after preparing, before clipboard paste.
        monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:999)
        return original_snapshot(contact)
    obj.snapshot=snapshot
    with pytest.raises(SendUncertain,match='粘贴前'):obj.send(c,'不要粘贴到其它软件')
    assert state['paste_count']==0 and not state['sent']


def test_late_confirmation_unblocks_remaining_ai_parts_only_once(store_contact):
    from wechat_panel.ai import AIService
    store,c=store_contact;ai=AIService(store)
    try:
        provider=ai.data.save('providers',None,dict(name='test',base_url='http://localhost:9000',model='test'))
        agent=ai.data.save('agents',None,dict(name='test',provider_id=provider['id']))
        ai.data.settings({'enabled':True})
        store.update_contact(c['id'],listening=True,mode='ai_auto',ai_agent_id=agent['id'])
        before=baseline()+[item('trigger','请输出一份长报告，谢谢。')]
        store.ingest(c['id'],before);trigger=store.messages(c['id'])[-1]
        run=ai.create_run(agent['id'],trigger['text'],c['id'],trigger['id'])
        ai.data.update_run(run['id'],status='queued')
        parts=store.enqueue_ai_reply(c['id'],'报告内容。'*600,run['id']);assert len(parts)>=2
        first=parts[0]
        store.set_message_state(first['id'],'sending')
        store.send_receipt(first['id'],'prepared',before);store.send_receipt(first['id'],'attempted')
        store.set_message_state(first['id'],'uncertain','等待回执')
        assert store.awaiting_receipt(parts[1]) and store.message(parts[1]['id'])['status']=='queued'
        current=before+[item('first-echo',first['text'],'outgoing')]
        store.ingest(c['id'],current)
        assert ai.data.run(run['id'])['status']=='queued' and store.ai_send_valid(parts[1])
        assert not store.awaiting_receipt(parts[1])
        for part in parts[1:]:store.set_message_state(part['id'],'confirmed',source_key='part-'+str(part['id']))
        assert ai.data.run(run['id'])['status']=='sent'
        store.ingest(c['id'],current)
        assert len([e for e in store.events() if e['type']=='send.recovered'])==1
    finally:ai.stop()


def test_send_intent_is_persisted_before_the_only_click(monkeypatch,tmp_path):
    from test_wechat_draft import make_bridge
    obj,c,state,_=make_bridge(monkeypatch,tmp_path);state['text']=''
    events=[]
    obj.receipt_observer=lambda stage,*args:events.append(stage)
    click=obj._click
    def recorded(control,contact,action):
        if action=='ui.send_once':
            assert events==['prepared','attempted']
            events.append('clicked')
        click(control,contact,action)
    obj._click=recorded
    assert obj.send(c,'完整流程测试')=='1'
    assert events==['prepared','attempted','clicked'] and len(state['sent'])==1


@pytest.mark.parametrize('foreground_after',[123,789])
def test_send_restores_previous_window_without_stealing_new_user_focus(monkeypatch,tmp_path,foreground_after):
    from test_wechat_draft import make_bridge
    import win32gui
    obj,c,state,_=make_bridge(monkeypatch,tmp_path)
    foreground={'value':456};restored=[]
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:foreground['value'])
    monkeypatch.setattr(win32gui,'SetForegroundWindow',lambda handle:restored.append(handle))
    def send(contact,text):
        foreground['value']=foreground_after
        return 'receipt'
    obj._send_text=send
    assert obj.send(c,'测试')=='receipt'
    assert restored==([456] if foreground_after==123 else [])


def test_late_receipt_never_reopens_a_cancelled_ai_run(store_contact):
    from wechat_panel.ai import AIService
    store,c=store_contact;ai=AIService(store)
    try:
        provider=ai.data.save('providers',None,dict(name='test',base_url='http://localhost:9000',model='test'))
        agent=ai.data.save('agents',None,dict(name='test',provider_id=provider['id']))
        run=ai.create_run(agent['id'],'测试',c['id'])
        parts=store.enqueue_ai_reply(c['id'],'报告内容。'*600,run['id'])
        first=parts[0]
        store.set_message_state(first['id'],'sending')
        store.send_receipt(first['id'],'prepared',baseline());store.send_receipt(first['id'],'attempted')
        store.set_message_state(first['id'],'uncertain','等待回执')
        for part in parts[1:]:store.set_message_state(part['id'],'cancelled','人工接管')
        ai.data.update_run(run['id'],status='cancelled')
        store.ingest(c['id'],baseline()+[item('echo',first['text'],'outgoing')])
        assert store.message(first['id'])['status']=='confirmed'
        assert ai.data.run(run['id'])['status']=='cancelled' and not store.pending()
    finally:ai.stop()
