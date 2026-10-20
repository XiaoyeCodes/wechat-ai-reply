"""Test saved Aria models with quoted text/images in isolated direct/group chats.

Uses encrypted local resources and the supplied test image. Never sends WeChat.
"""
import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wechat_panel.ai import AIService
from wechat_panel.companion_store import CompanionStore
from wechat_panel.group_bridge import group_key
from wechat_panel.store import Store
from wechat_panel.wechat_quotes import split_quote


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent-id',required=True)
    parser.add_argument('--image',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();results=[]
    with tempfile.TemporaryDirectory(prefix='quoted-dialogue-') as folder:
        store=Store(Path(folder)/'panel.sqlite3');ai=AIService(store)
        try:
            with sqlite3.connect('.panel-data/panel.sqlite3') as source,store.connect() as target:
                target.execute('DELETE FROM ai_resources')
                for row in source.execute('SELECT kind,id,data,created_at,updated_at FROM ai_resources'):
                    target.execute('INSERT INTO ai_resources VALUES(?,?,?,?,?)',row)
            ai.data.settings({'enabled':True});agent=ai.data.get('agents',args.agent_id)
            for group in (False,True):
                name='隔离引用群' if group else '隔离引用私聊'
                contact=store.add_contact({'lookup':name,'nickname':name,'account_id':'quote-qa-self',
                    'wechat_id':group_key('quote-qa-self',name) if group else 'quote-qa-contact',
                    'chat_type':'group' if group else 'direct','group_members':['Aria'],
                    'group_self_name':'机器人','group_identity_verified':True,'group_members_verified':True,'group_self_name_verified':True})
                cid=contact['id'];store.update_contact(cid,mode='ai_auto',listening=True,ai_agent_id=agent['id'])
                store.ingest(cid,[]);rows=[]
                chosen=CompanionStore(store).media('wechat:'+cid,args.image.read_bytes())

                def incoming(question,quoted=None):
                    item={'key':'qa-row-'+str(len(rows)),'kind':'text','text':question,'direction':'incoming',
                        'sender':'Aria','sender_verified':True,'mention_me':True,'mention_verified':True}
                    if quoted is not None:
                        raw=question+'\n引用 原文作者 的消息 : '+quoted
                        item.update(split_quote(raw));item['identity_text']=raw
                        if item['quote']['kind']=='image':item['quote'].update(status='ready',media_id=chosen['id'])
                    rows.append(item);store.ingest(cid,rows)
                    return store.messages(cid)[-1]

                def turn(question,quoted=None):
                    if group:question='@机器人 '+question
                    message=incoming(question,quoted)
                    run=ai.create_run(agent['id'],question,cid,message['id']);ai.execute(run['id'])
                    run=ai.data.run(run['id']);detail=ai.data.detail(run['id'])
                    result={'chat_type':'group' if group else 'direct','question':question,'quoted':quoted,
                        'reply':run['reply'],'status':run['status'],'error':run['error'],'trace':detail['trace'],
                        'images':run['state'].get('dynamic_context',{}).get('images',[])}
                    results.append(result)
                    print(json.dumps({k:result[k] for k in ('chat_type','question','status','reply','error')},ensure_ascii=False),flush=True)
                    for outgoing in store.pending():
                        if outgoing.get('ai_run_id')!=run['id']:continue
                        key='isolated-quote-receipt-'+str(outgoing['id'])
                        store.set_message_state(outgoing['id'],'confirmed',source_key=key)
                        rows.append({'key':key,'kind':'text','text':outgoing['text'],'direction':'outgoing',
                            'sender':'机器人','sender_verified':True})
                    store.ingest(cid,rows)
                    return result

                turn('我应该几点到？只回答到达时间，不要联网。','明天下午三点在会议室开会，请提前十分钟到。')
                buffer=BytesIO();Image.new('RGB',(30,30),'red').save(buffer,format='PNG')
                rows.append({'key':'unrelated-image','kind':'image','text':'图片','direction':'incoming',
                    'sender':'Aria','sender_verified':True,'mention_me':False,
                    'image_data':base64.b64encode(buffer.getvalue()).decode()})
                store.ingest(cid,rows)
                image_result=turn('请只看引用的图片：里面写了什么，主要背景是什么颜色？不要联网。','图片')
                assert len(image_result['images'])==1 and image_result['images'][0]['quoted']
                turn('刚才被引用的图片是书籍封面，还是一张功能测试卡？不要联网。')
        finally:ai.stop()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'transport':'isolated; real models; no WeChat sends','turns':results},ensure_ascii=False,indent=2),encoding='utf-8')
    assert all(r['status']=='queued' for r in results)
    for result in (results[0],results[3]):assert re.search(r'14[:：]50|2[:：]50|(?:两|二)点五十|14点50',result['reply'])
    for result in (results[1],results[4]):
        assert 'GROUP' in result['reply'].upper() or '测试' in result['reply']
        assert result['images'][0]['status']=='ready'
    for result in (results[2],results[5]):assert '测试' in result['reply']
    print('Saved:',args.output)


if __name__=='__main__':main()
