"""Multiprocess command bridge. All UI mutations share one serial worker."""
from concurrent.futures import Future, TimeoutError
import multiprocessing as mp
from queue import Empty
import threading
import time
import traceback
import uuid

from .bridge import DemoBridge, WeixinBridge, SendUncertain, WindowTransition, DesktopBusy, connection_error
from .store import Store, now


def worker_main(commands, output, demo, state_path=None):
    instances = []
    try:
        _worker_main(commands, output, demo, instances, state_path)
    finally:
        for bridge in instances:
            if hasattr(bridge, 'close'):
                bridge.close()


def _worker_main(commands, output, demo, instances, state_path=None):
    send_store=Store(state_path) if state_path else None
    bridge = None
    contacts, blocked, attached = {}, set(), set()
    listener_states = {}
    retry_due, retry_attempts = {}, {}
    paused, last_health, last_poll, last_avatar = send_store.paused() if send_store else False, 0, 0, 0
    health = dict(state='starting', detail='正在初始化微信连接', account_id=None)
    identity_attempt = None
    identity_error = ''

    def emit(kind, **data):
        output.put(dict(kind=kind, **data))

    def ai_current(command):
        if not command.get('ai_run_id'):return True
        message=send_store.message(command['message_id']) if send_store else None
        if message and send_store.ai_send_valid(message):return True
        emit('result',request_id=command['request_id'],message_id=command.get('message_id'),
             cancelled=True,error='AI 会话或消息已变化，旧回复已取消')
        return False

    def probe(reveal=False):
        nonlocal bridge, health, identity_attempt, identity_error
        previous_account = health.get('account_id')
        previous_session = health.get('session_key')
        try:
            if bridge is None:
                bridge = DemoBridge() if demo else WeixinBridge()
                instances.append(bridge)
            health = bridge.health()
            if reveal and health['state'] == 'hidden':
                emit('health', state='starting', detail='正在恢复微信主窗口并连接联系人',
                     account_id=health.get('account_id'))
                bridge.reveal()
                health = bridge.health()
            if health['state'] in ('offline','account_changed'):
                identity_attempt = None
            if (health['state'] in ('ready','account_changed') and not health.get('account_id')
                    and hasattr(bridge,'identify') and (not paused or reveal)):
                key = health.get('session_key', health['state'])
                if reveal or identity_attempt != key:
                    identity_attempt = key
                    try:
                        health = bridge.identify()
                        identity_error = ''
                    except Exception as exc:
                        if hasattr(bridge,'_record'):
                            bridge._record('connection.identity_failed',error=str(exc),trace=traceback.format_exc())
                        identity_error = f'微信账号核验失败：{exc}。请在运行状态重新连接'
                if identity_error:
                    health = dict(state='unavailable', detail=identity_error, account_id=None)
        except Exception as exc:
            health = dict(state='unavailable', detail=str(exc), account_id=None)
        if (previous_account != health.get('account_id')
                or (previous_session and health.get('session_key') and previous_session != health['session_key'])):
            attached.clear()
            blocked.clear()
            retry_due.clear()
            retry_attempts.clear()
        emit('health', **health)

    def listening(contact):
        state = bridge.listener_state(contact) if hasattr(bridge, 'listener_state') else 'listening'
        if listener_states.get(contact['id']) != state:
            listener_states[contact['id']] = state
            emit('listener', contact_id=contact['id'], state=state, detail='')

    def connect(contact, for_send=False):
        cid = contact['id']
        if not account_matches(contact):raise RuntimeError('此会话属于其他微信账号，请切换到对应账号')
        if contact['listening']:
            listener_states.pop(cid, None)
            emit('listener', contact_id=cid, state='starting', detail='')
        items = bridge.attach(contact, for_send=for_send)
        attached.add(cid)
        blocked.discard(cid)
        retry_due.pop(cid, None)
        retry_attempts.pop(cid, None)
        if contact['listening']:
            emit('snapshot', contact_id=cid, items=items)
            listening(contact)
        probe()

    def account_matches(contact):
        return not contact.get('account_id') or contact['account_id'] == health.get('account_id')

    def failed(contact, exc):
        cid = contact['id']
        attached.discard(cid)
        if hasattr(bridge, '_record'):
            bridge._record('connection.failed', contact, error=str(exc), trace=traceback.format_exc())
        if connection_error(exc):
            attempt = retry_attempts.get(cid, 0)+1
            retry_attempts[cid] = attempt
            delay = min(2**min(attempt-1, 5), 30)
            retry_due[cid] = time.monotonic()+delay
            blocked.discard(cid)
            if hasattr(bridge, 'invalidate'):
                bridge.invalidate(cid)
            detail = f'微信聊天窗口暂时不可用，{delay} 秒后自动重新连接'
            listener_states[cid] = 'reconnecting'
            emit('listener', contact_id=cid, state='reconnecting', detail=detail)
        else:
            blocked.add(cid)
            listener_states.pop(cid, None)
            emit('listener', contact_id=cid, state='error', detail=str(exc))

    probe()
    while True:
        try:
            command = commands.get(timeout=0.15)
        except Empty:
            command = None
        if command:
            op = command['op']
            if op == 'stop':
                return
            if op == 'configure':
                new = {c['id']: c for c in command['contacts']}
                for cid in list(contacts):
                    group_fields=('chat_type','lookup','account_id','group_members','group_self_name','group_self_name_verified','group_mention_only')
                    group_changed=(cid in new and any(contacts[cid].get(k)!=new[cid].get(k) for k in group_fields)
                        and ('group' in (contacts[cid].get('chat_type'),new[cid].get('chat_type'))))
                    if cid not in new or not new[cid]['listening'] or group_changed:
                        if bridge:
                            bridge.detach(cid)
                        attached.discard(cid)
                        listener_states.pop(cid, None)
                        retry_due.pop(cid, None)
                        retry_attempts.pop(cid, None)
                if command['paused'] and not paused and bridge and hasattr(bridge, 'suspend'):
                    bridge.suspend()
                resuming = paused and not command['paused']
                contacts, paused = new, command['paused']
                if resuming:
                    # A window may have closed while paused. attach() reuses a
                    # valid binding or opens one new independent chat.
                    attached.clear()
                    for cid, contact in contacts.items():
                        if contact['listening']:
                            emit('listener', contact_id=cid, state='starting', detail='')
                blocked.clear()
                retry_due.clear()
                retry_attempts.clear()
                last_poll = 0
                last_avatar = 0
                if not paused and (resuming or any(c['listening'] for c in contacts.values())):
                    # A minimized/tray-hidden main window with no open chats is
                    # recoverable. Wake it before the health gate on polling.
                    probe(reveal=True)
                continue
            try:
                if bridge is None:
                    probe()
                if bridge is None:
                    raise RuntimeError(health['detail'])
                if op == 'inspect':
                    result = bridge.inspect(command['lookup'], command['wechat_id'])
                    probe()
                elif op == 'inspect_group':
                    result=bridge.inspect_group(command['lookup'],command.get('group_members',[]),
                        command.get('group_self_name',''),command.get('account_id'))
                    probe()
                elif op == 'probe':
                    probe(reveal=command.get('reveal',False))
                    result = health
                elif op == 'sync_avatars':
                    if paused or health['state'] != 'ready':
                        raise RuntimeError('请先恢复微信连接再同步头像')
                    images = bridge.avatars(list(contacts.values()), locate=True)
                    emit('avatars', images=images)
                    result = dict(synced=len(images))
                    last_avatar = time.monotonic()
                elif op in ('send', 'inject'):
                    if paused:
                        raise RuntimeError('全部监听和发送已暂停')
                    contact = contacts.get(command['contact_id'])
                    if contact is None:
                        raise RuntimeError('联系人已移除')
                    probe()
                    if not account_matches(contact):raise RuntimeError('此会话属于其他微信账号，请切换到对应账号')
                    if op == 'send':
                        if not ai_current(command):continue
                        if contact['mode'] not in ('manual','ai_assist','ai_auto'):
                            raise RuntimeError('该会话为仅监听模式，发送已停止')
                        if contact['id'] not in attached:
                            for attempt in range(2):
                                try:
                                    connect(contact, for_send=True)
                                    break
                                except Exception as exc:
                                    if not connection_error(exc) or attempt:
                                        raise
                                    if hasattr(bridge, 'invalidate'):
                                        bridge.invalidate(contact['id'])
                                    probe(reveal=True)
                                    time.sleep(0.15)
                        # Recheck after potentially slow window attachment. A
                        # manual takeover can arrive while the worker reconnects.
                        if not ai_current(command):continue
                        message = send_store.message(command['message_id']) if send_store else None
                        if message and message['kind'] == 'file':
                            from .financial_pdf import report_path
                            path = report_path(send_store.path.parent, message['ai_run_id'])
                            result = {'source_key': bridge.send_file(contact, path)}
                        else:
                            if send_store and command.get('message_id'):
                                bridge.receipt_observer=lambda stage,baseline=None,count=0:send_store.send_receipt(command['message_id'],stage,baseline,count)
                            try:
                                if message and message['kind']=='image':
                                    from .image_delivery import deliver
                                    result=deliver(bridge,send_store,message,contact)
                                elif message and message.get('origin')=='ai':
                                    from .voice_reply import deliver
                                    result=deliver(bridge,send_store,message,contact,demo=demo)
                                else:result = {'source_key': bridge.send(contact, command['text'])}
                            finally:bridge.receipt_observer=None
                        if contact['listening']:
                            blocked.discard(contact['id'])
                            attached.add(contact['id'])
                            listening(contact)
                    else:
                        if not demo:
                            raise RuntimeError('真实模式不能注入测试消息')
                        if not contact['listening'] or contact['id'] not in attached:
                            raise RuntimeError('请先开启该联系人的监听')
                        result = (bridge.inject(contact,command['text'],sender=command.get('sender',''),
                            mention_me=command.get('mention_me',False)) if contact.get('chat_type')=='group'
                            else bridge.inject(contact,command['text']))
                else:
                    raise RuntimeError('未知的微信操作')
                emit('result', request_id=command['request_id'], result=result,
                     message_id=command.get('message_id'))
            except Exception as exc:
                from .voice_reply import ReplySuperseded
                target = contacts.get(command.get('contact_id'))
                if (op == 'send' and target and not paused and not isinstance(exc, (SendUncertain,DesktopBusy))
                        and (connection_error(exc) or target['id'] not in attached)):
                    failed(target, exc)
                detail = ('微信连接暂时不可用，正在自动恢复；本条消息未执行发送'
                          if connection_error(exc) else str(exc))
                emit('result', request_id=command['request_id'], error=detail,
                     deferred=isinstance(exc,DesktopBusy),
                     cancelled=isinstance(exc,ReplySuperseded),
                     uncertain=isinstance(exc, SendUncertain), message_id=command.get('message_id'))
        stamp = time.monotonic()
        active = not paused and any(c['listening'] and cid not in blocked for cid,c in contacts.items())
        if stamp-last_health > (10 if health['state'] == 'ready' else 2):
            # A later window close, tray minimize, or delayed Weixin login must
            # recover too; automatic preparation is not limited to resume.
            probe(reveal=active)
            last_health = stamp
        if stamp-last_poll >= 1 and bridge and health['state'] == 'ready' and not paused:
            last_poll = stamp
            for cid, contact in list(contacts.items()):
                if not contact['listening'] or not account_matches(contact) or cid in blocked or stamp < retry_due.get(cid, 0):
                    continue
                try:
                    if cid not in attached:
                        connect(contact)
                    else:
                        if send_store and contact.get('ai_agent_id'):
                            import json
                            try:
                                with send_store.connect() as db:
                                    cfg=db.execute("SELECT data FROM ai_resources WHERE kind='agents' AND id=?",(contact['ai_agent_id'],)).fetchone()
                                media=json.loads(cfg[0]).get('companion',{}).get('media',{}) if cfg else {}
                                contact['_read_images']=bool(((media.get('understand_images') and media.get('vision_provider_id')) or media.get('image_service_id')) and contact['mode'] in ('ai_auto','ai_assist'))
                            except Exception:contact['_read_images']=False
                        items = bridge.snapshot(contact)
                        emit('snapshot', contact_id=cid, items=items)
                        listening(contact)
                except WindowTransition:
                    continue  # Minimize happened during this read; host next tick.
                except Exception as exc:
                    failed(contact, exc)
        if (stamp-last_avatar >= 30 and bridge and hasattr(bridge, 'avatars')
                and health['state'] == 'ready' and not paused):
            last_avatar = stamp
            try:
                emit('avatars', images=bridge.avatars(list(contacts.values())))
            except Exception:
                pass  # Avatar refresh must not interrupt message listening.
        emit('heartbeat')


class Runtime:
    def __init__(self, store, demo=False):
        self.store, self.demo = store, demo
        self.status = dict(state='starting', detail='正在初始化微信连接', account_id=None)
        self.pending_requests = {}
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.last_heartbeat = time.monotonic()
        self.inflight = None
        self.process = None

    def start(self):
        self.store.recover()
        self.store.activate_account('')
        context = mp.get_context('spawn')
        self.commands, self.output = context.Queue(), context.Queue()
        self.process = context.Process(target=worker_main, args=(self.commands, self.output, self.demo, str(self.store.path)), daemon=True)
        self.process.start()
        self.configure()
        self.thread = threading.Thread(target=self._pump, name='wechat-events', daemon=True)
        self.thread.start()

    def configure(self):
        with self.lock:
            self.commands.put(dict(op='configure', contacts=self.store.contacts(), paused=self.store.paused()))

    def set_paused(self, paused):
        # The queue pump uses the same lock so a send cannot overtake resume.
        with self.lock:
            resuming=self.store.paused() and not paused
            self.store.pause(paused)
            if resuming:
                for contact in self.store.contacts():
                    if contact['listening']:self.store.update_contact(contact['id'],listener_state='starting',error='')
            self.configure()

    def request(self, op, timeout=60, **data):
        if not self.process or not self.process.is_alive():
            raise RuntimeError('微信工作进程已停止，请重启连接')
        request_id, future = str(uuid.uuid4()), Future()
        with self.lock:
            self.pending_requests[request_id] = future
        self.commands.put(dict(op=op, request_id=request_id, **data))
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            raise RuntimeError('微信操作超时，请检查微信窗口后重试') from None
        finally:
            with self.lock:
                self.pending_requests.pop(request_id, None)

    def _pump(self):
        retry_due={};retry_attempts={}
        while not self.stopping.is_set():
            try:
                event = self.output.get(timeout=0.2)
            except Empty:
                event = None
            try:
                if event:
                    self.last_heartbeat = time.monotonic()
                    kind = event['kind']
                    if kind == 'health':
                        self.status = {k:v for k,v in event.items() if k != 'kind'}
                        self.status['checked_at'] = now()
                        if self.store.activate_account(event.get('account_id'),event.get('account_name') or ''):
                            self.configure()
                    elif kind == 'snapshot':
                        if self.store.account_matches(self.store.contact(event['contact_id'])):
                            self.store.ingest(event['contact_id'], event['items'])
                    elif kind == 'avatars':
                        for item in event['images']:
                            self.store.set_avatar(item['account_id'], item['wechat_id'], item['data'])
                    elif kind == 'listener':
                        try:
                            if self.store.account_matches(self.store.contact(event['contact_id'])):
                                self.store.update_contact(event['contact_id'], listener_state=event['state'], error=event['detail'])
                        except KeyError:
                            pass
                    elif kind == 'result':
                        message_id = event.get('message_id')
                        if message_id:
                            state = 'cancelled' if event.get('cancelled') else 'uncertain' if event.get('uncertain') else 'failed' if event.get('error') else 'confirmed'
                            if event.get('deferred'):
                                # No paste/click has happened. Keep waiting while
                                # the user works, without failing after a minute.
                                retry_attempts[message_id]=min(retry_attempts.get(message_id,0)+1,3)
                                retry_due[message_id]=time.monotonic()+2**retry_attempts[message_id]
                                state='queued'
                            else:
                                retry_due.pop(message_id,None);retry_attempts.pop(message_id,None)
                            self.store.set_message_state(message_id, state, event.get('error',''),
                                                         event.get('result',{}).get('source_key'))
                            self.inflight = None
                        with self.lock:
                            future = self.pending_requests.get(event['request_id'])
                            if future and not future.done():
                                if event.get('error'):
                                    future.set_exception(RuntimeError(event['error']))
                                else:
                                    future.set_result(event['result'])
                if not self.process.is_alive():
                    self.status = dict(state='offline', detail='微信工作进程意外退出，请重启连接', account_id=None)
                    if self.inflight:
                        self.store.set_message_state(self.inflight, 'uncertain', '工作进程中断，请在微信核对结果')
                        self.inflight = None
                    with self.lock:
                        for future in self.pending_requests.values():
                            if not future.done():
                                future.set_exception(RuntimeError(self.status['detail']))
                    return
                with self.lock:
                    if (not self.inflight and not self.store.paused()
                            and self.status['state'] in ('ready', 'hidden')):
                        pending=[];seen_contacts=set()
                        for message in self.store.pending():
                            # Waiting for the first part must not let later parts
                            # overtake it. Other contacts may continue normally.
                            if message['contact_id'] in seen_contacts:continue
                            seen_contacts.add(message['contact_id'])
                            if (time.monotonic()>=retry_due.get(message['id'],0)
                                    and not self.store.awaiting_receipt(message)):pending.append(message)
                        if pending:
                            message = pending[0]
                            if not self.store.ai_send_valid(message):
                                self.store.set_message_state(message['id'],'cancelled','AI 会话设置或新消息已变化，旧回复已取消')
                                continue
                            self.inflight = message['id']
                            self.store.set_message_state(message['id'], 'sending')
                            self.commands.put(dict(op='send', request_id=str(uuid.uuid4()), message_id=message['id'],
                                                   contact_id=message['contact_id'], text=message['text'],ai_run_id=message.get('ai_run_id')))
            except Exception as exc:
                # Persistence errors must not kill the event pump silently.
                self.status = dict(state='error', detail=f'消息同步异常：{exc}', account_id=None)

    def health(self):
        status = self.status
        age = time.monotonic()-self.last_heartbeat
        if age > 90:
            status = dict(state='stalled', detail='微信操作长时间未响应，请检查微信后重启连接', account_id=None)
        enabled = [c for c in self.store.contacts() if c['listening']]
        connected = sum(c['listener_state'] in ('listening', 'background') for c in enabled)
        errors = sum(c['listener_state'] == 'error' for c in enabled)
        return dict(**status, demo=self.demo, paused=self.store.paused(),
                    contacts_total=len(enabled), contacts_connected=connected, contacts_errors=errors,
                    worker_alive=bool(self.process and self.process.is_alive()),
                    busy=bool(self.inflight), heartbeat_age=round(age,1),
                    queue_size=len(self.store.pending()))

    def stop(self):
        self.stopping.set()
        if self.process:
            self.commands.put(dict(op='stop'))
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=2)
            if self.inflight:
                self.store.set_message_state(self.inflight, 'uncertain', '连接已停止，请在微信核对结果')
        if hasattr(self, 'thread'):
            self.thread.join(timeout=2)
        with self.lock:
            for future in self.pending_requests.values():
                if not future.done():
                    future.set_exception(RuntimeError('服务正在停止'))
