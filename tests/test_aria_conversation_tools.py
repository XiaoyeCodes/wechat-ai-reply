"""Conversation tools through simulated receipt, AI execution and demo delivery.

These tests use neither the production database nor a real model/WeChat account.
The existing serial worker/pump runs in demo mode behind an in-process API.
"""
import base64
from collections import deque
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from test_simulated_delivery import ThreadedDemoRuntime, post, wait_for
from wechat_panel import runtime
from wechat_panel.app import create_app
from wechat_panel.bridge import DemoBridge
from wechat_panel.failure_replies import REPLY


class ConversationModel:
    def __init__(self):
        self.steps=deque()
        self.calls=[]
        self.vision_calls=[]
        self.names={}

    def __call__(self,provider,messages,tools):
        snapshot=json.loads(json.dumps(messages,ensure_ascii=False))
        if isinstance(messages[-1].get('content'),list):
            self.vision_calls.append(snapshot)
            encoded=messages[-1]['content'][-1]['image_url']['url'].split(',',1)[1]
            with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                assert image.getpixel((0,0))[:3]==(255,0,0)
            return {'content':'这张被引用的图片是红色封面，标识为“红色样本”。'},{}
        self.calls.append(snapshot)
        assert self.steps, 'An unplanned model call escaped the isolated scenario'
        return self.steps.popleft()(snapshot,tools),{}

    def tool(self,builtin,arguments,answer,check_result=None,check_context=None):
        name=self.names[builtin]
        def request(messages,specs):
            assert name in {spec['function']['name'] for spec in specs}
            if check_context:check_context(messages)
            value=arguments(messages) if callable(arguments) else arguments
            return {'role':'assistant','content':'','tool_calls':[{'id':'conversation-tool-'+str(len(self.calls)),
                'type':'function','function':{'name':name,'arguments':json.dumps(value,ensure_ascii=False)}}]}
        self.steps.append(request)
        if answer is not None:
            def complete(messages,specs):
                assert messages[-1]['role']=='tool'
                if check_result:check_result(messages[-1]['content'])
                return {'content':answer}
            self.steps.append(complete)

    def answer(self,answer,check_context=None):
        def complete(messages,specs):
            if check_context:check_context(messages,specs)
            return {'content':answer}
        self.steps.append(complete)


@pytest.fixture
def conversations(tmp_path,monkeypatch):
    model=ConversationModel()
    bridges=[]
    enrichment={}
    class ConversationBridge(DemoBridge):
        def __init__(self):
            super().__init__()
            self.sent=[]
            bridges.append(self)

        def inject(self,contact,text,direction='incoming',sender='',mention_me=False):
            item=super().inject(contact,text,direction,sender,mention_me)
            if direction=='incoming':item.update(enrichment.pop(contact['id'],{}))
            return item

        def send(self,contact,text):
            assert contact['account_id']=='demo-account'
            self.sent.append((contact['id'],text))
            return super().send(contact,text)

    def no_real_bridge():
        pytest.fail('This isolated conversation suite must never connect to real WeChat')
    monkeypatch.setattr(runtime,'WeixinBridge',no_real_bridge)
    monkeypatch.setattr(runtime,'DemoBridge',ConversationBridge)
    app=create_app(tmp_path,demo=True,runtime_factory=ThreadedDemoRuntime,ai_model_call=model)
    with TestClient(app) as client:
        ai,store=app.state.ai,app.state.store
        wait_for(lambda: app.state.runtime.health()['state']=='ready' and store.active_account()=='demo-account')
        assert store.path==tmp_path/'panel.sqlite3' and len(bridges)==1
        provider=ai.data.save('providers',None,dict(name='conversation fixture',model='mock',base_url='http://localhost:1'))
        library=ai.data.save('knowledge',None,dict(name='公开商品说明',documents=[
            dict(title='保温杯退货',content='保温杯退货规则：七天内可以退货，运费以订单说明为准。'),
            dict(title='红色样本',content='红色封面颜色资料：红色样本的主色为红色。')]))
        private=ai.data.save('knowledge',None,dict(name='未绑定内部资料',documents=[
            dict(title='内部退货',content='内部保温杯退货工资 PRIVATE_UNBOUND_NOTICE 不得提供。')]))
        allowed={'remember','memory_search','forget','set_reminder'}
        tools=ai.data.list('tools')
        model.names={tool['builtin']:tool['name'] for tool in tools if tool.get('builtin')}
        agent=ai.data.save('agents',None,dict(name='Aria conversation fixture',provider_id=provider['id'],
            tool_ids=[tool['id'] for tool in tools if tool.get('builtin') in allowed],
            knowledge_ids=[library['id']],knowledge_mode='on_demand',web_policy='off',companion={
                'memory':{'enabled':True,'auto_extract':False},
                'care':{'enabled':True,'quiet_start':'00:00','quiet_end':'00:00','min_gap_minutes':5},
                'media':{'understand_images':True,'vision_provider_id':provider['id']},
                'safety':{'emotion_enabled':False,'crisis_detection':False,'require_adult':False}}))
        response=client.put('/api/ai/settings',headers={'X-Panel-Request':'1'},
            json=dict(enabled=True,debounce_seconds=.3,context_messages=30))
        assert response.status_code==200,response.text
        env=SimpleNamespace(client=client,ai=ai,store=store,agent=agent,model=model,bridge=bridges[0],
            enrichment=enrichment,library=library,private=private)
        yield env
        assert not enrichment


def chat(env,kind='direct',label='目标会话'):
    body=dict(lookup=label,chat_type=kind)
    if kind=='group':body.update(group_members=['张三','李四'],group_self_name='机器人')
    else:body['wechat_id']='demo-'+label
    contact=post(env.client,'/api/contacts',body)
    result=env.client.patch('/api/contacts/'+contact['id'],headers={'X-Panel-Request':'1'},
        json=dict(listening=True,mode='ai_auto',ai_agent_id=env.agent['id']))
    assert result.status_code==200,result.text
    wait_for(lambda: env.store.contact(contact['id'])['listener_state']=='listening')
    return env.store.contact(contact['id'])


def incoming(env,contact,text,*,sender='张三',extra=None,waiting=False):
    previous={run['id'] for run in env.ai.data.runs(contact['id'])}
    before=len(env.bridge.sent)
    if extra:env.enrichment[contact['id']]=extra
    body=dict(text=text)
    if contact['chat_type']=='group':body.update(sender=sender,mention_me=True)
    source=post(env.client,'/api/demo/'+contact['id']+'/incoming',body)
    def ready():
        return next((env.ai.data.run(run['id']) for run in env.ai.data.runs(contact['id'])
            if run['id'] not in previous and run['status'] in
                ('waiting_approval','sent','failed','cancelled','uncertain')),None)
    run=wait_for(ready)
    assert run['status']==('waiting_approval' if waiting else 'sent'),run
    trigger=env.store.message(run['trigger_id'])
    assert trigger['source_key']==source['key'] and trigger['contact_id']==contact['id']
    if not waiting:
        assert len(env.bridge.sent)==before+1
        messages=[message for message in env.store.messages(contact['id']) if message.get('ai_run_id')==run['id']]
        assert len(messages)==1 and messages[0]['status']=='confirmed'
        assert env.bridge.sent[-1]==(contact['id'],messages[0]['text'])
        assert not env.store.pending()
    else:assert len(env.bridge.sent)==before
    return run


def no_duplicate(env,contact):
    sent=list(env.bridge.sent)
    count=len(env.ai.data.runs(contact['id']))
    rows=env.bridge.snapshot(contact)
    env.store.ingest(contact['id'],rows)
    env.store.ingest(contact['id'],rows)
    env.ai._collect()
    env.client.app.state.runtime.request('probe')
    assert env.bridge.sent==sent and len(env.ai.data.runs(contact['id']))==count and not env.store.pending()


def contains(messages,*values):
    body=json.dumps(messages,ensure_ascii=False)
    assert all(value in body for value in values),body


def test_private_remember_search_forget_is_a_delivered_multiturn_conversation(conversations):
    env=conversations
    target=chat(env)
    decoy=chat(env,label='其他联系人')
    def saved(text):assert json.loads(text)['saved'] is True
    env.model.tool('remember',dict(key='饮品偏好',content='喜欢无糖乌龙茶'),'记住啦，你喜欢无糖乌龙茶。',saved)
    first=incoming(env,target,'记住我的饮品偏好，我喜欢无糖乌龙茶')
    memory=env.ai.companion.memory
    entries=memory.list_memories(first['state']['scope'])
    assert len(entries)==1 and entries[0]['content']=='喜欢无糖乌龙茶'
    def found(text):
        rows=json.loads(text)
        assert len(rows)==1 and rows[0]['content']=='喜欢无糖乌龙茶'
    env.model.tool('memory_search',{'query':'饮品偏好'},'你喜欢无糖乌龙茶。',found,
        lambda messages: contains(messages,'无糖乌龙茶'))
    incoming(env,target,'你还记得我喜欢喝什么吗')
    env.model.tool('forget',{'memory_id':entries[0]['id']},'已忘记这个偏好。')
    forgotten=incoming(env,target,'请忘记刚才的饮品偏好')
    assert not memory.list_memories(first['state']['scope'])
    def absent(text):assert json.loads(text)==[]
    env.model.tool('memory_search',{'query':'饮品偏好'},'现在没有记录你的饮品偏好。',absent)
    last=incoming(env,target,'现在还有我的饮品偏好吗')
    assert not last['state']['dynamic_context']['memories']
    assert '无糖乌龙茶' not in json.dumps(last['state']['context'],ensure_ascii=False)
    assert memory.profile(forgotten['state']['scope'])['forget_before']>0
    assert not env.store.messages(decoy['id']) and len(env.bridge.sent)==4
    no_duplicate(env,target)
    assert not env.model.steps


def test_group_memory_is_per_member_across_middle_and_trailing_mentions(conversations):
    env=conversations
    target=chat(env,'group')
    env.model.tool('remember',{'key':'颜色偏好','content':'张三喜欢蓝色'},'记住了，你喜欢蓝色。')
    alice=incoming(env,target,'请@机器人\u2005记住我喜欢蓝色',sender='张三')
    env.model.tool('remember',{'key':'颜色偏好','content':'李四喜欢绿色'},'记住了，你喜欢绿色。')
    bob=incoming(env,target,'记住我喜欢绿色@机器人\u2005',sender='李四')
    assert alice['state']['scope']!=bob['state']['scope']
    def only_alice(text):
        values=json.loads(text)
        assert len(values)==1 and values[0]['content']=='张三喜欢蓝色'
    env.model.tool('memory_search',{'query':'颜色偏好'},'你喜欢蓝色。',only_alice,
        lambda messages: contains(messages,'张三喜欢蓝色'))
    last=incoming(env,target,'我喜欢什么颜色@机器人\u2005',sender='张三')
    assert '李四喜欢绿色' not in json.dumps(last['state']['context'],ensure_ascii=False)
    memories=env.ai.companion.memory.list_memories(alice['state']['scope'])
    env.model.tool('forget',{'memory_id':memories[0]['id']},'已经忘记你的颜色偏好。')
    incoming(env,target,'请@机器人\u2005忘记我的颜色偏好',sender='张三')
    assert not env.ai.companion.memory.list_memories(alice['state']['scope'])
    def only_bob(text):
        values=json.loads(text)
        assert len(values)==1 and values[0]['content']=='李四喜欢绿色'
    env.model.tool('memory_search',{'query':'颜色偏好'},'你喜欢绿色。',only_bob)
    incoming(env,target,'我喜欢什么颜色@机器人\u2005',sender='李四')
    assert len(env.bridge.sent)==5 and all(cid==target['id'] for cid,_ in env.bridge.sent)
    no_duplicate(env,target)
    assert not env.model.steps


@pytest.mark.parametrize('kind',['direct','group'])
def test_quoted_text_reaches_knowledge_tool_and_answer_stays_in_original_chat(conversations,kind):
    env=conversations
    target=chat(env,kind)
    decoy=chat(env,label='禁止接收者')
    quoted='保温杯订单的退货规则是什么？'
    def results(text):
        rows=json.loads(text)
        assert rows and all(row['knowledge_id']==env.library['id'] for row in rows)
        assert '七天' in text and 'PRIVATE_UNBOUND_NOTICE' not in text
    env.model.tool('knowledge_search',{'query':'保温杯退货规则'},'可以在七天内退货，运费按订单说明处理。',results,
        lambda messages: contains(messages,quoted,'结合每条引用与对应的新问题'))
    question='请结合这句引用，查一下退货规定'+('@机器人\u2005' if kind=='group' else '')
    run=incoming(env,target,question,extra={'quote':{
        'kind':'text','sender':'商家','text':quoted,'status':'ready'}})
    assert run['state']['quoted_messages'][0]['text']==quoted
    assert env.bridge.sent[-1][1]=='可以在七天内退货，运费按订单说明处理。'
    assert not env.ai.companion.memory.list_memories(run['state']['scope'])
    assert not env.store.messages(decoy['id'])
    no_duplicate(env,target)
    assert not env.model.steps


@pytest.mark.parametrize('kind',['direct','group'])
def test_quoted_image_and_followup_reach_tool_without_cross_member_context(conversations,kind):
    env=conversations
    target=chat(env,kind)
    output=BytesIO()
    Image.new('RGB',(40,60),'red').save(output,format='PNG')
    picture=env.ai.companion.memory.media('wechat:'+target['id'],output.getvalue())
    def results(text):assert '红色样本' in text and 'PRIVATE_UNBOUND_NOTICE' not in text
    env.model.tool('knowledge_search',{'query':'红色样本封面颜色'},'图中是红色封面，与资料中的红色样本一致。',results,
        lambda messages: contains(messages,'红色封面','quoted'))
    question='请结合引用图片查一下样本颜色'+('@机器人\u2005' if kind=='group' else '')
    first=incoming(env,target,question,extra={'quote':{
        'kind':'image','sender':'原图作者','text':'图片','status':'ready','media_id':picture['id']}})
    observations=first['state']['dynamic_context']['images']
    assert len(observations)==1 and observations[0]['quoted'] and observations[0]['status']=='ready'
    assert observations[0]['media_id']==picture['id'] and len(env.model.vision_calls)==1
    env.model.tool('knowledge_search',{'query':'红色样本颜色'},'主要颜色仍是红色。',results,
        lambda messages: contains(messages,'红色封面','question_observation'))
    follow=incoming(env,target,'刚才那张图片的主色再核对一下'+('@机器人\u2005' if kind=='group' else ''))
    assert follow['state']['dynamic_context']['images'][0]['media_id']==picture['id']
    assert len(env.model.vision_calls)==2
    if kind=='group':
        def no_foreign_image(messages,specs):
            assert '红色封面' not in json.dumps(messages,ensure_ascii=False)
        env.model.answer('你还没有提供需要查询的图片。',no_foreign_image)
        bob=incoming(env,target,'请看看我的图片@机器人\u2005',sender='李四')
        assert not bob['state']['dynamic_context'].get('images')
    assert all(cid==target['id'] for cid,_ in env.bridge.sent)
    no_duplicate(env,target)
    assert not env.model.steps


@pytest.mark.parametrize('approved',[True,False])
def test_reminder_waits_for_approval_then_fires_once_only_to_private_requester(conversations,approved):
    env=conversations
    target=chat(env)
    decoy=chat(env,label='其他联系人')
    memory=env.ai.companion.memory
    scope=memory.scope(env.agent['id'],target['id'])
    profile=memory.profile(scope,env.agent['id'],target['id'])
    memory.set_profile(scope,profile['data']|{'care_consent':True})
    at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    env.model.tool('set_reminder',dict(title='喝水提醒',at=at,instruction='提醒用户喝水。'),'提醒已设置。')
    pending=incoming(env,target,'请在明天这个时间提醒我喝水',waiting=True)
    assert not memory.schedules() and not env.bridge.sent
    approval=env.ai.data.detail(pending['id'])['approvals'][0]
    post(env.client,'/api/ai/approvals/'+approval['id'],{'approved':approved})
    if not approved:
        assert env.ai.data.run(pending['id'])['status']=='cancelled'
        assert not memory.schedules() and not env.bridge.sent
        env.model.steps.clear()  # A rejected tool never reaches the planned completion turn.
        return
    wait_for(lambda: env.ai.data.run(pending['id'])['status']=='sent')
    schedules=memory.schedules()
    assert len(schedules)==1 and schedules[0]['contact_id']==target['id']
    assert env.bridge.sent==[(target['id'],'提醒已设置。')]
    env.model.answer('该喝水啦。',lambda messages,specs: contains(messages,'提醒用户喝水'))
    stamp=datetime.now(timezone.utc)
    with env.store.connect() as db:
        db.execute('UPDATE companion_schedules SET next_at=? WHERE id=?',
            ((stamp-timedelta(seconds=1)).isoformat(),schedules[0]['id']))
    env.ai.scheduler.tick(stamp)
    env.ai.scheduler.tick(stamp)
    def sent_reminder():
        return next((run for run in env.ai.data.runs(target['id'])
            if run['schedule_id']==schedules[0]['id'] and run['status']=='sent'),None)
    occurrence=wait_for(sent_reminder)
    assert env.bridge.sent==[(target['id'],'提醒已设置。'),(target['id'],'该喝水啦。')]
    delivered=[message for message in env.store.messages(target['id'])
        if message.get('ai_run_id')==occurrence['id']]
    assert len(delivered)==1 and delivered[0]['status']=='confirmed'
    with env.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM companion_occurrences').fetchone()[0]==1
    assert not env.store.messages(decoy['id'])
    no_duplicate(env,target)
    assert not env.model.steps


def test_group_cannot_create_another_members_private_care_reminder(conversations):
    env=conversations
    target=chat(env,'group')
    def no_personal_reminder(messages,specs):
        assert env.model.names['set_reminder'] not in {spec['function']['name'] for spec in specs}
    env.model.answer('个人提醒请在私聊中设置。',no_personal_reminder)
    run=incoming(env,target,'请@机器人\u2005明天提醒我喝水')
    assert not env.ai.companion.memory.schedules()
    assert env.model.names['set_reminder'] not in {
        tool['name'] for tool in run['state']['snapshot']['tools'].values()}
    assert env.bridge.sent==[(target['id'],'个人提醒请在私聊中设置。')]


@pytest.mark.parametrize('kind',['direct','group'])
def test_tool_failure_delivers_only_short_notice_without_commands_or_paths(conversations,monkeypatch,kind):
    env=conversations
    target=chat(env,kind)
    decoy=chat(env,label='禁止接收者')
    def unavailable(*args,**kwargs):
        raise RuntimeError('py -3.12 scripts/run_daily.py --workspace C:/Users/private/reports HTTP 403 api-key-secret')
    monkeypatch.setattr(env.ai.companion.memory,'save_memory',unavailable)
    env.model.tool('remember',dict(key='偏好',content='模拟失败'),None)
    run=incoming(env,target,'请记住这个模拟偏好'+('@机器人\u2005' if kind=='group' else ''))
    assert run['reply']==REPLY and run['state']['task_failure']
    assert env.bridge.sent==[(target['id'],REPLY)]
    assert not any(value in env.bridge.sent[-1][1] for value in ('C:/','py -3','scripts/','403','api-key'))
    assert not env.store.messages(decoy['id'])
    no_duplicate(env,target)
    assert not env.model.steps
