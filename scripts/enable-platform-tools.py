"""Enable read-only platform tools for an existing workflow agent, preserving memory."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_panel.ai_store import AIStore
from wechat_panel.store import Store
from wechat_panel.platform_catalog import BUILTINS, INSTRUCTIONS


def fingerprints(db):
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'companion_%' OR name='contacts')")]
    result = {}
    for table in tables:
        columns = [r[1] for r in db.execute('PRAGMA table_info("'+table+'")')]
        # AIStore.save intentionally advances job-revision guards/cursors to
        # invalidate stale sends. These are not contact settings or memory;
        # ai_context_start_message MUST remain included in the comparison.
        if table == 'contacts':columns = [c for c in columns if c not in ('ai_revision','ai_start_message')]
        sql = 'SELECT '+','.join('"'+c+'"' for c in columns)+' FROM "'+table+'" ORDER BY rowid'
        result[table] = hashlib.sha256(json.dumps([tuple(r) for r in db.execute(sql)],ensure_ascii=False,default=str).encode()).hexdigest()
    return result


def enable(database, agent_name):
    database = Path(database).resolve()
    with sqlite3.connect(database) as db:
        if db.execute("SELECT count(*) FROM ai_runs WHERE status IN ('running','pending')").fetchone()[0]:
            raise RuntimeError('请等待当前AI任务完成后再启用平台工具')
        before = fingerprints(db)
        backup = database.parent/'backups'/('before-platform-tools-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3')
        backup.parent.mkdir(exist_ok=True)
        with sqlite3.connect(backup) as destination:db.backup(destination)
    store = Store(database)
    data = AIStore(store)
    agent = next((a for a in data.list('agents') if a['name']==agent_name and a['kind']=='workflow'), None)
    if not agent:raise ValueError('没有找到指定工作流Agent')
    original = json.loads(json.dumps(agent))
    skill = next((s for s in data.list('skills') if s['name']=='agent-reach-platforms'), None)
    skill = data.save('skills',skill['id'] if skill else None,dict(name='agent-reach-platforms',
        description='多平台搜索与阅读：YouTube、B站、GitHub、RSS、V2EX、X、小红书、Reddit、Facebook、Instagram、LinkedIn、Boss、雪球、小宇宙和网页。',
        instructions=INSTRUCTIONS,enabled=True))
    names={t[0] for t in BUILTINS}
    ids=[t['id'] for t in data.list('tools') if t.get('builtin') in names]
    agent['tool_ids']=list(dict.fromkeys(agent['tool_ids']+ids))
    agent['skill_ids']=list(dict.fromkeys(agent['skill_ids']+[skill['id']]))
    saved=data.save('agents',agent['id'],{k:v for k,v in agent.items() if k not in ('id','key_configured')})
    assert saved['prompt']==original['prompt'] and saved['companion']==original['companion']
    with sqlite3.connect(database) as db:assert fingerprints(db)==before, '记忆或联系人数据发生非预期变化'
    return dict(agent=agent_name,tools_added=len(ids),skill=skill['name'],backup=str(backup),memory_unchanged=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',default=str(ROOT/'.panel-data/panel.sqlite3'))
    parser.add_argument('--agent',default='Aria')
    args=parser.parse_args()
    print(json.dumps(enable(args.database,args.agent),ensure_ascii=False))
