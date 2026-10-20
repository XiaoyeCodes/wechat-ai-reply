"""Lossless transport splitting; never summarize or reformat generated text."""


def receipt_text(text):
    """WeChat trims bubble edges and may expose CRLF through accessibility.

    Use only for delivery receipts, never for the queued/generated message.
    Interior whitespace remains significant (including Markdown table/code rows).
    """
    return text.replace('\r\n', '\n').strip()


def pasted_text_matches(bridge,window,edit,contact,expected):
    """Read back rich emoji as text; an object placeholder alone proves nothing."""
    import time
    actual=edit.window_text()
    normalize=lambda text:text.replace('\r\n','\n')
    if normalize(actual)==normalize(expected):return True
    if '\ufffc' not in actual:return False
    import pyautogui
    import win32gui
    import win32clipboard as cb
    from .wechat_draft import clipboard_read,clipboard_write
    saved=None;owned_sequence=None

    def current():
        bridge._check_account(contact)
        bridge._check_chat_type(window,contact)
        return (bridge._title(window)==contact['lookup'] and edit.window_text()==actual
                and win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)==window.handle)

    try:
        if not current():return False
        saved=clipboard_read();before=cb.GetClipboardSequenceNumber()
        pyautogui.hotkey('ctrl','a',_pause=False)
        if not current():return False
        pyautogui.hotkey('ctrl','c',_pause=False)
        for _ in range(20):
            sequence=cb.GetClipboardSequenceNumber()
            if sequence!=before:
                owned_sequence=sequence;break
            time.sleep(.05)
        if owned_sequence is None or not current():return False
        cb.OpenClipboard()
        try:copied=cb.GetClipboardData(cb.CF_UNICODETEXT)
        finally:cb.CloseClipboard()
        verified=isinstance(copied,str) and normalize(copied)==normalize(expected) and current()
        if verified:bridge._record('ui.input.rich_text_verified',contact,characters=len(expected))
        return verified
    finally:
        if win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)==window.handle and edit.window_text()==actual:
            pyautogui.hotkey('ctrl','end',_pause=False)
        if saved is not None and cb.GetClipboardSequenceNumber()==owned_sequence:
            clipboard_write(saved)


def split_text(text, limit=1999):
    parts = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        cut = cut + 1 if cut >= limit // 2 else limit
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts
