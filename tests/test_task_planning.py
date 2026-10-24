"""Task constraints with fake model/tool receipts; never import a live Runtime."""
from copy import deepcopy
import json

import pytest

from wechat_panel import task_planning as planning
from wechat_panel.failure_replies import REPLY


class FakeData:
    def __init__(self):self.updates=[];self.traces=[]
    def update_run(self,run_id,**changes):self.updates.append((run_id,deepcopy(changes)))
    def trace(self,*args):self.traces.append(deepcopy(args))


class FakeAI:
    def __init__(self,answer=None):self.data=FakeData();self.answer=answer;self.calls=[]
    def model_call(self,provider,messages,tools):
        self.calls.append(deepcopy((provider,messages,tools)))
        if isinstance(self.answer,Exception):raise self.answer
        if self.answer is None:raise RuntimeError('fake planner unavailable')
        return {'content':json.dumps(self.answer,ensure_ascii=False)},{}


def tool(name,**extra):
    return {'id':name,'name':name,'builtin':name,'kind':'builtin','enabled':True,
            'approval':'auto','description':name,'parameters':{'type':'object','properties':{}},**extra}


def env(text,answer=None):
    names=['weather','web_search','read_web','generate_image','financial_daily','memory_search','remember','forget',
           'knowledge_search','music_search','movie_search','calculator','clock','set_reminder','speak',
           'platform_read','platform_transcribe','news_collect','use_skill','read_skill_file','search_capabilities']
    tools={name:tool(name) for name in names}
    tools['disabled']=tool('disabled',enabled=False)
    tools['maps_weather']=tool('maps_weather',builtin='',kind='mcp',capability='amap',remote_name='maps_weather')
    tools['maps_ip_location']=tool('maps_ip_location',builtin='',kind='mcp',capability='amap',remote_name='maps_ip_location')
    skills={sid:{'id':sid,'name':name,'enabled':True,'description':description,
                 'instructions':'DO NOT EXPOSE FULL SKILL INSTRUCTIONS','files':[{'name':'secret.txt','content':'never expose'}]}
        for sid,name,description in [('daily','Ai日报','读取AI工具集最新一期，生成9:16图片'),
            ('hot','Ai热点新闻','读取AI热点新闻前十名，生成海报'),
            ('sixty','每日新闻日报','读取60秒新闻，生成新闻卡片'),
            ('finance','jin10-financial-daily','生成财经日报PDF及审校图片') ]}
    run={'id':'isolated-run','input':text,'status':'running','contact_id':'private-a','agent_id':'aria'}
    state={'snapshot':{'agent':{'kind':'workflow','web_policy':'model','companion':{'memory':{'enabled':False}}},
                       'tools':tools,'skills':skills,'provider':{'id':'fake-model','api_key':'TEST-NOT-A-SECRET'}},
           'active_skills':[],'artifacts':[]}
    return FakeAI(answer),run,state


def step(sid,title,kind,tools,dependencies=(),skill=''):
    value={'id':sid,'title':title,'kind':kind,'tool_ids':list(tools),'depends_on':list(dependencies)}
    if skill:value['skill_id']=skill
    return value


def prepared(text,answer=None):
    ai,run,state=env(text,answer);planning.prepare(ai,run,state)
    return ai,run,state


def image(state,mid):
    item={'id':mid,'kind':'image','mime_type':'image/png'}
    state['artifacts'].append(item)
    return item


def record(state,name,args,result,cid):
    planning.record_tool(state,state['snapshot']['tools'][name],args,json.dumps(result,ensure_ascii=False),cid)


@pytest.mark.parametrize('text',['你好呀','今天工作很累，陪我聊聊','上海天气怎么样？','帮我查询上海天气',
    '这张图片的主要颜色是什么？','请只查询AI日报文字版'])
def test_simple_turn_has_no_extra_model_call(text):
    ai,run,state=prepared(text)
    assert ai.calls==[] and 'task_plan' not in state
    assert not planning.bypass_shortcuts(state) and planning.prompt(state)==''


@pytest.mark.parametrize('text',['请把今天AI热榜前十做成一张海报','请把今日AI日报做成一张海报',
    '把每天60秒读懂世界的新闻做成卡片'])
def test_single_skill_read_then_generation_is_complex(text):
    ai,run,state=prepared(text)
    assert len(ai.calls)==1
    assert [s['kind'] for s in state['task_plan']['steps']]==['read','generate_image']
    assert not planning.include_text(state)


def test_one_bounded_structured_call_and_metadata_only():
    answer={'steps':[step('weather','查询上海天气','query',['weather']),
        step('read','读取AI日报','read',['read_web'],skill='daily'),
        step('draw','生成AI日报海报','generate_image',['generate_image'],['read'],'daily')]}
    ai,run,state=prepared('先查询上海天气，再把AI日报做成一张海报',answer)
    planning.prepare(ai,run,state)
    assert len(ai.calls)==1 and state['task_plan']['source']=='model'
    provider,messages,tools=ai.calls[0]
    assert tools==[] and provider['_structured'] and provider['max_tokens']<=2200
    metadata=json.loads(messages[-1]['content'])
    assert metadata['request']==run['input']
    assert not any(t['id'] in ('disabled','maps_ip_location','use_skill') for t in metadata['tools'])
    raw=json.dumps(metadata)
    assert 'DO NOT EXPOSE' not in raw and 'never expose' not in raw and 'api_key' not in raw
    assert planning.include_text(state) and planning.bypass_shortcuts(state)
    assert all(s['status']=='pending' and not s['evidence'] for s in state['task_plan']['steps'])


@pytest.mark.parametrize('bad',[
    {'steps':[step('a','只有天气，漏掉海报','query',['weather'])]},
    {'steps':[step('a','读取AI日报','read',['read_web'],skill='daily'),step('b','假装生成','generate_image',['read_web'],['a'],'daily'),step('c','查询天气','query',['weather'])]},
    {'steps':[dict(step('a','天气','query',['weather']),status='done')]},
    {'steps':[step('a','未知工具','query',['not-bound'])]},
    {'steps':[step('a','禁用工具','query',['disabled'])]},
    {'steps':[step('a','元工具不是执行','query',['search_capabilities'])]},
    {'steps':[step('a','前向依赖','query',['weather'],['b']),step('b','循环','query',['weather'],['a'])]},
    {'steps':[step('a','重复','query',['weather']),step('a','重复','query',['read_web'])]},
    {'steps':[step('a','未知技能','read',['read_web'],skill='not-bound')]},
    {'steps':[step(f's{i}','超过六步','query',['weather']) for i in range(7)]},
])
def test_untrusted_or_incomplete_model_plan_cannot_reduce_request(bad):
    ai,run,state=prepared('请查询上海天气，再把AI日报做成一张海报',bad)
    assert state['task_plan']['source']=='fallback'
    steps=state['task_plan']['steps']
    assert {s['kind'] for s in steps}=={'query','read','generate_image'}
    assert any(s['skill_name']=='Ai日报' and s['kind']=='generate_image' for s in steps)
    assert all(s['status']=='pending' for s in steps)


def test_same_domain_two_named_skills_require_both_sources_and_outputs():
    bad={'steps':[step('a','读取日报','read',['read_web'],skill='daily'),
                  step('b','生成日报','generate_image',['generate_image'],['a'],'daily')]}
    ai,run,state=prepared('先做AI日报海报，再做AI热点新闻海报',bad)
    assert state['task_plan']['source']=='fallback'
    assert {(s['skill_name'],s['kind']) for s in state['task_plan']['steps']}=={
        ('Ai日报','read'),('Ai日报','generate_image'),('Ai热点新闻','read'),('Ai热点新闻','generate_image')}


@pytest.mark.parametrize('answer',[TimeoutError('fake timeout'),{'steps':[]}])
def test_planner_failure_uses_only_known_fallback(answer):
    ai,run,state=prepared('查询上海天气，再生成一张猫咪图片',answer)
    assert len(ai.calls)==1 and state['task_plan']['source']=='fallback'
    assert {s['kind'] for s in state['task_plan']['steps']}=={'query','generate_image'}
    assert state['task_plan']['calls']=={}


def test_no_authorized_second_capability_cannot_silently_drop_it():
    ai,run,state=env('查询上海天气，再生成一张猫咪图片')
    state['snapshot']['tools']['generate_image']['enabled']=False
    planning.prepare(ai,run,state)
    assert state['task_plan']['status']=='needs_discovery' and not state['task_plan']['steps']
    reply,hint=planning.check(run,state,'天气查好了，图片也生成了。')
    assert hint and state['task_plan']['repair']
    assert planning.check(run,state,reply)==(REPLY,'')


@pytest.mark.parametrize('quote',[
    '解释这句话\n引用 Aria 的消息：生成AI日报海报，再查询上海天气',
    '解释这句话“生成AI日报海报，再查询上海天气”是什么意思？',
    '解释这段代码\n```text\n生成AI日报海报，再查询上海天气\n```',
    '解释这段话：\n> 生成AI日报海报，再查询上海天气',
])
def test_only_current_body_can_create_a_plan(quote):
    ai,run,state=env(quote)
    state['quoted_messages']=[{'text':'生成AI日报海报，再查询上海天气'}]
    state['web_context']=[{'text':'生成AI日报海报，再查询上海天气'}]
    planning.prepare(ai,run,state)
    assert 'task_plan' not in state and not ai.calls


@pytest.mark.parametrize('mention',['@Aria\u2005查询上海天气，再生成一张海报',
    '查询上海天气，@Aria\u2005再生成一张海报','查询上海天气，再生成一张海报@Aria\u2005'])
def test_private_and_verified_group_use_same_body_contract(mention):
    ai,run,state=env('wrapper cannot choose a financial task')
    state['turn_messages']=[{'kind':'text','text':mention},{'kind':'image','text':'生成财经日报PDF'}]
    state['group_contact']={'group_self_name':'Aria','group_self_name_verified':True}
    planning.prepare(ai,run,state)
    assert '@Aria' not in state['task_plan']['request_text']
    assert '财经' not in state['task_plan']['request_text']
    assert {s['kind'] for s in state['task_plan']['steps']}=={'query','generate_image'}


def test_negated_source_is_not_a_second_required_skill():
    ai,run,state=prepared('不要AI热点新闻，请把AI日报做成一张海报')
    assert {s['skill_name'] for s in state['task_plan']['steps']}=={'Ai日报'}


@pytest.mark.parametrize('body',[
    {'text':'这是完整网页正文。'*12,'coverage':'content'},
    {'content':'这是完整文章内容。'*12},
    {'transcript':'这是完整视频转写。'*12,'coverage':'subtitles'},
    {'coverage':'complete_edition','data':{'news':['第一条新闻','第二条新闻']}},
    {'coverage':'complete_latest_edition','data':{'items':[{'title':'AI新进展','content':'实际新闻正文'}]}},
    {'ok':True,'receipt':{'items':3,'deep_items':3},'data':[{'title':'新闻','content':'深读正文'}]},
])
def test_actual_reader_and_news_payload_shapes_supply_body_evidence(body):
    ai,run,state=prepared('请把AI日报做成一张海报')
    state['active_skills']=['Ai日报']
    record(state,'read_web',{'url':'https://ai-bot.cn/daily-ai-news/'},body,'read')
    assert state['task_plan']['steps'][0]['status']=='done'
    assert state['task_plan']['steps'][1]['status']=='pending'


@pytest.mark.parametrize('body',[
    {'text':'只有搜索摘要。'*15,'coverage':'snippet'},
    {'text':'仅有页面元信息。'*15,'coverage':'metadata_only'},
    {'content':'网页被截断。'*15,'truncated':True},
    {'text':'失败时也附有一些说明。'*15,'ok':False},
    {'content':'错误页面。'*15,'error':'blocked'},
    {'coverage':'complete_latest_edition','data':{'items':[]}},
    {'title':'只是标题','description':'没有正文'},
])
def test_metadata_partial_and_error_do_not_finish_read_step(body):
    ai,run,state=prepared('请把AI日报做成一张海报')
    state['active_skills']=['Ai日报']
    record(state,'read_web',{'url':'https://ai-bot.cn/daily-ai-news/'},body,'read')
    assert state['task_plan']['steps'][0]['status']!='done'
    assert planning.before_tool(state,state['snapshot']['tools']['generate_image'],{'prompt':'海报'})


@pytest.mark.parametrize('args,result_url',[
    ({'url':'https://aihot.news/hot'},'https://aihot.news/hot'),
    ({'url':'https://ai-bot.cn/other-page'},'https://ai-bot.cn/other-page'),
    ({'url':'https://ai-bot.cn/daily-ai-news/'},'https://aihot.news/hot'),
    ({'url':'https://ai-bot.cn.evil.invalid/daily-ai-news/'},''),
])
def test_read_requires_this_named_source_not_other_news(args,result_url):
    ai,run,state=prepared('把AI日报做成一张海报')
    state['active_skills']=['Ai日报']
    record(state,'read_web',args,{'url':result_url,'text':'这是完整的实际正文。'*20},'read')
    assert state['task_plan']['steps'][0]['status']=='pending'


def test_two_sources_read_out_of_order_are_credited_once_to_correct_skill():
    ai,run,state=prepared('先做AI日报海报，再做AI热点新闻海报')
    state['active_skills']=['Ai日报','Ai热点新闻']
    record(state,'read_web',{'url':'https://aihot.news/hot'},
        {'url':'https://aihot.news/hot','coverage':'complete_latest_edition','data':{'items':[{'title':'热点'}]}},'hot-read')
    steps=state['task_plan']['steps']
    assert [s['status'] for s in steps]==['pending','pending','done','pending']
    hot=image(state,'hot-image')
    assert not planning.before_tool(state,state['snapshot']['tools']['generate_image'],{'prompt':'热点海报'})
    record(state,'generate_image',{'prompt':'热点海报'},hot,'hot-generate')
    assert [s['status'] for s in steps]==['pending','pending','done','done']
    record(state,'read_web',{'url':'https://ai-bot.cn/daily-ai-news/'},
        {'text':'完整AI日报正文。'*15},'daily-read')
    daily=image(state,'daily-image')
    record(state,'generate_image',{'prompt':'日报海报'},daily,'daily-generate')
    assert all(s['status']=='done' for s in steps)
    assert len({e['call_id'] for s in steps for e in s['evidence']})==4
    assert planning.check(run,state,'两张海报已经生成。')==('两张海报已经生成。','')


def test_read_dependency_blocks_generation_before_cost_and_skill_must_load():
    ai,run,state=prepared('把AI日报做成一张海报')
    reader=state['snapshot']['tools']['read_web'];generator=state['snapshot']['tools']['generate_image']
    assert planning.before_tool(state,reader,{'url':'https://ai-bot.cn/daily-ai-news/'})
    assert planning.before_tool(state,generator,{'prompt':'日报'})
    state['active_skills']=['Ai日报']
    assert not planning.before_tool(state,reader,{'url':'https://ai-bot.cn/daily-ai-news/'})
    assert planning.before_tool(state,generator,{'prompt':'日报'})
    record(state,'read_web',{'url':'https://ai-bot.cn/daily-ai-news/'},{'text':'完整正文。'*15},'r')
    assert not planning.before_tool(state,generator,{'prompt':'日报'})
    record(state,'generate_image',{},image(state,'new'),'g')
    assert planning.before_tool(state,generator,{'prompt':'重复生成'})
    assert not planning.before_tool(state,reader,{'url':'https://ai-bot.cn/daily-ai-news/'})
    assert not planning.before_tool(state,state['snapshot']['tools']['use_skill'],{'name':'Ai日报'})


@pytest.mark.parametrize('quoted',[False,True])
def test_incoming_or_quoted_image_cannot_be_new_generation(quoted):
    ai,run,state=prepared('查询上海天气，再生成一张图片')
    if quoted:state['quoted_messages']=[{'media_id':'old-image'}]
    else:state['input_images']=[{'media_id':'old-image'}]
    state['weather_results']=[{'tool':'weather','ok':True,'city':'上海'}]
    record(state,'weather',{'city':'上海'},{'weather':{'temperature_2m':20}},'w')
    record(state,'generate_image',{},image(state,'old-image'),'g')
    assert state['task_plan']['steps'][1]['status']=='failed'
    assert not state['task_plan']['steps'][1]['evidence']


def test_duplicate_call_id_and_same_artifact_cannot_cover_two_outputs():
    ai,run,state=prepared('先做AI日报海报，再做AI热点新闻海报')
    state['active_skills']=['Ai日报','Ai热点新闻']
    for sid,url in [('d','https://ai-bot.cn/daily-ai-news/'),('h','https://aihot.news/hot')]:
        record(state,'read_web',{'url':url},{'text':'完整新闻正文。'*20},sid)
    media=image(state,'only-image')
    record(state,'generate_image',{},media,'generate-once')
    record(state,'generate_image',{},media,'generate-once')
    assert len([s for s in state['task_plan']['steps'] if s['status']=='done'])==3
    record(state,'generate_image',{},media,'different-call-same-image')
    assert state['task_plan']['steps'][3]['status']=='failed'
    assert planning.check(run,state,'两份全部完成了')[1]
    assert planning.check(run,state,'两份全部完成了')==(REPLY,'')


def test_actual_weather_receipt_and_reply_must_both_cover_independent_query():
    ai,run,state=prepared('查询上海天气，再生成一张猫咪图片')
    record(state,'weather',{'city':'上海'},{'weather':{'temperature_2m':20}},'fake-weather')
    assert state['task_plan']['steps'][0]['status']=='failed'
    state['weather_results']=[{'tool':'weather','ok':True,'city':'上海'}]
    record(state,'weather',{'city':'上海'},{'weather':{'temperature_2m':20}},'actual-weather')
    record(state,'generate_image',{},image(state,'cat'),'actual-image')
    assert state['task_plan']['status']=='complete'
    assert planning.check(run,state,'图片已经生成。')[1]
    assert planning.check(run,state,'上海天气晴，20℃，猫咪图片已生成。')==('上海天气晴，20℃，猫咪图片已生成。','')


def test_authorized_weather_alternative_requires_real_related_mcp_receipt():
    answer={'steps':[step('w','查询上海天气','query',['weather','maps_weather']),
        step('i','生成猫咪图片','generate_image',['generate_image'],['w'])]}
    ai,run,state=prepared('查询上海天气，再生成一张猫咪图片',answer)
    state['mcp_results']=[{'tool':'maps_weather','ok':True,'capability':'amap'}]
    record(state,'maps_weather',{'city':'上海'},{'ok':True,'result':{'content':[{'type':'text','text':'晴20℃'}]}},'mcp')
    assert state['task_plan']['steps'][0]['status']=='done'
    assert state['task_plan']['steps'][0]['evidence'][0]['tool_id']=='maps_weather'


def test_uncertain_writes_never_get_an_automatic_repeat():
    ai,run,state=prepared('查询上海天气，再生成一张图片')
    state['weather_results']=[{'tool':'weather','ok':True,'city':'上海'}]
    record(state,'weather',{'city':'上海'},{'weather':{'temperature_2m':20}},'w')
    record(state,'generate_image',{}, {'status':'uncertain'},'uncertain-generation')
    assert planning.before_tool(state,state['snapshot']['tools']['generate_image'],{})
    assert planning.check(run,state,'我再生成一次')==(REPLY,'')
    assert 'repair' not in state['task_plan']


def test_clarification_cannot_accidentally_send_partial_media_or_a_pdf_question():
    ai,run,state=prepared('查询天气，再制作金十财经日报PDF')
    state['artifacts']=[{'id':'partial-image','kind':'image'}]
    state['pdf']={'url':'/api/ai/runs/isolated-run/pdf'}
    state['financial_daily']={'status':'ready','validation':{'passed':True}}
    state['output_format']='pdf'
    assert planning.check(run,state,'请告诉我你想查询哪个城市的天气？')==('请告诉我你想查询哪个城市的天气？','')
    assert state['task_plan']['status']=='needs_input'
    assert not state['artifacts'] and 'pdf' not in state and 'financial_daily' not in state
    assert state['output_format']=='text'


def test_generic_pdf_is_delivery_format_not_a_nonexistent_tool_step():
    answer={'steps':[step('r','读取并分析网页','read',['read_web']),
        step('c','组织中文分析结果','compose',[],['r'])]}
    ai,run,state=prepared('请阅读这个网页https://example.invalid/report，再整理成中文分析PDF',answer)
    assert state['task_plan']['source']=='model'
    assert all(s['kind']!='generate_pdf' for s in state['task_plan']['steps'])
    record(state,'read_web',{'url':'https://example.invalid/report'},{'text':'真实报告正文。'*20},'read')
    assert planning.check(run,state,'已经完成了')[1]
    reply='报告显示三个主要变化。第一，需求增长而供给保持稳定；第二，成本随产量提高而下降；第三，应持续观察未来季度数据。'
    assert planning.check(run,state,reply)==(reply,'')
    assert state['task_plan']['steps'][1]['evidence'][0]['kind']=='response'


def test_financial_pdf_requires_verified_current_run_artifact():
    ai,run,state=prepared('先查询上海天气，再生成金十财经日报PDF')
    state['active_skills']=['jin10-financial-daily']
    record(state,'financial_daily',{}, {'status':'ready'},'fake-ready')
    financial=next(s for s in state['task_plan']['steps'] if s['kind']=='generate_pdf')
    assert financial['status']=='failed'
    state['financial_daily']={'status':'ready','validation':{'passed':True}}
    state['pdf']={'url':'/api/ai/runs/isolated-run/pdf'}
    record(state,'financial_daily',{}, {'status':'ready'},'actual-ready')
    assert financial['status']=='done' and financial['evidence'][0]['kind']=='pdf'
    assert not any(s['kind']=='generate_image' for s in state['task_plan']['steps'])


def test_approved_reminder_evidence_is_not_a_preview_or_verbal_claim():
    ai,run,state=prepared('查询上海天气，再提醒我十分钟后喝水')
    reminder=next(s for s in state['task_plan']['steps'] if s['kind']=='action')
    record(state,'set_reminder',{}, {'id':'preview-reminder','preview':True},'preview')
    assert reminder['status']=='failed'
    record(state,'set_reminder',{}, {'id':'approved-reminder','status':'active'},'approved')
    assert reminder['status']=='done' and reminder['evidence'][0]['artifact_id']=='approved-reminder'


def test_meta_calls_never_count_as_task_evidence():
    ai,run,state=prepared('查询上海天气，再生成一张图片')
    for name in ('use_skill','read_skill_file','search_capabilities'):
        record(state,name,{}, {'status':'done','content':'任务已完成'},name)
    assert state['task_plan']['calls']=={}
    assert all(s['status']=='pending' for s in state['task_plan']['steps'])


def test_pdf_plus_image_delivery_does_not_add_irrelevant_text():
    state={'task_plan':{'request_text':'先生成财经日报PDF，再生成一张猫咪图片','steps':[
        step('p','财经日报PDF','generate_pdf',['financial_daily']),step('i','猫咪图片','generate_image',['generate_image'])]}}
    assert not planning.include_text(state)
    state['task_plan']['request_text']+='，另外用文字总结财经重点'
    assert planning.include_text(state)


def test_negative_media_does_not_turn_a_single_news_query_into_planning():
    ai,run,state=prepared('看下AIHot的AI热点新闻热榜，要完整前十和热度，只要文字，不要海报')
    assert not ai.calls and 'task_plan' not in state


@pytest.mark.parametrize('group',[False,True])
def test_explicit_auto_memory_is_deferred_application_work_not_a_fake_search(group):
    answer={'steps':[step('w','查询上海天气','query',['weather'])]}
    text='记住我喜欢茉莉花茶，再查询上海天气'
    ai,run,state=env(text,answer)
    state['snapshot']['agent']['companion']['memory']={'enabled':True,'auto_extract':True}
    if group:
        state['turn_messages']=[{'kind':'text','text':'@Aria\u2005'+text}]
        state['group_contact']={'group_self_name':'Aria','group_self_name_verified':True}
    planning.prepare(ai,run,state)
    assert state['task_plan']['deferred_memory'] and state['task_plan']['source']=='model'
    assert '"deferred_memory": true' in planning.prompt(state)
    assert [s['tool_ids'] for s in state['task_plan']['steps']]==[['weather']]
    assert 'deferred_memory' in ai.calls[0][1][0]['content']
    assert 'remember' not in {t['id'] for t in json.loads(ai.calls[0][1][-1]['content'])['tools']}


def test_automatic_memory_receipts_require_actual_local_save(tmp_path):
    from wechat_panel.ai import AIService
    from wechat_panel.store import Store
    store=Store(tmp_path/'receipt-only.sqlite3')
    ai=AIService(store,model_call=lambda *args:pytest.fail('Model/network prohibited'))
    try:
        provider=ai.data.save('providers',None,{'name':'isolated','model':'fake','base_url':'http://localhost:1'})
        agent=ai.data.save('agents',None,{'name':'isolated','provider_id':provider['id'],'web_policy':'off',
            'companion':{'memory':{'enabled':True,'auto_extract':True}}})
        for text,content,evidence,expected in [
            ('记住我喜欢茉莉花茶，再查询上海天气','喜欢茉莉花茶','记住我喜欢茉莉花茶',True),
            ('帮我查询上海天气','住在上海','上海',False)]:
            run=ai.create_run(agent['id'],text,dry_run=True)
            run=ai.data.run(run['id'])
            state=run['state'];profile=ai.companion.memory.profile(state['scope'],agent['id'])
            state.update(companion_prepared=True,memory_revision=profile['revision'],analysis={'summary':'','mood':'',
                'facts':[{'key':'饮品偏好','content':content,'category':'preference','evidence':evidence}]})
            ai.companion.remember_turn(run,state,'已处理')
            receipts=state.get('automatic_memory_receipts',[])
            assert bool(receipts)==expected
            if expected:
                assert receipts[0]['run_id']==run['id'] and receipts[0]['scope']==state['scope']
                assert receipts[0]['memory_id'] in {m['id'] for m in ai.companion.memory.list_memories(state['scope'])}
    finally:ai.stop()


@pytest.mark.parametrize('text',['画一张猫，再画一张狗','生成两张图片'])
def test_explicit_two_images_cannot_be_reduced_to_one_model_step(text):
    answer={'steps':[step('i','生成图片','generate_image',['generate_image'])]}
    ai,run,state=prepared(text,answer)
    assert state['task_plan']['source']=='fallback'
    assert len(state['task_plan']['steps'])==2
    assert all(s['kind']=='generate_image' for s in state['task_plan']['steps'])


def test_two_plain_image_outputs_need_two_distinct_actual_artifacts():
    ai,run,state=prepared('生成两张图片')
    generator=state['snapshot']['tools']['generate_image']
    assert not planning.before_tool(state,generator,{})
    record(state,'generate_image',{},image(state,'first'),'first')
    assert state['task_plan']['status']=='pending'
    assert not planning.before_tool(state,generator,{})
    record(state,'generate_image',{},image(state,'second'),'second')
    assert state['task_plan']['status']=='complete'
    assert planning.before_tool(state,generator,{})


def test_over_limit_images_clarify_without_model_or_partial_generation():
    ai,run,state=prepared('生成7张海报')
    assert state['task_plan']['status']=='needs_input' and not ai.calls
    assert planning.before_tool(state,state['snapshot']['tools']['generate_image'],{})
    state['artifacts']=[{'kind':'image','id':'must-not-send'}]
    reply,hint=planning.check(run,state,'全部做好了')
    assert '每批最多6张' in reply and not hint and not state['artifacts']
    assert state['output_format']=='text'


def test_platform_reader_nested_body_is_accepted_but_nested_metadata_is_not():
    answer={'steps':[step('r','读取视频完整转写','read',['platform_transcribe']),
        step('c','组织分析','compose',[],['r'])]}
    ai,run,state=prepared('阅读网页视频，再整理成中文分析PDF',answer)
    record(state,'platform_transcribe',{'target':'https://example.invalid/video'},
        {'ok':True,'coverage':'subtitles','data':{'transcript':'真实字幕内容。'*20,'coverage':'subtitles'}},'subtitles')
    assert state['task_plan']['steps'][0]['status']=='done'
    assert not planning._body({'ok':True,'coverage':'metadata_only','data':{'text':'仅有元信息。'*20}})
    assert not planning._body({'coverage':'complete_edition','data':{'error':'blocked','news':['不能使用的错误数据']}})


def test_memory_search_cannot_replace_an_explicit_forget_in_a_complex_task():
    answer={'steps':[step('w','天气','query',['weather']),step('m','删除偏好','query',['memory_search'])]}
    ai,run,state=prepared('查询上海天气，再忘掉我喝茶的偏好',answer)
    assert state['task_plan']['source']=='fallback'
    assert any(s['kind']=='action' and s['tool_ids']==['forget'] for s in state['task_plan']['steps'])


@pytest.mark.parametrize('wording',['告诉我今天Ai日报的三条新闻内容','介绍一下Ai日报的新闻重点',
    '列举Ai日报的三条新闻','说明Ai日报的主要结论和摘要'])
def test_independent_news_text_and_unrelated_cat_image_are_both_required(wording):
    answer={'steps':[step('r','读取AI日报的新闻正文','read',['read_web'],skill='daily'),
        step('i','生成独立猫咪图片','generate_image',['generate_image'])]}
    ai,run,state=prepared(wording+'，然后帮我生成一张猫咪图片。',answer)
    assert state['task_plan']['source']=='model'
    assert state['task_plan']['delivery_text'] and planning.include_text(state)
    assert state['task_plan']['steps'][1]['skill_name']==''
    # Planner failure must preserve the same two actual user outputs.
    ai,run,state=prepared(wording+'，然后帮我生成一张猫咪图片。')
    assert [s['kind'] for s in state['task_plan']['steps']]==['read','generate_image']
    assert state['task_plan']['steps'][1]['skill_name']=='' and planning.include_text(state)


def test_independent_compose_after_image_requests_text_delivery():
    answer={'steps':[step('i','生成猫咪图片','generate_image',['generate_image']),
        step('c','用文字介绍图片','compose',[],['i'])]}
    ai,run,state=prepared('生成一张猫咪图片，然后用文字介绍一下它',answer)
    assert state['task_plan']['source']=='model' and planning.include_text(state)


def test_no_skill_news_can_search_real_sources_without_loading_a_skill():
    ai,run,state=env('搜索今天的科技新闻，再计算18*9')
    state['snapshot']['skills']={}
    planning.prepare(ai,run,state)
    assert [s['tool_ids'] for s in state['task_plan']['steps']]==[['web_search'],['calculator']]
    record(state,'web_search',{'query':'今天科技新闻'},{'source':'https://search-provider.invalid/','results':[]},'empty')
    assert state['task_plan']['steps'][0]['status']=='failed'
    record(state,'web_search',{'query':'今天科技新闻'},
        {'results':[{'title':'今日科技消息','url':'https://publisher.invalid/technology','text':'实际搜索结果'}]},'search')
    record(state,'calculator',{'expression':'18*9'},162,'calculate')
    assert state['task_plan']['status']=='complete' and not state['active_skills']
    proof=state['task_plan']['steps'][0]['evidence'][0]
    assert proof['provider']=='web_search' and proof['sources']==['https://publisher.invalid/technology']


def test_irrelevant_skill_does_not_prevent_news_search_and_image_generation():
    ai,run,state=env('搜索今天的科技新闻，然后做成一张海报')
    state['snapshot']['skills']={'poem':{'id':'poem','name':'写诗','description':'只写古诗','enabled':True}}
    planning.prepare(ai,run,state)
    assert [s['kind'] for s in state['task_plan']['steps']]==['query','generate_image']
    assert all(not s['skill_id'] for s in state['task_plan']['steps'])
    record(state,'web_search',{'query':'科技新闻'},
        {'content':[{'type':'text','text':'Title: 科技新闻\nURL: https://publisher.invalid/news\nText: 实际新闻摘要'}]},'search')
    assert not planning.before_tool(state,state['snapshot']['tools']['generate_image'],{})
    record(state,'generate_image',{},image(state,'news-poster'),'draw')
    assert state['task_plan']['status']=='complete'


def test_disabled_source_and_unrelated_news_skills_do_not_block_builtin_search():
    ai,run,state=env('搜索AI日报的最新新闻，然后计算18*9')
    state['snapshot']['skills']['daily']['enabled']=False
    planning.prepare(ai,run,state)
    assert state['task_plan']['steps'][0]['tool_ids']==['web_search']
    assert all(not s['skill_id'] for s in state['task_plan']['steps'])
    metadata=json.loads(ai.calls[0][1][-1]['content'])
    assert 'daily' not in {s['id'] for s in metadata['skills']}


def test_generic_full_text_news_search_must_then_read_before_generating():
    bad={'steps':[step('s','搜索新闻','query',['web_search']),step('i','生成海报','generate_image',['generate_image'],['s'])]}
    ai,run,state=env('搜索今天科技新闻的全文，然后生成一张图片',bad)
    state['snapshot']['skills']={}
    planning.prepare(ai,run,state)
    assert state['task_plan']['source']=='fallback'
    assert [s['kind'] for s in state['task_plan']['steps']]==['query','read','generate_image']
    reader=state['snapshot']['tools']['read_web'];generator=state['snapshot']['tools']['generate_image']
    assert planning.before_tool(state,reader,{'url':'https://publisher.invalid/news'})
    record(state,'web_search',{'query':'今天科技新闻'},
        {'results':[{'title':'新闻','url':'https://publisher.invalid/news','text':'搜索摘要'}]},'search')
    assert not planning.before_tool(state,reader,{'url':'https://publisher.invalid/news'})
    assert planning.before_tool(state,generator,{})
    record(state,'read_web',{'url':'https://publisher.invalid/news'},{'text':'来源完整正文。'*20},'read')
    assert not planning.before_tool(state,generator,{})
    record(state,'generate_image',{},image(state,'full-poster'),'draw')
    assert state['task_plan']['status']=='complete'


@pytest.mark.parametrize('domain,text,disabled',[
    ('weather','查询上海天气，再生成一张猫咪图片',['weather','maps_weather']),
    ('movie','查询电影流浪地球的介绍，再生成一张猫咪图片',['movie_search']),
    ('places','查询上海附近的餐厅，再生成一张猫咪图片',[]),
])
def test_missing_specialized_information_tool_can_use_search_sources(domain,text,disabled):
    ai,run,state=env(text)
    state['snapshot']['skills']={}
    for name in disabled:state['snapshot']['tools'][name]['enabled']=False
    planning.prepare(ai,run,state)
    assert state['task_plan']['steps'][0]['tool_ids']==['web_search']
    assert state['task_plan']['steps'][1]['kind']=='generate_image'
    record(state,'web_search',{'query':text},{'error':'unavailable','results':[
        {'title':'不应使用','url':'https://publisher.invalid/untrusted'}]},'bad')
    assert state['task_plan']['steps'][0]['status']=='failed'
    payload=({'title':'上海今日天气预报','url':'https://publisher.invalid/weather','text':'上海天气晴，气温25℃'}
        if domain=='weather' else {'title':'检索结果','url':'https://publisher.invalid/'+domain,'text':'实际公开资料'})
    record(state,'web_search',{'query':text},{'results':[
        payload]},'actual')
    assert state['task_plan']['steps'][0]['status']=='done'
    assert not state.get('weather_results') and not state.get('mcp_results')
    assert not planning.before_tool(state,state['snapshot']['tools']['generate_image'],{})


@pytest.mark.parametrize('blocked',['web_off','search_disabled'])
def test_search_fallback_never_bypasses_network_or_tool_authorization(blocked):
    ai,run,state=env('搜索今日科技新闻，再生成一张图片')
    state['snapshot']['skills']={}
    if blocked=='web_off':state['snapshot']['agent']['web_policy']='off'
    else:state['snapshot']['tools']['web_search']['enabled']=False
    planning.prepare(ai,run,state)
    assert state['task_plan']['status']=='needs_discovery' and not state['task_plan']['steps']
    assert planning.before_tool(state,state['snapshot']['tools']['generate_image'],{})


def test_search_cannot_impersonate_existing_weather_or_any_effect_step():
    answer={'steps':[step('w','查询上海天气','query',['web_search']),step('i','生成猫咪图片','generate_image',['generate_image'])]}
    ai,run,state=prepared('查询上海天气，再生成一张猫咪图片',answer)
    assert state['task_plan']['source']=='fallback' and 'weather' in state['task_plan']['steps'][0]['tool_ids']
    assert 'web_search' not in state['task_plan']['steps'][0]['tool_ids']
    result={'results':[{'title':'搜索不执行操作','url':'https://publisher.invalid/news','text':'返回了网页'}]}
    for kind in ('action','generate_image','generate_pdf','read'):
        with pytest.raises(ValueError):
            planning._validate({'steps':[step('fake','假冒实际操作',kind,['web_search'])]},
                planning._tools(run,state),state['snapshot']['skills'])
        fake=step('fake','假冒实际操作',kind,['web_search'])
        assert planning._proof(state,fake,state['snapshot']['tools']['web_search'],{},result) is None


def weather_news_search_env(answer=None):
    ai,run,state=env('查询上海天气，并搜索今日科技新闻',answer)
    state['snapshot']['skills']={}
    for name in ('weather','maps_weather'):state['snapshot']['tools'][name]['enabled']=False
    planning.prepare(ai,run,state)
    return ai,run,state


NEWS_SEARCH={'results':[{'title':'今日科技新闻：人工智能模型更新','url':'https://publisher.invalid/tech',
    'text':'多家科技公司发布人工智能模型，这是今天的科技新闻。'}]}
SHANGHAI_WEATHER={'results':[{'title':'上海今日天气预报','url':'https://weather.invalid/shanghai',
    'text':'上海今日天气晴，最高气温25℃，最低温度18℃。'}]}


def test_repeated_news_search_receipts_cannot_complete_weather():
    ai,run,state=weather_news_search_env()
    record(state,'web_search',{'query':'今日科技新闻'},NEWS_SEARCH,'news-first')
    assert [s['status'] for s in state['task_plan']['steps']]==['pending','done']
    record(state,'web_search',{'query':'今日科技新闻'},NEWS_SEARCH,'news-repeat')
    assert [s['status'] for s in state['task_plan']['steps']]==['pending','done']
    assert planning.check(run,state,'上海晴25℃，科技新闻已更新。')[1]
    assert planning.check(run,state,'上海晴25℃，科技新闻已更新。')==(REPLY,'')


def test_search_receipts_are_credited_to_correct_domain_when_out_of_order():
    ai,run,state=weather_news_search_env()
    record(state,'web_search',{'query':'今日科技新闻'},NEWS_SEARCH,'news')
    record(state,'web_search',{'query':'上海 今日天气预报'},SHANGHAI_WEATHER,'weather')
    assert state['task_plan']['status']=='complete'
    assert [s['evidence'][0]['call_id'] for s in state['task_plan']['steps']]==['weather','news']
    assert planning.check(run,state,'上海今日天气晴25℃，科技公司发布了新模型。')==('上海今日天气晴25℃，科技公司发布了新模型。','')


@pytest.mark.parametrize('query,payload',[
    ('北京今日天气',{'results':[{'title':'北京天气','url':'https://weather.invalid/beijing','text':'北京晴20℃'}]}),
    ('上海今日天气',{'results':[{'title':'北京天气','url':'https://weather.invalid/beijing','text':'北京晴20℃'}]}),
    ('上海今日天气',NEWS_SEARCH),
    ('上海今日科技新闻',SHANGHAI_WEATHER),
])
def test_weather_fallback_rejects_wrong_city_unrelated_content_or_query(query,payload):
    ai,run,state=weather_news_search_env()
    record(state,'web_search',{'query':query},payload,'wrong')
    assert state['task_plan']['steps'][0]['status']=='pending'
    assert not state['task_plan']['steps'][0]['evidence']


def test_model_cannot_collapse_two_information_domains_into_one_search_step():
    answer={'steps':[step('one','搜索天气和科技新闻','query',['web_search'])]}
    ai,run,state=weather_news_search_env(answer)
    assert state['task_plan']['source']=='fallback'
    assert len(state['task_plan']['steps'])==2


@pytest.mark.parametrize('payload',[
    {'query':'上海今日天气','results':[{'title':'北京天气预报','url':'https://weather.invalid/beijing','text':'北京晴20℃'}]},
    {'results':[{'title':'上海科技新闻','url':'https://publisher.invalid/shanghai','text':'上海模型企业发布新产品'},
        {'title':'北京天气预报','url':'https://weather.invalid/beijing','text':'北京晴20℃'}]},
])
def test_weather_cannot_borrow_echoed_query_or_another_results_city(payload):
    ai,run,state=weather_news_search_env()
    record(state,'web_search',{'query':'上海今日天气'},payload,'wrong')
    assert state['task_plan']['steps'][0]['status']=='pending'


@pytest.mark.parametrize('exa',[False,True])
def test_mixed_search_results_accept_one_actual_same_city_weather_source(exa):
    ai,run,state=weather_news_search_env()
    if exa:payload={'content':[{'type':'text','text':
        'Title: 北京天气\nURL: https://weather.invalid/beijing\nText: 北京晴20℃\n'
        'Title: 上海天气\nURL: https://weather.invalid/shanghai\nText: 上海晴，气温25℃'}]}
    else:payload={'results':[NEWS_SEARCH['results'][0],SHANGHAI_WEATHER['results'][0]]}
    record(state,'web_search',{'query':'上海今日天气'},payload,'weather')
    assert state['task_plan']['steps'][0]['status']=='done'
    assert state['task_plan']['steps'][1]['status']=='pending'


@pytest.mark.parametrize('label',['Query','Search Query','搜索词','搜索关键词'])
def test_exa_query_metadata_cannot_supply_weather_city_evidence(label):
    ai,run,state=weather_news_search_env()
    payload={'content':[{'type':'text','text':label+': 上海今日天气\nURL: https://weather.invalid/beijing\nText: 北京晴20℃'}]}
    record(state,'web_search',{'query':'上海今日天气'},payload,'echo')
    assert state['task_plan']['steps'][0]['status']=='pending'


def test_exa_url_and_author_metadata_are_not_weather_source_text():
    ai,run,state=weather_news_search_env()
    payload={'content':[{'type':'text','text':
        'URL: https://weather.invalid/beijing?q=上海今日天气\nAuthor: 上海天气\nText: 北京晴20℃'}]}
    record(state,'web_search',{'query':'上海今日天气'},payload,'metadata')
    assert state['task_plan']['steps'][0]['status']=='pending'


@pytest.mark.parametrize('prefix',['先到网上查','帮我在网上查','请联网查询','先上网查'])
def test_natural_online_prefix_preserves_actual_requested_weather_city(prefix):
    ai,run,state=env(prefix+'上海明天天气，再搜索今天的AI新闻')
    state['snapshot']['skills']={}
    for name in ('weather','maps_weather'):state['snapshot']['tools'][name]['enabled']=False
    planning.prepare(ai,run,state)
    assert state['task_plan']['steps'][0]['request_targets']==['上海']
    record(state,'web_search',{'query':'上海明天天气预报'},SHANGHAI_WEATHER,'weather')
    record(state,'web_search',{'query':'今天的AI新闻'},NEWS_SEARCH,'news')
    assert state['task_plan']['status']=='complete'
