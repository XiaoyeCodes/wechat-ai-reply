from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import uuid

from .snapshots import compare, align
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
                CREATE TABLE IF NOT EXISTS snapshot_pending (
                    contact_id TEXT PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS send_receipts (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                    baseline TEXT NOT NULL, before_count INTEGER NOT NULL DEFAULT 0,
                    attempted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
                    contact_id TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wechat_accounts (
                    account_id TEXT PRIMARY KEY, name TEXT NOT NULL, last_seen_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS voice_clones (
                    request_id TEXT PRIMARY KEY, scope TEXT NOT NULL, voice_id TEXT NOT NULL,
                    name TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS avatars (
                    account_id TEXT NOT NULL, wechat_id TEXT NOT NULL,
                    digest TEXT NOT NULL, data BLOB NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, wechat_id)
                );
                INSERT OR IGNORE INTO settings VALUES ('paused', 'false');
                CREATE TABLE IF NOT EXISTS group_member_avatars (
                    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    sender TEXT NOT NULL, digest TEXT NOT NULL, data BLOB NOT NULL,
                    PRIMARY KEY(contact_id, sender)
                );
            ''')
            for table, fields in {
                'contacts':{'ai_agent_id':"TEXT NOT NULL DEFAULT ''",'ai_revision':'INTEGER NOT NULL DEFAULT 0','ai_start_message':'INTEGER NOT NULL DEFAULT 0',
                            'chat_type':"TEXT NOT NULL DEFAULT 'direct'",'group_members':"TEXT NOT NULL DEFAULT '[]'",
                            'group_mention_only':'INTEGER NOT NULL DEFAULT 1','group_self_name':"TEXT NOT NULL DEFAULT ''",
                            'group_identity_verified':'INTEGER NOT NULL DEFAULT 0','group_members_verified':'INTEGER NOT NULL DEFAULT 0',
                            'group_self_name_verified':'INTEGER NOT NULL DEFAULT 0',
                            'group_self_name_source':"TEXT NOT NULL DEFAULT ''",
                            'ai_delivery_revision':'INTEGER NOT NULL DEFAULT 0',
                            'ai_context_start_message':'INTEGER NOT NULL DEFAULT 0','reply_format':"TEXT NOT NULL DEFAULT 'text'",
                            'voice_preset':"TEXT NOT NULL DEFAULT 'windows-huihui'",
                            'voice_model':"TEXT NOT NULL DEFAULT 'speech-2.8-turbo'"},
                'messages':{'origin':"TEXT NOT NULL DEFAULT 'manual'",'ai_run_id':'TEXT',
                            'sender':"TEXT NOT NULL DEFAULT ''",'sender_verified':'INTEGER NOT NULL DEFAULT 0',
                            'mention_me':'INTEGER NOT NULL DEFAULT 0','mention_verified':'INTEGER NOT NULL DEFAULT 0',
                            'mention_source':"TEXT NOT NULL DEFAULT ''",
                            'direction_verified':'INTEGER NOT NULL DEFAULT 0','media_id':"TEXT NOT NULL DEFAULT ''",'text_pending':'INTEGER NOT NULL DEFAULT 0',
                            'quote':"TEXT NOT NULL DEFAULT '{}'"}
            }.items():
                columns={r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
                for name,definition in fields.items():
                    if name not in columns:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
                        if table=='contacts' and name=='ai_context_start_message':
                            # Existing histories have no reliable binding boundary.
                            # Start clean while retaining a short quoted handoff.
                            db.execute('UPDATE contacts SET ai_context_start_message=COALESCE((SELECT MAX(id) FROM messages WHERE contact_id=contacts.id),0)')

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

    def active_account(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='active_wechat_account'").fetchone()
            # Stores used without a running WeChat connection retain their
            # unscoped behavior (imports, migrations and isolated tests).
            return row['value'] if row else None

    def account_matches(self, contact):
        active = self.active_account()
        return active is None or bool(active and contact.get('account_id') == active)

    def activate_account(self, account_id, name=''):
        account_id = account_id or ''
        with self.connect() as db:
            prior = db.execute("SELECT value FROM settings WHERE key='active_wechat_account'").fetchone()
            changed = prior is None or prior['value'] != account_id
            if account_id:
                db.execute('INSERT INTO wechat_accounts VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET '
                           "name=CASE WHEN excluded.name!='' THEN excluded.name ELSE name END,last_seen_at=excluded.last_seen_at",
                           (account_id, name, now()))
            if not changed:return False
            db.execute("INSERT OR REPLACE INTO settings VALUES('active_wechat_account',?)", (account_id,))
            reason = '微信登录账号已变化，旧账号任务已取消'
            db.execute("UPDATE contacts SET listener_state=CASE WHEN listening THEN 'inactive' ELSE 'idle' END,error='',"
                       'ai_revision=ai_revision+1,ai_start_message=COALESCE((SELECT MAX(id) FROM messages WHERE contact_id=contacts.id),0) '
                       'WHERE account_id!=?', (account_id,))
            db.execute("UPDATE messages SET status='cancelled',error=? WHERE status='queued' AND contact_id IN "
                       '(SELECT id FROM contacts WHERE account_id!=?)', (reason, account_id))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_runs'").fetchone():
                db.execute("DELETE FROM ai_inbox WHERE contact_id IN (SELECT id FROM contacts WHERE account_id!=?)", (account_id,))
                db.execute("UPDATE ai_runs SET status='cancelled',error=?,updated_at=? WHERE contact_id IN "
                           "(SELECT id FROM contacts WHERE account_id!=?) AND status IN ('pending','running','waiting_approval','draft','queued')",
                           (reason, now(), account_id))
                db.execute("UPDATE ai_approvals SET status='cancelled' WHERE run_id IN (SELECT id FROM ai_runs WHERE status='cancelled')")
                db.execute("UPDATE hermes_confirmations SET status='cancelled' WHERE contact_id IN "
                           "(SELECT id FROM contacts WHERE account_id!=?) AND status='pending'", (account_id,))
            self.event(db, 'runtime.account', account_id=account_id, name=name)
            return True

    def accounts(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM wechat_accounts ORDER BY last_seen_at DESC')]

    def contacts(self, all_accounts=False):
        active = None if all_accounts else self.active_account()
        with self.connect() as db:
            rows = db.execute('''SELECT c.*, a.digest avatar_version, own.digest self_avatar_version,
                (SELECT text FROM messages WHERE contact_id=c.id ORDER BY id DESC LIMIT 1) last_message,
                (SELECT created_at FROM messages WHERE contact_id=c.id ORDER BY id DESC LIMIT 1) last_at
                FROM contacts c
                LEFT JOIN avatars a ON a.account_id=c.account_id AND a.wechat_id=c.wechat_id
                LEFT JOIN avatars own ON own.account_id=c.account_id AND own.wechat_id=c.account_id
                WHERE (? IS NULL OR c.account_id=?)
                ORDER BY COALESCE(last_at,c.created_at) DESC''', (active, active)).fetchall()
            return [self._contact_image_urls(dict(x)) for x in rows]

    @staticmethod
    def _contact_image_urls(contact):
        from .group_chats import members
        if 'group_members' in contact:contact['group_members'] = members(contact['group_members'])
        for field in ('group_mention_only','group_identity_verified','group_members_verified','group_self_name_verified'):
            if field in contact:contact[field]=bool(contact[field])
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

    def set_member_avatar(self, contact_id, sender, data):
        if not isinstance(data, bytes) or len(data) > 65536 or not sender:
            raise ValueError('无效群成员头像')
        with Image.open(BytesIO(data)) as image:
            if image.format != 'PNG' or image.size != (64, 64):raise ValueError('无效群成员头像')
            image.verify()
        digest = hashlib.sha256(data).hexdigest()[:24]
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM contacts WHERE id=? AND chat_type='group'", (contact_id,)).fetchone():return
            old = db.execute('SELECT digest FROM group_member_avatars WHERE contact_id=? AND sender=?', (contact_id, sender)).fetchone()
            if old and old[0] == digest:return
            db.execute('INSERT OR REPLACE INTO group_member_avatars VALUES(?,?,?,?)', (contact_id, sender, digest, data))
            self.event(db, 'contact.member_avatar', contact_id)

    def member_avatar(self, contact_id, sender):
        self.contact(contact_id)
        with self.connect() as db:
            row = db.execute('SELECT data FROM group_member_avatars WHERE contact_id=? AND sender=?', (contact_id, sender)).fetchone()
            if not row:raise KeyError('群成员头像暂不可用')
            return row['data']

    def add_contact(self, profile):
        from .group_chats import is_group, members, VERIFICATION_FIELDS
        contact_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute('''INSERT INTO contacts
                (id,lookup,wechat_id,nickname,account_id,created_at,verified_at)
                VALUES(?,?,?,?,?,?,?)''', (contact_id, profile['lookup'], profile['wechat_id'],
                profile['nickname'], profile['account_id'], now(), now()))
            if is_group(profile):
                db.execute('''UPDATE contacts SET chat_type='group',group_members=?,group_mention_only=?,group_self_name=?,
                    group_identity_verified=?,group_members_verified=?,group_self_name_verified=? WHERE id=?''',
                    (json.dumps(members(profile.get('group_members', [])), ensure_ascii=False),bool(profile.get('group_mention_only', True)),
                     profile.get('group_self_name', ''),*(bool(profile.get(k)) for k in VERIFICATION_FIELDS),contact_id))
                db.execute('UPDATE contacts SET group_self_name_source=? WHERE id=?',(profile.get('group_self_name_source',''),contact_id))
            self.event(db, 'contact.added', contact_id)
        if profile.get('avatar_data'):
            self.set_avatar(profile['account_id'], profile['wechat_id'], profile['avatar_data'])
        return self.contact(contact_id)

    def update_contact(self, contact_id, **values):
        current = self.contact(contact_id)
        allowed = {'listening', 'mode', 'listener_state', 'error', 'unread', 'verified_at','ai_agent_id','reply_format','voice_preset','voice_model'}
        from .group_chats import is_group, members, RULE_FIELDS, VERIFICATION_FIELDS
        allowed |= RULE_FIELDS | set(VERIFICATION_FIELDS) | {'group_self_name_source'}
        if not values or not set(values) <= allowed:
            raise ValueError('无效的联系人字段')
        if set(values) & (RULE_FIELDS | set(VERIFICATION_FIELDS)) and not is_group(current):
            raise ValueError('群监听规则仅适用于群聊')
        if 'group_members' in values:values['group_members'] = json.dumps(members(values['group_members']),ensure_ascii=False)
        if 'reply_format' in values and values['reply_format'] not in ('agent','text','voice'):
            raise ValueError('请选择跟随 Agent、文字或语音回复')
        if 'voice_preset' in values:
            from .voice_synthesis import preset
            preset(values['voice_preset'],store=self)
        if 'voice_model' in values:
            from .minimax_voice import MODELS
            if values['voice_model'] not in MODELS:raise ValueError('请选择 Turbo 或 HD 模型')
        with self.connect() as db:
            previous=db.execute('SELECT ai_agent_id FROM contacts WHERE id=?',(contact_id,)).fetchone()[0]
            if 'ai_agent_id' in values and values['ai_agent_id']!=previous:
                db.execute('UPDATE contacts SET ai_context_start_message=COALESCE((SELECT MAX(id) FROM messages WHERE contact_id=?),0) WHERE id=?',
                           (contact_id,contact_id))
            db.execute('UPDATE contacts SET ' + ','.join(f'{x}=?' for x in values) + ' WHERE id=?',
                       (*values.values(), contact_id))
            if set(values)&({'listening','mode','ai_agent_id'} | RULE_FIELDS):
                db.execute('''UPDATE contacts SET ai_revision=ai_revision+1,ai_delivery_revision=ai_delivery_revision+1,ai_start_message=
                    COALESCE((SELECT MAX(id) FROM messages WHERE contact_id=?),0) WHERE id=?''',(contact_id,contact_id))
                if set(values)&RULE_FIELDS:
                    db.execute('UPDATE contacts SET ai_context_start_message=ai_start_message WHERE id=?',(contact_id,))
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
            rows = db.execute('''SELECT messages.*,
                (SELECT digest FROM group_member_avatars WHERE contact_id=messages.contact_id AND sender=messages.sender) sender_avatar_version
                FROM messages WHERE contact_id=? AND id<?
                ORDER BY id DESC LIMIT ?''', (contact_id, before or 2**63-1, limit)).fetchall()
            return [self._message_values(dict(x)) for x in reversed(rows)]

    @staticmethod
    def _message_values(message):
        if 'quote' in message:message['quote']=json.loads(message['quote'] or '{}')
        from urllib.parse import urlencode
        version = message.pop('sender_avatar_version', None)
        message['sender_avatar_url'] = (f"/api/contacts/{message['contact_id']}/member-avatar?" +
            urlencode({'sender': message.get('sender', ''), 'v': version})
            if version and message.get('sender_verified') else None)
        for field in ('sender_verified','mention_me','mention_verified'):
            if field in message:message[field]=bool(message[field])
        return message

    def message(self, message_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
            return self._message_values(dict(row)) if row else None

    def enqueue(self, contact_id, text, client_key, origin='manual', ai_run_id=None, kind='text'):
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
                (contact_id,direction,text,status,created_at,client_key,origin,ai_run_id,kind) VALUES(?,?,?,?,?,?,?,?,?)''',
                (contact_id, 'outgoing', text, 'queued', now(), client_key,origin,ai_run_id,kind))
            self.event(db, 'message.updated', contact_id, message_id=cursor.lastrowid)
            message_id = cursor.lastrowid
        return self.message(message_id), True

    def enqueue_ai_reply(self, contact_id, text, run_id):
        """Enqueue all parts atomically, so early confirmation cannot end the run."""
        from .wechat_text import split_text
        if self.contact(contact_id)['mode'] == 'listen_only':
            raise ValueError('仅监听模式不能发送')
        ids = []
        with self.connect() as db:
            for index, part in enumerate(split_text(text)):
                key = 'ai:' + run_id + (f':part:{index}' if index else '')
                prior = db.execute('SELECT id,text FROM messages WHERE contact_id=? AND client_key=?',
                                   (contact_id, key)).fetchone()
                if prior:
                    if prior['text'] != part:
                        raise ValueError('相同请求标识不能用于不同消息')
                    ids.append(prior['id'])
                    continue
                cursor = db.execute('''INSERT INTO messages
                    (contact_id,direction,text,status,created_at,client_key,origin,ai_run_id)
                    VALUES(?,?,?,?,?,?,?,?)''', (contact_id, 'outgoing', part, 'queued', now(), key, 'ai', run_id))
                ids.append(cursor.lastrowid)
                self.event(db, 'message.updated', contact_id, message_id=cursor.lastrowid)
        return [self.message(mid) for mid in ids]

    def enqueue_ai_images(self, contact_id, run_id, media_ids):
        """Atomically queue every image; repeating finish never sends twice."""
        if self.contact(contact_id)['mode'] == 'listen_only':
            raise ValueError('仅监听模式不能发送')
        ids = []
        with self.connect() as db:
            for index, mid in enumerate(dict.fromkeys(media_ids)):
                key = f'ai:{run_id}:image:{index}'
                prior = db.execute('SELECT * FROM messages WHERE contact_id=? AND client_key=?',
                                   (contact_id, key)).fetchone()
                if prior:
                    if prior['media_id'] != mid or prior['kind'] != 'image':
                        raise ValueError('相同请求标识不能用于不同图片')
                    ids.append(prior['id'])
                    continue
                cursor = db.execute('''INSERT INTO messages
                    (contact_id,direction,text,status,created_at,client_key,origin,ai_run_id,kind,media_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (contact_id, 'outgoing', '[图片]', 'queued', now(), key, 'ai', run_id, 'image', mid))
                ids.append(cursor.lastrowid)
                self.event(db, 'message.updated', contact_id, message_id=cursor.lastrowid)
        return [self.message(mid) for mid in ids]

    def enqueue_ai_bundle(self, contact_id, run_id, text, media_ids, pdf_name=''):
        """Queue a checked compound result atomically before any part can send."""
        from .wechat_text import split_text
        if self.contact(contact_id)['mode']=='listen_only':raise ValueError('仅监听模式不能发送')
        parts=[('text',part,'',f'ai:{run_id}:summary:{index}')
               for index,part in enumerate(split_text(text))] if text.strip() else []
        parts.extend(('image','[图片]',mid,f'ai:{run_id}:image:{index}')
                     for index,mid in enumerate(dict.fromkeys(media_ids)))
        if pdf_name:parts.append(('file',pdf_name,'',f'ai:{run_id}'))
        ids=[]
        with self.connect() as db:
            for kind,body,media_id,key in parts:
                prior=db.execute('SELECT * FROM messages WHERE contact_id=? AND client_key=?',(contact_id,key)).fetchone()
                if prior:
                    if (prior['kind'],prior['text'],prior['media_id'])!=(kind,body,media_id):
                        raise ValueError('相同请求标识不能用于不同交付内容')
                    ids.append(prior['id']);continue
                cursor=db.execute('''INSERT INTO messages
                    (contact_id,direction,text,status,created_at,client_key,origin,ai_run_id,kind,media_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (contact_id,'outgoing',body,'queued',now(),key,'ai',run_id,kind,media_id))
                ids.append(cursor.lastrowid)
                self.event(db,'message.updated',contact_id,message_id=cursor.lastrowid)
        return [self.message(mid) for mid in ids]

    def set_message_state(self, message_id, status, error='', source_key=None, reconciled=False):
        with self.connect() as db:
            if reconciled:db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
            if row:
                if reconciled and row['status']!='uncertain':
                    return  # A cancellation/failure after the snapshot query wins.
                if source_key:
                    # A passive snapshot can observe the outgoing bubble before
                    # the worker's send confirmation arrives. Merge only that
                    # exact source identity, never all messages with equal text.
                    from .wechat_text import receipt_text
                    echo = db.execute('''SELECT id,text,kind FROM messages WHERE contact_id=? AND source_key=?
                        AND id!=? AND status='observed' AND direction='outgoing' ''',
                        (row['contact_id'], source_key, message_id)).fetchone()
                    if echo and (receipt_text(echo['text']) == receipt_text(row['text']) or
                                 (row['kind'] == echo['kind'] == 'file' and row['text'] in echo['text']) or
                                 (row['kind'] == echo['kind'] and row['kind'] in ('voice','image'))):
                        db.execute('DELETE FROM messages WHERE id=?', (echo['id'],))
                        self.event(db, 'message.removed', row['contact_id'], message_id=echo['id'])
                db.execute('UPDATE messages SET status=?,error=?,source_key=COALESCE(?,source_key) WHERE id=?',
                           (status, error, source_key, message_id))
                self.event(db, 'message.updated', row['contact_id'], message_id=message_id)
                if row['ai_run_id'] and row['origin']=='ai' and status in ('confirmed','uncertain','failed','cancelled'):
                    run=db.execute('SELECT status FROM ai_runs WHERE id=?',(row['ai_run_id'],)).fetchone()
                    if reconciled and run and run['status'] not in ('uncertain','queued','sent'):
                        return  # Late evidence must not undo manual cancellation/takeover.
                    remaining = db.execute("SELECT id,status FROM messages WHERE ai_run_id=? AND origin='ai' AND id!=?",
                                           (row['ai_run_id'], message_id)).fetchall()
                    if status == 'confirmed' and any(part['status'] != 'confirmed' for part in remaining):
                        if any(part['status']=='queued' for part in remaining):
                            db.execute("UPDATE ai_runs SET status='queued',error='',updated_at=? WHERE id=?",(now(),row['ai_run_id']))
                            self.event(db,'ai.run',row['contact_id'],run_id=row['ai_run_id'])
                        return
                    receipt=db.execute('SELECT attempted_at FROM send_receipts WHERE message_id=?',(message_id,)).fetchone()
                    recovering=status=='uncertain' and receipt and receipt['attempted_at']
                    if status != 'confirmed' and not recovering:
                        for part in remaining:
                            if part['status'] == 'queued':
                                db.execute("UPDATE messages SET status='cancelled',error=? WHERE id=?",
                                           ('同一回复前段未确认送达，后续已停止', part['id']))
                                self.event(db, 'message.updated', row['contact_id'], message_id=part['id'])
                    db.execute('UPDATE ai_runs SET status=?,error=?,updated_at=? WHERE id=?',
                               ('sent' if status=='confirmed' else status,error,now(),row['ai_run_id']))
                    self.event(db,'ai.run',row['contact_id'],run_id=row['ai_run_id'])

    def send_receipt(self, message_id, stage, baseline=None, before_count=0):
        """Write intent before the single click; a restart must never resend it."""
        with self.connect() as db:
            if stage=='prepared':
                clean=[{k:v for k,v in i.items() if k not in ('image_data',)} for i in baseline or []]
                db.execute('INSERT OR REPLACE INTO send_receipts VALUES(?,?,?,NULL)',
                           (message_id,json.dumps(clean,ensure_ascii=False),before_count))
            elif stage=='attempted':
                db.execute('UPDATE send_receipts SET attempted_at=? WHERE message_id=?',(now(),message_id))

    def awaiting_receipt(self, message):
        if not message.get('ai_run_id'):return False
        with self.connect() as db:
            row=db.execute('''SELECT r.attempted_at FROM send_receipts r JOIN messages m ON m.id=r.message_id
                WHERE m.ai_run_id=? AND m.status='uncertain' ORDER BY m.id LIMIT 1''',(message['ai_run_id'],)).fetchone()
        return bool(row and row[0] and (datetime.now(timezone.utc)-datetime.fromisoformat(row[0])).total_seconds()<120)

    def reconcile_receipts(self, contact_id, items):
        from .send_receipts import match
        with self.connect() as db:
            rows=[dict(r) for r in db.execute('''SELECT m.*,r.baseline,r.before_count,r.attempted_at
                FROM send_receipts r JOIN messages m ON m.id=r.message_id
                WHERE m.contact_id=? AND m.status='uncertain' AND r.attempted_at IS NOT NULL
                ORDER BY m.id DESC LIMIT 10''',(contact_id,))]
        for row in rows:
            if row['kind'] not in ('text','file','image'):continue
            try:
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(row['attempted_at'])).total_seconds()
                if not 0<=age<=120:continue
                found=match(json.loads(row['baseline']),items,row['text'],row['before_count'],kind=row['kind'])
            except (TypeError,ValueError,KeyError):
                continue  # Corrupt/stale intent is not evidence of delivery.
            if not found:continue
            with self.connect() as db:
                claimed=db.execute('''SELECT 1 FROM messages WHERE contact_id=? AND source_key=?
                    AND id!=? AND status!='observed' ''',(contact_id,found['key'],row['id'])).fetchone()
            if claimed:continue  # One observed bubble can confirm only one send.
            note=('消息已核对发送；'+row['error'][row['error'].find('原草稿'):]
                  if '恢复备份' in row['error'] and '原草稿' in row['error'] else '')
            self.set_message_state(row['id'],'confirmed',error=note,source_key=found['key'],reconciled=True)
            with self.connect() as db:
                confirmed=db.execute("SELECT 1 FROM messages WHERE id=? AND status='confirmed' AND source_key=?",
                                     (row['id'],found['key'])).fetchone()
                if confirmed:self.event(db,'send.recovered',contact_id,message_id=row['id'],source_key=found['key'],detail='后台已核对新增本人消息，发送状态已自动恢复')

    def ai_send_valid(self, message):
        if not self.account_matches(self.contact(message['contact_id'])):return False
        if message.get('origin')=='approval':
            from .hermes_confirmations import valid_notice
            return valid_notice(self,message)
        if message.get('origin') not in ('ai','ai_progress'):return True
        progress = message.get('origin') == 'ai_progress'
        with self.connect() as db:
            row=db.execute('''SELECT r.*,c.mode current_mode,c.ai_agent_id,c.ai_revision,c.listening FROM ai_runs r
                JOIN contacts c ON c.id=r.contact_id WHERE r.id=?''',(message.get('ai_run_id'),)).fetchone()
            if not row:return False
            from .ai_store import AIStore
            state=AIStore.decode_state(row['state'])
            if state.get('agent_schedule'):
                from .agent_schedules import task_valid
                return row['status']=='queued' and task_valid(self,dict(row)|{'state':state})
            if row['schedule_id']:
                from .companion_scheduler import schedule_valid
                return row['status']=='queued' and schedule_valid(self,dict(row))
            settings=json.loads(db.execute("SELECT value FROM settings WHERE key='ai_settings'").fetchone()[0])
            from .conversation_turn import latest_id
            from .ai_store import AIStore
            latest=latest_id(db,row['contact_id'],state['snapshot']['agent'],group_sender=state.get('group_sender'))
            current=(row['resume_message_id'] or row['trigger_id'])==latest
            if state.get('conversation_queue'):
                from .conversation_queue import current as queued_current
                contact=db.execute('SELECT * FROM contacts WHERE id=?',(row['contact_id'],)).fetchone()
                current=queued_current(db,contact,row)
            paused=db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='true'
            return (not paused and settings['enabled'] and row['listening'] and row['status'] in (('running','queued') if progress else ('queued',))
                    and row['mode']==row['current_mode'] and row['agent_id']==row['ai_agent_id']
                    and row['revision']==row['ai_revision'] and current)

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE messages SET status='uncertain',error='服务重启，发送结果需要核对' WHERE status='sending'")
            db.execute("UPDATE messages SET status='cancelled',error='服务重启，未执行的发送已取消' WHERE status='queued'")
            db.execute("UPDATE contacts SET listener_state='idle' WHERE listening=0")
            db.execute("UPDATE contacts SET listener_state='starting' WHERE listening=1")

    def pending(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM messages WHERE status='queued' ORDER BY id")]

    def _agent_message(self,db,contact_id,message_id,text,account_id=None):
        from .group_chats import qualifies
        contact=db.execute('SELECT * FROM contacts WHERE id=?',(contact_id,)).fetchone()
        message=db.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if contact and message and not message['text_pending'] and qualifies(dict(contact),dict(message)):
            self.event(db,'agent.message',contact_id,message_id=message_id,text=text,account_id=account_id)

    def ingest(self, contact_id, items):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM contacts WHERE id=?',(contact_id,)).fetchone():return
            prior=db.execute('SELECT data FROM snapshots WHERE contact_id=?',(contact_id,)).fetchone()
            previous=json.loads(prior[0]) if prior else None
            items=[dict(item) for item in items]
            for item in items:
                if not item.get('sender_verified'):continue
                collision=next((old for old in previous or [] if old['key']==item['key']
                    and old.get('sender_verified') and old.get('sender')!=item.get('sender')),None)
                if collision:
                    item['raw_key']=item.get('raw_key',item['key'])
                    item['key']+=':member:'+hashlib.sha256(item['sender'].encode()).hexdigest()[:16]
            items=align(previous,items)
            delta=compare(previous,items)
            if delta.gap and any(item.get('native_fresh') for item in items):delta.gap=False
            if delta.gap:
                db.execute('INSERT INTO snapshot_pending VALUES(?,1) ON CONFLICT(contact_id) DO UPDATE SET attempts=attempts+1',(contact_id,))
                attempts=db.execute('SELECT attempts FROM snapshot_pending WHERE contact_id=?',(contact_id,)).fetchone()[0]
                if attempts<3:return  # Ignore transient empty/recycled frames; retain cursor.
            else:
                db.execute('DELETE FROM snapshot_pending WHERE contact_id=?',(contact_id,))
                error=db.execute('SELECT error FROM contacts WHERE id=?',(contact_id,)).fetchone()
                if error and error[0].startswith('窗口历史发生变化'):
                    db.execute("UPDATE contacts SET error='' WHERE id=?",(contact_id,))
                    self.event(db,'listener.recovered',contact_id,detail='已自动对齐消息并恢复监听；历史中断记录保留在日志中')
        media={}
        for item in items:
            if item.get('sender_verified') and item.get('sender_avatar_data'):
                import base64
                self.set_member_avatar(contact_id, item['sender'], base64.b64decode(item['sender_avatar_data'], validate=True))
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
            # previous/delta were checked before media handling; the serial pump
            # is the only snapshot writer.
            for item in delta.items:
                historical=delta.historical and not item.get('native_fresh',False)
                cursor = db.execute('''INSERT OR IGNORE INTO messages
                    (contact_id,direction,text,kind,status,created_at,source_key,historical,direction_verified,media_id,text_pending,error,
                     sender,sender_verified,mention_me,mention_verified,mention_source,quote)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (contact_id, item['direction'], item['text'],
                    item.get('kind','text'), 'observed', now(), item['key'], historical,bool(item.get('direction_verified')),media.get(item['key'],''),bool(item.get('text_pending')),item.get('text_error',''),
                    item.get('sender',''),bool(item.get('sender_verified')),bool(item.get('mention_me')),bool(item.get('mention_verified')),item.get('mention_source',''),json.dumps(item.get('quote',{}),ensure_ascii=False)))
                if cursor.rowcount:
                    self.event(db, 'message.updated', contact_id, message_id=cursor.lastrowid)
                    if not historical and item['direction'] == 'incoming':
                        db.execute('UPDATE contacts SET unread=unread+1 WHERE id=?', (contact_id,))
                        if (item.get('kind') == 'text' and not item.get('text_pending')) or (item.get('kind')=='image' and media.get(item['key'])):
                            self._agent_message(db,contact_id,cursor.lastrowid,item['text'],item.get('account_id'))
                        elif item.get('kind')=='image':
                            self.event(db,'agent.attachment',contact_id,message_id=cursor.lastrowid)
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
                if item.get('quote') and (json.loads(row['quote'] or '{}')!=item['quote'] or row['text']!=item['text']):
                    db.execute('UPDATE messages SET quote=?,text=? WHERE id=?',(json.dumps(item['quote'],ensure_ascii=False),item['text'],row['id']))
                    self.event(db,'message.updated',contact_id,message_id=row['id'])
                if item.get('sender_verified') and (not row['sender_verified'] or row['sender']==item.get('sender')):
                    metadata=(item.get('sender',''),True,bool(item.get('mention_me')),bool(item.get('mention_verified')),item.get('mention_source',''))
                    if metadata != tuple(row[k] for k in ('sender','sender_verified','mention_me','mention_verified','mention_source')):
                        db.execute('''UPDATE messages SET sender=?,sender_verified=?,mention_me=?,mention_verified=?,mention_source=? WHERE id=?''',(*metadata,row['id']))
                        self.event(db,'message.updated',contact_id,message_id=row['id'])
                        enriched=dict(row)|dict(zip(('sender','sender_verified','mention_me','mention_verified','mention_source'),metadata))
                        from .group_chats import qualifies
                        contact=dict(db.execute('SELECT * FROM contacts WHERE id=?',(contact_id,)).fetchone())
                        if (direction=='incoming' and not row['historical'] and qualifies(contact,enriched)
                                and not db.execute("SELECT 1 FROM events WHERE type='agent.message' AND contact_id=? AND json_extract(payload,'$.message_id')=?",(contact_id,row['id'])).fetchone()):
                            self._agent_message(db,contact_id,row['id'],item['text'],item.get('account_id'))
                if not (row['origin']=='ai' and row['kind']=='voice') and 'text_pending' in item and (row['text']!=item['text'] or bool(row['text_pending'])!=bool(item['text_pending']) or row['error']!=item.get('text_error','')):
                    # Enrich the original UI row instead of inserting/replaying it.
                    db.execute('UPDATE messages SET text=?,text_pending=?,error=? WHERE id=?',
                               (item['text'],bool(item['text_pending']),item.get('text_error',''),row['id']))
                    self.event(db,'message.updated',contact_id,message_id=row['id'])
                    if (not item['text_pending'] and direction=='incoming' and not row['historical']
                            and not db.execute("SELECT 1 FROM events WHERE contact_id=? AND type='agent.message' AND json_extract(payload,'$.message_id')=?",(contact_id,row['id'])).fetchone()):
                        self._agent_message(db,contact_id,row['id'],item['text'],item.get('account_id'))
                if item['key'] in media and not row['media_id']:
                    db.execute("UPDATE messages SET media_id=?,error='' WHERE id=?",(media[item['key']],row['id']))
                    self.event(db,'message.updated',contact_id,message_id=row['id'])
                    if row['direction']=='incoming' and not row['historical']:
                        self._agent_message(db,contact_id,row['id'],row['text'],item.get('account_id'))
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
                            if ((row['kind']=='text' and not item.get('text_pending',row['text_pending'])) or (row['kind']=='image' and (row['media_id'] or media.get(item['key'])))) and not db.execute("SELECT 1 FROM events WHERE contact_id=? AND type='agent.message' AND json_extract(payload,'$.message_id')=?",
                                                                   (contact_id,row['id'])).fetchone():
                                self._agent_message(db,contact_id,row['id'],item['text'],item.get('account_id'))
                        elif row['direction']=='incoming':
                            db.execute('UPDATE contacts SET unread=MAX(0,unread-1) WHERE id=?',(contact_id,))
                elif verified and not row['direction_verified'] and row['direction']==direction:
                    db.execute('UPDATE messages SET direction_verified=1 WHERE id=?',(row['id'],))
            # A temporarily empty/unreadable window must not erase our cursor and
            # turn old messages into new Agent events when it reappears.
            if (items or not prior) and not delta.retain:
                db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?)',
                           (contact_id, json.dumps([{k:v for k,v in item.items() if k not in ('image_data','sender_avatar_data')} for item in items], ensure_ascii=False)))
            if delta.gap:
                detail = '窗口历史发生变化；已重新建立监听基线，中断区间是否完整需要核对'
                error = db.execute('SELECT error FROM contacts WHERE id=?', (contact_id,)).fetchone()[0]
                if error != detail:
                    db.execute('UPDATE contacts SET error=? WHERE id=?', (detail, contact_id))
                    self.event(db, 'listener.gap', contact_id, detail=detail)
        self.reconcile_receipts(contact_id,items)

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
