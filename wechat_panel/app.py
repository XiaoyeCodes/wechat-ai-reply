import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import sqlite3
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .runtime import Runtime
from .store import Store
from .ai import AIService
from .ai_store import unprotect
from .ai_tools import request_json

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / 'static'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ContactCreate(StrictModel):
    lookup: str = Field(min_length=1, max_length=128)
    wechat_id: str = Field(min_length=1, max_length=128)

    @field_validator('lookup', 'wechat_id')
    @classmethod
    def clean(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('不能只填写空格')
        return value


class ContactPatch(StrictModel):
    listening: bool | None = None
    mode: Literal['manual', 'listen_only','ai_assist','ai_auto'] | None = None
    ai_agent_id: str | None = Field(default=None,max_length=100)


class MessageCreate(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    client_key: str = Field(min_length=8, max_length=128)

    @field_validator('text')
    @classmethod
    def nonblank(cls, text):
        if not text.strip():
            raise ValueError('不能发送空白消息')
        if len(text) >= 2000:
            raise ValueError('第一版支持单条 1999 字以内的文字消息')
        return text


class Pause(StrictModel):
    paused: bool


class DemoMessage(StrictModel):
    text: str = Field(min_length=1, max_length=1999)


def create_app(data_dir: Path | None = None, demo=False, runtime_factory=Runtime, ai_model_call=None):
    data_dir = Path(data_dir or os.environ.get('WECHAT_PANEL_DATA_DIR') or ROOT / ('.panel-demo-data' if demo else '.panel-data'))

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(data_dir / 'panel.sqlite3')
        app.state.ai = AIService(app.state.store,model_call=ai_model_call)
        app.state.runtime = runtime_factory(app.state.store, demo=demo)
        app.state.runtime.start()
        app.state.ai.start()
        app.state.restart_lock = asyncio.Lock()
        try:
            yield
        finally:
            app.state.ai.stop()
            await asyncio.to_thread(app.state.runtime.stop)

    app = FastAPI(title='微信工作台', version='0.2.0', lifespan=lifespan)
    app.state.exiting = False
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])

    @app.middleware('http')
    async def local_requests(request, call_next):
        if request.url.path.startswith('/api'):
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail':'不允许来自其他网页的请求'}, status_code=403)
            if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('x-panel-request') != '1':
                return JSONResponse({'detail':'缺少本机面板请求标识'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail':str(exc).strip("'")}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail':str(exc)}, status_code=409)

    @app.exception_handler(RuntimeError)
    async def unavailable(request, exc):
        return JSONResponse({'detail':str(exc)}, status_code=503)

    @app.get('/api/status')
    def status():
        return dict(app='wechat-panel', **app.state.runtime.health(), agent_connected=app.state.ai.health()['enabled'], ai=app.state.ai.health())

    @app.get('/api/bootstrap')
    def bootstrap():
        return dict(contacts=app.state.store.contacts(), status=status(), cursor=app.state.store.latest_event())

    @app.get('/api/contacts')
    def contacts():
        return app.state.store.contacts()

    @app.get('/api/contacts/{contact_id}/avatar')
    def contact_avatar(contact_id: str, own: bool = False):
        return Response(app.state.store.avatar(contact_id, own), media_type='image/png')

    @app.post('/api/contacts/sync-avatars')
    async def sync_avatars():
        if app.state.store.paused():
            raise HTTPException(409, '请先恢复运行再同步头像')
        return await asyncio.to_thread(app.state.runtime.request, 'sync_avatars')

    @app.post('/api/contacts', status_code=201)
    async def add_contact(body: ContactCreate):
        profile = await asyncio.to_thread(app.state.runtime.request, 'inspect', lookup=body.lookup, wechat_id=body.wechat_id)
        try:
            contact = app.state.store.add_contact(profile)
        except sqlite3.IntegrityError:
            raise HTTPException(409, '该微信账号下已经添加过这个联系人') from None
        app.state.runtime.configure()
        return contact

    @app.patch('/api/contacts/{contact_id}')
    def update_contact(contact_id: str, body: ContactPatch):
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(422, '请选择需要修改的设置')
        if 'listening' in changes:
            changes.update(listener_state='starting' if changes['listening'] else 'idle', error='')
        current=app.state.store.contact(contact_id)
        agent_id=changes.get('ai_agent_id',current['ai_agent_id'])
        mode=changes.get('mode',current['mode'])
        if agent_id:app.state.ai.data.get('agents',agent_id)
        if mode in ('ai_assist','ai_auto') and not agent_id:
            raise HTTPException(409,'请先为此联系人选择 Agent')
        if mode in ('ai_assist','ai_auto') and changes.get('listening',current['listening']):
            app.state.ai.snapshot(agent_id)
        if set(changes)&{'mode','listening','ai_agent_id'}:
            with app.state.ai.lock:app.state.ai.data.cancel_contact(contact_id,'会话模式、监听或 Agent 已切换')
        contact = app.state.store.update_contact(contact_id, **changes)
        app.state.runtime.configure()
        return contact

    @app.delete('/api/contacts/{contact_id}', status_code=204)
    def delete_contact(contact_id: str):
        with app.state.ai.lock:app.state.ai.data.cancel_contact(contact_id,'联系人已移除')
        app.state.store.delete_contact(contact_id)
        app.state.runtime.configure()

    @app.post('/api/contacts/{contact_id}/read')
    def mark_read(contact_id: str):
        # Avoid generating an endless event -> refresh -> mark-read loop.
        if app.state.store.contact(contact_id)['unread']:
            app.state.store.update_contact(contact_id, unread=0)
        return {'ok':True}

    @app.get('/api/contacts/{contact_id}/messages')
    def messages(contact_id: str, before: int | None = None, limit: int = Query(100, ge=1, le=200)):
        return app.state.store.messages(contact_id, before, limit)

    @app.post('/api/contacts/{contact_id}/messages', status_code=202)
    def send(contact_id: str, body: MessageCreate):
        if app.state.store.paused():
            raise HTTPException(409, '全部监听和发送已暂停，请先恢复')
        if app.state.runtime.health()['state'] not in ('ready', 'starting', 'hidden'):
            raise HTTPException(503, '微信连接未就绪，请先检查运行状态')
        with app.state.ai.lock:
            # A retry of the same manual HTTP request must not cancel newer AI work.
            with app.state.store.connect() as db:
                prior=db.execute('SELECT id FROM messages WHERE contact_id=? AND client_key=?',(contact_id,body.client_key)).fetchone()
            if not prior:app.state.ai.data.cancel_contact(contact_id,'人工回复已接管本轮对话')
            message, _ = app.state.store.enqueue(contact_id, body.text, body.client_key)
        return message

    @app.post('/api/runtime/pause')
    def pause(body: Pause):
        if body.paused:
            with app.state.ai.lock:app.state.ai.data.cancel_all('全部运行已暂停')
        app.state.runtime.set_paused(body.paused)
        return status()

    @app.post('/api/runtime/reconnect')
    async def reconnect():
        async with app.state.restart_lock:
            await asyncio.to_thread(app.state.runtime.stop)
            app.state.runtime = runtime_factory(app.state.store, demo=demo)
            app.state.runtime.start()
            await asyncio.to_thread(app.state.runtime.request, 'probe', reveal=True)
        return status()

    @app.post('/api/shutdown')
    def shutdown():
        callback = getattr(app.state, 'shutdown_callback', None)
        if callback is None:
            raise HTTPException(503, '此启动方式不支持远程停止，请关闭服务进程')
        app.state.exiting = True
        callback()
        return {'ok':True}

    @app.get('/api/events')
    async def events(request: Request, after: int = Query(0, ge=0)):
        try:
            cursor = max(after, int(request.headers.get('last-event-id') or 0))
        except ValueError:
            raise HTTPException(422, '无效的事件游标') from None

        async def stream():
            nonlocal cursor
            ticks = 0
            while not app.state.exiting and not await request.is_disconnected():
                batch = app.state.store.events(cursor)
                if batch:
                    cursor = batch[-1]['id']
                    yield f'id: {cursor}\nevent: change\ndata: {json.dumps(batch,ensure_ascii=False)}\n\n'
                if ticks % 10 == 0:
                    yield f'event: status\ndata: {json.dumps(status(),ensure_ascii=False)}\n\n'
                ticks += 1
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering':'no'})

    @app.get('/api/activity')
    def activity():
        return app.state.store.recent_events()

    @app.get('/api/agent/schema')
    def agent_schema():
        return dict(version='1', enabled=False, delivery='cursor-poll',
                    endpoint='/api/agent/events?after=<event_id>',
                    authorization='Bearer WECHAT_PANEL_AGENT_TOKEN',
                    event_fields=['event_id','account_id','contact_id','wechat_id','message_id','text','received_at'],
                    note='经核验的新入站文本事件接口；AI 回复与工具流程在 AI 中心配置')

    @app.get('/api/ai/bootstrap')
    def ai_bootstrap():
        data=app.state.ai.data
        return dict(settings=data.settings(),health=app.state.ai.health(),
                    **{kind:data.list(kind) for kind in ('providers','styles','knowledge','tools','agents','services','skills','mcp')})

    @app.post('/api/ai/mcp/{resource_id}/discover')
    async def mcp_discover(resource_id: str):
        from .mcp_client import discover
        connection=app.state.ai.data.get('mcp',resource_id,private=True)
        return {'tools':await asyncio.to_thread(discover,connection)}

    @app.post('/api/ai/mcp/{resource_id}/import')
    async def mcp_import(resource_id: str,body:dict):
        from .mcp_client import discover,import_tools
        names=body.get('names')
        if not isinstance(names,list) or not all(isinstance(n,str) for n in names) or not 1<=len(names)<=40:
            raise ValueError('请选择 1 至 40 个工具')
        data=app.state.ai.data
        connection=data.get('mcp',resource_id,private=True)
        rows=await asyncio.to_thread(discover,connection)
        with app.state.ai.lock:
            if data.get('mcp',resource_id,private=True)!=connection:raise ValueError('连接配置已改变，请重新发现工具')
            return {'tools':import_tools(data,connection,rows,names)}

    @app.post('/api/ai/skills/preview')
    def skill_preview(body:dict):
        from .skills import import_package
        try:return import_package(body.get('files'))
        except Exception as exc:
            from pydantic import ValidationError
            if isinstance(exc,ValidationError):raise ValueError('技能文件格式或大小不符合要求') from None
            if isinstance(exc,ValueError):raise
            raise ValueError('无法读取 SKILL.md，请检查文件格式') from None

    @app.put('/api/ai/settings')
    def ai_settings(body: dict):
        with app.state.ai.lock:return app.state.ai.data.settings(body)

    @app.get('/api/ai/config/{kind}/{resource_id}')
    def ai_resource(kind: str,resource_id: str):
        return app.state.ai.data.get(kind,resource_id)

    @app.post('/api/ai/config/{kind}',status_code=201)
    def ai_create(kind: str,body: dict):
        with app.state.ai.lock:return app.state.ai.data.save(kind,None,body)

    @app.put('/api/ai/config/{kind}/{resource_id}')
    def ai_update(kind: str,resource_id: str,body: dict):
        with app.state.ai.lock:return app.state.ai.data.save(kind,resource_id,body)

    @app.delete('/api/ai/config/{kind}/{resource_id}',status_code=204)
    def ai_delete(kind: str,resource_id: str):
        with app.state.ai.lock:app.state.ai.data.delete(kind,resource_id)

    @app.post('/api/ai/providers/{resource_id}/test')
    async def ai_provider_test(resource_id: str):
        provider=app.state.ai.data.get('providers',resource_id,private=True)
        answer,usage=await asyncio.to_thread(app.state.ai.model_call,provider,[{'role':'user','content':'请只回复：连接成功'}],[])
        return dict(reply=answer.get('content',''),usage=usage)

    @app.post('/api/ai/providers/{resource_id}/models')
    async def ai_models(resource_id: str):
        provider=app.state.ai.data.get('providers',resource_id,private=True)
        key=unprotect(provider.get('secret'))
        result=await asyncio.to_thread(request_json,provider['base_url']+'/models',None,method='GET',
                                     headers={'Authorization':'Bearer '+key} if key else {},timeout=15)
        return {'models':[row['id'] for row in result.get('data',[]) if isinstance(row.get('id'),str)][:500]}

    @app.post('/api/ai/knowledge/search')
    def ai_knowledge_search(body: dict):
        ids=body.get('knowledge_ids',[]);query=body.get('query','')
        if not isinstance(ids,list) or len(ids)>30 or not isinstance(query,str) or len(query)>1000:raise ValueError('无效检索参数')
        return {'results':app.state.ai.data.search(ids,query)}

    @app.post('/api/ai/test',status_code=202)
    def ai_test(body: dict):
        text=body.get('text','');agent_id=body.get('agent_id','')
        if not isinstance(text,str) or not 1<=len(text.strip())<=5000:raise ValueError('请输入 1～5000 字测试消息')
        with app.state.ai.lock:
            if len(app.state.ai.futures)>=2:raise HTTPException(409,'AI 正在处理其他请求，请稍后测试')
            return app.state.ai.create_run(agent_id,text,dry_run=True,session_id=body.get('session_id'),input_media_id=body.get('media_id'))

    @app.get('/api/ai/runs')
    def ai_runs(contact_id: str | None=None):return app.state.ai.data.runs(contact_id)

    @app.get('/api/ai/runs/{run_id}')
    def ai_run(run_id: str):return app.state.ai.data.detail(run_id)

    @app.post('/api/ai/approvals/{approval_id}')
    def ai_approve(approval_id: str,body: dict):
        if type(body.get('approved')) is not bool:raise ValueError('请选择批准或拒绝')
        return app.state.ai.approve(approval_id,body['approved'])

    @app.post('/api/ai/runs/{run_id}/send',status_code=202)
    def ai_send(run_id: str,body: MessageCreate):
        return app.state.ai.send_draft(run_id,body.text)

    @app.post('/api/ai/runs/{run_id}/cancel')
    def ai_cancel(run_id: str):
        with app.state.ai.lock:
            run=app.state.ai.data.run(run_id)
            if run['contact_id']:app.state.ai.data.cancel_contact(run['contact_id'],'已人工取消 AI 回复')
            else:app.state.ai.data.update_run(run_id,status='cancelled',error='已取消测试')
        return {'ok':True}

    @app.get('/api/agent/events')
    def agent_events(request: Request, after: int = Query(0, ge=0)):
        token = os.environ.get('WECHAT_PANEL_AGENT_TOKEN')
        if not token:
            raise HTTPException(503, 'Agent 接口尚未启用，请配置 WECHAT_PANEL_AGENT_TOKEN')
        supplied = request.headers.get('authorization','')
        if not hmac.compare_digest(supplied, f'Bearer {token}'):
            raise HTTPException(401, 'Agent 接口身份验证失败')
        events = app.state.store.events(after, agent_only=True)
        result = []
        for event in events:
            try:
                contact = app.state.store.contact(event['contact_id'])
            except KeyError:
                continue
            result.append(dict(event_id=event['id'], contact_id=contact['id'], account_id=contact['account_id'],
                               wechat_id=contact['wechat_id'], message_id=event['payload']['message_id'],
                               text=event['payload']['text'], received_at=event['created_at']))
        return dict(events=result, next_cursor=events[-1]['id'] if events else after)

    if demo:
        @app.post('/api/demo/{contact_id}/incoming')
        async def inject(contact_id: str, body: DemoMessage):
            app.state.store.contact(contact_id)
            return await asyncio.to_thread(app.state.runtime.request, 'inject', contact_id=contact_id, text=body.text)

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    from .companion_api import install_companion_routes
    install_companion_routes(app)

    app.mount('/assets', StaticFiles(directory=STATIC, check_dir=False), name='assets')
    return app
