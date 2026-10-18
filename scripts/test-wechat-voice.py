"""Explicit one-message native voice test; no automatic retry after sending."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request
import win32gui

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from wechat_panel.bridge import WeixinBridge
from wechat_panel.store import Store
from wechat_panel.voice_synthesis import synthesize,PRESETS

def api(path,body=None):
    r=urllib.request.Request('http://127.0.0.1:8765'+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'x-panel-request':'1','Content-Type':'application/json'})
    with urllib.request.urlopen(r,timeout=10) as res:return json.load(res)

def main():
    p=argparse.ArgumentParser();p.add_argument('--contact-id',required=True)
    p.add_argument('--text',default='你好，这是一条自动语音测试。收到后，可以直接在微信里播放。')
    p.add_argument('--voice-preset',choices=[v['id'] for v in PRESETS])
    args=p.parse_args()
    state=api('/api/status')
    if state['busy'] or state['queue_size'] or state['ai']['running']:raise RuntimeError('工作台正在处理消息，稍后再测试')
    store=Store(ROOT/'.panel-data/panel.sqlite3')
    contact=store.contact(args.contact_id)
    voice_id=args.voice_preset or contact.get('voice_preset','windows-huihui')
    import hashlib
    label=hashlib.sha256(voice_id.encode()).hexdigest()[:12] if voice_id.startswith('minimax:') else voice_id
    audio=ROOT/'output/verification'/('wechat-voice-'+label+'-test.wav')
    selected=synthesize(args.text,audio,voice_id,store=store,model=contact.get('voice_model','speech-2.8-turbo'))
    foreground=win32gui.GetForegroundWindow();bridge=None
    api('/api/runtime/pause',{'paused':True});time.sleep(1)
    try:
        bridge=WeixinBridge()
        result=bridge.send_voice(contact,audio)
        window=bridge.windows[contact['id']]
        screenshot=ROOT/'output/verification'/('wechat-voice-'+label+'-sent.png')
        bridge._capture(window)[0].save(screenshot)
        report={'contact':contact['lookup'],'text':args.text,'voice_receipt':result,'screenshot':str(screenshot),
                'audio':str(audio),'microphone_restored':True,'voice':selected}
        (ROOT/'output/verification'/('wechat-voice-'+label+'-result.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        api('/api/runtime/pause',{'paused':state['paused']})
        if foreground and win32gui.IsWindow(foreground):
            try:win32gui.SetForegroundWindow(foreground)
            except Exception:pass

if __name__=='__main__':main()
