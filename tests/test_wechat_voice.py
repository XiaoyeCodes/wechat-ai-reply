from types import SimpleNamespace
import wave

import pytest
np=pytest.importorskip('numpy')

from wechat_panel.wechat_voice import load_pcm,signal_similarity,RealtekLoopback


def test_recording_match_accepts_latency_and_gain_but_rejects_silence_and_noise():
    t=np.arange(4*48000)/48000
    envelope=np.maximum(0,np.sin(t*5)+.3*np.sin(t*13))
    source=np.column_stack([envelope*np.sin(2*np.pi*300*t)]*2).astype('float32')
    delayed=np.pad(source*.08,((9600,4800),(0,0)))
    assert signal_similarity(source,delayed)>.85
    assert signal_similarity(source,np.zeros_like(source))==0
    assert signal_similarity(source,np.random.default_rng(1).normal(0,.01,source.shape))<.2


@pytest.mark.parametrize('seconds,amplitude',[(.5,.1),(51,.1),(2,0)])
def test_invalid_or_empty_audio_is_rejected_before_ui_actions(tmp_path,seconds,amplitude):
    path=tmp_path/'test.wav'
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000)
        w.writeframes((np.sin(np.arange(round(seconds*8000))*.1)*amplitude*32767).astype('<i2').tobytes())
    with pytest.raises(ValueError):load_pcm(path)


def test_restore_attempts_every_role_and_keeps_backup_if_one_fails(tmp_path,monkeypatch):
    pytest.importorskip('pycaw')
    from pycaw.pycaw import AudioUtilities
    route=RealtekLoopback(tmp_path/'journal.json');route.journal.write_text('{}')
    route.original={0:'first',1:'second',2:'third'};route.was_enabled=False
    route.mix=SimpleNamespace(id='mix');changes=[]
    route.policy=SimpleNamespace(SetEndpointVisibility=lambda *a:changes.append(a))
    def restore(device,roles):
        changes.append(device)
        if device=='second':raise OSError('failure')
    monkeypatch.setattr(AudioUtilities,'SetDefaultDevice',restore)
    with pytest.raises(RuntimeError,match='恢复失败'):route.restore()
    assert changes==['first','second','third',('mix',False)]
    assert route.journal.exists()


def test_failed_recording_cancel_keeps_muted_loopback_instead_of_physical_microphone(tmp_path):
    route=RealtekLoopback(tmp_path/'journal.json');route.recording=True
    calls=[];route.mix=SimpleNamespace(EndpointVolume=SimpleNamespace(SetMute=lambda *a:calls.append(a)))
    route.restore=lambda:calls.append('restore')
    with pytest.raises(RuntimeError,match='静音'):route.__exit__(None,None,None)
    assert calls==[(1,None)]
