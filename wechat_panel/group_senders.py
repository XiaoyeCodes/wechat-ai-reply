"""Pair normal/selected UIA rows to read Qt's independent sender metadata."""
from collections import Counter
import hashlib
import sqlite3
import time


SUPPORTED = {'mmui::ChatTextItemView', 'mmui::ChatVoiceItemView',
             'mmui::ChatImageItemView', 'mmui::ChatImgItemView',
             'mmui::ChatBubbleItemView', 'mmui::ChatBubbleReferItemView'}


def stamp(row):
    return (tuple(row.element_info.runtime_id), row.class_name(),
            row.element_info.control_type, row.window_text())


def paired_senders(before, selected, after, members):
    """A name must be added by Qt to an unchanged row, never parsed from body."""
    counts = Counter(members)
    result = {}
    before_counts = Counter(value[0] for value in before)
    selected_counts = Counter(value[0] for value in selected)
    after_counts = Counter(value[0] for value in after)
    selected = {value[0]: value for value in selected}
    after = {value[0]: value for value in after}
    for value in before:
        rid, cls, kind, body = value
        if (not rid or cls not in SUPPORTED or kind != 'ListItem' or not body
                or before_counts[rid] != 1 or selected_counts[rid] != 1 or after_counts[rid] != 1
                or after.get(rid) != value):continue
        chosen = selected.get(rid)
        if not chosen or chosen[1:3] != (cls, 'CheckBox'):continue
        names = [name for name, count in counts.items() if count == 1 and
                 chosen[3] in (name + ' ' + body, name + '\n' + body)]
        if len(names) == 1:result[value] = names[0]
    return result


def cached_sender(bridge, window, contact, row):
    cache = getattr(bridge, 'group_sender_cache', {})
    key = (contact['account_id'], contact['wechat_id'], window.handle)
    return cache.get(key, {}).get(stamp(row), '')


def header_fingerprint(row, frame, clip):
    """Invalidate recycled row IDs when the visible sender header changes."""
    image, (left, top), scale = frame
    rect = row.rectangle()
    bottom = min(rect.bottom, rect.top + round(26 * scale))
    if rect.top < clip.top or bottom > clip.bottom:return None
    bounds = (max(rect.left,clip.left)-left, rect.top-top,
              min(rect.right,clip.right)-left, bottom-top)
    return hashlib.sha256(image.crop(bounds).tobytes()).digest()


class SenderLedger:
    """Persist native selection proof, bound to a single WeChat process session."""
    def __init__(self,path):
        self.path=path
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS group_sender_proofs (
                scope TEXT NOT NULL,row_key TEXT NOT NULL,header BLOB NOT NULL,sender TEXT NOT NULL,
                PRIMARY KEY(scope,row_key,header))''')

    def connect(self):return sqlite3.connect(self.path,timeout=10)

    @staticmethod
    def row_key(value):return hashlib.sha256(repr(value).encode()).hexdigest()

    def restore(self,scope,pixels,members):
        with self.connect() as db:
            known={(r[0],r[1]):r[2] for r in db.execute('SELECT row_key,header,sender FROM group_sender_proofs WHERE scope=?',(scope,))}
        result={}
        for value,header in pixels.items():
            sender=known.get((self.row_key(value),header))
            if header is not None and sender and list(members).count(sender)==1:result[value]=sender
        return result

    def save(self,scope,evidence,pixels):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO group_sender_proofs VALUES(?,?,?,?)',
                [(scope,self.row_key(value),pixels[value],sender) for value,sender in evidence.items() if pixels.get(value) is not None])
            db.execute('DELETE FROM group_sender_proofs WHERE scope=? AND rowid NOT IN '
                '(SELECT rowid FROM group_sender_proofs WHERE scope=? ORDER BY rowid DESC LIMIT 500)',(scope,scope))


def persistent_proof(bridge,window,contact):
    if not getattr(bridge,'trace_path',None) or not hasattr(window,'process_id'):return None,None
    import psutil
    if not hasattr(bridge,'native_sender_sessions'):bridge.native_sender_sessions={}
    pid=window.process_id()
    if pid not in bridge.native_sender_sessions:bridge.native_sender_sessions[pid]=psutil.Process(pid).create_time()
    scope=repr((contact['account_id'],contact['wechat_id'],window.handle,pid,bridge.native_sender_sessions[pid]))
    if not hasattr(bridge,'group_sender_ledger'):bridge.group_sender_ledger=SenderLedger(bridge.trace_path.with_name('panel.sqlite3'))
    return bridge.group_sender_ledger,scope


def hydrate(bridge, window, contact, rows, allowed=(), gate=None):
    """Read once for new visible rows, preserving a user's existing selection."""
    members = getattr(bridge, 'group_rosters', {}).get((contact['account_id'], contact['wechat_id']))
    if not members:return
    if not hasattr(bridge, 'group_sender_cache'):bridge.group_sender_cache = {}
    if not hasattr(bridge, 'group_sender_retry'):bridge.group_sender_retry = {}
    if not hasattr(bridge, 'group_sender_headers'):bridge.group_sender_headers = {}
    if not hasattr(bridge, 'group_observed_headers'):bridge.group_observed_headers = {}
    key = (contact['account_id'], contact['wechat_id'], window.handle)
    cache = bridge.group_sender_cache.setdefault(key, {})
    headers = bridge.group_sender_headers.setdefault(key, {})
    listing = window.child_window(**bridge.Lists.FriendChatList)
    clip = listing.rectangle()
    frame = bridge._capture(window)
    pixels = {stamp(row):header_fingerprint(row,frame,clip) for row in rows if row.class_name() in SUPPORTED}
    bridge.group_observed_headers[key] = pixels
    for value in list(cache):
        # A Qt runtime ID and identical body can be recycled for another member.
        # Pixels only invalidate prior native evidence; they never identify a sender.
        if pixels.get(value) is None or headers.get(value) != pixels[value]:cache.pop(value)
    for value in list(headers):
        if value not in pixels:headers.pop(value)
    ledger,scope=persistent_proof(bridge,window,contact)
    if ledger:
        restored=ledger.restore(scope,pixels,members)
        cache.update(restored)
        headers.update({value:pixels[value] for value in restored})
    missing = [row for row in rows if row.class_name() in SUPPORTED
               and row.element_info.control_type == 'ListItem' and pixels.get(stamp(row)) is not None and stamp(row) not in cache
               and stamp(row) in allowed and (gate is None or gate.can_identify(allowed[stamp(row)]))]
    if getattr(bridge, '_voice_recording', False):return
    if not missing or time.monotonic() < bridge.group_sender_retry.get(key, 0):return
    bridge.group_sender_retry[key] = time.monotonic() + 10

    import win32gui
    from pywinauto import mouse
    from .bridge import DesktopBusy

    cancel = window.child_window(title_re='^(取消多选|取消多選|Cancel selection|Cancel Selection)$', control_type='Button')
    if cancel.exists(timeout=.1) and cancel.is_visible():return
    if window.descendants(control_type='MenuItem'):return
    edit = window.child_window(**bridge.Edits.CurrentChatEdit)
    if edit.exists(timeout=.1) and edit.window_text():return
    clip = listing.rectangle()
    targets = [row for row in rows if row.class_name() in SUPPORTED and row.element_info.control_type == 'ListItem'
               and row.rectangle().top >= clip.top and row.rectangle().bottom <= clip.bottom]
    if not targets:return
    foreground = win32gui.GetForegroundWindow()
    hosted = bridge.window_host.managed(window.handle)
    selected_mode = False
    menu = None
    try:
        bridge._check_account(contact)
        bridge._focus(window, contact)
        time.sleep(.25)  # Qt may still be closing the group-info slideout.
        bridge._check_chat_type(window, contact)
        if bridge._title(window) != contact['lookup']:return
        # Obtain fresh immutable values after focus/layout changes.
        before_rows = listing.children()
        before = [stamp(row) for row in before_rows]
        row = next((r for r in before_rows if stamp(r) == stamp(targets[-1])), None)
        if row is None:return
        frame = bridge._capture(window)
        rect, clip = row.rectangle(), listing.rectangle()
        if rect.top < clip.top or rect.bottom > clip.bottom:return
        direction, _ = bridge._direction(row, window, contact, frame,
                                         (clip.left, clip.top, clip.right, clip.bottom))
        if direction not in ('incoming', 'outgoing'):return
        scale = frame[2]
        x = rect.right - round(100 * scale) if direction == 'outgoing' else rect.left + round(100 * scale)
        y = min(rect.top + round((22 if direction == 'outgoing' else 44) * scale), rect.bottom - round(10 * scale))
        if win32gui.GetAncestor(win32gui.WindowFromPoint((x, y)), 2) != window.handle:
            raise DesktopBusy('群消息被其他窗口遮挡，稍后重新识别成员')
        bridge._record('ui.group.sender.open_menu', contact)
        if gate:gate.identifying([allowed[stamp(r)] for r in missing])
        mouse.click(button='right', coords=(x, y))
        time.sleep(.15)
        menu = window.child_window(title_re='^(多选|多選|Select|Select Messages)$', control_type='MenuItem')
        if not menu.exists(timeout=.4):
            # The first click may only dismiss Qt's group-info slideout. Retry
            # only while that same native message still owns the hit point.
            hit = bridge.desktop.from_point(x, y)
            if stamp(hit) == stamp(row):mouse.click(button='right', coords=(x, y))
        menu.wait('visible', timeout=2)
        bridge._click(menu, contact, 'ui.group.sender.select')
        selected_mode = True
        cancel.wait('visible', timeout=2)
        time.sleep(.2)  # Rich bubble sender labels update after the toolbar appears.
        chosen = [stamp(r) for r in listing.children()]
        bridge._click(cancel, contact, 'ui.group.sender.cancel')
        selected_mode = False
        cancel.wait_not('visible', timeout=2)
        time.sleep(.2)  # Sticker/reference accessibility names settle after the cancel button hides.
        after = [stamp(r) for r in listing.children()]
        bridge._check_account(contact)
        bridge._check_chat_type(window, contact)
        if bridge._title(window) != contact['lookup']:return
        proven = paired_senders(before, chosen, after, members)
        current_frame, current_clip = bridge._capture(window), listing.rectangle()
        images = {stamp(r):header_fingerprint(r,current_frame,current_clip) for r in listing.children() if r.class_name() in SUPPORTED}
        cache.update({s:name for s,name in proven.items() if images.get(s) is not None})
        headers.update({s:images[s] for s in cache if images.get(s) is not None})
        if ledger:ledger.save(scope,proven,images)
        if len(cache) > 500:
            live = set(after)
            bridge.group_sender_cache[key] = {s: name for s, name in cache.items() if s in live}
        bridge.group_sender_retry[key] = time.monotonic() + (1 if proven else 10)
        bridge._record('ui.group.sender.verified', contact, members=len(proven))
    finally:
        # Exit only the selection opened by this read; never close a user selection.
        if selected_mode and cancel.exists(timeout=.1) and cancel.is_visible():
            bridge._click(cancel, contact, 'ui.group.sender.cancel')
        elif menu is not None and menu.exists(timeout=.1) and menu.is_visible():
            window.type_keys('{ESC}')
        if hosted:
            window.minimize()
            bridge.window_host.ensure(window.handle, contact)
        if (foreground and win32gui.IsWindow(foreground) and foreground != window.handle
                and win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) == window.handle):
            try:win32gui.SetForegroundWindow(foreground)
            except Exception:pass
