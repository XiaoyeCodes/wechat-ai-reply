"""Exercise imported skills with real configured services; never start WeChat.

Only AI configuration is copied. The test Store has no contacts or chat history.
All runs are dry runs and every outbox entry point is blocked independently.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wechat_panel.store import Store
from wechat_panel.ai_store import AIStore
from wechat_panel.ai import AIService

CASES = [
    ('jin10-financial-daily','请使用 jin10-financial-daily 生成今日完整财经日报 PDF，保留原文和九项行情。'),
    ('codex-image-generation','请使用 codex-image-generation 生成一张 9:16 的蓝绿色山川与湖泊插画，图片中不需要文字。'),
    ('每日新闻日报','请使用“每日新闻日报”技能，读取真实接口并生成完整的今日 60 秒新闻竖版卡片。'),
    ('Ai热点新闻','请使用“Ai热点新闻”技能，读取 AIHot 完整前十榜单及真实热度，生成一张 9:16 热点新闻海报。'),
    ('Ai日报','请使用“Ai日报”技能，从 AI 工具集读取网站最新一期，生成暖白色的每日 AI 新闻竖版卡片。'),
]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path('.panel-data/panel.sqlite3'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--only',nargs='+',choices=[c[0] for c in CASES])
    parser.add_argument('--agent-id',help='Optional workflow Agent configuration to test')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    target=args.output/'panel.sqlite3'
    if target.resolve()==args.source.resolve() or target.exists():raise ValueError('必须使用全新隔离目录')
    store=Store(target);AIStore(store)
    with sqlite3.connect(args.source) as source,store.connect() as db:
        rows=source.execute('SELECT kind,id,data,created_at,updated_at FROM ai_resources').fetchall()
        db.execute('DELETE FROM ai_resources')
        db.executemany('INSERT INTO ai_resources VALUES(?,?,?,?,?)',rows)
    ai=AIService(store)
    candidates=[a for a in ai.data.list('agents') if a['kind']=='workflow' and (not args.agent_id or a['id']==args.agent_id)]
    chosen=max(candidates,key=lambda a:len(a.get('skill_ids',[])))
    original=ai.data.get('agents',chosen['id'],private=True)
    allowed={'clock','web_search','read_web','news_collect','financial_daily','generate_image','use_skill','read_skill_file'}
    tools={t['id']:t for t in ai.data.list('tools')}
    cases=[]
    def no_send(*a,**kw):raise AssertionError('Test attempted to queue a WeChat message')
    try:
        with patch.object(Store,'enqueue',no_send),patch.object(Store,'enqueue_ai_reply',no_send),patch.object(Store,'enqueue_ai_images',no_send):
            for name,prompt in CASES:
                if args.only and name not in args.only:continue
                print(json.dumps({'event':'starting','skill':name,'at':datetime.now().isoformat()},ensure_ascii=False),flush=True)
                skill=next(s for s in ai.data.list('skills') if s['name']==name)
                body={k:v for k,v in original.items() if k not in ('id','key_configured','secret')}
                body.update(name='隔离测试 '+name,skill_ids=[skill['id']],tool_ids=[t for t in original['tool_ids'] if tools.get(t,{}).get('builtin') in allowed])
                agent=ai.data.save('agents',None,body);aid=agent['id']
                run=ai.create_run(aid,prompt+' 本轮只是隔离测试，仅预览生成结果。',dry_run=True,session_id=str(uuid.uuid4()))
                for _ in range(6):
                    ai.execute(run['id']);run=ai.data.run(run['id'])
                    if run['status']!='waiting_approval':break
                    with store.connect() as db:
                        approvals=[dict(r) for r in db.execute("SELECT * FROM ai_approvals WHERE run_id=? AND status='pending'",(run['id'],))]
                    for approval in approvals:
                        tool=run['state']['snapshot']['tools'][approval['tool_id']]
                        if tool.get('builtin') not in {'generate_image','financial_daily'}:raise AssertionError('Unexpected effect tool')
                        ai.approve(approval['id'],True)
                detail=ai.data.detail(run['id'])
                artifacts=run['state'].get('artifacts',[])
                result={'skill':name,'run_id':run['id'],'status':run['status'],'error':run['error'],
                        'reply':run['reply'],'pdf':detail.get('pdf'),'artifacts':artifacts,
                        'trace':[{'step':t['step'],'detail':t['detail']} for t in detail['trace']]}
                result['passed']=run['status']=='completed' and bool(detail.get('pdf') if name=='jin10-financial-daily' else any(a.get('kind')=='image' for a in artifacts))
                cases.append(result)
                with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0
                (args.output/'results.json').write_text(json.dumps({'wechat_messages':0,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'event':'finished','skill':name,'status':run['status'],'passed':result['passed'],'error':run['error']},ensure_ascii=False),flush=True)
    finally:ai.stop()
    if any(not case['passed'] for case in cases):raise SystemExit(1)


if __name__=='__main__':main()
