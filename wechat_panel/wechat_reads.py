"""Permit desktop reads only for fresh incoming requests, never viewport changes."""
import re
import time
import uuid

from .snapshots import align, compare
from .wechat_quotes import split_quote


def follow_unread(bridge,window,contact):
    """Follow a native unread marker without moving the mouse or taking focus."""
    import win32gui
    import win32api
    import win32con
    button=window.child_window(class_name='mmui::UnreadBarView',control_type='Button')
    if not hasattr(bridge,'unread_markers'):bridge.unread_markers={}
    key=(contact['account_id'],contact['id'],window.handle)
    if not button.exists(timeout=.05) or not button.is_visible():
        bridge.unread_markers.pop(key,None)
        return False
    name=button.window_text()
    if not re.fullmatch(r'\d+\s*条新消息',name):return False
    marker=(tuple(button.element_info.runtime_id),name)
    if bridge.unread_markers.get(key)==marker:return False
    if button.top_level_parent().handle!=window.handle:return False
    bridge._check_account(contact)
    bridge._check_chat_type(window,contact)
    if bridge._title(window)!=contact['lookup']:return False
    rect=button.rectangle();point=(rect.mid_point().x,rect.mid_point().y)
    left,top,right,bottom=win32gui.GetWindowRect(window.handle)
    if not (left<point[0]<right and top<point[1]<bottom):return False
    client=win32gui.ScreenToClient(window.handle,point)
    if not button.is_visible() or (tuple(button.element_info.runtime_id),button.window_text())!=marker:return False
    bridge.unread_markers[key]=marker
    try:
        # Qt advertises InvokePattern but its unread bar ignores Invoke().
        # Deliver one press to this HWND; never use global mouse input or focus.
        position=win32api.MAKELONG(*client)
        try:
            win32gui.SendMessageTimeout(window.handle,win32con.WM_LBUTTONDOWN,win32con.MK_LBUTTON,position,win32con.SMTO_ABORTIFHUNG,1000)
        finally:
            win32gui.SendMessageTimeout(window.handle,win32con.WM_LBUTTONUP,0,position,win32con.SMTO_ABORTIFHUNG,1000)
        if button.exists(timeout=.05) and button.is_visible():raise ValueError('新消息标记仍可见，未确认恢复最新位置')
        bridge._record('listener.follow_unread',contact,method='native_marker',marker=name)
        return True
    except Exception as exc:
        bridge._record('listener.follow_unread.unavailable',contact,error=str(exc))
        return False


def requested(contact, item):
    if item['direction']=='outgoing' or item['kind']!='text':return False
    if contact.get('chat_type')!='group' or not contact.get('group_mention_only',True):return True
    own=contact.get('group_self_name','')
    if not own or not contact.get('group_self_name_verified'):return False
    quoted=split_quote(item['text'])
    body=quoted['text'] if quoted else item['text']
    body='\n'.join(line for line in body.splitlines() if not line.lstrip().startswith(('>','＞')))
    body=re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"','',body)
    from .group_chats import mention_pattern
    return bool(re.search(mention_pattern(own),body))


class ReadGate:
    def __init__(self):
        self.previous=None
        self.seen={}
        self.pending={}
        self.attempts={}
        self.tail=None
        self.admissions={}

    def observe(self,contact,items,now=None,followed=False):
        now=time.monotonic() if now is None else now
        if not items:
            self.fresh_tokens=set();self.admission_tokens={}
            return {}
        baseline=self.tail if followed and self.tail is not None else self.previous
        current=align(baseline,items)
        delta=compare(baseline,current)
        if delta.gap:self.pending.clear()
        enabled=contact.get('listening',True) and contact.get('mode') in ('ai_auto','ai_assist')
        if not enabled:self.pending.clear()
        if enabled and not delta.historical:
            for item in delta.items:
                if item['key'] not in self.seen and item['direction'] in ('incoming','unknown') and item['kind'] in ('text','image'):
                    self.pending[item['key']]=now
                    self.admissions[item['key']]=uuid.uuid4().hex
        for item in current:self.seen[item['key']]=True
        live={item['key']:item for item in current}
        self.pending={key:at for key,at in self.pending.items() if key in live
            and now-at<(600 if live[key]['kind']=='image' else 120)}
        requests=[item for item in current if item['key'] in self.pending and requested(contact,item)]
        # A fresh @ question may use pictures observed before it or arriving
        # just after it. Unmentioned group pictures alone never touch the UI.
        group=contact.get('chat_type')=='group'
        media=bool(contact.get('_read_images'))
        image_requests=[self.pending[item['key']] for item in requests if not split_quote(item['text'])]
        allowed={item['key'] for item in requests}
        if media:
            allowed.update(item['key'] for item in current if item['key'] in self.pending and item['kind']=='image'
                and (not group or any(self.pending[item['key']]<=at+20 for at in image_requests)))
        if self.previous is None or current and not delta.retain:self.previous=current
        tail_current=align(self.tail,current)
        tail_delta=compare(self.tail,tail_current)
        if self.tail is None or not tail_delta.historical and not tail_delta.retain:self.tail=tail_current
        while len(self.seen)>1500:self.seen.pop(next(iter(self.seen)))
        self.attempts={key:count for key,count in self.attempts.items() if key in self.pending}
        self.admissions={key:value for key,value in self.admissions.items() if key in self.pending}
        self.fresh_tokens={item['native_token'] for item in current if item['key'] in self.pending}
        self.admission_tokens={item['native_token']:self.admissions[item['key']] for item in current if item['key'] in self.admissions}
        return {item['native_token']:item['key'] for item in current if item['key'] in allowed}

    def can_identify(self,key):return self.attempts.get(key,0)<2

    def identifying(self,keys):
        for key in keys:self.attempts[key]=self.attempts.get(key,0)+1


def observe(bridge,window,contact,rows,frame,clip,followed=False):
    """Build a cursor without sender enrichment, which changes normal row keys."""
    from .group_senders import stamp
    if not hasattr(bridge,'read_gates'):bridge.read_gates={}
    key=(contact['account_id'],contact['id'],window.handle)
    gate=bridge.read_gates.setdefault(key,ReadGate())
    items=[]
    for row in rows:
        cls=row.class_name();body=row.window_text()
        if 'Chat' not in cls or not body:continue
        direction,text=bridge._direction(row,window,contact,frame,(clip.left,clip.top,clip.right,clip.bottom))
        token=stamp(row)
        kind='text' if cls=='mmui::ChatTextItemView' else 'image' if cls in ('mmui::ChatImageItemView','mmui::ChatImgItemView') else 'system'
        items.append({'key':repr(token),'native_token':token,'text':text,'kind':kind,'direction':direction})
    return gate,gate.observe(contact,items,followed=followed)
