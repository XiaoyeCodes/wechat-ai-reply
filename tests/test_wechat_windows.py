from types import SimpleNamespace

import psutil
import pytest
import win32gui
import win32process
from pywinauto import uia_element_info

from wechat_panel.wechat_windows import discover_weixin_windows


@pytest.fixture
def native(monkeypatch):
    # Same native Qt class/title can refer to several different UIA roles.
    rows={
        10:dict(pid=100,name='Weixin.exe',kind='mmui::MainWindow',started=1),
        11:dict(pid=100,name='Weixin.exe',kind='mmui::ChatSingleWindow',started=1),
        12:dict(pid=100,name='Weixin.exe',kind='Qt51514WxTrayIconMessageWindowClass',started=1),
        20:dict(pid=200,name='Other.exe',kind='mmui::MainWindow',started=2),
        30:dict(pid=300,name='Weixin.exe',kind='mmui::LoginWindow',started=3),
    }
    monkeypatch.setattr(win32gui,'EnumWindows',lambda callback,arg:[callback(h,arg) for h in list(rows)])
    monkeypatch.setattr(win32gui,'IsWindow',lambda h:h in rows)
    monkeypatch.setattr(win32process,'GetWindowThreadProcessId',lambda h:(1,rows[h]['pid']))
    def process(pid):
        r=next((v for v in rows.values() if v['pid']==pid),None)
        if r is None:raise psutil.NoSuchProcess(pid)
        return SimpleNamespace(name=lambda:r['name'],create_time=lambda:r['started'])
    monkeypatch.setattr(psutil,'Process',process)
    monkeypatch.setattr(uia_element_info,'UIAElementInfo',lambda h:SimpleNamespace(class_name=rows[h]['kind'],process_id=rows[h]['pid']))
    # Discovery itself must not activate windows or read their geometry.
    monkeypatch.setattr(win32gui,'ShowWindow',lambda *a:pytest.fail('Read-only discovery'))
    monkeypatch.setattr(win32gui,'SetForegroundWindow',lambda *a:pytest.fail('Read-only discovery'))
    return rows


def test_native_discovery_finds_hidden_main_without_desktop_tree_or_account_cache(native):
    assert discover_weixin_windows()==[
        dict(handle=10,pid=100,started_at=1,kind='mmui::MainWindow'),
        dict(handle=30,pid=300,started_at=3,kind='mmui::LoginWindow')]


def test_restart_discards_old_process_and_finds_new_main(native):
    discover_weixin_windows()
    del native[10]
    native[40]=dict(pid=400,name='Weixin.exe',kind='mmui::MainWindow',started=40)
    found=discover_weixin_windows()
    assert [w['handle'] for w in found if w['kind']=='mmui::MainWindow']==[40]


@pytest.mark.parametrize('race',['destroyed','pid-reused','process-restarted','uia-wrong-process'])
def test_discovery_rejects_destroyed_or_reused_handles(native,monkeypatch,race):
    def element(h):
        r=native[h];pid=r['pid'];kind=r['kind']
        if h==10:
            if race=='destroyed':del native[h]
            elif race=='pid-reused':r['pid']=999
            elif race=='process-restarted':r['started']=999
            elif race=='uia-wrong-process':pid=999
        return SimpleNamespace(class_name=kind,process_id=pid)
    monkeypatch.setattr(uia_element_info,'UIAElementInfo',element)
    assert all(w['handle']!=10 for w in discover_weixin_windows())


def test_one_inaccessible_process_does_not_hide_current_weixin(native,monkeypatch):
    original=psutil.Process
    def process(pid):
        if pid==200:raise psutil.AccessDenied(pid)
        return original(pid)
    monkeypatch.setattr(psutil,'Process',process)
    assert any(w['handle']==10 for w in discover_weixin_windows())
