"""Direct tool selection must still deliver the bound skill's evidence rules."""
import json

import pytest

from wechat_panel.ai import AIService
from wechat_panel.store import Store


@pytest.mark.parametrize('builtin,skill_name,args',[
    ('read_web','web-video-reader',{'url':'https://example.com/article'}),
    ('platform_read','agent-reach-platforms',{'platform':'github','target':'https://github.com/psf/requests'}),
    ('news_collect','news-aggregator-skill',{'source':'github','limit':3}),
])
@pytest.mark.parametrize('approval',['auto','confirm'])
def test_direct_reader_result_contains_bound_guide_without_bypassing_skill_approval(tmp_path,monkeypatch,builtin,skill_name,args,approval):
    calls=[]
    def model(provider,messages,tools):
        if messages[-1]['role']!='tool':
            return {'content':'','tool_calls':[{'id':'reader-call','type':'function','function':{
                'name':builtin,'arguments':json.dumps(args)}}]},{}
        result=json.loads(messages[-1]['content'])
        assert result['coverage']=='page' and result['text']=='Actual article body'
        if approval=='auto':
            assert result['bound_skill_context']['skill']==skill_name
            assert 'only actual text' in result['bound_skill_context']['instructions']
        else:assert 'bound_skill_context' not in result
        return {'content':'页面介绍了一项具体功能。'},{}
    ai=AIService(Store(tmp_path/'panel.sqlite3'),model_call=model)
    try:
        provider=ai.data.save('providers',None,dict(name='test',model='fake',base_url='http://127.0.0.1:9/v1'))
        skill=ai.data.save('skills',None,dict(name=skill_name,description='Read actual sources',instructions='Summarize only actual text; never invent subtitles.'))
        reader=next(t for t in ai.data.list('tools') if t.get('builtin')==builtin)
        loader=next(t for t in ai.data.list('tools') if t.get('builtin')=='use_skill')
        loader=ai.data.save('tools',loader['id'],{k:v for k,v in loader.items() if k not in ('id','key_configured')}|{'approval':approval})
        agent=ai.data.save('agents',None,dict(name='test',provider_id=provider['id'],tool_ids=[reader['id']],
            skill_ids=[skill['id']],web_policy='model',companion={'memory':{'enabled':False},
                'safety':{'emotion_enabled':False,'crisis_detection':False}}))
        def read(tool,args,run,state):
            calls.append(tool['builtin'])
            return json.dumps({'url':'https://example.com/article','coverage':'page','text':'Actual article body'})
        monkeypatch.setattr(ai.companion,'tool',read)
        request={'read_web':'请读取 https://example.com/article 这个页面并总结',
                 'platform_read':'请读取 GitHub 的 https://github.com/psf/requests 仓库说明',
                 'news_collect':'请用 news-aggregator-skill 汇总三条新闻'}[builtin]
        run=ai.create_run(agent['id'],request,dry_run=True)
        ai.execute(run['id']);run=ai.data.run(run['id'])
        assert run['status']=='completed',run['error']
        assert calls==[builtin]
        assert (skill_name in run['state'].get('active_skills',[]))==(approval=='auto')
        assert not ai.store.pending()
    finally:ai.stop()


def test_active_specialized_skill_is_not_replaced_by_generic_reader_context():
    from wechat_panel.skills import context_for_tool
    state={'active_skills':['Ai日报'],'snapshot':{'tools':{'u':{'builtin':'use_skill','enabled':True,'approval':'auto'}},
        'skills':{'s':{'name':'web-video-reader','enabled':True}}}}
    assert context_for_tool({'builtin':'read_web'},state) is None
    assert state['active_skills']==['Ai日报']


def test_ambiguous_bound_skill_does_not_break_an_authorized_reader():
    from wechat_panel.skills import context_for_tool
    state={'snapshot':{'tools':{'u':{'builtin':'use_skill','enabled':True,'approval':'auto'}},
        'skills':{'s1':{'name':'web-video-reader','enabled':True},'s2':{'name':'web-video-reader','enabled':True}}}}
    assert context_for_tool({'builtin':'read_web'},state) is None
    assert not state.get('active_skills')
