"""Exercise model HTTP, the AI dispatcher, and the actual demo worker together."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

from fastapi.testclient import TestClient

from wechat_panel.app import create_app

HEADERS={'X-Panel-Request':'1'}


def wait(call,predicate,timeout=12):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=call()
        if predicate(value):return value
        time.sleep(.1)
    raise AssertionError(f'等待 AI 状态超时：{value}')


def test_per_contact_agents_full_http_reply_and_review_flow(tmp_path):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((body,self.headers.get('Authorization')))
            role='财务' if 'FINANCE_ROLE' in body['messages'][0]['content'] else '电商'
            content=role+'回复：'+body['messages'][-1]['content']
            result=json.dumps({'choices':[{'message':{'role':'assistant','content':content}}]},ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(result)));self.end_headers();self.wfile.write(result)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with TestClient(create_app(tmp_path,demo=True)) as client:
            def post(path,body):
                r=client.post(path,headers=HEADERS,json=body);assert r.status_code<300,r.text;return r.json()
            def patch(cid,body):
                r=client.patch('/api/contacts/'+cid,headers=HEADERS,json=body);assert r.status_code==200,r.text
            def run(cid):return client.get('/api/ai/runs',params={'contact_id':cid}).json()
            wait(lambda:client.get('/api/status').json(),lambda s:s['state']=='ready')
            provider=post('/api/ai/config/providers',{'name':'模拟兼容模型','model':'mock','base_url':f'http://127.0.0.1:{server.server_port}/v1','api_key':'private-test-key'})
            commerce=post('/api/ai/config/agents',{'name':'电商 Agent','provider_id':provider['id'],'prompt':'ECOMMERCE_ROLE'})
            finance=post('/api/ai/config/agents',{'name':'财务 Agent','provider_id':provider['id'],'prompt':'FINANCE_ROLE'})
            a=post('/api/contacts',{'lookup':'客户甲','wechat_id':'customer-a'})['id']
            b=post('/api/contacts',{'lookup':'客户乙','wechat_id':'customer-b'})['id']
            patch(a,{'listening':True,'mode':'ai_assist','ai_agent_id':commerce['id']})
            patch(b,{'listening':True,'mode':'ai_auto','ai_agent_id':finance['id']})
            wait(lambda:client.get('/api/contacts').json(),lambda cs:all(c['listener_state']=='listening' for c in cs))
            assert client.put('/api/ai/settings',headers=HEADERS,json={'enabled':True,'debounce_seconds':.5,'context_messages':20}).status_code==200
            post(f'/api/demo/{a}/incoming',{'text':'商品咨询 A-PRIVATE'})
            post(f'/api/demo/{a}/incoming',{'text':'退货怎么处理？'})
            draft=wait(lambda:run(a),lambda rs:rs and rs[0]['status']=='draft')[0]
            assert draft['reply']=='电商回复：退货怎么处理？'
            assert len(run(a))==1
            assert not [m for m in client.get(f'/api/contacts/{a}/messages').json() if m['direction']=='outgoing']
            post(f"/api/ai/runs/{draft['id']}/send",{'text':'人工修改的电商回复','client_key':'review-001'})
            wait(lambda:run(a),lambda rs:rs[0]['status']=='sent')
            post(f'/api/demo/{b}/incoming',{'text':'核对 B-PRIVATE 账单'})
            wait(lambda:run(b),lambda rs:rs and rs[0]['status']=='sent')
            reply=client.get(f'/api/contacts/{b}/messages').json()[-1]
            assert reply['origin']=='ai' and reply['text']=='财务回复：核对 B-PRIVATE 账单'
            assert all(auth=='Bearer private-test-key' for _,auth in requests)
            finance_messages=[body['messages'] for body,_ in requests if 'FINANCE_ROLE' in body['messages'][0]['content']]
            assert 'A-PRIVATE' not in json.dumps(finance_messages,ensure_ascii=False)
            # Switch the first contact to the already configured finance Agent.
            patch(a,{'ai_agent_id':finance['id']})
            post(f'/api/demo/{a}/incoming',{'text':'新财务问题'})
            switched=wait(lambda:run(a),lambda rs:rs[0]['status']=='draft' and rs[0]['id']!=draft['id'])[0]
            assert switched['reply']=='财务回复：新财务问题'
            public=client.get('/api/ai/bootstrap').text+client.get('/api/ai/runs/'+switched['id']).text
            assert 'private-test-key' not in public and 'secret' not in public
            assert client.patch('/api/contacts/'+a,headers=HEADERS,json={'mode':'manual'}).status_code==200
            assert client.post('/api/ai/runs/'+switched['id']+'/send',headers=HEADERS,json={'text':'过期草稿','client_key':'review-002'}).status_code==409
            # A disabled Agent must still allow its contacts to stop AI replies.
            disabled={k:v for k,v in finance.items() if k not in ('id','key_configured')}
            disabled['enabled']=False
            assert client.put('/api/ai/config/agents/'+finance['id'],headers=HEADERS,json=disabled).status_code==200
            patch(b,{'listening':False})
            patch(b,{'mode':'manual'})
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_http_tool_approval_is_exactly_once_and_test_never_sends_wechat(tmp_path):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            result=b'{"order_status":"shipped"}'
            self.send_response(200);self.send_header('Content-Length',str(len(result)));self.end_headers();self.wfile.write(result)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with TestClient(create_app(tmp_path,demo=True,ai_model_call=lambda *a:({'role':'assistant','content':'订单已发货'},{}))) as client:
            def post(path,body):
                r=client.post(path,headers=HEADERS,json=body);assert r.status_code<300,r.text;return r.json()
            p=post('/api/ai/config/providers',{'name':'模型','model':'test','base_url':'http://127.0.0.1:8000/v1'})
            tool=post('/api/ai/config/tools',{'name':'orders','description':'查订单','url':f'http://127.0.0.1:{server.server_port}/order','parameters':{'type':'object','properties':{'id':{'type':'string'}},'required':['id']}})
            agent=post('/api/ai/config/agents',{'name':'自定义订单流程','provider_id':p['id'],'tool_ids':[tool['id']],
                'steps':[{'type':'tool','tool_id':tool['id'],'arguments':{'id':'{{message}}'}},{'type':'model'},{'type':'reply'}]})
            r=post('/api/ai/test',{'agent_id':agent['id'],'text':'ORDER-42'})
            detail=lambda:client.get('/api/ai/runs/'+r['id']).json()
            approval=wait(detail,lambda r:r['status']=='waiting_approval')['approvals'][0]
            assert calls==[] and approval['arguments']=={'id':'ORDER-42'}
            post('/api/ai/approvals/'+approval['id'],{'approved':True})
            assert wait(detail,lambda r:r['status']=='completed')['reply']=='订单已发货'
            assert calls==[{'id':'ORDER-42'}]
            assert client.post('/api/ai/approvals/'+approval['id'],headers=HEADERS,json={'approved':True}).status_code==409
            assert client.get('/api/contacts').json()==[]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
