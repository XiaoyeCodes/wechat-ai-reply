"""Simulate inbound requests locally, send only to File Transfer Assistant.

The panel must be stopped. Real desktop input requires --send-to-self.
No contact/group argument exists and production chat data is never modified.
"""
import argparse
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def verify_self_target(bridge,contact):
    """Fail closed before input; a same-title existing window proves no identity."""
    if (contact.get('lookup')!='文件传输助手' or contact.get('wechat_id')!='filehelper'
            or contact.get('chat_type')!='direct'):
        raise RuntimeError('自测目标必须是内置文件传输助手，未发送消息')
    bridge._check_account(contact)
    if bridge._find_dialog(contact) is not None:
        # Normal contacts have previously verified bindings. This temporary
        # self target has none, so it must be opened from native Features by
        # attach(), rather than reusing a possibly renamed friend's window.
        raise RuntimeError('已有同名独立窗口，无法确认内置自测目标，未发送消息')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-to-self',action='store_true')
    parser.add_argument('--account-id',required=True)
    args=parser.parse_args()
    if not args.send_to_self:raise SystemExit('未授权桌面自测；需显式 --send-to-self')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8765))==0:
            raise SystemExit('必须先停止面板，避免两个微信自动化进程竞争焦点')
    cache=ROOT/'.panel-data/wechat-account.json'
    identity=json.loads(cache.read_text(encoding='utf-8'))
    if identity.get('account_id')!=args.account_id:raise SystemExit('账号缓存不匹配，未执行自测')
    folder=ROOT/'.panel-data'/('self-delivery-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(cache,folder/cache.name)
    os.environ['WECHAT_PANEL_DATA_DIR']=str(folder)
    from PIL import Image,ImageDraw
    from wechat_panel.store import Store,now
    from wechat_panel.ai import AIService
    from wechat_panel.runtime import Runtime
    store=Store(folder/'panel.sqlite3')
    store.activate_account(args.account_id,identity.get('account_name',''))
    ai=AIService(store,model_call=lambda *a:({'content':'仅用于本机发送验证。'},{}))
    provider=ai.data.save('providers',None,dict(name='local-test',model='fake',base_url='http://127.0.0.1:9/v1'))
    agent=ai.data.save('agents',None,dict(name='本机发送自测',provider_id=provider['id'],web_policy='off',
        companion={'memory':{'enabled':False},'safety':{'emotion_enabled':False,'crisis_detection':False}}))
    ai.data.settings({'enabled':True})
    # Fixed built-in self destination. Never load real contacts or recipients.
    contact=store.add_contact(dict(lookup='文件传输助手',nickname='文件传输助手',wechat_id='filehelper',
                                   account_id=args.account_id,chat_type='direct'))
    from wechat_panel.bridge import WeixinBridge
    bridge=WeixinBridge()
    try:
        verify_self_target(bridge,contact)
    finally:
        bridge.close()
    store.update_contact(contact['id'],listening=True,mode='ai_auto',ai_agent_id=agent['id'])
    runtime=Runtime(store);results=[]
    try:
        runtime.start()
        deadline=time.monotonic()+50
        while time.monotonic()<deadline:
            health=runtime.health()
            if health.get('account_id') not in (None,'',args.account_id):raise RuntimeError('当前微信账号变化，停止自测')
            c=store.contact(contact['id'])
            if health['state']=='ready' and c['listener_state'] in ('listening','background'):break
            if c['listener_state']=='error':raise RuntimeError('文件传输助手未能核验：'+c['error'])
            time.sleep(.2)
        else:raise RuntimeError('文件传输助手连接超时')
        token=uuid.uuid4().hex[:8]
        for kind in ('text','file','image'):
            text=f'【本机自测 {token}】模拟入站请求：'+{'text':'请回复一条文字','file':'请以PDF格式回复测试报告','image':'请回复一张测试图片'}[kind]
            with store.connect() as db:
                # An isolated synthetic trigger, outside the real UI snapshot.
                cursor=db.execute('''INSERT INTO messages(contact_id,direction,text,kind,status,created_at,source_key,historical,direction_verified)
                    VALUES(?,?,?,?,?,?,?,?,?)''',(contact['id'],'incoming',text,'text','observed',now(),'synthetic:'+uuid.uuid4().hex,0,1))
                trigger=cursor.lastrowid
            run=ai.create_run(agent['id'],text,contact['id'],trigger)
            if kind=='image':
                image=Image.new('RGB',(320,180),'#164e63');draw=ImageDraw.Draw(image)
                draw.rectangle((20,20,300,160),outline='#f9dc5c',width=6)
                draw.text((42,75),'LOCAL DELIVERY TEST '+token,fill='white')
                data=BytesIO();image.save(data,format='PNG')
                saved=ai.data.run(run['id']);state=saved['state']
                state['artifacts']=[ai.companion.memory.media(state['scope'],data.getvalue())]
                ai.data.update_run(run['id'],status='running',state=state)
            answer=f'【本机自测 {token}】模拟消息已收到，'+{'text':'文字发送测试。','file':'PDF 发送测试。','image':'图片发送测试。'}[kind]
            ai.finish(run['id'],answer)
            deadline=time.monotonic()+145
            while time.monotonic()<deadline:
                result=ai.data.run(run['id'])
                if result['status'] in ('sent','failed','cancelled'):break
                time.sleep(.25)
            outgoing=[m for m in store.messages(contact['id']) if m.get('ai_run_id')==run['id'] and m['direction']=='outgoing']
            row={'kind':kind,'run_id':run['id'],'status':result['status'],'error':result['error'],
                 'messages':[{'id':m['id'],'kind':m['kind'],'status':m['status'],'source_key':m['source_key']} for m in outgoing]}
            results.append(row)
            (folder/'result.json').write_text(json.dumps({'destination':'filehelper','account_id':args.account_id,'cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(row,ensure_ascii=False),flush=True)
            if result['status']!='sent' or len(outgoing)!=1 or outgoing[0]['status']!='confirmed':
                raise RuntimeError('本项未确认送达，停止后续测试；不自动重发')
        print(json.dumps({'passed':True,'result':str(folder/'result.json')},ensure_ascii=False),flush=True)
    finally:
        runtime.stop();ai.stop()


if __name__=='__main__':main()
