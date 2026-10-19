"""Per-turn lookup decisions and evidence, independent of character dialogue."""
from datetime import datetime
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ONLINE={'web_search','read_web','news_collect','weather','music_search','places_search','movie_search',
        'platform_search','platform_read','platform_browse','platform_transcribe'}
NO_WEB=re.compile(r'(?:不要|不用|别|禁止|无需)(?:再|去)?(?:联网|上网|搜索|搜|查)|只(?:能)?(?:根据|用)(?:我|上述|上面|以下).{0,12}(?:资料|内容|文字)')
EXPLICIT=re.compile(r'(?:联网|上网)(?:查|搜)?|(?:帮我|请|你可以|能不能|给我).{0,5}(?:搜|搜索|查一下|查查|查证|核实)|搜索|查一查')
FACT=re.compile(r'是什么|什么(?:意思|东西|理论|梗)|哪些|谁是|是谁|怎么回事|真的吗|是否属实|多少|推荐|新闻|热搜|热榜|议论|讨论|发生|售价|价格|版本|政策|汇率')
CURRENT=re.compile(r'今天|今日|明天|最近|近期|最新|目前|现在|实时|当下|刚刚|近几天')
PERSONAL=re.compile(r'我的(?:名字|生日|猫|狗|宠物|喜好)|我叫|记得我|记住我|不想上班|不想工作|好累|很累|好难过|心情不好|想你')
TRANSFORM=re.compile(r'^(?:请|帮我)?(?:翻译|改写|润色|续写|复述)|你(?:刚才|之前|到底).{0,8}(?:搜|查|联网).{0,3}(?:吗|了吗|没有)')
CLAIM=re.compile(r'(?:我)?(?:搜|查)(?:了一圈|了一下|了下|到了|过了|过一遍)|我(?:已经)?(?:搜过|查过)|(?:已经|刚刚|刚才)(?:帮你|为你)?(?:搜|查)|(?:没|没有|暂时没)(?:搜到|查到)|根据(?:搜索|检索|网上)(?:的)?(?:结果|资料)')
UNCERTAIN=re.compile(r'不(?:知道|清楚|确定|了解)|没(?:听过|法确认)|无法确认|拿不准')
SOURCE_REQUEST=re.compile(r'(?:给|发|附|带|提供|标注|列出|保留|贴).{0,8}(?:来源|出处|链接|网址|原文)|(?:来源|出处|链接|网址)(?:呢|在哪|是什么|给我)|哪里(?:看到|查到)|source|citation',re.I)
NO_SOURCES=re.compile(r'(?:不要|不用|别|无需).{0,4}(?:附|带|给|贴|提供)?(?:来源|出处|链接|网址|参考)')


def conversation_only(text):
    """Self/role/previous-action questions are answered from the conversation.

    Every clause must be conversational: mixed requests can still fetch news.
    """
    if re.fullmatch(r'(?:(?:你好|您好|在吗|早安|晚安|谢谢[你啦]?|嗯+|哦+|好[的呀啊]?|知道了)[！!。？?，,～~\s]*)+',text.strip()):return True
    clauses=[s.strip(' “”\"「」') for s in re.split(r'[，,。！？!?；;\n]',text) if s.strip()]
    identity=re.compile(r'(?:你|您).{0,12}(?:是不是|是|算是).{0,8}(?:AI|人工智能|机器人|真人|人类|程序)|(?:你|您).{0,8}(?:是谁|叫什么|名字|身份|人设)|(?:你|您).{0,14}(?:搜|查|联网).{0,8}(?:什么|干什么|了吗|了没有|吗|呢)|(?:不用|不需要|别).{0,5}(?:搜|查|联网)',re.I)
    followup=re.compile(r'^(?:这|那|刚才说的)?(?:是真的吗|真的假的|真的吗|是真的|为什么|怎么回事|我只是问你|我问的是你|回答我|说实话|嗯|哦)[呀啊呢吧嘛]*$')
    return bool(clauses) and any(identity.search(c) for c in clauses) and all(identity.search(c) or followup.fullmatch(c) for c in clauses)


def source_reason(run,state,reply):
    text=run['input'];policy=state['snapshot']['agent'].get('source_policy','auto')
    if NO_SOURCES.search(text):return ''
    if SOURCE_REQUEST.search(text):return '用户要求来源'
    if policy=='always':return '配置为附带来源'
    if policy=='requested':return ''
    # A long search payload alone does not make a one-line factual answer a summary.
    material=json.dumps(state.get('web_context',[]),ensure_ascii=False)
    material+=''.join(str(m.get('content','')) for m in state.get('messages',[]) if m.get('role')=='tool')
    summarizing=bool(re.search(r'总结|摘要|概括|梳理|精简|要点|说了什么|讲了什么|说了啥',text))
    if len(material)>=2400 and len(material)>max(1,len(reply))*4 and (summarizing or len(reply)>=260):
        return '长内容已精简，提供原文继续阅读'
    return ''


def factual(text):return bool(FACT.search(text)) and not PERSONAL.search(text) and not conversation_only(text)


def claimed_lookup(text):
    # Quoted user/source wording is not the assistant claiming an action.
    clean=re.sub(r'[“「『"].*?[”」』"]','',text,flags=re.S)
    clean=re.sub(r'(?:没有|没|还没|尚未|并未|并没有)(?:实际)?(?:联网|搜索|查过|搜过)', '', clean)
    return bool(CLAIM.search(clean))


def allowed(state,run):
    return state['snapshot']['agent'].get('web_policy','auto')!='off' and not NO_WEB.search(run['input']) and not conversation_only(run['input'])


def query_for(run,state):
    text=run['input']
    if EXPLICIT.search(text) and not FACT.search(text) and len(text)<35:
        previous=next((m['content'] for m in reversed(state.get('context',[]))
                       if m['role']=='user' and m['content']!=text and isinstance(m['content'],str)), '')
        if previous:text=previous+' '+text
    if CURRENT.search(text):text=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d')+' '+text
    if re.search(r'推特|Twitter|(?<!\w)X(?:上|平台)',text,re.I):text+=' site:x.com OR site:twitter.com'
    return text[:1000]


def decide(run,state):
    text=run['input'];policy=state['snapshot']['agent'].get('web_policy','auto')
    if conversation_only(text):return dict(required=False,reason='身份、角色或对话记录追问，直接依据当前设定和会话回答')
    if not allowed(state,run):return dict(required=False,reason='本轮关闭联网检索')
    from .mcp_policy import route
    selected=route(run,state)
    if selected:return dict(required=False,reason='优先使用已授权的专业工具；缺少输入先询问',capability=selected)
    if any(t.get('builtin')=='platform_search' and t.get('enabled') for t in state['snapshot'].get('tools',{}).values()):
        from .platform_catalog import PLATFORMS
        platform_names=list(PLATFORMS.values())+['推特','小红书','领英','BOSS','bilibili','youtu.be','x.com','xiaohongshu.com','reddit.com','github.com','v2ex.com','xueqiu.com','xiaoyuzhoufm.com']
        if any(name.lower() in text.lower() for name in platform_names if name not in ('网页','全网搜索')):
            return dict(required=False,reason='用户指定平台，由模型选择已授权的平台搜索或读取工具')
    if policy=='model':return dict(required=False,reason='由模型自行选择工具')
    if TRANSFORM.search(text):return dict(required=False,reason='文字处理或询问操作记录，不主动查询')
    url=re.search(r'https?://[^\s<>"\u4e00-\u9fff，。！？）]+',text)
    reason='日常交流或稳定知识，按需使用工具'
    if url and not state.get('input_media_id'):
        return dict(required=True,reason='用户提供公开链接',builtin='read_web',arguments={'url':url.group(0)[:2000]})
    if EXPLICIT.search(text):reason='用户明确要求查证'
    elif CURRENT.search(text) and factual(text):reason='涉及近期或实时事实'
    elif state.get('analysis',{}).get('needs_web') is True and not PERSONAL.search(text) and not state.get('input_media_id'):
        reason='事实需要外部来源核实'
    else:return dict(required=False,reason=reason)
    return dict(required=True,reason=reason,builtin='web_search',arguments={'query':query_for(run,state)})


def evidence(result):
    try:obj=json.loads(result)
    except (ValueError,TypeError):obj={}
    if isinstance(obj,dict) and (obj.get('ok') is False or obj.get('error') or obj.get('isError') or obj.get('_meta',{}).get('ai.exa/rateLimited')):
        return {'status':'error','sources':[]}
    if isinstance(obj,dict) and obj.get('coverage')=='metadata_only':return {'status':'partial','sources':[]}
    urls=[]
    def add(url):
        if isinstance(url,str) and urlsplit(url).scheme in ('http','https') and url not in urls:urls.append(url)
    def walk(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k in ('url','source','link'):add(v)
                elif k=='text' and isinstance(v,str):
                    for u in re.findall(r'(?:URL|Source):\s*(https?://[^\s\\]+)',v):add(u.rstrip('.,。'))
                elif isinstance(v,(dict,list)):walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
        elif isinstance(value,str):
            for u in re.findall(r'(?:URL|Source):\s*(https?://[^\s\\]+)',value):add(u.rstrip('.,。'))
    walk(obj)
    return {'status':'ok' if urls else 'empty','sources':urls[:8]}


class WebPolicy:
    def __init__(self,ai):self.ai=ai

    def prepare(self,run,state):
        # Public tasks explicitly choose their skills/tools; a general news
        # classifier must not replace their source with another news pipeline.
        if state.get('agent_schedule') or state.get('group_sender'):return True
        from .skills import explicitly_requested
        if explicitly_requested(state,run['input']):return True
        from . import news_native
        if news_native.requested(run,state):return news_native.prepare(self.ai,run,state)
        from .news import requested, NewsResearch
        if not news_native.bound(state) and requested(run,state):return NewsResearch(self.ai).prepare(run,state)
        if 'web_decision' not in state:
            state['web_decision']=decide(run,state)
            self.ai.data.trace(run['id'],'联网判断',state['web_decision'])
            self.ai.data.update_run(run['id'],state=state)
        decision=state['web_decision']
        if decision['required'] and not state.get('web_prepared'):
            return self.lookup(run,state,decision,'web-required')
        return True

    def lookup(self,run,state,decision,call_id):
        tool=next((t for t in state['snapshot']['tools'].values() if t['enabled'] and t.get('builtin')==decision['builtin']),None)
        if tool is None:
            state['web_prepared']=True
            state.setdefault('web_evidence',[]).append({'status':'unavailable','sources':[],'tool':decision['builtin']})
            state.setdefault('web_context',[]).append({'ok':False,'error':'本轮所需联网工具未授权，无法查证。'})
            self.ai.data.trace(run['id'],'联网不可用',{'reason':'所需工具未授权','builtin':decision['builtin']})
        else:
            result=self.ai._tool(run,state,{'id':call_id,'tool_id':tool['id'],'arguments':decision['arguments']})
            if result is None:return False # existing approval flow resumes here
            state['web_prepared']=True
            state.setdefault('web_context',[]).append({'tool':tool['name'],'result':result})
        self.ai.data.update_run(run['id'],state=state)
        return True

    @staticmethod
    def prompt(agent=None):
        policy=(agent or {}).get('source_policy','auto')
        links={'auto':'短问答和普通聊天不附参考链接。只有把较长外部内容压缩为摘要、值得继续阅读原文，或用户明确要来源时，附一两个实际来源。',
               'requested':'只有用户明确索要来源时才附参考链接。',
               'always':'使用联网资料回答时附一两个实际来源。'}[policy]
        return ('\n# 本轮联网与证据规则\n'
                '只有本轮实际工具结果能证明本轮查过，历史助手说过“查到了”不是证据。'
                '未调用工具时禁止说“搜了一圈/搜了下/查到了/没搜到”。'
                '陌生术语、拿不准的外部事实先查询，普通陪伴与个人记忆不需要联网。身份、角色及对刚才回复的追问直接回答，不能仅因“刚刚”“真的吗”搜索。'
                '搜索失败与没有匹配结果是不同情况，限流时说明当前无法查询。'
                '来源不匹配则说明尚未核实，不能补写细节。新闻必须核对日期。'
                '已有本轮查询结果时先使用结果；只有确实缺少信息才补充搜索，不重复查询同一问题。'
                '普通网页或新闻搜索不能证明推特实时热度；没有实际平台帖子时直接说明未取得平台内容。'
                '来源保留在运行日志，不必在聊天正文展示检索过程。'+links+
                '用户明确不需要链接时遵从。音乐、地图、生成文件等本身就是交付结果的链接正常提供。'
                '只能引用实际工具返回的来源，不杜撰网址；按用户问题简短回答。')

    def check(self,run,state,reply):
        if state.get('native_news'):
            from .news_native import finish
            return finish(self.ai,run,state,reply)
        if state.get('news_ready'):
            from .news import render
            state['web_checked_reply']=render(run,state,reply)
            self.ai.data.trace(run['id'],'新闻汇总完成',{'verified_items':len(state['news_packet']['items']),
                'sources':[item['url'] for item in state['news_packet']['items']]})
            return 'done'
        attempts=state.get('web_evidence',[])
        has_results=any(e['status']=='ok' for e in attempts)
        claim=claimed_lookup(reply) and not TRANSFORM.search(run['input']) and not conversation_only(run['input'])
        uncertain=factual(run['input']) and bool(UNCERTAIN.search(reply))
        auto=state['snapshot']['agent'].get('web_policy','auto')=='auto'
        if not attempts and not state.get('web_repair') and auto and allowed(state,run) and (claim or uncertain):
            state['web_repair']=True
            decision={'required':True,'reason':'回复称已查证或事实不确定，但没有本轮工具记录','builtin':'web_search','arguments':{'query':query_for(run,state)}}
            state['web_repair_decision']=decision
            self.ai.data.trace(run['id'],'联网补查',{'reason':decision['reason']})
            self.ai.data.update_run(run['id'],state=state)
            return 'lookup'
        required=state.get('web_decision',{}).get('required') or state.get('web_repair')
        if (required and not has_results) or (claim and not has_results):
            failed=any(e['status'] in ('error','unavailable','disabled') for e in attempts)
            partial=any(e['status']=='partial' for e in attempts)
            text=('这次查询服务暂时不可用，我还没法核实这个问题，不能把它当作“没有搜到”。稍后可以再试。' if failed else
                  '目前只拿到部分页面信息，还不足以核实内容。你可以补充正文或更具体的来源。' if partial else
                  '这次查询没有取得足够的匹配来源，我暂时不能确认。可以补充具体名称、截图或出处。' if attempts else
                  '这次没有进行联网查证，我暂时不能确认这个说法。')
            state['web_checked_reply']=text
            self.ai.data.trace(run['id'],'回复核验',{'action':'使用未核实说明','attempts':len(attempts),'successful':has_results})
        else:
            sources=list(dict.fromkeys(u for e in attempts if e['status']=='ok' for u in e['sources']))
            reason=source_reason(run,state,reply)
            if sources and reason and not any(u in reply for u in sources):reply+='\n\n'+('原文：' if '精简' in reason else '参考：')+'\n'.join(sources[:2])
            elif not reason:
                # Remove only a trailing reference footer, never inline delivery links.
                reply=re.sub(r'\n\s*(?:参考(?:来源|链接|资料)?|来源|出处|原文)[：:].*https?://.*\Z','',reply,flags=re.S).rstrip()
            if sources:self.ai.data.trace(run['id'],'来源展示',{'attached':bool(reason),'reason':reason or '简短回答，来源保留在运行记录','sources':sources})
            state['web_checked_reply']=reply
        return 'done'
