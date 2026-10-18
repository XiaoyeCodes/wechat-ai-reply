"""Opt-in conversational pacing, stable within one run and sensitive to context."""
import hashlib
import re
from .web_policy import conversation_only


def instruction(run,state):
    if state.get('news_ready') or state.get('native_news'):return ''
    agent=state['snapshot']['agent']
    if agent.get('kind')!='workflow' or '【按语境组织回复】' not in agent.get('prompt',''):return ''
    if 'dialogue_pacing' not in state:
        text=run['input']
        if conversation_only(text) or re.fullmatch(r'(?:嗯+|哦+|好[的呀啊]?|知道了|谢谢[你啦]?|在吗|晚安|早安|你好)[！!。？?～~\s]*',text):
            mode='brief'
        elif re.search(r'简短|一句话|别长篇|不要解释|少说',text):mode='brief'
        elif re.search(r'详细|展开|一步步|分析|解释原因|讲个故事|多说一点|认真聊',text) or len(text)>160:
            mode='develop'
        else:
            recent=[m.get('content','') for m in state.get('context',[]) if m.get('role')=='assistant' and isinstance(m.get('content'),str)][-2:]
            repetitive=len(recent)==2 and all(len(re.split(r'\n\s*\n',m.strip()))>=3 for m in recent)
            modes=('brief','flow') if repetitive else ('brief','flow','flow','warm')
            mode=modes[int.from_bytes(hashlib.sha256(run['id'].encode()).digest()[:4],'big')%len(modes)]
        state['dialogue_pacing']=mode
    hint={'brief':'这轮直接回应核心意思，用一个自然短回应说清就停，不拆段，不附加总结、舞台动作或反问。',
          'develop':'用户需要认真展开，按具体内容充分说明；不要为了模仿短聊天省略必要信息。',
          'flow':'像接着聊天一样把意思说顺，按话题自然收束，不刻意拆行或凑段落。',
          'warm':'先接住对方这句话，有值得回应的细节再多聊一点；不固定搭配动作、安慰和提问。'}[state['dialogue_pacing']]
    return '\n本轮对话节奏：'+hint+' 用户明确要求的长度和格式优先。参考最近对话，避免重复同一种开头、结尾和段数；不为了随机变化添加无关内容。'


def compact_short_reply(run,state,reply):
    """Join gratuitous blank lines in brief chat, preserving every sentence."""
    if state.get('dialogue_pacing')!='brief' or len(reply)>240:return reply
    if re.search(r'分段|换行|逐行|列表|诗|歌词|代码|格式',run['input']):return reply
    if re.search(r'```|https?://|^\s*(?:[-*>#]|\d+[.)、])',reply,re.M):return reply
    return re.sub(r'\n\s*\n',' ',reply).strip()
