"""Qt timing and attachment regressions, with all desktop I/O replaced."""
from types import SimpleNamespace

import pytest

from wechat_panel.bridge import SendUncertain
from wechat_panel.wechat_files import send_pdf


class TransientReadError(RuntimeError):
    hresult = -2147220991  # UIA: 0x80040201, element temporarily unavailable.


@pytest.mark.parametrize('slow_stage', ['paste', 'button', 'receipt'])
def test_text_tolerates_slow_qt_without_second_paste_or_click(monkeypatch, tmp_path, slow_stage):
    from test_wechat_draft import make_bridge
    from wechat_panel import wechat_clipboard
    import pyautogui
    import wechat_panel.bridge as bridge_module

    obj, contact, state, _ = make_bridge(monkeypatch, tmp_path)
    state['text'] = ''
    clock = {'time': 0., 'pending': None, 'pastes': 0, 'clicks': 0, 'reads': 0}
    real_snapshot = obj.snapshot
    window = obj.windows[contact['id']]
    button = window.child_window(class_name='mmui::XOutlineButton')
    button.is_enabled = lambda: slow_stage != 'button' or clock['time'] >= .6
    real_click = obj._click

    def click(control, current, action):
        if action == 'ui.send_once':
            clock['clicks'] += 1
        real_click(control, current, action)

    def paste(*keys, **kwargs):
        assert keys == ('ctrl', 'v')
        clock['pastes'] += 1
        if slow_stage == 'paste':
            clock['pending'] = state['clipboard'][0][1]
        else:
            state['text'] = state['clipboard'][0][1]

    def sleep(seconds):
        clock['time'] += seconds
        if clock['pending'] is not None and clock['time'] >= .6:
            state['text'] = clock['pending']
            clock['pending'] = None

    def snapshot(current):
        if slow_stage == 'receipt' and clock['clicks'] and clock['reads'] < 2:
            clock['reads'] += 1
            raise TransientReadError('temporary UIA read')
        return real_snapshot(current)

    obj._click = click
    obj.snapshot = snapshot
    monkeypatch.setattr(pyautogui, 'hotkey', paste)
    monkeypatch.setattr(wechat_clipboard, 'copy_text', lambda value: state.update(clipboard=[(13, value)]))
    monkeypatch.setattr(bridge_module.time, 'sleep', sleep)
    assert obj.send(contact, '模拟收到消息后的完整回复') == '1'
    assert clock['pastes'] == clock['clicks'] == 1
    assert len(state['sent']) == 1


def pdf_fixture(monkeypatch, tmp_path, *, late_dialog=False, preview=True, verified=True):
    import pyautogui
    import win32gui
    from wechat_panel import wechat_clipboard

    path = tmp_path / 'report.pdf'
    path.write_bytes(b'%PDF-1.4\n% isolated transport test')
    state = {'pastes': 0, 'clicks': 0, 'enumerations': 0, 'stages': [], 'sent': False}
    button = SimpleNamespace(window_text=lambda: '发送', is_enabled=lambda: True,
                             element_info=SimpleNamespace(control_type='Button'))
    label = SimpleNamespace(window_text=lambda: 'report.pdf',
                            element_info=SimpleNamespace(control_type='Text'))
    # A filename in the history must not prove that the current paste is ready.
    history = SimpleNamespace(window_text=lambda: '文件\nreport.pdf\n12 KB',
                              element_info=SimpleNamespace(control_type='ListItem'))
    edit = SimpleNamespace(window_text=lambda: '')
    window = SimpleNamespace(handle=123, process_id=lambda: 42,
        descendants=lambda: [history, button] if not preview else ([] if late_dialog else [label, button]))
    dialog = SimpleNamespace(handle=124, class_name=lambda: 'mmui::SendFileDialog',
                             descendants=lambda: [label, button])

    def windows(**kwargs):
        state['enumerations'] += 1
        return [dialog] if late_dialog and state['enumerations'] >= 2 else []

    old = dict(key='old', text='文件\nreport.pdf\n12 KB', kind='file', direction='outgoing', direction_verified=True)
    bridge = SimpleNamespace(_prepare_send=lambda *args: (window, edit, button, 0),
        _check_account=lambda *args: None, _title=lambda *args: 'friend', _record=lambda *args, **kw: None,
        desktop=SimpleNamespace(windows=windows),
        receipt_observer=lambda stage, *args: state['stages'].append(stage))
    bridge.snapshot = lambda c: [old] + ([dict(old, key='new', direction_verified=verified)] if state['sent'] else [])

    def paste(*keys, **kwargs):
        assert keys == ('ctrl', 'v')
        assert state['stages'] == ['prepared', 'attempted']
        state['pastes'] += 1

    def click(*args):
        state['clicks'] += 1
        state['sent'] = True

    bridge._click = click
    monkeypatch.setattr(wechat_clipboard, 'copy_files', lambda **kwargs: None)
    monkeypatch.setattr(pyautogui, 'hotkey', paste)
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 124 if late_dialog and state['pastes'] else 123)
    monkeypatch.setattr(win32gui, 'GetAncestor', lambda handle, flag: handle)
    monkeypatch.setattr('wechat_panel.wechat_files.time.sleep', lambda seconds: None)
    return bridge, path, state


def test_pdf_persists_intent_before_paste_and_recognizes_delayed_dialog(monkeypatch, tmp_path):
    bridge, path, state = pdf_fixture(monkeypatch, tmp_path, late_dialog=True)
    assert send_pdf(bridge, {'lookup': 'friend'}, path) == 'new'
    assert state['stages'] == ['prepared', 'attempted']
    assert state['pastes'] == state['clicks'] == 1
    assert state['enumerations'] >= 2


def test_pdf_old_history_filename_is_not_a_current_attachment_preview(monkeypatch, tmp_path):
    bridge, path, state = pdf_fixture(monkeypatch, tmp_path, preview=False)
    with pytest.raises(SendUncertain):
        send_pdf(bridge, {'lookup': 'friend'}, path)
    assert state['pastes'] == 1
    assert state['clicks'] == 0


def test_pdf_does_not_accept_explicitly_unverified_direction_as_delivery(monkeypatch, tmp_path):
    bridge, path, state = pdf_fixture(monkeypatch, tmp_path, verified=False)
    with pytest.raises(SendUncertain):
        send_pdf(bridge, {'lookup': 'friend'}, path)
    assert state['pastes'] == state['clicks'] == 1


def test_pdf_recovers_transient_receipt_reads_without_another_send(monkeypatch, tmp_path):
    bridge, path, state = pdf_fixture(monkeypatch, tmp_path)
    real_snapshot = bridge.snapshot
    state['failed_reads'] = 0

    def snapshot(contact):
        if state['clicks'] and state['failed_reads'] < 2:
            state['failed_reads'] += 1
            raise TransientReadError('temporary UIA file receipt')
        return real_snapshot(contact)

    bridge.snapshot = snapshot
    assert send_pdf(bridge, {'lookup': 'friend'}, path) == 'new'
    assert state['failed_reads'] == 2
    assert state['pastes'] == state['clicks'] == 1


def test_pdf_supports_delayed_inline_attachment_without_filename_label(monkeypatch, tmp_path):
    bridge, path, state = pdf_fixture(monkeypatch, tmp_path, preview=False)
    window, _, button, count = bridge._prepare_send(None, None)
    state['edit_reads'] = 0

    def edit_text():
        if not state['pastes']:
            return ''
        state['edit_reads'] += 1
        return '\ufffc' if state['edit_reads'] >= 3 else ''

    edit = SimpleNamespace(window_text=edit_text)
    bridge._prepare_send = lambda *args: (window, edit, button, count)
    assert send_pdf(bridge, {'lookup': 'friend'}, path) == 'new'
    assert state['edit_reads'] >= 3
    assert state['pastes'] == state['clicks'] == 1


def test_failed_file_clipboard_verification_never_pastes_or_clicks(monkeypatch, tmp_path):
    from wechat_panel import wechat_clipboard
    bridge, path, state = pdf_fixture(monkeypatch, tmp_path)

    def fail(**kwargs):
        raise wechat_clipboard.ClipboardError('clipboard verification failed')

    monkeypatch.setattr(wechat_clipboard, 'copy_files', fail)
    with pytest.raises(wechat_clipboard.ClipboardError):
        send_pdf(bridge, {'lookup': 'friend'}, path)
    assert state['stages'] == ['prepared']
    assert state['pastes'] == state['clicks'] == 0
