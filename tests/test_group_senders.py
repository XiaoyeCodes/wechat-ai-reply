from types import SimpleNamespace

import pytest

from wechat_panel.group_senders import paired_senders, cached_sender
from wechat_panel.group_bridge import observe_group_row
from test_group_chats import groups


def row(rid, body, kind='ListItem', cls='mmui::ChatTextItemView'):
    return ((rid,), cls, kind, body)


def test_selection_adds_sender_to_same_unchanged_native_row():
    before = [row(1, '@机器人 你好'), row(2, '1')]
    selected = [row(1, 'Aria @机器人 你好', 'CheckBox'), row(2, '本人 1', 'CheckBox')]
    assert paired_senders(before, selected, before, ['Aria', '本人']) == {
        before[0]: 'Aria', before[1]: '本人'}


@pytest.mark.parametrize('selected,after,members', [
    ([row(1, '@机器人 你好', 'CheckBox')], [row(1, '@机器人 你好')], ['Aria']),
    ([row(1, 'Aria @机器人 你好')], [row(1, '@机器人 你好')], ['Aria']),
    ([row(2, 'Aria @机器人 你好', 'CheckBox')], [row(1, '@机器人 你好')], ['Aria']),
    ([row(1, 'Aria @机器人 你好', 'CheckBox')], [row(1, '被替换的正文')], ['Aria']),
    ([row(1, 'Aria @机器人 你好', 'CheckBox')], [row(2, '@机器人 你好')], ['Aria']),
    ([row(1, 'Aria @机器人 你好', 'CheckBox')], [row(1, '@机器人 你好')], ['Aria', 'Aria']),
    ([row(1, 'Aria @机器人 你好', 'CheckBox')]*2, [row(1, '@机器人 你好')], ['Aria']),
])
def test_selection_requires_independent_metadata_unique_member_and_stable_row(selected, after, members):
    assert not paired_senders([row(1, '@机器人 你好')], selected, after, members)


def test_body_cannot_impersonate_the_sender_or_lose_a_sender_shaped_prefix():
    body = 'Aria\n@机器人 这段文字来自另一个人'
    before = [row(1, body)]
    selected = [row(1, '李四 '+body, 'CheckBox')]
    proven = paired_senders(before, selected, before, ['Aria', '李四'])
    assert proven[before[0]] == '李四'
    node = SimpleNamespace(descendants=lambda: [], class_name=lambda: 'mmui::ChatTextItemView')
    evidence = observe_group_row(node, {'group_self_name':'机器人','group_self_name_verified':True},
                                 'incoming', body, ['Aria','李四'], proven[before[0]])
    assert evidence['sender'] == '李四' and evidence['text'] == body


def test_sticker_or_reference_identifies_sender_without_treating_quoted_at_as_request():
    cls='mmui::ChatBubbleReferItemView';body='引用 @机器人 你好'
    before=[row(1,body,cls=cls)];selected=[row(1,'Aria '+body,'CheckBox',cls)]
    proven=paired_senders(before,selected,before,['Aria'])
    assert proven[before[0]]=='Aria'
    node=SimpleNamespace(descendants=lambda:[],class_name=lambda:cls)
    evidence=observe_group_row(node,{'group_self_name':'机器人','group_self_name_verified':True},
        'incoming',body,['Aria'],'Aria')
    assert evidence['sender_verified'] and not evidence['mention_me']


def test_sender_cache_is_scoped_to_account_group_window_and_exact_body():
    contact = {'account_id':'self', 'wechat_id':'group:a'}
    value = row(1, '独立正文')
    node = SimpleNamespace(element_info=SimpleNamespace(runtime_id=(1,), control_type='ListItem'),
        class_name=lambda: value[1], window_text=lambda: value[3])
    bridge = SimpleNamespace(group_sender_cache={('self','group:a',100):{value:'Aria'}})
    assert cached_sender(bridge, SimpleNamespace(handle=100), contact, node) == 'Aria'
    assert not cached_sender(bridge, SimpleNamespace(handle=101), contact, node)
    assert not cached_sender(bridge, SimpleNamespace(handle=100), contact|{'wechat_id':'group:b'}, node)
    node.window_text = lambda: '另一条消息'
    assert not cached_sender(bridge, SimpleNamespace(handle=100), contact, node)


def test_recycled_row_with_same_body_has_different_fingerprint_when_member_header_changes():
    from PIL import Image, ImageDraw
    from wechat_panel.group_senders import header_fingerprint
    rect=SimpleNamespace(left=10,top=20,right=200,bottom=100)
    node=SimpleNamespace(rectangle=lambda:rect)
    clip=SimpleNamespace(left=0,top=0,right=250,bottom=150)
    image=Image.new('RGB',(250,150),'white')
    ImageDraw.Draw(image).rectangle((25,25,40,40),fill='blue')
    first=header_fingerprint(node,(image,(0,0),1),clip)
    ImageDraw.Draw(image).rectangle((25,25,40,40),fill='red')
    assert header_fingerprint(node,(image,(0,0),1),clip)!=first
    clip.top=30
    assert header_fingerprint(node,(image,(0,0),1),clip) is None


def test_native_sender_proof_survives_worker_restart_with_exact_session_row_and_header(tmp_path):
    from wechat_panel.group_senders import SenderLedger
    value=row(1,'@机器人 你好');pixels={value:b'original-header'}
    scope=repr(('self','group:a',100,200,12345.6))
    first=SenderLedger(tmp_path/'panel.sqlite3');first.save(scope,{value:'Aria'},pixels)
    restored=SenderLedger(tmp_path/'panel.sqlite3')
    assert restored.restore(scope,pixels,['Aria','本人'])=={value:'Aria'}
    assert not restored.restore(scope,pixels,['本人'])
    assert not restored.restore(scope,pixels,['Aria','Aria'])
    assert not restored.restore(scope,{value:b'changed-member-header'},['Aria'])
    assert not restored.restore(scope,{row(1,'另一条消息'):b'original-header'},['Aria'])
    assert not restored.restore(repr(('self','group:a',100,200,98765.4)),pixels,['Aria'])
    assert not restored.restore(repr(('other','group:a',100,200,12345.6)),pixels,['Aria'])
    assert not restored.restore(repr(('self','group:b',100,200,12345.6)),pixels,['Aria'])


def test_fresh_native_admission_is_not_history_when_sender_enrichment_changes_page_identity(groups):
    import json
    store,ai,_,contacts,_=groups;contact=contacts[0];cid=contact['id']
    old={'key':'old-unverified','kind':'text','direction':'incoming','text':'@机器人 历史问题',
        'sender':'','sender_verified':False,'identity_text':json.dumps(['',False,'@机器人 历史问题'])}
    store.ingest(cid,[old])
    with store.connect() as db:db.execute('UPDATE messages SET historical=1 WHERE contact_id=?',(cid,))
    proven={**old,'key':'old-verified','sender':'张三','sender_verified':True,'mention_me':True,'mention_verified':True,
        'identity_text':json.dumps(['张三',True,old['text']])}
    fresh={'key':'100:live:new-question','native_key':'100:42.7','kind':'text','direction':'incoming',
        'text':'@机器人 群聊恢复测试','sender':'张三','sender_verified':True,'mention_me':True,'mention_verified':True,
        'native_fresh':True,'identity_text':json.dumps(['张三',True,'@机器人 群聊恢复测试'])}
    for _ in range(4):store.ingest(cid,[proven,fresh]);ai._collect()
    actual=[m for m in store.messages(cid) if m['text']==fresh['text']]
    assert len(actual)==1 and not actual[0]['historical']
    runs=ai.data.runs(cid)
    assert len(runs)==1 and runs[0]['trigger_id']==actual[0]['id']
    assert all(m['historical'] for m in store.messages(cid) if m['text']==old['text'])
