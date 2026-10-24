"""Receive verified image bubbles and send PNGs without stale copies or resends."""
from io import BytesIO
from pathlib import Path
import tempfile
import time

from PIL import Image, ImageGrab, ImageStat

from .snapshots import align, compare


def match_image(baseline, current):
    current = align(baseline, current)
    delta = compare(baseline, current)
    if delta.historical or delta.gap:
        return None
    matches = [x for x in delta.items if x.get('direction') == 'outgoing'
               and x.get('direction_verified', True) and x.get('kind') == 'image']
    return matches[0] if len(matches) == 1 else None


def send_image(bridge, contact, data, before_send=None):
    import pyautogui
    import win32gui
    from PIL import Image
    from .wechat_clipboard import copy_files
    from .bridge import BridgeError, SendUncertain, DesktopBusy, connection_error
    from .wechat_draft import ChatDraft, clipboard_read, clipboard_write
    from .voice_reply import ReplySuperseded

    if len(data) > 8_000_000:
        raise BridgeError('待发送图片超过 8 MB')
    with Image.open(BytesIO(data)) as img:
        if img.format != 'PNG' or img.width * img.height > 20_000_000:
            raise BridgeError('待发送图片不是有效 PNG 或尺寸过大')
        img.verify()
    if before_send:before_send()
    window, edit, button, _ = bridge._prepare_send(contact, '[图片]')
    baseline = bridge.snapshot(contact)
    observer = getattr(bridge, 'receipt_observer', None)
    original = edit.window_text()
    draft = ChatDraft(bridge, window, edit, contact, original) if original else None
    clipboard = clipboard_read()
    pasted = confirmed = attempted = False
    try:
        if draft:draft.stash()
        if observer:observer('prepared', baseline, 0)
        with tempfile.TemporaryDirectory(prefix='relay-image-') as folder:
            path = Path(folder) / (Path(folder).name + '.png')
            path.write_bytes(data)

            def verify(target):
                bridge._check_account(contact)
                if bridge._title(window) != contact['lookup']:
                    raise SendUncertain('图片发送前聊天窗口发生变化，未点击发送')
                if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != target.handle:
                    error=SendUncertain if pasted else DesktopBusy
                    raise error('图片发送前焦点发生变化，未点击发送')
                if before_send:before_send()

            def receipt():
                try:return match_image(baseline,bridge.snapshot(contact))
                except Exception as exc:
                    if not connection_error(exc):raise
                    bridge._record('send.receipt.read_retry',contact,kind='image',error=type(exc).__name__)
                    return None

            verify(window)
            if edit.window_text():raise BridgeError('图片粘贴前输入框发生变化')
            copy_files(filepaths_list=[str(path)])
            verify(window)
            # Some builds send on paste. Persist intent before any possible send.
            if observer:observer('attempted')
            pasted = True
            pyautogui.hotkey('ctrl', 'v', _pause=False)
            time.sleep(.5)
            target, send_button = window, button
            controls=[];candidates=[window]
            for _ in range(20):
                # Weixin 4.1 inserts a single rich image object into the empty
                # editor, without exposing its filename through UI Automation.
                try:
                    inline_ready=edit.window_text()=='\ufffc' and button.is_enabled()
                except Exception as exc:
                    if not connection_error(exc):raise
                    inline_ready=False
                if inline_ready:break
                found_receipt=receipt()
                if found_receipt:
                    confirmed = True
                    return found_receipt['key']
                candidates = [window]
                try:
                    candidates += [w for w in bridge.desktop.windows(process=window.process_id())
                                   if w.handle != window.handle and 'Dialog' in w.class_name()]
                except AttributeError:pass
                found = False
                for candidate in candidates:
                    try:controls=candidate.descendants()
                    except Exception as exc:
                        if not connection_error(exc):raise
                        continue
                    # The unique clipboard path/name must appear in the preview,
                    # not merely a generic enabled chat Send button.
                    preview = any(path.name in c.window_text() for c in controls)
                    buttons = [c for c in controls if c.element_info.control_type == 'Button'
                               and c.window_text() in ('发送','发送(S)','Send','send','傳送') and c.is_enabled()]
                    if preview and buttons:
                        target, send_button = candidate, buttons[-1]
                        found = True
                        break
                if found:break
                time.sleep(.25)
            else:
                bridge._record('send.image.preview_unconfirmed', contact,
                    controls=[{'class':c.class_name(),'type':c.element_info.control_type}
                              for c in controls][-50:] if candidates else [])
                raise SendUncertain('图片预览尚未核对，未点击发送，请检查微信')
            verify(target)
            attempted = True
            bridge._click(send_button, contact, 'ui.image.send_once')
            deadline=time.monotonic()+12
            for _ in range(48):
                time.sleep(.25)
                found_receipt=receipt()
                if found_receipt:
                    confirmed = True
                    bridge._record('send.image.confirmed', contact, source_key=found_receipt['key'])
                    return found_receipt['key']
                if time.monotonic()>=deadline:break
            raise SendUncertain('已尝试发送图片，但未核对到新增的本人图片消息，请在微信确认；不会自动重发')
    except (SendUncertain, ReplySuperseded):
        raise
    except Exception as exc:
        if pasted:raise SendUncertain('图片发送过程被打断，请在微信核对；不会自动重发') from exc
        raise
    finally:
        try:
            if draft:
                # An unconfirmed rich image must not be overwritten with a draft.
                if pasted and not confirmed:
                    bridge._record('send.draft.restore_pending',contact,backup=str(draft.backup or ''))
                    raise SendUncertain('图片发送结果待核对，原草稿保存在恢复备份：'+str(draft.backup or ''))
                try:draft.restore('', attempted)
                except Exception as exc:
                    raise SendUncertain('图片发送结果请在微信核对，原草稿保存在恢复备份：'+str(draft.backup or '')) from exc
        finally:
            try:clipboard_write(clipboard)
            except Exception:bridge._record('send.image.clipboard_restore_failed',contact)


def encode_image(image):
    if image.width*image.height>20_000_000:raise ValueError('图片尺寸超过识别限制')
    image=image.convert('RGB');image.thumbnail((2048,2048))
    buf=BytesIO();image.save(buf,format='PNG')
    if len(buf.getvalue())>8_000_000:raise ValueError('图片超过 8 MB 识别限制')
    return buf.getvalue()


def ready_thumbnail(image):
    # Weixin's unloaded image icon is a nearly uniform pale gray rectangle.
    return max(ImageStat.Stat(image.convert('RGB')).stddev)>18


def fresh_clipboard_image(before,timeout=4):
    import win32clipboard as cb
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        time.sleep(.1)
        if cb.GetClipboardSequenceNumber()==before:continue
        value=ImageGrab.grabclipboard()
        if isinstance(value,Image.Image):return encode_image(value),cb.GetClipboardSequenceNumber()
        if isinstance(value,list) and len(value)==1:
            with Image.open(value[0]) as image:return encode_image(image),cb.GetClipboardSequenceNumber()
    raise ValueError('微信尚未将图片复制完成，将在下次读取重试')


def copy_bubble(bridge,window,contact,runtime_id):
    import win32gui
    import win32clipboard as cb
    from pywinauto import mouse
    foreground=win32gui.GetForegroundWindow();hosted=bridge.window_host.managed(window.handle)
    saved=[];copied_seq=None;menu=None
    try:
        bridge._check_account(contact)
        bridge._focus(window,contact)
        if bridge._title(window)!=contact['lookup']:raise ValueError('图片窗口身份发生变化')
        listing=window.child_window(**bridge.Lists.FriendChatList)
        row=next((r for r in listing.children() if tuple(r.element_info.runtime_id)==tuple(runtime_id)),None)
        if row is None:raise ValueError('图片消息已不在可见区域')
        rect=row.rectangle();clip=listing.rectangle();frame=bridge._capture(window)
        direction,_=bridge._direction(row,window,contact,frame,(clip.left,clip.top,clip.right,clip.bottom))
        if direction!='incoming' or rect.top<clip.top or rect.bottom>clip.bottom:
            raise ValueError('图片方向或可见区域尚未确认')
        # The leading edge of every incoming image starts at 70 DIP, after its
        # avatar. Click inside that bubble, not the full row's center.
        # Group rows include a sender-name header above the image bubble.
        # Clicking that header opens member actions instead of image Copy.
        point=(rect.left+round(85*frame[2]),rect.top+round((44 if contact.get('chat_type')=='group' else 22)*frame[2]))
        if not (rect.left<point[0]<rect.right and rect.top<point[1]<rect.bottom):raise ValueError('图片控件坐标无效')
        if win32gui.GetAncestor(win32gui.WindowFromPoint(point),2)!=window.handle:raise ValueError('图片控件被遮挡')
        mouse.click(button='right',coords=point)
        menu=window.child_window(title_re='^(复制|Copy|複製)$',control_type='MenuItem')
        menu.wait('visible',timeout=2)
        cb.OpenClipboard()
        try:
            fmt=0
            while True:
                fmt=cb.EnumClipboardFormats(fmt)
                if not fmt:break
                try:
                    value=cb.GetClipboardData(fmt)
                    if isinstance(value,(bytes,str)):saved.append((fmt,value))
                except Exception:pass
            cb.EmptyClipboard()
        finally:cb.CloseClipboard()
        before=cb.GetClipboardSequenceNumber()
        # Qt advertises InvokePattern but does not copy on Invoke. A verified
        # native click is required; sequence change is part of success criteria.
        bridge._click(menu,contact,'ui.image.copy')
        data,copied_seq=fresh_clipboard_image(before)
        return data
    finally:
        if saved and (copied_seq is None or cb.GetClipboardSequenceNumber()==copied_seq):
            try:
                cb.OpenClipboard()
                try:
                    cb.EmptyClipboard()
                    for fmt,value in saved:
                        try:cb.SetClipboardData(fmt,value)
                        except Exception:pass
                finally:cb.CloseClipboard()
            except Exception:pass
        try:
            # Only close a menu still owned by this verified chat window.
            if menu is not None and menu.exists(timeout=.1):window.type_keys('{ESC}')
            if hosted:
                window.minimize();bridge.window_host.ensure(window.handle,contact)
            if foreground and win32gui.IsWindow(foreground) and win32gui.GetForegroundWindow()==window.handle:
                try:win32gui.SetForegroundWindow(foreground)
                except Exception:pass
        except Exception:pass
