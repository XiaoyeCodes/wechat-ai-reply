from io import BytesIO
import json
import shutil
import subprocess
import uuid
import wave
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from test_ai import setup
from test_voice_reply import queued
from wechat_panel.app import create_app
from wechat_panel.store import Store
from wechat_panel.minimax_voice import MiniMaxVoice,MiniMaxError,remote_call,validate_sample
from wechat_panel.voice_synthesis import synthesize
from wechat_panel.voice_reply import deliver,ReplySuperseded

HEADERS={'X-Panel-Request':'1'}
VOICE='minimax:Chinese (Mandarin)_Sweet_Lady'
KEY='sk-private-voice-test-key'


def wav(seconds=12,silence=False):
    b=BytesIO()
    with wave.open(b,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000)
        f.writeframes((np.sin(np.arange(int(24000*seconds))*.1)*(0 if silence else 8000)).astype('<i2').tobytes())
    return b.getvalue()


@pytest.fixture
def remote(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url.host=='api.minimax.cn'
        assert request.headers['authorization']=='Bearer '+KEY
        route=request.url.path
        data={'base_resp':{'status_code':0}}
        if route=='/v1/get_voice':
            data.update(voice_cloning=[{'voice_id':'remote-voice','voice_name':'远端音色'}],
                        system_voice=[{'voice_id':'other-system-voice','voice_name':'其他预设'}])
        elif route=='/v1/files/upload':
            assert b'voice_clone' in request.content and b'filename="sample.wav"' in request.content
            data['file']={'file_id':'123456789012345678'}
        elif route=='/v1/voice_clone':
            payload=json.loads(request.content)
            assert payload['file_id']==123456789012345678
            assert payload['voice_id'].startswith('Relay')
            # No paid activation or automatic preview during clone creation.
            assert 'text' not in payload
        elif route=='/v1/t2a_v2':
            payload=json.loads(request.content)
            assert payload['voice_setting']['speed']==.95
            assert payload['output_format']=='hex'
            data['data']={'audio':wav(2).hex(),'status':2}
        else:pytest.fail(route)
        return httpx.Response(200,json=data)
    monkeypatch.setattr('wechat_panel.minimax_voice.http_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    return calls


def test_key_is_encrypted_not_returned_and_validation_never_echoes_secret(tmp_path):
    with TestClient(create_app(tmp_path,demo=True)) as client:
        assert not client.get('/api/voice/minimax/config').json()['key_configured']
        assert client.put('/api/voice/minimax/config',json={'api_key':KEY}).status_code==403
        assert client.put('/api/voice/minimax/config',headers=HEADERS,json={'api_key':KEY}).json()['key_configured']
        with client.app.state.store.connect() as db:
            saved=db.execute("SELECT value FROM settings WHERE key='minimax_voice'").fetchone()[0]
        assert KEY not in saved
        assert MiniMaxVoice(client.app.state.store).key()==KEY
        for endpoint in ('/api/voice/minimax/config','/api/voice/voices','/api/bootstrap'):
            body=client.get(endpoint).text
            assert KEY not in body and '"secret"' not in body
        bad=client.put('/api/voice/minimax/config',headers=HEADERS,json={'api_key':KEY*300})
        assert bad.status_code==422 and KEY not in bad.text
        client.put('/api/voice/minimax/config',headers=HEADERS,json={'api_key':''})
        assert MiniMaxVoice(client.app.state.store).key()==KEY
        client.put('/api/voice/minimax/config',headers=HEADERS,json={'clear_secret':True})
        assert not client.get('/api/voice/minimax/config').json()['key_configured']


def test_upload_clone_preview_persistence_and_no_wechat_send(tmp_path,remote):
    with TestClient(create_app(tmp_path,demo=True)) as client:
        store=client.app.state.store
        MiniMaxVoice(store).save_config(KEY)
        cid=client.post('/api/contacts',headers=HEADERS,json={'lookup':'阿慧测试','wechat_id':'test-a'}).json()['id']
        original=store.contact(cid)
        request_id=str(uuid.uuid4());url='/api/voice/minimax/clones/'+request_id
        response=client.post(url,params={'name':'阿慧专属','format':'wav'},headers=HEADERS,content=wav())
        assert response.status_code==200,response.text
        vid=response.json()['voice_id']
        assert [c.url.path for c in remote]==['/v1/files/upload','/v1/voice_clone']
        # Replaying the same request never creates another voice.
        again=client.post(url,params={'name':'阿慧专属','format':'wav'},headers=HEADERS,content=wav())
        assert again.status_code==200 and len(remote)==2
        assert store.contact(cid)['voice_preset']==original['voice_preset']
        assert any(v['id']==vid for v in MiniMaxVoice(Store(store.path)).voices())
        # Refresh omits unactivated clone; the locally created voice must remain selectable.
        assert client.post('/api/voice/minimax/refresh',headers=HEADERS).status_code==200
        catalog=client.get('/api/voice/voices').json()['voices']
        assert any(v['id']==vid and v['selectable'] for v in catalog)
        assert any(v['id']=='minimax:remote-voice' and v['selectable'] for v in catalog)
        assert not next(v for v in catalog if v['id']=='minimax:other-system-voice')['selectable']
        changed=client.patch('/api/contacts/'+cid,headers=HEADERS,json={'voice_preset':vid,'voice_model':'speech-2.8-hd'})
        assert changed.status_code==200,changed.text
        assert changed.json()['ai_revision']==original['ai_revision']
        response=client.post('/api/voice/preview',headers=HEADERS,json={'voice_id':vid,'text':'今天辛苦啦，我在呢。','model':'speech-2.8-hd'})
        assert response.status_code==200 and response.content.startswith(b'RIFF')
        sent=json.loads(remote[-1].content)
        assert sent['model']=='speech-2.8-hd' and sent['voice_setting']['voice_id']==vid.removeprefix('minimax:')
        assert store.pending()==[] and store.messages(cid)==[]
        assert not list(tmp_path.rglob('*.wav'))


@pytest.mark.parametrize('voice_id',[
    'Chinese (Mandarin)_ExplorativeGirl',
    'Chinese (Mandarin)_Warm_HeartedGirl',
    'Chinese (Mandarin)_IntellectualGirl',
])
def test_selected_presets_send_exact_voice_id(tmp_path,remote,voice_id):
    with TestClient(create_app(tmp_path,demo=True)) as client:
        MiniMaxVoice(client.app.state.store).save_config(KEY)
        response=client.post('/api/voice/preview',headers=HEADERS,
                             json={'voice_id':'minimax:'+voice_id,'text':'你好呀。'})
        assert response.status_code==200 and response.content.startswith(b'RIFF')
        assert json.loads(remote[-1].content)['voice_setting']['voice_id']==voice_id


def test_invalid_samples_never_upload_and_key_required(tmp_path,remote):
    with TestClient(create_app(tmp_path,demo=True)) as client:
        route='/api/voice/minimax/clones/'+str(uuid.uuid4())+'?name=test'
        assert client.post(route,headers=HEADERS,content=wav()).status_code==409
        MiniMaxVoice(client.app.state.store).save_config(KEY)
        for data in (b'not a wav',wav(2),wav(12,True)):
            assert client.post(route,headers=HEADERS,content=data).status_code==409
        assert client.post(route,headers=HEADERS,content=b'x'*20_000_001).status_code==413
        assert remote==[]
    with pytest.raises(ValueError,match='文件头'):validate_sample(b'file:///secret','m4a')


@pytest.mark.parametrize('extension,codec',[('m4a','aac'),('mp3','libmp3lame')])
def test_compressed_samples_decode_before_upload(tmp_path,extension,codec):
    executable=shutil.which('ffmpeg')
    if not executable:pytest.skip('FFmpeg is needed to produce the compressed sample')
    source=tmp_path/'sample.wav';source.write_bytes(wav())
    target=tmp_path/('sample.'+extension)
    subprocess.run([executable,'-nostdin','-v','error','-i',str(source),'-c:a',codec,str(target)],
                   check=True,capture_output=True,timeout=25,
                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    audio,normalized,duration=validate_sample(target.read_bytes(),extension)
    assert 11.9<=duration<=12.2
    assert normalized==('wav' if extension=='m4a' else 'mp3')
    if extension=='m4a':assert audio.startswith(b'RIFF')


@pytest.mark.parametrize('response',[
    httpx.Response(401,json={'detail':KEY}),
    httpx.Response(200,json={'base_resp':{'status_code':1008,'status_msg':KEY}}),
    httpx.Response(200,json={'base_resp':{'status_code':1004,'status_msg':KEY}}),
    httpx.Response(302,headers={'Location':'https://attacker.test/'+KEY}),
    httpx.Response(200,content=KEY),
])
def test_remote_errors_are_redacted_and_not_retried(monkeypatch,response):
    calls=[]
    def handler(request):calls.append(request);return response
    monkeypatch.setattr('wechat_panel.minimax_voice.http_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MiniMaxError) as exc:remote_call(KEY,'/v1/get_voice',body={'voice_type':'all'})
    assert KEY not in str(exc.value) and len(calls)==1


def test_uncertain_clone_is_durable_and_not_recreated(tmp_path,remote,monkeypatch):
    from wechat_panel import minimax_voice as module
    service=MiniMaxVoice(Store(tmp_path/'test.sqlite3'));service.save_config(KEY)
    original=module.remote_call
    def fail(key,endpoint,**kwargs):
        if endpoint=='/v1/voice_clone':raise httpx.ReadTimeout('secret '+KEY)
        return original(key,endpoint,**kwargs)
    monkeypatch.setattr(module,'remote_call',fail)
    rid=str(uuid.uuid4())
    with pytest.raises(MiniMaxError,match='不会自动重复创建') as exc:service.clone(rid,'测试',wav(),'wav')
    assert KEY not in str(exc.value)
    with pytest.raises(ValueError,match='不会自动重复创建'):service.clone(rid,'测试',wav(),'wav')
    assert len(remote)==1


@pytest.mark.parametrize('code,phrase,status',[
    (1008,'API 账户余额不足','failed'),
    (2037,'样音时长不符合要求','failed'),
    (2038,'未开通声音克隆权限','failed'),
    (2013,'请求参数无效','failed'),
    (1001,'请求超时','uncertain'),
    (2039,'音色编号已存在','uncertain'),
])
def test_clone_preserves_provider_reason_and_distinguishes_rejection(tmp_path,monkeypatch,code,phrase,status):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/v1/files/upload':
            return httpx.Response(200,json={'base_resp':{'status_code':0},'file':{'file_id':12345}})
        return httpx.Response(200,json={'base_resp':{'status_code':code,'status_msg':'remote secret '+KEY}})
    monkeypatch.setattr('wechat_panel.minimax_voice.http_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    with TestClient(create_app(tmp_path,demo=True)) as client:
        store=client.app.state.store;MiniMaxVoice(store).save_config(KEY)
        rid=str(uuid.uuid4());route='/api/voice/minimax/clones/'+rid
        response=client.post(route,params={'name':'错误原因测试','format':'wav'},headers=HEADERS,content=wav())
        assert response.status_code==503
        assert phrase in response.json()['detail'] and str(code) in response.json()['detail']
        assert ('结果尚无法确认' in response.json()['detail'])==(status=='uncertain')
        assert KEY not in response.text
        with store.connect() as db:
            row=db.execute('SELECT status,error FROM voice_clones WHERE request_id=?',(rid,)).fetchone()
        assert row['status']==status and row['error']==response.json()['detail']
        # Replaying the same request doesn't upload or clone again, even for rejection.
        client.post(route,params={'name':'错误原因测试','format':'wav'},headers=HEADERS,content=wav())
        assert calls==['/v1/files/upload','/v1/voice_clone']
        assert store.pending()==[]


@pytest.mark.parametrize('model',['speech-2.8-turbo','speech-2.8-hd'])
def test_native_delivery_uses_saved_minimax_voice_and_model(setup,remote,model):
    store,ai,contact,run,message=queued(setup)
    MiniMaxVoice(store).save_config(KEY)
    store.update_contact(contact['id'],voice_preset=VOICE,voice_model=model)
    sent=[]
    def send(contact,path,before_send):
        before_send()
        with wave.open(str(path)) as f:assert f.getsampwidth()==2 and f.getnchannels()==1
        sent.append(path)
        return {'key':'outgoing-voice','text':'语音2秒'}
    result=deliver(SimpleNamespace(send_voice=send),store,message,contact)
    assert result['delivery']=='voice' and len(sent)==1 and not sent[0].exists()
    body=json.loads(remote[-1].content)
    assert body['model']==model and body['voice_setting']['voice_id']==VOICE.removeprefix('minimax:')


def test_model_switch_cancels_old_recording_and_balance_error_falls_back_before_recording(setup,remote,monkeypatch):
    store,ai,contact,run,message=queued(setup)
    MiniMaxVoice(store).save_config(KEY)
    store.update_contact(contact['id'],voice_preset=VOICE)
    def send(*args,before_send):
        store.update_contact(contact['id'],voice_model='speech-2.8-hd');before_send()
        pytest.fail('must cancel old model')
    with pytest.raises(ReplySuperseded,match='模型'):deliver(SimpleNamespace(send_voice=send),store,message,contact)
    def fail(*args,**kwargs):raise MiniMaxError('MiniMax 账户余额不足（1008）')
    monkeypatch.setattr('wechat_panel.minimax_voice.remote_call',fail)
    bridge=SimpleNamespace(send=lambda c,t:'text-result',send_voice=lambda *a,**k:pytest.fail('must not start recording'))
    result=deliver(bridge,store,message,contact)
    assert result['delivery']=='text' and '余额不足' in result['voice_fallback']


def test_no_unknown_voices_and_account_catalogs_are_isolated(tmp_path,remote):
    store=Store(tmp_path/'test.sqlite3');service=MiniMaxVoice(store);service.save_config(KEY);service.refresh()
    assert service.get_voice('minimax:remote-voice')['kind']=='clone'
    service.save_config('other-account-key')
    with pytest.raises(ValueError):service.get_voice('minimax:remote-voice')
    with pytest.raises(ValueError):synthesize('测试',tmp_path/'test.wav','minimax:unknown',store=store)
