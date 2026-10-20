"""Group turns must retain same-member pictures without broadening @ triggers."""
import base64
from io import BytesIO
import json

from PIL import Image
import pytest

from test_group_chats import groups, incoming, group_runs


def photo(color='red'):
    buffer = BytesIO()
    Image.new('RGB', (80, 60), color).save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode()


def enable_vision(env):
    _, ai, agent, _, calls = env
    body = {k: v for k, v in agent.items() if k not in ('id', 'key_configured')}
    body['companion']['media'].update(understand_images=True, vision_provider_id=agent['provider_id'])
    ai.data.save('agents', agent['id'], body)

    def model(provider, messages, tools):
        calls.append(messages)
        return {'content': '图片背景是红色。' if isinstance(messages[-1]['content'], list) else '看到了，主要是红色。'}, {}

    ai.model_call = model


def run_latest(env):
    _, ai, _, contacts, _ = env
    ai._collect()
    run = group_runs(env, contacts[0])[0]
    ai.execute(run['id'])
    return ai.data.run(run['id'])


def test_picture_without_at_is_available_to_same_members_later_at_question(groups):
    store, ai, _, contacts, calls = groups
    enable_vision(groups)
    image = incoming(groups, contacts[0], '图片', kind='image', mention_me=False, image_data=photo())
    ai._collect()
    assert not group_runs(groups, contacts[0])
    incoming(groups, contacts[0], '@机器人 这张图里有什么？')
    run = run_latest(groups)
    assert run['status'] == 'queued', run['error']
    assert [o['message_id'] for o in run['state']['dynamic_context']['images']] == [image['id']]
    assert '图片背景是红色' in calls[-1][0]['content']
    assert len(store.pending()) == 1


def test_picture_posted_just_after_at_is_included_before_model_starts(groups):
    _, ai, _, contacts, _ = groups
    enable_vision(groups)
    incoming(groups, contacts[0], '@机器人 看看这张图')
    ai._collect()
    image = incoming(groups, contacts[0], '图片', kind='image', mention_me=False, image_data=photo())
    run = run_latest(groups)
    assert run['status'] == 'queued', run['error']
    assert image['id'] in [r['message_id'] for r in run['state']['input_images']]


def test_same_member_unanswered_at_messages_are_combined_without_other_members(groups):
    _, _, _, contacts, calls = groups
    first = incoming(groups, contacts[0], '@机器人 帮我安排明天的两件事')
    incoming(groups, contacts[0], 'OTHER_MEMBER_PRIVATE', sender='李四')
    second = incoming(groups, contacts[0], '@机器人 上午读书，下午散步')
    run = run_latest(groups)
    assert run['state']['turn_message_ids'] == [first['id'], second['id']]
    assert all(word in calls[-1][-1]['content'] for word in ('安排明天', '上午读书'))
    assert 'OTHER_MEMBER_PRIVATE' not in json.dumps(calls, ensure_ascii=False)


def test_recent_image_search_filters_before_taking_limit_and_rejects_other_members(groups):
    _, _, _, contacts, _ = groups
    enable_vision(groups)
    own = incoming(groups, contacts[0], '图片', kind='image', mention_me=False, image_data=photo())
    for _ in range(5):
        incoming(groups, contacts[0], '图片', sender='李四', kind='image', mention_me=False, image_data=photo('blue'))
    incoming(groups, contacts[0], '@机器人 我刚发的图是什么颜色？')
    run = run_latest(groups)
    assert [r['message_id'] for r in run['state']['input_images']] == [own['id']]


def test_explicit_image_reference_cannot_borrow_another_group_members_photo(groups):
    from wechat_panel.image_generation import reference_for
    _, ai, _, contacts, _ = groups
    enable_vision(groups)
    other = incoming(groups, contacts[0], '图片', kind='image', sender='李四', mention_me=False, image_data=photo('blue'))
    incoming(groups, contacts[0], '@机器人 画一张图片')
    ai._collect()
    run = ai.data.run(group_runs(groups, contacts[0])[0]['id'])
    with pytest.raises(ValueError, match='成员'):
        reference_for(ai.companion, run, run['state'], {}, {'mode': 'edit', 'reference_media_id': other['media_id']})


def test_pdf_followup_cannot_reuse_another_group_members_answer(groups, monkeypatch):
    store, ai, _, contacts, _ = groups
    store.update_contact(contacts[0]['id'], group_members=[])
    incoming(groups, contacts[0], '@机器人 请回答', sender='李四')
    other = run_latest(groups)
    ai.data.update_run(other['id'], status='sent', reply='OTHER_MEMBER_PDF_SECRET')
    incoming(groups, contacts[0], '@机器人 把刚才的内容转成PDF')
    captured = []
    monkeypatch.setattr(ai, 'finish', lambda rid, reply: captured.append(reply))
    run_latest(groups)
    assert 'OTHER_MEMBER_PDF_SECRET' not in captured


def test_group_voice_switch_with_at_acknowledges_without_unnecessary_model_call(groups):
    store, _, _, contacts, calls = groups
    incoming(groups, contacts[0], '@机器人 用语音回复我吧')
    run = run_latest(groups)
    assert store.contact(contacts[0]['id'])['reply_format'] == 'voice'
    assert run['reply'] == '好呀，之后用语音回复你。'
    assert not calls and store.pending()


def test_group_turn_waits_for_pending_image_from_same_member_but_not_other_members(groups):
    from wechat_panel.conversation_turn import pending_images
    store, ai, _, contacts, _ = groups
    enable_vision(groups)
    incoming(groups, contacts[0], '图片', kind='image', sender='李四', mention_me=False)
    trigger = incoming(groups, contacts[0], '@机器人 请看看图片')
    contact = store.contact(contacts[0]['id'])
    assert not pending_images(ai, contact, trigger)
    image = incoming(groups, contacts[0], '图片', kind='image', mention_me=False)
    assert pending_images(ai, contact, trigger)
    from wechat_panel.companion_store import CompanionStore
    media = CompanionStore(store).media('wechat:' + contact['id'], base64.b64decode(photo()))
    with store.connect() as db:db.execute('UPDATE messages SET media_id=? WHERE id=?', (media['id'], image['id']))
    assert not pending_images(ai, contact, trigger)


def test_unknown_member_picture_is_never_attached_to_an_at_question(groups):
    _, _, _, contacts, _ = groups
    enable_vision(groups)
    incoming(groups, contacts[0], '图片', kind='image', sender='张三', sender_verified=False, mention_me=False, image_data=photo())
    incoming(groups, contacts[0], '@机器人 这张图片有什么？')
    run = run_latest(groups)
    assert not run['state']['input_images']
