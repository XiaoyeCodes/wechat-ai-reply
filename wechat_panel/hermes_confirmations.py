"""One-time, contact-bound WeChat confirmations for Hermes approvals."""
import json
import re
import secrets
import time


def parse_reply(text):
    match=re.fullmatch(r'\s*(确认|取消)(?:\s*([A-Fa-f0-9]{6}))?\s*',text or '')
    return (match[1]=='确认',match[2].upper() if match[2] else None) if match else None


def reply_receipt(db,contact_id,reply):
    if reply[1]:
        return db.execute('SELECT * FROM hermes_confirmations WHERE code=? AND contact_id=?',(reply[1],contact_id)).fetchone()
    rows=db.execute("SELECT c.* FROM hermes_confirmations c JOIN ai_runs r ON r.id=c.run_id WHERE c.contact_id=? AND c.panel_only=0 AND c.status='pending' AND c.expires>? AND r.status IN ('waiting_approval','running','pending')",
                    (contact_id,time.time())).fetchall()
    if rows:return rows[0] if len(rows)==1 else None
    # Consume a duplicate confirmation while the same task is still finishing.
    rows=db.execute("SELECT c.* FROM hermes_confirmations c JOIN ai_runs r ON r.id=c.run_id WHERE c.contact_id=? AND c.panel_only=0 AND c.status='approved' AND r.status IN ('waiting_approval','running','pending','queued') ORDER BY c.expires DESC",
                    (contact_id,)).fetchall()
    return rows[0] if rows and len({r['run_id'] for r in rows})==1 else None


def describe_operation(args):
    description=args.get('description') or json.dumps(args,ensure_ascii=False)
    prefix='电脑操作需要确认。'
    if description.startswith(prefix):
        try:
            operation=json.loads(description[len(prefix):]);name=operation['工具']
            labels={'write_file':'写入文件','patch':'修改文件','terminal':'运行命令',
                    'browser_exec':'执行浏览器脚本','computer_use':'操作电脑界面','delegate_task':'委派电脑任务'}
            return '操作：'+labels.get(name,name)+'\n工具：'+name+'\n参数：\n'+json.dumps(operation['参数'],ensure_ascii=False,indent=2)
        except (ValueError,KeyError,TypeError):pass
    if args.get('command') and not str(args['command']).startswith('<'):description+='\n'+args['command']
    return description


def pending_reply(db,run,message_id):
    if not message_id or run['status'] not in ('waiting_approval','running','pending','queued'):return False
    source=db.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
    if not source or source['direction']!='incoming' or source['historical'] or not source['direction_verified']:return False
    reply=parse_reply(source['text'])
    if not reply:return False
    receipt=reply_receipt(db,source['contact_id'],reply)
    return bool(receipt and receipt['run_id']==run['id'] and not receipt['panel_only']
        and (receipt['status']=='approved' or (receipt['status']=='pending' and receipt['expires']>time.time()))
        and 0<receipt['notice_id']<message_id)


def valid_notice(store,message):
    with store.connect() as db:
        run=db.execute('SELECT * FROM ai_runs WHERE id=?',(message['ai_run_id'],)).fetchone()
        receipt=db.execute('SELECT * FROM hermes_confirmations WHERE notice_id=? OR response_id=?',(message['id'],message['id'])).fetchone()
        if not run or not receipt:return False
        contact=db.execute('SELECT * FROM contacts WHERE id=?',(run['contact_id'],)).fetchone()
        enabled=json.loads(db.execute("SELECT value FROM settings WHERE key='ai_settings'").fetchone()[0])['enabled']
        paused=db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]=='true'
        if (not contact or paused or not enabled or not contact['listening'] or contact['ai_agent_id']!=run['agent_id']
                or contact['ai_revision']!=run['revision'] or contact['mode']!=run['mode']):return False
        latest=db.execute("SELECT MAX(id) FROM messages WHERE contact_id=? AND direction='incoming' AND historical=0",(contact['id'],)).fetchone()[0]
        if latest!=(run['resume_message_id'] or run['trigger_id']):return False
        if receipt['notice_id']==message['id']:
            return run['status']=='waiting_approval' and receipt['status']=='pending' and receipt['expires']>time.time()
        return receipt['status'] in ('rejected','expired') and run['status']=='cancelled' and time.time()<receipt['expires']+120


class WeChatConfirmations:
    def __init__(self,ai):self.ai=ai

    def notify(self,run,call_id):
        agent=run['state']['snapshot']['agent']
        if run['dry_run'] or not run['contact_id'] or agent.get('hermes_approval_channel','panel')!='wechat':return
        with self.ai.store.connect() as db:
            approval=db.execute('SELECT * FROM ai_approvals WHERE run_id=? AND call_id=?',(run['id'],call_id)).fetchone()
            receipt=db.execute('SELECT * FROM hermes_confirmations WHERE id=?',(approval['id'],)).fetchone() if approval else None
        if not approval or approval['status']!='pending':return
        if not receipt:
            from .hermes_descriptions import describe
            scope=agent.get('hermes_approval_scope','operation')
            description,panel_only=describe(self.ai,run,json.loads(approval['arguments']),scope)
            if not self.ai._guard(run['id']):return
            with self.ai.store.connect() as db:
                db.execute("INSERT OR IGNORE INTO hermes_confirmations(id,run_id,contact_id,code,expires,panel_only,summary,scope) VALUES(?,?,?,?,?,?,?,?)",
                    (approval['id'],run['id'],run['contact_id'],secrets.token_hex(3).upper(),time.time()+agent.get('hermes_approval_timeout',600),int(panel_only),description,scope))
                receipt=db.execute('SELECT * FROM hermes_confirmations WHERE id=?',(approval['id'],)).fetchone()
        receipt=dict(receipt)
        if receipt['notice_id']:return
        description=receipt['summary'] or describe_operation(json.loads(approval['arguments']))
        minutes=max(1,round((receipt['expires']-time.time())/60))
        once=receipt['scope']=='task'
        title='这项任务需要你确认一次：' if once else '这一步需要你确认：'
        permission='确认后连续完成本次任务，不再逐步询问；新任务重新判断权限。' if once else '只执行本次操作。'
        text=f"{title}\n{description}\n\n回复「确认」继续，或「取消」。\n{permission}\n{minutes} 分钟内有效；其他消息会取消当前任务。"
        if receipt['panel_only']:text=description+'\n请在工作台运行详情确认，待确认的操作尚未执行。'
        message,_=self.ai.store.enqueue(run['contact_id'],text,'hermes-confirm:'+approval['id'],origin='approval',ai_run_id=run['id'])
        with self.ai.store.connect() as db:db.execute('UPDATE hermes_confirmations SET notice_id=? WHERE id=?',(message['id'],approval['id']))
        self.ai.data.trace(run['id'],'确认请求已加入微信发送队列',{'approval_id':approval['id'],'message_id':message['id']})

    def _feedback(self,receipt,text):
        message,_=self.ai.store.enqueue(receipt['contact_id'],text,'hermes-decision:'+receipt['id'],origin='approval',ai_run_id=receipt['run_id'])
        with self.ai.store.connect() as db:db.execute('UPDATE hermes_confirmations SET response_id=? WHERE id=?',(message['id'],receipt['id']))

    def _retry_notice(self,receipt):
        # These errors can only occur before the bridge starts pasting. Any
        # error after that point is SendUncertain and MUST NOT be retried.
        safe_errors={'目标控件被其他窗口遮挡，已停止操作',
                     '目标聊天窗口未获得前台焦点，已停止操作',
                     '微信聊天窗口未获得前台焦点，已停止发送'}
        notice=self.ai.store.message(receipt['notice_id']) if receipt['notice_id'] else None
        if (not notice or notice['status']!='failed' or notice['error'] not in safe_errors
                or notice['source_key'] or receipt['delivery_retries']>=2
                or not valid_notice(self.ai.store,notice)):return
        if not receipt['retry_after']:
            with self.ai.store.connect() as db:
                db.execute('UPDATE hermes_confirmations SET retry_after=? WHERE id=?',(time.time()+3,receipt['id']))
            return
        if time.time()<receipt['retry_after']:return
        with self.ai.store.connect() as db:
            changed=db.execute("UPDATE messages SET status='queued',error='' WHERE id=? AND status='failed' AND error=? AND source_key IS NULL",
                               (notice['id'],notice['error'])).rowcount
            if not changed:return
            db.execute('UPDATE hermes_confirmations SET delivery_retries=delivery_retries+1,retry_after=0 WHERE id=?',(receipt['id'],))
            self.ai.store.event(db,'message.updated',receipt['contact_id'],message_id=notice['id'])
        self.ai.data.trace(receipt['run_id'],'确认消息在输入前受阻，重新发送',{'message_id':notice['id'],'retry':receipt['delivery_retries']+1})

    def consume(self,contact,source):
        reply=parse_reply(source['text'])
        if (not reply or source['contact_id']!=contact['id'] or source['historical']
                or source['direction']!='incoming' or not source['direction_verified']):return False
        with self.ai.store.connect() as db:
            receipt=reply_receipt(db,contact['id'],reply)
        if not receipt:return False
        receipt=dict(receipt);run=self.ai.data.run(receipt['run_id'])
        if receipt['panel_only']:return False
        if receipt['status']!='pending':
            if receipt['status']=='approved' and self.ai.data.valid(run):
                with self.ai.store.connect() as db:db.execute('UPDATE ai_runs SET resume_message_id=? WHERE id=?',(source['id'],run['id']))
                if not reply[0]:
                    with self.ai.store.connect() as db:db.execute("UPDATE hermes_confirmations SET status='rejected' WHERE id=?",(receipt['id'],))
                    self.ai.data.update_run(run['id'],status='cancelled',error='用户在微信取消了当前任务')
                    self.ai.data.trace(run['id'],'微信取消当前任务',{'message_id':source['id']})
                    self._feedback(receipt,'已停止继续执行这项任务；已经完成的操作不会自动撤销。')
            return True  # Never replay a consumed/expired confirmation as a task.
        if receipt['expires']<=time.time():return True
        if not self.ai.data.valid(run):return False
        if source['id']<=receipt['notice_id']:return False
        notice=self.ai.store.message(receipt['notice_id'])
        # A fast reply can arrive before the worker's send receipt. Keep the run
        # waiting and consume it on the next tick after native send verification.
        if not notice or notice['status']!='confirmed':return True
        with self.ai.store.connect() as db:
            db.execute('UPDATE ai_runs SET resume_message_id=? WHERE id=?',(source['id'],run['id']))
        self.ai.approve(receipt['id'],reply[0])
        self.ai.data.trace(run['id'],'微信确认' if reply[0] else '微信取消',{'approval_id':receipt['id'],'message_id':source['id']})
        if not reply[0]:self._feedback(receipt,'已取消这次操作。')
        return True

    def tick(self):
        with self.ai.store.connect() as db:
            receipts=[dict(r) for r in db.execute("SELECT * FROM hermes_confirmations WHERE status='pending'")]
        for receipt in receipts:
            run=self.ai.data.run(receipt['run_id'])
            if run['status'] not in ('waiting_approval','running','pending'):
                with self.ai.store.connect() as db:db.execute("UPDATE hermes_confirmations SET status='cancelled' WHERE id=?",(receipt['id'],))
                continue
            if time.time()>=receipt['expires']:
                with self.ai.store.connect() as db:
                    db.execute("UPDATE hermes_confirmations SET status='expired' WHERE id=?",(receipt['id'],))
                    db.execute("UPDATE ai_approvals SET status='expired' WHERE id=? AND status='pending'",(receipt['id'],))
                self.ai.data.update_run(run['id'],status='cancelled',error='微信确认已过期，未执行待确认操作')
                self._feedback(receipt,'确认已过期，待确认的操作没有执行。需要时请重新下达指令。')
                continue
            self._retry_notice(receipt)
            with self.ai.store.connect() as db:
                latest=db.execute("SELECT * FROM messages WHERE contact_id=? AND direction='incoming' AND historical=0 ORDER BY id DESC LIMIT 1",(receipt['contact_id'],)).fetchone()
            if latest:self.consume(self.ai.store.contact(receipt['contact_id']),dict(latest))

    def recover(self):
        with self.ai.store.connect() as db:
            ids=[r[0] for r in db.execute("SELECT run_id FROM hermes_confirmations WHERE status='pending'")]
        for rid in ids:
            self.ai.data.update_run(rid,status='uncertain',error='服务重启，旧微信确认已失效，请核对任务后重新下达指令')
        with self.ai.store.connect() as db:
            db.execute("UPDATE hermes_confirmations SET status='expired' WHERE status='pending'")
            db.execute("UPDATE messages SET status='cancelled',error='旧确认已失效' WHERE origin='approval' AND status='queued'")
