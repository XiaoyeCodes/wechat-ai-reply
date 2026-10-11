"""Copy one verified image bubble, never reuse stale clipboard content."""
from io import BytesIO
import time

from PIL import Image, ImageGrab, ImageStat


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
    import win32process
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
        point=(rect.left+round(85*frame[2]),rect.top+round(22*frame[2]))
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
        rect=menu.rectangle();point=(rect.mid_point().x,rect.mid_point().y)
        target=win32gui.WindowFromPoint(point)
        # Qt popups have their own native root, although their UIA parent is
        # the chat. Verify the actual popup and process instead of comparing
        # the UIA root with the native root (they intentionally differ).
        hit=bridge.desktop.from_point(*point)
        expected=tuple(hit.element_info.runtime_id)
        ancestor=menu.wrapper_object()
        matched=False
        for _ in range(4):
            if tuple(ancestor.element_info.runtime_id)==expected:matched=True;break
            ancestor=ancestor.parent()
            if ancestor is None:break
        if (not target or win32process.GetWindowThreadProcessId(target)[1]!=win32process.GetWindowThreadProcessId(window.handle)[1]
                or not matched or hit.class_name() not in ('mmui::XMenu','mmui::XMenuView') or rect.width()<=0 or rect.height()<=0):
            raise ValueError('图片复制菜单被遮挡，停止点击')
        menu.click_input()
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
