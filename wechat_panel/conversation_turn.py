"""Build one unanswered conversational turn, including its image attachments."""
from datetime import datetime, timedelta
import json

from .web_policy import ONLINE

MERGE_SECONDS = 600
IMAGE_WAIT_SECONDS = 20
READ_ONLY = ONLINE | {'clock','calculator','knowledge_search','memory_search','use_skill','read_skill_file'}


def latest_id(db,cid,agent,ready_only=False,group_sender=None):
    media=agent.get('companion',{}).get('media',{})
    pending=bool((media.get('understand_images') and media.get('vision_provider_id')) or media.get('image_service_id'))
    from .group_chats import eligible_sql
    contact=db.execute('SELECT * FROM contacts WHERE id=?',(cid,)).fetchone()
    clause,args=eligible_sql(dict(contact),group_sender) if contact else ('',[])
    return db.execute("SELECT MAX(id) FROM messages WHERE contact_id=? AND historical=0 AND direction='incoming' "
        +("AND text_pending=0 " if ready_only else '')+
        "AND (kind='text' OR (kind='image' AND (media_id!='' OR ?)))"+clause,(cid,pending,*args)).fetchone()[0]


def stamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def unanswered(ai,contact,trigger,forget_before=0):
    from .group_chats import is_group, qualifies
    group=is_group(contact)
    boundary=max(contact.get('ai_start_message',0),contact.get('ai_context_start_message',0),forget_before)
    with ai.store.connect() as db:
        # A manual answer or attempted AI delivery closes the preceding turn.
        answered=db.execute("SELECT COALESCE(MAX(id),0) FROM messages WHERE contact_id=? AND id<? "
            "AND direction='outgoing' AND historical=0 AND status IN ('observed','confirmed','sending','uncertain') "
            "AND origin NOT IN ('approval','ai_progress')",(contact['id'],trigger['id'])).fetchone()[0]
        runs=db.execute("SELECT state,trigger_id FROM ai_runs WHERE contact_id=? AND agent_id=? "
            "AND trigger_id>? AND trigger_id<? ORDER BY trigger_id DESC LIMIT 20",
            (contact['id'],contact['ai_agent_id'],boundary,trigger['id'])).fetchall()
    boundary=max(boundary,answered)
    if group:
        # Replies to another member do not close this member's pending turn.
        boundary=max(contact.get('ai_start_message',0),contact.get('ai_context_start_message',0),forget_before)
        for message in ai.store.messages(contact['id'],before=trigger['id'],limit=200):
            if message['direction']!='outgoing' or message['historical'] or message['status'] not in ('observed','confirmed','sending','uncertain'):continue
            if message.get('origin') in ('approval','ai_progress'):continue
            if message.get('origin')=='ai' and message.get('ai_run_id'):
                previous=ai.data.run(message['ai_run_id'])
                if previous['state'].get('group_sender')!=trigger['sender']:continue
            boundary=max(boundary,message['id'])
    # Never turn a follow-up into a replay of an external write already started.
    for row in runs:
        if row['trigger_id']<=boundary:continue
        old=ai.data.decode_state(row['state'])
        if group and old.get('group_sender')!=trigger['sender']:continue
        if old.get('effect_started'):boundary=max(boundary,row['trigger_id'])
    cutoff=stamp(trigger['created_at'])-timedelta(seconds=MERGE_SECONDS)
    return [m for m in ai.store.messages(contact['id'],before=None if group else trigger['id']+1,limit=200)
            if m['id']>boundary and not m['historical'] and m['direction']=='incoming'
            and m['kind'] in ('text','image') and not m.get('text_pending') and stamp(m['created_at'])>=cutoff
            and (not group or m.get('sender_verified') and m['sender']==trigger['sender']
                 and (m['id']<=trigger['id'] or m['kind']=='image' and stamp(m['created_at'])<=stamp(trigger['created_at'])+timedelta(seconds=IMAGE_WAIT_SECONDS))
                 and (m['kind']=='image' or qualifies(contact,m)))]


def prepare(ai,contact,trigger,state,forget_before=0):
    agent=state['snapshot']['agent']
    rows=unanswered(ai,contact,trigger,forget_before) if agent['kind']=='workflow' else [trigger]
    if not rows:rows=[trigger]
    state['turn_message_ids']=[m['id'] for m in rows]
    state['turn_messages']=[{'id':m['id'],'kind':m['kind'],'text':m['text']} for m in rows]
    from .quoted_context import prepare as prepare_quotes, image_ref
    quoted_images=prepare_quotes(contact,rows,state)
    media=agent.get('companion',{}).get('media',{})
    boundary=max(contact.get('ai_context_start_message',0),forget_before)
    current=[m for m in rows if m['kind']=='image']
    recent=[]
    if media.get('understand_images') or media.get('image_service_id'):
        from .group_chats import is_group
        recent=[m for m in ai.store.messages(contact['id'],before=trigger['id']+1,limit=200 if is_group(contact) else 40)
                if m['id']>boundary and m['kind']=='image' and m['direction']=='incoming'
                and m['id'] not in state['turn_message_ids']]
        if is_group(contact):recent=[m for m in recent if not m.get('historical') and m.get('sender_verified') and m['sender']==trigger['sender']]
        recent=recent[-4:]
    chosen=(current+recent)[:8]
    state['input_images']=[{'message_id':m['id'],'media_id':m.get('media_id',''),
                            'media_scope':'wechat:'+contact['id'],'current':m in current} for m in chosen]
    if state['quoted_messages']:
        state['input_images']=quoted_images
    elif media.get('understand_images') or media.get('image_service_id'):
        from .group_chats import is_group
        quoted_recent=[m for m in ai.store.messages(contact['id'],before=trigger['id'],limit=200)
            if m['id']>boundary and not m.get('historical') and m['direction']=='incoming'
            and m.get('quote',{}).get('kind')=='image'
            and (not is_group(contact) or m.get('sender_verified') and m['sender']==trigger['sender'])][-4:]
        state['input_images']+= [image_ref(contact,m,False) for m in quoted_recent]
    state['input_images']=state['input_images'][:8]
    state['omitted_images']=max(0,len(current)-len(chosen))
    if len(rows)==1:return trigger['text']
    parts=[{'顺序':i+1,'类型':m['kind'],'内容':m['text'] if m['kind']=='text' else '[图片]'} for i,m in enumerate(rows)]
    return '以下是用户在本轮连续发送、尚未得到回复的消息，请按顺序结合理解并统一回复；后面的补充或更正优先：\n'+json.dumps(parts,ensure_ascii=False)


def pending_images(ai,contact,trigger):
    agent=ai.data.get('agents',contact['ai_agent_id'])
    media=agent.get('companion',{}).get('media',{})
    if agent['kind']!='workflow' or not ((media.get('understand_images') and media.get('vision_provider_id')) or media.get('image_service_id')):return False
    now=datetime.now().astimezone()
    return any(m['kind']=='image' and not m.get('media_id')
               and (now-stamp(m['created_at'])).total_seconds()<IMAGE_WAIT_SECONDS
               for m in unanswered(ai,contact,trigger))
