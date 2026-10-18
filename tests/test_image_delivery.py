from io import BytesIO
import time

from fastapi.testclient import TestClient
from PIL import Image

from wechat_panel.app import create_app
from wechat_panel.companion_services import Services


def test_wechat_request_generates_and_sends_image_through_serial_worker(tmp_path,monkeypatch):
    calls=[]
    def generate(self,*args,**kwargs):
        calls.append(args)
        buf=BytesIO();Image.new('RGB',(24,24),'blue').save(buf,format='PNG');return buf.getvalue()
    monkeypatch.setattr(Services,'generate_image',generate)
    with TestClient(create_app(tmp_path,demo=True)) as client:
        ai=client.app.state.ai
        p=ai.data.save('providers',None,dict(name='test',model='test',base_url='http://localhost:19999/v1'))
        t=next(t for t in ai.data.list('tools') if t.get('builtin')=='generate_image')
        body={k:v for k,v in t.items() if k not in ('id','key_configured')};body['approval']='auto'
        ai.data.save('tools',t['id'],body)
        service=next(s for s in ai.data.list('services') if s['driver']=='codex_image')
        a=ai.data.save('agents',None,dict(name='test',provider_id=p['id'],tool_ids=[t['id']],web_policy='off',
            companion={'media':{'image_service_id':service['id']},'safety':{'emotion_enabled':False,'crisis_detection':False}}))
        headers={'X-Panel-Request':'1'}
        for _ in range(80):
            if client.get('/api/status').json()['state']=='ready':break
            time.sleep(.1)
        c=client.post('/api/contacts',headers=headers,json=dict(lookup='生图测试',wechat_id='image-test')).json()
        response=client.patch('/api/contacts/'+c['id'],headers=headers,json=dict(listening=True,mode='ai_auto',ai_agent_id=a['id'],reply_format='voice'))
        assert response.status_code==200,response.text
        client.put('/api/ai/settings',headers=headers,json=dict(enabled=True,debounce_seconds=.3,context_messages=20))
        for _ in range(80):
            if client.get('/api/contacts').json()[0]['listener_state']=='listening':break
            time.sleep(.1)
        client.post('/api/demo/'+c['id']+'/incoming',headers=headers,json=dict(text='帮我生成一张富春山居图'))
        for _ in range(160):
            runs=client.get('/api/ai/runs',params={'contact_id':c['id']}).json()
            if runs and runs[0]['status'] in ('sent','failed','uncertain'):break
            time.sleep(.1)
        assert runs and runs[0]['status']=='sent',runs
        messages=client.get('/api/contacts/'+c['id']+'/messages').json()
        images=[m for m in messages if m['direction']=='outgoing']
        assert len(calls)==1 and len(images)==1
        assert images[0]['kind']=='image' and images[0]['status']=='confirmed' and images[0]['media_id']
        response=client.get('/api/ai/media/'+images[0]['media_id'])
        assert response.status_code==200 and response.content.startswith(b'\x89PNG')
