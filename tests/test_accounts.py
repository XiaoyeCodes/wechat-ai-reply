from wechat_panel.store import Store


def add(store, account, lookup='同名联系人', wechat_id='shared-friend', **extra):
    contact = store.add_contact(dict(lookup=lookup, nickname=lookup, wechat_id=wechat_id,
                                     account_id=account))
    return store.update_contact(contact['id'], **extra) if extra else contact


def test_switch_loads_only_current_accounts_contacts_and_preserves_history(tmp_path):
    store = Store(tmp_path/'panel.sqlite3')
    first = add(store, 'first', listening=True)
    second = add(store, 'second', listening=True)
    message, _ = store.enqueue(first['id'], '旧账号草稿', 'old-account-request')
    store.activate_account('first', '第一个账号')
    assert [c['id'] for c in store.contacts()] == [first['id']]
    assert store.account_matches(first) and not store.account_matches(second)
    store.activate_account('second', '第二个账号')
    assert [c['id'] for c in store.contacts()] == [second['id']]
    assert store.message(message['id'])['status'] == 'cancelled'
    assert not store.ai_send_valid(message)
    store.activate_account('first', '第一个账号')
    assert store.contacts()[0]['listening']
    assert store.messages(first['id'])[0]['text'] == '旧账号草稿'
    assert len(store.contacts(all_accounts=True)) == 2
    assert {a['account_id'] for a in store.accounts()} == {'first', 'second'}


def test_unverified_login_and_new_account_have_empty_contact_lists(tmp_path):
    store = Store(tmp_path/'panel.sqlite3')
    old = add(store, 'old', listening=True)
    store.activate_account('')
    assert not store.contacts() and not store.account_matches(old)
    store.activate_account('new', '新账号')
    assert not store.contacts()
    new = add(store, 'new')
    assert store.contacts()[0]['id'] == new['id']
    assert len(store.contacts(all_accounts=True)) == 2


def test_switch_cancels_inbox_and_running_reply_instead_of_replaying_on_return(tmp_path):
    from wechat_panel.ai_store import AIStore
    store=Store(tmp_path/'panel.sqlite3')
    AIStore(store)
    contact=add(store,'first',listening=True)
    store.activate_account('first')
    with store.connect() as db:
        db.execute("INSERT INTO ai_runs(id,contact_id,agent_id,revision,mode,dry_run,input,status,state,created_at,updated_at) "
                   "VALUES('old-reply',?,'agent',0,'ai_auto',0,'测试','running','{}','now','now')",(contact['id'],))
        db.execute('INSERT INTO ai_inbox VALUES(?,1,0)',(contact['id'],))
    store.activate_account('second')
    with store.connect() as db:
        assert db.execute("SELECT status FROM ai_runs WHERE id='old-reply'").fetchone()[0]=='cancelled'
        assert not db.execute('SELECT 1 FROM ai_inbox').fetchone()
    store.activate_account('first')
    assert AIStore(store).run('old-reply')['status']=='cancelled'


def test_inactive_account_cannot_trigger_an_ai_reply(tmp_path):
    from wechat_panel.ai import AIService
    store = Store(tmp_path/'panel.sqlite3')
    old = add(store, 'old')
    store.activate_account('new')
    ai = AIService(store)
    try:
        import pytest
        with pytest.raises(ValueError, match='账号'):
            ai.create_run('unused-agent', '不要处理旧账号', old['id'])
    finally:
        ai.stop()


def test_api_adds_private_and_group_conversations_under_current_login(tmp_path):
    from fastapi.testclient import TestClient
    from wechat_panel.app import create_app
    from wechat_panel.group_bridge import group_key

    class Runtime:
        def __init__(self, store, demo=False):self.store=store;self.account='first'
        def start(self):self.store.activate_account(self.account, '当前用户')
        def stop(self):pass
        def configure(self):pass
        def health(self):return dict(state='ready',account_id=self.account,paused=False)
        def request(self,op,**data):
            profile=dict(account_id=self.account,lookup=data['lookup'],nickname=data['lookup'])
            if op=='inspect_group':
                return profile|dict(chat_type='group',wechat_id=group_key(self.account,data['lookup']),
                                    group_identity_verified=True,group_self_name='当前用户',group_self_name_verified=True)
            return profile|dict(wechat_id=data['wechat_id'])

    headers={'X-Panel-Request':'1'}
    with TestClient(create_app(tmp_path,runtime_factory=Runtime)) as client:
        first=client.post('/api/contacts',headers=headers,json=dict(lookup='好友',wechat_id='friend')).json()
        rt=client.app.state.runtime
        rt.account='second';rt.store.activate_account('second','另一个用户')
        assert client.get('/api/contacts').json()==[]
        for method,suffix,body in [('get','/messages',None),('post','/messages',dict(text='不能串发',client_key='wrong-account')),
                                   ('patch','',dict(listening=True)),('delete','',None)]:
            kwargs={'headers':headers}
            if body is not None:kwargs['json']=body
            result=getattr(client,method)('/api/contacts/'+first['id']+suffix,**kwargs)
            assert result.status_code==409,result.text
        second=client.post('/api/contacts',headers=headers,json=dict(lookup='好友',wechat_id='friend')).json()
        group=client.post('/api/contacts',headers=headers,json=dict(lookup='测试群',chat_type='group')).json()
        assert second['id']!=first['id'] and second['account_id']=='second'
        assert group['wechat_id']==group_key('second','测试群')
        assert len(client.get('/api/contacts').json())==2
        rt.account='first';rt.store.activate_account('first')
        assert [c['id'] for c in client.get('/api/bootstrap').json()['contacts']]==[first['id']]
        assert client.get('/api/contacts/'+second['id']+'/messages').status_code==409
