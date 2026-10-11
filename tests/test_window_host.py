import json

import pytest

from wechat_panel.window_host import NativeWindows, WindowHost


CONTACT = dict(account_id='self', lookup='friend')
PLACEMENT = [0, 2, [-32000, -32000], [-1, -1], [100, 200, 900, 1000]]


class Windows:
    def __init__(self):
        self.iconic, self.visible, self.front = True, False, 999
        self.signature = [123, 100, 1, 'Qt']
        self.actions = []

    def identity(self, handle):
        return self.signature

    def minimized(self, handle):
        return self.iconic

    def foreground(self):
        return self.front

    def on_screen(self, handle):
        return self.visible

    def placement(self, handle):
        return PLACEMENT

    def park(self, handle):
        self.actions.append('park')
        self.iconic, self.visible = False, False

    def restore(self, handle, placement, minimized):
        assert placement == PLACEMENT
        self.actions.append('minimize-original' if minimized else 'reveal-original')
        self.iconic, self.visible = minimized, not minimized


def test_background_host_moves_once_and_preserves_focus_and_original_position(tmp_path):
    native = Windows()
    host = WindowHost(tmp_path/'windows.json', lambda *args:True, native)
    assert host.ensure(1, CONTACT)
    assert not native.iconic and native.front == 999 and host.managed(1)
    for _ in range(3):
        assert not host.ensure(1, CONTACT)
    # Minimizing an already managed window must retain the original placement.
    native.iconic = True
    assert host.ensure(1, CONTACT)
    host.reveal(1)
    assert native.actions == ['park', 'park', 'reveal-original']
    assert native.front == 999 and not host.managed(1)
    assert json.loads(host.path.read_text()) == {}


@pytest.mark.parametrize('taskbar_activation', [True, False])
def test_manual_activation_or_moving_on_screen_releases_host(tmp_path, taskbar_activation):
    native = Windows()
    host = WindowHost(tmp_path/'windows.json', lambda *args:True, native)
    host.ensure(1, CONTACT)
    if taskbar_activation:
        native.front = 1
    else:
        native.visible = True
    host.ensure(1, CONTACT)
    assert native.actions == ['park', 'reveal-original']
    assert not host.managed(1)


def test_pause_or_stop_restores_minimized_position_and_restart_recovers_journal(tmp_path):
    native = Windows()
    path = tmp_path/'windows.json'
    host = WindowHost(path, lambda *args:True, native)
    host.ensure(1, CONTACT)
    # Simulate a killed worker: a new manager recovers its write-ahead journal.
    recovered = WindowHost(path, lambda *args:True, native)
    assert native.iconic and not recovered.managed(1)
    assert native.actions == ['park', 'minimize-original']
    recovered.ensure(1, CONTACT)
    recovered.release_all()
    assert native.actions[-1] == 'minimize-original'
    assert json.loads(path.read_text()) == {}


@pytest.mark.parametrize('change', ['process', 'contact'])
def test_recovery_never_moves_a_reused_handle_or_different_contact(tmp_path, change):
    native = Windows()
    path = tmp_path/'windows.json'
    host = WindowHost(path, lambda *args:True, native)
    host.ensure(1, CONTACT)
    if change == 'process':
        native.signature = [123, 200, 1, 'Qt']
    recovered = WindowHost(path, lambda *args:change != 'contact', native)
    assert native.actions == ['park']
    assert not recovered.managed(1)


def test_failed_move_restores_original_position(tmp_path):
    native = Windows()
    host = WindowHost(tmp_path/'windows.json', lambda *args:True, native)
    native.park = lambda handle: (_ for _ in ()).throw(RuntimeError('move failed'))
    with pytest.raises(RuntimeError, match='move failed'):
        host.ensure(1, CONTACT)
    assert native.actions == ['minimize-original'] and not host.managed(1)


def test_failed_cleanup_retains_journal_for_later_recovery(tmp_path):
    native = Windows()
    host = WindowHost(tmp_path/'windows.json', lambda *args:True, native)
    host.ensure(1, CONTACT)
    native.restore = lambda *args: (_ for _ in ()).throw(RuntimeError('temporarily unavailable'))
    host.release_all()
    assert host.managed(1) and '1' in json.loads(host.path.read_text())


def test_native_park_and_restore_use_no_activation_flags(monkeypatch):
    import win32api
    import win32con
    import win32gui
    calls = []
    monkeypatch.setattr(win32gui, 'ShowWindow', lambda *args:calls.append(('show', args)))
    monkeypatch.setattr(win32api, 'GetSystemMetrics', lambda metric:{76:-1920,78:5760,77:0}[metric])
    monkeypatch.setattr(win32gui, 'SetWindowPos', lambda *args:calls.append(('move', args)))
    monkeypatch.setattr(win32gui, 'SetWindowPlacement', lambda *args:calls.append(('restore', args)))
    native = NativeWindows()
    native.minimized = native.on_screen = lambda handle:False
    native.park(1)
    native.restore(1, PLACEMENT, True)
    native.restore(1, PLACEMENT, False)
    assert calls[0] == ('show', (1,win32con.SW_SHOWNOACTIVATE))
    assert calls[1][1][2] == 4040
    assert calls[1][1][-1] & win32con.SWP_NOACTIVATE
    assert calls[2][1][1][1] == win32con.SW_SHOWMINNOACTIVE
    assert calls[3][1][1][1] == win32con.SW_SHOWNOACTIVATE
