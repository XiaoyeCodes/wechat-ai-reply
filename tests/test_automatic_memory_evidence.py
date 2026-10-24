"""Automatic memory needs personal evidence, not merely a task name substring."""
import json

import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store


def fact(content,evidence,key='个人事实',category='fact'):
    return dict(key=key,content=content,evidence=evidence,category=category)


@pytest.fixture
def memory_env(tmp_path):
    store=Store(tmp_path/'automatic-memory.sqlite3')
    ai=AIService(store,model_call=lambda *args:pytest.fail('This test may not call a model or network'))
    provider=ai.data.save('providers',None,dict(name='fixture',model='mock',base_url='http://localhost:1'))
    agent=ai.data.save('agents',None,dict(name='memory fixture',provider_id=provider['id'],web_policy='off',
        companion={'memory':{'enabled':True,'auto_extract':True},
            'safety':{'emotion_enabled':False,'crisis_detection':False}}))
    def save(text,items,chat_type=None,**state_extra):
        contact_id=trigger_id=None
        if chat_type=='group':
            contact=store.add_contact(dict(lookup='isolated group',nickname='isolated group',
                wechat_id='isolated-group',account_id='isolated-self',chat_type='group',
                group_members=['测试成员'],group_self_name='Aria',group_identity_verified=True,
                group_members_verified=True,group_self_name_verified=True,group_self_name_source='user_config'))
            store.update_contact(contact['id'],mode='ai_auto',listening=True,ai_agent_id=agent['id'])
            store.ingest(contact['id'],[])
            store.ingest(contact['id'],[dict(key='group-source',text=text,kind='text',direction='incoming',
                sender='测试成员',sender_verified=True,mention_me=True,mention_verified=True)])
            contact_id=contact['id'];trigger_id=store.messages(contact_id)[-1]['id']
        run=ai.create_run(agent['id'],text,contact_id=contact_id,trigger_id=trigger_id,dry_run=True)
        run=ai.data.run(run['id'])
        state=run['state']
        profile=ai.companion.memory.profile(state['scope'],agent['id'])
        state.update(companion_prepared=True,memory_revision=profile['revision'],
            analysis={'mood':'平静','summary':'','facts':items},**state_extra)
        ai.companion.remember_turn(run,state,'已处理本轮请求。')
        return ai.companion.memory.list_memories(state['scope'])
    yield ai,agent,save
    ai.stop()


@pytest.mark.parametrize('text,item',[
    ('帮我找一首周杰伦的晴天，给我可以打开的听歌链接。',
        fact('喜欢周杰伦','周杰伦','喜欢歌手','preference')),
    ('帮我找一首周杰伦的晴天，给我可以打开的听歌链接。',
        fact('喜欢歌曲晴天','晴天','喜欢歌曲','preference')),
    ('帮我生成今日财经日报PDF。',
        fact('偏好每天获取财经日报PDF','财经日报PDF','日报偏好','preference')),
    ('做一张AI热点海报，标题要清楚。',
        fact('用户计划制作AI热点海报','做一张AI热点海报','创作事件','event')),
    ('我想查一下杭州天气。',fact('用户住在杭州','杭州','城市')),
    ('我的这份报告请生成PDF。',fact('用户偏好PDF格式','PDF','输出偏好','preference')),
    ('请翻译：我喜欢咖啡。',fact('用户喜欢咖啡','我喜欢咖啡','饮品偏好','preference')),
    ('他说“我喜欢咖啡”，这句话是什么意思？',fact('用户喜欢咖啡','我喜欢咖啡','饮品偏好','preference')),
    ('假设我喜欢咖啡，应该怎样回答？',fact('用户喜欢咖啡','我喜欢咖啡','饮品偏好','preference')),
    ('> 我喜欢咖啡\n请解释这句话。',fact('用户喜欢咖啡','我喜欢咖啡','饮品偏好','preference')),
])
def test_one_off_tasks_and_discussed_statements_are_not_personal_memories(memory_env,text,item):
    _,_,save=memory_env
    assert save(text,[item])==[]


@pytest.mark.parametrize('text,item',[
    ('我喜欢无糖乌龙茶。',fact('用户喜欢无糖乌龙茶','无糖乌龙茶','饮品偏好','preference')),
    ('我不喜欢太甜的饮料。',fact('用户不喜欢太甜的饮料','不喜欢太甜的饮料','饮品偏好','preference')),
    ('我住在杭州。',fact('用户住在杭州','杭州','城市')),
    ('我的生日是11月12日。',fact('生日是11月12日','11月12日','生日')),
    ('我家猫叫芝麻。',fact('宠物猫叫芝麻','芝麻','宠物名')),
    ('猫改叫团子了。',fact('宠物猫现在叫团子','团子','宠物名')),
    ('请记住：我喜欢短一点的回复。',fact('用户喜欢简短回复','我喜欢短一点的回复','回复偏好','preference')),
    ('以后每次生成财经日报都用PDF。',fact('财经日报默认用PDF','以后每次生成财经日报都用PDF','输出偏好','preference')),
    ('我明天有一场面试。',fact('用户明天有面试','明天有一场面试','面试','event')),
    ('I like oolong tea.',fact('Likes oolong tea','oolong tea','drink','preference')),
])
def test_explicit_personal_facts_and_durable_preferences_remain_available(memory_env,text,item):
    _,_,save=memory_env
    rows=save(text,[item])
    assert len(rows)==1 and rows[0]['content']==item['content']


def test_mixed_turn_saves_only_the_personal_clause_not_the_adjacent_task(memory_env):
    _,_,save=memory_env
    rows=save('我喜欢乌龙茶，请查询杭州天气。',[
        fact('用户喜欢乌龙茶','我喜欢乌龙茶','饮品偏好','preference'),
        fact('用户住在杭州','杭州','城市'),
        fact('用户喜欢查天气','我喜欢乌龙茶，请查询杭州天气','查询偏好','preference')])
    assert [row['content'] for row in rows]==['用户喜欢乌龙茶']


def test_real_aria_remember_prefix_and_attached_tea_preference_are_both_supported(memory_env):
    _,_,save=memory_env
    rows=save('记住我喜欢茉莉花茶，而且喝茶不要加糖。',[
        fact('喜欢茉莉花茶','记住我喜欢茉莉花茶','喜欢的茶','preference'),
        fact('喝茶不要加糖','喝茶不要加糖','喝茶偏好','preference')])
    assert {row['content'] for row in rows}=={'喜欢茉莉花茶','喝茶不要加糖'}


def test_evidence_can_include_memory_request_prefix_and_pure_personal_clauses(memory_env):
    _,_,save=memory_env
    text='请记住：我喜欢茉莉花茶，而且喝茶不要加糖。'
    rows=save(text,[fact('喜欢茉莉花茶，喝茶不加糖',text,'饮品偏好','preference')])
    assert len(rows)==1 and rows[0]['content']=='喜欢茉莉花茶，喝茶不加糖'


def test_attached_task_does_not_inherit_permission_to_infer_preferences(memory_env):
    _,_,save=memory_env
    text='记住我喜欢茶，而且帮我找一首周杰伦的晴天。'
    rows=save(text,[fact('用户喜欢茶','记住我喜欢茶','饮品偏好','preference'),
        fact('用户喜欢周杰伦','周杰伦','歌手偏好','preference'),
        fact('用户喜欢找歌',text,'查询偏好','preference')])
    assert [row['content'] for row in rows]==['用户喜欢茶']


def test_personal_continuation_stops_after_an_intervening_task(memory_env):
    _,_,save=memory_env
    rows=save('我喜欢茶，请查询杭州天气，而且喝茶不要加糖。',[
        fact('用户喜欢茶','我喜欢茶','饮品偏好','preference'),
        fact('用户不加糖','喝茶不要加糖','糖分偏好','preference')])
    assert [row['content'] for row in rows]==['用户喜欢茶']


@pytest.mark.parametrize('text',[
    '@Aria\u2005记住我喜欢茉莉花茶',
    '记住我@Aria\u2005喜欢茉莉花茶',
    '记住我喜欢茉莉花茶@Aria\u2005',
])
def test_verified_group_self_mentions_do_not_hide_personal_memory_evidence(memory_env,text):
    _,_,save=memory_env
    rows=save(text,[fact('喜欢茉莉花茶',text,'饮品偏好','preference')],chat_type='group')
    assert len(rows)==1 and rows[0]['content']=='喜欢茉莉花茶'


def test_group_mention_normalization_preserves_english_word_boundaries(memory_env):
    _,_,save=memory_env
    text='@Aria\u2005I like oolong tea.'
    rows=save(text,[fact('Likes oolong tea',text,'drink','preference')],chat_type='group')
    assert len(rows)==1 and rows[0]['content']=='Likes oolong tea'


def test_self_mention_alone_is_not_evidence_for_a_personal_fact(memory_env):
    _,_,save=memory_env
    assert save('@Aria\u2005我喜欢茶',[fact('用户喜欢晴天','@Aria','歌曲偏好','preference')],chat_type='group')==[]


@pytest.mark.parametrize('text',[
    '@Aria\u2005帮我找周杰伦的晴天',
    '请@Aria\u2005帮我找周杰伦的晴天',
    '帮我找周杰伦的晴天@Aria\u2005',
])
def test_group_mentions_do_not_turn_music_tasks_into_personal_preferences(memory_env,text):
    _,_,save=memory_env
    assert save(text,[fact('用户喜欢周杰伦','周杰伦','歌手偏好','preference')],chat_type='group')==[]


def test_quote_content_is_not_the_current_users_memory(memory_env):
    _,_,save=memory_env
    assert save('解释一下被引用的这句话。',[fact('用户喜欢咖啡','我喜欢咖啡')],
        quoted_messages=[{'text':'我喜欢咖啡','sender':'其他用户','kind':'text'}])==[]


def test_analysis_prompt_and_storage_reject_task_preferences_without_extra_model_calls(memory_env):
    ai,agent,_=memory_env
    calls=[]
    def model(provider,messages,tools):
        calls.append(messages)
        if '只输出 JSON' in messages[0]['content']:
            # Deliberately ignore the prompt: the program must reject this inference too.
            return {'content':json.dumps({'mood':'平静','risk':'none','needs_web':False,'summary':'',
                'facts':[fact('用户喜欢周杰伦','周杰伦','喜欢歌手','preference')]},ensure_ascii=False)},{}
        return {'content':'已经处理你的找歌请求。'},{}
    ai.model_call=model
    run=ai.create_run(agent['id'],'帮我找一首周杰伦的晴天，给我可以打开的听歌链接。',dry_run=True)
    ai.execute(run['id'])
    saved=ai.data.run(run['id'])
    assert saved['status']=='completed',saved['error']
    assert ai.companion.memory.list_memories(saved['state']['scope'])==[]
    assert len(calls)==2
    prompt=calls[0][0]['content']
    assert '一次性' in prompt and '喜欢' in prompt and '个人' in prompt


def test_explicit_remember_tool_is_not_subject_to_automatic_extraction_filter(memory_env):
    ai,agent,_=memory_env
    run=ai.create_run(agent['id'],'帮我找周杰伦的晴天',dry_run=True)
    run=ai.data.run(run['id'])
    state=run['state']
    state['memory_revision']=ai.companion.memory.profile(state['scope'])['revision']
    result=ai.companion.tool({'builtin':'remember'},dict(key='用户主动指定记录',content='查找晴天听歌链接'),run,state)
    assert json.loads(result)['saved'] is True
    assert ai.companion.memory.list_memories(state['scope'])[0]['content']=='查找晴天听歌链接'
