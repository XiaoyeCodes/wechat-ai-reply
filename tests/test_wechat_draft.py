"""Exercise the send transaction without sending anything to real WeChat."""
import json
from types import SimpleNamespace

import pyautogui
import pytest
import win32gui

from wechat_panel import bridge as module
from wechat_panel import wechat_draft
from wechat_panel.ai_store import unprotect


def make_bridge(monkeypatch, tmp_path, failure=None):
    from pyweixin import SystemSettings
    original = '尚未发送的草稿📊\n第二行\n'
    state = dict(text=original, clipboard=[(13, '之前的剪贴板')], sent=[], selected=False,
                 alive=True, failure=failure, paste_count=0)
    edit = SimpleNamespace(exists=lambda **kw: True, window_text=lambda: state['text'])
    button = SimpleNamespace(exists=lambda **kw: True, is_enabled=lambda: failure != 'disabled')
    cancel = SimpleNamespace(exists=lambda **kw: False)
    window = SimpleNamespace(handle=123, exists=lambda **kw: True, class_name=lambda: 'mmui::ChatSingleWindow',
        child_window=lambda **kw: button if kw.get('class_name') == 'mmui::XOutlineButton' else cancel if 'title_re' in kw else edit)
    obj = module.WeixinBridge.__new__(module.WeixinBridge)
    obj.windows = {'c': window}
    obj.Edits = SimpleNamespace(CurrentChatEdit={})
    obj.trace_path = tmp_path / 'bridge.jsonl'
    obj._check_account = lambda c: None
    obj._title = lambda w: 'friend'
    obj._focus = lambda *args: None
    obj._visible_text_count = lambda w, c, t: sum(x['text'] == t for x in state['sent'])
    obj.snapshot = lambda c: state['sent']

    def click(control, contact, action):
        if control is button:
            if failure == 'unconfirmed':
                return
            state['sent'].append(dict(key=str(len(state['sent']) + 1), text=state['text'], direction='outgoing'))
            state['text'] = ''
            if failure == 'closed':
                state['alive'] = False

    obj._click = click

    def hotkey(*keys, **kw):
        if keys == ('ctrl', 'a'):
            state['selected'] = True
        elif keys == ('ctrl', 'c'):
            assert state['selected']
            state['clipboard'] = [(13, state['text']), (49301, b'<b>draft rich text</b>')]
        elif keys == ('ctrl', 'x'):
            assert state['selected']
            if failure == 'cut-failed':
                raise RuntimeError('Cut failed before modifying the input')
            state['clipboard'] = [(13, state['text']), (49301, b'<b>draft rich text</b>')]
            state['text'] = ''
            state['selected'] = False
        elif keys == ('ctrl', 'v'):
            state['paste_count'] += 1
            state['text'] += next(value for fmt, value in state['clipboard'] if fmt == 13)
            if state['paste_count'] == 1 and failure == 'paste-error':
                raise RuntimeError('Lost UIA after paste')
            if state['paste_count'] == 1 and failure == 'user-typing':
                state['text'] = '用户刚输入的新内容'
        else:
            pytest.fail(str(keys))

    monkeypatch.setattr(pyautogui, 'hotkey', hotkey)
    monkeypatch.setattr('wechat_panel.wechat_clipboard.copy_text', lambda text: state.update(clipboard=[(13, text)]))
    monkeypatch.setattr(wechat_draft, 'clipboard_read', lambda: list(state['clipboard']))
    monkeypatch.setattr(wechat_draft, 'clipboard_write', lambda values: state.update(clipboard=list(values)))
    monkeypatch.setattr(win32gui, 'GetForegroundWindow', lambda: 123)
    monkeypatch.setattr(win32gui, 'GetAncestor', lambda h, flag: h)
    monkeypatch.setattr(win32gui, 'IsWindow', lambda h: state['alive'])
    monkeypatch.setattr(module.time, 'sleep', lambda *args: None)
    contact = dict(id='c', lookup='friend', account_id='self')
    return obj, contact, state, original


def test_existing_multiline_draft_is_cut_and_restored_for_each_reply_part(monkeypatch, tmp_path):
    obj, contact, state, original = make_bridge(monkeypatch, tmp_path)
    for index, text in enumerate(['报告第一段', '报告第二段', '报告第三段'], 1):
        assert obj.send(contact, text) == str(index)
        assert state['text'] == original
        assert (49301, b'<b>draft rich text</b>') in state['clipboard']
    assert [x['text'] for x in state['sent']] == ['报告第一段', '报告第二段', '报告第三段']
    assert not list((tmp_path / 'draft-recovery').glob('*.json'))
    assert original not in obj.trace_path.read_text(encoding='utf-8')


def test_long_reply_queue_survives_trimmed_bubbles_and_restores_draft(monkeypatch, tmp_path):
    from wechat_panel.ai import AIService
    from wechat_panel.store import Store
    from wechat_panel.wechat_text import receipt_text
    obj, contact, state, original = make_bridge(monkeypatch, tmp_path)
    click = obj._click
    def wechat_click(control, c, action):
        click(control, c, action)
        if action == 'ui.send_once':
            state['sent'][-1]['text'] = receipt_text(state['sent'][-1]['text']).replace('\n', '\r\n')
    obj._click = wechat_click
    obj._visible_text_count = lambda w, c, t: sum(receipt_text(x['text']) == receipt_text(t) for x in state['sent'])
    store = Store(tmp_path / 'panel.sqlite3')
    ai = AIService(store)
    try:
        p = ai.data.save('providers', None, dict(name='test', base_url='http://localhost:19999/v1', model='test'))
        a = ai.data.save('agents', None, dict(name='native', kind='vibe', provider_id=p['id'], vibe_engine='sparkflow'))
        ai.data.settings({'enabled': True})
        c = store.add_contact(dict(lookup='friend', nickname='friend', wechat_id='friend', account_id='self'))
        store.update_contact(c['id'], listening=True, mode='ai_auto', ai_agent_id=a['id'])
        store.ingest(c['id'], [])
        store.ingest(c['id'], [dict(key='question', text='汇总', kind='text', direction='incoming')])
        incoming = store.messages(c['id'])[-1]
        r = ai.create_run(a['id'], incoming['text'], c['id'], incoming['id'])
        reply = '  ## 完整报告\n\n' + '保留段落与表格，数值不变。📊\n' * 350 + '\n'
        ai.finish(r['id'], reply)
        parts = store.pending()
        assert len(parts) >= 3
        for part in parts:
            assert store.ai_send_valid(part)
            store.set_message_state(part['id'], 'sending')
            key = obj.send(contact, part['text'])
            # The passive listener can observe a trimmed echo before confirmation.
            store.ingest(c['id'], state['sent'])
            store.set_message_state(part['id'], 'confirmed', source_key=key)
            assert state['text'] == original
        assert ai.data.run(r['id'])['status'] == 'sent'
        assert not store.pending()
        delivered = [m for m in store.messages(c['id']) if m['direction'] == 'outgoing']
        assert len(delivered) == len(parts)  # Exact source-key echo merged.
        assert ''.join(m['text'] for m in delivered) == reply
        assert all(m['status'] == 'confirmed' for m in delivered)
    finally:
        ai.stop()


@pytest.mark.parametrize('case', ['old', 'incoming', 'unknown', 'changed_body'])
def test_normalized_receipt_does_not_confirm_old_unverified_or_changed_text(monkeypatch, tmp_path, case):
    obj, contact, state, original = make_bridge(monkeypatch, tmp_path)
    reply = '## 标题\nA  B\n\n'
    if case == 'old':
        obj._visible_text_count = lambda *args: 1
    click = obj._click
    def wechat_click(control, c, action):
        click(control, c, action)
        if action == 'ui.send_once':
            state['sent'][-1]['text'] = reply.strip()
            if case in ('incoming', 'unknown'):
                state['sent'][-1]['direction'] = case
            if case == 'changed_body':
                state['sent'][-1]['text'] = '## 标题\nA B'
    obj._click = wechat_click
    with pytest.raises(module.SendUncertain, match='未能核对'):
        obj.send(contact, reply)
    assert len(state['sent']) == 1
    assert state['text'] == original


@pytest.mark.parametrize('failure', ['paste-error', 'disabled', 'cut-failed'])
def test_unsent_reply_failure_restores_original_draft(monkeypatch, tmp_path, failure):
    obj, contact, state, original = make_bridge(monkeypatch, tmp_path, failure)
    with pytest.raises(module.SendUncertain):
        obj.send(contact, '本次回复')
    assert not state['sent']
    assert state['text'] == original
    assert not list((tmp_path / 'draft-recovery').glob('*.json'))


@pytest.mark.parametrize('failure', ['closed', 'user-typing', 'unconfirmed'])
def test_interruption_preserves_encrypted_backup_without_overwriting_new_input(monkeypatch, tmp_path, failure):
    obj, contact, state, original = make_bridge(monkeypatch, tmp_path, failure)
    with pytest.raises(module.SendUncertain, match='恢复备份'):
        obj.send(contact, '本次回复')
    backups = list((tmp_path / 'draft-recovery').glob('*.json'))
    assert len(backups) == 1
    raw = backups[0].read_text(encoding='utf-8')
    assert original not in raw
    recovered = json.loads(unprotect(raw))
    assert recovered['text'] == original
    assert recovered['contact_id'] == 'c'
    assert len(recovered['formats']) == 2
    if failure == 'user-typing':
        assert state['text'] == '用户刚输入的新内容'
        assert not state['sent']
    elif failure == 'closed':
        assert len(state['sent']) == 1
    else:
        assert state['text'] == '本次回复'
