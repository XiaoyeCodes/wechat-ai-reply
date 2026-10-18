"""Send an explicitly selected generated image once via the real serial queue.

Stop the panel first. This sends a real message to --contact-id.
"""
import argparse
import json
from pathlib import Path
import socket
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contact-id',required=True)
    parser.add_argument('--image',required=True,type=Path)
    args=parser.parse_args()
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',8765))==0:raise SystemExit('请先停止面板，避免两个微信操作进程同时运行')
    from wechat_panel.store import Store
    from wechat_panel.ai import AIService
    from wechat_panel.runtime import Runtime
    from wechat_panel.conversation_turn import latest_id
    store=Store(ROOT/'.panel-data/panel.sqlite3')
    contact=store.contact(args.contact_id)
    if contact['mode']!='ai_auto' or not contact['listening'] or store.paused():
        raise SystemExit('联系人必须已启用自动回复与监听，且未暂停')
    ai=AIService(store);runtime=Runtime(store)
    try:
        runtime.start()
        for _ in range(150):
            if runtime.health()['state']=='ready' and store.contact(contact['id'])['listener_state']=='listening':break
            time.sleep(.2)
        else:raise RuntimeError('微信会话未就绪')
        run=ai.create_run(contact['ai_agent_id'],'回传之前生成的图片（图片发送验证）',contact['id'])
        saved=ai.data.run(run['id']);state=saved['state']
        with store.connect() as db:
            latest=latest_id(db,contact['id'],state['snapshot']['agent'])
            db.execute('UPDATE ai_runs SET resume_message_id=? WHERE id=?',(latest,run['id']))
        media=ai.companion.memory.media(state['scope'],args.image.read_bytes())
        state['artifacts']=[media]
        ai.data.update_run(run['id'],status='running',state=state)
        ai.data.trace(run['id'],'生成媒体',{**media,'verification':'复用之前生成的图片，验证微信发送'})
        ai.finish(run['id'],'图片已经生成，正在发送到本次微信会话。')
        print(json.dumps({'run_id':run['id'],'media_id':media['id']},ensure_ascii=False),flush=True)
        for _ in range(720):
            saved=ai.data.run(run['id'])
            if saved['status'] in ('sent','failed','cancelled','uncertain'):break
            time.sleep(.25)
        result={'run_id':run['id'],'status':saved['status'],'error':saved['error']}
        (ROOT/'tmp/wechat-image-send-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if saved['status']!='sent':raise SystemExit(1)
    finally:
        runtime.stop();ai.stop()


if __name__=='__main__':main()
