"""Local-only, bounded voice catalogue and preview endpoints."""
import asyncio
import threading
from typing import Literal
from uuid import UUID
from fastapi import HTTPException, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field,SecretStr

from .voice_synthesis import PRESETS,SAMPLE_TEXT,preset,preview
from .minimax_voice import MiniMaxVoice,MAX_UPLOAD,SYSTEM_VOICES


class VoicePreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    voice_id:str=Field(min_length=1,max_length=300)
    text:str=Field(default=SAMPLE_TEXT,min_length=1,max_length=120)
    model:Literal['speech-2.8-turbo','speech-2.8-hd']='speech-2.8-turbo'


class MiniMaxKey(BaseModel):
    model_config=ConfigDict(extra='forbid')
    api_key:SecretStr=Field(default='',max_length=4096)
    clear_secret:bool=False


def install_voice_routes(app):
    limit=threading.BoundedSemaphore(2)
    clone_limit=threading.BoundedSemaphore(1)

    @app.exception_handler(RequestValidationError)
    async def safe_validation(request,exc):
        if request.url.path.startswith('/api/voice/'):
            # Pydantic's default error body includes the submitted API Key.
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'参数格式不正确，请检查文本长度、Key、模型或文件格式'},status_code=422)
        return await request_validation_exception_handler(request,exc)

    @app.get('/api/voice/voices')
    def voices():
        service=MiniMaxVoice(app.state.store)
        # Synchronization may return hundreds of system voices. Only these presets
        # are offered; retain metadata for existing selections and all custom voices.
        selected={vid for vid,_,_ in SYSTEM_VOICES}
        catalog=[dict(v,selectable=v['id'] in ('xiaoxiao','xiaoyi')) for v in PRESETS]
        catalog.extend(dict(v,selectable=v['kind']!='system' or v['voice'] in selected)
                       for v in service.voices())
        return {'voices':catalog,'sample_text':SAMPLE_TEXT,
                'minimax':service.config()}

    @app.post('/api/voice/preview')
    def voice_preview(body:VoicePreview):
        preset(body.voice_id,store=app.state.store)
        if not body.text.strip():raise ValueError('试听文本不能为空')
        if not limit.acquire(blocking=False):raise HTTPException(429,'正在生成试听，请稍后再试')
        try:
            data=(preview(body.text,body.voice_id,store=app.state.store,model=body.model)
                  if body.voice_id.startswith('minimax:') else preview(body.text,body.voice_id))
        finally:limit.release()
        return Response(data,media_type='audio/wav',headers={'Content-Disposition':'inline; filename="voice-preview.wav"'})

    @app.get('/api/voice/minimax/config')
    def minimax_config():
        return MiniMaxVoice(app.state.store).config()

    @app.put('/api/voice/minimax/config')
    def minimax_save(body:MiniMaxKey):
        key=body.api_key.get_secret_value() if isinstance(body.api_key,SecretStr) else ''
        return MiniMaxVoice(app.state.store).save_config(key,body.clear_secret)

    @app.post('/api/voice/minimax/refresh')
    def minimax_refresh():
        if not limit.acquire(blocking=False):raise HTTPException(429,'正在连接，请稍后再试')
        try:return MiniMaxVoice(app.state.store).refresh()
        finally:limit.release()

    @app.post('/api/voice/minimax/clones/{request_id}')
    async def minimax_clone(request:Request,request_id:UUID,
                            name:str=Query(min_length=1,max_length=60),
                            format:Literal['wav','mp3','m4a']='wav'):
        service=MiniMaxVoice(app.state.store)
        service.key()  # Fail before accepting an audio upload if not configured.
        if not clone_limit.acquire(blocking=False):raise HTTPException(429,'已有音色正在创建，请稍后查看结果')
        try:
            audio=bytearray()
            async for part in request.stream():
                audio.extend(part)
                if len(audio)>MAX_UPLOAD:raise HTTPException(413,'样音不能超过 20 MB')
            return await asyncio.to_thread(service.clone,str(request_id),name,bytes(audio),format)
        finally:clone_limit.release()
