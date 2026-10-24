"""Forgetting requires actual scoped deletions before a demo confirmation send."""
import json

import pytest

from test_aria_conversation_tools import conversations,chat,incoming,no_duplicate
from test_simulated_delivery import post,wait_for
from wechat_panel.companion import forget_request
from wechat_panel.failure_replies import REPLY
from wechat_panel.group_chats import member_scope


CONFIRMED='好，相关记忆已经删掉了。'


def memories(env,target,sender='张三'):
    memory=env.ai.companion.memory
    scope=memory.scope(env.agent['id'],target['id'])
    if target['chat_type']=='group':scope=member_scope(scope,sender)
    memory.profile(scope,env.agent['id'],target['id'])
    values=[dict(key='喜欢的茶',content='喜欢茉莉花茶',category='preference'),
            dict(key='喝茶偏好',content='喝茶不要加糖',category='preference'),
            dict(key='生日',content='生日是11月12日',category='fact')]
    ids=[memory.save_memory(scope,value) for value in values]
    return scope,ids


def forget_text(kind):
    return '请忘掉我喝茶的偏好'+('@机器人\u2005' if kind=='group' else '')


def delete_calls(env,ids):
    def request(messages,specs):
        assert '尚未执行完成' in messages[-1]['content']
        name=env.model.names['forget']
        assert name in {spec['function']['name'] for spec in specs}
        return {'role':'assistant','content':'','tool_calls':[
            {'id':'delete-'+str(index),'type':'function',
             'function':{'name':name,'arguments':json.dumps({'memory_id':mid})}}
            for index,mid in enumerate(ids)]}
    env.model.steps.append(request)


@pytest.mark.parametrize('kind',['direct','group'])
def test_oral_forget_claim_is_repaired_into_actual_scoped_deletions_and_one_send(conversations,monkeypatch,kind):
    env=conversations
    target=chat(env,kind)
    decoy=chat(env,label='禁止接收者')
    scope,ids=memories(env,target)
    foreign_scope,foreign_ids=memories(env,target,'李四') if kind=='group' else memories(env,decoy)
    calls=[]
    original=env.ai.companion.forget
    def recorded(*args,**kwargs):
        calls.append((args[0],args[2]))
        return original(*args,**kwargs)
    monkeypatch.setattr(env.ai.companion,'forget',recorded)
    env.model.answer('好，我已经忘掉你喝茶的偏好了。')
    delete_calls(env,ids[:2])
    # The final model turn deliberately repeats deleted facts. The actual send must not.
    env.model.answer('已经忘记你喜欢茉莉花茶和不加糖的偏好了。')
    run=incoming(env,target,forget_text(kind))
    assert env.bridge.sent==[(target['id'],CONFIRMED)]
    assert calls==[(scope,mid) for mid in ids[:2]]
    assert [item['id'] for item in env.ai.companion.memory.list_memories(scope)]==[ids[2]]
    assert {item['id'] for item in env.ai.companion.memory.list_memories(foreign_scope)}==set(foreign_ids)
    receipts=run['state']['memory_forget_receipts']
    assert {receipt['memory_id'] for receipt in receipts}==set(ids[:2])
    assert all(receipt['scope']==scope and receipt['run_id']==run['id'] and receipt['deleted']==1 for receipt in receipts)
    assert not env.store.messages(decoy['id'])
    assert '茉莉' not in env.bridge.sent[-1][1] and '加糖' not in env.bridge.sent[-1][1]
    def not_reintroduced(messages):
        text=json.dumps(messages,ensure_ascii=False)
        assert '茉莉花茶' not in text and '不要加糖' not in text
    def no_tea_result(text):
        assert '茉莉花茶' not in text and '不要加糖' not in text
    env.model.tool('memory_search',{'query':'喝茶偏好'},'没有保存对应的喝茶偏好记录。',
                   no_tea_result,not_reintroduced)
    incoming(env,target,'还记得我喝茶的偏好吗'+('@机器人\u2005' if kind=='group' else ''))
    no_duplicate(env,target)
    assert len(calls)==2 and not env.model.steps


@pytest.mark.parametrize('kind',['direct','group'])
def test_model_that_only_claims_forgetting_gets_one_repair_then_fixed_failure(conversations,kind):
    env=conversations
    target=chat(env,kind)
    scope,ids=memories(env,target)
    env.model.answer('已经忘记了。')
    env.model.answer('放心，都忘记了。')
    run=incoming(env,target,forget_text(kind))
    assert env.bridge.sent==[(target['id'],REPLY)]
    assert {item['id'] for item in env.ai.companion.memory.list_memories(scope)}==set(ids)
    assert run['state']['memory_forget_repair'] and run['state']['task_failure']
    assert not run['state'].get('memory_forget_receipts')
    assert len(env.model.calls)==2 and not env.model.steps


def test_memory_search_is_not_a_deletion_receipt(conversations):
    env=conversations
    target=chat(env)
    scope,ids=memories(env,target)
    env.model.tool('memory_search',{'query':'喝茶偏好'},'已经把你的喝茶偏好忘掉了。')
    env.model.answer('已经忘了。')
    run=incoming(env,target,forget_text('direct'))
    assert run['reply']==REPLY and env.bridge.sent==[(target['id'],REPLY)]
    assert {item['id'] for item in env.ai.companion.memory.list_memories(scope)}==set(ids)
    assert not run['state'].get('memory_forget_receipts')


def test_partial_deletion_cannot_be_confirmed_as_a_complete_forget(conversations):
    env=conversations
    target=chat(env)
    scope,ids=memories(env,target)
    env.model.tool('forget',{'memory_id':ids[0]},'你的喝茶偏好都忘记了。')
    env.model.answer('已经全部忘记了。')
    run=incoming(env,target,forget_text('direct'))
    assert run['reply']==REPLY and env.bridge.sent==[(target['id'],REPLY)]
    assert {item['id'] for item in env.ai.companion.memory.list_memories(scope)}==set(ids[1:])
    assert len(run['state']['memory_forget_receipts'])==1


def test_all_memories_about_one_topic_still_preserves_other_topics(conversations):
    env=conversations
    target=chat(env)
    scope,ids=memories(env,target)
    env.model.answer('已经忘了。')
    delete_calls(env,ids[:2])
    env.model.answer('已经删除了全部喝茶记忆。')
    run=incoming(env,target,'请忘掉我所有喝茶的记忆')
    assert run['reply']==CONFIRMED
    assert [item['id'] for item in env.ai.companion.memory.list_memories(scope)]==[ids[2]]


def test_ambiguous_previous_preference_requires_clarification_not_all_preferences(conversations):
    env=conversations
    target=chat(env)
    scope,ids=memories(env,target)
    env.model.answer('已经忘了。')
    run=incoming(env,target,'请忘记刚才这条偏好')
    assert run['reply']=='你想删除哪条记忆？请告诉我具体内容。'
    assert {item['id'] for item in env.ai.companion.memory.list_memories(scope)}==set(ids)
    assert not run['state'].get('memory_forget_receipts')


@pytest.mark.parametrize('kind',['direct','group'])
def test_no_match_does_not_delete_an_unrelated_memory_or_claim_success(conversations,kind):
    env=conversations
    target=chat(env,kind)
    scope,ids=memories(env,target)
    # Remove tea preferences as fixture setup; only an unrelated birthday remains.
    for mid in ids[:2]:env.ai.companion.memory.forget(scope,mid)
    env.model.answer('好，已经忘记你的喝茶偏好了。')
    run=incoming(env,target,forget_text(kind))
    assert env.bridge.sent==[(target['id'],'没有保存与这项要求匹配的记忆。')]
    assert [item['id'] for item in env.ai.companion.memory.list_memories(scope)]==[ids[2]]
    assert not run['state'].get('memory_forget_receipts')
    assert not env.model.steps


def test_forget_tool_cannot_delete_unrelated_record_to_satisfy_a_specific_request(conversations):
    env=conversations
    target=chat(env)
    scope,ids=memories(env,target)
    env.model.tool('forget',{'memory_id':ids[2]},None)
    run=incoming(env,target,forget_text('direct'))
    assert run['reply']==REPLY and env.bridge.sent==[(target['id'],REPLY)]
    assert {item['id'] for item in env.ai.companion.memory.list_memories(scope)}==set(ids)
    assert not run['state'].get('memory_forget_receipts')


@pytest.mark.parametrize('kind',['direct','group'])
def test_quote_is_data_and_does_not_request_deletion(conversations,kind):
    env=conversations
    target=chat(env,kind)
    scope,ids=memories(env,target)
    env.model.answer('这句话是在请求删除记忆。')
    run=incoming(env,target,'解释一下这句话'+('@机器人\u2005' if kind=='group' else ''),extra={
        'quote':{'kind':'text','sender':'他人','text':'请忘掉我喝茶的偏好','status':'ready'}})
    assert not run['state'].get('memory_forget_repair')
    assert not run['state'].get('memory_forget_receipts')
    assert {item['id'] for item in env.ai.companion.memory.list_memories(scope)}==set(ids)
    assert env.bridge.sent==[(target['id'],'这句话是在请求删除记忆。')]


@pytest.mark.parametrize('kind',['direct','group'])
def test_manual_takeover_before_repair_prevents_deletion_and_send(conversations,kind):
    env=conversations
    target=chat(env,kind)
    scope,ids=memories(env,target)
    def cancel(messages,specs):
        env.store.update_contact(target['id'],mode='manual')
        return {'content':'已经忘记了。'}
    env.model.steps.append(cancel)
    body={'text':forget_text(kind)}
    if kind=='group':body.update(sender='张三',mention_me=True)
    post(env.client,'/api/demo/'+target['id']+'/incoming',body)
    run=wait_for(lambda: next((row for row in env.ai.data.runs(target['id']) if row['status']=='cancelled'),None))
    assert run['status']=='cancelled' and not env.bridge.sent and not env.store.pending()
    assert {item['id'] for item in env.ai.companion.memory.list_memories(scope)}==set(ids)


@pytest.mark.parametrize('text',[
    '不要忘记我的生日','请不要删除我的偏好','先别忘掉我喝茶的偏好',
    '忘记是什么意思？','如何忘记一个人？','我忘记了喝茶的偏好',
    '他让我忘掉喝茶的偏好','请解释“忘掉我的偏好”这句话',
    '请删除我的PDF文件','请忘掉其他人的喝茶偏好',
])
def test_noncommands_and_third_party_instructions_do_not_trigger_forget_guard(text):
    assert forget_request(text) is None


def test_same_forget_tool_id_is_not_deleted_twice_after_a_success_receipt(conversations,monkeypatch):
    env=conversations
    target=chat(env)
    scope,ids=memories(env,target)
    run=env.ai.create_run(env.agent['id'],'请忘掉我喝茶的偏好',dry_run=True)
    saved=env.ai.data.run(run['id'])
    saved['state']['scope']=scope
    calls=[]
    original=env.ai.companion.forget
    def recorded(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(env.ai.companion,'forget',recorded)
    for _ in range(2):
        value=json.loads(env.ai.companion.tool({'builtin':'forget'},{'memory_id':ids[0]},saved,saved['state']))
        assert value['deleted']==1 and value['memory_id']==ids[0]
    assert len(calls)==1 and len(saved['state']['memory_forget_receipts'])==1
