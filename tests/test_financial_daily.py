import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from PIL import Image
from pypdf import PdfWriter
import pytest

from wechat_panel import financial_daily as daily
from wechat_panel.ai import AIService
from wechat_panel.store import Store


def ready(workspace, expected_date=None):
    workspace.mkdir(parents=True,exist_ok=True)
    pdf=workspace/'daily.pdf'
    writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.add_blank_page(width=100,height=100)
    writer.write(pdf)
    previews=[]
    for n in (1,2):
        p=workspace/f'daily-{n}.png';Image.new('RGB',(100,100),'white').save(p);previews.append(str(p))
    markets=workspace/'markets.json';markets.write_text(json.dumps({'rows':[{}]*9}),encoding='utf-8')
    return dict(status='ready',pdf=str(pdf),previews=previews,markets=str(markets),date='2026-10-22',
                url='https://xnews.jin10.com/details/1',snapshot_at='2026-10-22T22:00:00+08:00',warnings=[],
                validation={'passed':True,'pages':2},artifact_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())


@pytest.fixture
def environment(tmp_path,monkeypatch):
    store=Store(tmp_path/'panel.sqlite3')
    ai=AIService(store,model_call=lambda *a:pytest.fail('Financial skill should execute its own pipeline'))
    provider=ai.data.save('providers',None,dict(name='fixture',base_url='http://127.0.0.1:9/v1',model='fixture'))
    skill=ai.data.save('skills',None,dict(name=daily.NAME,instructions='Run the financial newspaper.'))
    agent=ai.data.save('agents',None,dict(name='fixture',provider_id=provider['id'],skill_ids=[skill['id']],web_policy='model',
        companion={'memory':{'enabled':False},'safety':{'emotion_enabled':False,'crisis_detection':False}}))
    monkeypatch.setattr(daily,'run_pipeline',ready)
    try:yield store,ai,agent
    finally:ai.stop()


def test_financial_skill_produces_original_pdf_in_dry_run_without_an_outbox(environment,monkeypatch):
    store,ai,agent=environment
    import wechat_panel.financial_pdf as pdf
    monkeypatch.setattr(pdf,'render_report',lambda *a,**kw:pytest.fail('Must preserve skill PDF layout'))
    monkeypatch.setattr(store,'enqueue',lambda *a,**kw:pytest.fail('Dry run must never send'))
    run=ai.create_run(agent['id'],'生成今日财经日报',dry_run=True)
    ai.execute(run['id']);run=ai.data.run(run['id'])
    assert run['status']=='completed',run['error']
    assert run['state']['pdf']['name']=='每日财经-2026-10-22.pdf'
    assert len(run['state']['pdf']['preview_urls'])==2
    assert daily.verified_pdf(store.path.parent,run).is_file()
    assert daily.preview_path(store.path.parent,run,1).is_file()
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0


@pytest.mark.parametrize('failure',['hash','validation','pages','escape'])
def test_unverified_or_external_artifacts_are_rejected(environment,monkeypatch,failure):
    store,ai,agent=environment
    def broken(workspace,expected_date):
        result=ready(workspace)
        if failure=='hash':result['artifact_sha256']='0'*64
        if failure=='validation':result['validation']['passed']=False
        if failure=='pages':result['previews'].pop()
        if failure=='escape':result['pdf']=str(workspace.parent/'outside.pdf')
        return result
    monkeypatch.setattr(daily,'run_pipeline',broken)
    run=ai.create_run(agent['id'],'生成今日财经日报',dry_run=True)
    ai.execute(run['id']);run=ai.data.run(run['id'])
    assert run['status']=='failed' and not run['state'].get('pdf')
    assert not store.pending()


def test_bound_skill_does_not_accept_commands_or_workspace_paths(tmp_path):
    state={'snapshot':{'skills':{'s':{'enabled':True,'name':daily.NAME}}}}
    for arguments in ({'command':'whoami'},{'workspace':'C:/outside'},{'date':'not-a-date'}):
        with pytest.raises(ValueError):daily.collect(arguments,{'id':str(uuid.uuid4())},state,tmp_path)


def test_package_tampering_blocks_execution(tmp_path,monkeypatch):
    (tmp_path/'scripts').mkdir()
    script=tmp_path/'scripts/run_daily.py';script.write_text('print(1)',encoding='utf-8')
    (tmp_path/'package.json').write_text(json.dumps({'sha256':{'scripts/run_daily.py':hashlib.sha256(script.read_bytes()).hexdigest()}}),encoding='utf-8')
    monkeypatch.setattr(daily,'ROOT',tmp_path)
    daily.verify_package()
    script.write_text('print(2)',encoding='utf-8')
    with pytest.raises(ValueError,match='变化'):daily.verify_package()


def test_article_validation_excludes_furniture_but_keeps_identical_words_in_body():
    spec=importlib.util.spec_from_file_location('financial_workflow',daily.ROOT/'scripts/workflow.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def page(entries):
        def extract(visitor_text):
            for text,y in entries:visitor_text(text,[1,0,0,1,0,0],[1,0,0,1,0,y],None,10)
        return SimpleNamespace(extract_text=extract)
    reader=SimpleNamespace(pages=[None,
        page([('HEADER',804),('正文包含 HEADER，段落跨',100),('FOOTER',45)]),
        page([('HEADER',804),('页仍完整。',760),('FOOTER',45)])])
    assert module.article_text(reader)=='正文包含 HEADER，段落跨页仍完整。'


def test_format_followup_reuses_original_pdf_only_in_the_same_test_session(environment):
    store,ai,agent=environment
    session=str(uuid.uuid4())
    first=ai.create_run(agent['id'],'生成今日财经日报',dry_run=True,session_id=session)
    ai.execute(first['id']);first=ai.data.run(first['id'])
    followup=ai.create_run(agent['id'],'请以 PDF 格式发给我',dry_run=True,session_id=session)
    ai.execute(followup['id']);followup=ai.data.run(followup['id'])
    assert followup['status']=='completed',followup['error']
    assert daily.verified_pdf(store.path.parent,followup).read_bytes()==daily.verified_pdf(store.path.parent,first).read_bytes()
    assert followup['state']['pdf']['url'].endswith(followup['id']+'/pdf')
    assert followup['id'] in str(daily.preview_path(store.path.parent,followup,1))
    other=ai.create_run(agent['id'],'请以 PDF 格式发给我',dry_run=True,session_id=str(uuid.uuid4()))
    ai.model_call=lambda *a:({'content':'另一个测试会话，没有可导出的上文。'}, {})
    ai.execute(other['id']);other=ai.data.run(other['id'])
    assert not other['state'].get('financial_daily') and '另一个测试会话' in other['reply']
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0


def test_preview_api_serves_all_pages_and_refuses_tampered_pdf(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from wechat_panel.app import create_app
    class NoDesktop:
        def __init__(self,*a,**kw):pass
        def start(self):pass
        def stop(self):pass
    monkeypatch.setattr(AIService,'start',lambda self:None)
    with TestClient(create_app(tmp_path,runtime_factory=NoDesktop)) as client:
        ai=client.app.state.ai
        p=ai.data.save('providers',None,dict(name='fixture',model='fixture',base_url='http://localhost:1'))
        a=ai.data.save('agents',None,dict(name='fixture',provider_id=p['id']))
        run=ai.create_run(a['id'],'fixture',dry_run=True)
        run=ai.data.run(run['id'])
        state=run['state'];state['snapshot']['skills']={'s':{'enabled':True,'name':daily.NAME}}
        monkeypatch.setattr(daily,'run_pipeline',ready)
        daily.collect({},run,state,tmp_path);ai.data.update_run(run['id'],state=state)
        assert client.get(state['pdf']['url']).content.startswith(b'%PDF-')
        for url in state['pdf']['preview_urls']:
            response=client.get(url)
            assert response.status_code==200 and response.content.startswith(b'\x89PNG')
        assert client.get(state['pdf']['url']+'/pages/3').status_code==409
        daily.verified_pdf(tmp_path,ai.data.run(run['id'])).write_bytes(b'tampered')
        assert client.get(state['pdf']['url']).status_code==409
        assert client.get(state['pdf']['preview_urls'][0]).status_code==409
