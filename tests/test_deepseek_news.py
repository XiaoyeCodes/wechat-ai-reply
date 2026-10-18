import json

import pytest

from wechat_panel.ai import AIService
from wechat_panel.ai_models import Service
from wechat_panel.companion_services import Services
from wechat_panel.news import request_info, requested, validate_item, render, topic_matches, headline_similarity
from wechat_panel.store import Store


DAY = '2026-10-18'
URL = 'https://news.example.com/story'
BODY = '测试通讯社 | 2026年10月18日 08:00\n2026年10月17日，研究团队发布新的开源工具，本次发布包括中文文档和数据检查功能。' + '该报道解释了发布背景及功能范围。' * 8


def sample():
    return {'page': 0, 'title': '研究团队发布开源工具', 'summary': '团队发布了新的开源工具，包含中文文档和数据检查功能。',
            'publisher': '测试通讯社', 'published_date': DAY, 'published_quote': '测试通讯社 | 2026年10月18日 08:00',
            'event_date': '2026-10-17', 'event_quote': '2026年10月17日，研究团队发布新的开源工具，本次发布包括中文文档和数据检查功能。',
            'claim_quote': '研究团队发布新的开源工具，本次发布包括中文文档和数据检查功能。',
            'event_status': 'happened', 'attribution': ''}


def info():
    return request_info({'created_at': '2026-10-18T05:00:00+00:00', 'input': '汇总5条今日热点新闻'})


def pages():return [{'url': URL, 'content': BODY, 'date': '2026-10-17T16:00:00Z'}]


def test_deepseek_schema_requires_official_endpoint():
    assert Service(name='search', category='search', driver='deepseek', url='').url == 'https://api.deepseek.com'
    with pytest.raises(ValueError):Service(name='bad', category='search', driver='deepseek', url='https://evil.example/v1')


def test_native_search_fetch_protocol_and_bounded_valid_json(monkeypatch):
    services = Services(None);calls = []
    monkeypatch.setattr(services, 'get', lambda *a: {'driver': 'deepseek'})
    monkeypatch.setattr('wechat_panel.companion_services.public_url', lambda u:u)
    def call(s, path, body):
        calls.append((path, body))
        return {'results': [{'url': URL, 'summary': '文' * 20000}]} if path.endswith('search') else {'title': '新闻', 'content': BODY}
    monkeypatch.setattr(services, 'call', call)
    search = json.loads(services.search('sid', '今' * 1000))
    page = json.loads(services.fetch('sid', URL))
    assert calls[0] == ('/v1/tools/search', {'query': '今' * 128, 'count': 5, 'need_content': False})
    assert calls[1] == ('/v1/tools/fetch', {'url': URL})
    assert search['engine'] == 'deepseek' and len(search['results'][0]['summary']) == 1800
    assert page['engine'] == 'deepseek_fetch' and page['content'] == BODY


def test_failed_auth_never_retries_or_leaks_provider_body(monkeypatch):
    services=Services(None);calls=[]
    def fail(*a):calls.append(a);raise RuntimeError('HTTP 401 secret-sensitive-response')
    monkeypatch.setattr(services,'call',fail)
    with pytest.raises(RuntimeError, match='API Key 无效') as error:services.deepseek_call({},'search',{})
    assert len(calls)==1 and 'secret-sensitive' not in str(error.value)


def test_empty_fetch_is_failure(monkeypatch):
    services=Services(None)
    monkeypatch.setattr(services,'get',lambda *a:{'driver':'deepseek'})
    monkeypatch.setattr(services,'deepseek_call',lambda *a:{'content':''})
    monkeypatch.setattr('wechat_panel.companion_services.public_url',lambda u:u)
    with pytest.raises(RuntimeError,match='未取得页面正文'):services.fetch('sid',URL)


def test_media_links_keep_existing_platform_reader(monkeypatch):
    services=Services(None);calls=[]
    monkeypatch.setattr(services,'read_web',lambda url:calls.append(url) or 'subtitles')
    assert services.fetch('deepseek','https://www.bilibili.com/video/BV123')=='subtitles'
    assert calls==['https://www.bilibili.com/video/BV123']


def test_utc_dates_event_dates_and_quotes_are_checked():
    item, error = validate_item(sample(), pages(), info())
    assert not error and item['event_date'] == '2026-10-17' and item['published_date'] == DAY
    for field, value in [('published_date', '2026-10-17'), ('claim_quote', '这是完全不存在的新闻内容和声称已经发生的事实'),
                         ('published_quote', '2026年10月18日 11:00'), ('publisher', '虚构的另一家媒体')]:
        invalid = sample();invalid[field] = value
        assert validate_item(invalid, pages(), info())[0] is None
    mismatch = pages();mismatch[0]['date'] = '2026-10-16T16:00:00Z'
    assert validate_item(sample(), mismatch, info())[0] is None
    future = pages();future[0]['content'] = BODY.replace('08:00', '23:30')
    invalid=sample();invalid['published_quote']=invalid['published_quote'].replace('08:00','23:30')
    assert validate_item(invalid,future,info())[0] is None
    uncertain=sample();uncertain['event_quote']='17日研究团队发布';uncertain['event_date']=DAY
    assert validate_item(uncertain,pages(),info())[0]['event_date']==''
    assert validate_item(uncertain,pages(),info())[0]['event_time_text']==''
    relative=sample();relative['event_date']=''
    assert validate_item(relative,pages(),info())[0]['event_time_text']=='10月17日'


def test_routing_scope_and_requested_dates():
    state={'snapshot':{'agent':{'companion':{'news_verify':True}}}}
    assert requested({'input':'汇总5条今日热点新闻'},state)
    for text in ['今天好累，想你了','不要联网，翻译这段新闻','帮我润色“今天的新闻”']:
        assert not requested({'input':text},state)
    state['snapshot']['agent']['companion']['news_verify']=False
    assert not requested({'input':'今天有哪些新闻'},state)
    assert request_info({'input':'昨天五条新闻','created_at':'2026-10-18T05:00:00Z'})['end']=='2026-10-17'


@pytest.mark.parametrize('text,topics,excluded', [
    ('今天只看民生新闻', ['民生'], []),
    ('我只想看关于国际的', ['国际'], []),
    ('整理3条财经新闻', ['经济'], []),
    ('汇总经济和科技新闻', ['经济','科技'], []),
    ('不要国际和经济，只看民生新闻', ['民生'], ['国际','经济']),
    ('今天新闻汇总，不看国际', [], ['国际']),
    ('今日重要新闻', [], []),
])
def test_category_scope_is_per_request(text, topics, excluded):
    details=request_info({'input':text,'created_at':'2026-10-18T05:00:00Z'})
    assert details['topics']==topics and details['excluded_topics']==excluded
    assert info()['topics']==[]


def test_short_category_commands_do_not_hijack_general_conversation():
    state={'snapshot':{'agent':{'web_policy':'model','companion':{'news_verify':True}},
                       'skills':{'s':{'name':'thepaper-daily-news','enabled':True}}}}
    for text in ['只看民生','我只想看关于国际的','今天看看经济和科技','帮我看民生新闻']:
        assert requested({'input':text},state),text
    for text in ['我想看国际电影','经济是什么','我今天经济压力很大','你看国际新闻吗','帮我翻译国际新闻','不要联网，看看民生新闻']:
        assert not requested({'input':text},state),text
    state['snapshot']['skills']={}
    assert not requested({'input':'只看民生'},state)


def test_category_overlap_exclusion_and_no_economy_livelihood_conflation():
    scope={'topics':['民生'],'excluded_topics':[]}
    assert not topic_matches({'category':'经济'},scope)
    assert topic_matches({'category':'国际','topics':['国际','民生']},scope)
    assert not topic_matches({'category':'国际','topics':['国际','民生']},{**scope,'excluded_topics':['国际']})
    assert not topic_matches({'category':'经济民生'},scope)


def test_headline_resolution_rejects_unrelated_current_results():
    title='某食品公司召回不合格批次产品，消费者可申请退款'
    assert headline_similarity(title,'某食品公司召回不合格批次产品，消费者可申请全额退款_澎湃新闻')>0
    assert headline_similarity(title,'某食品公司召回不合格批次产品')>0
    assert headline_similarity(title,'历史上的今天：某地重要历史事件回顾')==0
    assert headline_similarity(title,'')==0


def test_thepaper_article_metadata_is_parsed_without_joined_model_quotes():
    article={'url':'https://www.thepaper.cn/newsDetail_forward_123','date':'2026-10-17T16:00:00Z',
        'content':'# 标题\n2026\\-10\\-18 08\\:00\n山东\n来源：澎湃新闻·澎湃号·媒体\n字号\n'+BODY}
    item=sample();item['publisher']='澎湃新闻（转载测试通讯社）';item['published_quote']='错误拼接日期和来源'
    valid,error=validate_item(item,[article],{**info(),'source':'thepaper'})
    assert not error and valid['publisher']=='澎湃新闻·澎湃号·媒体' and valid['published_date']==DAY
    article['url']='https://www.thepaper.cn/morningEveningPaper?n=1'
    assert validate_item(item,[article],{**info(),'source':'thepaper'})[0] is None


@pytest.fixture
def setup(tmp_path, monkeypatch):
    ai=AIService(Store(tmp_path/'panel.db'))
    provider=ai.data.save('providers',None,{'name':'test','model':'test','base_url':'http://localhost:9000'})
    tools=[t for t in ai.data.list('tools') if t.get('builtin') in ('web_search','read_web')]
    agent=ai.data.save('agents',None,{'name':'Aria','provider_id':provider['id'],'tool_ids':[t['id'] for t in tools],
        'prompt':'温柔聊天，不使用列表。','companion':{'news_verify':True},'steps':[{'type':'model'},{'type':'reply'}]})
    calls=[]
    def tool(t,args,*a):
        calls.append(t['builtin'])
        if t['builtin']=='web_search':return json.dumps({'results':[{'url':URL,'date':'2026-10-17T16:00:00Z'}]})
        return json.dumps({'url':URL,'content':BODY})
    monkeypatch.setattr(ai.companion,'tool',tool)
    def model(p,m,t):
        if '新闻资料核验员' in m[0]['content']:
            assert t==[]
            assert calls==['web_search','read_web']
            return {'content':json.dumps({'items':[sample(),sample()]},ensure_ascii=False)},{}
        if '本轮是新闻汇总任务' in m[0]['content']:
            assert t==[]
            assert '不要再调用工具' in m[0]['content']
            return {'content':json.dumps({'items':[{'id':'news-1','summary':'这次发布带来了中文文档和数据检查功能。'}]},ensure_ascii=False)},{}
        return {'content':'我在呢。'},{}
    ai.model_call=model
    yield ai,agent,calls,tools
    ai.stop()


def start(ai,agent,text='汇总5条今日热点新闻'):
    r=ai.create_run(agent['id'],text,dry_run=True)
    # Freeze time so this regression stays valid next year.
    with ai.store.connect() as db:db.execute('UPDATE ai_runs SET created_at=? WHERE id=?',('2026-10-18T05:00:00Z',r['id']))
    ai.execute(r['id'])
    return ai.data.run(r['id'])


def test_end_to_end_search_read_verify_handoff_and_shortfall(setup):
    ai,agent,calls,_=setup;r=start(ai,agent)
    assert r['status']=='completed',r['error']
    assert calls==['web_search','read_web']
    assert '不足 5 条' in r['reply'] and '报道：2026-10-18｜事件：2026-10-17' in r['reply']
    assert r['reply'].count(URL)==1 and '这次发布带来了' in r['reply']
    steps=[t['step'] for t in ai.data.detail(r['id'])['trace']]
    assert steps.index('新闻原文读取') < steps.index('新闻日期与来源核验') < steps.index('核验资料交付 Agent') < steps.index('模型处理')
    assert start(ai,agent,'今天好累')['reply']=='我在呢。' and len(calls)==2


def test_failed_fetch_never_passes_search_summary_as_news(setup,monkeypatch):
    ai,agent,calls,_=setup
    def failed(t,args,*a):
        calls.append(t['builtin'])
        return json.dumps({'results':[{'url':URL,'summary':'伪造热点不能进入最终事实包'}]}) if t['builtin']=='web_search' else '{"ok":false,"error":"HTTP 403"}'
    monkeypatch.setattr(ai.companion,'tool',failed)
    r=start(ai,agent)
    assert r['status']=='completed' and '没取得' in r['reply'] and not r['state']['news_packet']['items']
    assert '伪造热点' not in r['reply']


def test_read_approval_resumes_without_repeating_search(setup):
    ai,agent,calls,tools=setup
    reader=next(t for t in tools if t['builtin']=='read_web')
    ai.data.save('tools',reader['id'],{**{k:v for k,v in reader.items() if k not in ('id','key_configured')},'approval':'confirm'})
    r=start(ai,agent)
    assert r['status']=='waiting_approval' and calls==['web_search']
    approval=ai.data.detail(r['id'])['approvals'][0]
    ai.approve(approval['id'],True);ai.execute(r['id'])
    assert ai.data.run(r['id'])['status']=='completed' and calls==['web_search','read_web']


def test_cancel_during_read_stops_before_extraction(setup,monkeypatch):
    ai,agent,calls,_=setup
    def tool(t,args,run,state):
        calls.append(t['builtin'])
        if t['builtin']=='web_search':return json.dumps({'results':[{'url':URL}]})
        ai.data.update_run(run['id'],status='cancelled')
        return json.dumps({'url':URL,'content':BODY})
    monkeypatch.setattr(ai.companion,'tool',tool)
    model_calls=[]
    def unexpected(*a):model_calls.append(list(calls));return {'content':'{}'},{}
    ai.model_call=unexpected
    assert start(ai,agent)['status']=='cancelled'
    assert all('read_web' not in c for c in model_calls)


def test_thepaper_skill_limits_source_loads_once_and_filters_topics(setup,monkeypatch):
    from pathlib import Path
    from wechat_panel.skills import import_package
    ai,agent,calls,_=setup
    content=Path('skills/thepaper-daily-news/SKILL.md').read_text(encoding='utf-8')
    skill=ai.data.save('skills',None,import_package([{'path':'SKILL.md','content':content}]))
    agent=ai.data.save('agents',agent['id'],{**{k:v for k,v in agent.items() if k not in ('id','key_configured')},
        'skill_ids':[skill['id']],'web_policy':'model'})
    paper='https://www.thepaper.cn/newsDetail_forward_123'
    def tool(t,args,*a):
        calls.append(t['builtin'])
        if t['builtin']=='web_search':
            assert 'site:thepaper.cn' in args['query']
            return json.dumps({'results':[{'url':URL},{'url':paper,'date':'2026-10-17T16:00:00Z'}]})
        assert args['url']==paper
        return json.dumps({'url':paper,'content':BODY})
    monkeypatch.setattr(ai.companion,'tool',tool)
    def model(p,m,t):
        if '新闻资料核验员' in m[0]['content']:
            item={**sample(),'important':True,'exclude_reason':'','category':'科技','event_key':'new-tool'}
            gossip={**sample(),'important':False,'exclude_reason':'明星八卦'}
            return {'content':json.dumps({'items':[item,item,gossip]},ensure_ascii=False)},{}
        return {'content':'{}'},{}
    ai.model_call=model
    r=start(ai,agent)
    assert r['status']=='completed',r['error']
    assert r['state']['active_skills']==['thepaper-daily-news']
    assert calls==['web_search','web_search','read_web']
    assert len(r['state']['news_packet']['items'])==1 and paper in r['reply'] and URL not in r['reply']
    assert '澎湃今日重要新闻 1 条' in r['reply'] and '不足' not in r['reply']
    with_foreign=pages();item,error=validate_item(sample(),with_foreign,{**info(),'source':'thepaper'})
    assert item is None and '不是澎湃' in error


@pytest.mark.parametrize('text,selected,other', [
    ('今天只看民生新闻，最多3条', '民生', '经济'),
    ('我只想看关于国际的', '国际', '国内'),
    ('整理3条财经新闻', '经济', '民生'),
])
def test_selected_category_applies_to_search_discovery_evidence_and_reply(setup,monkeypatch,text,selected,other):
    from pathlib import Path
    from wechat_panel.skills import import_package
    ai,agent,calls,_=setup
    skill=ai.data.save('skills',None,import_package([{'path':'SKILL.md',
        'content':Path('skills/thepaper-daily-news/SKILL.md').read_text(encoding='utf-8')}]))
    agent=ai.data.save('agents',agent['id'],{**{k:v for k,v in agent.items() if k not in ('id','key_configured')},
        'skill_ids':[skill['id']],'web_policy':'model'})
    paper='https://www.thepaper.cn/newsDetail_forward_123'
    portal='https://www.thepaper.cn/'
    def tool(t,args,*a):
        calls.append(t['builtin'])
        if t['builtin']=='web_search':
            assert selected in args['query'] and other not in args['query']
            return json.dumps({'results':[{'url':portal},{'url':paper}]})
        return json.dumps({'url':args['url'],'content':BODY})
    monkeypatch.setattr(ai.companion,'tool',tool)
    model_steps=[]
    def model(p,m,t):
        instruction=m[0]['content']
        if '当前目录中选择' in instruction:
            assert '仅选择与指定类别直接相关的新闻：'+selected in instruction
            model_steps.append('discovery')
            return {'content':'{"queries":[]}'},{}
        if '新闻资料核验员' in instruction:
            assert '仅选择与指定类别直接相关的新闻：'+selected in instruction
            assert json.loads(m[1]['content'])['range']['topics']==[selected]
            model_steps.append('extract')
            item={**sample(),'page':1,'important':True,'exclude_reason':'','category':selected,'event_key':'event'}
            outside={**item,'category':other,'title':'不应补进来的其它类别新闻','event_key':'outside'}
            return {'content':json.dumps({'items':[outside,item,item]},ensure_ascii=False)},{}
        return {'content':'{}'},{}
    ai.model_call=model
    r=start(ai,agent,text)
    assert r['status']=='completed',r['error']
    packet=r['state']['news_packet']
    assert model_steps==['discovery','extract']
    assert len(packet['items'])==1 and packet['items'][0]['topics']==[selected]
    assert any(i['reason']=='不符合本次指定新闻类别' for i in packet['rejected'])
    assert f'今日{selected}重要新闻 1 条' in r['reply'] and r['reply'].count(paper)==1
    assert '不应补进来' not in r['reply']
    packet['items']=[]
    assert f'今日{selected}重要新闻' in render(r,r['state'],'{}')
