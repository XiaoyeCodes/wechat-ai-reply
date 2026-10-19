"""Per-contact reply preference and delivery through the serial UI worker."""
import re
import tempfile
from pathlib import Path

from .bridge import BridgeError


class ReplySuperseded(BridgeError):
    """The turn or its output preference changed before the send click."""


def requested_format(text):
    """Only explicit, direct requests; quotes, reported speech and hypotheticals are data."""
    if len(text)>300 or re.search(r'[“”「」『』"`]|https?://|如果|假如|例如|比如|他说|她说|用户说|翻译|总结|什么意思|怎么实现|写.{0,15}(?:文章|故事|代码|提示词)|教程',text):
        return None
    text=re.sub(r'\s+','',text)
    if re.search(r'(?:我(?:刚刚|刚|已经|曾经|正在)|他|她|别人|对方).{0,6}(?:发|用).*语音|不要(?:关闭|取消|停用)',text):return None
    speech=r'(?:语音|声音)'
    writing=r'(?:文字|文本|打字)'
    action=r'(?:回复|回答|回我|和我(?:说话|聊天)|跟我(?:说话|聊天))'
    off=rf'(?:不要|别|不用|停止|关闭|取消|不想听|不想用)(?:再|给我|用|发|你的|自动|一直|每条都|总是)*{speech}'
    on=rf'(?:不要|别|不用)(?:再|给我|用|发)*{writing}(?:{action})?'
    voice=rf'(?:改(?:成|用|为)|切换(?:成|到|为)|开启|打开|启用|用|发送|发|以后|接下来)(?:一下|成|为)?{speech}(?:来|给我)?(?:{action})?'
    plain=rf'(?:改(?:回|成|用|为)|切换(?:成|到|为)|开启|用|发)(?:一下|成|为)?{writing}(?:来|给我)?(?:{action})?'
    matches=[]
    for pattern,mode in ((off,'text'),(on,'voice'),(voice,'voice'),(plain,'text')):
        for match in re.finditer(pattern,text):
            # A negative request must not also be interpreted as its positive suffix.
            if mode=='voice' and pattern==voice and re.search(r'(不要|不用|别|停止|关闭|取消).{0,8}$',text[:match.start()]):continue
            if mode=='text' and pattern==plain and re.search(r'(不要|不用|别).{0,3}$',text[:match.start()]):continue
            # "发语音消息怎么做" is a question about the feature, not a preference.
            tail=text[match.end():]
            if re.search(r'怎么|如何|是什么|的方法|的功能|功能吗|的时候|会怎样',tail):continue
            matches.append((match.start(),mode))
    return max(matches)[1] if matches else None


def preference_prompt(contact):
    mode=contact.get('reply_format','text') if contact else 'text'
    if mode=='voice':
        return ('\n本会话已启用真实微信原生语音回复，系统会把你的正文转成语音并发送。'
                '无需调用 speak 工具、无需说明技术流程，不要声称无法发送语音。日常聊天适合直接听：'
                '通常控制在 120 字以内，保留角色风格，不读出表情、Markdown 或动作括号。'
                '需要完整解释、链接、新闻清单或代码时保留完整正文，系统会改发文字，不为语音省略重要信息。')
    return ('\n系统具有真实微信原生语音发送能力，本会话当前使用文字回复。'
            '当前用文字不代表此前的语音没有发出；历史中标记“已通过微信原生语音发送”的消息已经核对送达气泡，'
            '不要否认或声称那些消息只是文字。只有联系人明确要求切换回复形式时系统才会更新设置。')


def switch_only(text):
    """A settings command needs an acknowledgement, not a fresh roleplay essay."""
    if not requested_format(text):return False
    clean=re.sub(r'[\s，。！？,.!?～~]','',text)
    words=(r'阿慧|Alice|小糯|你|请|麻烦|帮我|可以|能不能|能|以后|接下来|现在|还是|就|一直|都|'
           r'我想|我希望|给我|再|别|不要|不用|停止|关闭|取消|不想听|开启|打开|启用|'
           r'改回|改成|改用|改为|切换成|切换到|切换为|用|发|发送|语音|声音|文字|文本|打字|'
           r'回复|回答|回我|来|跟我|和我|聊天|说话|我|吧|呀|啦|了|吗|好不好|行不行|好嘛|一下|每条')
    return len(clean)<=60 and bool(re.fullmatch('(?:'+words+')+',clean))


def speech_text(text):
    if re.search(r'https?://|www\.|```|^\s*(?:[-*]|\d+[.、])\s',text,re.M):return None
    text=re.sub(r'[（(](?:偷笑|轻笑|微笑|点头|抱抱|轻声|眨眼)[）)]','',text)
    text=re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]','',text)
    text=re.sub(r'[*_#]','',text).strip()
    text=re.sub(r'\s+',' ',text)
    return text if 2<=len(text)<=200 and re.search(r'[\w\u4e00-\u9fff]',text) else None


def deliver(bridge,store,message,contact,*,demo=False):
    """Send once; only synthesis failure before recording may fall back to text."""
    current=store.contact(contact['id'])
    voice_id=current.get('voice_preset','windows-huihui')
    voice_model=current.get('voice_model','speech-2.8-turbo')
    minimax=voice_id.startswith('minimax:')
    if minimax:
        from .minimax_voice import MiniMaxVoice
        voice_revision=MiniMaxVoice(store).config()['revision']
    use_voice=(message.get('origin')=='ai' and message['kind'] in ('text','voice')
               and current.get('reply_format')=='voice')
    if use_voice and message.get('ai_run_id'):
        from .ai_store import AIStore
        # Shared task results keep the same text/image presentation for all
        # recipients, rather than performing per-contact voice generation.
        if AIStore(store).run(message['ai_run_id'])['state'].get('agent_schedule'):use_voice=False
    text=speech_text(message['text']) if use_voice else None
    fallback='内容较长或包含链接、列表，保留完整文字' if use_voice and not text else ''
    def ensure_current():
        if not store.ai_send_valid(message):raise ReplySuperseded('收到新消息或会话设置变化，旧回复已取消')
        if text and store.contact(contact['id']).get('reply_format')!='voice':
            raise ReplySuperseded('已切换为文字回复，未发送旧语音')
        if text and store.contact(contact['id']).get('voice_preset','windows-huihui')!=voice_id:
            raise ReplySuperseded('音色已变化，未发送旧音色语音')
        if text and minimax and (store.contact(contact['id']).get('voice_model','speech-2.8-turbo')!=voice_model
                or MiniMaxVoice(store).config()['revision']!=voice_revision):
            raise ReplySuperseded('语音模型或 Key 已变化，未发送旧语音')
    def mark(kind):
        with store.connect() as db:
            db.execute('UPDATE messages SET kind=? WHERE id=?',(kind,message['id']))
            store.event(db,'message.updated',contact['id'],message_id=message['id'])
    def trace(kind,detail):
        from .ai_store import AIStore
        if message.get('ai_run_id'):AIStore(store).trace(message['ai_run_id'],kind,detail)
    ensure_current()
    if text:
        if demo:
            mark('voice')
            receipt=bridge.send_voice(contact,text)
            return {'source_key':receipt['key'],'delivery':'voice','simulated':True}
        from .wechat_voice import load_pcm
        from .voice_synthesis import synthesize
        directory=store.path.parent/'voice-tmp';directory.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='reply-',dir=directory) as folder:
            path=Path(folder)/'reply.wav'
            try:
                selected_voice=synthesize(text,path,voice_id,store=store,model=voice_model) if minimax else synthesize(text,path,voice_id)
                _,seconds=load_pcm(path)
            except Exception as exc:
                # No UI recording was started. Do not silently lose the answer.
                fallback='语音合成不可用或超过 50 秒，本次改发完整文字'
                from .minimax_voice import MiniMaxError
                if isinstance(exc,MiniMaxError):fallback=str(exc)+'；本次改发完整文字'
                text=None
            else:
                ensure_current();mark('voice')
                trace('原生语音发送',{'characters':len(text),'duration':round(seconds,2),'voice':selected_voice['voice'],'voice_name':selected_voice['name'],'voice_preset':voice_id,'model':selected_voice.get('model','')})
                receipt=bridge.send_voice(contact,path,before_send=ensure_current)
                trace('原生语音已核对',{'source_key':receipt['key'],'bubble':receipt['text']})
                return {'source_key':receipt['key'],'delivery':'voice'}
    ensure_current();mark('text')
    if fallback:trace('本次使用文字回复',{'reason':fallback})
    return {'source_key':bridge.send(contact,message['text']),'delivery':'text','voice_fallback':fallback}
