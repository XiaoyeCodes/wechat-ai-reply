"""Separate Qt's quoted data from the request, and copy its exact image preview."""
import hashlib
import json
import re
import sqlite3
import time


MARKER = re.compile(r'\n引用 ([^\r\n]{1,128}?) 的(?:消息|訊息)\s*[:：]\s?')
IMAGE_LABELS = {'图片', '[图片]', '圖片', '[圖片]', 'Image', '[Image]'}


def split_quote(text, cls='mmui::ChatTextItemView'):
    if cls != 'mmui::ChatTextItemView':return None
    match = MARKER.search(text)
    if not match or not text[:match.start()].strip():return None
    content = text[match.end():]
    kind = 'image' if content.strip() in IMAGE_LABELS else 'text'
    return {'text':text[:match.start()].strip(), 'quote':{
        'kind':kind, 'sender':match[1].strip(), 'text':content[:12000],
        'status':'pending' if kind == 'image' else 'ready',
        'truncated':len(content)>12000, 'proof':hashlib.sha256(text.encode()).hexdigest()}}


def image_point(rect, scale, sender):
    """The native reference thumbnail follows the 12-DIP author label."""
    from PIL import ImageFont
    from pathlib import Path
    import os
    font = ImageFont.truetype(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'msyh.ttc'), max(1,round(12*scale)))
    return (rect.left+round(88*scale+font.getlength(sender+':')+24*scale),
            rect.bottom-round(31*scale))


def fingerprint(quote,row,frame,clip):
    image,(left,top),scale=frame;rect=row.rectangle()
    x,y=image_point(rect,scale,quote['sender'])
    bounds=(round(x-16*scale),round(y-12*scale),round(x+16*scale),round(y+12*scale))
    if bounds[0]<clip.left or bounds[1]<clip.top or bounds[2]>clip.right or bounds[3]>clip.bottom:return ''
    return hashlib.sha256(image.crop((bounds[0]-left,bounds[1]-top,bounds[2]-left,bounds[3]-top)).tobytes()).hexdigest()


def copy_image(bridge, window, contact, runtime_id, raw):
    import win32gui
    import win32clipboard as cb
    from pywinauto import mouse
    from .wechat_draft import clipboard_read, clipboard_write
    from .wechat_images import fresh_clipboard_image
    from .bridge import DesktopBusy

    foreground = win32gui.GetForegroundWindow()
    hosted = bridge.window_host.managed(window.handle)
    viewer = None; saved = None; owned_sequence = None
    try:
        bridge._check_account(contact)
        bridge._check_chat_type(window, contact)
        if getattr(bridge,'_voice_recording',False):raise DesktopBusy('正在录音，稍后读取引用图片')
        previews = bridge.desktop.windows(class_name='mmui::PreviewWindow', process=window.process_id())
        if any(p.is_visible() for p in previews):raise DesktopBusy('请先关闭当前图片预览，再读取引用图片')
        edit = window.child_window(**bridge.Edits.CurrentChatEdit)
        if edit.exists(timeout=.1) and edit.window_text():raise DesktopBusy('保留当前输入草稿，暂不打开引用图片')
        bridge._focus(window, contact);time.sleep(.2)
        if bridge._title(window)!=contact['lookup']:raise ValueError('引用图片窗口身份发生变化')
        listing = window.child_window(**bridge.Lists.FriendChatList)
        row = next((r for r in listing.children() if tuple(r.element_info.runtime_id)==tuple(runtime_id)),None)
        parsed = split_quote(raw)
        if row is None or row.class_name()!='mmui::ChatTextItemView' or row.window_text()!=raw or not parsed or parsed['quote']['kind']!='image':
            raise ValueError('引用消息已变化或不在可见区域')
        rect, clip = row.rectangle(), listing.rectangle()
        scale = bridge._capture(window)[2]
        direction, _ = bridge._direction(row,window,contact,None)
        # Private rows without semantic avatars need the rendered direction proof.
        if direction=='unknown':
            frame=bridge._capture(window)
            direction,_=bridge._direction(row,window,contact,frame,(clip.left,clip.top,clip.right,clip.bottom))
        if direction!='incoming':raise ValueError('引用图片消息方向尚未确认')
        point = image_point(rect,scale,parsed['quote']['sender'])
        if not (rect.left<point[0]<rect.right and rect.top<point[1]<rect.bottom):raise ValueError('引用图片卡片坐标无效')
        # Qt rows can include a separator just beyond the viewport. Require the
        # referenced thumbnail itself to be visible, rather than its whole row.
        if not (clip.left<point[0]-20*scale and point[0]+20*scale<clip.right
                and clip.top<point[1]-20*scale and point[1]+20*scale<clip.bottom):
            raise DesktopBusy('请让引用图片的缩略图完整可见')
        if win32gui.GetAncestor(win32gui.WindowFromPoint(point),2)!=window.handle:raise DesktopBusy('引用图片被其他窗口遮挡')
        before = {p.handle for p in previews if p.is_visible()}
        mouse.double_click(coords=point)
        for _ in range(12):
            opened = [p for p in bridge.desktop.windows(class_name='mmui::PreviewWindow',process=window.process_id())
                      if p.is_visible() and p.handle not in before]
            if opened:break
            time.sleep(.1)
        if len(opened)!=1:raise ValueError('未打开这条引用的唯一图片预览，不使用最近图片替代')
        viewer = bridge.desktop.window(handle=opened[0].handle)
        preview = viewer.child_window(class_name='mmui::PreviewImage',control_type='Button')
        preview.wait('visible',timeout=2)
        rect = preview.rectangle();point=(rect.mid_point().x,rect.mid_point().y)
        if win32gui.GetAncestor(win32gui.WindowFromPoint(point),2)!=viewer.handle:raise DesktopBusy('引用图片预览被遮挡')
        mouse.click(button='right',coords=point)
        menu = viewer.child_window(title_re='^(复制|Copy|複製)$',control_type='MenuItem')
        menu.wait('visible',timeout=2)
        saved = clipboard_read()
        cb.OpenClipboard()
        try:cb.EmptyClipboard()
        finally:cb.CloseClipboard()
        owned_sequence=cb.GetClipboardSequenceNumber()
        bridge._click(menu,contact,'ui.quote.image.copy')
        data,owned_sequence=fresh_clipboard_image(owned_sequence)
        bridge._check_account(contact)
        if bridge._title(window)!=contact['lookup'] or row.window_text()!=raw:
            raise ValueError('读取期间引用消息发生变化')
        return data
    finally:
        if saved is not None and cb.GetClipboardSequenceNumber()==owned_sequence:
            clipboard_write(saved)
        if viewer is not None:
            try:
                if viewer.exists(timeout=.1) and viewer.class_name()=='mmui::PreviewWindow' and viewer.process_id()==window.process_id():viewer.close()
            except Exception:pass
        if hosted:
            window.minimize();bridge.window_host.ensure(window.handle,contact)
        if foreground and win32gui.IsWindow(foreground) and win32gui.GetForegroundWindow()==window.handle:
            try:win32gui.SetForegroundWindow(foreground)
            except Exception:pass


class QuoteLedger:
    """A native copy is claimed once and its scoped media survives restarts."""
    def __init__(self,path):
        self.path=path
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS quoted_image_reads (
                account_id TEXT NOT NULL,contact_id TEXT NOT NULL,source_key TEXT NOT NULL,
                proof TEXT NOT NULL,media_id TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(account_id,contact_id,source_key,proof))''')

    def connect(self):return sqlite3.connect(self.path,timeout=10)

    def get(self,contact,key,proof):
        with self.connect() as db:
            return db.execute('SELECT media_id,error FROM quoted_image_reads WHERE account_id=? AND contact_id=? AND source_key=? AND proof=?',
                (contact['account_id'],contact['id'],key,proof)).fetchone()

    def claim(self,contact,key,proof):
        with self.connect() as db:
            return bool(db.execute('INSERT OR IGNORE INTO quoted_image_reads VALUES(?,?,?,?,?,?)',
                (contact['account_id'],contact['id'],key,proof,'','引用图片读取未完成，请重新引用发送')).rowcount)

    def finish(self,contact,key,proof,media_id='',error=''):
        with self.connect() as db:
            db.execute('UPDATE quoted_image_reads SET media_id=?,error=? WHERE account_id=? AND contact_id=? AND source_key=? AND proof=?',
                (media_id,error,contact['account_id'],contact['id'],key,proof))


def hydrate(bridge,window,contact,candidates,new_keys,active_keys=None):
    if not candidates:return
    from .store import Store
    from .companion_store import CompanionStore
    from .group_chats import is_group, qualifies
    if not hasattr(bridge,'quote_pending'):bridge.quote_pending={}
    if not hasattr(bridge,'quote_ledger'):
        bridge.quote_ledger=QuoteLedger(bridge.trace_path.with_name('panel.sqlite3'))
    ledger=bridge.quote_ledger;attempted=False
    for item,runtime_id,raw in candidates:
        quote=item['quote'];key=item['key'];proof=quote['proof']
        # Sender enrichment changes a group row's content key. Keep the pending
        # read attached to the same native row and raw quote across that change.
        token=(contact['account_id'],contact['id'],tuple(runtime_id),hashlib.sha256(raw.encode()).hexdigest())
        if active_keys is not None and key not in active_keys:bridge.quote_pending.pop(token,None)
        ledger_key=f"{getattr(window,'handle',0)}:{'.'.join(map(str,runtime_id))}:{item.get('sender','')}"
        if key in new_keys:bridge.quote_pending[token]=True
        result=ledger.get(contact,ledger_key,proof)
        if result is None and getattr(bridge,'_voice_recording',False) and bridge.quote_pending.get(token):
            item['text_pending']=True
            continue
        if result is None and bridge.quote_pending.get(token) and not attempted and item['direction']=='incoming' and (not is_group(contact) or qualifies(contact,item)):
            attempted=True
            if ledger.claim(contact,ledger_key,proof):
                try:
                    data=copy_image(bridge,window,contact,runtime_id,raw)
                    media=CompanionStore(Store(ledger.path)).media('wechat:'+contact['id'],data)
                    ledger.finish(contact,ledger_key,proof,media['id'])
                    bridge._record('message.quote.image',contact,source_key=key,media_id=media['id'])
                except Exception as exc:
                    ledger.finish(contact,ledger_key,proof,error='引用图片暂未读取成功，请重新引用发送。')
                    bridge._record('message.quote.unavailable',contact,source_key=key,error=str(exc))
            result=ledger.get(contact,ledger_key,proof)
        if result:
            quote.update(media_id=result[0],status='ready' if result[0] else 'unavailable',error=result[1])
            item['text_pending']=False
        else:
            quote['status']='pending' if bridge.quote_pending.get(token) else 'unavailable'
            quote['error']='引用原图尚未读取，不使用其他图片代替。'
            item['text_pending']=quote['status']=='pending'
    while len(bridge.quote_pending)>150:bridge.quote_pending.pop(next(iter(bridge.quote_pending)))
