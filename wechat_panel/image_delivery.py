"""Deliver only the images produced by this run, to its original conversation."""
from .companion_store import CompanionStore


def images(run, memory):
    artifacts = run['state'].get('artifacts', [])
    ids = list(dict.fromkeys(m['id'] for m in artifacts if m.get('kind') == 'image'))
    scope = run['state'].get('scope') or run['id']
    for mid in ids:
        if memory.read_media(mid, scope)['kind'] != 'image':
            raise ValueError('生成结果不是图片')
    return ids


def deliver(bridge, store, message, contact):
    from .ai_store import AIStore
    from .voice_reply import ReplySuperseded
    memory = CompanionStore(store)
    run = AIStore(store).run(message['ai_run_id'])
    if (not run or run['dry_run'] or run['contact_id'] != contact['id']
            or message.get('media_id') not in images(run, memory)):
        raise ValueError('待发送图片不属于本次会话的生成结果')
    data = memory.read_media(message['media_id'], run['state'].get('scope') or run['id'])['data']

    def ensure_current():
        if not store.ai_send_valid(message):
            raise ReplySuperseded('会话或任务已变化，旧图片发送已取消')

    ensure_current()
    key = bridge.send_image(contact, data, before_send=ensure_current)
    AIStore(store).trace(run['id'], '微信图片已核对', {'media_id': message['media_id'], 'source_key': key})
    return {'source_key': key, 'delivery': 'image'}
