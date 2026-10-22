"""Idle, history and repaint observations must never authorize desktop actions."""
from types import SimpleNamespace

from PIL import Image
import pytest

from wechat_panel.wechat_reads import ReadGate


GROUP={'chat_type':'group','mode':'ai_auto','listening':True,'_read_images':True,
    'group_self_name':'机器人','group_self_name_verified':True,'group_mention_only':True}
DIRECT=GROUP|{'chat_type':'direct'}


@pytest.mark.parametrize('body,question',[
    ('@Aria\u2005只回复OK','只回复OK'),
    ('请@Aria\u2005只回复OK','请\u2005只回复OK'),
    ('请只回复OK@Aria\u2005','请只回复OK'),
    ('@Aria\u2005开头，中间@Aria\u2005，以及末尾@Aria\u2005','开头，中间\u2005，以及末尾'),
])
def test_native_mention_at_any_position_admits_request_without_a_leading_space(body,question):
    from wechat_panel.group_chats import request_text
    contact=GROUP|{'group_self_name':'Aria'}
    gate=ReadGate();rows=[item('anchor')];gate.observe(GROUP,rows,0)
    rows+=[item('question',body)]
    assert set(gate.observe(contact,rows,1))=={'question'}
    assert request_text(contact,rows[-1]['text'])==question


def item(key,text='一段有足够长度的普通历史消息',kind='text',direction='incoming'):
    return {'key':key,'native_token':key,'text':text,'kind':kind,'direction':direction}


@pytest.mark.parametrize('contact',[GROUP,DIRECT])
def test_baseline_idle_scrolling_and_return_to_known_tail_never_authorize_reads(contact):
    gate=ReadGate();history=[item('a','较早的一条历史消息'),item('b','第二条历史消息'),item('c','@机器人 旧问题'),item('d','图片','image')]
    assert not gate.observe(contact,history,0)
    for n in range(1,80):assert not gate.observe(contact,history,n)
    assert not gate.observe(contact,history[:2],80)
    assert not gate.observe(contact,history,81)
    older=[item('older','@机器人 更早的历史问题'),item('older-image','图片','image')]
    assert not gate.observe(contact,older,82)
    assert not gate.observe(contact,history,83)
    assert not gate.observe(contact,history,84)


@pytest.mark.parametrize('contact',[GROUP,DIRECT])
def test_repaint_with_recreated_native_ids_is_not_a_new_request(contact):
    gate=ReadGate()
    old=[item('a','第一条用于稳定对齐的完整历史消息'),item('b','第二条用于稳定对齐的完整历史消息')]
    gate.observe(contact,old,0)
    changed=[item('new-a',old[0]['text']),item('new-b',old[1]['text'])]
    assert not gate.observe(contact,changed,1)
    assert not gate.observe(contact,changed,2)


def test_group_unmentioned_text_and_images_are_passive_until_new_at_question():
    gate=ReadGate();rows=[item('anchor')];gate.observe(GROUP,rows,0)
    rows+=[item('picture','图片','image')];assert not gate.observe(GROUP,rows,1)
    rows+=[item('ordinary','其他成员的普通聊天')];assert not gate.observe(GROUP,rows,2)
    rows+=[item('question','@机器人 这张图片是什么颜色？')]
    assert set(gate.observe(GROUP,rows,3))=={'picture','question'}
    rows+=[item('after','图片','image')]
    assert 'after' in gate.observe(GROUP,rows,4)
    assert not gate.observe(GROUP,rows,124)


def test_group_reference_at_does_not_authorize_reads_but_fresh_question_at_does():
    gate=ReadGate();rows=[item('anchor')];gate.observe(GROUP,rows,0)
    rows+=[item('quoted-at','解释一下\n引用 Aria 的消息 : @机器人 你好')]
    assert not gate.observe(GROUP,rows,1)
    rows+=[item('image','图片','image')];gate.observe(GROUP,rows,2)
    rows+=[item('real','@机器人 看这张引用图\n引用 Aria 的消息 : 图片')]
    assert set(gate.observe(GROUP,rows,3))=={'real'}


def test_old_at_question_cannot_activate_later_unmentioned_picture():
    gate=ReadGate();rows=[item('history','@机器人 旧问题')];gate.observe(GROUP,rows,0)
    rows+=[item('picture','图片','image')]
    assert not gate.observe(GROUP,rows,1)


@pytest.mark.parametrize('body',['> @机器人 你好','他刚才说“@机器人 你好”','解释一下\n引用 Aria 的消息 : @机器人 你好'])
def test_at_inside_quoted_data_never_authorizes_member_selection(body):
    gate=ReadGate();rows=[item('anchor')];gate.observe(GROUP,rows,0)
    rows+=[item('quote',body)]
    assert not gate.observe(GROUP,rows,1)


def test_recording_delay_preserves_related_image_but_not_later_unrelated_images():
    gate=ReadGate();rows=[item('anchor')];gate.observe(GROUP,rows,0)
    rows+=[item('picture','图片','image')];gate.observe(GROUP,rows,1)
    rows+=[item('question','@机器人 看看图片')];gate.observe(GROUP,rows,2)
    rows+=[item('related','图片','image')];gate.observe(GROUP,rows,5)
    rows+=[item('unrelated','图片','image')]
    allowed=gate.observe(GROUP,rows,65)
    assert set(allowed)=={'picture','question','related'}


def test_new_request_after_following_unread_uses_last_live_tail_not_scrolled_history():
    gate=ReadGate();latest=[item('a','最新窗口第一条足够长的消息'),item('b','最新窗口第二条足够长的消息')]
    gate.observe(GROUP,latest,0)
    history=[item('older-a','更早的历史消息一'),item('older-b','@机器人 更早的历史问题')]
    assert not gate.observe(GROUP,history,1)
    current=latest+[item('new','@机器人 本次新问题')]
    assert set(gate.observe(GROUP,current,2,followed=True))=={'new'}
    token=gate.admission_tokens['new']
    assert gate.fresh_tokens=={'new'}
    gate.observe(GROUP,current,3)
    assert gate.admission_tokens['new']==token


def test_initial_unread_follow_does_not_admit_pre_start_history():
    gate=ReadGate();rows=[item('old','@机器人 启动前的消息')]
    assert not gate.observe(GROUP,rows,0,followed=True)
    assert not gate.fresh_tokens


def test_empty_loading_frames_do_not_turn_first_history_frame_into_live_requests():
    gate=ReadGate()
    for n in range(3):assert not gate.observe(GROUP,[],n)
    assert not gate.observe(GROUP,[item('history','@机器人 启动前的问题')],3)
    assert not gate.fresh_tokens and not gate.admission_tokens


@pytest.mark.parametrize('contact',[GROUP|{'mode':'manual'},GROUP|{'mode':'listen'},GROUP|{'listening':False}])
def test_inactive_ai_never_permits_native_reads(contact):
    gate=ReadGate();rows=[item('anchor')];gate.observe(contact,rows,0)
    rows+=[item('question','@机器人 你好'),item('image','图片','image')]
    assert not gate.observe(contact,rows,1)


def test_direct_only_fresh_incoming_rows_can_be_read():
    gate=ReadGate();rows=[item('anchor')];gate.observe(DIRECT,rows,0)
    rows+=[item('own','自己发送的消息',direction='outgoing')]
    assert not gate.observe(DIRECT,rows,1)
    rows+=[item('question','引用图片的问题\n引用 Aria 的消息 : 图片')]
    assert set(gate.observe(DIRECT,rows,2))=={'question'}


def test_new_request_can_wait_for_direction_proof_without_becoming_history():
    gate=ReadGate();rows=[item('anchor')];gate.observe(GROUP,rows,0)
    rows+=[item('question','@机器人 你好',direction='unknown')]
    first=gate.observe(GROUP,rows,1)
    rows[-1]['direction']='incoming'
    assert gate.observe(GROUP,rows,2)==first=={'question':'question'}


def test_sender_identification_has_a_bounded_budget_per_actual_new_request():
    gate=ReadGate();rows=[item('anchor')];gate.observe(GROUP,rows,0)
    rows+=[item('question','@机器人 新问题')];gate.observe(GROUP,rows,1)
    for _ in range(2):
        assert gate.can_identify('question');gate.identifying(['question'])
    assert not gate.can_identify('question')
    gate.observe(GROUP,rows,2)
    assert not gate.can_identify('question')


def test_any_member_trigger_mode_still_allows_fresh_ordinary_text():
    contact=GROUP|{'group_mention_only':False};gate=ReadGate();rows=[item('anchor')]
    gate.observe(contact,rows,0);rows+=[item('question','没有 @ 的新问题')]
    assert set(gate.observe(contact,rows,1))=={'question'}


def test_sender_cache_invalidation_without_fresh_requests_never_enters_selection():
    from wechat_panel.group_senders import hydrate,stamp
    rect=SimpleNamespace(left=0,top=10,right=300,bottom=100)
    node=SimpleNamespace(element_info=SimpleNamespace(runtime_id=(1,),control_type='ListItem'),
        class_name=lambda:'mmui::ChatTextItemView',window_text=lambda:'@机器人 历史问题',rectangle=lambda:rect)
    clip=SimpleNamespace(left=0,top=0,right=300,bottom=200)
    listing=SimpleNamespace(rectangle=lambda:clip)
    contact=GROUP|{'account_id':'self','wechat_id':'group:test'}
    key=('self','group:test',100)
    bridge=SimpleNamespace(group_rosters={('self','group:test'):['Aria','机器人']},
        group_sender_cache={key:{stamp(node):'Aria'}},group_sender_headers={key:{stamp(node):b'old'}},
        Lists=SimpleNamespace(FriendChatList={}),_capture=lambda w:(Image.new('RGB',(300,200),'white'),(0,0),1),
        _focus=lambda *a:pytest.fail('idle history must never focus or click'))
    window=SimpleNamespace(handle=100,child_window=lambda **kw:listing)
    for _ in range(10):hydrate(bridge,window,contact,[node],allowed={})
    assert not bridge.group_sender_cache[key]


@pytest.mark.parametrize('group',[False,True])
def test_native_snapshot_copies_only_fresh_requested_images_and_stays_quiet_on_history(tmp_path,monkeypatch,group):
    from io import BytesIO
    from pywinauto.win32structures import RECT
    from wechat_panel.bridge import WeixinBridge
    from wechat_panel.group_senders import stamp
    monkeypatch.setattr('wechat_panel.wechat_reads.follow_unread',lambda *a:False)
    contact=(GROUP if group else DIRECT)|{'id':'c','lookup':'测试会话','account_id':'self','wechat_id':'group:test'}
    rows=[];copies=[];selections=[]
    def native_row(rid,body,image=False):
        return SimpleNamespace(element_info=SimpleNamespace(runtime_id=(rid,),control_type='ListItem'),
            class_name=lambda:'mmui::ChatImageItemView' if image else 'mmui::ChatTextItemView',
            window_text=lambda:body,rectangle=lambda:RECT(0,20+rid*75,900,90+rid*75),descendants=lambda:[])
    rows.extend([native_row(1,'第一条用于稳定对齐的完整历史消息'),native_row(2,'@机器人 旧问题'),native_row(3,'图片',True)])
    listing=SimpleNamespace(exists=lambda **kw:True,children=lambda:list(rows),rectangle=lambda:RECT(0,0,900,800))
    window=SimpleNamespace(handle=100,class_name=lambda:'mmui::ChatSingleWindow',child_window=lambda **kw:listing)
    bridge=WeixinBridge.__new__(WeixinBridge)
    bridge.windows={'c':window};bridge.previous_items={};bridge.image_pending={};bridge.image_cache={}
    bridge.Lists=SimpleNamespace(FriendChatList={});bridge.window_host=SimpleNamespace(ensure=lambda *a:False)
    bridge.trace_path=tmp_path/'ui-actions.jsonl';bridge.group_rosters={('self','group:test'):['Aria','机器人']}
    bridge._window_alive=lambda w:True;bridge._title=lambda w:contact['lookup']
    bridge._check_account=lambda c:None;bridge._check_chat_type=lambda w,c:None
    bridge._direction=lambda row,*a:('incoming',row.window_text())
    bridge._capture=lambda w:(Image.new('RGB',(900,900),'white'),(0,0),1)
    bridge._record=lambda *a,**k:None
    def identify(b,w,c,current,allowed,gate):
        cache=getattr(b,'group_sender_cache',{}).get(('self','group:test',100),{})
        if not any(stamp(row) in allowed and stamp(row) not in cache for row in current):return
        selections.append(set(allowed))
        b.group_sender_cache={('self','group:test',100):{stamp(row):'Aria' for row in current}}
    monkeypatch.setattr('wechat_panel.group_senders.hydrate',identify)
    buffer=BytesIO();Image.new('RGB',(10,10),'red').save(buffer,format='PNG')
    monkeypatch.setattr('wechat_panel.wechat_images.copy_bubble',lambda b,w,c,rid:copies.append(rid) or buffer.getvalue())
    bridge.snapshot(contact)
    for _ in range(3):bridge.snapshot(contact)
    assert not copies and not selections
    rows.append(native_row(4,'图片',True));bridge.snapshot(contact)
    assert copies==([] if group else [(4,)])
    if group:assert not selections
    rows.append(native_row(5,'@机器人 看看刚才的图片' if group else '看看刚才的图片'))
    result=bridge.snapshot(contact)
    assert copies==[(4,)]
    assert any(m['kind']=='image' and m.get('image_data') for m in result)
    fresh=next(m for m in result if m['text'].endswith('看看刚才的图片'))
    assert fresh['native_fresh'] and fresh['key'].startswith('100:live:')
    before=len(selections)
    for _ in range(5):bridge.snapshot(contact)
    rows[:]=rows[:3];bridge.snapshot(contact)
    for _ in range(3):bridge.snapshot(contact)
    assert copies==[(4,)] and len(selections)==before


@pytest.mark.parametrize('active,unread,valid_name,expected',[
    (False,True,True,1),(True,True,True,1),(False,False,True,0),(False,True,False,0)])
def test_unread_follow_invokes_only_the_owned_native_marker_without_mouse_or_focus(monkeypatch,active,unread,valid_name,expected):
    from wechat_panel.wechat_reads import follow_unread
    import win32gui,win32con
    from pywinauto.win32structures import RECT
    calls=[];state={'visible':unread}
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:100 if active else 200)
    monkeypatch.setattr(win32gui,'GetAncestor',lambda h,f:h)
    monkeypatch.setattr(win32gui,'GetWindowRect',lambda h:(0,0,500,500))
    monkeypatch.setattr(win32gui,'ScreenToClient',lambda h,p:p)
    def send(handle,message,*args):
        assert handle==100
        if message==win32con.WM_LBUTTONUP:
            calls.append('press');state['visible']=False
    monkeypatch.setattr(win32gui,'SendMessageTimeout',send)
    button=SimpleNamespace(exists=lambda **kw:state['visible'],is_visible=lambda:state['visible'],
        window_text=lambda:'1条新消息' if valid_name else '旧消息',
        element_info=SimpleNamespace(runtime_id=(42,100,4,9)),top_level_parent=lambda:SimpleNamespace(handle=100),
        rectangle=lambda:RECT(200,200,400,300))
    window=SimpleNamespace(handle=100,child_window=lambda **kw:button)
    bridge=SimpleNamespace(_check_account=lambda c:None,_check_chat_type=lambda w,c:None,
        _title=lambda w:'测试群',_record=lambda *a,**k:None)
    contact={'account_id':'self','id':'group','lookup':'测试群'}
    for _ in range(20):follow_unread(bridge,window,contact)
    assert len(calls)==expected
    if expected:
        state['visible']=False;follow_unread(bridge,window,contact)
        state['visible']=True;follow_unread(bridge,window,contact)
        assert calls==['press','press']
