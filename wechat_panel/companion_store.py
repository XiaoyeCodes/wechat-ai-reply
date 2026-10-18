"""Encrypted, isolated companion memory and durable care schedules."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import uuid
from zoneinfo import ZoneInfo

from .ai_store import protect, unprotect, tokens
from .companion_models import MemoryEntry, PersonProfile, Schedule
from .store import now


def utcnow():return datetime.now(timezone.utc)


class CompanionStore:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS companion_profiles(
                    scope TEXT PRIMARY KEY,agent_id TEXT NOT NULL,contact_id TEXT,
                    data TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',mood TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 0,forget_before INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS companion_memories(
                    id TEXT PRIMARY KEY,scope TEXT NOT NULL,category TEXT NOT NULL,fact_key TEXT NOT NULL,
                    content TEXT NOT NULL,pinned INTEGER NOT NULL,source_id INTEGER,
                    expires_at TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    UNIQUE(scope,fact_key));
                CREATE TABLE IF NOT EXISTS companion_forgotten(scope TEXT NOT NULL,fact_key TEXT NOT NULL,
                    PRIMARY KEY(scope,fact_key));
                CREATE TABLE IF NOT EXISTS companion_turns(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,role TEXT NOT NULL,
                    content TEXT NOT NULL,run_id TEXT NOT NULL,created_at TEXT NOT NULL,
                    UNIQUE(scope,run_id,role));
                CREATE TABLE IF NOT EXISTS companion_media(
                    id TEXT PRIMARY KEY,scope TEXT NOT NULL,kind TEXT NOT NULL,mime TEXT NOT NULL,
                    data TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS companion_schedules(
                    id TEXT PRIMARY KEY,data TEXT NOT NULL,next_at TEXT NOT NULL,
                    last_status TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS companion_occurrences(
                    id TEXT PRIMARY KEY,schedule_id TEXT NOT NULL,due_at TEXT NOT NULL,run_id TEXT,
                    status TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(schedule_id,due_at));
                CREATE TABLE IF NOT EXISTS companion_daily_context(
                    scope TEXT PRIMARY KEY,cache_key TEXT NOT NULL,data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS companion_image_observations(
                    scope TEXT NOT NULL,media_id TEXT NOT NULL,model_key TEXT NOT NULL,
                    description TEXT NOT NULL,created_at TEXT NOT NULL,
                    PRIMARY KEY(scope,media_id,model_key));
            ''')
            cols={r['name'] for r in db.execute('PRAGMA table_info(companion_memories)')}
            if 'key_hash' not in cols:
                db.execute('ALTER TABLE companion_memories ADD COLUMN key_hash TEXT')
                for row in db.execute('SELECT id,scope,fact_key FROM companion_memories').fetchall():
                    hashed=hashlib.sha256((row['scope']+':'+unprotect(row['fact_key'])).encode()).hexdigest()
                    db.execute('UPDATE companion_memories SET key_hash=? WHERE id=?',(hashed,row['id']))
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS memory_fact_identity ON companion_memories(scope,key_hash)')

    def scope(self,agent_id,contact_id=None,session_id=None):
        if contact_id:
            c=self.store.contact(contact_id)
            identity='contact:'+c['account_id']+':'+c['id']
        else:
            if not session_id or not isinstance(session_id,str) or len(session_id)>100:
                raise ValueError('测试记忆需要独立会话 ID')
            identity='sandbox:'+session_id
        return hashlib.sha256((agent_id+':'+identity).encode()).hexdigest()

    def daily_context(self,scope,cache_key):
        with self.store.connect() as db:
            row=db.execute('SELECT data FROM companion_daily_context WHERE scope=? AND cache_key=?',
                           (scope,cache_key)).fetchone()
        return json.loads(unprotect(row['data'])) if row else None

    def save_daily_context(self,scope,cache_key,data):
        with self.store.connect() as db:
            db.execute('INSERT INTO companion_daily_context VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET cache_key=excluded.cache_key,data=excluded.data',
                       (scope,cache_key,protect(json.dumps(data,ensure_ascii=False))))

    def profile(self,scope,agent_id='',contact_id=None):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM companion_profiles WHERE scope=?',(scope,)).fetchone()
            if not row:
                db.execute('INSERT INTO companion_profiles(scope,agent_id,contact_id,data,updated_at) VALUES(?,?,?,?,?)',
                           (scope,agent_id,contact_id,protect(json.dumps(PersonProfile().model_dump())),now()))
                row=db.execute('SELECT * FROM companion_profiles WHERE scope=?',(scope,)).fetchone()
        result=dict(row)
        for k in ('data','summary','mood'):result[k]=unprotect(result[k]) if result[k] else ''
        result['data']=json.loads(result['data'])
        return result

    def set_profile(self,scope,data):
        value=PersonProfile.model_validate(data).model_dump()
        with self.store.connect() as db:
            db.execute('UPDATE companion_profiles SET data=?,revision=revision+1,updated_at=? WHERE scope=?',
                       (protect(json.dumps(value,ensure_ascii=False)),now(),scope))
        return self.profile(scope)

    def list_memories(self,scope):
        stamp=now()
        with self.store.connect() as db:
            db.execute('DELETE FROM companion_memories WHERE scope=? AND pinned=0 AND expires_at<=?',(scope,stamp))
            rows=db.execute('SELECT * FROM companion_memories WHERE scope=? ORDER BY pinned DESC,updated_at DESC LIMIT 500',(scope,)).fetchall()
        return [{**dict(r),'content':unprotect(r['content']),'key':unprotect(r['fact_key'])} for r in rows]

    def save_memory(self,scope,data,source_id=None,days=180,mid=None,revision=None):
        value=MemoryEntry.model_validate(data).model_dump()
        key=value['key'] or value['content'][:80]
        # Keys are encrypted too; use a separate deterministic lookup hash.
        lookup=hashlib.sha256((scope+':'+key).encode()).hexdigest()
        expiry=value['expires_at'] or (utcnow()+timedelta(days=days)).isoformat()
        try:expiry=datetime.fromisoformat(expiry).astimezone(timezone.utc).isoformat()
        except ValueError:raise ValueError('无效的记忆过期时间') from None
        with self.store.connect() as db:
            profile=db.execute('SELECT revision FROM companion_profiles WHERE scope=?',(scope,)).fetchone()
            if revision is not None and (not profile or profile[0]!=revision):return None
            if source_id is not None and db.execute('SELECT 1 FROM companion_forgotten WHERE scope=? AND fact_key=?',(scope,lookup)).fetchone():return None
            if source_id is None:db.execute('DELETE FROM companion_forgotten WHERE scope=? AND fact_key=?',(scope,lookup))
            if mid:
                if not db.execute('SELECT 1 FROM companion_memories WHERE id=? AND scope=?',(mid,scope)).fetchone():raise KeyError('记忆不存在')
            else:
                # Fact keys are kept inside the protected payload, so compare after decrypting.
                existing=[r for r in db.execute('SELECT id,fact_key FROM companion_memories WHERE scope=?',(scope,)) if unprotect(r['fact_key'])==key]
                mid=existing[0]['id'] if existing else str(uuid.uuid4())
            collision=db.execute('SELECT id FROM companion_memories WHERE scope=? AND key_hash=?',(scope,lookup)).fetchone()
            if collision and collision['id']!=mid:raise ValueError('该记忆字段已存在，请直接编辑已有记录')
            db.execute('INSERT INTO companion_memories VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET category=excluded.category,fact_key=excluded.fact_key,key_hash=excluded.key_hash,content=excluded.content,pinned=excluded.pinned,source_id=excluded.source_id,expires_at=excluded.expires_at,updated_at=excluded.updated_at',
                (mid,scope,value['category'],protect(key),protect(value['content']),int(value['pinned']),source_id,expiry,now(),now(),lookup))
        return mid

    def forget(self,scope,mid=None):
        with self.store.connect() as db:
            rows=db.execute('SELECT * FROM companion_memories WHERE scope=?'+(' AND id=?' if mid else ''),(scope,mid) if mid else (scope,)).fetchall()
            for r in rows:
                lookup=hashlib.sha256((scope+':'+unprotect(r['fact_key'])).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO companion_forgotten VALUES(?,?)',(scope,lookup))
                db.execute('DELETE FROM companion_memories WHERE id=?',(r['id'],))
            profile=db.execute('SELECT contact_id FROM companion_profiles WHERE scope=?',(scope,)).fetchone()
            boundary=db.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE contact_id=?',(profile[0] if profile else None,)).fetchone()[0]
            db.execute("UPDATE companion_profiles SET revision=revision+1,summary='',mood='',forget_before=?,updated_at=? WHERE scope=?",(boundary,now(),scope))
            # Prevent the old transcript from silently re-creating deleted facts.
            db.execute('DELETE FROM companion_turns WHERE scope=?',(scope,))
            db.execute('DELETE FROM companion_daily_context WHERE scope=?',(scope,))
            db.execute('DELETE FROM companion_image_observations WHERE scope=?',(scope,))
        return len(rows)

    def retrieve(self,scope,query,limit=6):
        keys=set(tokens(query));rows=self.list_memories(scope)
        for r in rows:r['score']=len(keys & set(tokens(r['key']+' '+r['content'])))+int(r['pinned'])*2
        rows.sort(key=lambda r:(r['score'],r['updated_at']),reverse=True)
        return rows[:limit]

    def update_context(self,scope,revision,summary,mood):
        with self.store.connect() as db:
            db.execute('UPDATE companion_profiles SET summary=?,mood=?,updated_at=? WHERE scope=? AND revision=?',
                       (protect(summary[:5000]),protect(mood[:2000]),now(),scope,revision))

    def turn(self,scope,run_id,role,content):
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO companion_turns(scope,role,content,run_id,created_at) VALUES(?,?,?,?,?)',
                       (scope,role,protect(content[:16000]),run_id,now()))
            db.execute('DELETE FROM companion_turns WHERE scope=? AND id NOT IN (SELECT id FROM companion_turns WHERE scope=? ORDER BY id DESC LIMIT 100)',(scope,scope))

    def conversation(self,scope,limit=20):
        with self.store.connect() as db:rows=db.execute('SELECT * FROM companion_turns WHERE scope=? ORDER BY id DESC LIMIT ?',(scope,limit)).fetchall()
        return [{'role':r['role'],'content':unprotect(r['content'])} for r in reversed(rows)]

    def media(self,scope,data,kind='image',mime='image/png'):
        if len(data)>8_000_000:raise ValueError('媒体文件最多 8 MB')
        if kind=='image':
            from PIL import Image
            image=Image.open(BytesIO(data))
            if image.width*image.height>20_000_000:raise ValueError('图片尺寸过大')
            image=image.convert('RGBA' if image.mode in ('RGBA','LA') or 'transparency' in image.info else 'RGB');image.thumbnail((1536,1536))
            output=BytesIO();image.save(output,format='PNG');data=output.getvalue();mime='image/png'
        elif mime not in ('audio/mpeg','audio/wav','audio/flac'):raise ValueError('不支持的音频格式')
        mid=str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute('INSERT INTO companion_media VALUES(?,?,?,?,?,?)',(mid,scope,kind,mime,protect(base64.b64encode(data).decode()),now()))
        return {'id':mid,'kind':kind,'mime':mime,'url':'/api/ai/media/'+mid}

    def read_media(self,mid,scope=None):
        with self.store.connect() as db:row=db.execute('SELECT * FROM companion_media WHERE id=?',(mid,)).fetchone()
        if not row or (scope and row['scope']!=scope):raise KeyError('媒体不存在或不属于当前会话')
        return {**dict(row),'data':base64.b64decode(unprotect(row['data']))}

    def image_observation(self,scope,mid,model_key):
        with self.store.connect() as db:
            row=db.execute('SELECT description FROM companion_image_observations WHERE scope=? AND media_id=? AND model_key=?',
                           (scope,mid,model_key)).fetchone()
        return unprotect(row[0]) if row else None

    def save_image_observation(self,scope,mid,model_key,description,revision):
        with self.store.connect() as db:
            profile=db.execute('SELECT revision FROM companion_profiles WHERE scope=?',(scope,)).fetchone()
            if not profile or profile[0]!=revision:return
            db.execute('INSERT OR REPLACE INTO companion_image_observations VALUES(?,?,?,?,?)',
                       (scope,mid,model_key,protect(description[:4000]),now()))
            db.execute('DELETE FROM companion_image_observations WHERE scope=? AND rowid NOT IN '
                       '(SELECT rowid FROM companion_image_observations WHERE scope=? ORDER BY created_at DESC LIMIT 80)',(scope,scope))

    def schedules(self):
        with self.store.connect() as db:rows=db.execute('SELECT * FROM companion_schedules ORDER BY created_at DESC').fetchall()
        return [{**json.loads(unprotect(r['data'])),'id':r['id'],'next_at':r['next_at'],'last_status':r['last_status']} for r in rows]

    @staticmethod
    def next_time(value,after=None):
        if value['kind']=='once':return datetime.fromisoformat(value['at']).astimezone(timezone.utc).isoformat()
        from croniter import croniter
        return croniter(value['cron'],(after or utcnow()).astimezone(ZoneInfo(value['timezone']))).get_next(datetime).astimezone(timezone.utc).isoformat()

    def save_schedule(self,data,sid=None):
        value=Schedule.model_validate(data).model_dump();self.store.contact(value['contact_id'])
        sid=sid or str(uuid.uuid4());next_at=self.next_time(value)
        with self.store.connect() as db:
            db.execute('INSERT INTO companion_schedules VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,next_at=excluded.next_at,last_status=excluded.last_status,updated_at=excluded.updated_at',
                       (sid,protect(json.dumps(value,ensure_ascii=False)),next_at,'等待执行' if value['enabled'] else '已停用',now(),now()))
        return next(x for x in self.schedules() if x['id']==sid)

    def delete_schedule(self,sid):
        with self.store.connect() as db:db.execute('DELETE FROM companion_schedules WHERE id=?',(sid,))
