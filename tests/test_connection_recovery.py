"""Connection recovery with a fake bridge only; no desktop or WeChat access."""
from queue import Empty, Queue
from threading import Thread
import time

import pytest

from wechat_panel import runtime
from wechat_panel.bridge import BridgeError, DesktopBusy


def until(output, predicate, timeout=4):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        event=output.get(timeout=max(.01,deadline-time.monotonic()))
        if predicate(event):return event
    raise AssertionError('Expected worker event was not observed')


@pytest.mark.parametrize('busy_stage', ['attach', 'snapshot'])
def test_busy_contact_recovers_without_blocking_another_contact(monkeypatch,busy_stage):
    actions=[];busy_count=0
    class Bridge:
        def health(self):return dict(state='ready',account_id='self')
        def attach(self,c,for_send=False):
            nonlocal busy_count
            actions.append(('attach',c['id']))
            if c['id']=='busy' and busy_stage=='attach' and busy_count==0:
                busy_count+=1
                raise DesktopBusy('Target temporarily lacks foreground focus')
            return []
        def snapshot(self,c):
            nonlocal busy_count
            if c['id']=='busy' and busy_stage=='snapshot' and busy_count==0:
                busy_count+=1
                raise DesktopBusy('A user window temporarily covers the control')
            return []
        def invalidate(self,cid):
            actions.append(('invalidate',cid))
    monkeypatch.setattr(runtime,'WeixinBridge',Bridge)
    commands,output=Queue(),Queue()
    contacts=[dict(id=cid,account_id='self',listening=True) for cid in ('busy','other')]
    commands.put(dict(op='configure',contacts=contacts,paused=False))
    thread=Thread(target=runtime.worker_main,args=(commands,output,False),daemon=True)
    thread.start()
    try:
        if busy_stage=='snapshot':
            until(output,lambda e:e['kind']=='listener' and e['contact_id']=='other' and e['state']=='listening')
        waiting=until(output,lambda e:e['kind']=='listener' and e['contact_id']=='busy' and e['state'] in ('error','reconnecting'))
        assert waiting['state']=='reconnecting', 'A temporary focus conflict must not permanently block a contact'
        if busy_stage=='attach':
            until(output,lambda e:e['kind']=='listener' and e['contact_id']=='other' and e['state']=='listening')
        until(output,lambda e:e['kind']=='listener' and e['contact_id']=='busy' and e['state']=='listening')
        assert ('invalidate','busy') not in actions, 'A focus conflict does not invalidate verified identity'
        assert busy_count==1
    finally:
        commands.put(dict(op='stop'));thread.join(timeout=2)
        assert not thread.is_alive()


def test_identity_mismatch_remains_blocked_while_other_contact_listens(monkeypatch):
    actions=[]
    class Bridge:
        def health(self):return dict(state='ready',account_id='self')
        def attach(self,c,for_send=False):
            actions.append(c['id'])
            if c['id']=='mismatch':raise BridgeError('Window identity does not match the verified contact')
            return []
        def snapshot(self,c):return []
    monkeypatch.setattr(runtime,'WeixinBridge',Bridge)
    commands,output=Queue(),Queue()
    contacts=[dict(id=cid,account_id='self',listening=True) for cid in ('mismatch','other')]
    commands.put(dict(op='configure',contacts=contacts,paused=False))
    thread=Thread(target=runtime.worker_main,args=(commands,output,False),daemon=True)
    thread.start()
    try:
        until(output,lambda e:e['kind']=='listener' and e['contact_id']=='mismatch' and e['state']=='error')
        until(output,lambda e:e['kind']=='listener' and e['contact_id']=='other' and e['state']=='listening')
        deadline=time.monotonic()+2.2
        while time.monotonic()<deadline:
            try:output.get(timeout=.1)
            except Empty:pass
        assert actions.count('mismatch')==1
    finally:
        commands.put(dict(op='stop'));thread.join(timeout=2)
        assert not thread.is_alive()


def test_covered_session_row_defers_before_any_double_click(monkeypatch):
    from types import SimpleNamespace
    import pyautogui
    import win32gui
    from wechat_panel import bridge
    rect=SimpleNamespace(left=20,width=lambda:200,mid_point=lambda:SimpleNamespace(y=100))
    row=SimpleNamespace(window_text=lambda:'Friend\nPreview',rectangle=lambda:rect)
    listing=SimpleNamespace(exists=lambda **kw:True,is_visible=lambda:True,
        children=lambda **kw:[row],wait=lambda *args,**kw:None)
    main=SimpleNamespace(handle=99,child_window=lambda **kw:listing)
    obj=bridge.WeixinBridge.__new__(bridge.WeixinBridge)
    obj.Main=SimpleNamespace(SessionList={'title':'Sessions'})
    obj._search_unique=lambda *args:main
    obj._focus=lambda *args:None
    monkeypatch.setattr(win32gui,'WindowFromPoint',lambda point:1234)
    monkeypatch.setattr(win32gui,'GetAncestor',lambda handle,flag:handle)
    monkeypatch.setattr(bridge.time,'sleep',lambda duration:None)
    def forbidden(*args,**kw):raise AssertionError('A covered row must never be clicked')
    monkeypatch.setattr(pyautogui,'doubleClick',forbidden)
    with pytest.raises(DesktopBusy):obj._open_dialog(dict(id='friend',lookup='Friend'))


@pytest.mark.parametrize('failure', ['focus', 'stale-uia'])
@pytest.mark.parametrize('health_state', ['ready', 'account_changed'])
def test_transient_identity_failure_retries_after_delay_without_manual_reconnect(monkeypatch,failure,health_state):
    from wechat_panel.bridge import ConnectionLost
    attempts=[]
    class Bridge:
        verified=False
        def health(self):
            return dict(state='ready' if self.verified else health_state,account_id='self' if self.verified else None,session_key='same-login')
        def identify(self):
            attempts.append('identify')
            if len(attempts)==1:
                if failure=='focus':raise DesktopBusy('A window currently holds focus')
                raise ConnectionLost('Temporary UIA layout transition')
            self.verified=True
            return self.health()
    monkeypatch.setattr(runtime,'WeixinBridge',Bridge)
    commands,output=Queue(),Queue()
    thread=Thread(target=runtime.worker_main,args=(commands,output,False),daemon=True)
    thread.start()
    try:
        failed=until(output,lambda e:e['kind']=='health' and e['state']=='unavailable')
        assert failed['account_id'] is None
        for n in range(3):
            commands.put(dict(op='probe',request_id=str(n)))
            until(output,lambda e:e['kind']=='result' and e['request_id']==str(n))
        assert attempts==['identify'], 'Repeated probes must respect identity retry backoff'
        until(output,lambda e:e['kind']=='health' and e['state']=='ready' and e['account_id']=='self')
        assert attempts==['identify','identify']
    finally:
        commands.put(dict(op='stop'));thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize('health_state', ['ready', 'account_changed'])
def test_permanent_identity_failure_does_not_repeat_profile_automatically(monkeypatch,health_state):
    attempts=[]
    class Bridge:
        def health(self):return dict(state=health_state,account_id=None,session_key='same-login')
        def identify(self):
            attempts.append('identify')
            raise BridgeError('Current account does not match the verified identity')
    monkeypatch.setattr(runtime,'WeixinBridge',Bridge)
    commands,output=Queue(),Queue()
    thread=Thread(target=runtime.worker_main,args=(commands,output,False),daemon=True)
    thread.start()
    try:
        until(output,lambda e:e['kind']=='health' and e['state']=='unavailable')
        deadline=time.monotonic()+2.3
        while time.monotonic()<deadline:
            try:output.get(timeout=.1)
            except Empty:pass
        assert attempts==['identify']
        commands.put(dict(op='probe',request_id='manual',reveal=True))
        until(output,lambda e:e['kind']=='result' and e['request_id']=='manual')
        assert attempts==['identify','identify']
    finally:
        commands.put(dict(op='stop'));thread.join(timeout=2)
        assert not thread.is_alive()
