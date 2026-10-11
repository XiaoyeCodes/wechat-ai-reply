import pytest

from wechat_panel.bridge import validate_identity, parse_sender, BridgeError
from wechat_panel.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'test.db')


@pytest.fixture
def contact(store):
    return store.add_contact(dict(lookup='唯一备注', wechat_id='expected', nickname='昵称', account_id='self'))


def test_contact_identity_checks_wechat_id_and_logged_in_account():
    profile = dict(lookup='唯一备注', wechat_id='wrong', account_id='self')
    with pytest.raises(BridgeError):
        validate_identity(profile, '唯一备注', 'expected')
    profile['wechat_id'] = 'expected'
    with pytest.raises(BridgeError):
        validate_identity(profile, '唯一备注', 'expected', 'other-account')


def test_multiselect_sender_prefix_is_explicit_and_unambiguous():
    friends = {'Demo Friend'}
    assert parse_sender('Demo Friend 测试回复', friends, '演示自己') == ('incoming', '测试回复')
    assert parse_sender('演示自己\n你好', friends, '演示自己') == ('outgoing', '你好')
    assert parse_sender('陌生人 你好', friends, '演示自己')[0] == 'unknown'
    assert parse_sender('同名 你好', {'同名'}, '同名')[0] == 'unknown'
    assert parse_sender('A B 你好', {'A'}, 'A B')[0] == 'unknown'


def test_send_idempotency_and_listen_only(store, contact):
    first, created = store.enqueue(contact['id'], '你好', 'same-request')
    second, created_twice = store.enqueue(contact['id'], '你好', 'same-request')
    assert created and not created_twice and first['id'] == second['id']
    with pytest.raises(ValueError):
        store.enqueue(contact['id'], '另一条消息', 'same-request')
    store.update_contact(contact['id'], mode='listen_only')
    with pytest.raises(ValueError):
        store.enqueue(contact['id'], '你好', 'new-request')


def test_history_and_echo_do_not_become_agent_requests(store, contact):
    item = lambda k,d='incoming': dict(key=k, text='你好', direction=d, kind='text')
    store.ingest(contact['id'], [item('1')])
    assert store.events(agent_only=True) == []
    store.ingest(contact['id'], [item('1'), item('2'), item('3','outgoing')])
    store.ingest(contact['id'], [item('1'), item('2'), item('3','outgoing')])
    assert len(store.messages(contact['id'])) == 3
    assert len(store.events(agent_only=True)) == 1
    assert store.contact(contact['id'])['unread'] == 1


def test_restart_does_not_resend_inflight_messages(store, contact):
    pending, _ = store.enqueue(contact['id'], '待发送', 'one')
    sending, _ = store.enqueue(contact['id'], '发送中', 'two')
    store.set_message_state(sending['id'], 'sending')
    store.recover()
    assert store.message(pending['id'])['status'] == 'cancelled'
    assert store.message(sending['id'])['status'] == 'uncertain'
    assert store.pending() == []


def test_delete_contact_removes_local_history(store, contact):
    store.enqueue(contact['id'], '消息', 'id')
    store.delete_contact(contact['id'])
    assert store.contacts() == []
    with pytest.raises(KeyError):
        store.messages(contact['id'])


def test_snapshot_echo_before_send_confirmation_merges_exact_source_only(store, contact):
    cid = contact['id']
    store.ingest(cid, [])
    queued, _ = store.enqueue(cid, '同一句话', 'sent-request')
    store.set_message_state(queued['id'], 'sending')
    items = [dict(key=key, text='同一句话', direction='outgoing', kind='text')
             for key in ('manual-outgoing', 'confirmed-source')]
    store.ingest(cid, items)
    assert len(store.messages(cid)) == 3
    store.set_message_state(queued['id'], 'confirmed', source_key='confirmed-source')
    messages = store.messages(cid)
    assert len(messages) == 2
    assert store.message(queued['id'])['status'] == 'confirmed'
    assert {m['source_key'] for m in messages} == {'manual-outgoing', 'confirmed-source'}
    assert store.events()[-2]['type'] == 'message.removed'
    store.ingest(cid, items)
    assert len(store.messages(cid)) == 2


def test_empty_window_does_not_replay_old_messages(store, contact):
    item = dict(key='old', text='原有消息', direction='incoming', kind='text')
    store.ingest(contact['id'], [item])
    store.ingest(contact['id'], [])
    store.ingest(contact['id'], [item])
    assert len(store.messages(contact['id'])) == 1
    assert store.events(agent_only=True) == []


def test_live_unknown_sender_is_resolved_once_without_duplicate_message(store, contact):
    cid=contact['id']
    store.ingest(cid,[])
    item=dict(key='clipped',text='自动回复测试',kind='text',direction='unknown')
    store.ingest(cid,[item])
    message=store.messages(cid)[0]
    assert not store.events(agent_only=True)
    resolved={**item,'direction':'incoming','direction_verified':True}
    store.ingest(cid,[resolved]);store.ingest(cid,[resolved])
    assert len(store.messages(cid))==1
    assert store.message(message['id'])['direction']=='incoming'
    assert store.contact(cid)['unread']==1
    assert [e['payload']['message_id'] for e in store.events(agent_only=True)]==[message['id']]
    # Later clipping must not downgrade the identified sender.
    store.ingest(cid,[item])
    assert store.message(message['id'])['direction']=='incoming'


def test_historical_sender_correction_never_replays_agent_tasks(store, contact):
    cid=contact['id']
    item=dict(key='history',text='旧消息',kind='text',direction='unknown')
    store.ingest(cid,[item])
    store.ingest(cid,[{**item,'direction':'incoming','direction_verified':True}])
    assert store.messages(cid)[0]['direction']=='incoming'
    assert store.messages(cid)[0]['historical']
    assert not store.events(agent_only=True) and store.contact(cid)['unread']==0


def test_physical_pixel_evidence_corrects_legacy_sender_once(store, contact):
    cid=contact['id'];store.ingest(cid,[])
    legacy=dict(key='old-dpi',text='本人发送',kind='text',direction='incoming')
    store.ingest(cid,[legacy])
    item={**legacy,'direction':'outgoing','direction_verified':True}
    store.ingest(cid,[item])
    assert store.messages(cid)[0]['direction']=='outgoing'
    assert store.messages(cid)[0]['direction_verified']
    assert store.contact(cid)['unread']==0
    for incorrect in [legacy,{**legacy,'direction_verified':True}]:
        store.ingest(cid,[incorrect])
        assert store.messages(cid)[0]['direction']=='outgoing'
