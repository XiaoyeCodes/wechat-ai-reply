"""Generated-image followups through isolated incoming messages and demo sends."""
import base64
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from test_simulated_delivery import demo_pipeline, post, wait_for
from wechat_panel.failure_replies import REPLY


@pytest.fixture
def image_chat(demo_pipeline,monkeypatch):
    client,agent,bridge,_,_=demo_pipeline
    ai,store=client.app.state.ai,client.app.state.store
    body={k:v for k,v in agent.items() if k not in ('id','key_configured')}
    body['companion']['media'].update(understand_images=True,vision_provider_id=agent['provider_id'])
    agent=ai.data.save('agents',agent['id'],body)
    env=SimpleNamespace(client=client,ai=ai,store=store,bridge=bridge,agent=agent,agent_body=body,
        generation=[],vision=[],explicit=None,observed=False)

    def generate(service_id,prompt,media,reference=None,**kwargs):
        output=BytesIO()
        Image.new('RGB',(24,24),'purple' if reference else 'blue').save(output,format='PNG')
        data=output.getvalue()
        env.generation.append(dict(reference=reference,prompt=prompt,data=data))
        return data
    monkeypatch.setattr(ai.companion.services,'generate_image',generate)

    def model(provider,messages,specs):
        if isinstance(messages[-1].get('content'),list):
            url=messages[-1]['content'][-1]['image_url']['url']
            data=base64.b64decode(url.split(',',1)[1])
            env.vision.append(data)
            env.observed=True
            return {'content':'原图是一幅蓝色山水图片，主要颜色是蓝色。'},{}
        if messages[-1]['role']=='tool':return {'content':'图片已经生成。'},{}
        question=str(messages[-1].get('content',''))
        if '背景改成夜空' in question or '请生成一张蓝色山水图片' in question:
            assert 'generate_image' in {spec['function']['name'] for spec in specs}
            arguments={'prompt':question,'mode':'edit' if '背景改成夜空' in question else 'generate'}
            if env.explicit:arguments['reference_media_id']=env.explicit
            return {'role':'assistant','content':'','tool_calls':[{'id':'generated-followup',
                'type':'function','function':{'name':'generate_image',
                'arguments':json.dumps(arguments,ensure_ascii=False)}}]},{}
        return {'content':'主要颜色是蓝色。' if env.observed else '还没有读取到这张原图。'},{}
    ai.model_call=model
    return env


def add_chat(env,kind='direct',name='生成图测试'):
    body=dict(lookup=name,chat_type=kind)
    if kind=='group':body.update(group_members=['模拟成员','其他成员'],group_self_name='模拟助手')
    else:body['wechat_id']=name+'-id'
    result=post(env.client,'/api/contacts',body)
    response=env.client.patch('/api/contacts/'+result['id'],headers={'X-Panel-Request':'1'},
        json=dict(listening=True,mode='ai_auto',ai_agent_id=env.agent['id']))
    assert response.status_code==200,response.text
    wait_for(lambda: env.store.contact(result['id'])['listener_state']=='listening')
    return env.store.contact(result['id'])


def incoming(env,contact,question,sender='模拟成员'):
    previous={run['id'] for run in env.ai.data.runs(contact['id'])}
    body=dict(text=question)
    if contact['chat_type']=='group':body.update(text=question+' @模拟助手',sender=sender,mention_me=True)
    source=post(env.client,'/api/demo/'+contact['id']+'/incoming',body)
    def ready():
        return next((env.ai.data.run(run['id']) for run in env.ai.data.runs(contact['id'])
            if run['id'] not in previous and run['status'] in ('sent','failed','uncertain','cancelled')),None)
    run=wait_for(ready)
    assert run['status']=='sent',run
    assert env.store.message(run['trigger_id'])['source_key']==source['key']
    messages=[message for message in env.store.messages(contact['id']) if message.get('ai_run_id')==run['id']]
    assert len(messages)==1 and messages[0]['status']=='confirmed'
    assert messages[0]['contact_id']==contact['id'] and run['contact_id']==contact['id']
    assert not env.store.pending()
    return run,messages[0]


def initial_image(env,contact):
    run,message=incoming(env,contact,'请生成一张蓝色山水图片')
    assert message['kind']=='image' and len(env.generation)==1
    assert env.generation[0]['reference'] is None
    original=env.ai.companion.memory.read_media(message['media_id'])['data']
    assert original==env.generation[0]['data']
    return run,message,original


@pytest.mark.parametrize('kind',['direct','group'])
def test_confirmed_generated_image_can_be_edited_and_delivered_to_its_requester(image_chat,kind):
    env=image_chat
    target=add_chat(env,kind)
    decoy=add_chat(env,name='无关联系人')
    first,message,original=initial_image(env,target)
    second,edited=incoming(env,target,'把这张图背景改成夜空')
    assert edited['kind']=='image',second
    assert len(env.generation)==2
    assert base64.b64decode(env.generation[1]['reference'])==original
    assert env.generation[1]['data']!=original
    assert env.bridge.images==[(target['id'],original),(target['id'],env.generation[1]['data'])]
    assert first['state']['scope']==second['state']['scope']
    assert any(ref.get('media_id')==message['media_id'] for ref in second['state']['input_images'])
    assert not env.store.messages(decoy['id'])
    snapshot=env.bridge.snapshot(target)
    env.store.ingest(target['id'],snapshot)
    env.store.ingest(target['id'],snapshot)
    env.ai._collect()
    env.client.app.state.runtime.request('probe')
    assert len(env.generation)==len(env.bridge.images)==2
    assert len(env.ai.data.runs(target['id']))==2


@pytest.mark.parametrize('kind',['direct','group'])
def test_confirmed_generated_image_is_available_to_a_normal_visual_followup(image_chat,kind):
    env=image_chat
    target=add_chat(env,kind)
    _,_,original=initial_image(env,target)
    assert not env.vision
    _,answer=incoming(env,target,'这张图片的主色是什么？')
    assert answer['kind']=='text' and answer['text']=='主要颜色是蓝色。'
    assert env.vision and all(data==original for data in env.vision)
    assert len(env.generation)==len(env.bridge.images)==1
    assert all(cid==target['id'] for cid,_ in env.bridge.deliveries)


BOUNDARIES=[
    ('other_member','group',False),('other_member','group',True),
    ('other_contact','direct',False),('other_contact','direct',True),
    ('other_contact','group',False),('other_contact','group',True),
    ('agent_rebound','direct',False),('agent_rebound','direct',True),
    ('agent_rebound','group',False),('agent_rebound','group',True),
    ('uncertain','direct',False),('uncertain','group',False),
    ('cancelled','direct',False),('cancelled','group',False),
]


@pytest.mark.parametrize('boundary,kind,explicit',BOUNDARIES)
def test_generated_reference_does_not_cross_delivery_identity_or_binding_boundaries(image_chat,boundary,kind,explicit):
    env=image_chat
    owner=add_chat(env,kind)
    first,message,original=initial_image(env,owner)
    target,sender=owner,'模拟成员'
    if boundary=='other_contact':target=add_chat(env,kind,name='另一个会话')
    elif boundary=='other_member':sender='其他成员'
    elif boundary=='agent_rebound':
        replacement=env.ai.data.save('agents',None,env.agent_body|{'name':'另一个Agent'})
        response=env.client.patch('/api/contacts/'+owner['id'],headers={'X-Panel-Request':'1'},
            json=dict(ai_agent_id=replacement['id']))
        assert response.status_code==200,response.text
        env.client.app.state.runtime.configure()
        target=env.store.contact(owner['id'])
    else:
        # Inject an ambiguous/cancelled receipt into this temporary store only.
        # The visible image row must not override explicit nonconfirmed state.
        env.store.set_message_state(message['id'],boundary,'模拟未确认或取消的发送')
        assert env.ai.data.run(first['id'])['status']==boundary
    if explicit:env.explicit=message['media_id']
    prompt='针对上一张图片，请把背景改成夜空' if explicit else '把这张图背景改成夜空'
    second,answer=incoming(env,target,prompt,sender)
    assert answer['kind']=='text' and answer['text']==REPLY,second
    assert len(env.generation)==len(env.bridge.images)==1
    assert env.bridge.images==[(owner['id'],original)]
    assert env.bridge.deliveries[-1]==(target['id'],REPLY)
    assert len(env.bridge.deliveries)==2
    if boundary in ('uncertain','cancelled'):
        assert env.store.message(message['id'])['status']==boundary
    assert not any(ref.get('media_id')==message['media_id']
                   for ref in second['state'].get('input_images',[]))


@pytest.mark.parametrize('kind',['direct','group'])
def test_older_quoted_image_does_not_override_a_newer_confirmed_generated_image(image_chat,kind):
    from wechat_panel.store import now
    env=image_chat
    target=add_chat(env,kind)
    buffer=BytesIO();Image.new('RGB',(24,24),'red').save(buffer,format='PNG')
    quoted=env.ai.companion.memory.media('wechat:'+target['id'],buffer.getvalue())
    # Insert historical conversation evidence without creating a fresh incoming
    # event or invoking another AI task in the isolated demonstration runtime.
    quote={'kind':'image','status':'ready','media_id':quoted['id'],'sender':'原作者','text':'[图片]'}
    with env.store.connect() as db:
        cursor=db.execute('''INSERT INTO messages(contact_id,direction,text,status,created_at,source_key,
                            origin,kind,quote,sender,sender_verified,historical)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (target['id'],'incoming','之前引用过的图片','observed',now(),'old-quoted-image',
             'wechat','text',json.dumps(quote,ensure_ascii=False),'模拟成员',1,0))
        older_quote_id=cursor.lastrowid
    _,generated,original=initial_image(env,target)
    assert older_quote_id<generated['id']
    _,edited=incoming(env,target,'把这张图背景改成夜空')
    assert edited['kind']=='image' and len(env.generation)==2
    assert base64.b64decode(env.generation[1]['reference'])==original
    assert base64.b64decode(env.generation[1]['reference'])!=buffer.getvalue()


@pytest.mark.parametrize('kind',['direct','group'])
@pytest.mark.parametrize('boundary',['uncertain','cancelled','previous_binding'])
def test_explicit_generated_media_id_does_not_bypass_associated_message_boundary(image_chat,kind,boundary):
    env=image_chat
    target=add_chat(env,kind)
    first,message,original=initial_image(env,target)
    if boundary=='previous_binding':
        replacement=env.ai.data.save('agents',None,env.agent_body|{'name':'临时切换Agent'})
        # Switching back to the same agent restores the same member/media scope,
        # but deliberately starts a new conversation binding boundary.
        for aid in (replacement['id'],env.agent['id']):
            response=env.client.patch('/api/contacts/'+target['id'],headers={'X-Panel-Request':'1'},
                json=dict(ai_agent_id=aid))
            assert response.status_code==200,response.text
        env.client.app.state.runtime.configure()
        target=env.store.contact(target['id'])
        assert target['ai_context_start_message']>=message['id']
    else:
        env.store.set_message_state(message['id'],boundary,'隔离测试：送达状态尚未确认')
        assert env.ai.data.run(first['id'])['status']==boundary
    env.explicit=message['media_id']
    _,answer=incoming(env,target,'针对上一张图片，请把背景改成夜空')
    assert answer['kind']=='text' and answer['text']==REPLY
    assert len(env.generation)==len(env.bridge.images)==1
    assert env.bridge.images==[(target['id'],original)]


@pytest.mark.parametrize('kind',['direct','group'])
def test_explicit_same_scope_preview_without_a_wechat_message_remains_editable(image_chat,kind):
    from wechat_panel.group_chats import member_scope
    env=image_chat
    target=add_chat(env,kind)
    scope=env.ai.companion.memory.scope(env.agent['id'],target['id'])
    if kind=='group':scope=member_scope(scope,'模拟成员')
    buffer=BytesIO();Image.new('RGB',(24,24),'green').save(buffer,format='PNG')
    preview=env.ai.companion.memory.media(scope,buffer.getvalue())
    with env.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM messages WHERE media_id=?',(preview['id'],)).fetchone()[0]==0
    env.explicit=preview['id']
    _,answer=incoming(env,target,'针对预览图片，请把背景改成夜空')
    assert answer['kind']=='image' and len(env.generation)==1
    assert base64.b64decode(env.generation[0]['reference'])==buffer.getvalue()
    assert len(env.bridge.images)==1 and env.bridge.images[0][0]==target['id']
