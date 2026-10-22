from pathlib import Path
import time

import pytest
from pypdf import PdfReader
from fastapi.testclient import TestClient
from wechat_panel.financial_pdf import wants_pdf, report_path, render_report
from wechat_panel.app import create_app


@pytest.mark.parametrize('text,expected', [
    ('帮我分析美股，以 PDF 格式发给我', True), ('把上面的报告做成PDF', True),
    ('我要 PDF 格式', True), ('export this report as a PDF', True),
    ('分析这个 PDF 的结论', False), ('给我分析这份PDF文件', False),
    ('PDF 是什么？', False), ('不用PDF，直接文字回复', False), ('汇总昨日美股', False)])
def test_only_explicit_pdf_output_triggers(text, expected):
    assert wants_pdf(text) == expected


@pytest.mark.parametrize('text,expected',[
    ('请以 PDF 格式发给我',True),('帮我导出PDF文件',True),('用PDF回复吧',True),
    ('请分析黄金，以PDF格式发给我',False),('重新生成PDF',False),('PDF是什么',False)])
def test_format_only_followup_does_not_match_new_topic(text,expected):
    from wechat_panel.financial_pdf import format_only_request
    assert format_only_request(text)==expected


def test_report_preserves_tables_chinese_and_paginates(tmp_path):
    content = '# 中文研究标题\n\n## 数据比较\n\n| 标的 | 数值 | 解释 |\n|---|---:|---|\n'
    content += ''.join(f'| 样本{i} | {i}.25% | 有效数据与正文说明 |\n' for i in range(85))
    content += '\n## 来源\n\n[原始来源](https://example.org/report)\n\n**结束标记**'
    path = render_report(content, tmp_path / 'report.pdf')
    pdf = PdfReader(path)
    text = '\n'.join(p.extract_text() for p in pdf.pages)
    assert len(pdf.pages) > 2
    assert '中文研究标题' in text and '结束标记' in text
    assert '84.25%' in text and '0.25%' in text
    assert sum('标的' in p.extract_text() for p in pdf.pages) > 2
    links = [a.get_object() for p in pdf.pages for a in p.get('/Annots', [])]
    assert any(a.get('/A', {}).get('/URI') == 'https://example.org/report' for a in links)


def test_pdf_request_generates_download_and_file_queue_without_rewriting_reply(tmp_path):
    from wechat_panel.ai import AIService
    from wechat_panel.store import Store
    store = Store(tmp_path / 'panel.sqlite3')
    ai = AIService(store)
    try:
        p = ai.data.save('providers', None, dict(name='local', model='test', base_url='http://localhost:19999/v1'))
        a = ai.data.save('agents', None, dict(name='research', kind='vibe', vibe_engine='sparkflow', provider_id=p['id']))
        ai.data.settings({'enabled': True})
        c = store.add_contact(dict(lookup='test', nickname='test', wechat_id='test', account_id='self'))
        store.update_contact(c['id'], listening=True, mode='ai_auto', ai_agent_id=a['id'])
        store.ingest(c['id'], [])
        store.ingest(c['id'], [dict(key='q',text='分析并以PDF格式发给我',kind='text',direction='incoming')])
        msg = store.messages(c['id'])[-1]
        r = ai.create_run(a['id'], msg['text'], c['id'], msg['id'])
        content = '# 分析结果\n\n| 项目 | 数值 |\n|---|---|\n| 已验证结果 | 123.45 |'
        ai.finish(r['id'], content)
        assert ai.data.run(r['id'])['reply'] == content
        pending = store.pending()
        assert len(pending) == 1 and pending[0]['kind'] == 'file'
        assert store.ai_send_valid(pending[0])
        path = report_path(tmp_path, r['id'])
        assert path.is_file()
        # File cards include a filename and size, while the queue retains filename only.
        # Keep the visible incoming row as the continuity anchor. A completely
        # disjoint snapshot now waits for recovery instead of importing new rows.
        question=dict(key='q',text=msg['text'],kind='text',direction='incoming')
        store.ingest(c['id'], [question,dict(key='pdf-key', text=path.name+'\n120 KB',kind='file',direction='outgoing')])
        store.set_message_state(pending[0]['id'], 'confirmed', source_key='pdf-key')
        assert ai.data.run(r['id'])['status'] == 'sent'
        assert len([m for m in store.messages(c['id']) if m['direction']=='outgoing']) == 1
        store.ingest(c['id'], [question,dict(key='pdf-key',text=path.name+'\n120 KB',kind='file',direction='outgoing'),
                              dict(key='q2',text='把刚才的报告生成PDF，先不要发送',kind='text',direction='incoming')])
        latest = store.messages(c['id'])[-1]
        export = ai.create_run(a['id'],latest['text'],c['id'],latest['id'])
        ai.vibe.execute = lambda *args: pytest.fail('Exporting the previous report must not call the model')
        ai.execute(export['id'])
        assert ai.data.run(export['id'])['status'] == 'draft'
        assert ai.data.run(export['id'])['reply'] == content
        assert not store.pending()
    finally:
        ai.stop()


def test_pdf_test_route_download_and_normal_text_unchanged(tmp_path):
    def model(*args): return {'content':'# 测试报告\n\n正文保持完整。'}, {'total_tokens': 3}
    with TestClient(create_app(tmp_path, demo=True, ai_model_call=model)) as client:
        h={'X-Panel-Request':'1'}
        p=client.post('/api/ai/config/providers',headers=h,json=dict(name='test',model='test',base_url='http://localhost:19999/v1')).json()
        a=client.post('/api/ai/config/agents',headers=h,json=dict(name='test',provider_id=p['id'])).json()
        for question, expected in [('请生成PDF报告', True), ('请简单回复', False)]:
            r=client.post('/api/ai/test',headers=h,json=dict(agent_id=a['id'],text=question)).json()
            for _ in range(80):
                detail=client.get('/api/ai/runs/'+r['id']).json()
                if detail['status'] in ('completed','failed'):break
                time.sleep(.1)
            assert detail['status']=='completed',detail
            assert bool(detail['pdf']) == expected
            response=client.get('/api/ai/runs/'+r['id']+'/pdf')
            if expected:
                assert response.status_code==200 and response.content.startswith(b'%PDF-')
            else:assert response.status_code==404
        assert client.get('/api/contacts').json()==[]


def test_report_id_cannot_escape_directory(tmp_path):
    with pytest.raises(ValueError):report_path(tmp_path,'../../secret')


def test_cjk_bold_status_and_fixed_width_diagrams_survive_layout(tmp_path):
    source = '# 排版回归\n\n## 时间表\n\n| 事项 | 说明 |\n|---|---|\n| ✅ 完成 | 等**锁定期解禁（2027-05）**这两个节点 |\n\n'
    source += '```\n$100B ┤                     █\n  $9B ┴   ▃\n         2025末   5月   7月   9月\n```\n\n'
    source += '1. 第一条\n2. 第二条\n'
    pdf=PdfReader(render_report(source,tmp_path/'layout.pdf'))
    text='\n'.join(p.extract_text() for p in pdf.pages)
    assert '**' not in text
    assert '$100B ┤                     █' in text
    assert '锁定期解禁（2027-05）' in text
    assert '1. 第一条' in text and '2. 第二条' in text
    assert '✓' in text


def test_title_metadata_contains_only_local_time(tmp_path):
    pdf=PdfReader(render_report('# 标题\n\n正文',tmp_path/'timestamp.pdf',created_at='2026-10-17T08:55:00+00:00',sample=True))
    text=pdf.pages[0].extract_text()
    assert '2026.10.17 16:55' in text
    assert 'CST' not in text and '示例内容' not in text


def test_explicit_pdf_auto_reply_reaches_serial_demo_file_sender(tmp_path):
    def model(*args):return {'content':'# 自动 PDF\n\n| 结果 | 值 |\n|---|---|\n| 测试 | 12.34 |'},{}
    with TestClient(create_app(tmp_path,demo=True,ai_model_call=model)) as client:
        headers={'X-Panel-Request':'1'}
        def post(url, data):
            r=client.post(url,headers=headers,json=data)
            assert r.status_code<300,r.text
            return r.json()
        p=post('/api/ai/config/providers',dict(name='test',model='test',base_url='http://localhost:19999/v1'))
        a=post('/api/ai/config/agents',dict(name='test',provider_id=p['id']))
        for _ in range(80):
            if client.get('/api/status').json()['state']=='ready':break
            time.sleep(.1)
        c=post('/api/contacts',dict(lookup='PDF测试',wechat_id='pdf-test'))
        client.patch('/api/contacts/'+c['id'],headers=headers,json=dict(listening=True,mode='ai_auto',ai_agent_id=a['id']))
        client.put('/api/ai/settings',headers=headers,json=dict(enabled=True,debounce_seconds=.3,context_messages=20))
        for _ in range(80):
            if client.get('/api/contacts').json()[0]['listener_state']=='listening':break
            time.sleep(.1)
        post('/api/demo/'+c['id']+'/incoming',dict(text='请生成PDF格式的测试报告'))
        for _ in range(150):
            runs=client.get('/api/ai/runs',params={'contact_id':c['id']}).json()
            if runs and runs[0]['status'] in ('sent','failed','uncertain'):break
            time.sleep(.1)
        assert runs and runs[0]['status']=='sent',runs
        messages=client.get('/api/contacts/'+c['id']+'/messages').json()
        files=[m for m in messages if m['direction']=='outgoing']
        assert len(files)==1 and files[0]['kind']=='file' and files[0]['status']=='confirmed'
