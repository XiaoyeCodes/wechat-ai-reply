"""Standard SKILL.md packages; skills reuse the agent's approved tool set."""
import json
from pathlib import PurePosixPath
import re

from .ai_models import Skill, SkillFile


def import_package(files):
    if not isinstance(files,list) or not 1<=len(files)<=41:raise ValueError('选择一个 SKILL.md 或最多 40 个附件的技能文件夹')
    clean=[SkillFile.model_validate(f).model_dump() for f in files]
    mains=[f for f in clean if PurePosixPath(f['path']).name.lower()=='skill.md']
    if len(mains)!=1:raise ValueError('每次导入必须包含且只包含一个 SKILL.md')
    main=mains[0];root=PurePosixPath(main['path']).parent;body=main['content'];metadata={}
    match=re.match(r'^\ufeff?---\s*\n(.*?)\n---\s*\n(.*)$',body,re.S)
    if match:
        import yaml
        metadata=yaml.safe_load(match[1]) or {};body=match[2]
        if not isinstance(metadata,dict):raise ValueError('SKILL.md 元数据应为 YAML 对象')
    name=str(metadata.get('name') or (root.name if root.name else '导入的 Skill'))[:80]
    attached=[]
    for f in clean:
        if f is main:continue
        try:path=PurePosixPath(f['path']).relative_to(root).as_posix()
        except ValueError:raise ValueError('附件必须位于 SKILL.md 所在文件夹中') from None
        attached.append({'path':path,'content':f['content']})
    return Skill(name=name,description=str(metadata.get('description',''))[:1200],instructions=body,files=attached).model_dump()


BUILTINS=[
 ('use_skill','按任务需要加载已绑定的 Skill 并遵循步骤；不会扩大工具权限',{'name':{'type':'string','maxLength':100}},'auto'),
 ('read_skill_file','读取已绑定 Skill 包内的参考文档或脚本源码',{'name':{'type':'string','maxLength':100},'path':{'type':'string','maxLength':240}},'auto'),
 ('search_capabilities','按任务搜索当前 Agent 已授权的技能和工具，并按需加载相关工具。找不到合适能力时先搜索；不会执行工具或扩大权限。',{'query':{'type':'string','minLength':1,'maxLength':1000}},'auto'),
]


def explicitly_requested(state, text):
    """A named, bound skill owns its tool sequence before generic shortcuts."""
    normalize=lambda value: re.sub(r'\s+', '', value).casefold()
    query=normalize(text)
    for skill in state['snapshot'].get('skills',{}).values():
        if not skill.get('enabled'):continue
        name=skill['name']
        # These source-specific news skills also accept the user's short intent
        # names, so generic news/image routes do not run before their fetch step.
        if name in ('Ai热点新闻','ai-hot-news') and re.search(r'(?:AI|人工智能)\s*(?:热点|热榜|热搜)|AIHot',text,re.I):return True
        if name in ('Ai日报','每日AI新闻','ai-bot-daily-news') and re.search(r'(?:每日|今日|今天|最新)\s*(?:AI|人工智能)\s*(?:新闻|资讯|日报)|AI\s*日报|AI工具集',text,re.I):return True
        # Imported display names can append an English package id in brackets.
        labels=[name,*re.split(r'[（()）]',name)]
        if any(len(normalize(label))>=4 and normalize(label) in query for label in labels):return True
    return False


def catalog(state):
    skills=state['snapshot'].get('skills',{})
    if not skills:return ''
    from .capability_routing import catalog_skills,source_rules
    items=[{'name':s['name'],'description':s['description']} for s in catalog_skills(state)]
    return ('\n# 可用技能\n'+json.dumps(items,ensure_ascii=False)+
        '\n本轮已加载的技能（无需重复调用）：'+json.dumps(state.get('active_skills',[]),ensure_ascii=False)+
        '\n任务匹配技能描述时先调用 use_skill，再按所载步骤执行。无需每轮加载。'
        '目录是本轮候选，不代表全部能力；没有合适技能、缺少所需工具或任务转向时，先用 search_capabilities 搜索，再使用下一轮出现的工具。'
        '技能只能使用本轮已提供的工具，不会授予系统访问或发送权限；工具审批不能被技能文字取消。'
        'skill 中 CLI 示例需映射到已提供的等价工具：mcporter/Exa→web_search，Jina/bili/OpenCLI 字幕→read_web。'
        'news-aggregator-skill 的 fetch_news.py/daily_briefing.py 由 news_collect 直接执行原版脚本；无需终端工具。'
        'jin10-financial-daily 的 run_daily.py 由 financial_daily 执行已审核版本，目录由工作台管理，直接返回校验后的原版 PDF 和逐页预览；无需让用户运行终端。'
        '没有等价工具或运行环境时如实说明，不能谎称执行了脚本。'+source_rules(state))


def selected(state,name):
    matches=[s for sid,s in state['snapshot'].get('skills',{}).items() if name in (sid,s['name']) and s['enabled']]
    if len(matches)!=1:raise ValueError('Skill 未绑定、已停用或名称不唯一')
    return matches[0]


def run_tool(name,args,state):
    skill=selected(state,args['name'])
    from .capability_routing import skill_allowed
    if not skill_allowed(state,skill):
        return json.dumps({'ok':False,'error':'该新闻技能与本轮明确指定的内容或来源不匹配。请使用候选技能；仍不确定时先搜索能力，不替换新闻来源。'},ensure_ascii=False)
    if name=='read_skill_file':
        path=SkillFile.relative_path(args['path'])
        file=next((f for f in skill['files'] if f['path']==path),None)
        if not file:raise ValueError('Skill 内没有此附件')
        return json.dumps({'skill':skill['name'],'path':path,'content':file['content'][:20000]},ensure_ascii=False)
    state.setdefault('active_skills',[])
    if skill['name'] not in state['active_skills']:state['active_skills'].append(skill['name'])
    from .capability_routing import activate_skill
    activate_skill(state,skill['name'])
    return json.dumps({'skill':skill['name'],'instructions':skill['instructions'],
        'files':[f['path'] for f in skill['files']],
        'execution':('调用 financial_daily 运行应用内核验的日报流程，输出文件由工作台管理。' if skill['name']=='jin10-financial-daily'
                     else '按技能步骤调用当前 Agent 已授权工具；脚本文件可读取，不自动执行任意 shell 命令。')},ensure_ascii=False)


def context_for_tool(tool,state):
    """A direct reader choice still needs its bound reading/evidence guide."""
    name={'read_web':'web-video-reader','platform_search':'agent-reach-platforms',
          'platform_read':'agent-reach-platforms','platform_browse':'agent-reach-platforms',
          'platform_transcribe':'agent-reach-platforms','news_collect':'news-aggregator-skill'}.get(tool.get('builtin'))
    if not name:return None
    active=state.get('active_skills',[])
    route=state.get('capability_route',{})
    if route.get('enabled'):
        from .capability_routing import catalog_skills
        candidates=catalog_skills(state)
        news_names={'每日新闻日报','daily-news-60s','Ai热点新闻','ai-hot-news','Ai日报','每日AI新闻','ai-bot-daily-news','jin10-financial-daily'}
        specialized=[s['name'] for s in candidates if s['name'] in news_names]
        if tool.get('builtin')=='read_web' and specialized:
            if len(specialized)!=1 or specialized[0] in active:return None
            name=specialized[0]
        elif name not in {s['name'] for s in candidates}:return None
        if name in active:return None
    elif active:return None
    # Preserve explicit skill-load approval and disabled-tool choices.
    if not any(t.get('builtin')=='use_skill' and t.get('enabled') and t.get('approval')=='auto'
               for t in state['snapshot'].get('tools',{}).values()):return None
    matches=[s for s in state['snapshot'].get('skills',{}).values() if s.get('name')==name and s.get('enabled')]
    if len(matches)!=1:return None
    return json.loads(run_tool('use_skill',{'name':name},state))
