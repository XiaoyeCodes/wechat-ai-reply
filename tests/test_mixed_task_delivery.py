"""Compound results are queued together; no real bridge is constructed."""
from io import BytesIO

from PIL import Image
import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store
from test_agent_schedules import env as schedule_env,task


def setup_task(tmp_path):
    store=Store(tmp_path/'panel.sqlite3')
    ai=AIService(store,model_call=lambda *args:({'content':'未调用'},{}))
    ai.data.settings({'enabled':True,'debounce_seconds':.3,'context_messages':20})
    provider=ai.data.save('providers',None,dict(name='test',model='fake',base_url='http://localhost:1'))
    agent=ai.data.save('agents',None,dict(name='Aria test',provider_id=provider['id'],
        companion={'memory':{'enabled':False},'safety':{'emotion_enabled':False,'crisis_detection':False}}))
    contact=store.add_contact(dict(lookup='本地模拟联系人',nickname='本地模拟联系人',wechat_id='local',account_id='self'))
    store.update_contact(contact['id'],listening=True,mode='ai_auto',ai_agent_id=agent['id'])
    store.ingest(contact['id'],[])
    store.ingest(contact['id'],[dict(key='request',text='查天气并做一张图片',kind='text',direction='incoming',account_id='self')])
    message=store.messages(contact['id'])[-1]
    run=ai.create_run(agent['id'],message['text'],contact['id'],message['id'])
    run=ai.data.run(run['id'])
    state=run['state']
    state['task_plan']={'steps':[{'id':'weather','status':'done'},{'id':'image','status':'done'}],'delivery_text':True}
    buf=BytesIO();Image.new('RGB',(12,12),'green').save(buf,format='PNG')
    media=ai.companion.memory.media(state['scope'],buf.getvalue(),'image','image/png')
    state['artifacts']=[media]
    ai.data.update_run(run['id'],status='running',state=state)
    return ai,store,contact,run,media


def test_compound_weather_and_image_preserves_both_results_and_waits_for_all_receipts(tmp_path):
    ai,store,contact,run,media=setup_task(tmp_path)
    try:
        ai.finish(run['id'],'明天有雨，记得带伞。图片已生成。')
        rows=store.pending()
        assert [(row['kind'],row['contact_id']) for row in rows]==[('text',contact['id']),('image',contact['id'])]
        assert '带伞' in rows[0]['text'] and rows[1]['media_id']==media['id']
        store.set_message_state(rows[0]['id'],'confirmed',source_key='local-text')
        assert ai.data.run(run['id'])['status']=='queued'
        store.set_message_state(rows[1]['id'],'confirmed',source_key='local-image')
        assert ai.data.run(run['id'])['status']=='sent'
        assert all(row['ai_run_id']==run['id'] for row in rows)
    finally:ai.stop()


def test_bundle_is_idempotent_and_cannot_change_existing_payload(tmp_path):
    store=Store(tmp_path/'panel.sqlite3')
    contact=store.add_contact(dict(lookup='本地模拟',nickname='本地模拟',account_id='self',wechat_id='local'))
    store.update_contact(contact['id'],mode='manual')
    args=(contact['id'],'run-local','查询结果',['local-image'],'report.pdf')
    first=store.enqueue_ai_bundle(*args)
    again=store.enqueue_ai_bundle(*args)
    assert [row['id'] for row in first]==[row['id'] for row in again]
    assert [row['kind'] for row in first]==['text','image','file']
    assert len(store.pending())==3
    with pytest.raises(ValueError):store.enqueue_ai_bundle(contact['id'],'run-local','不同结果',['local-image'],'report.pdf')
    assert len(store.pending())==3


def test_short_task_failure_suppresses_all_compound_attachments(tmp_path):
    ai,store,contact,run,media=setup_task(tmp_path)
    try:
        state=ai.data.run(run['id'])['state'];state['reply_failure_reason']='第二个任务未完成'
        ai.data.update_run(run['id'],state=state)
        ai.finish(run['id'],'我只完成了其中一部分')
        rows=store.pending()
        assert len(rows)==1 and rows[0]['kind']=='text'
        assert rows[0]['text']=='我遇到点问题，未能完成该任务。'
        assert ai.data.run(run['id'])['state']['artifacts']==[]
    finally:ai.stop()


def test_compound_pdf_and_image_are_both_preserved(tmp_path,monkeypatch):
    ai,store,contact,run,media=setup_task(tmp_path)
    try:
        from pypdf import PdfWriter
        from wechat_panel import financial_pdf
        def render(text,path,**kwargs):
            path.parent.mkdir(parents=True,exist_ok=True)
            writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(path)
            return path
        monkeypatch.setattr(financial_pdf,'render_report',render)
        state=ai.data.run(run['id'])['state'];state['output_format']='pdf'
        state['task_plan']['delivery_text']=False
        ai.data.update_run(run['id'],state=state)
        ai.finish(run['id'],'这份文字结果需要与图片一起交付。')
        rows=store.pending()
        assert [row['kind'] for row in rows]==['image','file']
        assert rows[0]['media_id']==media['id']
        assert all(row['contact_id']==contact['id'] and row['ai_run_id']==run['id'] for row in rows)
        assert financial_pdf.report_path(store.path.parent,run['id']).read_bytes().startswith(b'%PDF-')
    finally:ai.stop()


@pytest.mark.parametrize('saved',[True,False])
def test_compound_explicit_memory_is_checked_after_the_automatic_writer(tmp_path,monkeypatch,saved):
    ai,store,contact,run,media=setup_task(tmp_path)
    try:
        state=ai.data.run(run['id'])['state']
        text='记住我喜欢茉莉花茶，再查上海天气。'
        state['turn_messages']=[{'kind':'text','text':text}]
        state['snapshot']['agent']['companion']['memory'].update(enabled=True,auto_extract=True)
        profile=ai.companion.memory.profile(state['scope'])
        state.update(companion_prepared=True,memory_revision=profile['revision'],analysis={
            'summary':'用户明确要求保存饮茶偏好','mood':'平静',
            'facts':[{'key':'喜欢的茶','content':'喜欢茉莉花茶','category':'preference','evidence':'记住我喜欢茉莉花茶'}]})
        state['task_plan']['deferred_memory']=True
        with store.connect() as db:db.execute('UPDATE ai_runs SET input=? WHERE id=?',(text,run['id']))
        if not saved:monkeypatch.setattr(ai.companion.memory,'save_memory',lambda *a,**kw:None)
        ai.data.update_run(run['id'],state=state)
        ai.finish(run['id'],'已记住饮茶偏好，上海明天有雨，图片已生成。')
        final=ai.data.run(run['id']);rows=store.pending()
        assert final['state']['task_plan']['memory_status']==('saved' if saved else 'failed')
        if saved:
            assert [m['kind'] for m in rows]==['text','image']
            assert any('茉莉花茶' in m['content'] for m in ai.companion.memory.list_memories(state['scope']))
        else:
            assert len(rows)==1 and rows[0]['kind']=='text'
            assert rows[0]['text']=='我遇到点问题，未能完成该任务。'
            assert final['state']['artifacts']==[]
    finally:ai.stop()


@pytest.mark.parametrize('pdf',[False,True])
def test_public_compound_results_keep_all_parts_and_independent_receipts(schedule_env,pdf):
    store,ai,provider,agent,contacts,calls=schedule_env
    occurrence=ai.agent_scheduler.run_now(task(ai,agent)['id'])
    run=ai.data.run(occurrence['run_id']);state=run['state']
    buf=BytesIO();Image.new('RGB',(12,12),'blue').save(buf,format='PNG')
    media=ai.companion.memory.media(state['scope'],buf.getvalue(),'image','image/png')
    state.update(artifacts=[media],task_plan={'steps':[],'status':'complete','delivery_text':not pdf})
    if pdf:
        from pypdf import PdfWriter
        from wechat_panel.financial_pdf import report_path
        path=report_path(store.path.parent,run['id']);path.parent.mkdir(parents=True,exist_ok=True)
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(path)
        state.update(output_format='pdf',pdf={'name':path.name,'url':f"/api/ai/runs/{run['id']}/pdf"})
    ai.data.update_run(run['id'],status='running',state=state)
    ai.agent_scheduler.complete(run['id'],'公共查询结果：明天有雨，请带伞。',[media['id']])
    queued=store.pending()
    for contact in contacts:
        rows=[row for row in queued if row['contact_id']==contact['id']]
        assert [m['kind'] for m in rows]==(['image','file'] if pdf else ['text','image'])
        assert all(store.ai_send_valid(m) for m in rows)
        store.set_message_state(rows[0]['id'],'confirmed',source_key='demo-first-'+contact['id'])
        assert ai.data.run(rows[0]['ai_run_id'])['status']=='queued'
        store.set_message_state(rows[1]['id'],'confirmed',source_key='demo-last-'+contact['id'])
        assert ai.data.run(rows[1]['ai_run_id'])['status']=='sent'
    assert not calls
