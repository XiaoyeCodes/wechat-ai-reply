"""Adapters run exclusively in the serial Windows worker process."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
import uuid
import base64
from io import BytesIO

from .avatars import crop_avatar, row_avatar
from .window_host import WindowHost


class BridgeError(RuntimeError):
    pass


class WindowTransition(BridgeError):
    """A minimize transition can be retried on the next passive poll."""


class DesktopBusy(BridgeError):
    """No paste/send has begun; the serial queue can safely wait and retry."""


class ConnectionLost(BridgeError):
    """The chat can be rebound without changing its verified identity."""


def connection_error(exc):
    if isinstance(exc, (ConnectionLost, WindowTransition)):
        return True
    if (type(exc).__module__.startswith('pywinauto.') and type(exc).__name__ in
            {'ElementNotFoundError', 'InvalidWindowHandle', 'InvalidElement', 'TimeoutError'}):
        return True
    code = getattr(exc, 'hresult', None)
    if code is None and exc.args and isinstance(exc.args[0], int):
        code = exc.args[0]
    # UIA element unavailable, RPC disconnected/server unavailable, UIA timeout,
    # and a native window destroyed between IsWindow and the next read.
    return code is not None and (code & 0xffffffff) in {
        0x80040201, 0x80010108, 0x800706BA, 0x80131505, 1400}


def physical_pixel_coordinates():
    """UIA, native capture and pointer operations must use the same pixels.

    PyAutoGUI sets process-wide system DPI awareness on import. A long-lived
    worker then uses stale scaling after display settings/monitors change.
    Explicit per-monitor thread awareness also overrides that import setting.
    """
    import ctypes
    user32=ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.argtypes=[ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype=ctypes.c_void_p
    if not (user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            or user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-3))):
        raise BridgeError('无法统一微信窗口的显示缩放，请重启工作台后重试')


class SendUncertain(BridgeError):
    """A send may already have occurred. Never retry automatically."""


def validate_identity(profile, lookup, expected_id, account_id=None):
    if not expected_id or profile.get('wechat_id') != expected_id:
        raise BridgeError('联系人微信号与填写的信息不一致，已停止操作')
    if profile.get('lookup') != lookup:
        raise BridgeError('联系人备注不一致，已停止操作')
    if not profile.get('account_id') or (account_id and profile['account_id'] != account_id):
        raise BridgeError('当前登录的微信账号与联系人绑定的账号不一致')
    return profile


def parse_sender(text, friend_names, own_name):
    """Parse the sender prefix exposed by Weixin's multiselect CheckBoxes."""
    matches = set()
    for name, direction in [(n, 'incoming') for n in friend_names] + [(own_name, 'outgoing')]:
        if name and text.startswith(name) and len(text) > len(name) and text[len(name)].isspace():
            matches.add((direction, text[len(name)+1:]))
    if len(matches) == 1:
        return matches.pop()
    return 'unknown', text


def image_direction(image, bounds, scale=1, visible_bounds=None):
    """Read avatar placement in a normal, light-theme chat without UI actions.

    Bounds are relative to the captured native window. Only a visible avatar
    on one side AND clear background on the other are sufficient evidence.
    Clipped avatars, blank captures, dark themes and ambiguous layouts stay unknown.
    """
    from PIL import ImageStat
    left, top, right, bottom = bounds
    width = round(36*scale)
    y = top + round(8*scale)
    end = min(bottom-round(8*scale), y+width)
    clip=visible_bounds or (0,0,image.width,image.height)
    # A long bubble can extend below the viewport while its avatar is fully
    # visible. Check the sampled avatar bounds, not the entire message height.
    if (left < 0 or top < 0 or right > image.width or end > image.height
            or y < clip[1] or end > clip[3] or end-y < width or right-left < 4*width):
        return 'unknown'
    sides = []
    for x in (left+round(20*scale), right-round(22*scale)-width):
        if x < clip[0] or x+width > clip[2]:return 'unknown'
        crop = image.crop((x, y, x+width, end)).convert('RGB')
        pixels = list(getattr(crop, 'get_flattened_data', crop.getdata)())
        nonwhite = sum(max(abs(v-250) for v in p) > 28 for p in pixels)/len(pixels)
        stat = ImageStat.Stat(crop)
        avatar = nonwhite > 0.30 and max(stat.stddev) > 12
        clear = nonwhite < 0.08 and min(stat.mean) > 235
        sides.append((avatar, clear))
    if sides[0][0] and sides[1][1]:
        return 'incoming'
    if sides[1][0] and sides[0][1]:
        return 'outgoing'
    return 'unknown'


def clipped_text_direction(image, bounds, scale=1, visible_bounds=None):
    """Recognize a long outgoing text bubble when its avatar is above the view.

    Only call for native text rows. Pictures/stickers can contain these colors.
    Several visible strips must show the green right bubble and clear left
    margin; white/blank/dark or ambiguous captures remain unknown.
    """
    left,top,right,bottom=bounds
    clip=visible_bounds or (0,0,image.width,image.height)
    start=max(0,top,clip[1])+round(8*scale)
    end=min(image.height,bottom,clip[3])-round(8*scale)
    size=max(2,round(6*scale))
    if (top>=clip[1] or end-start<48*scale or right-left<240*scale
            or left<0 or right>image.width):return 'unknown'
    # Sample several columns, including bubble padding. A fixed single column
    # often lands on dark text in a long reply and misses its green background.
    for offset in (76,82,88,94):
        xs=(left+round(82*scale),right-round(offset*scale))
        if any(x<max(0,clip[0]) or x+size>min(image.width,clip[2]) for x in xs):continue
        matches=0
        for i in range(9):
            y=round(start+(end-start-size)*i/8)
            crops=[image.crop((x,y,x+size,y+size)).convert('RGB') for x in xs]
            patches=[list(getattr(crop,'get_flattened_data',crop.getdata)()) for crop in crops]
            clear=sum(min(p)>235 for p in patches[0])/len(patches[0])>.9
            green=sum(90<=r<=210 and 200<=g<=255 and 90<=b<=210 and g-max(r,b)>40
                      for r,g,b in patches[1])/len(patches[1])>.8
            matches+=clear and green
        if matches>=6:return 'outgoing'
    return 'unknown'


class DemoBridge:
    def __init__(self):
        self.history = {}
        self.counter = 0
        self.session = str(uuid.uuid4())

    def health(self):
        return dict(state='ready', detail='演示环境 · 不连接真实微信', account_id='demo-account', version='DEMO')

    def inspect(self, lookup, expected_id, account_id=None):
        if expected_id == 'mismatch':
            raise BridgeError('联系人微信号与填写的信息不一致，已停止操作')
        return validate_identity(dict(lookup=lookup, nickname=lookup, wechat_id=expected_id,
                                      account_id='demo-account'), lookup, expected_id, account_id)

    def attach(self, contact, for_send=False):
        self.inspect(contact['lookup'], contact['wechat_id'], contact['account_id'])
        return self.history.setdefault(contact['id'], [])

    def snapshot(self, contact):
        return list(self.history.setdefault(contact['id'], []))

    def inject(self, contact, text, direction='incoming'):
        self.counter += 1
        item = dict(key=f'demo:{self.session}:{self.counter}', text=text, kind='text', direction=direction,
                    account_id='demo-account')
        self.history.setdefault(contact['id'], []).append(item)
        return item

    def send(self, contact, text):
        self.inspect(contact['lookup'], contact['wechat_id'], contact['account_id'])
        return self.inject(contact, text, 'outgoing')['key']

    def send_file(self, contact, path):
        key = self.send(contact, path.name)
        self.history[contact['id']][-1]['kind'] = 'file'
        return key

    def send_voice(self, contact, text, before_send=None):
        if before_send:before_send()
        key=self.send(contact,text)
        self.history[contact['id']][-1]['kind']='voice'
        return self.history[contact['id']][-1]

    def send_image(self, contact, data, before_send=None):
        if before_send:before_send()
        key=self.send(contact,'[图片]')
        self.history[contact['id']][-1]['kind']='image'
        return key

    def detach(self, contact_id):
        pass

    def avatars(self, contacts, locate=False):
        return []

    def reveal(self):
        return self.health()


class WeixinBridge:
    def __init__(self):
        if sys.platform != 'win32':
            raise BridgeError('真实微信连接需要 Windows 10/11')
        physical_pixel_coordinates()
        source = Path(__file__).resolve().parents[1] / 'pywechat-main' / 'src'
        sys.path.insert(0, str(source))
        from pyweixin import Contacts, Navigator, Tools
        from pyweixin.Config import GlobalConfig
        from pyweixin.Uielements import Main_window, SideBar, Lists, Texts, Edits, Buttons, Special_Labels
        from pywinauto import Desktop
        self.Contacts, self.Navigator, self.Tools = Contacts, Navigator, Tools
        self.Main, self.SideBar, self.Lists, self.Texts, self.Edits = Main_window, SideBar, Lists, Texts, Edits
        self.Buttons, self.Labels = Buttons, Special_Labels
        # Weixin 4.1.15 exposes an empty accessible name for the search edit.
        # Match its dedicated class instead; upstream helpers share this selector.
        self.Main.Search.pop('title', None)
        # Preserve the user's layout. Binding opens one normal independent chat.
        GlobalConfig.is_maximize = False
        self.desktop = Desktop(backend='uia')
        self.windows = {}
        self.account_id = None
        self.account_hint = None
        self.account_name = None
        self.profiles = {}
        self.verified_contacts = set()
        self.previous_items = {}
        self.defer_for_input = True
        self.image_cache = {}
        self.image_pending = {}
        self.avatar_cache = {}
        self.search_avatar = None
        self.trace_path = Path(os.environ.get('WECHAT_PANEL_DATA_DIR') or Path(__file__).resolve().parents[1] / '.panel-data') / 'ui-actions.jsonl'
        self.account_path = self.trace_path.with_name('wechat-account.json')
        # Native Qt captions become "Weixin" for both main and independent
        # windows. Upstream FindWindow can consequently return the wrong window.
        self.Navigator.open_weixin = staticmethod(self._open_main)
        self._load_account()
        self.window_host = WindowHost(self.trace_path.with_name('background-windows.json'), self._host_matches)

    def _host_matches(self, handle, record):
        window = self.desktop.window(handle=handle)
        return (self.account_id == record['account_id']
                and self.Tools.get_current_wxid() == self.account_hint
                and window.class_name() == 'mmui::ChatSingleWindow'
                and self._title(window) == record['lookup'])

    def suspend(self):
        self.window_host.release_all()

    def close(self):
        self.suspend()

    def listener_state(self, contact):
        window = self.windows.get(contact['id'])
        return 'background' if window and self.window_host.managed(window.handle) else 'listening'

    @staticmethod
    def _native_handle(window):
        # Accessing WindowSpecification.handle itself resolves UIA and raises
        # COMError for a destroyed HWND. Read the original search criteria first.
        criteria = vars(window).get('criteria', [])
        if criteria and criteria[0].get('handle'):
            return criteria[0]['handle']
        return window.handle

    def _window_alive(self, window):
        import win32gui
        if window is None:
            return False
        try:
            if not win32gui.IsWindow(self._native_handle(window)):
                return False
            return window.exists(timeout=0.2)
        except Exception as exc:
            if connection_error(exc):
                return False
            raise

    def invalidate(self, contact_id):
        window = self.windows.pop(contact_id, None)
        if window is not None:
            import win32gui
            handle = self._native_handle(window)
            if not win32gui.IsWindow(handle):
                self.window_host.release(handle)

    def _account_context(self):
        import psutil
        main = self._main_window()
        pid = main.process_id()
        return dict(pid=pid, main_handle=main.handle, started_at=psutil.Process(pid).create_time(),
                    hint=self.Tools.get_current_wxid())

    def _load_account(self):
        # Reuse a verified identity only for the same running Weixin process
        # and account hint. A new login/process requires verification again.
        try:
            cached = json.loads(self.account_path.read_text(encoding='utf-8'))
            if cached['context'] == self._account_context() and cached.get('account_id'):
                self.account_id = cached['account_id']
                self.account_hint = cached['context']['hint']
                self.account_name = cached.get('account_name', '')
        except (OSError, ValueError, KeyError, BridgeError):
            pass

    def _save_account(self):
        context = self._account_context()
        if context['hint'] != self.account_hint:
            raise BridgeError('核验期间微信账号发生变化，请重新连接')
        self.account_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.account_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(context=context, account_id=self.account_id,
                                             account_name=self.account_name), ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.account_path)

    def _ensure_account(self, expected_account=None):
        hint = self.Tools.get_current_wxid()
        if self.account_hint != hint or not self.account_id:
            self._record('ui.own_profile')
            mine = self.Contacts.check_my_info(close_weixin=False)
            self.account_id = mine.get('微信号', '').strip()
            self.account_name = mine.get('昵称', '').strip()
            self.account_hint = hint
            self.profiles.clear()
            self.verified_contacts.clear()
            if self.account_id in ('', '无'):
                raise BridgeError('无法读取当前微信账号身份')
            self._save_account()
        if expected_account and self.account_id != expected_account:
            raise BridgeError('当前登录的微信账号与联系人绑定的账号不一致')
        return self.account_id

    def _main_window(self):
        from .wechat_windows import discover_weixin_windows
        windows=discover_weixin_windows()
        self._login_windows=[w for w in windows if w['kind']=='mmui::LoginWindow']
        candidates=[w for w in windows if w['kind']=='mmui::MainWindow']
        signature=tuple((w['handle'],w['pid'],w['started_at'],w['kind']) for w in windows)
        if signature!=getattr(self,'_discovery_signature',None):
            self._record('connection.windows',windows=windows)
            self._discovery_signature=signature
        if not candidates:
            raise ConnectionLost('暂未发现微信主窗口，正在自动重试')
        if len(candidates)>1:
            raise BridgeError(f'检测到 {len(candidates)} 个微信主窗口，为避免串号已停止自动选择；请保留一个登录窗口后重试')
        return self.desktop.window(handle=candidates[0]['handle'])

    def _open_main(self, is_maximize=None, window_size=None):
        import win32gui
        import win32con
        window = self._main_window()
        if not win32gui.IsWindowVisible(window.handle):
            self._wake_from_tray(window)
        elif win32gui.IsIconic(window.handle):
            self._record('ui.restore_main')
            win32gui.ShowWindow(window.handle, win32con.SW_RESTORE)
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            if (win32gui.IsWindowVisible(window.handle) and not win32gui.IsIconic(window.handle)
                    and self._main_controls_ready(window)):
                break
            time.sleep(0.1)
        else:
            raise ConnectionLost('微信主窗口尚未恢复到可操作状态，正在自动重试')
        self._focus(window, None)
        return window

    def _main_controls_ready(self, window):
        # A logged-in main window can be on a subscription, contacts or other
        # page without a chat input/search. The navigation bar is sufficient
        # to return to chats; absence of an input does not mean login failed.
        return bool(window.descendants(**self.Main.Search)
                    or window.child_window(**self.Edits.CurrentChatEdit).exists(timeout=0.1)
                    or window.child_window(**self.SideBar.Weixin).exists(timeout=0.1))

    def _wake_from_tray(self, window):
        """Invoke the exact notification button without relying on its bounds.

        Explorer can report a Weixin button spanning the remaining taskbar,
        placing its midpoint over the network/volume controls. UIA Invoke
        targets the button itself and does not move or click the mouse.
        """
        names = {'微信', 'Weixin', 'WeChat'}

        def invoke(control, action):
            self._record(action, handle=self._native_handle(window), method='uia.invoke')
            try:
                control.invoke()
            except Exception as exc:
                raise ConnectionLost('微信托盘按钮暂时无法激活，正在自动重试') from exc

        def buttons(root):
            return [c for c in root.descendants(control_type='Button') if c.is_visible()]

        def icons(roots):
            return [c for root in roots for c in buttons(root) if c.window_text().strip() in names]

        def overflow_windows():
            return self.desktop.windows(class_name_re='^(NotifyIconOverflowWindow|TopLevelWindowForOverflow.*)$')

        roots = self.desktop.windows(class_name='Shell_TrayWnd')
        overflow = overflow_windows()
        candidates = icons(roots+overflow)
        if not candidates:
            # Notification icons can be in the Windows overflow area. Open it
            # once, then locate the exact Weixin name; never click a guessed icon.
            chevrons = [c for root in roots for c in buttons(root) if c.window_text().strip() in
                        {'显示隐藏的图标', '顯示隱藏的圖示', 'Show hidden icons'}]
            if len(chevrons) == 1:
                invoke(chevrons[0], 'ui.tray_overflow')
                deadline = time.monotonic()+2
                while time.monotonic() < deadline:
                    candidates = icons(overflow_windows())
                    if candidates:
                        break
                    time.sleep(0.1)
        if len(candidates) != 1:
            raise BridgeError('无法唯一识别微信托盘图标，请检查微信登录状态后重试')
        invoke(candidates[0], 'ui.restore_from_tray')

    def _record(self, action, contact=None, **details):
        path = getattr(self, 'trace_path', None)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), action=action,
                                         contact_id=contact and contact.get('id'), **details), ensure_ascii=False)+'\n')

    def _focus(self, window, contact):
        import win32gui
        import win32con
        if hasattr(self, 'window_host'):
            self.window_host.reveal(window.handle)
        if win32gui.IsIconic(window.handle):
            self._record('ui.restore', contact)
            win32gui.ShowWindow(window.handle, win32con.SW_RESTORE)
            deadline = time.monotonic()+2
            while win32gui.IsIconic(window.handle) and time.monotonic() < deadline:
                time.sleep(0.05)
            if win32gui.IsIconic(window.handle):
                raise BridgeError('聊天窗口未能自动还原，请检查微信窗口后重试发送')
            time.sleep(0.2)  # Let Qt lay out the input and message list again.
        if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != window.handle:
            self._record('ui.focus', contact)
            try:
                win32gui.SetForegroundWindow(window.handle)
            except Exception:
                # Windows restricts focus changes from a newly started worker.
                # Temporarily join its foreground input queue, without keys or
                # mouse clicks, then immediately release the association.
                import ctypes
                import win32api
                import win32process
                current = win32api.GetCurrentThreadId()
                foreground, _ = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
                target, _ = win32process.GetWindowThreadProcessId(window.handle)
                linked = [thread for thread in {foreground, target} if thread != current
                          and ctypes.windll.user32.AttachThreadInput(current, thread, True)]
                try:
                    win32gui.BringWindowToTop(window.handle)
                    win32gui.SetForegroundWindow(window.handle)
                finally:
                    for thread in linked:
                        ctypes.windll.user32.AttachThreadInput(current, thread, False)
            time.sleep(0.1)
        if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != window.handle:
            raise DesktopBusy('目标聊天窗口未获得前台焦点，等待空闲后重试')

    def _click(self, control, contact, action):
        import pyautogui
        import win32gui
        root = control.top_level_parent().handle
        # Restoring an offscreen Qt window can leave UIA bounds stale briefly.
        # Re-read the actual control bounds before every hit test. Never click
        # through an overlay, and never try to dismiss it with guessed clicks.
        hit = None
        for attempt in range(11):
            rect = control.rectangle()
            if rect.width() <= 0 or rect.height() <= 0:
                raise BridgeError('目标控件不可见，已停止操作')
            point = (rect.mid_point().x, rect.mid_point().y)
            hit = win32gui.GetAncestor(win32gui.WindowFromPoint(point), 2)
            if hit == root:
                if action in ('ui.input.focus','ui.send_once') and win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)!=root:
                    raise DesktopBusy('用户正在切换窗口，等待空闲后重试')
                self._record(action, contact)
                pyautogui.click(*point, _pause=False)
                return
            if attempt < 10:
                time.sleep(0.1)
        self._record('ui.click_blocked', contact, target=root, covering=hit, requested=action)
        raise DesktopBusy('目标控件被其他窗口遮挡，等待空闲后重试')

    def _visible_text_count(self, window, contact, text):
        from .wechat_text import receipt_text
        expected = receipt_text(text)
        count = 0
        for row in window.child_window(**self.Lists.FriendChatList).children():
            if row.class_name() != 'mmui::ChatTextItemView':
                continue
            _, body = self._direction(row, window, contact)
            count += receipt_text(body) == expected
        return count

    def _input_desktop(self):
        import ctypes
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        desktop = user32.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            return False
        try:
            name = ctypes.create_unicode_buffer(256)
            size = ctypes.c_ulong()
            user32.GetUserObjectInformationW(ctypes.c_void_p(desktop), 2, name,
                                            ctypes.sizeof(name), ctypes.byref(size))
            return name.value.lower() == 'default'
        finally:
            user32.CloseDesktop(desktop)

    def health(self):
        import win32gui
        if not self.Tools.is_weixin_running():
            return dict(state='offline', detail='微信未启动，请先启动并登录微信', account_id=None)
        if not self._input_desktop():
            return dict(state='locked', detail='桌面不可操作，请解锁 Windows 后继续', account_id=self.account_id)
        try:
            main = self._main_window()
        except BridgeError as exc:
            if getattr(self,'_login_windows',[]) or self.desktop.windows(class_name='mmui::LoginWindow', visible_only=False):
                self.windows.clear()
                self.profiles.clear()
                self.verified_contacts.clear()
                self.account_id = self.account_hint = self.account_name = None
                return dict(state='offline', detail='微信当前处于登录页，请先登录原账号后恢复运行', account_id=None)
            return dict(state='unavailable', detail=str(exc), account_id=self.account_id)
        hint = self.Tools.get_current_wxid()
        if self.account_hint and hint != self.account_hint:
            self.windows.clear()
            self.profiles.clear()
            self.verified_contacts.clear()
            self.account_id = None
            return dict(state='account_changed', detail='检测到微信账号变化，请重新核验联系人', account_id=None)
        if not win32gui.IsWindowVisible(main.handle) or win32gui.IsIconic(main.handle):
            independent = any(w.process_id() == main.process_id() for w in
                              self.desktop.windows(class_name='mmui::ChatSingleWindow', visible_only=False))
            if self.account_id and independent:
                return dict(state='ready', detail='微信主窗口已收起，独立聊天窗口可用', account_id=self.account_id,
                            version=self.Tools.get_weixin_version())
            return dict(state='hidden', detail='微信主窗口已收起，请打开微信主窗口后连接联系人', account_id=self.account_id)
        if not main.exists(timeout=0.3) or main.class_name() != 'mmui::MainWindow':
            return dict(state='unavailable', detail='未找到已登录的微信主界面，请检查登录及界面可访问性', account_id=None)
        if not self._main_controls_ready(main):
            return dict(state='unavailable', detail='微信界面控件不可访问，当前版本或账号可能不支持 UI 自动化', account_id=self.account_id)
        return dict(state='ready', detail='微信界面可访问', account_id=self.account_id,
                    version=self.Tools.get_weixin_version())

    def reveal(self):
        status = self.health()
        if status['state'] == 'ready':
            # Verified independent chats already provide the connection. Do
            # not expose a tray-hidden Qt main HWND with native ShowWindow.
            return status
        if status['state'] == 'hidden':
            self._open_main()
        return self.health()

    def _title(self, window):
        if not hasattr(window, 'child_window'):
            window = self.desktop.window(handle=window.handle)
        criteria = dict(self.Texts.CurrentChatNameText)
        criteria.pop('title', None)  # Upstream mutates this shared selector during search.
        title = window.child_window(**criteria)
        return title.window_text() if title.exists(timeout=0.2) else ''

    def _search_edit(self, main):
        searches = main.descendants(**self.Main.Search)

        def wait_for_search(seconds):
            deadline = time.monotonic()+seconds
            while time.monotonic() < deadline:
                found = main.descendants(**self.Main.Search)
                if found:
                    return found
                time.sleep(0.1)
            return []

        if not searches:
            back = main.child_window(title_re='^(返回|Back|返回上一頁)$',
                                     control_type='Button', class_name='mmui::XButton')
            if back.exists(timeout=0.1) and back.is_visible():
                self._click(back, None, 'ui.sessions.back')
                searches = wait_for_search(1)
        if not searches:
            navigation = main.child_window(**self.SideBar.Weixin)
            if not navigation.exists(timeout=0.2):
                raise ConnectionLost('微信会话导航暂时不可用，正在自动重试')
            self._click(navigation, None, 'ui.navigation.chats')
            searches = wait_for_search(2)
        if not searches:
            raise ConnectionLost('微信会话列表尚未就绪，正在自动重试')
        if len(searches) not in (1, 2):
            raise BridgeError('无法唯一识别微信搜索框')
        return searches[-1]

    def _search_unique(self, lookup):
        if self.health()['state'] not in ('ready', 'hidden'):
            raise BridgeError('微信当前不可操作，请在运行状态中检查连接')
        main = self._open_main()
        edit = self._search_edit(main)
        self._record('ui.search', lookup=lookup)
        edit.click_input()
        edit.set_text(lookup)
        results = main.child_window(**self.Lists.SearchResult).wait('ready', timeout=4)
        candidates = [x for x in results.children(control_type='ListItem')
                      if x.class_name() == 'mmui::SearchContentCellView' and x.window_text() == lookup]
        if len(candidates) != 1:
            raise BridgeError(f'找到 {len(candidates)} 个同名搜索结果，请使用唯一微信备注')
        self.search_avatar = None
        try:
            rect = candidates[0].rectangle()
            clip = results.rectangle()
            data = row_avatar(self._capture(main), rect,
                              (clip.left, clip.top, clip.right, clip.bottom), session=True)
            if data:
                self.search_avatar = (lookup, data)
        except Exception:
            pass  # Optional image capture must not change the chat-opening flow.
        candidates[0].click_input()
        deadline = time.monotonic()+2
        while self._title(main) != lookup and time.monotonic() < deadline:
            time.sleep(0.1)
        if self._title(main) != lookup or self.Tools.is_group_chat(main):
            raise BridgeError('打开的不是目标单人聊天，已停止操作')
        # The title can update before the narrow page finishes switching.
        # Wait for the actual chat input, not a retained/hidden title node.
        main.child_window(**self.Edits.CurrentChatEdit).wait('visible', timeout=3)
        return main

    def _read_profile(self, window, lookup):
        """Verify identity in the actual chat, including independent windows."""
        if self._title(window) != lookup or self.Tools.is_group_chat(window):
            raise BridgeError('打开的不是目标单人聊天，已停止操作')
        self._focus(window, None)
        info = window.child_window(auto_id='single_chat_info_view', control_type='Group')
        button = window.child_window(**self.Buttons.ChatInfoButton)
        opened = not info.exists(timeout=0.2) or not info.is_visible()
        popup = None
        try:
            if opened:
                self._click(button, None, 'ui.contact_profile.open_info')
            info.wait('visible', timeout=3)
            member = info.child_window(title=lookup, class_name='mmui::ChatMemberCell', control_type='Button')
            member.wait('visible', timeout=2)
            head = member.child_window(class_name='mmui::ContactHeadView', control_type='Button')
            avatar_data = None
            try:
                rect = head.rectangle()
                avatar_data = crop_avatar(self._capture(window), (rect.left, rect.top, rect.right, rect.bottom))
            except Exception:
                pass
            self._click(head, None, 'ui.contact_profile.avatar')
            popup = self.desktop.window(class_name='mmui::ProfileUniquePop', top_level_only=False,
                                        process=window.process_id())
            popup.wait('visible', timeout=3)
            texts = [item.window_text() for item in popup.descendants(control_type='Text')]
            def field(label, default=''):
                index = texts.index(label) if label in texts else -1
                return texts[index+1].strip() if 0 <= index < len(texts)-1 else default
            nickname = field(self.Labels.Nickname, texts[0] if texts else '')
            remark = field(self.Labels.Remark)
            return dict(lookup=remark if remark and remark != '无' else nickname,
                        nickname=nickname, wechat_id=field(self.Labels.WxNum), account_id=self.account_id,
                        avatar_data=avatar_data)
        finally:
            if popup is not None and popup.exists(timeout=0.2):
                popup.close()
            if opened and info.exists(timeout=0.2) and info.is_visible():
                self._click(button, None, 'ui.contact_profile.close_info')
                time.sleep(0.2)

    def inspect(self, lookup, expected_id, account_id=None):
        actual_account = self._ensure_account(account_id)
        # Establish the independent window before opening any contact controls.
        # Searching may leave a narrow main window on its sessions page.
        target = dict(lookup=lookup)
        window = self._find_dialog(target)
        if window is None:
            window = self._open_dialog(target)
        self._record('ui.contact_profile', lookup=lookup)
        profile = self._read_profile(window, lookup)
        validate_identity(profile, lookup, expected_id, account_id)
        if not profile.get('avatar_data') and getattr(self, 'search_avatar', None) and self.search_avatar[0] == lookup:
            profile['avatar_data'] = self.search_avatar[1]
        if profile.get('avatar_data'):
            self.avatar_cache[(actual_account, expected_id)] = profile['avatar_data']
        self.profiles[(actual_account, expected_id, lookup)] = profile
        self.verified_contacts.add((actual_account, expected_id, lookup))
        return profile

    def _check_account(self, contact):
        if (not self.account_hint or self.Tools.get_current_wxid() != self.account_hint
                or self.account_id != contact['account_id']):
            self.windows.clear()
            self.profiles.clear()
            self.verified_contacts.clear()
            raise BridgeError('当前微信账号与绑定账号不一致，请重新核验联系人')

    def _find_dialog(self, contact):
        pid = self._main_window().process_id()
        candidates = [self.desktop.window(handle=w.handle)
                      for w in self.desktop.windows(class_name='mmui::ChatSingleWindow', visible_only=False)
                      if w.process_id() == pid and self._title(w) == contact['lookup']]
        if len(candidates) > 1:
            raise BridgeError('存在多个同名独立聊天窗口，请关闭重复窗口后重试')
        return candidates[0] if candidates else None

    def _open_dialog(self, contact):
        import pyautogui
        # Reconnection is one search followed by one session double-click.
        main = self._search_unique(contact['lookup'])
        self._focus(main, contact)
        time.sleep(0.25)  # Search can expose the old sessions during Qt's page switch.
        listing = main.child_window(**self.Main.SessionList)
        # A narrow main window shows either the chat or the sessions. Return to
        # the sessions once to double-click; never maximize or resize it.
        if not listing.exists(timeout=0.2) or not listing.is_visible():
            back = main.child_window(title_re='^(返回|Back|返回上一頁)$', control_type='Button', class_name='mmui::XButton')
            try:
                back.wait('visible', timeout=3)
            except Exception as exc:
                raise ConnectionLost('聊天布局正在切换，稍后自动重新连接') from exc
            self._click(back, contact, 'ui.sessions.back')
        try:
            listing.wait('visible', timeout=3)
        except Exception as exc:
            raise ConnectionLost('会话列表暂时不可读，稍后自动重新连接') from exc
        time.sleep(0.2)  # Finish the narrow-view slide animation before clicking.
        matches = [item for item in listing.children(control_type='ListItem')
                   if item.window_text().split('\n', 1)[0] == contact['lookup']]
        if len(matches) != 1:
            raise BridgeError('无法唯一识别已核验的联系人会话，请在微信打开该联系人的独立窗口后重试')
        rect = matches[0].rectangle()
        import win32gui
        point = (int(rect.left+rect.width()*0.55), rect.mid_point().y)
        if win32gui.GetAncestor(win32gui.WindowFromPoint(point), 2) != main.handle:
            raise BridgeError('联系人会话被其他窗口遮挡，请重试连接')
        self._record('ui.open_dialog', contact)
        pyautogui.doubleClick(*point, interval=0.1, _pause=False)
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            window = self._find_dialog(contact)
            if window is not None:
                return window
            time.sleep(0.1)
        raise ConnectionLost('微信尚未打开目标聊天窗口，稍后自动重新连接')

    def attach(self, contact, for_send=False):
        existing = self.windows.get(contact['id'])
        if self._window_alive(existing):
            self._check_account(contact)
            if existing.class_name() != 'mmui::ChatSingleWindow' or self._title(existing) != contact['lookup']:
                raise BridgeError('绑定窗口的联系人发生变化，请重新核验')
            if for_send:
                self._focus(existing, contact)
            return self.snapshot(contact)
        # Contacts reaching this method came from the panel's verified store.
        # Reconnection checks the saved binding and account without reopening
        # contact information or clicking the avatar.
        if not contact.get('wechat_id'):
            raise BridgeError('联系人尚未完成微信号核验，请先添加联系人')
        self._ensure_account(contact['account_id'])
        self._check_account(contact)
        window = self._find_dialog(contact)
        if window is None:
            window = self._open_dialog(contact)
        if self._title(window) != contact['lookup']:
            raise BridgeError('独立聊天窗口与联系人绑定不一致')
        if for_send:
            self._focus(window, contact)
        self.windows[contact['id']] = window
        self._record('listener.bound', contact, handle=window.handle)
        return self.snapshot(contact)

    def detach(self, contact_id):
        # Retain the user's window; only release our reference.
        window = self.windows.get(contact_id)
        if window is not None:
            self.window_host.release(self._native_handle(window))
        self.windows.pop(contact_id, None)
        self.previous_items.pop(contact_id, None)

    def avatars(self, contacts, locate=False):
        """Passive reads by default. Only explicit avatar sync may locate a missing image."""
        valid = []
        for contact in contacts:
            try:
                self._check_account(contact)
                valid.append(contact)
            except BridgeError:
                continue
        if not valid:
            return []
        try:
            main = self._main_window()
            listing = main.child_window(**self.Main.SessionList)
            if listing.exists(timeout=0.1) and listing.is_visible():
                rows = listing.children(control_type='ListItem')
                layout = [(r.window_text(), str(r.rectangle())) for r in rows]
                frame, clip = self._capture(main), listing.rectangle()
                pending = {}
                for contact in valid:
                    matches = [r for r in rows if r.window_text().split('\n', 1)[0] == contact['lookup']]
                    if len(matches) == 1:
                        data = row_avatar(frame, matches[0].rectangle(),
                                          (clip.left, clip.top, clip.right, clip.bottom), session=True)
                        if data:
                            pending[(contact['account_id'], contact['wechat_id'])] = data
                if layout == [(r.window_text(), str(r.rectangle())) for r in listing.children(control_type='ListItem')]:
                    self.avatar_cache.update(pending)
        except Exception:
            pass  # Hidden/minimized lists can be read when the user restores them.
        for contact in valid:
            key = (contact['account_id'], contact['wechat_id'])
            window = self.windows.get(contact['id'])
            try:
                if window is not None and window.exists(timeout=0.1) and self._title(window) == contact['lookup']:
                    listing = window.child_window(**self.Lists.FriendChatList)
                    rows = listing.children()
                    layout = [(r.window_text(), str(r.rectangle())) for r in rows]
                    frame, clip = self._capture(window), listing.rectangle()
                    pending = {}
                    for row in reversed(rows):
                        if 'Chat' not in row.class_name() or row.class_name() in ('mmui::ChatItemView', 'mmui::ChatSystemInfoItemView'):
                            continue
                        direction, _ = self._direction(row, window, contact, frame,
                                                       (clip.left,clip.top,clip.right,clip.bottom))
                        if direction not in ('incoming', 'outgoing'):
                            continue
                        image_key = key if direction == 'incoming' else (contact['account_id'], contact['account_id'])
                        if image_key in pending:
                            continue
                        data = row_avatar(frame, row.rectangle(), (clip.left, clip.top, clip.right, clip.bottom),
                                          outgoing=direction == 'outgoing')
                        if data:
                            pending[image_key] = data
                    if layout == [(r.window_text(), str(r.rectangle())) for r in listing.children()]:
                        self.avatar_cache.update(pending)
            except Exception:
                pass
            if locate and key not in self.avatar_cache and not contact.get('avatar_url'):
                self._search_unique(contact['lookup'])
                if self.search_avatar and self.search_avatar[0] == contact['lookup']:
                    self._check_account(contact)
                    self.avatar_cache[key] = self.search_avatar[1]
        allowed = {(c['account_id'], c['wechat_id']) for c in valid} | {(c['account_id'], c['account_id']) for c in valid}
        self._check_account(valid[0])
        return [dict(account_id=account, wechat_id=wxid, data=data)
                for (account, wxid), data in self.avatar_cache.items() if (account, wxid) in allowed]

    def _capture(self, window):
        """PrintWindow renders this HWND even when another app covers it."""
        import ctypes
        import win32gui
        import win32ui
        from PIL import Image
        if win32gui.IsIconic(window.handle):
            raise WindowTransition('聊天窗口正在最小化，正在转入后台监听')
        left, top, right, bottom = win32gui.GetWindowRect(window.handle)
        width, height = right-left, bottom-top
        if width <= 0 or height <= 0:
            raise BridgeError('独立聊天窗口不可读取')
        dc = win32gui.GetWindowDC(window.handle)
        source = win32ui.CreateDCFromHandle(dc)
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(source, width, height)
            memory.SelectObject(bitmap)
            if not ctypes.windll.user32.PrintWindow(window.handle, memory.GetSafeHdc(), 2):
                raise BridgeError('无法读取独立聊天窗口图像，请检查微信窗口后重试监听')
            image = Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1)
            scale = (ctypes.windll.user32.GetDpiForWindow(window.handle) or 96)/96
            return image, (left, top), scale
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            memory.DeleteDC()
            source.DeleteDC()
            win32gui.ReleaseDC(window.handle, dc)

    def _direction(self, item, window, contact, frame=None, visible_bounds=None):
        text = item.window_text()
        if item.element_info.control_type == 'CheckBox':
            return parse_sender(text, {contact['lookup'], contact['nickname']}, self.account_name)
        avatars = [x for x in item.descendants() if any(label in x.class_name().lower()
                   for label in ('avatar', 'headview', 'headimage'))]
        if avatars:
            rectangle, parent = avatars[0].rectangle(), window.rectangle()
            center = (rectangle.left + rectangle.right) / 2
            if center < parent.left + parent.width() * 0.4:
                return 'incoming', text
            if center > parent.left + parent.width() * 0.6:
                return 'outgoing', text
        if frame is not None:
            image, (left, top), scale = frame
            rect = item.rectangle()
            clip=tuple(v-offset for v,offset in zip(visible_bounds,(left,top,left,top))) if visible_bounds else None
            bounds=(rect.left-left, rect.top-top, rect.right-left, rect.bottom-top)
            direction=image_direction(image,bounds,scale,clip)
            if direction=='unknown' and item.class_name()=='mmui::ChatTextItemView':
                direction=clipped_text_direction(image,bounds,scale,clip)
            return direction,text
        # Never convert an unproven direction into an Agent request.
        return 'unknown', text

    def snapshot(self, contact):
        window = self.windows.get(contact['id'])
        if not self._window_alive(window):
            raise ConnectionLost('聊天窗口已关闭，正在自动重新连接')
        # Inactive independent windows can report the generic native title
        # "Weixin". The bound handle and in-chat title identify the conversation.
        title = self._title(window)
        if not title:
            raise ConnectionLost('聊天界面正在切换，正在重新读取')
        if window.class_name() != 'mmui::ChatSingleWindow' or title != contact['lookup']:
            raise BridgeError('监听窗口标题变化，需重新核验联系人')
        self._check_account(contact)
        if self.window_host.ensure(window.handle, contact):
            self._record('ui.background', contact, handle=window.handle)
            time.sleep(0.15)  # Qt must lay out rows at the new native position.
        listing = window.child_window(**self.Lists.FriendChatList)
        if not listing.exists(timeout=0.3):
            raise ConnectionLost('聊天消息列表暂时不可用，正在重新连接')
        rows = listing.children()
        layout = [(row.window_text(), str(row.rectangle())) for row in rows]
        frame = self._capture(window)
        clip=listing.rectangle()
        items = []
        photos=[]
        folded_texts=[]
        previous={i['key'] for i in self.previous_items.get(contact['id'],[])}
        for row in rows:
            cls = row.class_name()
            if 'Chat' not in cls:
                continue
            text = row.window_text()
            if not text:
                continue
            direction, text = self._direction(row, window, contact, frame,
                                               (clip.left,clip.top,clip.right,clip.bottom))
            kind = 'text' if cls == 'mmui::ChatTextItemView' else 'voice' if cls == 'mmui::ChatVoiceItemView' else 'file' if 'File' in cls else 'unknown'
            if cls == 'mmui::ChatBubbleItemView' and text.split('\n', 1)[0] in ('文件', '檔案', 'File'):
                kind = 'file'
            photo=None
            if cls in ('mmui::ChatImageItemView','mmui::ChatImgItemView'):
                kind='image'
                rect=row.rectangle();image,(x,y),scale=frame
                if direction=='incoming' and rect.top>=clip.top and rect.bottom<=clip.bottom:
                    left,right=rect.left+round(70*scale),rect.right-round(70*scale)
                    top,bottom=rect.top+round(6*scale),rect.bottom-round(6*scale)
                    if right>left and bottom>top:
                        thumb=image.crop((left-x,top-y,right-x,bottom-y));thumb.thumbnail((1024,1024))
                        from .wechat_images import ready_thumbnail
                        if ready_thumbnail(thumb):
                            buf=BytesIO();thumb.save(buf,format='PNG');photo=base64.b64encode(buf.getvalue()).decode()
            if cls in ('mmui::ChatItemView', 'mmui::ChatSystemInfoItemView'):
                direction, kind = 'system', 'system'
            digest = hashlib.sha256(text.encode()).hexdigest()[:16]
            runtime = '.'.join(str(x) for x in row.element_info.runtime_id)
            key=f'{window.handle}:{runtime}:{digest}'
            items.append(dict(key=key, text=text,
                              direction=direction, kind=kind, account_id=contact['account_id'],
                              direction_verified=direction in ('incoming','outgoing')))
            if photo:items[-1]['image_data']=photo
            if kind=='text' and direction=='incoming':
                from .wechat_fulltext import folded
                if folded(text):folded_texts.append((items[-1],tuple(row.element_info.runtime_id)))
            if kind=='image' and contact.get('_read_images') and contact['id'] in self.previous_items and key not in previous:
                self.image_pending.setdefault(key,{'attempts':0,'due':0})
            if kind=='image' and direction=='incoming' and contact.get('_read_images'):
                # AI must see the copied image itself, not a partly rendered
                # thumbnail or surrounding chat UI.
                items[-1].pop('image_data',None)
                if key in self.image_cache:items[-1]['image_data']=self.image_cache[key]
                elif key in self.image_pending or (contact['id'] in self.previous_items and key not in previous):
                    self.image_pending.setdefault(key,{'attempts':0,'due':0})
                    photos.append((items[-1],tuple(row.element_info.runtime_id)))
        items = items[-150:]
        cid = contact['id']
        # A new bubble arriving between UIA reads and rendering can move rows.
        # Keep the previous observation for this tick instead of misattributing it.
        if layout != [(row.window_text(), str(row.rectangle())) for row in listing.children()]:
            return self.previous_items.get(cid, [])
        if folded_texts:
            from .wechat_fulltext import hydrate
            from .snapshots import compare
            delta = compare(self.previous_items.get(cid),items)
            new_keys = {item['key'] for item in delta.items} if not delta.historical else set()
            hydrate(self,window,contact,folded_texts,new_keys=new_keys)
        available=[p for p in photos if self.image_pending[p[0]['key']]['attempts']<3 and time.monotonic()>=self.image_pending[p[0]['key']]['due']]
        for item,runtime_id in available[:1]:
            pending=self.image_pending[item['key']]
            if pending['attempts']<3 and time.monotonic()>=pending['due']:
                try:
                    from .wechat_images import copy_bubble
                    data=copy_bubble(self,window,contact,runtime_id)
                    item['image_data']=base64.b64encode(data).decode()
                    self.image_cache[item['key']]=item['image_data']
                    self.image_pending.pop(item['key'],None)
                except Exception:
                    pending['attempts']+=1;pending['due']=time.monotonic()+5
                    item['image_error']='图片原图暂未取得；请保持图片可见或在测试台上传图片。'
            # Keep the cache bounded; DB media is the durable copy.
            if len(self.image_cache)>30:self.image_cache.pop(next(iter(self.image_cache)))
        visible_keys={item['key'] for item in items}
        for key in list(self.image_pending):
            if key.startswith(str(window.handle)+':') and key not in visible_keys:self.image_pending.pop(key,None)
        from .snapshots import align
        items=align(self.previous_items.get(cid),items)
        self.previous_items[cid] = items
        return items

    def _prepare_send(self, contact, text):
        import win32gui
        if getattr(self,'defer_for_input',False):
            import ctypes
            class LastInput(ctypes.Structure):
                _fields_=[('size',ctypes.c_uint),('tick',ctypes.c_uint)]
            last=LastInput();last.size=ctypes.sizeof(last)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last)):
                elapsed=(ctypes.windll.kernel32.GetTickCount()-last.tick)&0xffffffff
                if elapsed<900:raise DesktopBusy('检测到键盘或鼠标操作，等待电脑短暂空闲后发送')
        for attempt in range(2):
            try:
                window = self.windows.get(contact['id'])
                if not self._window_alive(window):
                    self.attach(contact, for_send=True)
                    window = self.windows[contact['id']]
                self._check_account(contact)
                title = self._title(window)
                if not title:
                    raise ConnectionLost('发送前聊天窗口正在切换')
                if window.class_name() != 'mmui::ChatSingleWindow' or title != contact['lookup']:
                    raise BridgeError('发送目标窗口不匹配，请重新核验联系人')
                self._focus(window, contact)
                before_count = self._visible_text_count(window, contact, text)
                cancel = window.child_window(title_re='^(取消多选|取消多選|Cancel selection|Cancel Selection)$', control_type='Button')
                if cancel.exists(timeout=0.2):
                    raise BridgeError('聊天窗口处于多选状态，请退出多选后发送')
                edit = window.child_window(**self.Edits.CurrentChatEdit)
                button = window.child_window(title_re='^(发送(?:\\(S\\))?|Send|send|傳送)$',
                                             control_type='Button', class_name='mmui::XOutlineButton')
                if not edit.exists(timeout=0.3) or not button.exists(timeout=0.3):
                    raise ConnectionLost('发送前聊天输入框暂时不可用')
                self._click(edit, contact, 'ui.input.focus')
                if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != window.handle:
                    raise DesktopBusy('微信聊天窗口未获得前台焦点，等待空闲后重试')
                return window, edit, button, before_count
            except Exception as exc:
                if not connection_error(exc) or attempt:
                    raise
                self.invalidate(contact['id'])
                self._record('send.reconnect', contact)
                time.sleep(0.15)

    def send_file(self, contact, path):
        from .wechat_files import send_pdf
        return send_pdf(self, contact, path)

    def send_voice(self, contact, path, before_send=None):
        from .wechat_voice import send_wav
        import win32gui
        foreground=win32gui.GetForegroundWindow()
        try:return send_wav(self, contact, path, before_send=before_send)
        finally:
            window=self.windows.get(contact['id'])
            if window and foreground:
                try:
                    if (win32gui.IsWindow(foreground) and foreground!=window.handle
                            and win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)==window.handle):
                        win32gui.SetForegroundWindow(foreground)
                except Exception:pass

    def send_image(self, contact, data, before_send=None):
        from .wechat_images import send_image
        import win32gui
        foreground=win32gui.GetForegroundWindow()
        try:return send_image(self,contact,data,before_send=before_send)
        finally:
            window=self.windows.get(contact['id'])
            if window and foreground:
                try:
                    if (win32gui.IsWindow(foreground) and foreground!=window.handle
                            and win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)==window.handle):
                        win32gui.SetForegroundWindow(foreground)
                except Exception:pass

    def send(self, contact, text):
        import win32gui
        foreground=win32gui.GetForegroundWindow()
        try:return self._send_text(contact,text)
        finally:
            window=self.windows.get(contact['id'])
            if window and foreground:
                try:
                    if (win32gui.IsWindow(foreground) and foreground!=window.handle
                            and win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)==window.handle):
                        win32gui.SetForegroundWindow(foreground)
                except Exception:pass

    def _send_text(self, contact, text):
        import pyautogui
        import win32gui
        from pyweixin import SystemSettings
        self._record('send.begin', contact)
        window, edit, button, before_count = self._prepare_send(contact, text)
        baseline=[dict(x) for x in self.snapshot(contact)]
        observer=getattr(self,'receipt_observer',None)
        if observer:observer('prepared',baseline,before_count)
        from .wechat_draft import ChatDraft
        original = edit.window_text()
        draft = ChatDraft(self, window, edit, contact, original) if original else None
        send_attempted = False
        confirmed = False
        # Once paste/send starts, any exception is uncertain and MUST NOT retry.
        try:
            if draft:
                draft.stash()
            if win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)!=window.handle or edit.window_text():
                raise SendUncertain('粘贴前焦点或输入内容已变化，尚未发送；已保护现有输入内容')
            SystemSettings.copy_text_to_clipboard(text)
            if win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)!=window.handle:
                raise SendUncertain('粘贴前焦点已变化，尚未发送；已停止键盘输入')
            self._record('ui.input.paste', contact)
            pyautogui.hotkey('ctrl', 'v', _pause=False)
            time.sleep(0.2)
            if edit.window_text().replace('\r\n','\n') != text.replace('\r\n','\n'):
                raise SendUncertain('粘贴内容无法核对，尚未按发送键；请检查微信输入框')
            if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != window.handle:
                raise SendUncertain('发送前焦点发生变化，请检查微信输入框')
            if not button.is_enabled():
                raise SendUncertain('发送按钮未启用，尚未点击发送；请检查微信输入框')
            send_attempted = True
            if observer:observer('attempted')
            self._click(button, contact, 'ui.send_once')
            from .send_receipts import match
            for attempt in range(12):
                time.sleep(0.25)
                items = self.snapshot(contact)
                found=match(baseline,items,text,before_count)
                if found:
                    self._record('send.confirmed', contact, source_key=found['key'])
                    confirmed = True
                    return found['key']
                if attempt in (3,7):
                    # Request a redraw without focusing, clicking, or sending.
                    try:
                        import win32con
                        win32gui.RedrawWindow(window.handle,None,None,win32con.RDW_INVALIDATE|win32con.RDW_UPDATENOW|win32con.RDW_ALLCHILDREN)
                    except Exception:pass
            raise SendUncertain('已尝试发送，但暂未能核对新增的本人消息；已安排后台复核（最多 2 分钟），请勿重复发送')
        except SendUncertain:
            self._record('send.uncertain', contact)
            raise
        except Exception as exc:
            self._record('send.interrupted', contact, error=str(exc))
            raise SendUncertain('发送过程被打断，请在微信核对结果后处理') from exc
        finally:
            if draft:
                try:
                    draft.restore(text, send_attempted)
                except Exception as exc:
                    self._record('send.draft.restore_pending', contact,
                                 backup=str(draft.backup or ''), confirmed=confirmed)
                    detail = '消息已确认发送' if confirmed else '发送结果需要核对'
                    raise SendUncertain(detail + '，原草稿暂未恢复，已保留恢复备份：' + str(draft.backup or '')) from exc
