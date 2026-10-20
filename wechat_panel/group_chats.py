"""Shared eligibility and per-member context boundaries for group conversations."""
import hashlib
import json
import re


RULE_FIELDS = {'group_members', 'group_mention_only', 'group_self_name'}
VERIFICATION_FIELDS = ('group_identity_verified', 'group_members_verified', 'group_self_name_verified')


def is_group(contact):
    return contact.get('chat_type', 'direct') == 'group'


def members(value):
    if isinstance(value, str):value = json.loads(value)
    if not isinstance(value, list) or len(value) > 50:raise ValueError('群成员最多指定50位')
    clean = []
    for name in value:
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(ord(c) < 32 for c in name):
            raise ValueError('请填写完整的群成员显示名，每位成员单独一项')
        name = name.strip()
        if name not in clean:clean.append(name)
    return clean


def qualifies(contact, message):
    if not is_group(contact):return True
    if (message.get('direction') != 'incoming' or not message.get('sender_verified')
            or not message.get('sender') or message.get('kind', 'text') not in ('text', 'image')):
        return False
    chosen = members(contact.get('group_members', []))
    if chosen and message['sender'] not in chosen:return False
    return (not contact.get('group_mention_only', True)
            or bool(message.get('mention_me') and message.get('mention_verified')))


def eligible_sql(contact, sender=None):
    """SQL predicate equivalent to qualifies, for latest-trigger/send guards."""
    if not is_group(contact):return '', []
    clauses = ["sender_verified=1", "sender!=''"]
    args = []
    chosen = members(contact.get('group_members', []))
    if chosen:
        clauses.append('sender IN (' + ','.join('?' for _ in chosen) + ')');args.extend(chosen)
    if sender is not None:clauses.append('sender=?');args.append(sender)
    if contact.get('group_mention_only', True):clauses.extend(['mention_me=1', 'mention_verified=1'])
    return ' AND ' + ' AND '.join(clauses), args


def member_scope(base, sender):
    return base + ':group-member:' + hashlib.sha256(sender.encode('utf-8')).hexdigest()[:24]


def request_text(contact, text):
    """Remove only the verified own mention when interpreting a chat command."""
    own=contact.get('group_self_name','')
    if not own or not contact.get('group_self_name_verified'):return text
    return re.sub(r'(?<![\w@])@'+re.escape(own)+r'(?=$|[\s\u2005\u2009\u200b，。！？、,:;!?：；])','',text).strip()


def context(ai, contact, agent, trigger_id, sender, forget_before=0, exclude_ids=()):
    """Only this member's eligible turns and replies actually sent for them."""
    boundary = max(contact.get('ai_context_start_message', 0), forget_before)
    rows = ai.store.messages(contact['id'], before=trigger_id + 1 if trigger_id else None, limit=200)
    result = []
    for message in rows:
        if (message['id'] <= boundary or message['id'] in exclude_ids or message.get('text_pending')
                or message['status'] not in ('observed', 'confirmed')
                or message['kind'] not in ('text', 'image', 'voice') or message.get('historical')):continue
        if message['direction'] == 'incoming':
            if not qualifies(contact, message) or message['sender'] != sender:continue
            role = 'user'
        elif message['direction'] == 'outgoing' and message.get('origin') == 'ai' and message.get('ai_run_id'):
            try:run = ai.data.run(message['ai_run_id'])
            except KeyError:continue
            if run['agent_id'] != agent['id'] or run['state'].get('group_sender') != sender:continue
            role = 'assistant'
        else:continue
        text = ('[已通过微信原生语音发送] ' + message['text'] if message['kind'] == 'voice'
                else message['text'] if message['kind'] == 'text' else '[图片，消息编号 ' + str(message['id']) + ']')
        if message['kind']=='text':
            from .quoted_context import history_text
            text=history_text(message)
        result.append({'role':role, 'content':text})
    return result[-ai.data.settings()['context_messages']:]
