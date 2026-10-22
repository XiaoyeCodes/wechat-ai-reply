"""Group routing uses verified sender/mention data; all desktop/model work is fake."""
import itertools
import json
from types import SimpleNamespace
from queue import Queue
import threading
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from wechat_panel.ai import AIService
from wechat_panel.app import create_app
from wechat_panel.store import Store
from wechat_panel.snapshots import align, compare, identity


def group_item(key, sender, text='相同的群消息正文，足够长以检验控件重建时的安全对齐'):
    return {'key': key, 'kind': 'text', 'text': text, 'direction': 'incoming',
            'sender': sender, 'sender_verified': True, 'mention_me': True,
            'mention_verified': True, 'mention_source': 'uia_entity'}


def test_group_snapshot_identity_keeps_different_senders_distinct():
    alice = group_item('alice', '张三')
    bob = group_item('bob', '李四')
    assert identity(alice) != identity(bob)
    assert compare([alice], [alice, bob]).items == [bob]
    assert compare([alice, bob], [alice, bob]).items == []


def test_group_snapshot_recreated_controls_cannot_align_same_text_from_other_members():
    previous = [group_item('old-1', '张三', '第一个相当长且独立的群消息锚点' * 3),
                group_item('old-2', '张三', '第二个相当长且独立的群消息锚点' * 3)]
    current = [group_item('new-1', '李四', previous[0]['text']),
               group_item('new-2', '李四', previous[1]['text'])]
    aligned = align(previous, current)
    assert [m['key'] for m in aligned] == ['new-1', 'new-2']


def test_group_snapshot_verified_sender_changes_do_not_reuse_a_recycled_raw_id():
    old = group_item('recycled', '张三')
    new = group_item('recycled', '李四')
    delta = compare([old], [new])
    assert delta.items == [new] and delta.gap and delta.historical


class GroupNode:
    ids = itertools.count(1)

    def __init__(self, name='', kind='Text', aid='', cls='', children=(), visible=True):
        self.name, self.cls, self.children, self.visible = name, cls, list(children), visible
        self.element_info = SimpleNamespace(control_type=kind, automation_id=aid,
                                            runtime_id=(next(self.ids),))
        self.owner = None
        for child in self.children:child.owner = self

    def window_text(self):return self.name
    def class_name(self):return self.cls
    def is_visible(self):return self.visible
    def parent(self):return self.owner
    def descendants(self):
        return [item for child in self.children for item in [child, *child.descendants()]]


@pytest.mark.parametrize('initially_open', [True, False])
@pytest.mark.parametrize('panel_id,panel_class', [
    ('', 'mmui::ChatRoomMemberInfoView'), ('group_chat_info_view', 'mmui::XView'),
])
def test_group_inspection_reads_complete_settings_grid_without_member_picker(
        monkeypatch, initially_open, panel_id, panel_class):
    from wechat_panel import group_bridge
    members = ['本人', 'Aria', '另一位成员']
    panel = GroupNode(kind='Group', aid=panel_id, cls=panel_class, visible=initially_open,
        children=[GroupNode(name, kind='ListItem', cls='mmui::ChatMemberCell') for name in members])
    window = GroupNode(children=[panel])
    window.child_window = lambda **kwargs: object()
    actions = []

    def click(button, contact, action):
        actions.append(action)
        panel.visible = not panel.visible

    bridge = SimpleNamespace(
        _ensure_account=lambda expected: 'self', _search_unique=lambda *args, **kwargs: None,
        _find_dialog=lambda target: window, _title=lambda win: '测试群',
        Tools=SimpleNamespace(is_group_chat=lambda win: True),
        Buttons=SimpleNamespace(ChatInfoButton={}), _check_account=lambda target: None,
        _focus=lambda *args: None, _click=click, _record=lambda *args, **kwargs: None,
        profiles={}, verified_contacts=set(), group_rosters={})
    monkeypatch.setattr(group_bridge, 'group_total', lambda *args: 3)
    monkeypatch.setattr(group_bridge, 'read_member_list',
        lambda *args: pytest.fail('a complete settings grid must not open the member picker'))
    profile = group_bridge.inspect_group(bridge, '测试群', ['Aria'], '本人')
    assert profile['group_members_verified'] and profile['group_member_count'] == 3
    assert bridge.group_rosters[('self', profile['wechat_id'])] == members
    assert panel.visible == initially_open
    assert actions == ([] if initially_open else ['ui.group.info.open', 'ui.group.info.close'])


def test_group_panel_detection_rejects_multiple_visible_panels_and_ignores_hidden_ones():
    from wechat_panel.group_bridge import group_info_panel, GroupEvidenceError
    hidden = GroupNode(kind='Group', cls='mmui::ChatRoomMemberInfoView', visible=False)
    visible = GroupNode(kind='Group', cls='mmui::ChatRoomMemberInfoView')
    assert group_info_panel(GroupNode(children=[hidden, visible])) is visible
    with pytest.raises(GroupEvidenceError, match='不唯一'):
        group_info_panel(GroupNode(children=[visible,
            GroupNode(kind='Group', cls='mmui::ChatRoomMemberInfoView')]))


@pytest.mark.parametrize('total', [None, 0, 2, 4])
def test_group_grid_does_not_accept_a_missing_or_mismatched_member_count(total):
    from wechat_panel.group_bridge import roster
    panel = GroupNode(children=[GroupNode(name, cls='mmui::ChatMemberCell')
                               for name in ['本人', 'Aria', '另一位成员']])
    assert not roster(panel, total)[1]


def test_group_member_picker_reads_named_rows_without_keyboard_focus():
    from wechat_panel.group_bridge import member_list_roster, selected_members
    rows = [GroupNode(name, kind='ListItem', cls='mmui::XTableCell')
            for name in ['', 'Aria', '', '另一位成员', '', '本人', '']]
    listing = SimpleNamespace(children=lambda **kwargs: rows)
    names, complete = member_list_roster(listing, 3)
    assert names == ['Aria', '另一位成员', '本人'] and complete
    assert selected_members(names, complete, ['Aria'])
    assert not member_list_roster(listing, 4)[1]
    rows[-1] = GroupNode('Aria', kind='ListItem', cls='mmui::XTableCell')
    names, complete = member_list_roster(listing, 4)
    assert complete and not selected_members(names, complete, ['Aria'])
    rows[-1].visible = False
    assert not member_list_roster(listing, 4)[1]


def observed_row(body, *, sender='张三', children=(), row_kind='ListItem', row_class='mmui::chattextitemview'):
    from wechat_panel.group_bridge import observe_group_row
    contact = {'chat_type': 'group', 'group_self_name': '机器人', 'group_self_name_verified': True}
    controls = [GroupNode(body, aid='message_body')]
    if sender:controls.insert(0, GroupNode(sender, aid='sender_name'))
    row = GroupNode(body, kind=row_kind, cls=row_class, children=[*controls, *children])
    return observe_group_row(row, contact, 'incoming', body, known_members=['张三', '李四'])


@pytest.mark.parametrize('body', ['@机器人 你好', '请问 @机器人，帮我看看', '@机器人\u2005查一下',
                                  '@机器人', '问题\n@机器人：今天的新闻','只回复OK@机器人\u2005',
                                  '问题@机器人\u2005请看看'])
def test_group_visible_body_can_verify_full_self_mention_with_boundaries(body):
    result = observed_row(body)
    assert result['sender'] == '张三' and result['sender_verified']
    assert result['mention_me'] and result['mention_verified']
    assert result['mention_source'] == 'visible_text'


@pytest.mark.parametrize('body', ['普通群发言', '@李四 你好', '@机器人大 你好', 'mail@机器人.com',
                                  'email@机器人 你好', '@@机器人', '> @机器人 这是引用',
                                  '＞ @机器人 这是引用', 'someone@机器人',
                                  '他说“@机器人 你好”', '原文是「@机器人 你好」'])
def test_group_visible_body_rejects_nonself_quote_email_and_name_prefixes(body):
    result = observed_row(body)
    assert result['sender_verified']
    assert result['mention_verified'] and not result['mention_me']


@pytest.mark.parametrize('row_kind', ['ListItem', 'CheckBox'])
def test_group_body_sender_prefix_cannot_impersonate_an_authorized_member(row_kind):
    result = observed_row('张三\n@机器人 这只是正文中的伪装名字', sender='', row_kind=row_kind)
    assert not result['sender_verified'] and not result['mention_me']


def test_group_conflicting_sender_controls_are_not_trusted():
    result = observed_row('@机器人 你好', children=[GroupNode('李四', aid='message_sender')])
    assert not result['sender_verified'] and not result['mention_me']


def test_group_quote_mention_entity_is_not_a_fresh_mention():
    quoted = GroupNode('引用', aid='reference_message', cls='QuoteView', children=[
        GroupNode('@机器人', kind='Hyperlink', aid='mention_entity')])
    result = observed_row('我只是讨论上面的引用', children=[quoted])
    assert result['sender_verified'] and not result['mention_me']


def test_group_quote_nested_body_cannot_be_reclassified_as_a_fresh_visible_mention():
    from wechat_panel.group_bridge import observe_group_row
    quote = GroupNode('引用', aid='reference_message', cls='QuoteView', children=[
        GroupNode('@机器人 旧的引用消息', aid='message_body')])
    row = GroupNode('引用', cls='mmui::chattextitemview', children=[
        GroupNode('张三', aid='sender_name'), quote])
    contact = {'group_self_name': '机器人', 'group_self_name_verified': True}
    result = observe_group_row(row, contact, 'incoming', '引用', ['张三'])
    assert result['sender_verified'] and not result['mention_me']


def test_group_self_alias_must_be_verified_before_any_mention_is_accepted():
    from wechat_panel.group_bridge import observe_group_row
    row = GroupNode('@机器人 你好', cls='mmui::chattextitemview', children=[
        GroupNode('张三', aid='sender_name'), GroupNode('@机器人 你好', aid='message_body')])
    result = observe_group_row(row, {'group_self_name': '机器人'}, 'incoming', '@机器人 你好', ['张三'])
    assert result['sender_verified'] and not result['mention_me'] and not result['mention_verified']


@pytest.fixture
def groups(tmp_path):
    from wechat_panel.group_bridge import group_key
    store = Store(tmp_path / 'panel.sqlite3')
    calls = []

    def model(provider, messages, tools):
        calls.append(messages)
        return {'role': 'assistant', 'content': '群内收到，我来处理。'}, {}

    ai = AIService(store, model_call=model)
    provider = ai.data.save('providers', None, {'name': 'mock', 'model': 'mock', 'base_url': 'http://localhost:1'})
    agent = ai.data.save('agents', None, {'name': '群助手', 'provider_id': provider['id'], 'web_policy': 'off',
        'companion': {'memory': {'enabled': False},
                      'safety': {'emotion_enabled': False, 'crisis_detection': False}}})
    ai.data.settings({'enabled': True})
    contacts = []
    for lookup in ['测试群甲', '测试群乙']:
        contact = store.add_contact({'lookup': lookup, 'nickname': lookup, 'account_id': 'self',
            'wechat_id': group_key('self', lookup), 'chat_type': 'group',
            'group_members': ['张三'], 'group_mention_only': True, 'group_self_name': '机器人',
            'group_identity_verified': True, 'group_members_verified': True, 'group_self_name_verified': True})
        store.update_contact(contact['id'], listening=True, mode='ai_auto', ai_agent_id=agent['id'])
        store.ingest(contact['id'], [])
        contacts.append(store.contact(contact['id']))
    direct = store.add_contact({'lookup': '张三私聊', 'nickname': '张三', 'account_id': 'self', 'wechat_id': 'zhangsan'})
    store.update_contact(direct['id'], listening=True, mode='ai_auto', ai_agent_id=agent['id'])
    store.ingest(direct['id'], [])
    contacts.append(store.contact(direct['id']))
    yield store, ai, agent, contacts, calls
    ai.stop()


def incoming(groups, contact, text='@机器人 请处理当前问题', **changes):
    store, ai, _, _, _ = groups
    with store.connect() as db:
        row = db.execute('SELECT data FROM snapshots WHERE contact_id=?', (contact['id'],)).fetchone()
    previous = json.loads(row[0]) if row else []
    message = group_item('group-msg-' + str(len(previous)), '张三', text)
    message.update(changes)
    store.ingest(contact['id'], [*previous, message])
    return store.messages(contact['id'])[-1]


def group_runs(groups, contact):
    return groups[1].data.runs(contact['id'])


def test_verified_designated_member_mention_triggers_original_group_only(groups):
    store, ai, _, contacts, calls = groups
    message = incoming(groups, contacts[0])
    ai._collect()
    runs = group_runs(groups, contacts[0])
    assert len(runs) == 1
    run = ai.data.run(runs[0]['id'])
    assert run['trigger_id'] == message['id'] and run['state']['group_sender'] == '张三'
    ai.execute(run['id'])
    assert ai.data.run(run['id'])['status'] == 'queued' and len(calls) == 1
    assert {m['contact_id'] for m in store.pending()} == {contacts[0]['id']}
    assert all(store.ai_send_valid(m) for m in store.pending())
    ai._collect()
    assert len(group_runs(groups, contacts[0])) == 1


@pytest.mark.parametrize('changes', [
    {'sender': '李四'}, {'sender': ''}, {'sender_verified': False},
    {'mention_me': False}, {'mention_verified': False},
    {'direction': 'outgoing'}, {'direction': 'unknown'}, {'kind': 'system'}, {'text_pending': True},
])
def test_ineligible_group_messages_never_create_agent_runs(groups, changes):
    _, ai, _, contacts, calls = groups
    incoming(groups, contacts[0], **changes)
    ai._collect()
    assert not group_runs(groups, contacts[0]) and not calls


def test_initial_group_history_never_becomes_a_new_request(groups):
    store, ai, _, contacts, calls = groups
    cid = contacts[0]['id']
    with store.connect() as db:db.execute('DELETE FROM snapshots WHERE contact_id=?', (cid,))
    store.ingest(cid, [group_item('initial-history', '张三', '@机器人 历史消息')])
    ai._collect()
    assert not group_runs(groups, contacts[0]) and not calls


def test_verified_sender_and_mention_enrichment_triggers_once_without_duplicating_message(groups):
    store, ai, _, contacts, _ = groups
    cid = contacts[0]['id']
    message = group_item('clipped-group-message', '', '@机器人 后补证据的同一条新消息')
    message.update(sender_verified=False, mention_me=False, mention_verified=False)
    store.ingest(cid, [message])
    ai._collect()
    assert not group_runs(groups, contacts[0])
    verified = message | {'sender': '张三', 'sender_verified': True, 'mention_me': True,
                           'mention_verified': True}
    store.ingest(cid, [verified])
    ai._collect()
    assert len(group_runs(groups, contacts[0])) == 1
    store.ingest(cid, [verified])
    ai._collect()
    assert len(group_runs(groups, contacts[0])) == 1
    assert len(store.messages(cid)) == 1


def test_group_store_keeps_same_text_from_two_members_as_distinct_messages(groups):
    store, _, _, contacts, _ = groups
    cid = contacts[0]['id']
    first = group_item('same-raw-id', '张三')
    second = group_item('same-raw-id', '李四')
    store.ingest(cid, [first])
    store.ingest(cid, [first, second])
    messages = store.messages(cid)
    assert len(messages) == 2
    assert {m['sender'] for m in messages} == {'张三', '李四'}
    assert len({m['source_key'] for m in messages}) == 2


def test_unrelated_member_speech_does_not_cancel_or_enter_valid_task_context(groups):
    store, ai, _, contacts, calls = groups
    incoming(groups, contacts[0], '@机器人 AUTHORIZED_QUESTION')
    ai._collect()
    run = group_runs(groups, contacts[0])[0]
    incoming(groups, contacts[0], '@机器人 UNAUTHORIZED_PRIVATE_MARKER', sender='李四')
    ai._collect()
    ai.execute(run['id'])
    assert ai.data.run(run['id'])['status'] == 'queued'
    assert len(calls) == 1 and 'UNAUTHORIZED_PRIVATE_MARKER' not in json.dumps(calls, ensure_ascii=False)
    assert all(store.ai_send_valid(m) for m in store.pending())


def test_two_allowed_members_have_independent_runs_and_send_guards(groups):
    store, ai, _, contacts, calls = groups
    store.update_contact(contacts[0]['id'], group_members=[])
    incoming(groups, contacts[0], '@机器人 MEMBER_ONE')
    ai._collect()
    first = group_runs(groups, contacts[0])[0]
    incoming(groups, contacts[0], '@机器人 MEMBER_TWO', sender='李四')
    ai._collect()
    second = next(r for r in group_runs(groups, contacts[0]) if r['id'] != first['id'])
    ai.execute(first['id'])
    ai.execute(second['id'])
    assert len(calls) == 2 and len(store.pending()) == 2
    assert all(store.ai_send_valid(m) for m in store.pending())
    assert 'MEMBER_TWO' not in json.dumps(calls[0], ensure_ascii=False)
    assert 'MEMBER_ONE' not in json.dumps(calls[1], ensure_ascii=False)


def test_same_member_later_qualified_trigger_supersedes_only_their_old_run(groups):
    store, ai, _, contacts, _ = groups
    incoming(groups, contacts[0], '@机器人 OLD_REQUEST')
    ai._collect()
    first = group_runs(groups, contacts[0])[0]
    incoming(groups, contacts[0], '@机器人 NEW_REQUEST')
    ai._collect()
    second = next(r for r in group_runs(groups, contacts[0]) if r['id'] != first['id'])
    ai.execute(first['id'])
    ai.execute(second['id'])
    assert ai.data.run(first['id'])['status'] == 'cancelled'
    assert ai.data.run(second['id'])['status'] == 'queued'
    assert len(store.pending()) == 1


@pytest.mark.parametrize('changes', [
    {'group_members': ['李四']}, {'group_mention_only': False}, {'group_self_name': '新昵称'},
    {'listening': False}, {'mode': 'manual'}, {'ai_agent_id': ''},
])
def test_group_configuration_changes_invalidate_old_queued_replies(groups, changes):
    store, ai, _, contacts, _ = groups
    incoming(groups, contacts[0])
    ai._collect()
    run = group_runs(groups, contacts[0])[0]
    ai.execute(run['id'])
    queued = store.pending()[0]
    assert store.ai_send_valid(queued)
    store.update_contact(contacts[0]['id'], **changes)
    assert not store.ai_send_valid(queued)


def test_ai_assist_group_request_produces_draft_without_sending(groups):
    store, ai, _, contacts, _ = groups
    store.update_contact(contacts[0]['id'], mode='ai_assist')
    incoming(groups, contacts[0])
    ai._collect()
    run = group_runs(groups, contacts[0])[0]
    ai.execute(run['id'])
    assert ai.data.run(run['id'])['status'] == 'draft' and not store.pending()


def test_group_same_member_names_in_other_group_and_private_chat_have_isolated_memory_scopes(groups):
    store, ai, agent, contacts, _ = groups
    scopes = []
    for c in contacts[:2]:
        incoming(groups, c)
        ai._collect()
        scopes.append(ai.data.run(group_runs(groups, c)[0]['id'])['state']['scope'])
    private_scope = ai.companion.memory.scope(agent['id'], contacts[2]['id'])
    assert len(set([*scopes, private_scope])) == 3
    private = ai.companion.memory.profile(private_scope, agent['id'], contacts[2]['id'])
    ai.companion.memory.save_memory(private_scope, {'key': 'private', 'content': 'PRIVATE_DIRECT_MEMORY'})
    ai.execute(group_runs(groups, contacts[0])[0]['id'])
    assert 'PRIVATE_DIRECT_MEMORY' not in json.dumps(groups[-1], ensure_ascii=False)


def test_group_prompt_reads_only_same_group_same_member_memory(groups):
    from wechat_panel.group_chats import member_scope
    store, ai, agent, contacts, calls = groups
    body = {k: v for k, v in agent.items() if k not in ('id', 'key_configured')}
    body['companion']['memory'].update(enabled=True, auto_extract=False)
    ai.data.save('agents', agent['id'], body)
    memory = ai.companion.memory
    base_a = memory.scope(agent['id'], contacts[0]['id'])
    base_b = memory.scope(agent['id'], contacts[1]['id'])
    scopes = [(member_scope(base_a, '张三'), contacts[0], 'GROUP_A_ALICE'),
              (member_scope(base_a, '李四'), contacts[0], 'GROUP_A_BOB'),
              (member_scope(base_b, '张三'), contacts[1], 'GROUP_B_ALICE'),
              (memory.scope(agent['id'], contacts[2]['id']), contacts[2], 'PRIVATE_DIRECT_ALICE')]
    for scope, contact, marker in scopes:
        p = memory.profile(scope, agent['id'], contact['id'])
        memory.set_profile(scope, {**p['data'], 'memory_consent': True})
        memory.save_memory(scope, {'key': '宠物', 'content': '我的宠物猫叫' + marker, 'pinned': True})
    incoming(groups, contacts[0], '@机器人 我的宠物猫叫什么')
    ai._collect()
    ai.execute(group_runs(groups, contacts[0])[0]['id'])
    actual = json.dumps(calls, ensure_ascii=False)
    assert 'GROUP_A_ALICE' in actual
    assert all(marker not in actual for marker in ['GROUP_A_BOB', 'GROUP_B_ALICE', 'PRIVATE_DIRECT_ALICE'])


def test_agent_public_schedule_excludes_groups_even_when_they_listen_in_auto_mode(groups):
    _, ai, agent, contacts, _ = groups
    assert {c['id'] for c in ai.agent_scheduler.eligible(agent['id'])} == {contacts[2]['id']}


@pytest.mark.parametrize('value', [True, 12, {}, [' '], ['name\nspoof'], [12], ['x' * 129],
                                   [f'member-{i}' for i in range(51)]])
def test_invalid_group_member_configuration_is_rejected(value):
    from wechat_panel.group_chats import members
    with pytest.raises(ValueError):members(value)


def test_group_member_list_is_trimmed_deduplicated_and_empty_means_all():
    from wechat_panel.group_chats import members, qualifies
    assert members([' 张三 ', '张三', '李四']) == ['张三', '李四']
    assert qualifies({'chat_type': 'group', 'group_members': [], 'group_mention_only': True},
                     group_item('one', '任意已验证成员'))


def test_group_profile_rejects_wrong_account_name_type_or_unverified_member():
    from wechat_panel.group_bridge import GroupEvidenceError, group_key, validate_group_profile
    profile = {'lookup': '测试群甲', 'nickname': '测试群甲', 'chat_type': 'group',
        'wechat_id': group_key('self', '测试群甲'), 'account_id': 'self',
        'group_identity_verified': True, 'group_members_verified': True,
        'group_members': ['张三'], 'group_self_name_verified': True, 'group_self_name': '机器人'}
    assert validate_group_profile(profile, '测试群甲', 'self', ['张三'], '机器人') == profile
    for changes in [{'account_id': 'other'}, {'nickname': '同名私聊'}, {'chat_type': 'direct'},
                    {'group_identity_verified': False}, {'group_members_verified': False},
                    {'wechat_id': 'private-id'}, {'group_members': ['李四']}]:
        with pytest.raises(GroupEvidenceError):
            validate_group_profile(profile | changes, '测试群甲', 'self', ['张三'], '机器人')


class GroupApiRuntime:
    def __init__(self, store, demo=False):self.store, self.requests = store, []
    def start(self):pass
    def stop(self):pass
    def configure(self):pass
    def health(self):return {'state': 'ready', 'account_id': 'self'}

    def request(self, op, **kwargs):
        from wechat_panel.group_bridge import group_key
        self.requests.append((op, kwargs))
        lookup = kwargs.get('lookup') or kwargs.get('contact', {}).get('lookup')
        if op == 'inspect_group':
            members = kwargs.get('group_members', kwargs.get('members', []))
            own = kwargs.get('group_self_name', kwargs.get('self_name', '机器人'))
            return {'lookup': lookup, 'nickname': lookup, 'account_id': 'self', 'chat_type': 'group',
                    'wechat_id': group_key('self', lookup), 'group_identity_verified': True,
                    'group_members_verified': True, 'group_members': members,
                    'group_self_name': own, 'group_self_name_verified': True,
                    'group_self_name_source': 'user_config'}
        if op == 'inspect':
            return {'lookup': lookup, 'nickname': lookup, 'account_id': 'self',
                    'wechat_id': kwargs['wechat_id'], 'chat_type': 'direct'}
        raise AssertionError('Unexpected desktop operation: ' + op)


@pytest.fixture
def group_api(tmp_path, monkeypatch):
    monkeypatch.setattr(AIService, 'start', lambda self: None)
    with TestClient(create_app(tmp_path, runtime_factory=GroupApiRuntime,
            ai_model_call=lambda *args: pytest.fail('group CRUD must not call a model'))) as client:
        yield client


def test_group_api_create_requires_no_private_wechat_id_and_uses_group_inspection(group_api):
    client = group_api
    response = client.post('/api/contacts', headers={'X-Panel-Request': '1'}, json={
        'lookup': '测试群甲', 'chat_type': 'group', 'group_members': ['张三'],
        'group_mention_only': True, 'group_self_name': '机器人'})
    assert response.status_code == 201, response.text
    contact = response.json()
    assert contact['chat_type'] == 'group' and contact['group_members'] == ['张三']
    assert contact['group_mention_only'] and contact['group_self_name'] == '机器人'
    assert contact['wechat_id'].startswith('group:')
    assert client.app.state.runtime.requests[0][0] == 'inspect_group'
    assert client.get('/api/contacts').json()[0]['chat_type'] == 'group'


def test_private_contact_api_still_requires_wechat_id(group_api):
    client = group_api
    assert client.post('/api/contacts', headers={'X-Panel-Request': '1'},
                       json={'lookup': '张三', 'chat_type': 'direct'}).status_code == 422
    assert not client.app.state.runtime.requests


@pytest.mark.parametrize('changes', [
    {'lookup': ' '}, {'chat_type': 'channel'}, {'group_members': ['']}, {'group_members': '张三'},
    {'group_members': ['bad\nname']}, {'group_identity_verified': True},
])
def test_group_api_rejects_invalid_or_client_forged_configuration(group_api, changes):
    response = group_api.post('/api/contacts', headers={'X-Panel-Request': '1'}, json={
        'lookup': '测试群甲', 'chat_type': 'group', 'group_members': ['张三'],
        'group_mention_only': True, 'group_self_name': '机器人', **changes})
    assert 400 <= response.status_code < 500, response.text
    assert not group_api.app.state.runtime.requests


@pytest.mark.parametrize('chat_type,actual_group,key_ok', [
    ('group', False, True), ('direct', True, True), ('group', True, False),
])
def test_native_group_window_guard_rejects_direct_group_or_identity_confusion(chat_type, actual_group, key_ok):
    from wechat_panel.bridge import BridgeError, WeixinBridge
    from wechat_panel.group_bridge import group_key
    bridge = WeixinBridge.__new__(WeixinBridge)
    bridge.Tools = SimpleNamespace(is_group_chat=lambda window: actual_group)
    contact = {'chat_type': chat_type, 'account_id': 'self', 'lookup': '测试群甲',
                'wechat_id': group_key('self', '测试群甲') if key_ok else 'private-account-id'}
    with pytest.raises(BridgeError):bridge._check_chat_type(object(), contact)


def test_demo_group_bridge_preserves_verified_sender_and_mention_metadata():
    from wechat_panel.bridge import DemoBridge
    bridge = DemoBridge()
    contact = bridge.inspect_group('演示群', ['张三'], '机器人') | {'id': 'demo-group'}
    result = bridge.inject(contact, '@机器人 问题', sender='张三', mention_me=True)
    assert result['sender'] == '张三' and result['sender_verified']
    assert result['mention_me'] and result['mention_verified'] and result['mention_source'] == 'demo'
    assert bridge.snapshot(contact) == [result]


def test_serial_demo_worker_passes_group_inspection_and_trigger_metadata():
    from wechat_panel.bridge import DemoBridge
    from wechat_panel.runtime import worker_main
    bridge = DemoBridge()
    contact = bridge.inspect_group('演示群', ['张三'], '机器人') | {
        'id': 'worker-demo-group', 'listening': True, 'mode': 'ai_auto'}
    commands, output = Queue(), Queue()
    worker = threading.Thread(target=worker_main, args=(commands, output, True), daemon=True)
    worker.start()
    try:
        commands.put({'op': 'configure', 'contacts': [contact], 'paused': False})
        commands.put({'op': 'inspect_group', 'request_id': 'inspect-group', 'lookup': '演示群',
                      'group_members': ['张三'], 'group_self_name': '机器人'})
        commands.put({'op': 'inject', 'request_id': 'group-inject', 'contact_id': contact['id'],
                      'text': '@机器人 问题', 'sender': '张三', 'mention_me': True})
        results = {}
        while len(results) < 2:
            item = output.get(timeout=5)
            if item['kind'] == 'result' and item.get('request_id') in ('inspect-group', 'group-inject'):
                results[item['request_id']] = item
        assert all('result' in result and not result.get('error') for result in results.values()), results
        assert results['inspect-group']['result']['chat_type'] == 'group'
        message = results['group-inject']['result']
        assert message['sender'] == '张三' and message['sender_verified']
        assert message['mention_me'] and message['mention_verified']
    finally:
        commands.put({'op': 'stop'})
        worker.join(timeout=2)


@pytest.mark.parametrize('source_ok', [True, False])
def test_group_news_skill_fetches_before_image_and_delivers_only_to_original_group(groups, monkeypatch, source_ok):
    from wechat_panel.companion_services import Services
    from wechat_panel.image_delivery import deliver
    store, ai, agent, contacts, _ = groups
    skill = ai.data.save('skills', None, {'name': 'daily-news-60s', 'description': '每日新闻·60秒',
        'instructions': Path('skills/daily-news-60s/SKILL.md').read_text(encoding='utf-8')})
    tools = [tool for tool in ai.data.list('tools') if tool.get('builtin') in ('read_web', 'generate_image')]
    for tool in tools:
        body = {k: v for k, v in tool.items() if k not in ('id', 'key_configured')}
        ai.data.save('tools', tool['id'], body | {'approval': 'auto'})
    service = next(s for s in ai.data.list('services') if s['driver'] == 'codex_image')
    body = {k: v for k, v in agent.items() if k not in ('id', 'key_configured')}
    body['companion']['media']['image_service_id'] = service['id']
    body.update(tool_ids=[tool['id'] for tool in tools], skill_ids=[skill['id']], web_policy='model', max_tool_rounds=5)
    ai.data.save('agents', agent['id'], body)
    buffer = BytesIO()
    Image.new('RGB', (90, 160), 'navy').save(buffer, format='PNG')
    png, steps, rounds = buffer.getvalue(), [], []

    def fetch(self, sid, url):
        assert steps == ['skill']
        steps.append('fetch')
        if not source_ok:raise RuntimeError('新闻源暂时不可用')
        return json.dumps({'url': url, 'date': '2026-10-19', 'news': ['GROUP_VERIFIED_NEWS']})

    def generate(self, sid, prompt, *args, **kwargs):
        assert source_ok and steps == ['skill', 'fetch'] and 'GROUP_VERIFIED_NEWS' in prompt
        steps.append('image')
        return png

    monkeypatch.setattr(Services, 'fetch', fetch)
    monkeypatch.setattr(Services, 'generate_image', generate)

    def model(provider, messages, specs):
        rounds.append(messages)
        count = len(rounds)
        if count == 1:
            assert not steps
            steps.append('skill')
            name, args = 'use_skill', {'name': 'daily-news-60s'}
        elif count == 2:
            assert 'daily-news-60s' in messages[-1]['content']
            name, args = 'read_web', {'url': 'https://60s.viki.moe/v2/60s'}
        elif count == 3 and source_ok:
            assert 'GROUP_VERIFIED_NEWS' in messages[-1]['content']
            name, args = 'generate_image', {'prompt': '9:16新闻卡 GROUP_VERIFIED_NEWS', 'mode': 'generate'}
        elif not source_ok:
            assert steps == ['skill', 'fetch'] and '暂时不可用' in messages[-1]['content']
            return {'role': 'assistant', 'content': '新闻来源暂时不可用，本次没有生成新闻图片。'}, {}
        else:
            assert steps == ['skill', 'fetch', 'image']
            return {'role': 'assistant', 'content': '新闻卡片已生成。'}, {}
        assert name in {spec['function']['name'] for spec in specs}
        return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'group-news-' + str(count),
            'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, {}

    ai.model_call = model
    incoming(groups, contacts[0], '@机器人 执行每日新闻60秒生成卡片')
    ai._collect()
    run = group_runs(groups, contacts[0])[0]
    ai.execute(run['id'])
    result = ai.data.run(run['id'])
    assert result['status'] == 'queued', result['error']
    queued = store.pending()
    assert len(queued) == 1 and queued[0]['contact_id'] == contacts[0]['id']
    assert not store.messages(contacts[1]['id']) and not store.messages(contacts[2]['id'])
    if not source_ok:
        assert steps == ['skill', 'fetch'] and len(rounds) == 3
        assert queued[0]['kind'] == 'text' and queued[0]['text']=='我遇到点问题，未能完成该任务。'
        assert not result['state'].get('artifacts')
        return
    assert steps == ['skill', 'fetch', 'image'] and len(rounds) == 4
    assert queued[0]['kind'] == 'image' and queued[0]['media_id']
    captured = []

    class GroupImageBridge:
        def send_image(self, contact, data, before_send):
            before_send()
            captured.append((contact['id'], data))
            return 'fake-original-group-image-receipt'

    receipt = deliver(GroupImageBridge(), store, queued[0], store.contact(contacts[0]['id']))
    assert receipt['delivery'] == 'image' and captured == [(contacts[0]['id'], png)]


def test_same_group_members_have_distinct_memory_scopes_and_contexts(groups):
    store, ai, agent, contacts, calls = groups
    store.update_contact(contacts[0]['id'], group_members=[])
    incoming(groups, contacts[0], '@机器人 ALICE_ONLY_CONTEXT')
    ai._collect()
    alice = group_runs(groups, contacts[0])[0]
    ai.execute(alice['id'])
    incoming(groups, contacts[0], '@机器人 BOB_ONLY_CONTEXT', sender='李四')
    ai._collect()
    bob = next(r for r in group_runs(groups, contacts[0]) if r['id'] != alice['id'])
    ai.execute(bob['id'])
    alice_state, bob_state = ai.data.run(alice['id'])['state'], ai.data.run(bob['id'])['state']
    assert alice_state['scope'] != bob_state['scope']
    assert alice_state['group_sender'] == '张三' and bob_state['group_sender'] == '李四'
    assert 'ALICE_ONLY_CONTEXT' not in json.dumps(bob_state['context'], ensure_ascii=False)
    assert 'BOB_ONLY_CONTEXT' not in json.dumps(alice_state['context'], ensure_ascii=False)
    assert 'ALICE_ONLY_CONTEXT' not in json.dumps(calls[-1], ensure_ascii=False)


def test_group_filters_personal_reminders_and_skips_automatic_tts(groups, monkeypatch):
    from wechat_panel.companion_services import Services
    store, ai, agent, contacts, _ = groups
    reminder = next(t for t in ai.data.list('tools') if t.get('builtin') == 'set_reminder')
    tts = ai.data.save('services', None, {'name': 'fake tts', 'category': 'tts', 'driver': 'minimax',
                                         'url': 'http://localhost:1', 'voice_id': 'fake'})
    body = {k: v for k, v in agent.items() if k not in ('id', 'key_configured')}
    body['companion']['media'].update(auto_voice=True, tts_service_id=tts['id'])
    body['tool_ids'] = [reminder['id']]
    ai.data.save('agents', agent['id'], body)
    monkeypatch.setattr(Services, 'tts', lambda *args, **kwargs: pytest.fail('group may not auto-synthesize voice'))
    incoming(groups, contacts[0], '@机器人 你好')
    ai._collect()
    run = group_runs(groups, contacts[0])[0]
    snapshot_tools = ai.data.run(run['id'])['state']['snapshot']['tools']
    assert reminder['id'] not in snapshot_tools
    ai.execute(run['id'])
    assert ai.data.run(run['id'])['status'] == 'queued'
    assert len(store.pending()) == 1 and store.pending()[0]['kind'] == 'text'


def test_contact_care_schedule_api_rejects_group_targets(group_api):
    client, headers = group_api, {'X-Panel-Request': '1'}
    group = client.post('/api/contacts', headers=headers, json={'lookup': '测试群甲', 'chat_type': 'group',
        'group_members': ['张三'], 'group_self_name': '机器人'}).json()
    ai = client.app.state.ai
    provider = ai.data.save('providers', None, {'name': 'mock', 'model': 'mock', 'base_url': 'http://localhost:1'})
    agent = ai.data.save('agents', None, {'name': 'mock', 'provider_id': provider['id']})
    schedule = {'agent_id': agent['id'], 'contact_id': group['id'], 'title': '不应给群创建个人关怀',
        'instruction': '晚安', 'kind': 'once', 'at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
    response = client.post('/api/ai/schedules', headers=headers, json=schedule)
    assert response.status_code == 409 and '私聊' in response.json()['detail']
    assert not ai.companion.memory.schedules()
