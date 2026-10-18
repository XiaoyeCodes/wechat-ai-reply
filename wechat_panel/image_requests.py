"""Route explicit image requests using current permissions, not old chat claims."""
import json
import re


PREFIX = r'(?:(?:请你|请|麻烦|帮我|给我|替我|帮忙|你|现在|再|重新|直接|能不能|能否|可不可以|可以|能)\s*)*'
CREATE = re.compile(r'^'+PREFIX+r'(?:画|绘制|创作|生成|制作|做)(?P<subject>.+)',re.S)
EDIT = re.compile(r'^'+PREFIX+r'(?:(?:修改|编辑|重绘|美化|修复|改|换).{0,15}(?:图|照片|背景)|'
                  r'把.{0,30}(?:图片|照片|这张|那张|背景|这幅|那幅).{0,20}(?:改|换|变|转|去掉|删除|移除))',re.S)


def requested(text):
    text=text.strip().rstrip('。！!')
    # These are requests for instructions or documents, not permission to draw.
    if re.search(r'提示词|prompt|教程|怎么|如何|能不能.{0,6}(?:教|介绍)|思维导图|流程图|表格|代码',text,re.I):return None
    if EDIT.search(text):return 'edit'
    match=CREATE.search(text)
    if match:
        prefix=text[:match.start('subject')]
        if re.search(r'画|绘制',prefix) or re.search(r'图|画|照片|海报|头像|壁纸|插画|logo',match['subject'],re.I):
            return 'generate'
    if re.search(r'^(?:please\s+)?(?:generate|create|draw|paint)\b.{0,120}\b(?:image|picture|painting|illustration|portrait|poster|photo)\b',text,re.I):
        return 'generate'
    return None


def prompt(state):
    tools=[t for t in state['snapshot']['tools'].values() if t.get('builtin')=='generate_image' and t['enabled']]
    service=state['snapshot']['agent'].get('companion',{}).get('media',{}).get('image_service_id')
    if not tools or not service:return ''
    return ('\n# 当前生图能力\n当前已授权 generate_image 且已绑定生图服务，可生成新图和编辑原图。'
            '能力以本轮工具配置为准，历史回复中的“不能画图/没有生图功能”已过时，不能沿用。'
            '用户明确要求生成或修改图片时调用工具；没有成功结果前不声称画好。'
            '新图使用 generate 模式，不把无关历史图片当作参考图。图生图使用 edit 模式并传原图。'
            '图片生成后，自动回复会把图片直接发到原微信会话；辅助模式先保留草稿，测试台仅预览。'
            '发送状态以系统核对为准，不要把生成成功说成已经送达。')


def execute(ai,run,state):
    agent=state['snapshot']['agent']
    if agent['kind']!='workflow' or run.get('schedule_id'):return False
    # Keep explicitly authored fixed tool workflows intact.
    if any(step['type']=='tool' for step in agent['steps']):return False
    mode=requested(run['input'])
    tools=[t for t in state['snapshot']['tools'].values() if t.get('builtin')=='generate_image' and t['enabled']]
    sid=agent.get('companion',{}).get('media',{}).get('image_service_id')
    if not mode or not tools or not sid:return False
    # Validate live service availability before starting an external effect.
    ai.companion.services.get(sid,'image')
    call=state.get('image_request_call')
    if not call:
        call={'id':'explicit-image-request','tool_id':tools[0]['id'],
              'arguments':{'prompt':run['input'],'mode':mode}}
        state['image_request_call']=call
        ai.data.update_run(run['id'],state=state)
        ai.data.trace(run['id'],'明确生图请求',{'mode':mode,'tool':tools[0]['name'],
            'detail':'根据当前授权直接调用生图，不使用旧聊天中的能力判断'})
    # _tool retains the existing approval, cancellation and uncertain-result guards.
    result=state.get('image_request_result')
    if not result:
        result=ai._tool(run,state,call)
        if result is None:return True
        state['image_request_result']=result
        ai.data.update_run(run['id'],state=state)
    receipt=json.loads(result)
    if receipt.get('kind')!='image' or not receipt.get('id'):
        raise RuntimeError('生图工具没有返回可用图片')
    if ai._guard(run['id']):
        reply=('图片已经生成。本次为测试，图片还没有发送到微信，可在运行详情预览、下载。' if run['dry_run'] else
               '图片已经生成，确认草稿后将发送到原微信会话。' if run['mode']=='ai_assist' else
               '图片已经生成，正在发送到本次微信会话。')
        ai.finish(run['id'],reply)
    return True
