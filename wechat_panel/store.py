from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import uuid

from .snapshots import compare
from PIL import Image


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY, lookup TEXT NOT NULL, wechat_id TEXT NOT NULL,
                    nickname TEXT NOT NULL, account_id TEXT NOT NULL,
                    listening INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT 'manual',
                    listener_state TEXT NOT NULL DEFAULT 'idle', error TEXT NOT NULL DEFAULT '',
                    unread INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    UNIQUE(account_id, wechat_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    direction TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'text',
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    source_key TEXT, client_key TEXT, historical INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    UNIQUE(contact_id, source_key), UNIQUE(contact_id, client_key)
                );
                CREATE INDEX IF NOT EXISTS messages_contact ON messages(contact_id, id);
                CREATE TABLE IF NOT EXISTS snapshots (
                    contact_id TEXT PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
                    contact_id TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS avatars (
                    account_id TEXT NOT NULL, wechat_id TEXT NOT NULL,
                    digest TEXT NOT NULL, data BLOB NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, wechat_id)
                );
                INSERT OR IGNORE INTO settings VALUES ('paused', 'false');
            ''')
            for table, fields in {
                'contacts':{'ai_agent_id':"TEXT NOT NULL DEFAULT ''",'ai_revision':'INTEGER NOT NULL DEFAULT 0','ai_start_message':'INTEGER NOT NULL DEFAULT 0'},
                'messages':{'origin':"TEXT NOT NULL DEFAULT 'manual'",'ai_run_id':'TEXT',
                            'direction_verified':'INTEGER NOT NULL DEFAULT 0','media_id':"TEXT NOT NULL DEFAULT ''"}
            }.items():
                columns={r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
                for name,definition in fields.items():
                    if name not in columns:db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def event(db, event_type, contact_id=None, **payload):
        db.execute('INSERT INTO events(type, contact_id, payload, created_at) VALUES(?,?,?,?)',
                   (event_type, contact_id, json.dumps(payload, ensure_ascii=False), now()))

    def contacts(self):
        with self.connect() as db:
            rows = db.execute('''SELECT c.*, a.digest avatar_version, own.digest self_avatar_version,
                (SELECT text FROM messages WHERE contact_id=c.id ORDER BY id DESC LIMIT 1) last_message,
                (SELECT created_at FROM messages WHERE contact_id=c.id ORDER BY id DESC LIMIT 1) last_at
                FROM contacts c
                LEFT JOIN avatars a ON a.account_id=c.account_id AND a.wechat_id=c.wechat_id
                LEFT JOIN avatars own ON own.account_id=c.account_id AND own.wechat_id=c.account_id
                ORDER BY COALESCE(last_at,c.created_at) DESC''').fetchall()
            return [self._contact_image_urls(dict(x)) for x in rows]

    @staticmethod
    def _contact_image_urls(contact):
        base = f"/api/contacts/{contact['id']}/avatar"
        contact['avatar_url'] = f"{base}?v={contact['avatar_version']}" if contact.get('avatar_version') else None
        contact['self_avatar_url'] = f"{base}?own=true&v={contact['self_avatar_version']}" if contact.get('self_avatar_version') else None
        return contact

    def contact(self, contact_id):
        with self.connect() as db:
            row = db.execute('''SELECT c.*, a.digest avatar_version, own.digest self_avatar_version FROM contacts c
                LEFT JOIN avatars a ON a.account_id=c.account_id AND a.wechat_id=c.wechat_id
                LEFT JOIN avatars own ON own.account_id=c.account_id AND own.wechat_id=c.account_id
                WHERE c.id=?''', (contact_id,)).fetchone()
            if row is None:
                raise KeyError('联系人不存在')
            return self._contact_image_urls(dict(row))

    def set_avatar(self, account_id, wechat_id, data):
        if not isinstance(data, bytes) or len(data) > 65536:
            raise ValueError('无效头像图片')
        with Image.open(BytesIO(data)) as image:
            if image.format != 'PNG' or image.size != (64, 64):
                raise ValueError('无效头像图片')
            image.verify()
        digest = hashlib.sha256(data).hexdigest()[:24]
        with self.connect() as db:
            # Ignore stale worker results after a contact was removed.
            if not db.execute('SELECT 1 FROM contacts WHERE account_id=? AND (wechat_id=? OR account_id=?)',
                              (account_id, wechat_id, wechat_id)).fetchone():
                return False
            old = db.execute('SELECT digest FROM avatars WHERE account_id=? AND wechat_id=?',
                             (account_id, wechat_id)).fetchone()
            if old and old[0] == digest:
                return False
            db.execute('INSERT OR REPLACE INTO avatars VALUES(?,?,?,?,?)',
                       (account_id, wechat_id, digest, data, now()))
            self.event(db, 'contact.avatar', account_id=account_id, wechat_id=wechat_id)
        return True

    def avatar(self, contact_id, own=False):
        contact = self.contact(contact_id)
        with self.connect() as db:
            row = db.execute('SELECT data FROM avatars WHERE account_id=? AND wechat_id=?',
                (contact['account_id'], contact['account_id'] if own else contact['wechat_id'])).fetchone()
            if row is None:
                raise KeyError('头像尚未同步')
            return row['data']

    def add_contact(self, profile):
        contact_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute('''INSERT INTO contacts
                (id,lookup,wechat_id,nickname,account_id,created_at,verified_at)
                VALUES(?,?,?,?,?,?,?)''', (contact_id, profile['lookup'], profile['wechat_id'],
                profile['nickname'], profile['account_id'], now(), now()))
            self.event(db, 'contact.added', contact_id)
        if profile.get('avatar_data'):
            self.set_avatar(profile['account_id'], profile['wechat_id'], profile['avatar_data'])
        return self.contact(contact_id)

    def update_contact(self, contact_id, **values):
        self.contact(contact_id)
        allowed = {'listening', 'mode', 'listener_state', 'error', 'unread', 'verified_at','ai_agent_id'}
        if not values or not set(values) <= allowed:
            raise ValueError('无效的联系人字段')
        with self.connect() as db:
            db.execute('UPDATE contacts SET ' + ','.join(f'{x}=?' for x in values) + ' WHERE id=?',
                       (*values.values(), contact_id))
            if set(values)&{'listening','mode','ai_agent_id'}:
                db.execute('''UPDATE contacts SET ai_revision=ai_revision+1,ai_start_message=
                    COALESCE((SELECT MAX(id) FROM messages WHERE contact_id=?),0) WHERE id=?''',(contact_id,contact_id))
            self.event(db, 'contact.updated', contact_id)
        return self.contact(contact_id)

    def delete_contact(self, contact_id):
        self.contact(contact_id)
        with self.connect() as db:
            db.execute('DELETE FROM contacts WHERE id=?', (contact_id,))
            db.execute('''DELETE FROM avatars WHERE NOT EXISTS (
                SELECT 1 FROM contacts c WHERE c.account_id=avatars.account_id
                AND (c.wechat_id=avatars.wechat_id OR c.account_id=avatars.wechat_id))''')
            db.execute('DELETE FROM events WHERE contact_id=?', (contact_id,))
            self.event(db, 'contact.removed', contact_id)

    def paused(self):
        with self.connect() as db:
            return db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == 'true'

    def pause(self, value):
        with self.connect() as db:
            db.execute("UPDATE settings SET value=? WHERE key='paused'", ('true' if value else 'false',))
            self.event(db, 'runtime.paused', paused=value)

    def messages(self, contact_id, before=None, limit=100):
        self.contact(contact_id)
        with self.connect() as db:
            rows = db.execute('''SELECT * FROM messages WHERE contact_id=? AND id<?
                ORDER BY id DESC LIMIT ?''', (contact_id, before or 2**63-1, limit)).fetchall()
            return [dict(x) for x in reversed(rows)]

    def message(self, message_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
            return dict(row) if row else None

    def enqueue(self, contact_id, text, client_key, origin='manual', ai_run_id=None):
        contact = self.contact(contact_id)
        if contact['mode'] == 'listen_only':
            raise ValueError('仅监听模式不能发送，请先切换到人工聊天')
        with self.connect() as db:
            prior = db.execute('SELECT * FROM messages WHERE contact_id=? AND client_key=?',
                               (contact_id, client_key)).fetchone()
            if prior:
                if prior['text'] != text:
                    raise ValueError('相同请求标识不能用于不同消息')
                return dict(prior), False
            cursor = db.execute('''INSERT INTO messages
                (contact_id,direction,text,status,created_at,client_key,origin,ai_run_id) VALUES(?,?,?,?,?,?,?,?)''',
                (contact_id, 'outgoing', text, 'queued', now(), client_key,origin,ai_run_id))
            self.event(db, 'message.updated', contact_id, message_id=cursor.lastrowid)
            message_id = cursor.lastrowid
        return self.message(message_id), True

    def set_message_state(self, message_id, status, error='', source_key=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
            if row:
                if source_key:
                    # A passive snapshot can observe the outgoing bubble before
                    # the worker's send confirmation arrives. Merge only that
                    # exact source identity, never all messages with equal text.
                    echo = db.execute('''SELECT id FROM messages WHERE contact_id=? AND source_key=?
                        AND id!=? AND status='observed' AND direction='outgoing' AND text=?''',
                        (row['contact_id'], source_key, message_id, row['text'])).fetchone()
                    if echo:
                        db.execute('DELETE FROM messages WHERE id=?', (echo['id'],))
                        self.event(db, 'message.removed', row['contact_id'], message_id=echo['id'])
                db.execute('UPDATE messages SET status=?,error=?,source_key=COALESCE(?,source_key) WHERE id=?',
                           (status, error, source_key, message_id))
                self.event(db, 'message.updated', row['contact_id'], message_id=message_id)
                if row['ai_run_id'] and status in ('confirmed','uncertain','failed','cancelled'):
                    db.execute('UPDATE ai_runs SET status=?,error=?,updated_at=? WHERE id=?',
                               ('sent' if status=='confirmed' else status,error,now(),row['ai_run_id']))
                    self.event(db,'ai.run',row['contact_id'],run_id=row['ai_run_id'])

    def ai_send_valid(self, message):
        if message.get('origin') != 'ai':return True
        with self.connect() as db:
            row=db.execute('''SELECT r.*,c.mode current_mode,c.ai_agent_id,c.ai_revision,c.listening FROM ai_runs r
                JOIN contacts c ON c.id=r.contact_id WHERE r.id=?''',(message.get('ai_run_id'),)).fetchone()
            if not row:return False
            if row['schedule_id']:
                from .companion_scheduler import schedule_valid
                return row['status']=='queued' and schedule_valid(self,dict(row))
            settings=json.loads(db.execute("SELECT value FROM settings WHERE key='ai_settings'").fetchone()[0])
            latest=db.execute("SELECT MAX(id) FROM messages WHERE contact_id=? AND direction='incoming' AND historical=0 AND (kind='text' OR (kind='image' AND media_id!=''))",(row['contact_id'],)).fetchone()[0]
            paused=db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='true'
            return (not paused and settings['enabled'] and row['listening'] and row['status']=='queued'
                    and row['mode']==row['current_mode'] and row['agent_id']==row['ai_agent_id']
                    and row['revision']==row['ai_revision'] and row['trigger_id']==latest)

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE messages SET status='uncertain',error='服务重启，发送结果需要核对' WHERE status='sending'")
            db.execute("UPDATE messages SET status='cancelled',error='服务重启，未执行的发送已取消' WHERE status='queued'")
            db.execute("UPDATE contacts SET listener_state='idle' WHERE listening=0")
            db.execute("UPDATE contacts SET listener_state='starting' WHERE listening=1")

    def pending(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM messages WHERE status='queued' ORDER BY id")]

    def ingest(self, contact_id, items):
        media={}
        for item in items:
            if item.get('image_data'):
                with self.connect() as lookup:
                    row=lookup.execute('SELECT media_id FROM messages WHERE contact_id=? AND source_key=?',(contact_id,item['key'])).fetchone()
                if not row or not row['media_id']:
                    from .companion_store import CompanionStore
                    import base64
                    memory=CompanionStore(self)
                    media[item['key']]=memory.media('wechat:'+contact_id,base64.b64decode(item['image_data']))['id']
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM contacts WHERE id=?', (contact_id,)).fetchone():
                return
            prior = db.execute('SELECT data FROM snapshots WHERE contact_id=?', (contact_id,)).fetchone()
            delta = compare(json.loads(prior[0]) if prior else None, items)
            for item in delta.items:
                cursor = db.execute('''INSERT OR IGNORE INTO messages
                    (contact_id,direction,text,kind,status,created_at,source_key,historical,direction_verified,media_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (contact_id, item['direction'], item['text'],
                    item.get('kind','text'), 'observed', now(), item['key'], delta.historical,bool(item.get('direction_verified')),media.get(item['key'],'')))
                if cursor.rowcount:
                    self.event(db, 'message.updated', contact_id, message_id=cursor.lastrowid)
                    if not delta.historical and item['direction'] == 'incoming':
                        db.execute('UPDATE contacts SET unread=unread+1 WHERE id=?', (contact_id,))
                        if item.get('kind') == 'text' or (item.get('kind')=='image' and media.get(item['key'])):
                            self.event(db, 'agent.message', contact_id, message_id=cursor.lastrowid,
                                       text=item['text'], account_id=item.get('account_id'))
            # A new row can be clipped/undrawn on its first observation. Resolve
            # its sender later without inserting a second copy or replaying history.
            # Older captures made with system-DPI coordinates can be corrected
            # once by verified physical-pixel evidence from the current adapter.
            for item in items:
                direction=item['direction']
                if direction not in ('incoming','outgoing'):continue
                row=db.execute('SELECT * FROM messages WHERE contact_id=? AND source_key=?',
                               (contact_id,item['key'])).fetchone()
                if not row:continue
                if item['key'] in media and not row['media_id']:
                    db.execute("UPDATE messages SET media_id=?,error='' WHERE id=?",(media[item['key']],row['id']))
                    self.event(db,'message.updated',contact_id,message_id=row['id'])
                    if row['direction']=='incoming' and not row['historical']:
                        self.event(db,'agent.message',contact_id,message_id=row['id'],text=row['text'],account_id=item.get('account_id'))
                if row['kind']=='image' and item.get('image_error') and not row['media_id']:
                    db.execute('UPDATE messages SET error=? WHERE id=?',(item['image_error'],row['id']))
                verified=bool(item.get('direction_verified'))
                if row['direction']!=direction and (row['direction']=='unknown' or (verified and not row['direction_verified'])):
                    db.execute('UPDATE messages SET direction=?,direction_verified=? WHERE id=?',
                               (direction,verified,row['id']))
                    self.event(db,'message.updated',contact_id,message_id=row['id'])
                    if not row['historical']:
                        if direction=='incoming':
                            db.execute('UPDATE contacts SET unread=unread+1 WHERE id=?',(contact_id,))
                            if (row['kind']=='text' or (row['kind']=='image' and (row['media_id'] or media.get(item['key'])))) and not db.execute("SELECT 1 FROM events WHERE contact_id=? AND type='agent.message' AND json_extract(payload,'$.message_id')=?",
                                                                   (contact_id,row['id'])).fetchone():
                                self.event(db,'agent.message',contact_id,message_id=row['id'],text=row['text'],account_id=item.get('account_id'))
                        elif row['direction']=='incoming':
                            db.execute('UPDATE contacts SET unread=MAX(0,unread-1) WHERE id=?',(contact_id,))
                elif verified and not row['direction_verified'] and row['direction']==direction:
                    db.execute('UPDATE messages SET direction_verified=1 WHERE id=?',(row['id'],))
            # A temporarily empty/unreadable window must not erase our cursor and
            # turn old messages into new Agent events when it reappears.
            if items or not prior:
                db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?)',
                           (contact_id, json.dumps([{k:v for k,v in item.items() if k!='image_data'} for item in items], ensure_ascii=False)))
            if delta.gap:
                detail = '窗口历史发生变化；已重新建立监听基线，中断区间是否完整需要核对'
                error = db.execute('SELECT error FROM contacts WHERE id=?', (contact_id,)).fetchone()[0]
                if error != detail:
                    db.execute('UPDATE contacts SET error=? WHERE id=?', (detail, contact_id))
                    self.event(db, 'listener.gap', contact_id, detail=detail)

    def events(self, after=0, limit=100, agent_only=False):
        with self.connect() as db:
            where = " AND type='agent.message'" if agent_only else ''
            rows = db.execute('SELECT * FROM events WHERE id>?' + where + ' ORDER BY id LIMIT ?',
                              (after, limit)).fetchall()
            return [{**dict(x), 'payload': json.loads(x['payload'])} for x in rows]

    def latest_event(self):
        with self.connect() as db:
            return db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]

    def recent_events(self, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
            return [{**dict(x), 'payload': json.loads(x['payload'])} for x in reversed(rows)]
