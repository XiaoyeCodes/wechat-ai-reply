"""Late attachment receipts, using only snapshots and an isolated SQLite store."""
from datetime import datetime, timedelta, timezone

import pytest

from wechat_panel.send_receipts import match, match_file
from wechat_panel.store import Store


def bubble(key, text='[图片]', kind='image', direction='outgoing', **extra):
    return dict(key=key, text=text, kind=kind, direction=direction,
                direction_verified=direction!='unknown', **extra)


def baseline():
    return [bubble('question', '这是前面已经核对过的完整提问内容。', 'text', 'incoming'),
            bubble('answer', '这是本人此前发送过的完整回答内容。', 'text')]


def test_file_receipt_requires_an_exact_filename_line():
    old=baseline()+[bubble('old-file','文件\nreport.pdf\n12 KB','file')]
    found=bubble('new-file','文件\r\nreport.pdf\r\n18 KB','file')
    assert match_file(old,old+[found],'report.pdf')['key']=='new-file'
    assert match(old,old+[found],'report.pdf',kind='file')['key']=='new-file'
    assert match_file(old,old,'report.pdf') is None


@pytest.mark.parametrize('case',['incoming','unknown','unverified','text','suffix','prefix',
                                 'embedded','duplicates','history','recreated_history'])
def test_file_receipt_rejects_unproven_or_ambiguous_bubbles(case):
    old=baseline()+[bubble('old-file','文件\nreport.pdf\n12 KB','file')]
    found=bubble('new-file','文件\nreport.pdf\n18 KB','file')
    current=old+[found]
    if case in ('incoming','unknown'):found['direction']=case
    if case=='unverified':found['direction_verified']=False
    if case=='text':found['kind']='text'
    if case=='suffix':found['text']='文件\nreport.pdf.exe\n18 KB'
    if case=='prefix':found['text']='文件\nmy-report.pdf\n18 KB'
    if case=='embedded':found['text']='我发了report.pdf文件'
    if case=='duplicates':current.append(dict(found,key='duplicate'))
    if case=='history':current=old
    if case=='recreated_history':current=[dict(old[-1],key='recreated-file')]
    assert match_file(old,current,'report.pdf') is None


def test_image_receipt_uses_unique_new_kind_instead_of_placeholder_text():
    old=baseline()+[bubble('old-image')]
    found=bubble('new-image','图片')
    assert match(old,old+[found],'[图片]',kind='image')['key']=='new-image'
    assert match(old,old+[found],'[图片]') is None


@pytest.mark.parametrize('case',['incoming','unknown','unverified','text','file','duplicates',
                                 'history','recreated_history'])
def test_image_receipt_rejects_unproven_or_ambiguous_bubbles(case):
    old=baseline()+[bubble('old-image')]
    found=bubble('new-image')
    current=old+[found]
    if case in ('incoming','unknown'):found['direction']=case
    if case=='unverified':found['direction_verified']=False
    if case in ('text','file'):found['kind']=case
    if case=='duplicates':current.append(dict(found,key='duplicate'))
    if case=='history':current=old
    if case=='recreated_history':current=[dict(old[-1],key='recreated-image')]
    assert match(old,current,'[图片]',kind='image') is None


def test_native_row_recycling_requires_known_context_before_media_is_new():
    old=baseline()+[bubble('old-image')]
    current=[dict(item,key='recycled-'+item['key']) for item in old]+[bubble('new-image')]
    assert match(old,current,'[图片]',kind='image')['key']=='new-image'
    assert match(old,current[:-1],'[图片]',kind='image') is None


def test_none_baseline_and_voice_do_not_confirm():
    assert match(None,[bubble('new-text','hi','text')],'hi') is None
    assert match(None,[bubble('new-image')],'[图片]',kind='image') is None
    assert match([], [bubble('voice','语音','voice')], '语音',kind='voice') is None


@pytest.fixture
def store_contact(tmp_path):
    store=Store(tmp_path/'receipts.sqlite3')
    contact=store.add_contact(dict(lookup='isolated friend',nickname='isolated friend',
                                   wechat_id='isolated-friend',account_id='isolated-self'))
    store.ingest(contact['id'],baseline())
    return store,contact


def uncertain(store,contact,kind='image',*,attempted=True,key='test-send'):
    text='report.pdf' if kind=='file' else '[图片]' if kind=='image' else '语音'
    message,_=store.enqueue(contact['id'],text,key,kind=kind)
    store.set_message_state(message['id'],'sending')
    store.send_receipt(message['id'],'prepared',baseline())
    if attempted:store.send_receipt(message['id'],'attempted')
    store.set_message_state(message['id'],'uncertain','等待新增本人消息')
    return message


@pytest.mark.parametrize('kind',['image','file'])
def test_store_recovers_one_late_media_receipt_without_requeue(store_contact,kind):
    store,contact=store_contact
    message=uncertain(store,contact,kind)
    text='文件\nreport.pdf\n12 KB' if kind=='file' else '图片'
    items=baseline()+[bubble('late-media',text,kind)]
    store.ingest(contact['id'],items)
    store.ingest(contact['id'],items)
    assert store.message(message['id'])['status']=='confirmed'
    assert store.message(message['id'])['source_key']=='late-media'
    assert len([event for event in store.events() if event['type']=='send.recovered'])==1
    assert len(store.messages(contact['id']))==len(baseline())+1
    assert not store.pending() and not store.events(agent_only=True)


@pytest.mark.parametrize('case',['not_attempted','expired','future','bad_timestamp','cancelled',
                                 'failed','other_contact','voice','placeholder'])
def test_store_does_not_recover_unproven_stale_or_cancelled_media(store_contact,case):
    store,contact=store_contact
    kind='voice' if case=='voice' else 'image'
    message=uncertain(store,contact,kind,attempted=case!='not_attempted')
    if case in ('expired','future','bad_timestamp'):
        value='not-a-timestamp' if case=='bad_timestamp' else (
            datetime.now(timezone.utc)+timedelta(minutes=3 if case=='future' else -3)).isoformat()
        with store.connect() as db:
            db.execute('UPDATE send_receipts SET attempted_at=? WHERE message_id=?',(value,message['id']))
    if case in ('cancelled','failed'):store.set_message_state(message['id'],case,'已停止')
    if case=='other_contact':
        contact=store.add_contact(dict(lookup='other',nickname='other',wechat_id='other',account_id='isolated-self'))
    items=baseline()+[bubble('late-media',kind='text' if case=='placeholder' else kind)]
    store.ingest(contact['id'],items)
    assert store.message(message['id'])['status']==(case if case in ('cancelled','failed') else 'uncertain')
    assert not [event for event in store.events() if event['type']=='send.recovered']


def test_cancellation_after_matching_wins_over_late_receipt(store_contact,monkeypatch):
    import wechat_panel.send_receipts as receipts
    store,contact=store_contact
    message=uncertain(store,contact)
    original=receipts.match
    def cancel_while_matching(*args,**kwargs):
        store.set_message_state(message['id'],'cancelled','人工取消')
        return original(*args,**kwargs)
    monkeypatch.setattr(receipts,'match',cancel_while_matching)
    store.reconcile_receipts(contact['id'],baseline()+[bubble('late-image')])
    assert store.message(message['id'])['status']=='cancelled'
    assert not [event for event in store.events() if event['type']=='send.recovered']


def test_one_media_bubble_cannot_confirm_two_sends(store_contact):
    store,contact=store_contact
    messages=[uncertain(store,contact,key='image-'+str(index)) for index in range(2)]
    store.ingest(contact['id'],baseline()+[bubble('one-late-image')])
    assert sorted(store.message(message['id'])['status'] for message in messages)==['confirmed','uncertain']
    assert len([event for event in store.events() if event['type']=='send.recovered'])==1
