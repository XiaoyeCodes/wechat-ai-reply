"""Relay image skill installation and conversation-scoped reference selection."""
import base64
from pathlib import Path


def install_defaults(data):
    if not any(s['driver']=='codex_image' for s in data.list('services')):
        data.save('services',None,{'name':'Codex / ChatGPT 账号生图','category':'image',
            'driver':'codex_image','timeout':300})
    if not any(s['name']=='codex-image-generation' for s in data.list('skills')):
        from .skills import import_package
        path=Path(__file__).resolve().parent.parent/'skills/codex-image-generation/SKILL.md'
        if path.is_file():
            data.save('skills',None,import_package([{'path':'SKILL.md','content':path.read_text(encoding='utf-8')}]))


def reference_for(runtime,run,state,media,args):
    mode=args.get('mode')
    explicit=args.get('reference_media_id')
    if mode=='generate' and explicit:
        raise ValueError('指定参考图时请使用 edit 模式')
    if mode=='generate':return None
    memory=runtime.memory;scope=state['scope']
    permitted={scope}
    if run.get('contact_id'):permitted.add('wechat:'+run['contact_id'])
    # A manually configured reference is an explicit operator-owned asset.
    configured=media.get('reference_media_id')
    if explicit:
        chosen=memory.read_media(explicit)
        if chosen['scope'] not in permitted:raise ValueError('参考图不属于当前会话')
    elif mode=='edit':
        refs=list(state.get('input_images',[]))
        if state.get('input_media_id'):
            refs.append({'media_id':state['input_media_id'],'media_scope':scope,'current':True})
        current=[r for r in refs if r.get('current')]
        refs=current or refs
        if len(refs)>1 and current:raise ValueError('本轮有多张图片，请指定要修改的 reference_media_id')
        if refs:
            ref=refs[-1]
            if ref.get('message_id'):
                source=runtime.ai.store.message(ref['message_id'])
                if not source or source['contact_id']!=run.get('contact_id'):
                    raise ValueError('参考图不属于当前联系人')
                ref={**ref,'media_id':source.get('media_id'),'media_scope':'wechat:'+source['contact_id']}
            if not ref.get('media_id'):raise ValueError('用户图片尚未读取成功，请重新上传后再修改')
            if ref.get('media_scope') not in permitted:raise ValueError('参考图不属于当前会话')
            chosen=memory.read_media(ref['media_id'],ref['media_scope'])
        elif configured:chosen=memory.read_media(configured)
        else:raise ValueError('图生图需要参考图，请先上传图片')
    elif configured:chosen=memory.read_media(configured)
    else:return None
    if chosen['kind']!='image':raise ValueError('参考文件必须是图片')
    return base64.b64encode(chosen['data']).decode()
