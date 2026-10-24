"""Receive -> AI -> serial delivery, entirely in a temporary demo environment."""
from io import BytesIO
import json
from pathlib import Path
from queue import Queue
from threading import Thread
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from wechat_panel import runtime
from wechat_panel.app import create_app
from wechat_panel.bridge import DemoBridge
from wechat_panel.financial_pdf import report_path


class ThreadedDemoRuntime(runtime.Runtime):
    """Use the real serial worker/pump with queues, explicitly prohibiting UI I/O."""
    def start(self):
        assert self.demo is True, 'These acceptance tests must never select real WeChat'
        self.store.recover()
        self.store.activate_account('')
        self.commands,self.output=Queue(),Queue()
        self.process=Thread(target=runtime.worker_main,
            args=(self.commands,self.output,True,str(self.store.path)),daemon=True)
        self.process.start()
        self.configure()
        self.thread=Thread(target=self._pump,daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.commands.put(dict(op='stop'))
        self.process.join(timeout=2)
        self.thread.join(timeout=2)
        assert not self.process.is_alive() and not self.thread.is_alive()


def wait_for(predicate, *, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=predicate()
        if result:return result
        time.sleep(.05)
    raise AssertionError('The isolated demo pipeline did not reach its expected state')


@pytest.fixture
def demo_pipeline(tmp_path,monkeypatch):
    instances=[]
    class RecordingDemoBridge(DemoBridge):
        def __init__(self):
            super().__init__()
            self.deliveries=[]
            self.files=[]
            self.images=[]
            instances.append(self)

        def send(self,contact,text):
            assert contact['account_id']=='demo-account'
            self.deliveries.append((contact['id'],text))
            return super().send(contact,text)

        def send_file(self,contact,path):
            assert Path(path).is_relative_to(tmp_path)
            assert Path(path).read_bytes().startswith(b'%PDF-')
            self.files.append((contact['id'],Path(path)))
            return super().send_file(contact,path)

        def send_image(self,contact,data,before_send=None):
            with Image.open(BytesIO(data)) as image:
                assert image.format=='PNG' and image.size==(24,24)
            self.images.append((contact['id'],data))
            return super().send_image(contact,data,before_send)

    def no_real_wechat():
        pytest.fail('A demo acceptance test attempted to create a real WeChat bridge')

    monkeypatch.setattr(runtime,'WeixinBridge',no_real_wechat)
    monkeypatch.setattr(runtime,'DemoBridge',RecordingDemoBridge)
    model_calls=[]
    image_calls=[]
    def model(provider,messages,tools):
        model_calls.append((provider,messages,tools))
        if (any(message.get('role')=='user' and '请生成一张蓝色山水图片' in str(message.get('content'))
                for message in messages) and not any(message.get('role')=='tool' for message in messages)):
            assert 'generate_image' in {spec['function']['name'] for spec in tools}
            return {'role':'assistant','content':'','tool_calls':[{'id':'demo-image-call','type':'function',
                'function':{'name':'generate_image','arguments':json.dumps({
                    'prompt':'请生成一张蓝色山水图片','mode':'generate'},ensure_ascii=False)}}]},{}
        return {'content':'# 模拟任务结果\n\n该任务已在隔离环境完成。'},{}

    app=create_app(tmp_path,demo=True,runtime_factory=ThreadedDemoRuntime,ai_model_call=model)
    with TestClient(app) as client:
        wait_for(lambda: app.state.runtime.health()['state']=='ready'
            and app.state.store.active_account()=='demo-account')
        assert app.state.store.path==tmp_path/'panel.sqlite3'
        assert len(instances)==1
        ai=app.state.ai
        provider=ai.data.save('providers',None,dict(name='isolated provider',model='mock',
            base_url='http://localhost:19999/v1'))
        tool=next(t for t in ai.data.list('tools') if t.get('builtin')=='generate_image')
        tool=ai.data.save('tools',tool['id'],{k:v for k,v in tool.items() if k not in ('id','key_configured')}|{'approval':'auto'})
        service=next(s for s in ai.data.list('services') if s['driver']=='codex_image')
        agent=ai.data.save('agents',None,dict(name='isolated reply agent',provider_id=provider['id'],
            tool_ids=[tool['id']],web_policy='off',companion={
                'media':{'image_service_id':service['id']},
                'safety':{'emotion_enabled':False,'crisis_detection':False}}))

        def generate(service_id,prompt,media,reference=None,**kwargs):
            image_calls.append(dict(prompt=prompt,reference=reference))
            output=BytesIO()
            Image.new('RGB',(24,24),'blue').save(output,format='PNG')
            return output.getvalue()
        monkeypatch.setattr(ai.companion.services,'generate_image',generate)
        headers={'X-Panel-Request':'1'}
        response=client.put('/api/ai/settings',headers=headers,
            json=dict(enabled=True,debounce_seconds=.3,context_messages=20))
        assert response.status_code==200,response.text
        yield client,agent,instances[0],model_calls,image_calls


def post(client,url,body):
    response=client.post(url,headers={'X-Panel-Request':'1'},json=body)
    assert response.status_code<300,response.text
    return response.json()


def add_chat(client,agent,chat_type,name):
    body=dict(lookup=name,chat_type=chat_type)
    if chat_type=='group':
        body.update(group_members=['模拟成员'],group_self_name='模拟助手')
    else:body['wechat_id']=name+'-id'
    contact=post(client,'/api/contacts',body)
    response=client.patch('/api/contacts/'+contact['id'],headers={'X-Panel-Request':'1'},
        json=dict(listening=True,mode='ai_auto',ai_agent_id=agent['id']))
    assert response.status_code==200,response.text
    wait_for(lambda: client.app.state.store.contact(contact['id'])['listener_state']=='listening')
    return client.app.state.store.contact(contact['id'])


@pytest.mark.parametrize('chat_type',['direct','group'])
@pytest.mark.parametrize('kind',['text','file','image'])
def test_incoming_message_gets_one_correct_reply_in_its_original_chat(demo_pipeline,chat_type,kind):
    client,agent,bridge,model_calls,image_calls=demo_pipeline
    store=client.app.state.store
    ai=client.app.state.ai
    target=add_chat(client,agent,chat_type,'接收目标-'+chat_type)
    decoy=add_chat(client,agent,'group' if chat_type=='direct' else 'direct','不得收到消息')
    command={'text':'请简短回复模拟测试结果','file':'请生成PDF格式的模拟测试报告',
             'image':'请生成一张蓝色山水图片'}[kind]
    body=dict(text=command)
    if chat_type=='group':body.update(text=command+' @模拟助手',sender='模拟成员',mention_me=True)
    source=post(client,'/api/demo/'+target['id']+'/incoming',body)

    def terminal_run():
        rows=ai.data.runs(contact_id=target['id'])
        return next((run for run in rows if run['status'] in ('sent','failed','uncertain','cancelled')),None)
    run=wait_for(terminal_run)
    assert run['status']=='sent',run
    messages=store.messages(target['id'])
    incoming=[message for message in messages if message['direction']=='incoming']
    outgoing=[message for message in messages if message['direction']=='outgoing']
    assert len(incoming)==len(outgoing)==1
    assert incoming[0]['source_key']==source['key'] and not incoming[0]['historical']
    if chat_type=='group':
        assert incoming[0]['sender']=='模拟成员' and incoming[0]['sender_verified']
        assert incoming[0]['mention_me'] and incoming[0]['mention_verified']
    sent=outgoing[0]
    assert sent['kind']==kind and sent['status']=='confirmed'
    assert sent['ai_run_id']==run['id'] and sent['contact_id']==target['id']
    assert run['trigger_id']==incoming[0]['id'] and run['contact_id']==target['id']
    assert bridge.deliveries==[(target['id'],sent['text'])]
    assert len([item for item in bridge.history[target['id']] if item['direction']=='outgoing'])==1
    assert not store.messages(decoy['id']) and not bridge.history[decoy['id']]
    assert not store.pending()

    if kind=='file':
        path=report_path(store.path.parent,run['id'])
        assert bridge.files==[(target['id'],path)] and path.read_bytes().startswith(b'%PDF-')
        assert len(model_calls)==1 and not image_calls
    elif kind=='image':
        assert len(bridge.images)==len(image_calls)==1
        assert len(model_calls)==(2 if chat_type=='group' else 0)
        assert bridge.images[0][0]==target['id'] and sent['media_id']
    else:
        assert len(model_calls)==1 and not image_calls and not bridge.files and not bridge.images

    # Re-observing the same incoming row must not generate or send a second reply.
    current=bridge.snapshot(target)
    store.ingest(target['id'],current)
    store.ingest(target['id'],current)
    ai._collect()
    client.app.state.runtime.request('probe')  # Drain a further serial-worker turn.
    assert len(ai.data.runs(contact_id=target['id']))==1
    assert len(bridge.deliveries)==1 and not store.pending()
    assert not store.messages(decoy['id'])
