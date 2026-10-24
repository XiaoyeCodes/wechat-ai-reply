from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from wechat_panel.wechat_images import match_image, send_image
from wechat_panel.bridge import SendUncertain


def bubble(key,kind='image',direction='outgoing',**kw):
    return dict(key=key,text='图片' if kind=='image' else '文件',kind=kind,direction=direction,**kw)


def test_receipt_requires_unique_new_verified_outgoing_image():
    old=[bubble('old')]
    assert match_image(old,old) is None
    assert match_image(old,old+[bubble('new')])['key']=='new'
    assert match_image(old,[bubble('unrelated')]) is None
    assert match_image(old,old+[bubble('new',direction='incoming')]) is None
    assert match_image(old,old+[bubble('new',direction_verified=False)]) is None
    assert match_image(old,old+[bubble('new',kind='file')]) is None
    assert match_image(old,old+[bubble('a'),bubble('b')]) is None


@pytest.mark.parametrize('mode',['paste_sends','click_sends','inline','receipt_retry','late_preview','unconfirmed','wrong_file','no_preview','focus_changed','cancelled'])
def test_native_image_send_once(monkeypatch,mode):
    import pyautogui
    import win32gui
    from pyweixin import SystemSettings
    from wechat_panel.voice_reply import ReplySuperseded
    buf=BytesIO();Image.new('RGB',(16,16),'red').save(buf,format='PNG')
    state=dict(pastes=0,clicks=0,sent=False,checks=0,stages=[],name='',reads=0,retries=0)
    button=SimpleNamespace(window_text=lambda:'发送',is_enabled=lambda:True,
                           element_info=SimpleNamespace(control_type='Button'),class_name=lambda:'Button')
    label=SimpleNamespace(window_text=lambda:state['name'] if mode!='no_preview' and not (mode=='late_preview' and state['reads']<3) else 'unrelated',
                          element_info=SimpleNamespace(control_type='Text'),class_name=lambda:'Text')
    window=SimpleNamespace(handle=123,descendants=lambda:[label,button])
    edit=SimpleNamespace(window_text=lambda:'\ufffc' if mode=='inline' and state['pastes'] and not state['sent'] else '')
    bridge=SimpleNamespace(_prepare_send=lambda *args:(window,edit,button,0),
        _check_account=lambda *args:None,_title=lambda *args:'friend',_record=lambda *args,**kw:None,
        receipt_observer=lambda stage,*args:state['stages'].append(stage))
    def snapshot(c):
        state['reads']+=1
        if mode=='receipt_retry' and state['sent'] and state['retries']<2:
            state['retries']+=1
            error=RuntimeError('UIA element temporarily unavailable');error.hresult=-2147220991
            raise error
        return [bubble('old')]+([bubble('new',kind='file' if mode=='wrong_file' else 'image')] if state['sent'] else [])
    bridge.snapshot=snapshot
    def paste(*args,**kwargs):
        state['pastes']+=1
        if mode=='paste_sends':state['sent']=True
    def click(*args):
        state['clicks']+=1
        if mode in ('click_sends','inline','receipt_retry','late_preview','wrong_file'):state['sent']=True
    def guard():
        state['checks']+=1
        if mode=='cancelled' and state['pastes']:raise ReplySuperseded('cancelled')
    bridge._click=click
    from pathlib import Path
    monkeypatch.setattr('wechat_panel.wechat_clipboard.copy_files',lambda **kw:state.update(name=Path(kw['filepaths_list'][0]).name))
    monkeypatch.setattr(pyautogui,'hotkey',paste)
    monkeypatch.setattr(win32gui,'GetForegroundWindow',lambda:999 if mode=='focus_changed' and state['pastes'] else 123)
    monkeypatch.setattr(win32gui,'GetAncestor',lambda h,f:h)
    monkeypatch.setattr('wechat_panel.wechat_images.time.sleep',lambda *args:None)
    monkeypatch.setattr('wechat_panel.wechat_draft.clipboard_read',lambda:[])
    monkeypatch.setattr('wechat_panel.wechat_draft.clipboard_write',lambda values:None)
    if mode in ('paste_sends','click_sends','inline','receipt_retry','late_preview'):
        assert send_image(bridge,{'lookup':'friend'},buf.getvalue(),before_send=guard)=='new'
    else:
        with pytest.raises(ReplySuperseded if mode=='cancelled' else SendUncertain):
            send_image(bridge,{'lookup':'friend'},buf.getvalue(),before_send=guard)
    assert state['pastes']==1 and state['clicks']<=1
    assert state['stages']==['prepared','attempted']
    if mode in ('no_preview','focus_changed','cancelled','paste_sends'):assert state['clicks']==0
