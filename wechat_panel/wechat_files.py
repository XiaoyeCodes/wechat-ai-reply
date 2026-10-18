"""Send one generated PDF through the already verified chat window."""
from pathlib import Path
import time


def send_pdf(bridge, contact, path):
    import pyautogui
    import win32gui
    from pyweixin import SystemSettings
    from .bridge import BridgeError, SendUncertain
    from .wechat_draft import ChatDraft
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() != '.pdf' or path.stat().st_size > 30_000_000:
        raise BridgeError('待发送 PDF 不存在或超过 30 MB')
    with path.open('rb') as source:
        if source.read(5) != b'%PDF-': raise BridgeError('待发送文件不是有效 PDF')
    window, edit, button, _ = bridge._prepare_send(contact, path.name)
    before = {x['key'] for x in bridge.snapshot(contact)}
    original = edit.window_text()
    draft = ChatDraft(bridge, window, edit, contact, original) if original else None
    attempted, confirmed = False, False
    try:
        if draft: draft.stash()
        SystemSettings.copy_files_to_clipboard(filepaths_list=[str(path)])
        pyautogui.hotkey('ctrl', 'v', _pause=False)
        time.sleep(.5)
        def receipts():
            return [x for x in bridge.snapshot(contact) if x['key'] not in before
                    and x['direction'] == 'outgoing' and x.get('kind') == 'file'
                    and path.name in x['text']]
        # Weixin builds differ: an inline attachment or a send-file dialog.
        target, send_button = window, button
        candidates = [window]
        try:
            candidates += [w for w in bridge.desktop.windows(process=window.process_id())
                           if w.handle != window.handle and 'Dialog' in w.class_name()]
        except AttributeError:
            pass
        found = False
        for _ in range(20):
            matches = receipts()
            if matches:
                confirmed = True
                bridge._record('send.file.confirmed', contact, source_key=matches[-1]['key'])
                return matches[-1]['key']
            for candidate in candidates:
                controls = candidate.descendants()
                if any(path.name in c.window_text() for c in controls):
                    buttons = [c for c in controls if c.element_info.control_type == 'Button'
                               and c.window_text() in ('发送', '发送(S)', 'Send', 'send', '傳送') and c.is_enabled()]
                    if buttons:
                        target, send_button = candidate, buttons[-1]
                        found = True
                        break
            if found: break
            time.sleep(.25)
        else:
            raise SendUncertain('PDF 附件尚未在发送区核对，未点击发送，请检查微信')
        bridge._check_account(contact)
        if bridge._title(window) != contact['lookup']:
            raise SendUncertain('文件发送前聊天窗口发生变化')
        if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != target.handle:
            raise SendUncertain('文件发送前焦点发生变化，未点击发送')
        attempted = True
        bridge._click(send_button, contact, 'ui.file.send_once')
        for _ in range(40):
            time.sleep(.25)
            matches = receipts()
            if matches:
                confirmed = True
                bridge._record('send.file.confirmed', contact, source_key=matches[-1]['key'])
                return matches[-1]['key']
        raise SendUncertain('已尝试发送 PDF，但未核对到新增的本人文件消息，请在微信确认')
    except SendUncertain:
        raise
    except Exception as exc:
        raise SendUncertain('PDF 发送过程被打断，请在微信核对结果') from exc
    finally:
        if draft:
            try: draft.restore('', attempted)
            except Exception as exc:
                bridge._record('send.draft.restore_pending', contact, confirmed=confirmed)
                raise SendUncertain('PDF 发送结果请在微信核对，原草稿保存在恢复备份：' + str(draft.backup or '')) from exc
