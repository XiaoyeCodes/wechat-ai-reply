"""Agent-wide scheduled generation and durable, individually guarded delivery.

Each occurrence has one anonymous generation run. Delivery runs contain the
same result and artifacts, but never execute a model or inherit chat memory.
"""
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Literal
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .ai_store import AIStore, protect, unprotect
from .store import now
from fastapi import HTTPException


ACTIVE = ('claimed', 'pending', 'running', 'waiting_approval', 'queued')
PRIVATE_TOOLS = {'memory_search', 'remember', 'forget', 'set_reminder'}


class AgentSchedule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=8000)
    kind: Literal['once', 'cron'] = 'once'
    at: str = Field(default='', max_length=60)
    cron: str = Field(default='', max_length=80)
    timezone: str = Field(default='Asia/Shanghai', max_length=100)
    enabled: bool = False

    @field_validator('agent_id', 'title', 'instruction')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():raise ValueError('Agent、标题和任务内容不能为空')
        return value.strip()

    @model_validator(mode='after')
    def valid(self):
        try:zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):raise ValueError('请选择有效的 IANA 时区') from None
        if self.kind == 'once':
            try:
                stamp = datetime.fromisoformat(self.at)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=zone)
                    # Reject local times that vanish during a DST transition.
                    if stamp.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != stamp.replace(tzinfo=None):
                        raise ValueError()
                self.at = stamp.isoformat()
            except ValueError:raise ValueError('请填写有效的一次性执行时间') from None
        else:
            from croniter import croniter
            if len(self.cron.split()) != 5 or not croniter.is_valid(self.cron):
                raise ValueError('Cron 必须包含有效的分、时、日、月、星期五段')
            try:croniter(self.cron, datetime.now(zone)).get_next(datetime)
            except (ValueError, KeyError):raise ValueError('Cron 在可支持的范围内没有下次执行时间') from None
        return self


def metadata(run):
    return run.get('state', {}).get('agent_schedule')


def task_valid(store, run):
    """Called by both the AI worker and the serial send worker before effects."""
    task = metadata(run)
    if not task:return False
    try:
        with store.connect() as db:
            schedule = db.execute('SELECT * FROM agent_schedules WHERE id=?', (task['schedule_id'],)).fetchone()
            occurrence = db.execute('SELECT * FROM agent_schedule_occurrences WHERE id=?', (task['occurrence_id'],)).fetchone()
            resource = db.execute("SELECT data FROM ai_resources WHERE kind='agents' AND id=?", (run['agent_id'],)).fetchone()
            settings = json.loads(db.execute("SELECT value FROM settings WHERE key='ai_settings'").fetchone()[0])
        if not schedule or not occurrence or not resource:return False
        value = json.loads(unprotect(schedule['data']))
        if (store.paused() or not settings['enabled'] or not value['enabled']
                or not json.loads(resource['data'])['enabled'] or schedule['revision'] != task['revision']
                or value['agent_id'] != run['agent_id'] or occurrence['status'] not in ACTIVE):return False
        if datetime.now(timezone.utc) >= datetime.fromisoformat(task['expires_at']):return False
        if task['role'] == 'generation':return occurrence['run_id'] == run['id']
        contact = store.contact(run['contact_id'])
        with store.connect() as db:
            recipient = db.execute('SELECT * FROM agent_schedule_recipients WHERE occurrence_id=? AND contact_id=?',
                                   (task['occurrence_id'], contact['id'])).fetchone()
        return bool(recipient and recipient['run_id'] == run['id'] and recipient['status'] in ('claimed', 'queued')
                    and contact['listening'] and contact['mode'] == 'ai_auto'
                    and contact['ai_agent_id'] == run['agent_id'] and contact['ai_delivery_revision'] == run['revision'])
    except (KeyError, ValueError, TypeError):return False


class AgentScheduler:
    def __init__(self, ai):
        self.ai = ai
        self.store = ai.store
        self.last_tick = 0
        with self.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS agent_schedules (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    next_at TEXT NOT NULL, last_status TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_schedule_occurrences (
                    id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, scheduled_at TEXT NOT NULL,
                    source TEXT NOT NULL, run_id TEXT, status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', targets TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(schedule_id, scheduled_at));
                CREATE TABLE IF NOT EXISTS agent_schedule_recipients (
                    occurrence_id TEXT NOT NULL, contact_id TEXT NOT NULL, lookup TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(occurrence_id, contact_id));
                CREATE INDEX IF NOT EXISTS agent_schedule_history
                    ON agent_schedule_occurrences(schedule_id, created_at);
            ''')
            if 'targets' not in {r['name'] for r in db.execute('PRAGMA table_info(agent_schedule_occurrences)')}:
                db.execute("ALTER TABLE agent_schedule_occurrences ADD COLUMN targets TEXT NOT NULL DEFAULT '[]'")

    @staticmethod
    def next_time(value, after=None):
        if value['kind'] == 'once':return datetime.fromisoformat(value['at']).astimezone(timezone.utc).isoformat()
        from croniter import croniter
        return croniter(value['cron'], (after or datetime.now(timezone.utc)).astimezone(ZoneInfo(value['timezone']))).get_next(datetime).astimezone(timezone.utc).isoformat()

    def eligible(self, agent_id):
        self.ai.data.get('agents', agent_id)
        return [c for c in self.store.contacts() if c.get('chat_type','direct')=='direct'
                and c['listening'] and c['mode'] == 'ai_auto' and c['ai_agent_id'] == agent_id]

    def _public(self, row):
        value = json.loads(unprotect(row['data']))
        try:count = len(self.eligible(value['agent_id']))
        except KeyError:count = 0
        return value | {k:row[k] for k in ('id', 'next_at', 'last_status', 'created_at', 'updated_at')} | {'eligible_count':count}

    def list(self):
        with self.store.connect() as db:rows = db.execute('SELECT * FROM agent_schedules ORDER BY created_at DESC').fetchall()
        return [self._public(row) for row in rows]

    def get(self, sid):
        with self.store.connect() as db:row = db.execute('SELECT * FROM agent_schedules WHERE id=?', (sid,)).fetchone()
        if not row:raise KeyError('Agent 定时任务不存在')
        return self._public(row)

    def save(self, data, sid=None):
        with self.ai.lock:
            old = self.get(sid) if sid else {}
            fields = AgentSchedule.model_fields
            try:value = AgentSchedule.model_validate({k:v for k,v in old.items() if k in fields} | data).model_dump()
            except ValidationError as exc:
                raise ValueError('；'.join(e['msg'].replace('Value error, ', '') for e in exc.errors(include_input=False))) from None
            agent = self.ai.data.get('agents', value['agent_id'])
            # Desktop agents require a specific human's authority. They cannot
            # borrow that authority to publish results to unrelated contacts.
            if agent['kind'] != 'workflow':raise ValueError('当前仅工作流 Agent 支持公共定时推送；请先选择工作流 Agent')
            if sid:self.cancel(sid, '定时任务已修改，旧执行和投递已取消')
            stamp = now();sid = sid or str(uuid.uuid4())
            next_at = self.next_time(value) if value['enabled'] else ''
            with self.store.connect() as db:
                db.execute('''INSERT INTO agent_schedules(id,data,next_at,last_status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=agent_schedules.revision+1,
                    next_at=excluded.next_at,last_status=excluded.last_status,updated_at=excluded.updated_at''',
                    (sid, protect(json.dumps(value, ensure_ascii=False)), next_at, '等待执行' if value['enabled'] else '已停用', stamp, stamp))
                self.store.event(db, 'ai.agent_schedule', schedule_id=sid)
            return self.get(sid)

    def cancel(self, sid, reason):
        with self.store.connect() as db:
            occurrences = db.execute("SELECT id,run_id FROM agent_schedule_occurrences WHERE schedule_id=? AND status IN ('claimed','pending','running','waiting_approval','queued')", (sid,)).fetchall()
            for occurrence in occurrences:
                ids = [r['run_id'] for r in db.execute('SELECT run_id FROM agent_schedule_recipients WHERE occurrence_id=?', (occurrence['id'],))]
                if occurrence['run_id']:ids.append(occurrence['run_id'])
                for rid in ids:
                    db.execute("UPDATE ai_runs SET status='cancelled',error=?,updated_at=? WHERE id=? AND status IN ('pending','running','waiting_approval','queued','draft')", (reason, now(), rid))
                    db.execute("UPDATE messages SET status='cancelled',error=? WHERE ai_run_id=? AND status='queued'", (reason, rid))
                    db.execute("UPDATE ai_approvals SET status='cancelled' WHERE run_id=? AND status='pending'", (rid,))
                db.execute("UPDATE agent_schedule_recipients SET status='cancelled',error=?,updated_at=? WHERE occurrence_id=? AND status IN ('claimed','queued')", (reason, now(), occurrence['id']))
                db.execute("UPDATE agent_schedule_occurrences SET status='cancelled',error=?,updated_at=? WHERE id=?", (reason, now(), occurrence['id']))

    def delete(self, sid):
        with self.ai.lock:
            self.get(sid)
            self.cancel(sid, '定时任务已删除')
            with self.store.connect() as db:
                db.execute('DELETE FROM agent_schedules WHERE id=?', (sid,))
                self.store.event(db, 'ai.agent_schedule', schedule_id=sid)

    def recover(self):
        """Never resume prior generation, approvals, or unconfirmed fanout."""
        with self.store.connect() as db:rows = db.execute("SELECT DISTINCT schedule_id FROM agent_schedule_occurrences WHERE status IN ('claimed','pending','running','waiting_approval','queued')").fetchall()
        for row in rows:
            with self.store.connect() as db:
                delivery_ids=[r[0] for r in db.execute('''SELECT p.run_id FROM agent_schedule_recipients p
                    JOIN agent_schedule_occurrences o ON o.id=p.occurrence_id WHERE o.schedule_id=?''', (row['schedule_id'],))]
                for rid in delivery_ids:
                    states=[r[0] for r in db.execute('SELECT status FROM messages WHERE ai_run_id=?', (rid,))]
                    if 'uncertain' in states or 'sending' in states:
                        db.execute("UPDATE ai_runs SET status='uncertain',error='服务重启，发送结果需要核对',updated_at=? WHERE id=?", (now(), rid))
            # Preserve uncertain delivery evidence; queued sends are cancelled
            # by Store.recover and must never be reconstructed from the result.
            self._sync(row['schedule_id'])
            self.cancel(row['schedule_id'], '服务重启，旧任务已停止；不自动重发，请核对投递记录')

    def run_now(self, sid):
        with self.ai.lock:
            value = self.get(sid)
            if not value['enabled']:raise ValueError('请先启用此定时任务，再立即执行')
            if self.store.paused() or not self.ai.data.settings()['enabled']:raise ValueError('请先恢复运行并开启 AI 总开关')
            if not self.eligible(value['agent_id']):raise ValueError('没有符合条件的联系人：需监听此 Agent 且开启 AI 自动回复')
            self.ai.snapshot(value['agent_id'])
            with self.store.connect() as db:
                active = db.execute("SELECT 1 FROM agent_schedule_occurrences WHERE schedule_id=? AND status IN ('claimed','pending','running','waiting_approval','queued')", (sid,)).fetchone()
            if active:raise HTTPException(409, '该任务正在执行或投递，请先处理当前运行')
            oid = self._claim(value, 'manual:' + str(uuid.uuid4()), 'manual')
            return self.detail(oid)

    def _claim(self, value, scheduled_at, source, stamp=None):
        stamp = stamp or datetime.now(timezone.utc)
        oid = str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            inserted = db.execute('''INSERT OR IGNORE INTO agent_schedule_occurrences
                (id,schedule_id,scheduled_at,source,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)''',
                (oid, value['id'], scheduled_at, source, 'claimed', now(), now())).rowcount
            if not inserted:return None
            row = db.execute('SELECT revision FROM agent_schedules WHERE id=?', (value['id'],)).fetchone()
            if source == 'scheduled':
                next_at = self.next_time(value, stamp) if value['kind'] == 'cron' else ''
                db.execute('UPDATE agent_schedules SET next_at=? WHERE id=?', (next_at, value['id']))
        try:
            if source == 'scheduled' and (stamp - datetime.fromisoformat(scheduled_at)).total_seconds() > 300:
                raise ValueError('跳过过期日程，避免重启后集中补发')
            if self.store.paused() or not self.ai.data.settings()['enabled']:raise ValueError('运行或 AI 已暂停，本次已跳过')
            targets = self.eligible(value['agent_id'])
            if not targets:raise ValueError('没有符合条件的监听且 AI 自动回复联系人，本次已跳过')
            with self.store.connect() as db:
                active = db.execute("SELECT 1 FROM agent_schedule_occurrences WHERE schedule_id=? AND id!=? AND status IN ('claimed','pending','running','waiting_approval','queued')", (value['id'], oid)).fetchone()
            if active:raise ValueError('上一次任务尚未结束，本次已跳过')
            with self.store.connect() as db:
                db.execute('UPDATE agent_schedule_occurrences SET targets=? WHERE id=?',
                    (protect(json.dumps([{'id':c['id'], 'revision':c['ai_delivery_revision']} for c in targets])), oid))
            task = {'schedule_id':value['id'], 'occurrence_id':oid, 'revision':row['revision'], 'role':'generation',
                    'timezone':value['timezone'], 'expires_at':(stamp + timedelta(hours=1)).isoformat()}
            instruction = ('这是预先配置的 Agent 公共定时任务。本次在独立上下文执行，结果将共享给符合条件的联系人。'
                '不引用任何联系人的身份、资料、历史和记忆。不要声称内容已经送达，投递由系统在生成完成后核对。\n'
                '任务：' + value['instruction'])
            run = self.ai.create_run(value['agent_id'], instruction, agent_schedule=task)
            with self.store.connect() as db:
                db.execute("UPDATE agent_schedule_occurrences SET run_id=?,status='pending',updated_at=? WHERE id=?", (run['id'], now(), oid))
                db.execute("UPDATE agent_schedules SET last_status='pending',updated_at=? WHERE id=?", (now(), value['id']))
            self.ai.data.trace(run['id'], 'Agent 定时执行', {'schedule_id':value['id'], 'eligible_count':len(targets), 'source':source})
        except (ValueError, KeyError) as exc:
            with self.store.connect() as db:
                db.execute("UPDATE agent_schedule_occurrences SET status='skipped',error=?,updated_at=? WHERE id=?", (str(exc)[:1000], now(), oid))
                db.execute('UPDATE agent_schedules SET last_status=?,updated_at=? WHERE id=?', (str(exc)[:1000], now(), value['id']))
        return oid

    def complete(self, rid, reply, image_ids):
        with self.ai.lock:
            run = self.ai._guard(rid)
            if not run:return
            reply = reply.strip() or ('[图片]' if image_ids else '')
            if not reply or len(reply) > 16000:raise ValueError('定时任务结果须为 1～16000 字')
            attempts=run['state'].get('web_evidence', [])
            if any(e.get('status') in ('error','unavailable','disabled') for e in attempts) and not any(e.get('status')=='ok' for e in attempts):
                raise ValueError('任务所需联网工具未能成功取得数据，未推送；请检查运行日志和工具权限')
            task = metadata(run);oid = task['occurrence_id']
            self.ai.data.update_run(rid, status='completed', reply=reply)
            # Commit one recipient claim and its inert run before enqueuing.
            # A crash in between is recoverable as stopped, never replayed.
            with self.store.connect() as db:
                record=db.execute('SELECT targets FROM agent_schedule_occurrences WHERE id=?', (oid,)).fetchone()
                targets={c['id']:c['revision'] for c in json.loads(unprotect(record['targets']))}
                db.execute("UPDATE agent_schedule_occurrences SET status='queued',updated_at=? WHERE id=?", (now(), oid))
            for contact in self.eligible(run['agent_id']):
                if targets.get(contact['id']) != contact['ai_delivery_revision']:continue
                if not task_valid(self.store, run):break
                delivery_id = str(uuid.uuid4());stamp = now()
                state = {'snapshot':run['state']['snapshot'], 'scope':run['state']['scope'],
                    'context':[], 'artifacts':run['state'].get('artifacts', []),
                    'agent_schedule':task | {'role':'delivery'}, 'session_id':run['state']['session_id']}
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if db.execute('SELECT 1 FROM agent_schedule_recipients WHERE occurrence_id=? AND contact_id=?', (oid, contact['id'])).fetchone():continue
                    db.execute('''INSERT INTO ai_runs(id,contact_id,agent_id,revision,mode,dry_run,input,status,state,reply,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (delivery_id, contact['id'], run['agent_id'], contact['ai_delivery_revision'], 'agent_delivery', 0,
                        'Agent 定时任务共享结果', 'queued', self.ai.data.encode_state(state), reply, stamp, stamp))
                    db.execute('INSERT INTO agent_schedule_recipients VALUES(?,?,?,?,?,?,?,?)',
                        (oid, contact['id'], contact['lookup'], delivery_id, 'claimed', '', stamp, stamp))
                if not task_valid(self.store, self.ai.data.run(delivery_id)):
                    self.ai.data.update_run(delivery_id, status='cancelled', error='联系人或任务资格已变化')
                    continue
                try:
                    if image_ids:self.store.enqueue_ai_images(contact['id'], delivery_id, image_ids)
                    else:self.store.enqueue_ai_reply(contact['id'], reply, delivery_id)
                    with self.store.connect() as db:
                        db.execute("UPDATE agent_schedule_recipients SET status='queued',updated_at=? WHERE run_id=?", (now(), delivery_id))
                except (ValueError, KeyError):
                    self.ai.data.update_run(delivery_id, status='failed', error='投递入队失败，请核对联系人配置')
            self._sync(task['schedule_id'])

    def _sync(self, sid=None):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM agent_schedule_occurrences WHERE status IN ('claimed','pending','running','waiting_approval','queued','uncertain','partial')" + (' AND schedule_id=?' if sid else ''), (sid,) if sid else ()).fetchall()
        for row in rows:
            if not row['run_id']:continue
            run = self.ai.data.run(row['run_id'])
            if run['status'] in ('pending','running','waiting_approval') and not task_valid(self.store,run):
                self.ai.data.update_run(run['id'],status='cancelled',error='任务已过期、暂停或配置变化，旧执行已取消')
                run=self.ai.data.run(run['id'])
            with self.store.connect() as db:
                recipients = db.execute('SELECT p.*,r.status run_status,r.error run_error FROM agent_schedule_recipients p LEFT JOIN ai_runs r ON r.id=p.run_id WHERE p.occurrence_id=?', (row['id'],)).fetchall()
            for recipient in recipients:
                if recipient['run_status']=='queued' and not task_valid(self.store,self.ai.data.run(recipient['run_id'])):
                    self.ai.data.update_run(recipient['run_id'],status='cancelled',error='任务过期或收件人资格变化，旧投递已取消')
                    with self.store.connect() as db:
                        db.execute("UPDATE messages SET status='cancelled',error='任务过期或收件人资格变化' WHERE ai_run_id=? AND status='queued'", (recipient['run_id'],))
            with self.store.connect() as db:
                recipients = db.execute('SELECT p.*,r.status run_status,r.error run_error FROM agent_schedule_recipients p LEFT JOIN ai_runs r ON r.id=p.run_id WHERE p.occurrence_id=?', (row['id'],)).fetchall()
                for recipient in recipients:
                    state = recipient['run_status'] or 'cancelled'
                    db.execute('UPDATE agent_schedule_recipients SET status=?,error=?,updated_at=? WHERE occurrence_id=? AND contact_id=?',
                        (state, recipient['run_error'] or '', now(), row['id'], recipient['contact_id']))
                statuses = [r['run_status'] or 'cancelled' for r in recipients]
                if run['status'] == 'completed':
                    if any(s in ('queued','sending') for s in statuses):status = 'queued'
                    elif not statuses:status = 'skipped'
                    elif all(s == 'sent' for s in statuses):status = 'completed'
                    elif any(s == 'uncertain' for s in statuses):status = 'uncertain'
                    elif any(s == 'sent' for s in statuses):status = 'partial'
                    elif all(s == 'cancelled' for s in statuses):status = 'cancelled'
                    else:status = 'failed'
                    error = '生成后没有符合条件的联系人，未推送' if not statuses else ''
                else:status = run['status'];error = run['error']
                if status not in ACTIVE:
                    db.execute("UPDATE ai_approvals SET status='cancelled' WHERE run_id=? AND status='pending'", (run['id'],))
                db.execute('UPDATE agent_schedule_occurrences SET status=?,error=?,updated_at=? WHERE id=?', (status, error, now(), row['id']))
                db.execute('''UPDATE agent_schedules SET last_status=?,updated_at=? WHERE id=?
                    AND ?=(SELECT id FROM agent_schedule_occurrences WHERE schedule_id=? ORDER BY created_at DESC LIMIT 1)''',
                    (error or status, now(), row['schedule_id'], row['id'], row['schedule_id']))

    def tick(self, stamp=None):
        if stamp is None and time.monotonic() - self.last_tick < 2:return
        self.last_tick = time.monotonic();stamp = stamp or datetime.now(timezone.utc)
        self._sync()
        for value in self.list():
            if value['enabled'] and value['next_at'] and datetime.fromisoformat(value['next_at']) <= stamp:
                self._claim(value, value['next_at'], 'scheduled', stamp)

    def detail(self, oid):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM agent_schedule_occurrences WHERE id=?', (oid,)).fetchone()
            recipients = [dict(r) for r in db.execute('SELECT * FROM agent_schedule_recipients WHERE occurrence_id=? ORDER BY created_at,contact_id', (oid,))]
            for recipient in recipients:
                recipient['message_ids'] = [r[0] for r in db.execute('SELECT id FROM messages WHERE ai_run_id=? ORDER BY id', (recipient['run_id'],))]
        if not row:raise KeyError('定时任务运行不存在')
        result = dict(row)
        result.pop('targets', None)
        run = self.ai.data.run(row['run_id']) if row['run_id'] else None
        media = run['state'].get('artifacts', []) if run else []
        return result | {'reply':run['reply'] if run else '', 'text':run['reply'] if run else '',
            'media':media, 'recipients':recipients, 'deliveries':recipients,
            'started_at':row['created_at'], 'finished_at':row['updated_at'] if row['status'] not in ACTIVE else None}

    def history(self, sid):
        self.get(sid)
        with self.ai.lock:self._sync(sid)
        with self.store.connect() as db:ids = [r[0] for r in db.execute('SELECT id FROM agent_schedule_occurrences WHERE schedule_id=? ORDER BY created_at DESC LIMIT 50', (sid,))]
        return [self.detail(oid) for oid in ids]


def install_agent_schedule_routes(app):
    @app.get('/api/ai/agent-schedules')
    def schedules():return app.state.ai.agent_scheduler.list()

    @app.get('/api/ai/agent-schedules/eligible')
    def eligible(agent_id:str):
        contacts = app.state.ai.agent_scheduler.eligible(agent_id)
        return {'count':len(contacts), 'contacts':[{k:c[k] for k in ('id','lookup')} for c in contacts]}

    @app.post('/api/ai/agent-schedules', status_code=201)
    def create(body:dict):return app.state.ai.agent_scheduler.save(body)

    @app.put('/api/ai/agent-schedules/{schedule_id}')
    def update(schedule_id:str, body:dict):return app.state.ai.agent_scheduler.save(body, schedule_id)

    @app.delete('/api/ai/agent-schedules/{schedule_id}')
    def delete(schedule_id:str):
        app.state.ai.agent_scheduler.delete(schedule_id)
        return {'ok':True}

    @app.post('/api/ai/agent-schedules/{schedule_id}/run', status_code=202)
    def run(schedule_id:str):return app.state.ai.agent_scheduler.run_now(schedule_id)

    @app.get('/api/ai/agent-schedules/{schedule_id}/runs')
    def history(schedule_id:str):return app.state.ai.agent_scheduler.history(schedule_id)
