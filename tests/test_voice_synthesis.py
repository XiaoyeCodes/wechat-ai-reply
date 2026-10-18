from io import BytesIO
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_voice_reply import queued
from test_ai import setup
from wechat_panel.app import create_app
from wechat_panel.voice_reply import deliver,ReplySuperseded
from wechat_panel.voice_synthesis import synthesize,decode_wav,preset,SAMPLE_TEXT

HEADERS={'X-Panel-Request':'1'}


def wav():
    import numpy as np
    b=BytesIO()
    with wave.open(b,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000)
        f.writeframes((np.sin(np.arange(48000)*.1)*8000).astype('<i2').tobytes())
    return b.getvalue()


def test_online_voice_uses_selected_model_and_is_converted_to_native_pcm(tmp_path,monkeypatch):
    calls=[]
    async def online(text,voice):calls.append((text,voice));return wav()
    monkeypatch.setattr('wechat_panel.voice_synthesis.edge_audio',online)
    selected=synthesize('晚上好，我在呢。',tmp_path/'test.wav','xiaoyi')
    assert selected['id']=='xiaoyi' and calls[0][1]['voice']=='zh-CN-XiaoyiNeural'
    with wave.open(str(tmp_path/'test.wav'),'rb') as f:
        assert f.getsampwidth()==2 and f.getnchannels()==1
        assert f.getnframes()/f.getframerate()==2


def test_online_error_never_silently_uses_mechanical_voice(tmp_path,monkeypatch):
    async def fail(*args):raise TimeoutError('upstream unavailable')
    monkeypatch.setattr('wechat_panel.voice_synthesis.edge_audio',fail)
    monkeypatch.setattr('wechat_panel.wechat_voice.synthesize_windows',lambda *a:pytest.fail('must not change voice'))
    with pytest.raises(RuntimeError,match='不会换回系统音色'):synthesize('晚上好呀',tmp_path/'test.wav','xiaoxiao')
    assert not (tmp_path/'test.wav').exists()


def test_voice_preview_returns_playable_audio_without_queueing_wechat(tmp_path,monkeypatch):
    seen=[]
    monkeypatch.setattr('wechat_panel.voice_api.preview',lambda text,voice:(seen.append((text,voice)),wav())[1])
    with TestClient(create_app(tmp_path,demo=True)) as client:
        response=client.get('/api/voice/voices')
        choices=[v for v in response.json()['voices'] if v['selectable']]
        assert [v['id'] for v in choices if v['engine']!='minimax']==['xiaoxiao','xiaoyi']
        assert [v['id'] for v in choices if v['engine']=='minimax']==[
            'minimax:Chinese (Mandarin)_ExplorativeGirl',
            'minimax:Chinese (Mandarin)_Warm_HeartedGirl',
            'minimax:Chinese (Mandarin)_IntellectualGirl']
        assert client.post('/api/voice/preview',json={'voice_id':'xiaoxiao'}).status_code==403
        response=client.post('/api/voice/preview',headers=HEADERS,json={'voice_id':'xiaoyi'})
        assert response.status_code==200 and response.content.startswith(b'RIFF')
        assert response.headers['content-type']=='audio/wav'
        assert "media-src 'self' blob:" in response.headers['content-security-policy']
        assert seen==[(SAMPLE_TEXT,'xiaoyi')]
        assert client.app.state.store.pending()==[]
        assert client.post('/api/voice/preview',headers=HEADERS,json={'voice_id':'../other'}).status_code==409
        assert client.post('/api/voice/preview',headers=HEADERS,json={'voice_id':'xiaoyi','text':' '*4}).status_code==409
        assert client.post('/api/voice/preview',headers=HEADERS,json={'voice_id':'xiaoyi','text':'a'*121}).status_code==422


def test_delivery_uses_live_selected_voice_and_contact_settings_are_independent(setup,monkeypatch):
    store,ai,contact,run,message=queued(setup)
    other=store.add_contact({'lookup':'乙','nickname':'乙','wechat_id':'b','account_id':'self'})
    before=store.contact(contact['id'])
    store.update_contact(contact['id'],voice_preset='xiaoyi')
    assert store.contact(contact['id'])['ai_revision']==before['ai_revision']
    assert store.contact(other['id'])['voice_preset']=='windows-huihui'
    with pytest.raises(ValueError):store.update_contact(contact['id'],voice_preset='arbitrary-url')
    calls=[]
    def generate(text,path,voice):calls.append(voice);path.write_bytes(wav());return preset(voice)
    monkeypatch.setattr('wechat_panel.voice_synthesis.synthesize',generate)
    def send(contact,path,before_send):before_send();return {'key':'real-voice','text':'语音2秒'}
    result=deliver(SimpleNamespace(send_voice=send),store,message,contact)
    assert calls==['xiaoyi'] and result['delivery']=='voice'
    assert not list((store.path.parent/'voice-tmp').rglob('*.wav'))


def test_switching_voice_during_recording_stops_old_voice(setup,monkeypatch):
    store,ai,contact,run,message=queued(setup)
    def generate(text,path,voice):path.write_bytes(wav());return preset(voice)
    monkeypatch.setattr('wechat_panel.voice_synthesis.synthesize',generate)
    def record(contact,path,before_send):
        store.update_contact(contact['id'],voice_preset='xiaoxiao');before_send()
        pytest.fail('old voice must not send')
    with pytest.raises(ReplySuperseded,match='音色'):deliver(SimpleNamespace(send_voice=record),store,message,contact)
