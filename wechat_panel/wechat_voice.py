"""Native voice delivery using Weixin 4.1's recording control on Windows.

Use only in the serial UI worker, or with that worker paused. A dedicated Realtek
analog output feeds Stereo Mix; the physical microphone is never recorded.
"""
from pathlib import Path
import json
import time
import wave


def synthesize_windows(text,path):
    import win32com.client
    if not text.strip() or len(text)>200:raise ValueError('单条语音限 1～200 字')
    path=Path(path).resolve();path.parent.mkdir(parents=True,exist_ok=True)
    speaker=win32com.client.Dispatch('SAPI.SpVoice')
    voices=[v for v in speaker.GetVoices() if 'Huihui' in v.GetDescription()]
    if not voices:raise ValueError('未安装 Windows 中文慧慧音色')
    speaker.Voice=voices[0]
    stream=win32com.client.Dispatch('SAPI.SpFileStream');stream.Format.Type=22
    stream.Open(str(path),3)
    try:
        speaker.AudioOutputStream=stream;speaker.Speak(text)
    finally:stream.Close()
    return path


def load_pcm(path):
    import numpy as np
    with wave.open(str(path),'rb') as src:
        if src.getsampwidth()!=2 or src.getnchannels() not in (1,2):raise ValueError('语音须为单声道或双声道 16 位 PCM WAV')
        rate=src.getframerate();channels=src.getnchannels()
        samples=np.frombuffer(src.readframes(src.getnframes()),dtype='<i2').astype('float32').reshape(-1,channels)/32768
    seconds=len(samples)/rate
    if not 1<=seconds<=50:raise ValueError('原生语音支持 1～50 秒音频')
    if float(np.max(np.abs(samples)))<.01:raise ValueError('音频内容为空或接近静音')
    mono=samples.mean(axis=1)
    mono=np.interp(np.arange(round(seconds*48000))/48000,np.arange(len(mono))/rate,mono).astype('float32')
    return np.column_stack((mono,mono)),seconds


class RealtekLoopback:
    """Temporarily route recording to a proven software loopback, then restore."""
    def __init__(self,journal):self.journal=Path(journal);self.original={};self.policy=None;self.volumes=[];self.recording=False

    def __enter__(self):
        import comtypes
        import sounddevice as sd
        from pycaw.pycaw import AudioUtilities
        from pycaw.constants import CLSID_CPolicyConfigClient,ERole,EDataFlow
        from pycaw.api.policyconfig import IPolicyConfig
        from pycaw.api.audiopolicy import IAudioSessionControl2
        if self.journal.exists():raise RuntimeError('上次音频路由未恢复，请先核对 voice-route.json')
        self.roles=[ERole.eConsole,ERole.eMultimedia,ERole.eCommunications]
        self.enum=AudioUtilities.GetDeviceEnumerator()
        devices=AudioUtilities.GetAllDevices()
        mixes=[d for d in devices if d.FriendlyName=='立体声混音 (Realtek USB Audio)']
        renders=[d for d in devices if d.FriendlyName=='扬声器 (Realtek USB Audio)' and d.state.value in (1,8)]
        if len(mixes)!=1 or len(renders)!=1:raise RuntimeError('需要可用的 Realtek 立体声混音和模拟输出，尚未发送')
        self.mix=mixes[0];self.was_enabled=self.mix.state.value==1
        sessions=renders[0].AudioSessionManager.GetSessionEnumerator()
        if any(sessions.GetSession(i).QueryInterface(IAudioSessionControl2).GetState()==1 for i in range(sessions.GetCount())):
            raise RuntimeError('Realtek 模拟输出正在播放其他内容，请空闲后再试')
        self.volumes=[(d,d.EndpointVolume.GetMasterVolumeLevelScalar(),d.EndpointVolume.GetMute()) for d in (self.mix,renders[0])]
        self.original={r:self.enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value,r.value).GetId() for r in self.roles}
        self.policy=comtypes.CoCreateInstance(CLSID_CPolicyConfigClient,IPolicyConfig,comtypes.CLSCTX_ALL)
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        self.journal.write_text(json.dumps({'capture_defaults':{str(r.value):d for r,d in self.original.items()},
                                           'mix':self.mix.id,'was_enabled':self.was_enabled,
                                           'volumes':[{'id':d.id,'volume':v,'mute':m} for d,v,m in self.volumes]}),encoding='utf-8')
        try:
            self.policy.SetEndpointVisibility(self.mix.id,True);time.sleep(.3)
            for d,_,_ in self.volumes:
                d.EndpointVolume.SetMasterVolumeLevelScalar(1,None);d.EndpointVolume.SetMute(0,None)
            AudioUtilities.SetDefaultDevice(self.mix.id,self.roles)
            # Enumerate again after enabling the previously hidden endpoint.
            sd._terminate();sd._initialize()
            devices=sd.query_devices();apis=sd.query_hostapis()
            outputs=[i for i,d in enumerate(devices) if d['name']=='扬声器 (Realtek USB Audio)' and apis[d['hostapi']]['name']=='Windows WDM-KS']
            inputs=[i for i,d in enumerate(devices) if d['name']=='立体声混音 (Realtek USB Audio)' and apis[d['hostapi']]['name']=='Windows WASAPI']
            if len(outputs)!=1 or len(inputs)!=1:raise RuntimeError('无法唯一识别专用音频通路')
            self.output,self.input=outputs[0],inputs[0]
            self.mix=next(d for d in AudioUtilities.GetAllDevices() if d.id==self.mix.id)
            return self
        except Exception:
            self.restore();raise

    def check(self,pid):
        from pycaw.constants import EDataFlow
        from pycaw.api.audiopolicy import IAudioSessionControl2
        if any(self.enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value,r.value).GetId()!=self.mix.id for r in self.roles):
            raise RuntimeError('录音输入设备已变化，已取消语音')
        sessions=self.mix.AudioSessionManager.GetSessionEnumerator()
        if not any(sessions.GetSession(i).QueryInterface(IAudioSessionControl2).GetProcessId()==pid for i in range(sessions.GetCount())):
            raise RuntimeError('未核对到微信使用立体声混音，已取消录音')

    def restore(self):
        from pycaw.pycaw import AudioUtilities
        errors=[]
        for role,device in self.original.items():
            try:AudioUtilities.SetDefaultDevice(device,[role])
            except Exception:errors.append('默认录音设备')
        for device,volume,mute in self.volumes:
            try:
                device.EndpointVolume.SetMasterVolumeLevelScalar(volume,None);device.EndpointVolume.SetMute(mute,None)
            except Exception:errors.append('原音量')
        if self.policy and not self.was_enabled:
            try:self.policy.SetEndpointVisibility(self.mix.id,False)
            except Exception:errors.append('立体声混音状态')
        if errors:raise RuntimeError('音频设置恢复失败：'+','.join(errors)+'；备份位于 '+str(self.journal))
        self.journal.unlink(missing_ok=True)

    def __exit__(self,*_):
        if self.recording:
            # Never restore a physical mic into a recording that failed to close.
            self.mix.EndpointVolume.SetMute(1,None)
            raise RuntimeError('微信录音尚未退出，已保留静音混音输入；请先取消微信录音，再从 voice-route.json 恢复音频设置')
        self.restore()


def signal_similarity(source,recorded):
    """Compare short-time speech energy, resistant to capture latency and gain."""
    import numpy as np
    def envelope(samples):
        x=samples.mean(axis=1);x=x-x.mean();n=len(x)//960
        if n<2:return np.array([])
        e=np.sqrt(np.mean(x[:n*960].reshape(n,960)**2,axis=1))
        return e-e.mean()
    a,b=envelope(source),envelope(recorded)
    if not a.size or not b.size:return 0
    denominator=float(np.linalg.norm(a)*np.linalg.norm(b))
    return float(np.max(np.correlate(a,b,'full'))/denominator) if denominator>1e-8 else 0


def send_wav(bridge,contact,path,before_send=None,interruption_guard=None):
    import numpy as np
    import sounddevice as sd
    import win32gui
    from .bridge import BridgeError,SendUncertain
    audio,seconds=load_pcm(path)
    if before_send:before_send()
    window,edit,_,_=bridge._prepare_send(contact,'[语音]')
    if edit.window_text():raise BridgeError('聊天输入框有未发送草稿，请处理后再发送语音')
    start=window.child_window(title='发语音 ( 按住右 Alt )',control_type='Button')
    if not start.exists(timeout=.3):raise BridgeError('当前微信窗口没有原生发语音按钮')
    baseline=bridge.snapshot(contact)
    before={x['key'] for x in baseline}
    observer=getattr(bridge,'receipt_observer',None)
    if observer:observer('prepared',baseline,0)
    attempted=False;recording=False;captured=[]
    def focused():
        if win32gui.GetAncestor(win32gui.GetForegroundWindow(),2)!=window.handle:
            raise BridgeError('录音期间焦点发生变化，已停止本次操作')
    def cancel():
        import pyautogui
        if not win32gui.IsWindow(window.handle):return
        if start.exists(timeout=.2):return
        # Weixin exposes InvokePattern here but silently ignores it. Escape is
        # sent only after confirming this exact chat owns the foreground.
        window.set_focus();focused();pyautogui.press('esc');time.sleep(.3)
        if not start.exists(timeout=.3):raise BridgeError('请在微信取消尚未发送的录音')
        bridge._record('voice.cancelled',contact)
    try:
        with RealtekLoopback(Path(bridge.trace_path).with_name('voice-route.json')) as route:
            try:
                focused();recording=True;route.recording=True;bridge._voice_recording=True;bridge._click(start,contact,'voice.record')
                time.sleep(.5);route.check(window.process_id())
                with sd.InputStream(device=route.input,samplerate=48000,channels=2,
                                    callback=lambda data,*args:captured.append(data.copy())):
                    sd.play(audio,samplerate=48000,device=route.output,blocking=False)
                    while sd.get_stream().active:
                        focused();time.sleep(.05)
                    sd.wait();time.sleep(.3)
                recorded=np.concatenate(captured) if captured else np.empty((0,2))
                rms=float(np.sqrt(np.mean(recorded**2))) if recorded.size else 0
                similarity=signal_similarity(audio,recorded)
                bridge._record('voice.audio_checked',contact,duration=seconds,rms=rms,similarity=similarity)
                if rms<.002 or similarity<.7:raise BridgeError(f'录入声音与合成语音未匹配（相似度 {similarity:.2f}），本次未发送')
                focused();route.check(window.process_id());bridge._check_account(contact)
                if before_send:
                    from .voice_reply import ReplySuperseded
                    # The serial worker cannot poll while recording. Read once
                    # before sending so a new incoming turn wins immediately.
                    new=[m for m in bridge.snapshot(contact) if m['key'] not in before
                         and m['direction'] in ('incoming','outgoing')]
                    if (interruption_guard(new) if interruption_guard else bool(new)):
                        raise ReplySuperseded('录音期间聊天有新消息，本条语音已取消，将合并新消息回复')
                    before_send()
                if bridge._title(window)!=contact['lookup']:raise BridgeError('录音目标联系人发生变化')
                send=window.child_window(title='发送语音',control_type='Button')
                if not send.exists(timeout=.3) or not send.is_enabled():raise BridgeError('语音发送按钮未就绪')
                attempted=True
                if observer:observer('attempted')
                bridge._click(send,contact,'voice.send_once');time.sleep(.3)
                if not start.exists(timeout=.5):raise SendUncertain('语音发送后界面尚未恢复，请核对微信')
                recording=False;route.recording=False
            finally:
                sd.stop()
                bridge._voice_recording=False
                if recording:
                    cancel();route.recording=False
        for _ in range(20):
            time.sleep(.25)
            matches=[m for m in bridge.snapshot(contact) if m['key'] not in before and m['direction']=='outgoing'
                     and m.get('kind')=='voice']
            if len(matches)==1:
                bridge._record('voice.confirmed',contact,source_key=matches[0]['key'])
                return matches[0]
        raise SendUncertain('已尝试发送原生语音，但暂未核对到新增本人语音；请在微信确认，勿重复发送')
    except Exception as exc:
        if attempted and not isinstance(exc,SendUncertain):raise SendUncertain('语音发送结果需要在微信核对，勿重复发送：'+str(exc)) from exc
        raise
