import json
from pathlib import Path

import pytest

from wechat_panel.ai import AIService
from wechat_panel.companion import companion_config
from wechat_panel.conversation_context import HANDOFF_CHARS, HANDOFF_MESSAGES
from wechat_panel.prompt_context import mood_choices
from wechat_panel.store import Store, now


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'panel.sqlite3');calls=[]
    def model(provider,messages,tools):
        calls.append(messages)
        if provider.get('_structured'):
            return {'role':'assistant','content':json.dumps({'mood':'平静','risk':'none','summary':'','facts':[]})},{}
        return {'role':'assistant','content':'本角色的回复'},{}
    ai=AIService(store,model_call=model)
    p=ai.data.save('providers',None,{'name':'mock','base_url':'http://localhost:9999','model':'mock'})
    agents=[ai.data.save('agents',None,{'name':n,'provider_id':p['id'],
        'prompt':'你是'+n+'，按自己的风格聊天。当前时间：{{current_time}}',
        'companion':{'memory':{'enabled':True},'safety':{'require_adult':False}}}) for n in ['甲','乙']]
    contacts=[store.add_contact({'lookup':n,'nickname':n,'wechat_id':n,'account_id':'self'}) for n in ['客户一','客户二']]
    for c in contacts:store.update_contact(c['id'],ai_agent_id=agents[0]['id'],listening=True,mode='ai_assist')
    ai.data.settings({'enabled':True})
    yield store,ai,agents,contacts,calls
    ai.stop()


def message(store,cid,text,direction='incoming',status='observed',origin='manual',run_id=None):
    with store.connect() as db:
        cur=db.execute('INSERT INTO messages(contact_id,direction,text,status,created_at,origin,ai_run_id) VALUES(?,?,?,?,?,?,?)',
                       (cid,direction,text,status,now(),origin,run_id))
        return cur.lastrowid


def run(ai,agent,contact,text='聊聊今天'):
    mid=message(ai.store,contact['id'],text)
    r=ai.create_run(agent['id'],text,contact['id'],mid)
    ai.execute(r['id']);result=ai.data.run(r['id'])
    assert result['status']=='draft',result['error']
    return result


def test_full_prompt_ignores_all_legacy_persona_and_expression_fields(env):
    store,ai,agents,contacts,calls=env;a=agents[0]
    config={k:v for k,v in a.items() if k not in ('id','key_configured')}
    config['style_id']='retired-style'
    config['companion']['persona'].update(enabled=True,name='OLD_NAME_MARKER',personality='OLD_PERSONALITY_MARKER',
        speaking_style='OLD_SPEAKING_MARKER',boundaries='OLD_BOUNDARY_MARKER',natural_rhythm=True,emoji_probability=0)
    ai.data.save('agents',a['id'],config)
    result=run(ai,a,contacts[0]);actual=calls[-1][0]['content']
    assert '你是甲' in actual and result['state']['prompt_variables']['current_time'] in actual
    assert not any(x in actual for x in ['OLD_', '本轮表达节奏','这轮不用 emoji','可以自然用一个 emoji'])
    assert 'reply_expression' not in result['state']
    assert 'user_profile' in actual


def test_switch_caps_old_dialogue_and_does_not_present_old_agent_as_assistant(env):
    store,ai,(a,b),(c,_),_=env
    for i in range(12):message(store,c['id'],f'旧记录{i}：'+'金融报告'*200,'outgoing' if i%2 else 'incoming')
    store.update_contact(c['id'],ai_agent_id=b['id'])
    result=run(ai,b,c,'你好，小乙');ctx=result['state']['context']
    assert len(ctx)==2 and all(m['role']=='user' for m in ctx)
    handoff=json.loads(ctx[0]['content'].split('\n',1)[1])
    assert len(handoff)==HANDOFF_MESSAGES
    assert all(len(m['内容'])<=HANDOFF_CHARS+10 for m in handoff)
    assert '旧记录0：' not in ctx[0]['content'] and '旧记录11：' in ctx[0]['content']
    assert '不要模仿' in ctx[0]['content'] and '长期记忆' in ctx[0]['content']
    assert ctx[-1]['content']=='你好，小乙'
    for i in range(4):message(store,c['id'],f'乙自己的对话{i}','outgoing' if i%2 else 'incoming')
    later=run(ai,b,c)['state']['context']
    assert not any('旧记录' in m['content'] for m in later)
    assert any(m['role']=='assistant' and m['content']=='乙自己的对话3' for m in later)


def test_memory_and_profiles_are_isolated_by_agent_and_contact_and_return_on_switch(env):
    store,ai,(a,b),(x,y),_=env;memory=ai.companion.memory
    for agent,contact,value in [(a,x,'甲一的猫'),(b,x,'乙一的猫'),(a,y,'甲二的猫'),(b,y,'乙二的猫')]:
        scope=memory.scope(agent['id'],contact['id']);p=memory.profile(scope,agent['id'],contact['id'])
        memory.set_profile(scope,{**p['data'],'display_name':value})
        memory.save_memory(scope,{'key':'宠物','content':value})
    for agent,contact,expected in [(b,x,'乙一的猫'),(a,y,'甲二的猫'),(a,x,'甲一的猫'),(b,y,'乙二的猫')]:
        store.update_contact(contact['id'],ai_agent_id=agent['id'])
        result=run(ai,agent,contact,'还记得我的宠物吗？');ctx=result['state']['dynamic_context']
        assert [m['content'] for m in ctx['memories']]==[expected]
        assert ctx['user_profile']['display_name']==expected
    assert memory.scope(a['id'],x['id'])!=memory.scope(a['id'],session_id=x['id'])
    # Reopening the runtime cannot merge namespaces or lose the binding boundary.
    restarted=AIService(store,model_call=ai.model_call)
    try:
        assert len(restarted.companion.memory.list_memories(memory.scope(a['id'],x['id'])))==1
        assert store.contact(x['id'])['ai_context_start_message']>0
    finally:restarted.stop()


def test_binding_boundary_changes_only_when_agent_changes_and_persists(env):
    store,ai,(a,b),(x,_),_=env
    first=message(store,x['id'],'切换前')
    store.update_contact(x['id'],ai_agent_id=b['id'])
    assert store.contact(x['id'])['ai_context_start_message']==first
    message(store,x['id'],'切换后')
    store.update_contact(x['id'],listening=False)
    store.update_contact(x['id'],listening=True,ai_agent_id=b['id'])
    assert Store(store.path).contact(x['id'])['ai_context_start_message']==first


def test_forget_does_not_reintroduce_handoff_or_old_agent_context(env):
    store,ai,(a,b),(x,_),_=env
    message(store,x['id'],'应该忘记的秘密')
    store.update_contact(x['id'],ai_agent_id=b['id'])
    memory=ai.companion.memory;scope=memory.scope(b['id'],x['id']);memory.profile(scope,b['id'],x['id'])
    memory.forget(scope)
    result=run(ai,b,x,'新的话题')
    assert not any('秘密' in m['content'] for m in result['state']['context'])


def test_failed_progress_and_late_other_agent_reply_are_excluded(env):
    store,ai,(a,b),(x,_),_=env
    old=run(ai,a,x)
    store.update_contact(x['id'],ai_agent_id=b['id'])
    message(store,x['id'],'LATE_OTHER_AGENT','outgoing','confirmed','ai',old['id'])
    message(store,x['id'],'FAILED','outgoing','failed')
    message(store,x['id'],'PROGRESS','outgoing','confirmed','ai_progress')
    result=run(ai,b,x)
    text=json.dumps(result['state']['context'],ensure_ascii=False)
    assert all(marker not in text for marker in ['LATE_OTHER_AGENT','FAILED','PROGRESS'])


def test_legacy_database_gets_a_safe_binding_boundary(env):
    store,ai,_,(x,_),_=env
    mid=message(store,x['id'],'旧版本历史')
    with store.connect() as db:db.execute('ALTER TABLE contacts DROP COLUMN ai_context_start_message')
    migrated=Store(store.path)
    assert migrated.contact(x['id'])['ai_context_start_message']==mid


def test_daily_mood_comes_only_from_the_current_prompt():
    assert mood_choices({'prompt':'你是严谨的财务助手。'})==()
    a=Path('docs/prompts/ahui-system.md').read_text('utf-8')
    b=Path('docs/prompts/sweet-maid-system.md').read_text('utf-8')
    assert '元气满满' in mood_choices({'prompt':a})
    assert '烘焙魂觉醒' in mood_choices({'prompt':b})
    assert '元气满满' not in mood_choices({'prompt':b})
