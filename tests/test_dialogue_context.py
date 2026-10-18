import json
import uuid
from pathlib import Path
import pytest
from wechat_panel.web_policy import allowed, decide, conversation_only
from wechat_panel.dialogue_rhythm import instruction
from wechat_panel.ai import AIService
from wechat_panel.store import Store


@pytest.mark.parametrize('text,chat',[
    ('你刚刚说你是AI，是真的吗？',True),
    ('我问你是不是AI，你给我查什么呢？',True),
    ('你是谁？',True),('你刚才联网了吗？',True),
    ('你刚才说今天美股下跌，是真的吗？',False),
    ('AI最新版本是多少？',False),
    ('你是不是AI？另外帮我查询今天北京的天气',False),
    ('请搜索人工智能公司的新闻',False),
])
def test_conversation_vs_external_facts(text,chat):
    s={'snapshot':{'agent':{'web_policy':'auto'}},'analysis':{'needs_web':True}}
    assert conversation_only(text)==chat
    assert allowed(s,{'input':text}) is (not chat)
    if chat:assert not decide({'input':text},s)['required']


@pytest.mark.parametrize('name',['ahui-system.md','sweet-maid-system.md'])
def test_identity_question_never_searches_even_with_bad_analysis(tmp_path,monkeypatch,name):
    captured=[]
    def model(provider,messages,tools):
        if provider.get('_structured'):return {'content':json.dumps({'needs_web':True,'risk':'none'})},{}
        captured.append((provider,messages,tools))
        return {'content':'刚才那句话是在解释角色设定。'},{}
    ai=AIService(Store(tmp_path/'panel.sqlite3'),model_call=model)
    try:
        p=ai.data.save('providers',None,dict(name='test',model='test',base_url='http://localhost:19999'))
        search=next(t for t in ai.data.list('tools') if t.get('builtin')=='web_search')
        prompt=Path('docs/prompts',name).read_text('utf-8')
        a=ai.data.save('agents',None,dict(name=name,provider_id=p['id'],tool_ids=[search['id']],prompt=prompt,temperature=.95))
        monkeypatch.setattr(ai.companion,'tool',lambda *args:pytest.fail('Identity follow-up must not search'))
        r=ai.create_run(a['id'],'你刚刚说你是AI，是真的吗？',dry_run=True)
        ai.execute(r['id']);run=ai.data.run(r['id'])
        assert run['status']=='completed',run['error']
        assert not run['state']['web_decision']['required']
        assert not run['state'].get('web_evidence')
        assert run['state']['dialogue_pacing']=='brief'
        assert captured[-1][0]['temperature']==.95
        assert not any(t['function']['name']==search['name'] for t in captured[-1][2])
        assert '1–4 小段' not in captured[-1][1][0]['content']
        assert '认真被问到身份时如实说明是 AI' not in captured[-1][1][0]['content']
        # Saving an edited prompt affects the NEXT run, not an old snapshot.
        config={k:v for k,v in a.items() if k not in ('id','key_configured')}
        config['prompt']=prompt+'\n本次新设定标记：NEW_SAVED_PROMPT'
        ai.data.save('agents',a['id'],config)
        newer=ai.create_run(a['id'],'你好',dry_run=True)
        ai.execute(newer['id'])
        assert 'NEW_SAVED_PROMPT' in captured[-1][1][0]['content']
        assert 'NEW_SAVED_PROMPT' not in run['state']['snapshot']['agent']['prompt']
    finally:ai.stop()


def test_pacing_varies_without_fixed_paragraph_quota_and_is_stable_per_run():
    def state():return {'snapshot':{'agent':{'kind':'workflow','prompt':'【按语境组织回复】'}},'context':[]}
    modes=set()
    for _ in range(40):
        s=state();run={'id':str(uuid.uuid4()),'input':'今天拍到一朵很好看的云'}
        first=instruction(run,s);assert instruction(run,s)==first
        modes.add(s['dialogue_pacing'])
    assert len(modes)>=2
    for text,expected in [('嗯','brief'),('晚安','brief'),('我工作上很迷茫，想认真聊聊怎么处理','develop')]:
        s=state();instruction({'id':'run','input':text},s);assert s['dialogue_pacing']==expected
    s=state();s['snapshot']['agent']['prompt']='普通 Agent'
    assert instruction({'id':'run','input':'你好'},s)==''


def test_short_chat_spacing_preserves_content_and_explicit_format():
    from wechat_panel.dialogue_rhythm import compact_short_reply
    reply='我在呀。\n\n刚才的问题不用搜索。'
    assert compact_short_reply({'input':'在吗'},{'dialogue_pacing':'brief'},reply)=='我在呀。 刚才的问题不用搜索。'
    assert compact_short_reply({'input':'请分段答复'},{'dialogue_pacing':'brief'},reply)==reply
    assert compact_short_reply({'input':'在吗'},{},reply)==reply
