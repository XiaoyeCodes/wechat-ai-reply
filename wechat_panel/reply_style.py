"""Stable per-turn expression choices; never randomize facts or tool decisions."""
import hashlib
import re

from .companion_models import Companion

EMOJI=re.compile('[\U0001f300-\U0001faff\u2600-\u27bf]')


def expression(run,state):
    persona=Companion.model_validate(state['snapshot']['agent'].get('companion',{})).persona
    if not persona.enabled or not persona.natural_rhythm:return None
    # The same run keeps its choice through tool calls, approvals and retries.
    if 'reply_expression' in state:return state['reply_expression']
    seed=hashlib.sha256(str(run['id']).encode()).digest()
    recent=[m['content'] for m in state.get('context',[]) if m['role']=='assistant' and isinstance(m['content'],str)][-2:]
    serious=state.get('analysis',{}).get('risk') in ('support','urgent') or bool(re.search(
        r'难过|伤心|去世|生病|分手|自杀|抑郁|亏损|事故|投诉',run.get('input','')))
    emoji=(int.from_bytes(seed[:4],'big')/2**32<persona.emoji_probability
           and not serious and not any(EMOJI.search(t) for t in recent))
    rhythm=['直接接话，能用一句表达就到这里','自然聊一小段，想说的说完就停','需要解释时适当展开，分段取决于内容'][seed[4]%3]
    value={'rhythm':rhythm,'emoji_allowed':emoji,'recent_questions':sum(t.rstrip().endswith(('？','?')) for t in recent)}
    state['reply_expression']=value
    return value


def prompt(run,state):
    choice=expression(run,state)
    if not choice:return ''
    text=('\n# 本轮表达节奏（事实与任务要求优先）\n'
          '沿用上面设定的角色性格和说话方式，查资料后也保持这个声音，不切换为报告或说教口吻。'
          '长短由内容决定，不把“一至三句”写成固定三段。简单接话通常一小段；复杂问题再展开，用户要求详细时不要省略关键内容。'
          '不要套用“先附和—再解释—最后建议或追问”的固定模板，不为了凑结构新增话题。'
          '表达亲近可以轻柔、俏皮，但不用每轮昵称、撒娇或反问；不为迎合用户附和没有依据的判断。'
          '不要凭空补上用户的习惯或过去经历。记忆只在相关时自然提及，不把每个问题绕回宠物或旧话题。'
          '历史助手消息只是背景，不模仿其固定段式、口头禅或每轮表情。'
          '\n本轮倾向：'+choice['rhythm']+'。')
    text+=('\n情境合适时可以自然用一个 emoji，也可以不用；贴合当下情绪，不固定用同一个。' if choice['emoji_allowed'] else
           '\n这轮不用 emoji，用文字表达语气。')
    if choice['recent_questions']:text+='\n近期已经用问题结尾，这轮优先自然收尾；只有完成用户任务确实缺少信息时再提问。'
    return text
