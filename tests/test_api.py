import time

from fastapi.testclient import TestClient

from wechat_panel.app import create_app

HEADERS = {'X-Panel-Request':'1'}


def wait_for(call, predicate, timeout=12):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = call()
        if predicate(value):
            return value
        time.sleep(.1)
    raise AssertionError(f'等待状态超时：{value}')


def test_end_to_end_api_with_real_worker_and_isolated_demo(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path, demo=True)) as client:
        wait_for(lambda:client.get('/api/status').json(), lambda x:x['state']=='ready')
        assert client.post('/api/contacts',json={'lookup':'测试','wechat_id':'x'}).status_code == 403
        assert client.post('/api/contacts',headers={**HEADERS,'Origin':'https://evil.example'},
                           json={'lookup':'测试','wechat_id':'x'}).status_code == 403
        mismatch = client.post('/api/contacts',headers=HEADERS,json={'lookup':'测试','wechat_id':'mismatch'})
        assert mismatch.status_code == 503 and not client.get('/api/contacts').json()
        result = client.post('/api/contacts',headers=HEADERS,json={'lookup':'测试联系人','wechat_id':'wx-test'})
        assert result.status_code == 201, result.text
        cid = result.json()['id']
        assert client.post('/api/contacts',headers=HEADERS,json={'lookup':'测试联系人','wechat_id':'wx-test'}).status_code == 409
        client.patch(f'/api/contacts/{cid}',headers=HEADERS,json={'listening':True})
        wait_for(lambda:client.get('/api/contacts').json()[0],lambda c:c['listener_state']=='listening')
        assert client.get('/api/status').json()['contacts_connected'] == 1
        for _ in range(2):
            assert client.post(f'/api/demo/{cid}/incoming',headers=HEADERS,json={'text':'同一句话'}).status_code==200
        messages = wait_for(lambda:client.get(f'/api/contacts/{cid}/messages').json(),lambda xs:len(xs)==2)
        assert all(x['direction']=='incoming' and not x['historical'] for x in messages)
        assert client.get('/api/agent/events').status_code == 503
        monkeypatch.setenv('WECHAT_PANEL_AGENT_TOKEN','test-token')
        assert client.get('/api/agent/events').status_code == 401
        agent = client.get('/api/agent/events',headers={'Authorization':'Bearer test-token'}).json()
        assert len(agent['events'])==2 and agent['events'][0]['wechat_id']=='wx-test'
        body = {'text':'工作台回复','client_key':'test-request'}
        original_health = client.app.state.runtime.health
        with monkeypatch.context() as context:
            context.setattr(client.app.state.runtime, 'health', lambda:dict(original_health(), state='hidden'))
            first = client.post(f'/api/contacts/{cid}/messages',headers=HEADERS,json=body)
            assert first.status_code == 202
        second = client.post(f'/api/contacts/{cid}/messages',headers=HEADERS,json=body)
        assert first.json()['id'] == second.json()['id']
        wait_for(lambda:client.get(f'/api/contacts/{cid}/messages').json(),lambda xs:xs[-1]['status']=='confirmed')
        time.sleep(1.2)
        assert len(client.get(f'/api/contacts/{cid}/messages').json())==3
        assert len(client.get('/api/agent/events',headers={'Authorization':'Bearer test-token'}).json()['events'])==2
        client.patch(f'/api/contacts/{cid}',headers=HEADERS,json={'mode':'listen_only'})
        assert client.post(f'/api/contacts/{cid}/messages',headers=HEADERS,json={'text':'阻止发送','client_key':'request-two'}).status_code==409
        client.post('/api/runtime/pause',headers=HEADERS,json={'paused':True})
        assert client.get('/api/status').json()['paused']
        assert client.post(f'/api/contacts/{cid}/messages',headers=HEADERS,json={'text':'阻止发送','client_key':'request-three'}).status_code==409
        assert client.delete(f'/api/contacts/{cid}',headers=HEADERS).status_code==204
        assert client.get('/api/contacts').json()==[]


def test_live_mode_does_not_expose_demo_injection(tmp_path):
    # Test routes without starting a real Windows worker.
    client=TestClient(create_app(tmp_path, demo=False))
    assert client.post('/api/demo/any/incoming',headers=HEADERS,json={'text':'x'}).status_code==404
    assert client.get('/',headers={'Host':'attacker.example'}).status_code==400
    assert client.get('/api/agent/schema').json()['enabled'] is False
