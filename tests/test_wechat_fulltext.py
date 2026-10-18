from types import SimpleNamespace

import pytest

from wechat_panel import wechat_fulltext as fulltext
from wechat_panel.snapshots import compare
from wechat_panel.store import Store
from wechat_panel.ai import AIService

PREVIEW = '1.58 复制...更多'
BODY = '1.58 复制打开抖音，看看作品 https://v.douyin.com/example/'


@pytest.mark.parametrize('text', [PREVIEW, '长消息…更多', 'Long message...More', '長訊息…顯示更多'])
def test_folded_markers(text):
    assert fulltext.folded(text)


@pytest.mark.parametrize('text', ['更多', '想了解更多', '省略号...', 'https://example.com/more', BODY])
def test_ordinary_text_is_not_folded(text):
    assert not fulltext.folded(text)


def test_copy_requires_complete_matching_prefix():
    assert fulltext.validate_fulltext(PREVIEW, BODY) == BODY
    for bad in [None, '', PREVIEW, '1.58 复制', '其他窗口的剪贴板内容']:
        with pytest.raises(ValueError):fulltext.validate_fulltext(PREVIEW, bad)


def item(key='one', pending=True, direction='incoming'):
    return dict(key=key, text=PREVIEW if pending else BODY, identity_text=PREVIEW,
                text_pending=pending, direction=direction, kind='text', account_id='self')


def test_enrichment_preserves_identity_and_distinct_repeated_messages():
    delta=compare([item()], [item(pending=False), item('two')])
    assert delta.items == [item('two')] and not delta.gap and not delta.historical


def test_cache_is_per_message_and_copy_is_bounded_per_poll(monkeypatch):
    bridge=SimpleNamespace(_record=lambda *a,**k:None)
    calls=[]
    monkeypatch.setattr(fulltext,'copy_text',lambda *args:calls.append(args[-2]) or BODY)
    first,second=item(),item('two')
    fulltext.hydrate(bridge,None,{},[(first,(1,)),(second,(2,))])
    assert first['text']==BODY and not first['text_pending'] and second['text_pending']
    first,second=item(),item('two')
    fulltext.hydrate(bridge,None,{},[(first,(1,)),(second,(2,))])
    assert calls==[(1,),(2,)] and second['text']==BODY


def test_copy_failure_keeps_preview_out_of_ai_without_retry(monkeypatch):
    bridge=SimpleNamespace(_record=lambda *a,**k:None)
    def fail(*args):raise ValueError('not ready')
    monkeypatch.setattr(fulltext,'copy_text',fail)
    first=item();fulltext.hydrate(bridge,None,{},[(first,(1,))])
    assert first['text_pending'] and first['text_error']
    monkeypatch.setattr(fulltext,'copy_text',lambda *args:pytest.fail('Do not retry a failed copy'))
    second=item();fulltext.hydrate(bridge,None,{},[(second,(1,))])
    assert second['text_pending'] and second['text']==PREVIEW


@pytest.mark.parametrize('success',[True,False])
def test_copy_attempt_survives_worker_restart_and_cache_eviction(env,monkeypatch,success):
    store,cid=env;contact=store.contact(cid);calls=[]
    def copy(*args):
        calls.append(1)
        if not success:raise ValueError('failure')
        return BODY
    monkeypatch.setattr(fulltext,'copy_text',copy)
    for _ in range(4):
        bridge=SimpleNamespace(_record=lambda *a,**k:None,fulltext_ledger=fulltext.FulltextLedger(store.path))
        current=item();fulltext.hydrate(bridge,None,contact,[(current,(1,))],new_keys={'one'})
        assert current['text_pending'] is not success
    assert calls==[1]


def test_preexisting_fulltext_reused_without_copy_after_restart(env,monkeypatch):
    store,cid=env;store.ingest(cid,[item(pending=False)])
    bridge=SimpleNamespace(_record=lambda *a,**k:None,fulltext_ledger=fulltext.FulltextLedger(store.path))
    monkeypatch.setattr(fulltext,'copy_text',lambda *a:pytest.fail('Full text is already stored'))
    current=item();fulltext.hydrate(bridge,None,store.contact(cid),[(current,(1,))],new_keys=set())
    assert current['text']==BODY and not current['text_pending']


def test_history_never_copied_and_new_identical_preview_still_copied(env,monkeypatch):
    store,cid=env;contact=store.contact(cid)
    bridge=SimpleNamespace(_record=lambda *a,**k:None,fulltext_ledger=fulltext.FulltextLedger(store.path))
    calls=[];monkeypatch.setattr(fulltext,'copy_text',lambda *a:calls.append(a[-2]) or BODY)
    for _ in range(3):fulltext.hydrate(bridge,None,contact,[(item(),(1,))],new_keys=set())
    new=item('two');fulltext.hydrate(bridge,None,contact,[(item(),(1,)),(new,(2,))],new_keys={'two'})
    assert calls==[(2,)] and new['text']==BODY


def test_deferred_new_messages_remain_eligible_next_poll(monkeypatch):
    bridge=SimpleNamespace(_record=lambda *a,**k:None)
    calls=[];monkeypatch.setattr(fulltext,'copy_text',lambda *a:calls.append(a[-2]) or BODY)
    fulltext.hydrate(bridge,None,{},[(item(),(1,)),(item('two'),(2,))],new_keys={'one','two'})
    fulltext.hydrate(bridge,None,{},[(item(),(1,)),(item('two'),(2,))],new_keys=set())
    assert calls==[(1,),(2,)]


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'panel.sqlite3')
    contact=store.add_contact(dict(lookup='friend',wechat_id='friend',nickname='friend',account_id='self'))
    return store,contact['id']


def events(store):
    return [e for e in store.events(0) if e['type']=='agent.message']


def test_store_waits_for_fulltext_then_emits_once_and_updates_same_row(env):
    store,cid=env;store.ingest(cid,[])
    store.ingest(cid,[item()]);assert not events(store)
    mid=store.messages(cid)[0]['id']
    for _ in range(3):store.ingest(cid,[item(pending=False)])
    messages=store.messages(cid)
    assert len(messages)==1 and messages[0]['id']==mid and messages[0]['text']==BODY
    assert not messages[0]['text_pending'] and len(events(store))==1
    store.ingest(cid,[item(pending=False),item('two',pending=False)])
    assert len(events(store))==2 and not store.contact(cid)['error']


def test_historical_fulltext_never_replays(env):
    store,cid=env
    store.ingest(cid,[item()]);store.ingest(cid,[item(pending=False)])
    assert store.messages(cid)[0]['text']==BODY and not events(store)


def test_old_already_answered_preview_enriches_without_second_event(env):
    store,cid=env;store.ingest(cid,[])
    legacy=item();legacy.pop('text_pending');legacy.pop('identity_text')
    store.ingest(cid,[legacy]);assert len(events(store))==1
    store.ingest(cid,[item(pending=False)])
    assert len(events(store))==1 and len(store.messages(cid))==1
    assert store.messages(cid)[0]['text']==BODY


def test_unknown_direction_does_not_bypass_fulltext_gate(env):
    store,cid=env;store.ingest(cid,[])
    store.ingest(cid,[item(direction='unknown')]);store.ingest(cid,[item()])
    assert not events(store)
    store.ingest(cid,[item(pending=False)])
    assert len(events(store))==1 and events(store)[0]['payload']['text']==BODY


def test_ai_collects_only_fulltext_and_passes_url_to_model(env):
    store,cid=env;received=[]
    def model(provider,messages,tools):
        received.extend(messages)
        return {'role':'assistant','content':'收到了完整分享链接。'},{}
    ai=AIService(store,model_call=model)
    try:
        provider=ai.data.save('providers',None,dict(name='test',base_url='http://127.0.0.1:19999/v1',model='test'))
        agent=ai.data.save('agents',None,dict(name='test',provider_id=provider['id'],web_policy='off'))
        ai.data.settings({'enabled':True,'debounce_seconds':.3,'context_messages':20})
        store.update_contact(cid,listening=True,mode='ai_auto',ai_agent_id=agent['id'])
        store.ingest(cid,[]);store.ingest(cid,[item()]);ai._collect()
        assert not ai.data.runs()
        store.ingest(cid,[item(pending=False)]);ai._collect()
        with store.connect() as db:db.execute('UPDATE ai_inbox SET due=0')
        ai._collect();run=ai.data.runs()[0];ai.execute(run['id'])
        assert run['input']==BODY
        assert any(m.get('role')=='user' and m.get('content')==BODY for m in received)
        assert not any(m.get('content')==PREVIEW for m in received)
        assert len(store.pending())==1
    finally:ai.stop()
