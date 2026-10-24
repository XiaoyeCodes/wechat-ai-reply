"""Send one generated PDF through the already verified chat window."""
from pathlib import Path
import time


def attachment_preview(control):
    """Exclude every label belonging to a previously sent message bubble."""
    current = control
    for _ in range(12):
        kind = getattr(getattr(current, 'element_info', None), 'control_type', None)
        cls = current.class_name() if hasattr(current, 'class_name') else ''
        if kind == 'ListItem' or ('Chat' in cls and 'ItemView' in cls):
            return False
        if not hasattr(current, 'parent'):
            return True
        current = current.parent()
        if current is None:
            return True
    return False


def send_pdf(bridge, contact, path):
    import pyautogui
    import win32gui
    from .bridge import BridgeError, DesktopBusy, SendUncertain, connection_error
    from .wechat_clipboard import copy_files
    from .send_receipts import match_file
    from .wechat_draft import ChatDraft
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() != '.pdf' or path.stat().st_size > 30_000_000:
        raise BridgeError('待发送 PDF 不存在或超过 30 MB')
    with path.open('rb') as source:
        if source.read(5) != b'%PDF-': raise BridgeError('待发送文件不是有效 PDF')
    window, edit, button, _ = bridge._prepare_send(contact, path.name)
    baseline = [dict(item) for item in bridge.snapshot(contact)]
    observer = getattr(bridge, 'receipt_observer', None)
    original = edit.window_text()
    draft = ChatDraft(bridge, window, edit, contact, original) if original else None
    attempted, confirmed, pasted = False, False, False

    def verify(target):
        bridge._check_account(contact)
        if bridge._title(window) != contact['lookup']:
            raise SendUncertain('文件发送前聊天窗口发生变化，未点击发送')
        if hasattr(bridge, '_check_chat_type'):
            bridge._check_chat_type(window, contact)
        if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != target.handle:
            if not pasted:
                raise DesktopBusy('文件粘贴前焦点发生变化，等待空闲后重试')
            raise SendUncertain('文件发送前焦点发生变化，未点击发送')

    def receipt():
        try:
            return match_file(baseline, bridge.snapshot(contact), path.name)
        except Exception as exc:
            if not connection_error(exc):
                raise
            bridge._record('send.file.receipt_pending', contact, error=str(exc))
            return None

    try:
        if draft: draft.stash()
        verify(window)
        if edit.window_text():
            raise BridgeError('PDF 粘贴前输入框发生变化')
        if observer: observer('prepared', baseline, 0)
        copy_files(filepaths_list=[str(path)])
        verify(window)
        # Some builds send on paste. Persist intent before either possible send.
        if observer: observer('attempted')
        pasted = True
        pyautogui.hotkey('ctrl', 'v', _pause=False)
        time.sleep(.5)
        # Weixin builds differ: an inline attachment or a send-file dialog.
        target, send_button = window, button
        for _ in range(20):
            found = receipt()
            if found:
                confirmed = True
                bridge._record('send.file.confirmed', contact, source_key=found['key'])
                return found['key']
            try:
                if edit.window_text() == '\ufffc' and button.is_enabled():
                    break  # This editor was verified empty before our single paste.
                candidates = [window]
                try:
                    candidates += [w for w in bridge.desktop.windows(process=window.process_id())
                                   if w.handle != window.handle and 'Dialog' in w.class_name()]
                except AttributeError:
                    pass
                ready = False
                for candidate in candidates:
                    controls = candidate.descendants()
                    if any(path.name in [line.strip() for line in c.window_text().splitlines()]
                           and attachment_preview(c) for c in controls):
                        buttons = [c for c in controls if c.element_info.control_type == 'Button'
                                   and c.window_text() in ('发送', '发送(S)', 'Send', 'send', '傳送') and c.is_enabled()]
                        if buttons:
                            target, send_button = candidate, buttons[-1]
                            ready = True
                            break
                if ready:
                    break
            except Exception as exc:
                if not connection_error(exc):
                    raise
                bridge._record('send.file.preview_pending', contact, error=str(exc))
            time.sleep(.25)
        else:
            raise SendUncertain('PDF 附件尚未在发送区核对，未点击发送，请检查微信')
        verify(target)
        attempted = True
        bridge._click(send_button, contact, 'ui.file.send_once')
        for _ in range(40):
            time.sleep(.25)
            found = receipt()
            if found:
                confirmed = True
                bridge._record('send.file.confirmed', contact, source_key=found['key'])
                return found['key']
        raise SendUncertain('已尝试发送 PDF，但未核对到新增的本人文件消息，请在微信确认')
    except SendUncertain:
        raise
    except Exception as exc:
        if not pasted:
            raise
        raise SendUncertain('PDF 发送过程被打断，请在微信核对结果') from exc
    finally:
        if draft:
            if pasted and not confirmed:
                bridge._record('send.draft.restore_pending', contact, confirmed=False)
                raise SendUncertain('PDF 发送结果待核对，原草稿保存在恢复备份：' + str(draft.backup or ''))
            try: draft.restore('', attempted)
            except Exception as exc:
                bridge._record('send.draft.restore_pending', contact, confirmed=confirmed)
                raise SendUncertain('PDF 发送结果请在微信核对，原草稿保存在恢复备份：' + str(draft.backup or '')) from exc
