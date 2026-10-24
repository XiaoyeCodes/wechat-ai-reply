from types import SimpleNamespace
import importlib.util
from pathlib import Path

import pytest

from wechat_panel.bridge import BridgeError, WeixinBridge


def row(title,cls='mmui::XTableCell'):
    return SimpleNamespace(window_text=lambda:title,class_name=lambda:cls)


def test_file_helper_is_selected_from_features_never_web_or_named_contact():
    web=row('文件传输助手');friend=row('文件传输助手','mmui::SearchContentCellView');helper=row('文件传输助手')
    rows=[row('搜索网络结果'),web,friend,row('功能'),helper]
    assert WeixinBridge._search_matches(rows,'文件传输助手')==[helper]
    assert WeixinBridge._search_matches(rows[:3],'文件传输助手')==[]
    duplicate=row('文件传输助手')
    assert WeixinBridge._search_matches(rows+[duplicate],'文件传输助手')==[helper,duplicate]


def test_regular_contacts_and_groups_keep_exact_native_result_selection():
    friend=row('张三','mmui::SearchContentCellView')
    assert WeixinBridge._search_matches([row('张三'),friend],'张三')==[friend]
    assert WeixinBridge._search_matches([row('功能'),row('文件传输助手')],'文件传输助手','group')==[]


@pytest.mark.parametrize('heading',['搜索网络结果','联系人','Search web','Chat History','其他分类'])
def test_file_helper_features_scope_ends_before_next_section(heading):
    web=row('文件传输助手');helper=row('文件传输助手')
    assert WeixinBridge._search_matches([row('功能'),row(heading),web],'文件传输助手')==[]
    assert WeixinBridge._search_matches([row('功能'),helper,row(heading),web],'文件传输助手')==[helper]


def self_test_guard():
    spec=importlib.util.spec_from_file_location('verify_self_delivery',
        Path(__file__).resolve().parents[1]/'scripts/verify-self-delivery.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_self_target


def test_self_test_refuses_existing_same_title_window_before_any_input():
    calls=[]
    bridge=SimpleNamespace(_check_account=lambda contact:calls.append('account'),
        _find_dialog=lambda contact:object())
    contact=dict(lookup='文件传输助手',wechat_id='filehelper',chat_type='direct',account_id='self')
    with pytest.raises(RuntimeError,match='已有同名独立窗口'):
        self_test_guard()(bridge,contact)
    assert calls==['account']


def test_self_test_requires_verified_account_and_no_existing_dialog():
    calls=[]
    bridge=SimpleNamespace(_check_account=lambda contact:calls.append('account'),
        _find_dialog=lambda contact:calls.append('lookup'))
    contact=dict(lookup='文件传输助手',wechat_id='filehelper',chat_type='direct',account_id='self')
    self_test_guard()(bridge,contact)
    assert calls==['account','lookup']
    bridge._check_account=lambda contact:(_ for _ in ()).throw(RuntimeError('account mismatch'))
    with pytest.raises(RuntimeError,match='account mismatch'):
        self_test_guard()(bridge,contact)
    assert calls==['account','lookup']


def test_worker_refuses_same_title_window_opened_after_self_test_preflight():
    calls=[]
    bridge=WeixinBridge.__new__(WeixinBridge)
    bridge.windows={}
    bridge._window_alive=lambda window:False
    bridge._ensure_account=lambda account:calls.append('ensure')
    bridge._check_account=lambda contact:calls.append('account')
    bridge._find_dialog=lambda contact:object()
    bridge._open_dialog=lambda contact:pytest.fail('Refused target must not cause desktop input')
    contact=dict(id='self-test',lookup='文件传输助手',wechat_id='filehelper',chat_type='direct',account_id='self')
    with pytest.raises(BridgeError,match='未绑定的同名窗口'):
        bridge.attach(contact)
    assert calls==['ensure','account']


@pytest.mark.parametrize('field,value',[('lookup','测试好友'),('wechat_id','friend'),('chat_type','group')])
def test_self_test_refuses_non_builtin_target(field,value):
    contact=dict(lookup='文件传输助手',wechat_id='filehelper',chat_type='direct',account_id='self')
    contact[field]=value
    with pytest.raises(RuntimeError,match='自测目标必须是内置'):
        self_test_guard()(SimpleNamespace(),contact)
