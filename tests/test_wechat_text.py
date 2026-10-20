from types import SimpleNamespace

import pytest

from wechat_panel.wechat_text import pasted_text_matches


def test_plain_paste_does_not_touch_clipboard_or_desktop():
    assert pasted_text_matches(None,None,SimpleNamespace(window_text=lambda:'你好\r\n呀'),{},'你好\n呀')
    assert not pasted_text_matches(None,None,SimpleNamespace(window_text=lambda:'另一条文字'),{},'你好')


@pytest.mark.parametrize('copied,expected_result',[
    ('引用“跳一个[呲牙]”是什么意思？',True),
    ('引用“跳一个[微笑]”是什么意思？',False),
    ('引用“跳一个\ufffc”是什么意思？',False),
    ('其他窗口的内容',False),
])
def test_rich_paste_requires_fresh_exact_copyback_and_restores_clipboard(monkeypatch,copied,expected_result):
    import pyautogui,win32gui,win32clipboard as cb
    from wechat_panel import wechat_draft
    state={'sequence':10};actions=[];restored=[]
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:100)
    monkeypatch.setattr(win32gui,'GetAncestor',lambda handle,flag:handle)
    monkeypatch.setattr(cb,'GetClipboardSequenceNumber',lambda:state['sequence'])
    monkeypatch.setattr(cb,'OpenClipboard',lambda:None)
    monkeypatch.setattr(cb,'CloseClipboard',lambda:None)
    monkeypatch.setattr(cb,'GetClipboardData',lambda fmt:copied)
    monkeypatch.setattr(wechat_draft,'clipboard_read',lambda:[(13,'原剪贴板')])
    monkeypatch.setattr(wechat_draft,'clipboard_write',lambda value:restored.append(value))
    def hotkey(*keys,**kwargs):
        actions.append(keys)
        if keys==('ctrl','c'):state['sequence']+=1
    monkeypatch.setattr(pyautogui,'hotkey',hotkey)
    bridge=SimpleNamespace(_check_account=lambda c:None,_check_chat_type=lambda w,c:None,
        _title=lambda w:'测试群',_record=lambda *a,**k:None)
    edit=SimpleNamespace(window_text=lambda:'引用“跳一个\ufffc”是什么意思？')
    result=pasted_text_matches(bridge,SimpleNamespace(handle=100),edit,{'lookup':'测试群'},'引用“跳一个[呲牙]”是什么意思？')
    assert result is expected_result
    assert actions==[('ctrl','a'),('ctrl','c'),('ctrl','end')]
    assert restored==[[(13,'原剪贴板')]]
