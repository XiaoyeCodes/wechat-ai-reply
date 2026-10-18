import pytest

from wechat_panel.research_delivery import analysis_request, substantive_analysis
from wechat_panel.ai import AIService
from wechat_panel.store import Store


REPORT = '''# 美股市场分析

## 核心结论
美股市场本周出现分化，科技股票回落，债券收益率上行。因此，需要分别观察估值与利润预期，而不是仅凭指数涨跌判断趋势。
## 数据比较
| 项目 | 变化 | 影响 |
|---|---|---|
| 收益率 | +0.2% | 利率上行导致估值承压 |
| 营收 | +12% | 利润增长提供支撑 |
## 情景与风险
市场下一步走势取决于利润能否兑现。如果营收增长持续，现金流改善可以缓解利率压力；但如果通胀反弹，债券收益率上升意味着股票估值仍有调整风险。因此应结合财报与流动性观察，避免把一天的波动当成长期趋势。以上数据仅为软件测试样本。
'''


@pytest.mark.parametrize('question,reply,expected', [
    ('帮我分析美股', REPORT, True),
    ('你好', '你好，有什么可以帮你？', False),
    ('什么是ETF', 'ETF是交易所交易基金，可以像股票一样交易。', False),
    ('比特币现在多少钱', 'BTC现价为100000美元，数据时间10:30。', False),
    ('美股怎么样', '我暂时没有获取到行情数据，请稍后再试。', False),
    ('分析美股', ('查询失败，无法取得市场、股票、收益率、债券、利率的数据。\n' * 20), False),
    ('随便聊聊', '今天可以聊生活、读书、电影，也可以分享心情。' * 50, False),
])
def test_only_actual_financial_analysis_becomes_pdf(question, reply, expected):
    assert substantive_analysis(question, reply) == expected


@pytest.mark.parametrize('question,expected', [('你好',False),('不用分析，先聊聊',False),('帮我分析一下',True),('昨天美股怎么样，汇总一下',True),('查一下BTC现在价格',False)])
def test_start_signal(question, expected):
    assert analysis_request(question) == expected


@pytest.fixture
def research(tmp_path):
    store=Store(tmp_path/'panel.sqlite3'); ai=AIService(store)
    p=ai.data.save('providers',None,dict(name='test',model='test',base_url='http://localhost:19999/v1'))
    a=ai.data.save('agents',None,dict(name='research',kind='vibe',vibe_engine='sparkflow',provider_id=p['id']))
    ai.data.settings({'enabled':True})
    c=store.add_contact(dict(lookup='test',nickname='test',wechat_id='test',account_id='self'))
    store.update_contact(c['id'],listening=True,mode='ai_auto',ai_agent_id=a['id'])
    store.ingest(c['id'],[])
    def run(question='帮我分析昨日美股市场', **kwargs):
        store.ingest(c['id'],[dict(key=question,text=question,kind='text',direction='incoming')])
        msg=store.messages(c['id'])[-1]
        return ai.create_run(a['id'],question,c['id'],msg['id'],**kwargs)
    yield ai,store,c,a,run
    ai.stop()


def test_analysis_sends_one_notice_then_pdf_and_keeps_native_answer(research):
    ai,store,c,a,new_run=research
    def native(run,state):
        ai.research_started(run['id'])  # repeated analytical tools must not spam
        ai.research_started(run['id'])
        pending=store.pending()
        assert len(pending)==1 and pending[0]['origin']=='ai_progress'
        assert store.ai_send_valid(pending[0])
        store.set_message_state(pending[0]['id'],'confirmed')
        assert ai.data.run(run['id'])['status']=='running'
        ai.finish(run['id'],REPORT)
    ai.vibe.execute=native
    run=new_run();ai.execute(run['id'])
    assert ai.data.run(run['id'])['reply']==REPORT
    assert ai.data.run(run['id'])['state']['pdf_automatic']
    pending=store.pending()
    assert len(pending)==1 and pending[0]['kind']=='file'
    store.set_message_state(pending[0]['id'],'confirmed')
    assert ai.data.run(run['id'])['status']=='sent'
    subsequent=new_run('谢谢')
    assert not any('正在分析中' in m['content'] for m in ai.data.run(subsequent['id'])['state']['context'])


@pytest.mark.parametrize('question,reply,kind', [('你好','你好。','text'),('帮我分析美股，不用PDF，直接文字回复',REPORT,'text'),('生成PDF，先不要发送',REPORT,None)])
def test_delivery_preferences_and_casual_chat(research,question,reply,kind):
    ai,store,c,a,new_run=research
    ai.vibe.execute=lambda run,state:ai.finish(run['id'],reply)
    run=new_run(question);ai.execute(run['id'])
    final=[m for m in store.pending() if m['origin']=='ai']
    if kind:assert len(final)==1 and final[0]['kind']==kind
    else:assert not final and ai.data.run(run['id'])['status']=='draft'
    if question=='你好':assert len(store.messages(c['id']))==2


def test_progress_failure_does_not_cancel_report_and_stale_notice_is_invalid(research):
    ai,store,c,a,new_run=research
    run=new_run(); ai.data.update_run(run['id'],status='running')
    ai.research_started(run['id']);msg=store.pending()[0]
    store.set_message_state(msg['id'],'uncertain')
    assert ai.data.run(run['id'])['status']=='running'
    ai.finish(run['id'],REPORT)
    assert store.pending()[0]['kind']=='file'
    ai.data.cancel_contact(c['id'])
    assert not store.ai_send_valid(msg) and not store.pending()


@pytest.mark.parametrize('mode,dry_run',[('ai_assist',False),('ai_auto',True)])
def test_preview_and_assist_do_not_send_progress(research,mode,dry_run):
    ai,store,c,a,new_run=research
    store.update_contact(c['id'],mode=mode)
    run=new_run(dry_run=dry_run);ai.data.update_run(run['id'],status='running')
    ai.research_started(run['id'])
    assert not store.pending()


def test_serial_worker_delivers_notice_before_automatic_pdf(tmp_path,monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from wechat_panel.app import create_app
    def native(service,run,state):
        service.ai.research_started(run['id'])
        service.ai.finish(run['id'],REPORT)
    monkeypatch.setattr('wechat_panel.vibe.VibeService.execute',native)
    with TestClient(create_app(tmp_path,demo=True)) as client:
        h={'X-Panel-Request':'1'}
        def post(url,data):
            response=client.post(url,headers=h,json=data)
            assert response.status_code<300,response.text
            return response.json()
        p=post('/api/ai/config/providers',dict(name='test',model='test',base_url='http://localhost:19999/v1'))
        a=post('/api/ai/config/agents',dict(name='test',kind='vibe',vibe_engine='sparkflow',provider_id=p['id']))
        for _ in range(80):
            if client.get('/api/status').json()['state']=='ready':break
            time.sleep(.1)
        c=post('/api/contacts',dict(lookup='分析报告测试',wechat_id='research-test'))
        client.patch('/api/contacts/'+c['id'],headers=h,json=dict(listening=True,mode='ai_auto',ai_agent_id=a['id']))
        client.put('/api/ai/settings',headers=h,json=dict(enabled=True,debounce_seconds=.3,context_messages=20))
        for _ in range(80):
            if client.get('/api/contacts').json()[0]['listener_state']=='listening':break
            time.sleep(.1)
        post('/api/demo/'+c['id']+'/incoming',dict(text='分析一下美股市场'))
        for _ in range(150):
            runs=client.get('/api/ai/runs',params={'contact_id':c['id']}).json()
            if runs and runs[0]['status'] in ('sent','failed','uncertain'):break
            time.sleep(.1)
        assert runs and runs[0]['status']=='sent',runs
        messages=client.get('/api/contacts/'+c['id']+'/messages').json()
        outgoing=[m for m in messages if m['direction']=='outgoing']
        assert [(m['origin'],m['kind'],m['status']) for m in outgoing]==[
            ('ai_progress','text','confirmed'),('ai','file','confirmed')]
