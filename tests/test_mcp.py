import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from wechat_panel.ai import AIService
from wechat_panel.ai_store import unprotect
from wechat_panel.ai_tools import ToolUncertain
from wechat_panel.store import Store
from wechat_panel import mcp_client, mcp_policy
from wechat_panel.web_policy import decide

SCHEMA={'type':'object','properties':{'city':{'type':'string'}},'required':['city'],'additionalProperties':False}


@pytest.fixture
def ai(tmp_path):
    service=AIService(Store(tmp_path/'panel.db'))
    yield service
    service.stop()


def tool(ai,profile='amap',approval='auto'):
    connection=next(c for c in ai.data.list('mcp') if c['profile']==profile)
    return ai.data.save('tools',None,{'name':'test_'+profile,'kind':'mcp','connection_id':connection['id'],
        'remote_name':'maps_weather' if profile=='amap' else 'create_mindmap','description':'测试工具',
        'parameters':SCHEMA,'approval':approval})


def state(t):return {'snapshot':{'agent':{'web_policy':'auto'},'tools':{t['id']:t}},'context':[]}


def test_presets_are_unconfigured_and_restart_idempotent(ai):
    connections=ai.data.list('mcp');assert len(connections)==2
    amap=next(c for c in connections if c['profile']=='amap')
    assert amap['url']=='https://mcp.amap.com/mcp' and not amap['key_configured']
    with pytest.raises(ValueError,match='尚未配置 MCP 密钥'):mcp_client.connection_args(amap)
    tree=next(c for c in connections if c['profile']=='treemind')
    with pytest.raises(ValueError,match='官方 MCP'):mcp_client.connection_args(tree)
    placeholder=next(t for t in ai.data.list('tools') if t['name']=='treemind_mindmap')
    assert not placeholder['enabled'] and not placeholder['url']
    from wechat_panel.ai_store import AIStore
    assert len(AIStore(ai.store).list('mcp'))==2


def test_key_encryption_and_url_restriction(ai):
    c=ai.data.save('mcp',None,{'name':'高德测试','profile':'amap','api_key':'secret-query-key'})
    assert c['auth']=='query' and c['auth_name']=='key'
    assert 'secret-query-key' not in json.dumps(c)
    private=ai.data.get('mcp',c['id'],private=True)
    assert unprotect(private['secret'])=='secret-query-key'
    assert mcp_client.connection_args(private)[0].endswith('?key=secret-query-key')
    with pytest.raises(ValueError,match='查询参数'):
        ai.data.save('mcp',None,{'name':'Bad','url':'https://example.com/mcp?key=unsafe'})
    c=ai.data.save('mcp',c['id'],{'name':'改名','profile':'amap'})
    assert c['key_configured']
    c=ai.data.save('mcp',c['id'],{'name':'改名','profile':'amap','clear_secret':True})
    assert not c['key_configured']


@pytest.mark.parametrize('text,expected',[
    ('上海明天天气怎么样？','amap'),('我附近有什么咖啡店？','amap'),('从虹桥到外滩怎么走？','amap'),
    ('帮我规划一份学习路线',''),('今天好累不想上班',''),('我的人生方向在哪',''),
    ('不要联网查天气',''),('帮我生成思维导图',''),
])
def test_amap_route(ai,text,expected):
    s=state(tool(ai))
    assert mcp_policy.route({'input':text},s)==expected
    if expected:assert not decide({'input':text},s)['required']


def test_location_clarification_followup(ai):
    s=state(tool(ai));s['context']=[{'role':'user','content':'查一下明天的天气'},
        {'role':'assistant','content':'你想查哪个城市的天气？'},{'role':'user','content':'上海'}]
    assert mcp_policy.route({'input':'上海'},s)=='amap'
    assert mcp_policy.route({'input':'不用了，今天好累'},s)==''


@pytest.mark.parametrize('text,enabled',[
    ('生成一份 AI 学习思维导图',True),('把这些资料整理成脑图',True),
    ('思维导图是什么？',False),('不要生成思维导图',False),('你好',False),
])
def test_mindmap_explicit_only(ai,text,enabled):
    t=tool(ai,'treemind');s=state(t)
    assert mcp_policy.eligible(t,{'input':text},s) is enabled


def test_discovery_and_import_preserves_permissions(ai,monkeypatch):
    c=next(c for c in ai.data.list('mcp') if c['profile']=='amap')
    monkeypatch.setattr(mcp_client,'request',lambda c:{'tools':[
        {'name':'maps_weather','description':'天气','inputSchema':SCHEMA},
        {'name':'maps_ip_location','description':'IP','inputSchema':SCHEMA},
        {'name':'future_write','description':'未来操作','inputSchema':SCHEMA}]})
    rows=mcp_client.discover(c)
    assert [r['name'] for r in rows]==['maps_weather','future_write']
    assert [r['approval'] for r in rows]==['auto','confirm']
    imported=mcp_client.import_tools(ai.data,c,rows,['maps_weather'])[0]
    value={k:v for k,v in imported.items() if k not in ('id','key_configured')}
    value['approval']='confirm';ai.data.save('tools',imported['id'],value)
    updated=mcp_client.import_tools(ai.data,c,rows,['maps_weather'])[0]
    assert updated['id']==imported['id'] and updated['approval']=='confirm'
    with pytest.raises(ValueError,match='本次发现'):mcp_client.import_tools(ai.data,c,rows,['invented'])
    with pytest.raises(ValueError,match='导入的工具'):ai.data.delete('mcp',c['id'])


def configure_connection(ai,t):
    c=ai.data.get('mcp',t['connection_id'])
    value={k:v for k,v in c.items() if k not in ('id','key_configured')}
    value.update(api_key='fake-test-key',url='https://example.com/mcp')
    return ai.data.save('mcp',c['id'],value)


def test_failures_and_no_repeat_for_writing(ai,monkeypatch):
    t=tool(ai);configure_connection(ai,t)
    monkeypatch.setattr(mcp_client,'request',lambda *a:(_ for _ in ()).throw(RuntimeError('MCP 请求未完成')))
    assert json.loads(mcp_client.execute(ai.data,t,{'city':'上海'}))['ok'] is False
    t=tool(ai,'treemind');configure_connection(ai,t)
    with pytest.raises(ToolUncertain):mcp_client.execute(ai.data,t,{'city':'上海'})


def setup_agent(ai,t):
    p=ai.data.save('providers',None,{'name':'fake','model':'fake','base_url':'http://127.0.0.1:8001'})
    return ai.data.save('agents',None,{'name':'测试Agent','provider_id':p['id'],'tool_ids':[t['id']]})


def call(t):return {'role':'assistant','content':None,'tool_calls':[{'id':'call-1','type':'function','function':{'name':t['name'],'arguments':'{"city":"上海"}'}}]}


def test_model_uses_real_query_once_and_no_web_prefetch(ai,monkeypatch):
    t=tool(ai);configure_connection(ai,t);a=setup_agent(ai,t);calls=[]
    def remote(*args):calls.append(args);return {'content':[{'type':'text','text':'{"status":"1","weather":"晴"}'}],'isError':False}
    monkeypatch.setattr(mcp_client,'request',remote)
    def model(p,m,specs):
        assert specs and specs[0]['function']['name']==t['name']
        return (call(t) if not any(m['role']=='tool' for m in m) else {'role':'assistant','content':'高德返回上海天气晴。'}),{}
    ai.model_call=model
    r=ai.create_run(a['id'],'上海今天天气怎么样',dry_run=True);ai.execute(r['id']);r=ai.data.run(r['id'])
    assert r['status']=='completed' and len(calls)==1 and '晴' in r['reply']
    assert r['state']['mcp_results'][0]['ok'] and not r['state'].get('web_evidence')
    assert not ai.store.pending()


def test_uncalled_result_cannot_claim_weather(ai):
    t=tool(ai);a=setup_agent(ai,t);count=[]
    def model(*args):count.append(1);return {'role':'assistant','content':'上海今天晴，20度。'},{}
    ai.model_call=model;r=ai.create_run(a['id'],'上海天气',dry_run=True);ai.execute(r['id']);r=ai.data.run(r['id'])
    assert r['status']=='completed' and len(count)==2 and '20度' not in r['reply']


def test_clarifies_missing_location_without_any_query(ai,monkeypatch):
    t=tool(ai);a=setup_agent(ai,t)
    monkeypatch.setattr(mcp_client,'request',lambda *a:pytest.fail('should ask location first'))
    ai.model_call=lambda *a:({'role':'assistant','content':'你在哪个城市、什么位置附近呢？'}, {})
    r=ai.create_run(a['id'],'我附近有什么咖啡店',dry_run=True);ai.execute(r['id'])
    assert '哪个城市' in ai.data.run(r['id'])['reply']


def test_mindmap_approval_and_actual_artifact_link(ai,monkeypatch):
    t=tool(ai,'treemind',approval='confirm');configure_connection(ai,t);a=setup_agent(ai,t);calls=[]
    def remote(*args):calls.append(args);return {'content':[{'type':'text','text':'https://shutu.cn/test-artifact'}]}
    monkeypatch.setattr(mcp_client,'request',remote)
    ai.model_call=lambda p,m,s:(call(t) if not any(x['role']=='tool' for x in m) else {'role':'assistant','content':'已整理好'}, {})
    r=ai.create_run(a['id'],'帮我生成旅游思维导图',dry_run=True);ai.execute(r['id'])
    assert ai.data.run(r['id'])['status']=='waiting_approval' and not calls
    approval=ai.data.detail(r['id'])['approvals'][0]
    ai.approve(approval['id'],True);ai.execute(r['id'])
    r=ai.data.run(r['id']);assert r['status']=='completed' and len(calls)==1
    assert 'https://shutu.cn/test-artifact' in r['reply']


def test_treemind_empty_result_not_success():
    s={};mcp_policy.receipt(s,{'name':'tree','capability':'mindmap'},'{"ok":true,"result":{"content":[]}}')
    assert s['mcp_results'][0]['ok'] is False


def test_sdk_protocol_with_real_local_http_server(ai,caplog):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path,body))
            if self.path.startswith('/fail'):
                self.send_response(401);self.end_headers();return
            if 'id' not in body:self.send_response(202);self.end_headers();return
            method=body['method']
            if method=='initialize':result={'protocolVersion':body['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'local-test','version':'1'}}
            elif method=='tools/list':result={'tools':[{'name':'maps_weather','description':'weather','inputSchema':SCHEMA}]}
            else:result={'content':[{'type':'text','text':'{"weather":"晴"}'}],'isError':False}
            encoded=json.dumps({'jsonrpc':'2.0','id':body['id'],'result':result}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
        def do_GET(self):self.send_response(405);self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        c=ai.data.save('mcp',None,{'name':'local','url':f'http://127.0.0.1:{server.server_port}/mcp','auth':'query','auth_name':'key','api_key':'secret-test-query'})
        c=ai.data.get('mcp',c['id'],private=True)
        with caplog.at_level(logging.INFO):
            assert mcp_client.discover(c)[0]['name']=='maps_weather'
            result=mcp_client.request(c,'maps_weather',{'city':'上海'})
        assert not result['isError']
        assert any('secret-test-query' in path for path,_ in requests)
        assert 'secret-test-query' not in caplog.text
        assert [b['method'] for _,b in requests].count('tools/call')==1
        c['url']=f'http://127.0.0.1:{server.server_port}/fail'
        with caplog.at_level(logging.DEBUG),pytest.raises(RuntimeError,match='MCP 请求未完成') as error:
            mcp_client.request(c)
        assert 'secret-test-query' not in caplog.text+str(error.value)
    finally:server.shutdown();server.server_close();thread.join(timeout=2)
