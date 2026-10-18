"""Local UI endpoints for companion configuration, memory and test media."""
import asyncio
import base64
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response

from .companion import companion_config
from .companion_models import PersonProfile


def install_companion_routes(app):
    def scoped(agent_id,contact_id=None,session_id=None):
        ai=app.state.ai
        agent=ai.data.get('agents',agent_id,private=True)
        scope=ai.companion.memory.scope(agent_id,contact_id,session_id)
        profile=ai.companion.memory.profile(scope,agent_id,contact_id)
        return ai,agent,scope,profile

    @app.get('/api/ai/companion')
    def details(agent_id:str,contact_id:str|None=None,session_id:str|None=None):
        ai,agent,scope,profile=scoped(agent_id,contact_id,session_id)
        memories=ai.companion.memory.list_memories(scope)
        return dict(profile=profile['data'],summary=profile['summary'],mood=profile['mood'],
            memories=[{k:m[k] for k in ('id','category','key','content','pinned','expires_at','updated_at')} for m in memories],
            conversation=ai.companion.memory.conversation(scope))

    @app.put('/api/ai/companion/profile')
    def profile_save(body:dict):
        ai,agent,scope,_=scoped(body.get('agent_id'),body.get('contact_id'),body.get('session_id'))
        with ai.lock:
            if body.get('contact_id'):ai.data.cancel_contact(body['contact_id'],'用户陪伴资料已更新')
            return ai.companion.memory.set_profile(scope,body.get('profile',{}))['data']

    @app.post('/api/ai/companion/memories')
    def memory_save(body:dict):
        ai,agent,scope,_=scoped(body.get('agent_id'),body.get('contact_id'),body.get('session_id'))
        with ai.lock:
            mid=ai.companion.memory.save_memory(scope,body.get('memory',{}),mid=body.get('memory_id'),days=companion_config(agent)['memory']['retention_days'])
        return {'id':mid}

    @app.post('/api/ai/companion/forget')
    async def memory_forget(body:dict):
        ai,agent,scope,_=scoped(body.get('agent_id'),body.get('contact_id'),body.get('session_id'))
        with ai.lock:
            if body.get('contact_id'):ai.data.cancel_contact(body['contact_id'],'用户已删除记忆')
            else:
                # Cancel running tests for this sandbox before forgetting.
                for r in ai.data.runs():
                    if r['dry_run'] and r['status'] in ('running','pending','waiting_approval') and ai.data.run(r['id'])['state'].get('scope')==scope:
                        ai.data.update_run(r['id'],status='cancelled',error='测试会话记忆已删除')
        count=await asyncio.to_thread(ai.companion.forget,scope,companion_config(agent),body.get('memory_id'))
        return {'deleted':count}

    @app.post('/api/ai/media')
    def upload_media(body:dict):
        ai,agent,scope,_=scoped(body.get('agent_id'),body.get('contact_id'),body.get('session_id'))
        value=body.get('data','')
        if not isinstance(value,str) or len(value)>11_000_000:raise ValueError('图片最多 8 MB')
        try:data=base64.b64decode(value.split(',',1)[-1],validate=True)
        except ValueError:raise ValueError('无效图片编码') from None
        return ai.companion.memory.media(scope,data)

    @app.get('/api/ai/media/{media_id}')
    def media_file(media_id:str):
        media=app.state.ai.companion.memory.read_media(media_id)
        return Response(media['data'],media_type=media['mime'],headers={'Cache-Control':'private, no-store'})

    @app.post('/api/ai/services/{service_id}/test')
    async def service_test(service_id:str):
        return await asyncio.to_thread(app.state.ai.companion.services.test,service_id)

    @app.get('/api/ai/schedules')
    def schedules():return app.state.ai.companion.memory.schedules()

    @app.post('/api/ai/schedules')
    def schedule_create(body:dict):
        app.state.ai.data.get('agents',body.get('agent_id'))
        with app.state.ai.lock:return app.state.ai.companion.memory.save_schedule(body)

    @app.put('/api/ai/schedules/{schedule_id}')
    def schedule_update(schedule_id:str,body:dict):
        app.state.ai.data.get('agents',body.get('agent_id'))
        with app.state.ai.lock:
            result=app.state.ai.companion.memory.save_schedule(body,schedule_id)
            app.state.ai.data.cancel_contact(body['contact_id'],'主动关怀日程已更改')
            return result

    @app.delete('/api/ai/schedules/{schedule_id}')
    def schedule_delete(schedule_id:str):
        with app.state.ai.lock:app.state.ai.companion.memory.delete_schedule(schedule_id)
        return {'ok':True}

    @app.post('/api/ai/companion/template')
    def template(body:dict):
        ai=app.state.ai;provider=body.get('provider_id')
        ai.data.get('providers',provider)
        with ai.lock:
            search=next((s for s in ai.data.list('services') if s['driver']=='agent_reach'),None)
            if not search:search=ai.data.save('services',None,{'name':'Agent Reach · Exa','category':'search','driver':'agent_reach'})
            allowed={'clock','weather','web_search','read_web','music_search','places_search','movie_search','memory_search','remember','forget','set_reminder'}
            tools=[t['id'] for t in ai.data.list('tools') if t.get('builtin') in allowed]
            agent=ai.data.save('agents',None,dict(name='阿慧 · 甜心陪伴',description='22 岁，温柔甜美、活泼俏皮，先接住情绪，再自然陪伴。',
                provider_id=provider,tool_ids=tools,temperature=.95,
                prompt=(Path(__file__).resolve().parent.parent/'docs/prompts/ahui-system.md').read_text('utf-8').strip(),
                companion={'memory':{'enabled':True},'care':{'enabled':False},'search_service_id':search['id']}))
        return agent

    @app.post('/api/ai/companion/prompt')
    def prompt_preview(body:dict):
        ai,agent,scope,profile=scoped(body.get('agent_id'),body.get('contact_id'),body.get('session_id'))
        state={'snapshot':ai.snapshot(agent['id']),'context':[],'knowledge':[],'last':'','dynamic_context':{'user_profile':profile['data'],'conversation_summary':profile['summary'],
            'recent_mood':profile['mood'],'time':'每轮按联系人时区注入','memories':'每轮按用户来信检索','weather':'配置城市并启用后获取'}}
        run={'id':'prompt-preview','input':'你好','contact_id':body.get('contact_id')}
        state['prompt_variables']=ai.companion.prompt_context.variables(run,companion_config(agent),profile['data'])
        state['dynamic_context']['time']=state['prompt_variables']['current_time']
        state['dynamic_context']['weather']=state['prompt_variables']['weather']
        instruction=next((s['instruction'] for s in agent['steps'] if s['type']=='model'),'')
        return {'prompt':ai._base_messages(run,state,instruction)[0]['content']}
