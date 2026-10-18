from pathlib import Path
from types import SimpleNamespace
import pytest

from wechat_panel.wechat_files import send_pdf
from wechat_panel.bridge import SendUncertain


@pytest.mark.parametrize('mode', ['paste_sends', 'click_sends', 'unconfirmed'])
def test_pdf_sent_once_and_requires_new_outgoing_file(monkeypatch, tmp_path, mode):
    import pyautogui
    import win32gui
    from pyweixin import SystemSettings
    path = tmp_path / 'report.pdf'
    path.write_bytes(b'%PDF-1.4\n% test')
    state = {'pastes':0,'clicks':0,'sent':False}
    button = SimpleNamespace(window_text=lambda:'发送',is_enabled=lambda:True,
                             element_info=SimpleNamespace(control_type='Button'))
    label = SimpleNamespace(window_text=lambda:'report.pdf',element_info=SimpleNamespace(control_type='Text'))
    window = SimpleNamespace(handle=123, descendants=lambda:[label,button])
    edit = SimpleNamespace(window_text=lambda:'')
    old = dict(key='old',text='文件\nreport.pdf\n12 KB',kind='file',direction='outgoing')
    bridge = SimpleNamespace(_prepare_send=lambda *args:(window,edit,button,0),
        _check_account=lambda *args:None, _title=lambda *args:'friend',_record=lambda *args,**kw:None)
    bridge.snapshot = lambda c: [old]+([dict(old,key='new')] if state['sent'] else [])
    def paste(*args, **kwargs):
        state['pastes'] += 1
        if mode=='paste_sends':state['sent']=True
    def click(*args):
        state['clicks'] += 1
        if mode=='click_sends':state['sent']=True
    bridge._click=click
    monkeypatch.setattr(SystemSettings,'copy_files_to_clipboard',lambda **kw:None)
    monkeypatch.setattr(pyautogui,'hotkey',paste)
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:123)
    monkeypatch.setattr(win32gui,'GetAncestor',lambda h,f:h)
    monkeypatch.setattr('wechat_panel.wechat_files.time.sleep',lambda *args:None)
    if mode=='unconfirmed':
        with pytest.raises(SendUncertain):send_pdf(bridge,{'lookup':'friend'},path)
    else:assert send_pdf(bridge,{'lookup':'friend'},path)=='new'
    assert state['pastes']==1
    assert state['clicks']==(0 if mode=='paste_sends' else 1)
