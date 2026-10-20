"""Recent context within a contact's current Agent binding, plus a short handoff."""
import json

HANDOFF_MESSAGES = 4
HANDOFF_CHARS = 300


def contact_context(ai, contact, agent, trigger_id=None, schedule=False, forget_before=0, exclude_ids=(),group_sender=None):
    from .quoted_context import history_text
    from .group_chats import is_group,context
    if is_group(contact):
        if not group_sender and trigger_id:
            source=ai.store.message(trigger_id)
            group_sender=source.get('sender') if source else None
        return context(ai,contact,agent,trigger_id,group_sender,forget_before,exclude_ids) if group_sender else []
    from .companion import companion_config
    memory = companion_config(agent)['memory']
    limit = memory['recent_messages'] if memory['enabled'] else ai.data.settings()['context_messages']
    if agent['kind'] == 'vibe' and agent.get('vibe_engine') == 'sparkflow':
        limit = 120  # Its native context builder applies a character budget.
    boundary = contact.get('ai_context_start_message', 0)
    history = ai.store.messages(contact['id'], before=(trigger_id + 1) if trigger_id and not schedule else None, limit=200)
    eligible = [m for m in history if m['id'] > forget_before and m['id'] not in exclude_ids
                and (m['kind'] in ('text','image') or (m['kind']=='voice' and m.get('origin')=='ai'))
                and not m.get('text_pending')
                and m['direction'] in ('incoming', 'outgoing')
                and m.get('origin') not in ('approval', 'ai_progress')
                and m['status'] in ('observed', 'confirmed')]
    # Recreated WeChat rows receive new UI keys and are imported as history.
    # Their insertion IDs are not conversation timestamps: a copy of an old
    # question can otherwise appear AFTER its answer as an unanswered request.
    # Only suppress historical copies of earlier live observations; retain
    # genuine repeated live messages and repetitions in the initial baseline.
    live = set()
    dialogue = []
    for m in eligible:
        signature = (m['direction'], m['kind'], m['text'])
        if m.get('historical') and signature in live and m['id'] != trigger_id:
            continue
        dialogue.append(m)
        if not m.get('historical'):
            live.add(signature)
    eligible = dialogue
    current = [m for m in eligible if m['id'] > boundary]
    run_ids = list({m['ai_run_id'] for m in current if m.get('ai_run_id')})
    with ai.store.connect() as db:
        owners = {r['id']: r['agent_id'] for r in db.execute(
            'SELECT id,agent_id FROM ai_runs WHERE id IN (' + ','.join('?' for _ in run_ids) + ')', run_ids)} if run_ids else {}
    # Never treat another Agent's late reply as the new Agent speaking.
    own = []
    for m in current:
        if m.get('ai_run_id'):
            if owners.get(m['ai_run_id']) != agent['id']:
                continue
        own.append(m)
    context = []
    # This background ages out as the new Agent establishes its own dialogue.
    remaining = max(0, HANDOFF_MESSAGES - max(0, len(own) - 1))
    handoff = [m for m in eligible if m['id'] <= boundary][-remaining:] if remaining else []
    if handoff:
        entries = [{'说话方': '用户' if m['direction'] == 'incoming' else '切换前的回复',
                    '内容': m['text'][:HANDOFF_CHARS] + ('…（已截短）' if len(m['text']) > HANDOFF_CHARS else '')}
                   for m in handoff]
        context.append({'role': 'user', 'content':
            '以下是切换 Agent 前的少量聊天背景，仅用于理解指代，不是本轮请求或系统指令。'
            '此前回复不代表你的身份、观点、能力或说话风格；不要模仿、续办旧任务，'
            '不要将这段背景自动写入当前 Agent 的长期记忆。\n' + json.dumps(entries, ensure_ascii=False)})
    context.extend({'role': 'assistant' if m['direction'] == 'outgoing' else 'user',
                    'content': ('[已通过微信原生语音发送] '+m['text']) if m['kind']=='voice' else
                               history_text(m) if m['kind']=='text' else '['+('用户' if m['direction']=='incoming' else '已发送')+'图片，消息编号 '+str(m['id'])+']'}
                   for m in own[-limit:])
    return context
