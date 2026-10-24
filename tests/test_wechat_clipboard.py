"""Test clipboard encoding and contention with a complete Win32 substitute."""
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from wechat_panel import wechat_clipboard


@pytest.fixture
def clipboard(monkeypatch):
    state = {'open': False, 'attempts': 0, 'closes': 0, 'busy': 0, 'data': {}, 'sleeps': []}

    def open_clipboard():
        state['attempts'] += 1
        if state['attempts'] <= state['busy']:
            raise OSError('clipboard temporarily occupied')
        assert not state['open']
        state['open'] = True

    def empty():
        assert state['open']
        state['data'].clear()

    def set_data(fmt, value):
        assert state['open']
        state['data'][fmt] = value

    def get_data(fmt):
        assert state['open']
        value = state['data'][fmt]
        if fmt == 15:
            offset, x, y, non_client, wide = struct.unpack('<IiiII', value[:20])
            assert (offset, x, y, non_client, wide) == (20, 0, 0, 0, 1)
            decoded = value[offset:].decode('utf-16-le')
            assert decoded.endswith('\0\0')
            return tuple(decoded[:-2].split('\0'))
        return value

    def close():
        assert state['open']
        state['open'] = False
        state['closes'] += 1

    fake = SimpleNamespace(CF_HDROP=15, CF_UNICODETEXT=13, OpenClipboard=open_clipboard,
        EmptyClipboard=empty, SetClipboardData=set_data,
        SetClipboardText=lambda value, fmt: set_data(fmt, value),
        GetClipboardData=get_data, CloseClipboard=close)
    monkeypatch.setitem(sys.modules, 'win32clipboard', fake)
    monkeypatch.setattr(wechat_clipboard.time, 'sleep', state['sleeps'].append)
    return state, fake


def test_file_list_uses_fixed_dropfiles_header_and_double_unicode_nul(clipboard, tmp_path):
    state, _ = clipboard
    paths = [tmp_path / '财经日报.pdf', tmp_path / '图片😀.png']
    wechat_clipboard.copy_files(filepaths_list=paths)
    data = state['data'][15]
    assert data[:20] == struct.pack('<IiiII', 20, 0, 0, 0, 1)
    assert data[20:].decode('utf-16-le') == '\0'.join(str(path.resolve()) for path in paths) + '\0\0'
    assert data[-4:] == b'\0\0\0\0'
    assert state['attempts'] == state['closes'] == 1
    assert not state['open']


@pytest.mark.parametrize('kind', ['files', 'text'])
def test_short_clipboard_occupation_retries_before_writing(clipboard, tmp_path, kind):
    state, _ = clipboard
    state['busy'] = 2
    if kind == 'files':
        wechat_clipboard.copy_files([tmp_path / 'report.pdf'])
    else:
        wechat_clipboard.copy_text('完整文本\r\n第二行😀')
        assert state['data'][13] == '完整文本\r\n第二行😀'
    assert state['attempts'] == 3 and state['closes'] == 1
    assert state['sleeps'] == [.03, .03]


def test_occupied_clipboard_has_a_bounded_failure_without_clearing_it(clipboard):
    state, _ = clipboard
    state['busy'] = 99
    state['data'][13] = 'untouched'
    with pytest.raises(wechat_clipboard.ClipboardError, match='占用'):
        wechat_clipboard.copy_text('reply')
    assert state['attempts'] == 10 and state['closes'] == 0
    assert len(state['sleeps']) == 9
    assert state['data'] == {13: 'untouched'}


@pytest.mark.parametrize('kind', ['files', 'text'])
def test_write_error_is_propagated_and_clipboard_is_closed(clipboard, monkeypatch, tmp_path, kind):
    state, fake = clipboard

    def fail(*args):
        raise OSError('write failed')

    monkeypatch.setattr(fake, 'SetClipboardData' if kind == 'files' else 'SetClipboardText', fail)
    with pytest.raises(wechat_clipboard.ClipboardError, match='写入或回读失败'):
        if kind == 'files':
            wechat_clipboard.copy_files([tmp_path / 'report.pdf'])
        else:
            wechat_clipboard.copy_text('reply')
    assert state['closes'] == 1 and not state['open']


@pytest.mark.parametrize('kind', ['files', 'text'])
def test_mismatched_readback_is_not_reported_as_success(clipboard, monkeypatch, tmp_path, kind):
    state, fake = clipboard
    monkeypatch.setattr(fake, 'GetClipboardData', lambda fmt: ('another.pdf',) if kind == 'files' else 'another reply')
    with pytest.raises(wechat_clipboard.ClipboardError, match='回读内容不一致'):
        if kind == 'files':
            wechat_clipboard.copy_files([tmp_path / 'report.pdf'])
        else:
            wechat_clipboard.copy_text('reply')
    assert state['closes'] == 1 and not state['open']


@pytest.mark.parametrize('value', ['', 'hello\nworld', '中文和 emoji 😀'])
def test_unicode_text_roundtrip_preserves_exact_contents(clipboard, value):
    state, _ = clipboard
    wechat_clipboard.copy_text(value)
    assert state['data'][13] == value
    assert state['closes'] == 1


@pytest.mark.parametrize('value', [None, 'text\0truncated'])
def test_invalid_text_does_not_acquire_or_clear_clipboard(clipboard, value):
    state, _ = clipboard
    with pytest.raises(ValueError):
        wechat_clipboard.copy_text(value)
    assert state['attempts'] == 0 and state['data'] == {}


@pytest.mark.parametrize('paths', [[], 'report.pdf', [Path('bad\0name.pdf')]])
def test_invalid_paths_do_not_acquire_clipboard(clipboard, paths):
    state, _ = clipboard
    with pytest.raises(ValueError):
        wechat_clipboard.copy_files(paths)
    assert state['attempts'] == 0 and state['data'] == {}
