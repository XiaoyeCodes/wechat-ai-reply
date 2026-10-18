from types import SimpleNamespace

import pyautogui
import win32gui
import pytest

from wechat_panel import bridge as module
from PIL import Image, ImageDraw
from wechat_panel.bridge import WeixinBridge, image_direction, clipped_text_direction


def test_click_waits_for_restored_control_bounds_without_clicking_other_window(monkeypatch):
    from pywinauto.win32structures import RECT
    obj=WeixinBridge.__new__(WeixinBridge)
    bounds=iter([RECT(100,100,200,200),RECT(300,300,400,400)])
    control=SimpleNamespace(rectangle=lambda:next(bounds),top_level_parent=lambda:SimpleNamespace(handle=123))
    clicks=[]
    monkeypatch.setattr(win32gui,'WindowFromPoint',lambda p:123 if p==(350,350) else 456)
    monkeypatch.setattr(win32gui,'GetAncestor',lambda h,flag:h)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    monkeypatch.setattr(pyautogui,'click',lambda *p,**kw:clicks.append(p))
    obj._click(control,{},'test')
    assert clicks==[(350,350)]


def test_click_never_clicks_persistent_covering_window(monkeypatch):
    from pywinauto.win32structures import RECT
    obj=WeixinBridge.__new__(WeixinBridge)
    control=SimpleNamespace(rectangle=lambda:RECT(100,100,200,200),top_level_parent=lambda:SimpleNamespace(handle=123))
    monkeypatch.setattr(win32gui,'WindowFromPoint',lambda p:456)
    monkeypatch.setattr(win32gui,'GetAncestor',lambda h,flag:h)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    monkeypatch.setattr(pyautogui,'click',lambda *a,**kw:pytest.fail('must not click an occluding app'))
    with pytest.raises(module.BridgeError,match='遮挡'):
        obj._click(control,{},'test')


def test_send_baseline_counts_existing_bubbles_with_trimmed_edges():
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.Lists = SimpleNamespace(FriendChatList={})
    rows = [SimpleNamespace(class_name=lambda: 'mmui::ChatTextItemView', body=body)
            for body in ['标题\r\n正文', '\n标题\n正文\n', '标题\n正文不同']]
    window = SimpleNamespace(child_window=lambda **kw: SimpleNamespace(children=lambda: rows))
    obj._direction = lambda row, *args: ('outgoing', row.body)
    assert obj._visible_text_count(window, {}, '标题\n正文\n\n') == 2


def test_normal_chat_direction_requires_one_avatar_and_opposite_clear_space():
    image = Image.new('RGB', (600, 80), '#fafafa')
    assert image_direction(image, (0, 0, 600, 80)) == 'unknown'
    draw = ImageDraw.Draw(image)
    for x in range(18, 54):
        draw.line((x, 10, x, 46), fill='#254480' if x % 2 else '#df8f9e')
    assert image_direction(image, (0, 0, 600, 80)) == 'incoming'
    assert image_direction(image.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (0, 0, 600, 80)) == 'outgoing'
    image.paste(image.crop((18, 10, 54, 47)), (546, 10))
    assert image_direction(image, (0, 0, 600, 80)) == 'unknown'
    assert image_direction(image, (0, -5, 600, 75)) == 'unknown'
    assert image_direction(Image.new('RGB', (600, 80)), (0, 0, 600, 80)) == 'unknown'


@pytest.mark.parametrize('scale',[1,1.25,1.75,2])
def test_direction_ignores_avatar_pixels_outside_message_viewport(scale):
    image=Image.new('RGB',(round(600*scale),round(80*scale)),'#fafafa')
    x,y,size=round(20*scale),round(8*scale),round(36*scale)
    draw=ImageDraw.Draw(image)
    for offset in range(size):
        draw.line((x+offset,y,x+offset,y+size-1),fill='#254480' if offset%2 else '#df8f9e')
    bounds=(0,0,image.width,image.height)
    assert image_direction(image,bounds,scale,bounds)=='incoming'
    # Header or composer pixels are not evidence for a clipped message.
    assert image_direction(image,bounds,scale,(0,y+1,image.width,image.height))=='unknown'
    assert image_direction(image,bounds,scale,(0,0,image.width,y+size-1))=='unknown'


@pytest.mark.parametrize('scale',[1,1.25,1.75,2])
def test_clipped_long_text_requires_green_right_bubble_and_clear_left(scale):
    image=Image.new('RGB',(round(668*scale),round(420*scale)),'#fafafa')
    box=lambda coords:tuple(round(v*scale) for v in coords)
    bounds=box((0,-220,668,285));clip=box((0,80,668,300))
    assert clipped_text_direction(image,bounds,scale,clip)=='unknown'
    ImageDraw.Draw(image).rectangle(box((155,80,595,275)),fill=(157,242,159))
    assert clipped_text_direction(image,bounds,scale,clip)=='outgoing'
    # Insufficient visible area, and pixels in header/composer, are not proof.
    assert clipped_text_direction(image,bounds,scale,box((0,260,668,300)))=='unknown'
    assert clipped_text_direction(image,bounds,scale,box((0,300,668,420)))=='unknown'
    assert clipped_text_direction(image.transpose(Image.Transpose.FLIP_LEFT_RIGHT),bounds,scale,clip)=='unknown'
    ImageDraw.Draw(image).rectangle(box((70,80,95,275)),fill=(157,242,159))
    assert clipped_text_direction(image,bounds,scale,clip)=='unknown'
    assert clipped_text_direction(Image.new('RGB',image.size),bounds,scale,clip)=='unknown'


def test_green_image_rows_do_not_use_text_direction_fallback(monkeypatch):
    from pywinauto.win32structures import RECT
    obj=WeixinBridge.__new__(WeixinBridge)
    item=SimpleNamespace(window_text=lambda:'[图片]',element_info=SimpleNamespace(control_type='ListItem'),
                         descendants=lambda:[],rectangle=lambda:RECT(0,-200,600,300),
                         class_name=lambda:'mmui::ChatImageItemView')
    monkeypatch.setattr(module,'image_direction',lambda *a:'unknown')
    monkeypatch.setattr(module,'clipped_text_direction',lambda *a:pytest.fail('must not classify image pixels as text'))
    assert obj._direction(item,None,{},(Image.new('RGB',(600,400)),(0,0),1))[0]=='unknown'


def test_send_reuses_bound_window_without_search_or_profile_clicks(monkeypatch):
    text = '不实际发送的测试'
    contact = dict(id='bound', lookup='Demo Friend', wechat_id='wx_demo_friend',
                   nickname='Demo Friend', account_id='self')
    state = dict(draft='', clipboard='', sent=False)
    actions = []
    edit = SimpleNamespace(window_text=lambda: state['draft'], exists=lambda **kw: True,
                           click_input=lambda: actions.append('focus-edit'))
    cancel = SimpleNamespace(exists=lambda **kw: False)
    def click_send():
        actions.append('click-send')
        state.update(draft='', sent=True)
    button = SimpleNamespace(exists=lambda **kw: True, is_enabled=lambda: True, click_input=click_send)
    window = SimpleNamespace(handle=123, exists=lambda **kw: True, class_name=lambda: 'mmui::ChatSingleWindow',
                             set_focus=lambda: actions.append('focus-window'),
                             child_window=lambda **kw: (button if kw.get('class_name') == 'mmui::XOutlineButton'
                                                       else cancel if 'title_re' in kw else edit))
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.windows = {'bound': window}
    obj.Edits = SimpleNamespace(CurrentChatEdit={'auto_id':'chat_input_field'})
    obj._check_account = lambda c: actions.append('read-account')
    obj._title = lambda w: 'Demo Friend'
    def visible_count(w, c, t):
        assert 'focus-window' in actions, 'Restore/focus must precede reading the send baseline'
        return 0
    obj._visible_text_count = visible_count
    obj._focus = lambda w,c: actions.append('focus-window')
    obj._click = lambda ctrl,c,action: ctrl.click_input()
    def unexpected(*args, **kwargs):
        raise AssertionError('A bound send must not search, inspect or attach')
    obj.inspect = obj.attach = obj._search_unique = unexpected
    obj.snapshot = lambda c: [dict(key='new', text=text, direction='outgoing')] if state['sent'] else []
    def hotkey(*keys, **kwargs):
        actions.append(keys)
        if keys == ('ctrl','v'):
            state['draft'] = state['clipboard']
        else:
            raise AssertionError('Only pasting is needed; sending clicks the button')
    from pyweixin import SystemSettings
    monkeypatch.setattr(SystemSettings, 'copy_text_to_clipboard', lambda value: state.update(clipboard=value))
    monkeypatch.setattr(pyautogui, 'hotkey', hotkey)
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 123)
    monkeypatch.setattr(win32gui, 'IsWindow', lambda h:True)
    monkeypatch.setattr(win32gui, 'GetAncestor', lambda h, flag: h)
    monkeypatch.setattr(module.time, 'sleep', lambda duration: None)
    assert obj.send(contact, text) == 'new'
    assert actions.count('click-send') == 1
    assert actions.count(('ctrl','v')) == 1
    assert actions.count('read-account') == 1
    assert actions == ['read-account', 'focus-window', 'focus-edit', ('ctrl','v'), 'click-send']


def test_send_automatically_connects_missing_window_before_any_input(monkeypatch):
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.windows = {}
    calls = []
    def attach(contact, for_send=False):
        calls.append(for_send)
        raise module.BridgeError('identity mismatch')
    obj.attach = attach
    monkeypatch.setattr(pyautogui,'hotkey',lambda *args,**kw:(_ for _ in ()).throw(AssertionError('No input before connection')))
    with pytest.raises(module.BridgeError, match='identity mismatch'):
        obj.send(dict(id='missing'), 'do not send')
    assert calls == [True]


@pytest.mark.parametrize('for_send', [False, True])
@pytest.mark.parametrize('already_bound', [False, True])
def test_only_send_attachment_restores_minimized_chat_before_snapshot(monkeypatch, for_send, already_bound):
    import win32con
    contact = dict(id='bound', lookup='Demo Friend', wechat_id='friend', account_id='self')
    state, actions = dict(minimized=True), []
    window = SimpleNamespace(handle=123, exists=lambda **kw: True,
                             class_name=lambda: 'mmui::ChatSingleWindow')
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.windows = {'bound':window} if already_bound else {}
    obj._check_account = lambda c: None
    obj._ensure_account = lambda account: account
    obj._title = lambda w: contact['lookup']
    obj._find_dialog = lambda c: window
    def forbidden(*args, **kwargs):
        raise AssertionError('Existing minimized chat must not be searched or inspected again')
    obj._search_unique = obj._open_dialog = obj._read_profile = forbidden
    def restore(handle, command):
        assert handle == 123 and command == win32con.SW_RESTORE
        actions.append('restore')
        state['minimized'] = False
    monkeypatch.setattr(win32gui, 'IsIconic', lambda h: state['minimized'])
    monkeypatch.setattr(win32gui, 'IsWindow', lambda h:True)
    monkeypatch.setattr(win32gui, 'ShowWindow', restore)
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 123)
    monkeypatch.setattr(win32gui, 'GetAncestor', lambda h, flag: h)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    def snapshot(c):
        if state['minimized']:
            raise module.BridgeError('独立聊天窗口已最小化')
        actions.append('snapshot')
        return []
    obj.snapshot = snapshot
    if for_send:
        assert obj.attach(contact, for_send=True) == []
        obj._focus(window, contact)
        assert actions == ['restore', 'snapshot']
    else:
        with pytest.raises(module.BridgeError, match='最小化'):
            obj.attach(contact)
        assert not actions and state['minimized']


def test_attach_reuses_verified_open_dialog_without_search_or_selection():
    contact = dict(id='bound', lookup='Demo Friend', wechat_id='wx_demo_friend', account_id='self')
    window = SimpleNamespace(handle=123)
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.windows = {}
    obj.profiles = {('self', 'wx_demo_friend', 'Demo Friend'): {}}
    obj.verified_contacts = set(obj.profiles)
    obj.account_hint = 'hint'
    obj.Tools = SimpleNamespace(get_current_wxid=lambda: 'hint')
    obj._check_account = lambda c: None
    obj._ensure_account = lambda account: account
    obj._find_dialog = lambda c: window
    obj._title = lambda w: 'Demo Friend'
    obj.snapshot = lambda c: []
    def unexpected(*args, **kwargs):
        raise AssertionError('Reusing a verified open dialog must perform no GUI action')
    obj.inspect = obj._open_dialog = obj._search_unique = unexpected
    assert obj.attach(contact) == []
    assert obj.windows['bound'] is window


def test_reconnect_opens_dialog_without_reopening_contact_profile():
    contact = dict(id='bound', lookup='Demo Friend', wechat_id='wx_demo_friend', account_id='self')
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.windows = {}
    obj._ensure_account = lambda account: account
    obj._check_account = lambda c: None
    obj._find_dialog = lambda c: None
    actions = []
    dialog = SimpleNamespace(handle=123)
    obj._open_dialog = lambda c: (actions.append('open-independent') or dialog)
    obj._title = lambda w: contact['lookup']
    obj.snapshot = lambda c: []
    def unexpected(*args, **kwargs):
        raise AssertionError('Reconnection must not inspect contact information')
    obj.inspect = obj._read_profile = unexpected
    assert obj.attach(contact) == []
    assert actions == ['open-independent']
    assert obj.windows['bound'] is dialog


def test_verified_account_cache_is_reused_only_in_same_weixin_session(tmp_path):
    import json
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.account_path = tmp_path/'account.json'
    context = dict(pid=123, main_handle=456, started_at=100, hint='hint')
    obj._account_context = lambda: context
    obj.account_id = obj.account_hint = obj.account_name = None
    obj.account_path.write_text(json.dumps(dict(context=context, account_id='self', account_name='我')),
                                encoding='utf-8')
    obj._load_account()
    assert obj.account_id == 'self' and obj.account_hint == 'hint'
    obj.Tools = SimpleNamespace(get_current_wxid=lambda: 'hint')
    obj.Contacts = SimpleNamespace(check_my_info=lambda **kw: (_ for _ in ()).throw(
        AssertionError('A verified account must not reopen its avatar')))
    assert obj._ensure_account('self') == 'self'
    with pytest.raises(module.BridgeError, match='账号'):
        obj._ensure_account('another-account')
    for change in (dict(pid=999), dict(main_handle=999), dict(started_at=999), dict(hint='different')):
        obj.account_id = obj.account_hint = obj.account_name = None
        obj._account_context = lambda: {**context, **change}
        obj._load_account()
        assert obj.account_id is None and obj.account_hint is None


def test_tray_hidden_main_is_rediscovered_after_restart_without_trusting_old_cache(tmp_path, monkeypatch):
    import json
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.account_path = tmp_path/'account.json'
    obj.account_path.write_text(json.dumps(dict(context=dict(main_handle=123, pid=456, started_at=100))))
    current=[dict(handle=789,pid=987,started_at=200,kind='mmui::MainWindow')]
    monkeypatch.setattr('wechat_panel.wechat_windows.discover_weixin_windows',lambda:current)
    window = SimpleNamespace(handle=789)
    def wrap(handle):
        assert handle==789, 'Never use the old HWND from an earlier process'
        return window
    obj.desktop = SimpleNamespace(windows=lambda **kw:[], window=wrap)
    assert obj._main_window() is window
    current.clear()
    with pytest.raises(module.ConnectionLost, match='自动重试'):
        obj._main_window()


def test_multiple_real_mains_are_distinguished_from_missing_main(monkeypatch):
    obj=WeixinBridge.__new__(WeixinBridge)
    windows=[dict(handle=h,pid=h,started_at=1,kind='mmui::MainWindow') for h in (1,2)]
    monkeypatch.setattr('wechat_panel.wechat_windows.discover_weixin_windows',lambda:windows)
    with pytest.raises(module.BridgeError,match='2 个微信主窗口') as exc:obj._main_window()
    assert not module.connection_error(exc.value)


def test_hidden_login_page_is_reported_as_logged_out_and_clears_bindings(monkeypatch):
    obj=WeixinBridge.__new__(WeixinBridge)
    obj.account_id=obj.account_hint=obj.account_name='old'
    obj.windows,obj.profiles,obj.verified_contacts={'x':1},{'x':1},{'x'}
    obj.Tools=SimpleNamespace(is_weixin_running=lambda:True)
    obj._input_desktop=lambda:True
    monkeypatch.setattr('wechat_panel.wechat_windows.discover_weixin_windows',lambda:[
        dict(handle=1,pid=1,started_at=1,kind='mmui::LoginWindow')])
    assert obj.health()['state']=='offline'
    assert obj.account_id is None and not obj.windows and not obj.profiles and not obj.verified_contacts


@pytest.mark.parametrize('same_process,account_changed', [(True,False),(False,False),(True,True)])
def test_verified_independent_chat_remains_available_with_main_in_tray(monkeypatch, same_process, account_changed):
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.account_id, obj.account_hint = 'self', 'hint'
    obj.windows, obj.profiles, obj.verified_contacts = {}, {}, set()
    obj.Tools = SimpleNamespace(is_weixin_running=lambda:True,
        get_current_wxid=lambda:'changed' if account_changed else 'hint', get_weixin_version=lambda:'test')
    obj._input_desktop = lambda: True
    obj._main_window = lambda: SimpleNamespace(handle=123, process_id=lambda:456)
    obj.desktop = SimpleNamespace(windows=lambda **kw:[SimpleNamespace(process_id=lambda:456 if same_process else 789)])
    monkeypatch.setattr(win32gui, 'IsWindowVisible', lambda h:False)
    monkeypatch.setattr(win32gui, 'IsIconic', lambda h:False)
    state = obj.health()['state']
    assert state == ('account_changed' if account_changed else 'ready' if same_process else 'hidden')


def test_reconnect_does_not_restore_main_when_independent_connection_is_ready(monkeypatch):
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.health = lambda:dict(state='ready', account_id='self')
    obj._main_window = lambda: (_ for _ in ()).throw(AssertionError('No main-window operation needed'))
    monkeypatch.setattr(win32gui, 'ShowWindow', lambda *args: (_ for _ in ()).throw(AssertionError('Do not show main')))
    assert obj.reveal()['state'] == 'ready'


def test_logged_in_subscription_page_is_ready_even_without_chat_input(monkeypatch):
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.account_id, obj.account_hint = 'self', 'hint'
    obj.Tools = SimpleNamespace(is_weixin_running=lambda:True, get_current_wxid=lambda:'hint',
                               get_weixin_version=lambda:'test')
    obj.Main = SimpleNamespace(Search={'class_name':'SearchEdit'})
    obj.Edits = SimpleNamespace(CurrentChatEdit={'auto_id':'chat_input_field'})
    obj.SideBar = SimpleNamespace(Weixin={'title':'微信'})
    window = SimpleNamespace(handle=123, exists=lambda **kw:True, class_name=lambda:'mmui::MainWindow',
        descendants=lambda **kw:[], child_window=lambda **kw:SimpleNamespace(
            exists=lambda **opts:kw.get('title')=='微信'))
    obj._main_window = lambda:window
    obj._input_desktop = lambda:True
    monkeypatch.setattr(win32gui,'IsWindowVisible',lambda h:True)
    monkeypatch.setattr(win32gui,'IsIconic',lambda h:False)
    assert obj.health()['state'] == 'ready'


@pytest.mark.parametrize('page', ['subscription', 'contacts', 'chats'])
def test_search_returns_from_logged_in_pages_only_when_needed(page):
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.Main = SimpleNamespace(Search={})
    obj.SideBar = SimpleNamespace(Weixin={'title':'微信'})
    actions, state = [], {'search':page=='chats'}
    edit = object()
    back = SimpleNamespace(exists=lambda **kw:page=='subscription', is_visible=lambda:True)
    navigation = SimpleNamespace(exists=lambda **kw:True)
    main = SimpleNamespace(descendants=lambda **kw:[edit] if state['search'] else [],
        child_window=lambda **kw:back if 'title_re' in kw else navigation)
    def click(control,contact,action):
        actions.append(action)
        state['search'] = True
    obj._click = click
    assert obj._search_edit(main) is edit
    assert actions == ([] if page=='chats' else ['ui.sessions.back'] if page=='subscription'
                       else ['ui.navigation.chats'])


@pytest.mark.parametrize('iconic', [False, True])
def test_hidden_main_uses_real_tray_activation_before_focus(monkeypatch, iconic):
    obj = WeixinBridge.__new__(WeixinBridge)
    actions, state = [], dict(visible=False, iconic=iconic)
    window = SimpleNamespace(handle=123, descendants=lambda **kw:[object()])
    obj._main_window = lambda:window
    obj.Main = SimpleNamespace(Search={})
    obj._wake_from_tray = lambda w:(actions.append('tray'),state.update(visible=True,iconic=False))
    obj._focus = lambda *args:actions.append('focus')
    monkeypatch.setattr(win32gui, 'IsWindowVisible', lambda handle:state['visible'])
    monkeypatch.setattr(win32gui, 'IsIconic', lambda handle:state['iconic'])
    monkeypatch.setattr(win32gui, 'ShowWindow', lambda *args: (_ for _ in ()).throw(
        AssertionError('Do not expose a tray-hidden Qt HWND with ShowWindow')))
    assert obj._open_main() is window
    assert actions == ['tray','focus']


def test_visible_minimized_main_restores_once_without_tray_click(monkeypatch):
    import win32con
    obj = WeixinBridge.__new__(WeixinBridge)
    actions, state = [], dict(iconic=True)
    window = SimpleNamespace(handle=123, descendants=lambda **kw:[object()])
    obj._main_window = lambda:window
    obj.Main = SimpleNamespace(Search={})
    obj._wake_from_tray = lambda *args: (_ for _ in ()).throw(AssertionError('No tray click needed'))
    obj._focus = lambda *args:actions.append('focus')
    monkeypatch.setattr(win32gui, 'IsWindowVisible', lambda handle:True)
    monkeypatch.setattr(win32gui, 'IsIconic', lambda handle:state['iconic'])
    monkeypatch.setattr(win32gui, 'ShowWindow', lambda h,command:(
        actions.append(command),state.update(iconic=False)))
    assert obj._open_main() is window
    assert actions == [win32con.SW_RESTORE,'focus']


@pytest.mark.parametrize('hidden_icon,ambiguous', [(False,False),(True,False),(False,True)])
def test_tray_activation_locates_exact_icon_and_handles_overflow(monkeypatch, hidden_icon, ambiguous):
    obj = WeixinBridge.__new__(WeixinBridge)
    actions, state = [], dict(overflow=False)
    def no_coordinate_click(*args, **kwargs):
        raise AssertionError('Explorer can report bounds spanning Wi-Fi; do not read or click coordinates')
    def button(name, action):
        return SimpleNamespace(window_text=lambda:name, is_visible=lambda:True,
            rectangle=no_coordinate_click, double_click_input=no_coordinate_click,
            click_input=no_coordinate_click, invoke=action)
    icon = button(' 微信', lambda:actions.append('invoke'))
    chevron = button('显示隐藏的图标', lambda:(actions.append('overflow'),state.update(overflow=True)))
    network = button('网络 DemoWiFi\nInternet 访问', no_coordinate_click)
    volume = button('音量 扬声器', no_coordinate_click)
    tray = SimpleNamespace(descendants=lambda **kw:([chevron,network,volume] if hidden_icon
        else [icon,icon,network,volume] if ambiguous else [icon,network,volume]))
    overflow = SimpleNamespace(descendants=lambda **kw:[icon])
    obj.desktop = SimpleNamespace(windows=lambda **kw:([tray] if kw.get('class_name')=='Shell_TrayWnd'
        else [overflow] if state['overflow'] else []))
    obj._click = no_coordinate_click
    monkeypatch.setattr(win32gui, 'WindowFromPoint', no_coordinate_click)
    if ambiguous:
        with pytest.raises(module.BridgeError,match='无法唯一识别'):
            obj._wake_from_tray(SimpleNamespace(handle=123))
        assert actions == []
    else:
        obj._wake_from_tray(SimpleNamespace(handle=123))
        assert actions == (['overflow','invoke'] if hidden_icon else ['invoke'])


def test_tray_invoke_failure_is_recoverable_without_mouse_fallback(monkeypatch):
    from pywinauto.uia_defines import NoPatternInterfaceError
    obj = WeixinBridge.__new__(WeixinBridge)
    def unavailable():
        raise NoPatternInterfaceError()
    icon = SimpleNamespace(window_text=lambda:'微信', is_visible=lambda:True, invoke=unavailable)
    tray = SimpleNamespace(descendants=lambda **kw:[icon])
    obj.desktop = SimpleNamespace(windows=lambda **kw:[tray] if kw.get('class_name')=='Shell_TrayWnd' else [])
    monkeypatch.setattr(pyautogui, 'click', lambda *a,**k:pytest.fail('No guessed mouse fallback'))
    with pytest.raises(module.ConnectionLost, match='自动重试') as error:
        obj._wake_from_tray(SimpleNamespace(handle=123))
    assert module.connection_error(error.value)


@pytest.mark.parametrize('hidden_node_exists', [False, True])
def test_narrow_window_connects_with_back_and_one_double_click(monkeypatch, hidden_node_exists):
    actions, state = [], dict(sessions=False)
    rect = SimpleNamespace(left=20, width=lambda: 200, mid_point=lambda: SimpleNamespace(y=100))
    row = SimpleNamespace(window_text=lambda: 'Demo Friend\n你好\n14:00', rectangle=lambda: rect)
    listing = SimpleNamespace(exists=lambda **kw: hidden_node_exists or state['sessions'], children=lambda **kw: [row])
    listing.is_visible = lambda: state['sessions']
    listing.wait = lambda *args, **kw: listing
    back = SimpleNamespace(exists=lambda **kw: True, wait=lambda *args, **kw: None)
    main = SimpleNamespace(handle=99, child_window=lambda **kw: back if 'title_re' in kw else listing)
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.Main = SimpleNamespace(SessionList={'title': '会话'})
    obj._main_window = lambda: main
    obj._focus = lambda *args: None
    obj._title = lambda w: 'Demo Friend'
    def click(*args):
        actions.append('back')
        state['sessions'] = True
    obj._click = click
    obj._search_unique = lambda *args: (actions.append('search') or main)
    dialog = SimpleNamespace(handle=123)
    obj._find_dialog = lambda c: dialog
    monkeypatch.setattr(pyautogui, 'doubleClick', lambda *args, **kw: actions.append('double-click'))
    monkeypatch.setattr(win32gui, 'WindowFromPoint', lambda point: 99)
    monkeypatch.setattr(win32gui, 'GetAncestor', lambda h, flag: h)
    monkeypatch.setattr(module.time, 'sleep', lambda duration: None)
    assert obj._open_dialog(dict(id='bound', lookup='Demo Friend')) is dialog
    assert actions == ['search', 'back', 'double-click']


def test_contact_verification_opens_independent_window_before_reading_profile():
    obj = WeixinBridge.__new__(WeixinBridge)
    obj.account_hint, obj.account_id = 'hint', 'self'
    obj.Tools = SimpleNamespace(get_current_wxid=lambda: 'hint')
    obj.profiles, obj.verified_contacts = {}, set()
    obj._find_dialog = lambda c: None
    actions = []
    dialog = SimpleNamespace(handle=123)
    obj._open_dialog = lambda c: (actions.append('open-independent') or dialog)
    def read_profile(window, lookup):
        assert window is dialog and actions == ['open-independent']
        actions.append('verify-independent')
        return dict(lookup=lookup, nickname=lookup, wechat_id='wx-friend', account_id='self')
    obj._read_profile = read_profile
    assert obj.inspect('Demo Friend', 'wx-friend', 'self')['wechat_id'] == 'wx-friend'
    assert actions == ['open-independent', 'verify-independent']
    assert ('self', 'wx-friend', 'Demo Friend') in obj.verified_contacts


class StaleUIAError(Exception):
    hresult = -2147220991


def test_destroyed_hwnd_is_detected_without_resolving_stale_uia(monkeypatch):
    class DeadWindow:
        criteria = [{'handle':777}]
        def __init__(self):
            self.criteria = [{'handle':777}]
        @property
        def handle(self):
            raise AssertionError('Resolving stale UIA reproduces COMError')
        def exists(self,**kw):
            raise AssertionError('Never query UIA on a destroyed HWND')
    obj=WeixinBridge.__new__(WeixinBridge)
    monkeypatch.setattr(win32gui,'IsWindow',lambda h:False)
    assert not obj._window_alive(DeadWindow())


@pytest.mark.parametrize('failure', ['closed','preflight','after-paste','wrong-contact'])
def test_send_reconnects_only_before_input_and_never_retries_uncertain_send(monkeypatch,failure):
    from pyweixin import SystemSettings
    contact=dict(id='c',lookup='friend',account_id='self')
    state=dict(alive=failure!='closed',draft='',clipboard='',title_calls=0,sent=False)
    actions=[]
    edit=SimpleNamespace(exists=lambda **kw:True,window_text=lambda:state['draft'])
    cancel=SimpleNamespace(exists=lambda **kw:False)
    button=SimpleNamespace(exists=lambda **kw:True,is_enabled=lambda:True)
    window=SimpleNamespace(handle=123,exists=lambda **kw:True,class_name=lambda:'mmui::ChatSingleWindow',
        child_window=lambda **kw:button if kw.get('class_name')=='mmui::XOutlineButton' else cancel if 'title_re' in kw else edit)
    obj=WeixinBridge.__new__(WeixinBridge)
    obj.windows={'c':window}
    obj.Edits=SimpleNamespace(CurrentChatEdit={})
    obj._check_account=lambda c:None
    obj._focus=lambda *args:None
    obj._visible_text_count=lambda *args:0
    def title(w):
        state['title_calls']+=1
        if failure=='preflight' and state['title_calls']==1:raise StaleUIAError()
        return 'someone else' if failure=='wrong-contact' else 'friend'
    obj._title=title
    obj.invalidate=lambda cid:obj.windows.pop(cid,None)
    def attach(c,for_send=False):
        assert for_send
        actions.append('attach')
        state['alive']=True
        obj.windows['c']=window
        return []
    obj.attach=attach
    def click(control,c,action):
        if control is button:
            actions.append('send-once')
            state.update(sent=True,draft='')
    obj._click=click
    obj.snapshot=lambda c:[dict(key='new',text='test',direction='outgoing')] if state['sent'] else []
    def paste(*args,**kwargs):
        actions.append('paste-once')
        state['draft']=state['clipboard']
        if failure=='after-paste':raise StaleUIAError()
    monkeypatch.setattr(SystemSettings,'copy_text_to_clipboard',lambda text:state.update(clipboard=text))
    monkeypatch.setattr(pyautogui,'hotkey',paste)
    monkeypatch.setattr(win32gui,'IsWindow',lambda h:state['alive'])
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:123)
    monkeypatch.setattr(win32gui,'GetAncestor',lambda h,flag:h)
    monkeypatch.setattr(module.time,'sleep',lambda *args:None)
    if failure=='after-paste':
        with pytest.raises(module.SendUncertain):obj.send(contact,'test')
        assert actions==['paste-once']
    elif failure=='wrong-contact':
        with pytest.raises(module.BridgeError,match='发送目标窗口不匹配'):obj.send(contact,'test')
        assert actions==[]
    else:
        assert obj.send(contact,'test')=='new'
        assert actions==['attach','paste-once','send-once']
