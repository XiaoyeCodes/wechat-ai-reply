"""Select a bounded, task-relevant view of an agent's existing capabilities.

This is discovery, not authorization: every returned tool is intersected with
the caller's eligible list. Skill instructions, quoted material and tool output
are deliberately excluded from the retrieval corpus.
"""
import re
from urllib.parse import urlsplit


VERSION = 1
MAX_TOOLS = 12
MAX_SKILLS = 6
SEARCH_TOOLS = 4
SEARCH_SKILLS = 3
LARGE_TOOLSET = 12
CORE = {'search_capabilities', 'use_skill', 'read_skill_file'}
NEWS_FAMILIES = {'ai_hot', 'ai_daily', 'daily60', 'financial', 'aggregator'}
PLATFORM = re.compile(r'github|youtube|youtu\.be|油管|bilibili|b23\.tv|B站|reddit|小红书|xiaohongshu|微博|weibo|v2ex|twitter|推特|x\.com|(?<!\w)X(?:上|平台)|linkedin|领英|Boss直聘|facebook|instagram|小宇宙|xiaoyuzhou|雪球|xueqiu|RSS|订阅源', re.I)
SOURCE_PATTERNS = {
    'ai_hot': (r'AI\s*(?:热点|热榜|热搜)|人工智能\s*(?:热点|热榜|热搜)|ai[-_ ]?hot[-_ ]?news|aihot(?:\.news)?'
               r'|(?:(?<![a-z])AI(?:圈|领域|行业)?(?![a-z])|人工智能(?:圈|领域|行业)?)(?!\s*(?:日报|工具集))'
               r'(?:(?!不要|不用|无需|不需要|不按|不是|别)[^\n]){0,70}?'
               r'(?:热度|排行(?:榜)?|最火|讨论.{0,4}话题|热议.{0,4}话题)'),
    'ai_daily': r'AI\s*日报|(?:每日|今日|今天|最新)\s*(?:AI|人工智能)\s*(?:新闻|资讯)|AI工具集|ai[-_ ]?bot[-_ ]?daily[-_ ]?news|ai-bot\.cn/daily-ai-news',
    'daily60': r'每日新闻日报|(?:每天|每日)?\s*60\s*(?:秒|s).{0,8}(?:世界|新闻)|daily[-_ ]?news[-_ ]?60s|60秒读懂世界',
    'financial': r'jin10[-_ ]?financial[-_ ]?daily|金十|财经日报|金融日报|financial\s+daily',
    'aggregator': r'news[-_ ]?aggregator|聚合新闻|新闻聚合|多源新闻|(?:28|二十八)\s*源|Hacker\s*News',
}
FAMILY_TOOLS = {
    'ai_hot': {'read_web', 'generate_image'},
    'ai_daily': {'read_web', 'generate_image'},
    'daily60': {'read_web', 'generate_image'},
    'financial': {'financial_daily'},
    'aggregator': {'news_collect'},
    'image': {'generate_image'},
    'web': {'read_web'},
    'platform': {'platform_search', 'platform_read', 'platform_browse', 'platform_transcribe'},
}
STOPWORDS = {'用户', '工具', '技能', '支持', '使用', '用于', '可以', '通过', '相关', '功能', '内容', '返回', '进行',
             '生成', '查询', '获取', '帮助', '提供', '需要', '以及', '一个', '一下', '帮我', '请问', '请你',
             'the', 'and', 'for', 'with', 'from', 'this', 'that', 'tool', 'skill', 'user', 'use', 'get'}


def _clean_text(text):
    from .wechat_quotes import split_quote
    if not isinstance(text, str):return ''
    text = text.split('\n[这条消息引用的资料]', 1)[0]
    text = (split_quote(text) or {}).get('text', text)
    text = re.sub(r'```[^\n]*\n?.*?```', '', text, flags=re.S)
    text = re.sub(r'(?m)^\s*>.*$', '', text)
    # Inline quoted commands are data when the actual request explains them.
    if re.search(r'解释|翻译|这(?:段|句|个)(?:话|文字|内容)|分析这段|总结这段', text):
        text = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"', '', text)
    return text.strip()[:8000]


def request_text(run, state):
    """Current user body only, excluding native quotes and verified own @."""
    turns = state.get('turn_messages')
    task = state.get('agent_schedule')
    original = task.get('request_text') if isinstance(task, dict) and task.get('role') == 'generation' else None
    if isinstance(original, str) and original.strip() and len(original) <= 8000:text = original
    elif (isinstance(task, dict) and task.get('role') == 'generation'
          and str(run.get('input', '')).startswith('这是预先配置的 Agent 公共定时任务。本次在独立上下文执行，结果将共享给符合条件的联系人。')
          and '\n任务：' in run.get('input', '')):
        text = run['input'].split('\n任务：', 1)[1]
    elif isinstance(turns, list):
        text = '\n'.join(m.get('text', '') for m in turns
                         if isinstance(m, dict) and m.get('kind', 'text') == 'text' and isinstance(m.get('text'), str))
    else:text = run.get('input', '')
    contact = state.get('group_contact')
    if isinstance(contact, dict):
        from .group_chats import request_text as without_mention
        text = without_mention(contact, text)
    return _clean_text(text)


def execution_allowed(run, state, tool):
    """Current-body exclusions survive discovery, planning and skill loading.

    This narrows an existing permission; it cannot authorize a tool. In
    particular an explanation of a quoted command is not permission to act.
    """
    text = request_text(run, state)
    builtin = tool.get('builtin', '')
    text_only = bool(re.search(r'(?:只要|只用|只需|仅需|仅要|只发|直接给|只给).{0,6}(?:文字|文本|原文|JSON)|(?:文字|文本)(?:就好|即可|就行)', text, re.I))
    if builtin == 'generate_image' and (text_only or re.search(r'(?:不要|不用|无需|别|不需要).{0,6}(?:图片|海报|配图|生图)', text)):
        return False
    if builtin == 'financial_daily' and (text_only or re.search(r'(?:不要|不用|无需|别|不需要).{0,6}PDF', text, re.I)):
        return False
    if builtin == 'speak' and (text_only or re.search(r'(?:不要|不用|无需|别).{0,6}(?:语音|朗读)', text)):
        return False
    exclusions = {'set_reminder': r'(?:不要|不用|无需|别).{0,6}(?:提醒|定时)',
                  'remember': r'(?:不要|不用|无需|别).{0,6}(?:记住|保存|记录)',
                  'forget': r'(?:不要|不用|无需|别).{0,6}(?:忘记|忘掉|删除)'}
    if builtin in exclusions and re.search(exclusions[builtin], text):return False
    explain_only = bool(re.search(r'只(?:需要|需|要|想|做|是)?(?:解释|翻译|分析|讨论|总结)|仅(?:作|做|需要|需)?(?:解释|翻译|分析|讨论|总结)|(?:不要|不用|无需|禁止|别|不)(?:再|实际|自动)?(?:执行|运行|操作|落实)', text))
    if not explain_only:return True
    if builtin in {'generate_image', 'financial_daily', 'set_reminder', 'remember', 'forget', 'speak'}:return False
    if tool.get('kind') == 'http':return tool.get('method', 'POST').upper() in ('GET', 'HEAD')
    if tool.get('kind') == 'mcp':
        if tool.get('capability') == 'mindmap':return False
        name = tool.get('remote_name') or tool.get('name', '')
        return bool(re.search(r'(?:^|_)(?:search|read|get|list|query|browse|fetch|lookup|weather|direction|geo|regeocode|around|distance|transcribe)(?:_|$)', name, re.I))
    return True


def _routing_text(run, state):
    text = request_text(run, state)
    if state.get('quoted_messages') or not text or len(text) > 100:return text
    continuation = re.search(r'^(?:继续|接着|再来|再给|换个|换一|还有|刚才|那份|这份|上一份|同样|也来)', text)
    history = [m for m in state.get('context', []) if isinstance(m, dict) and isinstance(m.get('content'), str)]
    if history and history[-1].get('role') == 'user' and _clean_text(history[-1]['content']) == text:history = history[:-1]
    if not history:return text
    clarification = (history[-1].get('role') == 'assistant' and
                     re.search(r'城市|起点|终点|出发|位置|哪个平台|哪个来源', history[-1]['content']) and
                     re.search(r'[？?]|请提供|请告诉', history[-1]['content']))
    if (continuation or clarification) and not re.search(r'不用|不要|不查|算了|谢谢|再见', text):
        previous = next((m for m in reversed(history[-4:]) if m.get('role') == 'user'), None)
        if previous:
            original = previous['content']
            contact = state.get('group_contact')
            if isinstance(contact, dict):
                from .group_chats import request_text as without_mention
                original = without_mention(contact, original)
            original = _clean_text(original)[:500]
            return original + '\n补充要求：' + text
    return text


def _normalize(value):return re.sub(r'[\s_\-（）()]+', '', str(value)).casefold()


def _family(skill):
    name = _normalize(skill.get('name', ''))
    if 'jin10financialdaily' in name or name in ('财经日报', '金融日报'):return 'financial'
    if 'aihotnews' in name or 'ai热点新闻' in name:return 'ai_hot'
    if 'aibotdailynews' in name or name in ('ai日报', '每日ai新闻'):return 'ai_daily'
    if 'dailynews60s' in name or '每日新闻日报' in name:return 'daily60'
    if 'newsaggregator' in name:return 'aggregator'
    if 'codeximagegeneration' in name:return 'image'
    if 'agentreach' in name:return 'platform'
    if 'webvideoreader' in name:return 'web'
    return ''


def _skills(state):
    return {sid: s for sid, s in state.get('snapshot', {}).get('skills', {}).items()
            if isinstance(s, dict) and s.get('enabled', True)}


def _tools(state):
    return {tid: t for tid, t in state.get('snapshot', {}).get('tools', {}).items()
            if isinstance(t, dict) and t.get('enabled', True)}


def _tokens(text):
    words = set(re.findall(r'[a-z][a-z0-9_]{2,}', str(text).casefold()))
    for part in re.findall(r'[\u4e00-\u9fff]{2,}', str(text)):
        words.update(part[i:i + 2] for i in range(len(part) - 1))
    return words - STOPWORDS


def _metadata(item):return str(item.get('name', '')) + ' ' + str(item.get('description', ''))[:1200]


def _score(query, item):
    name = _normalize(item.get('name', ''))
    if len(name) >= 4 and name in _normalize(query):return 100
    return min(12, len(_tokens(query) & _tokens(_metadata(item))))


def _positive(pattern, text):
    for match in re.finditer(pattern, text, re.I):
        prefix = re.split(r'[，,。！？!?；;\n]', text[:match.start()])[-1]
        if not re.search(r'(?:不要|不用|无需|不需要|不是|别|不做|不看|不查|排除)(?:再|用|做|查|看|生成|制作|发送|提供|那个|这个|这份|的|\s){0,8}$', prefix):
            return True
    return False


def _intents(text):
    sources = [family for family, pattern in SOURCE_PATTERNS.items() if _positive(pattern, text)]
    intents = list(sources)
    def add(name):
        if name not in intents:intents.append(name)
    if not sources and re.search(r'AI\s*(?:新闻|资讯)|人工智能新闻', text, re.I):add('ai_news')
    elif not sources and re.search(r'新闻|日报|热榜|热搜|资讯', text):add('news')
    # Known source URLs belong to their dedicated skill; separate platform URLs
    # in the same request still add the appropriate reader.
    urls = re.findall(r'https?://[^\s<>"“”]+', text)
    other_urls = [u for u in urls if not re.search(r'aihot\.news|ai-bot\.cn/daily-ai-news|xnews\.jin10\.com', u, re.I)]
    if any(PLATFORM.search(u) for u in other_urls) or (not sources and PLATFORM.search(text)):add('platform')
    if any(not PLATFORM.search(u) for u in other_urls) or re.search(r'(?:阅读|读取|读一下).{0,8}(?:网页|页面|文章|链接)|(?:网页|页面|网址|链接).{0,8}(?:读|总结|概括)|read_web', text, re.I):add('web')
    if re.search(r'(?:生成|画|绘制|制作|做一|设计|修改|编辑|改成|换成|重画).{0,18}(?:图片|图像|插画|海报|头像|壁纸)|(?:图片|图像|插画|海报|头像|壁纸).{0,12}(?:生成|修改|编辑)|generate_image', text, re.I):add('image')
    if re.search(r'天气|气温|降雨|下雨|带.{0,3}伞|weather|forecast', text, re.I):add('weather')
    if re.search(r'高德|\bamap\b', text, re.I):add('amap')
    if re.search(r'导航|怎么走|怎么去|如何去|开车|驾车|骑行|步行|公交|地铁|路线|距离', text) and not re.search(r'学习路线|技术路线|成长路线|职业路线|心理距离', text):add('route')
    if re.search(r'附近|餐厅|咖啡店|酒店|经纬度|坐标|地址|找.{0,8}(?:店|馆|公园|医院)', text):add('places')
    if re.search(r'(?:联网|上网|搜索|搜搜|查一下|查查|核实|查证)', text):add('lookup')
    if re.search(r'歌曲|音乐|歌单|听.{0,5}歌|找.{0,5}歌|music', text, re.I):add('music')
    if re.search(r'电影|影片|影评|movie', text, re.I):add('movie')
    if re.search(r'记忆|记得我|偏好|喜好|我.{0,8}喜欢|我的(?:名字|生日|档案)|记住|忘掉|忘记', text):add('memory')
    if re.search(r'记住|保存.{0,6}(?:偏好|喜好|记忆)', text):add('remember')
    if re.search(r'忘掉|忘记|删除.{0,6}(?:记忆|偏好|记录)', text):add('forget')
    if re.search(r'提醒我|设置.{0,5}提醒|定时.{0,8}(?:任务|发送|发布)|每天.{0,15}(?:提醒|发送|发布)', text):add('reminder')
    if re.search(r'几点|现在.{0,3}时间|今天.{0,3}(?:几号|星期)|当前时间', text):add('clock')
    if re.search(r'计算|算一下|\d\s*[+*/÷×]\s*\d', text):add('calculator')
    if re.search(r'语音|朗读|念给我', text):add('speak')
    if re.search(r'知识库|本地(?:文档|资料)', text):add('knowledge')
    if re.search(r'思维导图|脑图|mind\s*map|TreeMind|树图', text, re.I):add('mindmap')
    return intents, sources


def _skill_candidates(state, text, intents):
    matches = []
    separate_image = any('image' in _intents(clause)[0] and not _intents(clause)[1]
                         for clause in re.split(r'[，,。；;\n]|(?:然后|另外|并且|同时)', text))
    for sid, skill in _skills(state).items():
        family = _family(skill)
        score = _score(text, skill)
        if family == 'image' and set(intents) & NEWS_FAMILIES and not separate_image and score < 100:score = 0
        elif family in intents:score += 60
        elif family in ('ai_hot', 'ai_daily', 'aggregator') and 'ai_news' in intents:score += 20
        elif family in ('daily60', 'aggregator') and 'news' in intents:score += 20
        elif family in NEWS_FAMILIES:score = 0
        elif family and score < 100:score = 0  # Broad descriptions cannot override source selection.
        if score >= (1 if family else 2):matches.append((score, sid))
    return [sid for _, sid in sorted(matches, key=lambda x: -x[0])[:MAX_SKILLS]]


def _active_ids(state):
    names = state.get('active_skills', [])
    return [sid for sid, skill in _skills(state).items() if sid in names or skill.get('name') in names]


def _associated_tools(state, skill):
    family = _family(skill)
    tools = _tools(state)
    if family:return [tid for tid, t in tools.items() if t.get('builtin') in FAMILY_TOOLS.get(family, set())]
    return [tid for _, tid in sorted((( _score(_metadata(skill), t), tid) for tid, t in tools.items()
                                     if t.get('builtin') not in CORE), reverse=True)
            if _score(_metadata(skill), tools[tid]) >= 2][:SEARCH_TOOLS]


def _has_query_skill(state):
    """A relevant reading/query guide can own its source acquisition sequence."""
    skills = _skills(state);tools = _tools(state)
    readonly = {'web_search', 'read_web', 'news_collect', 'weather', 'music_search', 'places_search', 'movie_search',
                'platform_search', 'platform_read', 'platform_browse', 'platform_transcribe', 'financial_daily'}
    for sid in state.get('capability_route', {}).get('candidate_skill_ids', []):
        if sid not in skills:continue
        family = _family(skills[sid])
        if family in NEWS_FAMILIES | {'web', 'platform'}:return True
        if not family and any(tools[tid].get('builtin') in readonly
                              or (tools[tid].get('kind') == 'http' and tools[tid].get('method', 'POST') in ('GET', 'HEAD'))
                              for tid in _associated_tools(state, skills[sid])):
            return True
    return False


def _direct_lookup_needed(run, state):
    from . import web_policy
    current = request_text(run, state)
    route = state.get('capability_route', {})
    text = route.get('routing_text', current)
    if (not web_policy.allowed(state, {**run, 'input': current}) or web_policy.conversation_only(current)
            or web_policy.local_request_only(text) or _has_query_skill(state)):
        return False
    if re.fullmatch(r'(?:你好|您好|在吗|早安|晚安|谢谢|好的|嗯+|哦+|好)[呀啊啦呢吗哦！!。？?，,～~\s]*', current):return False
    external = set(route.get('intents', [])) & (NEWS_FAMILIES | {
        'lookup', 'ai_news', 'news', 'web', 'platform', 'weather', 'route', 'places', 'music', 'movie'})
    if not external and re.search(r'陪我|聊聊天|聊聊|想你|心情|很累|难过|不想上班', current):return False
    live = web_policy.CURRENT.search(text) and (web_policy.factual(text) or re.search(r'大家.{0,6}聊|动静|进展|消息|更新|股市|股价|市场', text))
    return bool(external or live or state.get('analysis', {}).get('needs_web') is True)


def prepare(run, state):
    """Initialize/restabilize a JSON-serializable route, without loading guides."""
    current = request_text(run, state)
    text = _routing_text(run, state)
    intents, sources = _intents(text)
    previous = state.get('capability_route')
    if not isinstance(previous, dict):previous = {}
    same = previous.get('version') == VERSION and previous.get('request_text') == current
    skills = _skills(state);tools = _tools(state)
    candidates = _skill_candidates(state, text, intents)
    if same:
        candidates += [sid for sid in previous.get('candidate_skill_ids', []) if sid in skills and sid not in candidates]
    state['capability_route'] = {
        'version': VERSION, 'enabled': bool(skills) or len(tools) > LARGE_TOOLSET,
        'request_text': current, 'routing_text': text, 'intents': intents, 'news_families': sources,
        'candidate_skill_ids': candidates[:MAX_SKILLS],
        'tool_ids': [],
        'expanded_tool_ids': [tid for tid in previous.get('expanded_tool_ids', []) if tid in tools] if same else [],
        'eligible_tool_ids': [tid for tid in previous.get('eligible_tool_ids', []) if tid in tools] if same else [],
        'eligibility_recorded': bool(same and previous.get('eligibility_recorded')),
        'searches': previous.get('searches', [])[-5:] if same else [],
    }
    return state['capability_route']


def catalog_skills(state):
    skills = _skills(state)
    route = state.get('capability_route')
    if not isinstance(route, dict) or not route.get('enabled'):return list(skills.values())
    ids = list(dict.fromkeys(route.get('candidate_skill_ids', []) + _active_ids(state)))
    return [skills[sid] for sid in ids if sid in skills]


def skill_allowed(state, skill):
    """Keep an explicit source-specific task on its requested news family."""
    skills = _skills(state)
    if isinstance(skill, dict):
        matches = [s for sid, s in skills.items() if skill is s or skill.get('id') == sid
                   or (skill.get('name') and skill.get('name') == s.get('name'))]
    else:matches = [s for sid, s in skills.items() if skill in (sid, s.get('name'))]
    if len(matches) != 1:return False
    skill = matches[0]
    family = _family(skill)
    sources = state.get('capability_route', {}).get('news_families', [])
    return not (sources and family in NEWS_FAMILIES and family not in sources)


def source_rules(state):
    route = state.get('capability_route', {})
    if not route.get('enabled'):return ''
    names = [s.get('name', '') for s in catalog_skills(state)]
    discovery = ''
    if not names:
        index = [s.get('name', '') for s in _skills(state).values()][:20]
        discovery = ('未预选不等于没有能力。已绑定技能名称索引（最多20项）：' + '、'.join(index) + '。'
                     '没有匹配技能时，已授权的基础查询工具仍可独立使用。需要联网就直接调用已提供的web_search，'
                     '必要时用read_web读取原来源，无需先加载技能，也无需先调用search_capabilities。'
                     '只有现有工具不足或需要发现其他能力时，才用search_capabilities改写任务关键词检索；'
                     '不能只因候选为空就断言无法完成。普通闲聊无需搜索。')
    return ('\n# 本轮能力选择\n当前候选技能：' + '、'.join(names) + '。' + discovery +
            'AI热点新闻用于AI热榜及热度；AI日报/每日AI新闻用于AI工具集最新一期；'
            '每日新闻日报用于60秒全球新闻；jin10-financial-daily用于金十财经PDF；'
            'news-aggregator-skill用于多源聚合。明确指定来源时遵循该来源，不用近名技能替换。'
            '任务包含多个明确来源时分别执行；来源不明确先澄清或搜索能力。'
            '网页和平台阅读器可以辅助取资料，不替换专用日报流程。'
            '命中专用技能时遵循其取源流程；没有适用技能时，已提供的web_search等基础工具可直接执行查询，'
            '用户指定来源时继续读取该来源。当前目录和工具只是相关子集，现有工具不足才调用search_capabilities；'
            '加载技能不会执行任务，也不取消工具审批。')


def activate_skill(state, name):
    matches = [(sid, s) for sid, s in _skills(state).items() if name in (sid, s.get('name'))]
    if len(matches) != 1:return
    route = state.get('capability_route')
    if not isinstance(route, dict):return
    sid, skill = matches[0]
    if not skill_allowed(state, skill):return
    if sid not in route['candidate_skill_ids']:
        route['candidate_skill_ids'] = (route['candidate_skill_ids'] + [sid])[-MAX_SKILLS:]
    run = {'input': route.get('request_text', '')}
    ids = [tid for tid in _associated_tools(state, skill) if execution_allowed(run, state, _tools(state)[tid])]
    route['expanded_tool_ids'] = list(dict.fromkeys(route.get('expanded_tool_ids', []) + ids))


def _relevant_tools(run, state):
    route = state['capability_route'];intents = set(route['intents']);text = route['routing_text']
    builtins = set()
    bound_families = {_family(_skills(state)[sid]) for sid in route.get('candidate_skill_ids', []) if sid in _skills(state)}
    for family in intents:
        related = FAMILY_TOOLS.get(family, set())
        # Without the bound source-specific guide, fall back to basic queries;
        # a news keyword alone does not acquire that guide's default artifacts.
        if family in NEWS_FAMILIES and family not in bound_families:related = related - {'generate_image', 'financial_daily'}
        builtins.update(related)
    if 'ai_news' in intents or 'news' in intents:builtins.update({'news_collect', 'read_web'})
    for intent, tool in [('weather', 'weather'), ('music', 'music_search'), ('movie', 'movie_search'),
                         ('memory', 'memory_search'), ('remember', 'remember'), ('forget', 'forget'),
                         ('reminder', 'set_reminder'), ('clock', 'clock'), ('calculator', 'calculator'),
                         ('speak', 'speak'), ('knowledge', 'knowledge_search')]:
        if intent in intents:builtins.add(tool)
    route['direct_lookup'] = _direct_lookup_needed(run, state)
    if route['direct_lookup']:builtins.update({'web_search', 'read_web'})
    map_names = set()
    weather_failed = any(isinstance(r, dict) and r.get('provider') == 'open-meteo' and r.get('ok') is False
                         for r in state.get('weather_results', []))
    has_weather = any(t.get('builtin') == 'weather' for t in _tools(state).values())
    if 'weather' in intents and ('amap' in intents or weather_failed or not has_weather):
        map_names.update({'maps_weather', 'maps_geo'})
        if 'amap' in intents:builtins.discard('weather')
    if 'route' in intents:
        map_names.add('maps_geo')
        modes = [('步行', 'walking'), ('骑行', 'bicycling'), ('公交|地铁', 'transit_integrated'), ('开车|驾车', 'driving')]
        chosen = [suffix for pattern, suffix in modes if re.search(pattern, text)]
        map_names.update('maps_direction_' + mode for mode in chosen or ['driving', 'transit_integrated'])
        if '距离' in text:map_names.add('maps_distance')
    if 'places' in intents:
        map_names.update({'maps_geo', 'maps_around_search' if '附近' in text else 'maps_text_search'})
        if '坐标' in text or '经纬度' in text:map_names.add('maps_regeocode')
        if 'amap' not in intents:builtins.add('places_search')
    ids = []
    for tid, tool in _tools(state).items():
        if (tool.get('builtin') in builtins or tool.get('remote_name') in map_names
                or ('mindmap' in intents and tool.get('capability') == 'mindmap')):
            ids.append(tid)
    for sid in route.get('candidate_skill_ids', []):
        if sid in _skills(state):ids.extend(_associated_tools(state, _skills(state)[sid]))
    # Generic custom tools are discoverable by their public metadata, never by
    # connection config or arbitrary instructions in an imported package.
    ids.extend(tid for tid, t in _tools(state).items() if not t.get('builtin') and t.get('capability') not in ('amap', 'mindmap')
               and _score(text, t) >= 2)
    return list(dict.fromkeys(ids))


def _plan_tools(state):
    steps = state.get('task_plan', {}).get('steps', [])
    return list(dict.fromkeys(tid for step in steps if isinstance(step, dict)
                             and step.get('status', 'pending') not in ('done', 'failed', 'uncertain')
                             for tid in step.get('tool_ids', []) if isinstance(tid, str)))


def _tool_id(tool, state):
    if tool.get('id'):return tool['id']
    return next((tid for tid, value in state.get('snapshot', {}).get('tools', {}).items()
                 if value is tool or value == tool), '')


def select_tools(run, state, eligible_tools):
    if not isinstance(state.get('capability_route'), dict):prepare(run, state)
    route = state['capability_route']
    eligible = {_tool_id(t, state): t for t in eligible_tools if t.get('enabled', True) and execution_allowed(run, state, t)}
    eligible.pop('', None)
    route['eligible_tool_ids'] = list(eligible)
    route['eligibility_recorded'] = True
    if not route['enabled']:
        route['selected_tool_ids'] = list(eligible)
        return list(eligible.values())
    route['tool_ids'] = _relevant_tools(run, state)
    core = [tid for tid, t in eligible.items() if t.get('builtin') in CORE]
    active = [tid for sid in _active_ids(state) for tid in _associated_tools(state, _skills(state)[sid])]
    priority = core + _plan_tools(state) + list(reversed(route.get('expanded_tool_ids', []))) + route['tool_ids'] + active
    selected = [tid for tid in dict.fromkeys(priority) if tid in eligible][:MAX_TOOLS]
    route['selected_tool_ids'] = selected
    return [eligible[tid] for tid in selected]


def _search_eligible(run, state):
    from . import mcp_policy, web_policy
    result = {}
    route = state.get('capability_route', {})
    known = route.get('eligible_tool_ids') if route.get('eligibility_recorded') else None
    memory = state.get('snapshot', {}).get('agent', {}).get('companion', {}).get('memory', {})
    for tid, tool in _tools(state).items():
        if known is not None and tid not in known:continue
        if not execution_allowed(run, state, tool):continue
        if not mcp_policy.eligible(tool, run, state):continue
        if tool.get('builtin') in web_policy.ONLINE and not web_policy.allowed(state, run):continue
        if tool.get('builtin') == 'remember' and memory.get('enabled', True) and memory.get('auto_extract', False):continue
        result[tid] = tool
    return result


def search(run, state, query):
    """Find public metadata only; execution and approvals stay with the caller."""
    if not isinstance(state.get('capability_route'), dict):prepare(run, state)
    if not isinstance(query, str) or not query.strip():return {'ok': True, 'skills': [], 'tools': []}
    query = _clean_text(query)[:1000]
    route = state['capability_route']
    intents, _ = _intents(query)
    candidates = _skill_candidates(state, query, intents)
    candidates = [sid for sid in candidates if skill_allowed(state, _skills(state)[sid])][:SEARCH_SKILLS]
    eligible = _search_eligible(run, state)
    related = [tid for sid in candidates for tid in _associated_tools(state, _skills(state)[sid]) if tid in eligible]
    ranked = sorted((( _score(query, t), tid) for tid, t in eligible.items() if t.get('builtin') not in CORE),
                    key=lambda pair: -pair[0])
    ids = list(dict.fromkeys(related + [tid for score, tid in ranked if score >= 2]))[:SEARCH_TOOLS]
    route['candidate_skill_ids'] = list(dict.fromkeys(candidates + route.get('candidate_skill_ids', [])))[:MAX_SKILLS]
    route['expanded_tool_ids'] = list(dict.fromkeys(route.get('expanded_tool_ids', []) + ids))
    route['searches'] = (route.get('searches', []) + [{'query': query, 'skill_ids': candidates, 'tool_ids': ids}])[-5:]
    return {'ok': True,
            'skills': [{'id': sid, 'name': _skills(state)[sid]['name'], 'description': _skills(state)[sid].get('description', '')}
                       for sid in candidates],
            'tools': [{'id': tid, 'name': eligible[tid]['name'], 'description': eligible[tid].get('description', ''),
                       'approval': eligible[tid].get('approval', 'auto')} for tid in ids],
            'instruction': '仅搜索已授权能力，未执行任务。技能先用use_skill加载；相关工具将在下一轮出现，仍须遵守审批。'}
