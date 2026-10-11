import base64
from datetime import datetime,timedelta,timezone
from io import BytesIO
import json
import uuid

from PIL import Image
import pytest
from fastapi.testclient import TestClient

from wechat_panel.ai import AIService
from wechat_panel.app import create_app
from wechat_panel.companion import companion_config
from wechat_panel.companion_models import Schedule
from wechat_panel.companion_scheduler import quiet
from wechat_panel.store import Store


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'panel.sqlite3');calls=[]
    def model(provider,messages,tools):
        calls.append(messages)
        if '只输出 JSON' in messages[0]['content']:
            text=json.loads(messages[-1]['content'])['message']
            facts=[]
            if '芝麻' in text:facts=[{'key':'宠物名','content':'宠物猫叫芝麻','category':'fact','evidence':'芝麻'}]
            if '团子' in text:facts=[{'key':'宠物名','content':'宠物猫现在叫团子','category':'fact','evidence':'团子'}]
            return {'role':'assistant','content':json.dumps({'mood':'开心','risk':'none','summary':'用户喜欢宠物猫','facts':facts},ensure_ascii=False)},{}
        return {'role':'assistant','content':'好呀，我记住啦。'},{}
    ai=AIService(store,model_call=model)
    provider=ai.data.save('providers',None,{'name':'mock','base_url':'http://127.0.0.1:9999/v1','model':'mock'})
    agent=ai.data.save('agents',None,{'name':'阿慧','provider_id':provider['id'],'companion':{'persona':{'enabled':True},'memory':{'enabled':True},'care':{'enabled':True,'quiet_start':'00:00','quiet_end':'00:00','min_gap_minutes':5}}})
    ai.data.settings({'enabled':True})
    contact=store.add_contact({'lookup':'测试甲','nickname':'测试甲','wechat_id':'a','account_id':'self'})
    store.update_contact(contact['id'],mode='ai_auto',listening=True,ai_agent_id=agent['id'])
    memory=ai.companion.memory;scope=memory.scope(agent['id'],contact['id']);memory.profile(scope,agent['id'],contact['id'])
    yield store,ai,provider,agent,contact,calls
    ai.stop()


def run_test(ai,agent,text,session='a',media=None):
    run=ai.create_run(agent['id'],text,dry_run=True,session_id=session,input_media_id=media)
    ai.execute(run['id']);result=ai.data.run(run['id'])
    assert result['status']=='completed',result['error']
    return result


def test_memory_layers_survive_restart_are_isolated_encrypted_and_updatable(setup):
    store,ai,provider,agent,contact,calls=setup
    first=run_test(ai,agent,'我家猫叫芝麻')
    scope=ai.companion.memory.scope(agent['id'],session_id='a')
    memories=ai.companion.memory.list_memories(scope)
    assert len(memories)==1 and memories[0]['content']=='宠物猫叫芝麻'
    second=run_test(ai,agent,'还记得我的猫吗')
    assert '宠物猫叫芝麻' in calls[-1][0]['content']
    assert len(second['state']['context'])==3
    run_test(ai,agent,'猫改叫团子了')
    rows=ai.companion.memory.list_memories(scope)
    assert len(rows)==1 and rows[0]['content']=='宠物猫现在叫团子'
    other=run_test(ai,agent,'你好','b')
    assert other['state']['dynamic_context']['memories']==[]
    assert ai.companion.memory.list_memories(ai.companion.memory.scope(agent['id'],contact['id']))==[]
    with store.connect() as db:
        assert '团子' not in db.execute('SELECT content FROM companion_memories').fetchone()[0]
        assert '芝麻' not in db.execute('SELECT state FROM ai_runs WHERE id=?',(first['id'],)).fetchone()[0]
    restarted=AIService(store,model_call=ai.model_call)
    try:assert restarted.companion.memory.list_memories(scope)[0]['content']=='宠物猫现在叫团子'
    finally:restarted.stop()


def test_forgetting_removes_summary_transcript_and_blocks_old_fact_recreation(setup):
    _,ai,_,agent,_,_=setup
    run_test(ai,agent,'我家猫叫芝麻')
    scope=ai.companion.memory.scope(agent['id'],session_id='a');memory=ai.companion.memory
    revision=memory.profile(scope)['revision'];entry=memory.list_memories(scope)[0]
    memory.forget(scope,entry['id'])
    assert not memory.list_memories(scope) and not memory.conversation(scope) and not memory.profile(scope)['summary']
    assert memory.save_memory(scope,{'key':'宠物名','content':'宠物猫叫芝麻'},source_id=1,revision=revision) is None
    assert memory.save_memory(scope,{'key':'宠物名','content':'宠物猫叫芝麻'},source_id=1,revision=memory.profile(scope)['revision']) is None
    # Explicit user edits can reintroduce a fact intentionally.
    assert memory.save_memory(scope,{'key':'宠物名','content':'新宠物叫花花'})


def test_expiry_and_pinned_memories(setup):
    _,ai,_,agent,contact,_=setup;memory=ai.companion.memory;scope=memory.scope(agent['id'],contact['id'])
    expired=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    memory.save_memory(scope,{'content':'过期事实','expires_at':expired})
    memory.save_memory(scope,{'content':'长期偏好','expires_at':expired,'pinned':True})
    assert [m['content'] for m in memory.list_memories(scope)]==['长期偏好']


def test_unverified_age_uses_friend_mode_and_crisis_skips_ordinary_reply(setup):
    _,ai,_,agent,_,calls=setup
    run=run_test(ai,agent,'你好')
    assert '普通朋友式陪伴' in calls[-1][0]['content']
    before=len(calls)
    urgent=run_test(ai,agent,'我现在想自杀')
    assert '当地急救' in urgent['reply'] and len(calls)==before+1
    assert urgent['state']['analysis']['risk']=='urgent'


def test_photo_stays_in_its_scope_and_reaches_vision_model(setup):
    _,ai,provider,agent,_,calls=setup
    data={k:v for k,v in agent.items() if k not in ('id','key_configured')}
    data['companion']['media'].update(understand_images=True,vision_provider_id=provider['id'])
    agent=ai.data.save('agents',agent['id'],data)
    buf=BytesIO();Image.new('RGB',(80,60),'red').save(buf,format='PNG')
    memory=ai.companion.memory;scope=memory.scope(agent['id'],session_id='photo')
    media=memory.media(scope,buf.getvalue())
    run_test(ai,agent,'这是什么','photo',media['id'])
    assert any(isinstance(m[-1]['content'],list) and m[-1]['content'][-1]['type']=='image_url' for m in calls)
    bad=ai.create_run(agent['id'],'看看图片',dry_run=True,session_id='other',input_media_id=media['id'])
    ai.execute(bad['id']);assert ai.data.run(bad['id'])['status']=='failed'


def care_schedule(ai,agent,contact,**overrides):
    memory=ai.companion.memory;scope=memory.scope(agent['id'],contact['id'])
    profile=memory.profile(scope)['data'];memory.set_profile(scope,{**profile,'care_consent':True})
    return memory.save_schedule({'agent_id':agent['id'],'contact_id':contact['id'],'title':'面试关心','instruction':'问问面试进展',
        'at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),'enabled':True,'delivery':'auto',**overrides})


def test_wechat_photo_becomes_one_event_after_capture_and_ignores_history(setup):
    store,ai,_,_,contact,_=setup
    buf=BytesIO();Image.new('RGB',(30,30),'red').save(buf,format='PNG')
    encoded=base64.b64encode(buf.getvalue()).decode()
    old=dict(key='old-photo',text='[图片]',kind='image',direction='incoming',image_data=encoded)
    store.ingest(contact['id'],[old])
    photo=dict(key='new-photo',text='[图片]',kind='image',direction='incoming')
    store.ingest(contact['id'],[old,photo])
    photo['image_data']=encoded
    store.ingest(contact['id'],[old,photo]);store.ingest(contact['id'],[old,photo])
    with store.connect() as db:
        events=db.execute("SELECT payload FROM events WHERE type='agent.message'").fetchall()
        row=db.execute("SELECT id,media_id FROM messages WHERE source_key='new-photo'").fetchone()
        assert len(events)==1 and json.loads(events[0]['payload'])['message_id']==row['id']
        assert encoded not in db.execute('SELECT data FROM snapshots').fetchone()[0]
        assert encoded not in db.execute('SELECT data FROM companion_media LIMIT 1').fetchone()[0]
    assert ai.companion.memory.read_media(row['media_id'],'wechat:'+contact['id'])['data']


def test_vector_memory_namespace_filter_and_embedding_dependency(setup,monkeypatch):
    _,ai,provider,agent,_,_=setup
    vector=ai.data.save('services',None,{'name':'vectors','category':'memory','driver':'qdrant',
        'url':'http://127.0.0.1:6333','embedding_provider_id':provider['id']})
    calls=[]
    def request(url,body=None,**kw):
        calls.append((url,body))
        return {'data':[{'embedding':[0.1,0.2]}]} if url.endswith('/embeddings') else {'result':{'points':[]}}
    monkeypatch.setattr('wechat_panel.companion_services.request_json',request)
    ai.companion.services.memory(vector['id'],'search','sandbox-one',query='宠物名')
    assert calls[-1][1]['filter']=={'must':[{'key':'namespace','match':{'value':'sandbox-one'}}]}
    ai.companion.services.memory(vector['id'],'clear','sandbox-two')
    assert calls[-1][1]['filter']['must'][0]['match']['value']=='sandbox-two'
    with pytest.raises(ValueError,match='Embedding'):ai.data.delete('providers',provider['id'])


def test_schedule_claim_is_unique_uses_instruction_and_stops_on_manual_takeover(setup):
    store,ai,_,agent,contact,calls=setup
    store.ingest(contact['id'],[dict(key='old',text='旧聊天',kind='text',direction='incoming')])
    schedule=care_schedule(ai,agent,contact)
    ai.scheduler.tick(datetime.now(timezone.utc));ai.scheduler.tick(datetime.now(timezone.utc))
    runs=ai.data.runs(contact['id']);assert len(runs)==1 and runs[0]['schedule_id']==schedule['id']
    ai.execute(runs[0]['id']);assert ai.data.run(runs[0]['id'])['status']=='queued'
    assert any('问问面试进展' in m['content'] for m in calls[-1])
    assert store.ai_send_valid(store.pending()[0])
    store.update_contact(contact['id'],mode='manual')
    assert not store.ai_send_valid(store.pending()[0])


@pytest.mark.parametrize('condition',['pause','consent','late','limit'])
def test_care_does_not_send_when_paused_unconsented_old_or_rate_limited(setup,condition):
    store,ai,_,agent,contact,_=setup;s=care_schedule(ai,agent,contact)
    if condition=='pause':store.pause(True)
    if condition=='consent':
        scope=ai.companion.memory.scope(agent['id'],contact['id']);p=ai.companion.memory.profile(scope)['data']
        ai.companion.memory.set_profile(scope,{**p,'care_consent':False})
    if condition=='late':
        with store.connect() as db:db.execute('UPDATE companion_schedules SET next_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),s['id']))
    if condition=='limit':
        ai.scheduler.tick(datetime.now(timezone.utc));run=ai.data.runs(contact['id'])[0]
        ai.data.update_run(run['id'],status='sent')
        care_schedule(ai,agent,contact)
    ai.scheduler.tick(datetime.now(timezone.utc))
    assert len(ai.data.runs(contact['id']))==(1 if condition=='limit' else 0)
    assert not store.pending()


def test_cron_timezone_and_quiet_hours():
    from wechat_panel.companion_store import CompanionStore
    s=Schedule(agent_id='a',contact_id='b',title='早安',instruction='早安',kind='cron',cron='0 9 * * *')
    after=datetime(2026,10,11,0,0,tzinfo=timezone.utc)
    assert CompanionStore.next_time(s.model_dump(),after)=='2026-10-11T01:00:00+00:00'
    assert quiet({'timezone':'Asia/Shanghai','quiet_start':'23:00','quiet_end':'08:00'},datetime(2026,10,11,16,0,tzinfo=timezone.utc))


def test_tts_and_reference_image_gateway_are_real_adapters(setup,monkeypatch):
    _,ai,_,agent,_,_=setup
    tts=ai.data.save('services',None,{'name':'voice','category':'tts','driver':'minimax','url':'http://127.0.0.1:9000','api_key':'tts-secret','voice_id':'fixed'})
    seen=[]
    def request(url,body,**kw):
        seen.append((url,body,kw))
        return {'base_resp':{'status_code':0},'data':{'audio':b'fake-mp3'.hex()}}
    monkeypatch.setattr('wechat_panel.companion_services.request_json',request)
    audio,mime=ai.companion.services.tts(tts['id'],'你好')
    assert audio==b'fake-mp3' and mime=='audio/mpeg' and seen[0][1]['voice_setting']['voice_id']=='fixed'
    assert 'tts-secret' not in json.dumps(ai.data.list('services'))
    gateway=ai.data.save('services',None,{'name':'Flux','category':'image','driver':'image_http','url':'http://127.0.0.1:9990'})
    def image_request(url,body,**kw):
        assert body['reference_image']=='reference' and body['lora']=='ahui'
        return {'image_base64':base64.b64encode(b'image').decode()}
    monkeypatch.setattr('wechat_panel.companion_services.request_json',image_request)
    assert ai.companion.services.generate_image(gateway['id'],'selfie',{'lora':'ahui'},'reference')==b'image'


def test_observability_defaults_to_metadata_only(setup,monkeypatch):
    _,ai,_,_,_,_=setup
    s=ai.data.save('services',None,{'name':'trace','category':'observability','driver':'langfuse','url':'http://127.0.0.1:9990','api_key':'secret','public_key':'public'})
    seen=[];monkeypatch.setattr('wechat_panel.companion_services.request_json',lambda url,body,**kw:seen.append(body) or {})
    run={'id':str(uuid.uuid4()),'agent_id':'agent','dry_run':True,'input':'私人输入','reply':'私人回复'}
    ai.companion.services.trace(s['id'],run,{'status':'completed'})
    assert '私人' not in json.dumps(seen,ensure_ascii=False)


def test_media_json_allows_base64_envelope_and_empty_success(monkeypatch):
    import httpx
    from wechat_panel.ai_tools import request_json
    client_type=httpx.Client
    def reply(request):
        if request.url.path=='/empty':return httpx.Response(202,content=b'')
        return httpx.Response(200,json={'image_base64':'x'*2_100_000})
    monkeypatch.setattr('wechat_panel.ai_tools.httpx.Client',lambda **kw:client_type(transport=httpx.MockTransport(reply),**kw))
    assert request_json('http://test/empty')=={}
    with pytest.raises(RuntimeError,match='大小限制'):request_json('http://test/image')
    assert len(request_json('http://test/image',max_bytes=12_000_000)['image_base64'])==2_100_000


def test_companion_api_template_memory_and_media(tmp_path):
    headers={'X-Panel-Request':'1'}
    with TestClient(create_app(tmp_path,demo=True)) as client:
        p=client.post('/api/ai/config/providers',headers=headers,json={'name':'model','base_url':'http://localhost:9000','model':'test'}).json()
        a=client.post('/api/ai/companion/template',headers=headers,json={'provider_id':p['id']}).json()
        assert a['companion']['memory']['enabled'] and a['companion']['persona']['name']=='阿慧'
        scope={'agent_id':a['id'],'session_id':'test-session'}
        result=client.get('/api/ai/companion',params=scope);assert result.status_code==200
        memory=client.post('/api/ai/companion/memories',headers=headers,json={**scope,'memory':{'key':'偏好','content':'喜欢散步'}})
        assert memory.status_code==200
        assert client.get('/api/ai/companion',params=scope).json()['memories'][0]['content']=='喜欢散步'
        assert client.post('/api/ai/companion/forget',headers=headers,json=scope).status_code==200
        assert client.get('/api/ai/companion',params=scope).json()['memories']==[]
