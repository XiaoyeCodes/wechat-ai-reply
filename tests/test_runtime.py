from queue import Queue
from threading import Thread
import time

import pytest

from wechat_panel import runtime


def receive(output, predicate):
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        event = output.get(timeout=max(.01, deadline-time.monotonic()))
        if predicate(event):
            return event
    raise AssertionError('Worker did not emit the expected event')


@pytest.mark.parametrize('connect_fails', [False, True])
def test_resume_reconnects_before_send_and_connection_failure_prevents_send(monkeypatch, connect_fails):
    actions, instances = [], []
    class Bridge:
        def __init__(self):
            self.bound, self.fail = False, False
            instances.append(self)
        def health(self):
            return dict(state='ready', account_id='self')
        def attach(self, contact, for_send=False):
            actions.append('connect')
            if self.fail:
                raise RuntimeError('独立窗口未打开')
            self.bound = True
            return []
        def snapshot(self, contact):
            assert self.bound
            return []
        def send(self, contact, text):
            assert self.bound, 'Send must wait for an independent window'
            actions.append('send')
            return 'new-key'
    monkeypatch.setattr(runtime, 'WeixinBridge', Bridge)
    commands, output = Queue(), Queue()
    contact = dict(id='friend', listening=True, mode='manual')
    commands.put(dict(op='configure', contacts=[contact], paused=False))
    thread = Thread(target=runtime.worker_main, args=(commands, output, False), daemon=True)
    thread.start()
    try:
        receive(output, lambda e:e['kind']=='listener' and e['state']=='listening')
        commands.put(dict(op='configure', contacts=[contact], paused=True))
        commands.put(dict(op='probe', request_id='paused'))
        receive(output, lambda e:e['kind']=='result' and e['request_id']=='paused')
        instances[0].bound = False  # User closed the chat while paused.
        instances[0].fail = connect_fails
        commands.put(dict(op='configure', contacts=[contact], paused=False))
        commands.put(dict(op='send', request_id='send', contact_id='friend', text='intercepted test'))
        result = receive(output, lambda e:e['kind']=='result' and e['request_id']=='send')
        if connect_fails:
            assert result['error'] == '独立窗口未打开'
            assert actions == ['connect', 'connect']
        else:
            assert result['result']['source_key'] == 'new-key'
            assert actions == ['connect', 'connect', 'send']
    finally:
        commands.put(dict(op='stop'))
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_send_recovers_listener_blocked_by_minimized_chat(monkeypatch):
    actions = []
    class Bridge:
        def __init__(self):
            self.minimized = True
        def health(self):
            return dict(state='ready', account_id='self')
        def attach(self, contact, for_send=False):
            actions.append('send-connect' if for_send else 'passive-connect')
            if for_send and self.minimized:
                actions.append('restore')
                self.minimized = False
            return self.snapshot(contact)
        def snapshot(self, contact):
            if self.minimized:
                raise RuntimeError('独立聊天窗口已最小化')
            return []
        def send(self, contact, text):
            assert not self.minimized
            actions.append('send-once')
            return 'confirmed-source'
    monkeypatch.setattr(runtime, 'WeixinBridge', Bridge)
    commands, output = Queue(), Queue()
    contact = dict(id='friend', listening=True, mode='manual')
    commands.put(dict(op='configure', contacts=[contact], paused=False))
    thread = Thread(target=runtime.worker_main, args=(commands, output, False), daemon=True)
    thread.start()
    try:
        receive(output, lambda e:e['kind']=='listener' and e['state']=='error')
        assert actions == ['passive-connect']
        commands.put(dict(op='send', request_id='send', contact_id='friend', text='intercepted'))
        result = receive(output, lambda e:e['kind']=='result' and e['request_id']=='send')
        assert result['result']['source_key'] == 'confirmed-source'
        assert actions == ['passive-connect', 'send-connect', 'restore', 'send-once']
    finally:
        commands.put(dict(op='stop'))
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_background_listening_survives_minimize_transition_and_cleans_up(monkeypatch):
    actions = []
    class Bridge:
        def __init__(self):
            self.polls, self.background = 0, False
        def health(self):
            return dict(state='ready', account_id='self')
        def attach(self, contact, for_send=False):
            actions.append('attach')
            return []
        def snapshot(self, contact):
            self.polls += 1
            if self.polls == 1:
                raise runtime.WindowTransition('minimizing')
            self.background = True
            return [dict(key='incoming', text='new', direction='incoming', kind='text')]
        def listener_state(self, contact):
            return 'background' if self.background else 'listening'
        def suspend(self):
            actions.append('restore-on-pause')
        def close(self):
            actions.append('restore-on-stop')
    monkeypatch.setattr(runtime, 'WeixinBridge', Bridge)
    commands, output = Queue(), Queue()
    contact = dict(id='friend', listening=True, mode='manual')
    commands.put(dict(op='configure', contacts=[contact], paused=False))
    thread = Thread(target=runtime.worker_main, args=(commands, output, False), daemon=True)
    thread.start()
    try:
        receive(output, lambda e:e['kind']=='listener' and e['state']=='background')
        assert actions == ['attach'], 'A minimize transition must not require reconnection'
        commands.put(dict(op='configure', contacts=[contact], paused=True))
        commands.put(dict(op='probe', request_id='paused'))
        receive(output, lambda e:e['kind']=='result' and e['request_id']=='paused')
        assert actions[-1] == 'restore-on-pause'
    finally:
        commands.put(dict(op='stop'))
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert actions[-1] == 'restore-on-stop'


def test_resume_wakes_hidden_weixin_and_connects_all_enabled_contacts(monkeypatch):
    actions = []
    class Bridge:
        def __init__(self):
            self.hidden = True
        def health(self):
            return dict(state='hidden' if self.hidden else 'ready',account_id='self')
        def reveal(self):
            actions.append('wake-main')
            self.hidden = False
        def attach(self,contact,for_send=False):
            actions.append('connect-'+contact['id'])
            return []
        def snapshot(self,contact):
            return []
        def detach(self,cid):
            pass
    monkeypatch.setattr(runtime,'WeixinBridge',Bridge)
    commands,output=Queue(),Queue()
    contacts=[dict(id='a',listening=True),dict(id='b',listening=True),dict(id='disabled',listening=False)]
    commands.put(dict(op='configure',contacts=contacts,paused=True))
    commands.put(dict(op='probe',request_id='paused'))
    thread=Thread(target=runtime.worker_main,args=(commands,output,False),daemon=True)
    thread.start()
    try:
        receive(output,lambda e:e['kind']=='result' and e['request_id']=='paused')
        assert actions==[]
        commands.put(dict(op='configure',contacts=contacts,paused=False))
        receive(output,lambda e:e['kind']=='listener' and e['contact_id']=='b' and e['state']=='listening')
        assert actions==['wake-main','connect-a','connect-b']
    finally:
        commands.put(dict(op='stop'))
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize('stale_uia', [False, True])
def test_closed_window_automatically_reconnects_without_manual_configure(monkeypatch, stale_uia):
    from wechat_panel.bridge import ConnectionLost
    from pywinauto.findwindows import ElementNotFoundError
    actions=[]
    class Bridge:
        def __init__(self):
            self.closed=False
        def health(self):
            return dict(state='hidden' if self.closed else 'ready',account_id='self')
        def reveal(self):
            actions.append('wake')
            self.closed=False
        def attach(self,c,for_send=False):
            actions.append('connect')
            self.closed=False
            return []
        def snapshot(self,c):
            if actions.count('connect')==1:
                self.closed=True
                if stale_uia:
                    raise ElementNotFoundError('temporary Qt layout')
                raise ConnectionLost('closed')
            return []
        def invalidate(self,cid):
            actions.append('invalidate')
    monkeypatch.setattr(runtime,'WeixinBridge',Bridge)
    commands,output=Queue(),Queue()
    commands.put(dict(op='configure',contacts=[dict(id='c',listening=True)],paused=False))
    thread=Thread(target=runtime.worker_main,args=(commands,output,False),daemon=True)
    thread.start()
    try:
        receive(output,lambda e:e['kind']=='listener' and e['state']=='reconnecting')
        receive(output,lambda e:e['kind']=='listener' and e['state']=='listening')
        assert actions.count('connect')==2
        assert actions.count('invalidate')==1
    finally:
        commands.put(dict(op='stop'))
        thread.join(timeout=2)


def test_delayed_weixin_login_automatically_connects_without_resume_click(monkeypatch):
    instances,actions=[],[]
    class Bridge:
        def __init__(self):
            self.logged=False
            self.hidden=True
            instances.append(self)
        def health(self):
            return dict(state='offline' if not self.logged else 'hidden' if self.hidden else 'ready',account_id='self')
        def reveal(self):
            actions.append('wake')
            self.hidden=False
        def attach(self,c,for_send=False):
            actions.append('connect')
            return []
        def snapshot(self,c):
            return []
    monkeypatch.setattr(runtime,'WeixinBridge',Bridge)
    commands,output=Queue(),Queue()
    commands.put(dict(op='configure',contacts=[dict(id='c',listening=True)],paused=False))
    commands.put(dict(op='probe',request_id='started'))
    thread=Thread(target=runtime.worker_main,args=(commands,output,False),daemon=True)
    thread.start()
    try:
        receive(output,lambda e:e['kind']=='result' and e['request_id']=='started')
        assert actions==[]
        instances[0].logged=True
        receive(output,lambda e:e['kind']=='listener' and e['state']=='listening')
        assert actions==['wake','connect']
    finally:
        commands.put(dict(op='stop'))
        thread.join(timeout=2)
