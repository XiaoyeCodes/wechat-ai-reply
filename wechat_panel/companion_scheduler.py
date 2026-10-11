"""Persistent care scheduling; each occurrence is claimed before model work."""
from datetime import datetime, timezone
import json
import time
import uuid
from zoneinfo import ZoneInfo

from .ai_store import unprotect
from .companion import companion_config
from .companion_store import CompanionStore
from .store import now


def quiet(c,stamp):
    current=stamp.astimezone(ZoneInfo(c['timezone'])).strftime('%H:%M')
    start,end=c['quiet_start'],c['quiet_end']
    return start<=current<end if start<end else current>=start or current<end if start!=end else False


def schedule_valid(store,run):
    try:
        contact=store.contact(run['contact_id'])
        with store.connect() as db:
            record=db.execute('SELECT data FROM companion_schedules WHERE id=?',(run['schedule_id'],)).fetchone()
            if not record:return False
            schedule=json.loads(unprotect(record['data']))
            resource=db.execute("SELECT data FROM ai_resources WHERE kind='agents' AND id=?",(run['agent_id'],)).fetchone()
            if not resource:return False
            agent=json.loads(resource['data']);c=companion_config(agent)
            settings=json.loads(db.execute("SELECT value FROM settings WHERE key='ai_settings'").fetchone()[0])
            baseline=db.execute("SELECT COALESCE(MAX(id),0) FROM messages WHERE contact_id=? AND origin!='ai' AND direction IN ('incoming','outgoing')",(contact['id'],)).fetchone()[0]
            profiles=db.execute('SELECT data FROM companion_profiles WHERE agent_id=? AND contact_id=?',(run['agent_id'],contact['id'])).fetchall()
        consent=any(json.loads(unprotect(p['data'])).get('care_consent') for p in profiles)
        return (not store.paused() and settings['enabled'] and agent['enabled'] and schedule['enabled']
            and c['care']['enabled'] and consent and contact['listening'] and contact['mode'] in ('ai_auto','ai_assist')
            and contact['mode']==run['mode'] and contact['ai_agent_id']==run['agent_id'] and contact['ai_revision']==run['revision']
            and baseline==run['baseline_id'] and not quiet(c['care'],datetime.now(timezone.utc)))
    except (KeyError,ValueError):return False


class CareScheduler:
    def __init__(self,ai):self.ai=ai;self.last_tick=0

    def tick(self,stamp=None):
        if stamp is None and time.monotonic()-self.last_tick<10:return
        self.last_tick=time.monotonic();stamp=stamp or datetime.now(timezone.utc)
        for s in self.ai.companion.memory.schedules():
            if not s['enabled'] or not s['next_at']:continue
            due=datetime.fromisoformat(s['next_at'])
            if due>stamp:continue
            oid=str(uuid.uuid4())
            with self.ai.store.connect() as db:
                inserted=db.execute('INSERT OR IGNORE INTO companion_occurrences VALUES(?,?,?,?,?,?)',
                    (oid,s['id'],s['next_at'],None,'claimed',now())).rowcount
                next_at=CompanionStore.next_time(s,stamp) if s['kind']=='cron' else ''
                db.execute('UPDATE companion_schedules SET next_at=?,updated_at=? WHERE id=?',(next_at,now(),s['id']))
            if not inserted:continue
            status='';run=None
            try:
                c=self.ai.store.contact(s['contact_id']);agent=self.ai.data.get('agents',s['agent_id'],private=True)
                config=companion_config(agent);care=config['care']
                scope=self.ai.companion.memory.scope(s['agent_id'],c['id'])
                profile=self.ai.companion.memory.profile(scope,s['agent_id'],c['id'])['data']
                if (stamp-due).total_seconds()>300:status='已跳过过期日程，避免重启后集中打扰'
                elif self.ai.store.paused() or not self.ai.data.settings()['enabled']:status='已跳过：全部运行或 AI 已暂停'
                elif not agent['enabled'] or not care['enabled'] or not profile['care_consent']:status='已跳过：未开启该联系人的主动关怀'
                elif not c['listening'] or c['ai_agent_id']!=s['agent_id'] or c['mode'] not in ('ai_auto','ai_assist'):status='已跳过：联系人未启用对应 Agent'
                elif quiet(care,stamp):status='已跳过：免打扰时间'
                else:
                    with self.ai.store.connect() as db:
                        recent=db.execute("SELECT created_at,status FROM ai_runs WHERE contact_id=? AND schedule_id!='' AND status NOT IN ('failed','cancelled') ORDER BY created_at DESC",(c['id'],)).fetchall()
                        active=db.execute("SELECT 1 FROM ai_runs WHERE contact_id=? AND status IN ('pending','running','queued','waiting_approval')",(c['id'],)).fetchone()
                    localdate=stamp.astimezone(ZoneInfo(care['timezone'])).date()
                    count=sum(datetime.fromisoformat(r['created_at']).astimezone(ZoneInfo(care['timezone'])).date()==localdate for r in recent)
                    if active:status='已跳过：正在处理该联系人的对话'
                    elif count>=care['daily_limit']:status='已跳过：达到每日关怀上限'
                    elif recent and (stamp-datetime.fromisoformat(recent[0]['created_at'])).total_seconds()<care['min_gap_minutes']*60:status='已跳过：距离上次关怀太近'
                    else:
                        run=self.ai.create_run(s['agent_id'],'主动关怀任务：'+s['instruction']+'\n自然地表达关心，不谎称知道尚未确认的结果；只输出要发给用户的文字。',c['id'],schedule=s)
                        status='已创建关怀任务'
            except Exception:status='日程执行失败，请检查联系人和 Agent 配置'
            with self.ai.store.connect() as db:
                db.execute('UPDATE companion_occurrences SET status=?,run_id=? WHERE id=?',(status,run['id'] if run else None,oid))
                db.execute('UPDATE companion_schedules SET last_status=?,updated_at=? WHERE id=?',(status,now(),s['id']))
                self.ai.store.event(db,'ai.schedule',s['contact_id'],schedule_id=s['id'],detail=status)
