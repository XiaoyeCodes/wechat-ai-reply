"""Temporarily park a chat draft while the serial worker sends a reply."""
import json
from pathlib import Path
import time
import uuid


def open_clipboard(cb):
    for attempt in range(10):
        try:
            cb.OpenClipboard()
            return
        except Exception:
            if attempt == 9:
                raise
            time.sleep(.03)


def clipboard_read():
    import win32clipboard as cb
    values = []
    open_clipboard(cb)
    try:
        fmt = 0
        while True:
            fmt = cb.EnumClipboardFormats(fmt)
            if not fmt:
                break
            try:
                value = cb.GetClipboardData(fmt)
                if isinstance(value, (bytes, str)):
                    values.append((fmt, value))
            except Exception:
                continue
    finally:
        cb.CloseClipboard()
    return values


def clipboard_write(values):
    import win32clipboard as cb
    open_clipboard(cb)
    try:
        cb.EmptyClipboard()
        for fmt, value in values:
            cb.SetClipboardData(fmt, value)
    finally:
        cb.CloseClipboard()


def same_text(a, b):
    return a.replace('\r\n', '\n') == b.replace('\r\n', '\n')


class ChatDraft:
    def __init__(self, bridge, window, edit, contact, text):
        self.bridge, self.window, self.edit, self.contact = bridge, window, edit, contact
        self.text = text
        self.formats = [(13, text)]  # CF_UNICODETEXT also survives a failed cut.
        self.backup = None
        self.cut_started = False

    def _verify_focus(self):
        import win32gui
        from .bridge import BridgeError
        if (not self.bridge._window_alive(self.window)
                or self.bridge._title(self.window) != self.contact['lookup']):
            raise BridgeError('原草稿所在聊天窗口已变化')
        self.bridge._check_account(self.contact)
        self.bridge._focus(self.window, self.contact)
        self.bridge._click(self.edit, self.contact, 'ui.draft.focus')
        if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != self.window.handle:
            raise BridgeError('草稿输入框未获得焦点')

    def _save(self):
        from .ai_store import protect
        import base64
        trace = getattr(self.bridge, 'trace_path', None)
        if trace is None:
            return
        if self.backup is None:
            directory = Path(trace).parent / 'draft-recovery'
            directory.mkdir(parents=True, exist_ok=True)
            self.backup = directory / (str(uuid.uuid4()) + '.json')
        value = dict(contact_id=self.contact['id'], text=self.text,
                     formats=[dict(format=fmt, binary=isinstance(data, bytes),
                         data=base64.b64encode(data).decode() if isinstance(data, bytes) else data)
                         for fmt, data in self.formats])
        temporary = self.backup.with_suffix('.tmp')
        temporary.write_text(protect(json.dumps(value, ensure_ascii=False)), encoding='utf-8')
        temporary.replace(self.backup)

    def stash(self):
        import pyautogui
        import win32gui
        from .bridge import BridgeError
        self._verify_focus()
        if not same_text(self.edit.window_text(), self.text):
            raise BridgeError('暂存前草稿发生变化，本条消息尚未发送')
        # Copy and verify first: a failed cut must never leave the only rich
        # representation of the draft on a transient system clipboard.
        pyautogui.hotkey('ctrl', 'a', _pause=False)
        pyautogui.hotkey('ctrl', 'c', _pause=False)
        time.sleep(.1)
        copied = clipboard_read()
        if not any(fmt == 13 and same_text(value, self.text) for fmt, value in copied):
            raise BridgeError('草稿暂存内容未核对，本条消息尚未发送')
        self.formats = copied
        self._save()
        if (not same_text(self.edit.window_text(), self.text)
                or win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != self.window.handle):
            raise BridgeError('剪切前草稿或焦点发生变化，本条消息尚未发送')
        self.cut_started = True
        pyautogui.hotkey('ctrl', 'x', _pause=False)
        time.sleep(.15)
        if self.edit.window_text():
            raise BridgeError('草稿尚未剪切完成，本条消息尚未发送')
        self.bridge._record('send.draft.saved', self.contact)

    def restore(self, reply, send_attempted):
        import pyautogui
        from .bridge import BridgeError
        if not self.cut_started:
            if self.backup:
                self.backup.unlink(missing_ok=True)
            return
        self._verify_focus()
        current = self.edit.window_text()
        if not same_text(current, self.text):
            if current:
                # Only remove our own unsent paste. Never erase new user typing,
                # or change text that an asynchronous Send click could still use.
                if send_attempted or not same_text(current, reply):
                    raise BridgeError('输入框内容发生变化，原草稿仍保存在恢复备份中')
                pyautogui.hotkey('ctrl', 'a', _pause=False)
                pyautogui.hotkey('ctrl', 'x', _pause=False)
                time.sleep(.1)
                if self.edit.window_text():
                    raise BridgeError('未能清除本次未发送内容，原草稿仍保存在恢复备份中')
            clipboard_write(self.formats)
            pyautogui.hotkey('ctrl', 'v', _pause=False)
            time.sleep(.15)
            if not same_text(self.edit.window_text(), self.text):
                raise BridgeError('原草稿恢复未核对，请保留恢复备份')
        if self.backup:
            self.backup.unlink(missing_ok=True)
        self.bridge._record('send.draft.restored', self.contact)
