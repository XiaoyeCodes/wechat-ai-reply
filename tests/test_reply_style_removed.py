import json

from fastapi.testclient import TestClient
from wechat_panel.app import create_app
from wechat_panel.ai import AIService
from wechat_panel.store import Store, now


def test_retired_style_api_and_old_client_binding(tmp_path):
    with TestClient(create_app(tmp_path,demo=True)) as client:
        h={'X-Panel-Request':'1'}
        response=client.get('/api/ai/bootstrap')
        assert response.status_code==200
        config=response.json()
        assert 'styles' not in config
        assert client.post('/api/ai/config/styles',headers=h,json={'name':'旧风格','prompt':'旧规则'}).status_code==404
        p=client.post('/api/ai/config/providers',headers=h,json={'name':'test','model':'test','base_url':'http://localhost:19999/v1'}).json()
        a=client.post('/api/ai/config/agents',headers=h,json={'name':'test','provider_id':p['id'],'style_id':'old-style'}).json()
        assert a['style_id']==''


def test_stored_legacy_style_and_historical_snapshot_never_reach_model(tmp_path):
    calls=[]
    def model(provider,messages,tools):
        calls.append(messages)
        return {'role':'assistant','content':'正常回复'},{}
    store=Store(tmp_path/'panel.sqlite3');ai=AIService(store,model_call=model)
    try:
        p=ai.data.save('providers',None,dict(name='test',model='test',base_url='http://localhost:19999/v1'))
        a=ai.data.save('agents',None,dict(name='test',provider_id=p['id'],prompt=''))
        with store.connect() as db:
            legacy=dict(name='旧风格',prompt='RETIRED_STYLE_MARKER')
            db.execute('INSERT INTO ai_resources VALUES(?,?,?,?,?)',('styles','old-style',json.dumps(legacy),now(),now()))
            raw=json.loads(db.execute("SELECT data FROM ai_resources WHERE id=?",(a['id'],)).fetchone()[0])
            raw['style_id']='old-style'
            db.execute('UPDATE ai_resources SET data=? WHERE id=?',(json.dumps(raw),a['id']))
        assert ai.snapshot(a['id'])['style'] is None
        assert ai.data.get('agents',a['id'])['style_id']==''
        r=ai.create_run(a['id'],'你好',dry_run=True)
        state=ai.data.run(r['id'])['state']
        state['snapshot']['style']=legacy  # Run created by the previous version.
        ai.data.update_run(r['id'],state=state)
        ai.execute(r['id'])
        assert ai.data.run(r['id'])['status']=='completed'
        assert calls and 'RETIRED_STYLE_MARKER' not in json.dumps(calls)
    finally:ai.stop()
