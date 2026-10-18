"""Read a folded text bubble through WeChat's Copy action, preserving the clipboard."""
import re
import sqlite3
import time

FOLDED = re.compile(r'(?:\.{3}|…+)\s*(?:更多|More|顯示更多|显示更多)\s*$', re.I)


def folded(text):
    return bool(FOLDED.search(text))


def validate_fulltext(preview, text):
    prefix = FOLDED.sub('', preview).strip()
    if (not isinstance(text, str) or not text.strip() or folded(text)
            or not text.lstrip().startswith(prefix) or len(text.strip()) <= len(prefix)):
        raise ValueError('复制结果不是这条消息的完整正文')
    return text


def copy_text(bridge, window, contact, runtime_id, preview):
    import win32clipboard as cb
    import win32gui
    import win32process
    from pywinauto import mouse
    from .wechat_draft import clipboard_read, clipboard_write

    foreground = win32gui.GetForegroundWindow()
    hosted = bridge.window_host.managed(window.handle)
    saved = None
    owned_sequence = None
    menu = None
    try:
        bridge._check_account(contact)
        bridge._focus(window, contact)
        if bridge._title(window) != contact['lookup']:
            raise ValueError('消息窗口身份发生变化')
        listing = window.child_window(**bridge.Lists.FriendChatList)
        row = next((r for r in listing.children() if tuple(r.element_info.runtime_id) == tuple(runtime_id)), None)
        if row is None or row.class_name() != 'mmui::ChatTextItemView' or row.window_text() != preview:
            raise ValueError('折叠消息已变化或不在可见区域')
        rect, clip = row.rectangle(), listing.rectangle()
        frame = bridge._capture(window)
        direction, _ = bridge._direction(row, window, contact, frame, (clip.left, clip.top, clip.right, clip.bottom))
        if direction != 'incoming' or rect.top < clip.top or rect.bottom > clip.bottom:
            raise ValueError('折叠消息方向或可见范围尚未确认')
        point = (rect.left + round(85 * frame[2]), rect.top + round(22 * frame[2]))
        if not (rect.left < point[0] < rect.right and rect.top < point[1] < rect.bottom):
            raise ValueError('消息控件坐标无效')
        if win32gui.GetAncestor(win32gui.WindowFromPoint(point), 2) != window.handle:
            raise ValueError('消息控件被遮挡')
        mouse.click(button='right', coords=point)
        menu = window.child_window(title_re='^(复制|Copy|複製)$', control_type='MenuItem')
        menu.wait('visible', timeout=2)
        rect = menu.rectangle()
        point = (rect.mid_point().x, rect.mid_point().y)
        hit = bridge.desktop.from_point(*point)
        ancestor = menu.wrapper_object()
        matched = False
        for _ in range(4):
            if tuple(ancestor.element_info.runtime_id) == tuple(hit.element_info.runtime_id):
                matched = True
                break
            ancestor = ancestor.parent()
            if ancestor is None:break
        target = win32gui.WindowFromPoint(point)
        if (not target or not matched or hit.class_name() not in ('mmui::XMenu','mmui::XMenuView')
                or win32process.GetWindowThreadProcessId(target)[1] != window.process_id()):
            raise ValueError('复制菜单被遮挡或不属于当前微信')
        saved = clipboard_read()
        clipboard_write([])
        owned_sequence = cb.GetClipboardSequenceNumber()
        menu.click_input()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            time.sleep(.05)
            sequence = cb.GetClipboardSequenceNumber()
            if sequence == owned_sequence:continue
            values = clipboard_read()
            text = next((value for fmt, value in values if fmt == cb.CF_UNICODETEXT), None)
            # Only restore over a clipboard value proven to come from this bubble.
            result = validate_fulltext(preview, text)
            owned_sequence = sequence
            return result
        raise ValueError('微信尚未复制全文，本条不再自动重试')
    finally:
        if saved is not None and cb.GetClipboardSequenceNumber() == owned_sequence:
            clipboard_write(saved)
        try:
            if menu is not None and menu.exists(timeout=.1) and win32gui.GetForegroundWindow() == window.handle:
                window.type_keys('{ESC}')
            if hosted:
                window.minimize()
                bridge.window_host.ensure(window.handle, contact)
            if foreground and win32gui.IsWindow(foreground) and win32gui.GetForegroundWindow() == window.handle:
                try:win32gui.SetForegroundWindow(foreground)
                except Exception:pass
        except Exception:pass


class FulltextLedger:
    """Claim before UI interaction so restarts cannot repeat a copy attempt."""
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS fulltext_reads (
                account_id TEXT NOT NULL, contact_id TEXT NOT NULL, source_key TEXT NOT NULL,
                preview TEXT NOT NULL, body TEXT,
                PRIMARY KEY(account_id,contact_id,source_key))''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def get(self, contact, key, preview):
        with self.connect() as db:
            row = db.execute('SELECT body FROM fulltext_reads WHERE account_id=? AND contact_id=? AND source_key=?',
                             (contact['account_id'],contact['id'],key)).fetchone()
            if row is not None:return True,row[0]
            # Reuse full text already captured before the persistent ledger existed.
            row = db.execute('''SELECT m.text FROM messages m JOIN contacts c ON c.id=m.contact_id
                WHERE c.account_id=? AND c.id=? AND m.source_key=? AND m.text_pending=0''',
                (contact['account_id'],contact['id'],key)).fetchone()
            if row:
                try:return True,validate_fulltext(preview,row[0])
                except ValueError:pass
        return False,None

    def claim(self, contact, key, preview):
        with self.connect() as db:
            return bool(db.execute('INSERT OR IGNORE INTO fulltext_reads VALUES(?,?,?,?,NULL)',
                (contact['account_id'],contact['id'],key,preview)).rowcount)

    def finish(self, contact, key, body):
        with self.connect() as db:
            db.execute('UPDATE fulltext_reads SET body=? WHERE account_id=? AND contact_id=? AND source_key=?',
                       (body,contact['account_id'],contact['id'],key))


def hydrate(bridge, window, contact, candidates, new_keys=None):
    """Copy a new message once; reuse durable results without touching the UI."""
    if not hasattr(bridge, 'fulltext_cache'):
        bridge.fulltext_cache, bridge.fulltext_pending = {}, {}
    ledger = getattr(bridge, 'fulltext_ledger', None)
    if ledger is None and getattr(bridge, 'trace_path', None):
        ledger = bridge.fulltext_ledger = FulltextLedger(bridge.trace_path.with_name('panel.sqlite3'))
    attempted = False
    for item, runtime_id in candidates:
        key = item['key']
        item['identity_text'] = item['text']
        if ledger and key not in bridge.fulltext_cache and key not in bridge.fulltext_pending:
            seen, body = ledger.get(contact,key,item['text'])
            if body is not None:bridge.fulltext_cache[key] = body
            elif seen:bridge.fulltext_pending[key] = {'attempted':True}
        if key not in bridge.fulltext_cache:
            pending = bridge.fulltext_pending.setdefault(key, {'attempted':False})
            if new_keys is not None and key in new_keys:pending['new'] = True
            eligible = new_keys is None or pending.get('new',False)
            if not attempted and not pending['attempted'] and eligible:
                attempted = True
                pending['attempted'] = True
                claimed = ledger is None or ledger.claim(contact,key,item['text'])
                if claimed:
                    try:
                        bridge._record('message.fulltext.copy_once', contact, source_key=key)
                        bridge.fulltext_cache[key] = copy_text(bridge, window, contact, runtime_id, item['text'])
                        if ledger:ledger.finish(contact,key,bridge.fulltext_cache[key])
                        bridge.fulltext_pending.pop(key, None)
                        bridge._record('message.fulltext', contact, characters=len(bridge.fulltext_cache[key]))
                    except Exception:
                        pass  # The durable claim prevents automatic retry, including after restart.
        if key in bridge.fulltext_cache:
            item['text'] = bridge.fulltext_cache[key]
            item['text_pending'] = False
        else:
            item['text_pending'] = True
            item['text_error'] = '微信消息已折叠，全文尚未取得；不会重复复制或交给 AI。如需处理，请重新发送这条消息。'
    for cache in (bridge.fulltext_cache, bridge.fulltext_pending):
        while len(cache) > 150:cache.pop(next(iter(cache)))
