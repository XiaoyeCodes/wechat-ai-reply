"""Persistent AI configuration, retrieval and resumable execution records."""
import base64
import json
import re
import uuid

from pydantic import ValidationError

from .ai_models import CONFIGS, AISettings
from .store import now


def protect(value):
    import win32crypt
    return base64.b64encode(win32crypt.CryptProtectData(value.encode(), 'Relay AI', None, None, None, 0)).decode()


def unprotect(value):
    if not value: return ''
    import win32crypt
    return win32crypt.CryptUnprotectData(base64.b64decode(value), None, None, None, 0)[1].decode()


def tokens(text):
    words = re.findall(r'[a-zA-Z0-9_]{2,}',text.lower())
    for part in re.findall(r'[\u3400-\u9fff]+',text):
        words.extend(part[i:i+2] for i in range(len(part)-1))
        if len(part)==1: words.append(part)
    return list(dict.fromkeys(words))


class AIStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ai_resources (
                    kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE VIRTUAL TABLE IF NOT EXISTS ai_chunks USING fts5(
                    knowledge_id UNINDEXED, title UNINDEXED, content UNINDEXED, terms);
                CREATE TABLE IF NOT EXISTS ai_runs (
                    id TEXT PRIMARY KEY, contact_id TEXT, agent_id TEXT NOT NULL,
                    trigger_id INTEGER, revision INTEGER, mode TEXT NOT NULL, dry_run INTEGER NOT NULL,
                    input TEXT NOT NULL, status TEXT NOT NULL, state TEXT NOT NULL,
                    reply TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(contact_id,trigger_id));
                CREATE TABLE IF NOT EXISTS ai_trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES ai_runs(id),
                    step TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_approvals (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ai_runs(id),
                    call_id TEXT NOT NULL, tool_id TEXT NOT NULL, arguments TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id,call_id));
                CREATE TABLE IF NOT EXISTS ai_inbox (
                    contact_id TEXT PRIMARY KEY, trigger_id INTEGER NOT NULL, due REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS hermes_confirmations (
                    id TEXT PRIMARY KEY REFERENCES ai_approvals(id), run_id TEXT NOT NULL,
                    contact_id TEXT NOT NULL, code TEXT NOT NULL UNIQUE, expires REAL NOT NULL,
                    notice_id INTEGER NOT NULL DEFAULT 0, response_id INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending');
            ''')
            db.execute("INSERT OR IGNORE INTO settings VALUES('ai_settings',?)",(json.dumps(AISettings().model_dump()),))
            # Start at the current event: enabling AI never replays old messages.
            db.execute("INSERT OR IGNORE INTO settings VALUES('ai_cursor',?)",(str(store.latest_event()),))
            columns={r['name'] for r in db.execute('PRAGMA table_info(ai_runs)')}
            for name,definition in {'schedule_id':"TEXT NOT NULL DEFAULT ''",'baseline_id':'INTEGER NOT NULL DEFAULT 0',
                                    'resume_message_id':'INTEGER NOT NULL DEFAULT 0'}.items():
                if name not in columns:db.execute(f'ALTER TABLE ai_runs ADD COLUMN {name} {definition}')
            columns={r['name'] for r in db.execute('PRAGMA table_info(hermes_confirmations)')}
            for name,definition in {'panel_only':'INTEGER NOT NULL DEFAULT 0',
                                    'delivery_retries':'INTEGER NOT NULL DEFAULT 0',
                                    'retry_after':'REAL NOT NULL DEFAULT 0',
                                    'summary':"TEXT NOT NULL DEFAULT ''",
                                    'scope':"TEXT NOT NULL DEFAULT 'operation'"}.items():
                if name not in columns:db.execute(f'ALTER TABLE hermes_confirmations ADD COLUMN {name} {definition}')
            if 'decision_source' not in {r['name'] for r in db.execute('PRAGMA table_info(ai_approvals)')}:
                db.execute("ALTER TABLE ai_approvals ADD COLUMN decision_source TEXT NOT NULL DEFAULT 'user'")
        builtins = [
            ('current_time','clock','读取当前日期时间',{}),
            ('calculate','calculator','计算四则运算表达式',{'expression':{'type':'string','maxLength':300}}),
            ('search_knowledge','knowledge_search','检索当前 Agent 已绑定知识库',{'query':{'type':'string','maxLength':500}})]
        for name,builtin,description,properties in builtins:
            if not any(t['name']==name for t in self.list('tools')):
                self.save('tools',None,dict(name=name,kind='builtin',builtin=builtin,description=description,
                    approval='auto',parameters={'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}))
        from .companion import BUILTINS as COMPANION_TOOLS
        from .skills import BUILTINS as SKILL_TOOLS
        BUILTINS=COMPANION_TOOLS+SKILL_TOOLS
        for name,description,properties,approval in BUILTINS:
            if name in ('news_collect','generate_image','financial_daily'):
                parameters={'type':'object','properties':properties,'required':['prompt'] if name=='generate_image' else [],'additionalProperties':False}
                existing=next((t for t in self.list('tools') if t.get('builtin')==name),None)
                if not existing:
                    self.save('tools',None,dict(name=name,kind='builtin',builtin=name,description=description,
                        approval=approval,parameters=parameters))
                elif existing['parameters']!=parameters or existing['description']!=description:
                    # Preserve enabled/approval/ID and existing Agent bindings.
                    self.save('tools',existing['id'],{**{k:v for k,v in existing.items() if k not in ('id','key_configured')},
                        'description':description,'parameters':parameters})
                continue
            if not any(t.get('builtin')==name for t in self.list('tools')):
                self.save('tools',None,dict(name=name,kind='builtin',builtin=name,description=description,
                    approval=approval,parameters={'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}))
        from .image_generation import install_defaults
        install_defaults(self)
        for name,profile in [('高德地图 MCP','amap'),('TreeMind 树图（待接入）','treemind')]:
            if not any(c['profile']==profile for c in self.list('mcp')):
                self.save('mcp',None,dict(name=name,profile=profile,auth='query' if profile=='amap' else 'bearer'))
        if not any(t['name']=='treemind_mindmap' for t in self.list('tools')):
            self.save('tools',None,dict(name='treemind_mindmap',kind='http',capability='mindmap',enabled=False,
                description='TreeMind 树图思维导图：仅在用户明确要求生成或修改导图时调用。待按官方文档填写 API 地址、认证与参数。',
                approval='confirm',parameters={'type':'object','properties':{},'additionalProperties':False}))

    @staticmethod
    def public(resource):
        result = {k:v for k,v in resource.items() if k not in ('secret','api_key')}
        if 'steps' in result:
            result['style_id'] = ''
            from .companion_models import Companion
            result['companion']=Companion.model_validate(result.get('companion',{})).model_dump()
            result.setdefault('knowledge_mode','on_demand')
            result.setdefault('web_policy','auto')
            result.setdefault('source_policy','auto')
            result.setdefault('skill_ids',[])
        result['key_configured'] = bool(resource.get('secret'))
        return result

    def list(self, kind):
        if kind not in CONFIGS: raise KeyError('配置类型不存在')
        with self.store.connect() as db:
            return [self.public(json.loads(r['data'])) | {'id':r['id']} for r in
                    db.execute('SELECT * FROM ai_resources WHERE kind=? ORDER BY created_at',(kind,))]

    def get(self, kind, rid, private=False):
        if kind not in CONFIGS:raise KeyError('配置类型不存在')
        with self.store.connect() as db:
            row = db.execute('SELECT data FROM ai_resources WHERE kind=? AND id=?',(kind,rid)).fetchone()
        if not row: raise KeyError('AI 配置不存在')
        value=json.loads(row['data']) | {'id':rid}
        if kind == 'agents':value['style_id'] = ''
        return value if private else self.public(value)

    def save(self, kind, rid, data):
        if kind not in CONFIGS: raise KeyError('配置类型不存在')
        old=self.get(kind,rid,private=True) if rid else {}
        if kind == 'agents' and old.get('vibe_engine') == 'sparkflow' and 'vibe_engine' not in data:
            data = {**data, 'vibe_engine': 'sparkflow'}
        try: value=CONFIGS[kind].model_validate(data).model_dump()
        except ValidationError as exc:
            # Validation errors can contain the submitted secret; only return messages.
            raise ValueError('；'.join(e['msg'].replace('Value error, ','') for e in exc.errors(include_input=False))) from None
        key=value.pop('api_key',None)
        clear=value.pop('clear_secret',False)
        if kind in ('providers','tools','agents','services','mcp'):
            value['secret']=protect(key) if key else '' if clear else old.get('secret','')
        if kind=='tools' and value['kind']=='mcp':
            connection=self.get('mcp',value['connection_id'])
            value['capability']={'amap':'amap','treemind':'mindmap'}.get(connection['profile'],'general')
        if kind=='agents':
            for resource, ids in [('providers',[value['provider_id']] if value['provider_id'] else []),
                    ('knowledge',value['knowledge_ids']),('tools',value['tool_ids']),('skills',value['skill_ids'])]:
                for item in ids: self.get(resource,item)
            if value['kind']=='hermes' and value['hermes_contact_id']:
                self.store.contact(value['hermes_contact_id'])
            c=value['companion']
            if c['voice']['preset']!=old.get('companion',{}).get('voice',{}).get('preset'):
                from .voice_synthesis import preset
                preset(c['voice']['preset'],store=self.store)
            for sid,category in [(c['search_service_id'],'search'),(c['memory']['service_id'],'memory'),
                (c['media']['tts_service_id'],'tts'),(c['media']['image_service_id'],'image'),(c['safety']['observability_service_id'],'observability')]:
                if sid and self.get('services',sid)['category']!=category:raise ValueError('绑定的服务类别不匹配')
            if c['media']['vision_provider_id']:self.get('providers',c['media']['vision_provider_id'])
        if kind=='services' and value['embedding_provider_id']:self.get('providers',value['embedding_provider_id'])
        if kind in ('tools','skills') and any(t['name']==value['name'] and t['id']!=rid for t in self.list(kind)):
            raise ValueError('工具名称已存在')
        rid=rid or str(uuid.uuid4()); stamp=now()
        with self.store.connect() as db:
            db.execute('INSERT INTO ai_resources VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at',
                       (kind,rid,json.dumps(value,ensure_ascii=False),stamp,stamp))
            if kind=='knowledge':
                db.execute('DELETE FROM ai_chunks WHERE knowledge_id=?',(rid,))
                for doc in value['documents']:
                    for start in range(0,len(doc['content']),450):
                        text=doc['content'][start:start+550]
                        db.execute('INSERT INTO ai_chunks VALUES(?,?,?,?)',(rid,doc['title'],text,' '.join(tokens(text))))
            self.store.event(db,'ai.config',kind=kind,resource_id=rid)
        self.cancel_all('AI 配置已更改')
        return self.get(kind,rid)

    def delete(self,kind,rid):
        self.get(kind,rid)
        if kind=='mcp' and any(t.get('connection_id')==rid for t in self.list('tools')):
            raise ValueError('该 MCP 连接仍有导入的工具，请先解除 Agent 绑定并删除这些工具')
        if kind=='providers' and any(s['embedding_provider_id']==rid for s in self.list('services')):
            raise ValueError('该模型正在被记忆服务用于 Embedding，请先修改服务连接')
        if kind=='agents' and any(c.get('ai_agent_id')==rid for c in self.store.contacts(all_accounts=True)):
            raise ValueError('该 Agent 已绑定联系人，请先切换联系人 Agent')
        for agent in self.list('agents'):
            linked={'providers':[agent['provider_id']],
                    'knowledge':agent['knowledge_ids'],'tools':agent['tool_ids'],'skills':agent.get('skill_ids',[])}
            c=agent['companion']
            linked['services']=[c['search_service_id'],c['memory']['service_id'],c['media']['tts_service_id'],c['media']['image_service_id'],c['safety']['observability_service_id']]
            linked['providers'].append(c['media']['vision_provider_id'])
            if kind in linked and rid in linked[kind]: raise ValueError('该配置正在被 Agent 使用，请先修改 Agent')
        self.cancel_all('AI 配置已删除')
        with self.store.connect() as db:
            db.execute('DELETE FROM ai_resources WHERE kind=? AND id=?',(kind,rid))
            if kind=='knowledge':db.execute('DELETE FROM ai_chunks WHERE knowledge_id=?',(rid,))
            self.store.event(db,'ai.config',kind=kind,resource_id=rid)

    def settings(self, value=None):
        if value is not None:
            value=AISettings.model_validate(value).model_dump()
            self.cancel_all('AI 设置已更改')
            with self.store.connect() as db:
                db.execute("UPDATE settings SET value=? WHERE key='ai_settings'",(json.dumps(value),))
                self.store.event(db,'ai.config',kind='settings')
        with self.store.connect() as db:return json.loads(db.execute("SELECT value FROM settings WHERE key='ai_settings'").fetchone()[0])

    def search(self, ids, query, limit=5):
        terms=tokens(query)[:40]
        if not ids or not terms:return []
        expression=' OR '.join('"'+t.replace('"','')+'"' for t in terms)
        with self.store.connect() as db:
            rows=db.execute('SELECT knowledge_id,title,content,bm25(ai_chunks) score FROM ai_chunks WHERE ai_chunks MATCH ? AND knowledge_id IN ('+
                ','.join('?' for _ in ids)+') ORDER BY score LIMIT ?', (expression,*ids,limit)).fetchall()
            return [dict(row) for row in rows]

    def trace(self,rid,step,detail):
        encoded=json.dumps(detail,ensure_ascii=False)
        if len(encoded)>20000:encoded=json.dumps({'preview':encoded[:19000],'truncated':True},ensure_ascii=False)
        with self.store.connect() as db:
            db.execute('INSERT INTO ai_trace(run_id,step,detail,created_at) VALUES(?,?,?,?)',
                       (rid,step,protect(encoded),now()))
            self.store.event(db,'ai.run',run_id=rid)

    def run(self,rid):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM ai_runs WHERE id=?',(rid,)).fetchone()
            if not row:raise KeyError('运行记录不存在')
            value=dict(row);value['state']=self.decode_state(value['state'])
            return value

    def runs(self,contact_id=None):
        with self.store.connect() as db:
            rows=db.execute('SELECT id,contact_id,agent_id,trigger_id,mode,dry_run,input,status,reply,error,created_at,updated_at,schedule_id FROM ai_runs '+
                ('WHERE contact_id=? ' if contact_id else '')+'ORDER BY created_at DESC LIMIT 50',
                (contact_id,) if contact_id else ()).fetchall()
            result=[dict(row) for row in rows]
        from .conversation_queue import position
        for run in result:
            if run['contact_id'] and run['status']=='pending':run['queue_position']=position(self.store,run)
        return result

    def detail(self,rid):
        result=self.run(rid);state=result.pop('state')
        if state.get('conversation_queue') and result['status']=='pending':
            from .conversation_queue import position
            result['queue_position']=position(self.store,result)
        result['pdf'] = state.get('pdf')
        result['approval_scope']=state['snapshot']['agent'].get('hermes_approval_scope','operation')
        with self.store.connect() as db:
            result['trace']=[dict(row)|{'detail':json.loads(row['detail'] if row['detail'].startswith(('{','[')) else unprotect(row['detail']))} for row in db.execute('SELECT * FROM ai_trace WHERE run_id=? ORDER BY id',(rid,))]
            result['approvals']=[dict(row)|{'arguments':json.loads(row['arguments'])} for row in db.execute('SELECT a.*,c.summary FROM ai_approvals a LEFT JOIN hermes_confirmations c ON c.id=a.id WHERE a.run_id=? ORDER BY a.created_at',(rid,))]
        return result

    def update_run(self,rid,**values):
        if not set(values)<={'status','state','reply','error'}:raise ValueError('无效运行字段')
        if 'state' in values:values['state']=self.encode_state(values['state'])
        values['updated_at']=now()
        with self.store.connect() as db:
            db.execute('UPDATE ai_runs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),rid))
            self.store.event(db,'ai.run',run_id=rid)

    def cancel_contact(self,cid,reason='已人工接管',discard_unseen=True):
        with self.store.connect() as db:
            db.execute('UPDATE contacts SET ai_revision=ai_revision+1 WHERE id=?',(cid,))
            if discard_unseen:
                db.execute('UPDATE contacts SET ai_start_message=COALESCE((SELECT MAX(id) FROM messages WHERE contact_id=?),0) WHERE id=?',(cid,cid))
            db.execute("DELETE FROM ai_inbox WHERE contact_id=?",(cid,))
            db.execute("UPDATE ai_runs SET status='cancelled',error=?,updated_at=? WHERE contact_id=? AND status IN ('pending','running','waiting_approval','draft','queued')"+
                       (" AND mode!='agent_delivery'" if not discard_unseen else ''),(reason,now(),cid))
            db.execute("UPDATE ai_approvals SET status='cancelled' WHERE run_id IN (SELECT id FROM ai_runs WHERE contact_id=? AND status='cancelled')",(cid,))
            db.execute("UPDATE messages SET status='cancelled',error=? WHERE contact_id=? AND origin IN ('ai','approval','ai_progress') AND status='queued'"+
                       (" AND (ai_run_id IS NULL OR ai_run_id NOT IN (SELECT id FROM ai_runs WHERE mode='agent_delivery'))" if not discard_unseen else ''),(reason,cid))
            db.execute("UPDATE hermes_confirmations SET status='cancelled' WHERE contact_id=? AND status='pending'",(cid,))
            self.store.event(db,'ai.run',cid,detail=reason)

    def cancel_run(self,rid,reason='已取消当前任务'):
        with self.store.connect() as db:
            db.execute("UPDATE ai_runs SET status='cancelled',error=?,updated_at=? WHERE id=? AND status IN ('pending','running','waiting_approval','draft','queued')",(reason,now(),rid))
            db.execute("UPDATE ai_approvals SET status='cancelled' WHERE run_id=? AND status='pending'",(rid,))
            db.execute("UPDATE hermes_confirmations SET status='cancelled' WHERE run_id=? AND status='pending'",(rid,))
            db.execute("UPDATE messages SET status='cancelled',error=? WHERE ai_run_id=? AND origin IN ('ai','approval','ai_progress') AND status='queued'",(reason,rid))
            self.store.event(db,'ai.run',run_id=rid,detail=reason)

    def cancel_all(self,reason):
        for contact in self.store.contacts(all_accounts=True):self.cancel_contact(contact['id'],reason)
        with self.store.connect() as db:
            db.execute("UPDATE ai_runs SET status='cancelled',error=?,updated_at=? WHERE dry_run=1 AND status IN ('pending','running','waiting_approval','draft')",(reason,now()))
            db.execute("UPDATE ai_runs SET status='cancelled',error=?,updated_at=? WHERE mode='agent_task' AND status IN ('pending','running','waiting_approval','draft')",(reason,now()))
            db.execute("UPDATE ai_approvals SET status='cancelled' WHERE status='pending' AND run_id IN (SELECT id FROM ai_runs WHERE status='cancelled')")

    def valid(self,run):
        if run['status'] in ('cancelled','failed'):return False
        if run.get('state',{}).get('agent_schedule'):
            from .agent_schedules import task_valid
            return task_valid(self.store,run)
        if run['dry_run']:return run['status'] not in ('cancelled','failed')
        if run.get('schedule_id'):
            from .companion_scheduler import schedule_valid
            return schedule_valid(self.store,run)
        try:contact=self.store.contact(run['contact_id'])
        except KeyError:return False
        if not self.store.account_matches(contact):return False
        settings=self.settings()
        with self.store.connect() as db:
            from .conversation_turn import latest_id
            latest=latest_id(db,contact['id'],run['state']['snapshot']['agent'],group_sender=run['state'].get('group_sender'))
            from .hermes_confirmations import pending_reply
            if run['state'].get('conversation_queue'):
                from .conversation_queue import current as queued_current
                current=queued_current(db,contact,run)
            else:current=(latest==(run.get('resume_message_id') or run['trigger_id']) or pending_reply(db,run,latest))
        return (not self.store.paused() and settings['enabled'] and contact['listening']
                and contact['mode']==run['mode'] and contact['ai_agent_id']==run['agent_id']
                and contact['ai_revision']==run['revision'] and current)

    @staticmethod
    def encode_state(value):return protect(json.dumps(value,ensure_ascii=False))

    @staticmethod
    def decode_state(value):return json.loads(value if value.startswith('{') else unprotect(value))
