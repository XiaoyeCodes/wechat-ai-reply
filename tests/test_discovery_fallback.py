"""Unknown wording gets one discovery chance, not an empty-catalog dead end."""
import json

import pytest

from wechat_panel.ai import AIService
from wechat_panel.failure_replies import REPLY
from wechat_panel.store import Store


@pytest.mark.parametrize('discover',[True,False])
def test_unmatched_live_question_gets_one_bounded_search_before_failure(tmp_path,monkeypatch,discover):
    seen=[]
    def call(name,args,cid):
        return {'content':'','tool_calls':[{'id':cid,'type':'function','function':{
            'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}
    def model(provider,messages,specs):
        if provider.get('_structured'):
            return {'content':json.dumps({'mood':'平静','summary':'','facts':[],'risk':'none','needs_web':True},ensure_ascii=False)},{}
        seen.append(messages[-1]['content'])
        if len(seen)==1:return {'content':REPLY},{}
        if len(seen)==2:
            assert '没有预选技能不代表没有能力' in messages[-1]['content']
            return (call('search_capabilities',{'query':'专业词条 术语检索'},'discover') if discover else {'content':REPLY}),{}
        if len(seen)==3:
            assert 'lookup_glossary' in {s['function']['name'] for s in specs}
            return call('lookup_glossary',{'term':'涟漪圈'},'read'),{}
        return {'content':'公开词条显示该话题近期有更新。'},{}
    ai=AIService(Store(tmp_path/'panel.sqlite3'),model_call=model)
    try:
        provider=ai.data.save('providers',None,dict(name='fake',model='fake',base_url='http://localhost:1'))
        skill=ai.data.save('skills',None,dict(name='glossary-entry',description='专业词条 术语检索',instructions='查询真实词条，不猜测。'))
        tool=ai.data.save('tools',None,dict(name='lookup_glossary',description='专业词条 术语检索',method='GET',
            url='http://localhost:1/test-only',approval='auto',parameters={'type':'object','properties':{'term':{'type':'string'}},'required':['term']}))
        agent=ai.data.save('agents',None,dict(name='test',provider_id=provider['id'],tool_ids=[tool['id']],skill_ids=[skill['id']],web_policy='model',
            companion={'memory':{'enabled':True,'auto_extract':False},'safety':{'emotion_enabled':True,'crisis_detection':False}}))
        executed=[]
        def execute(t,args,*rest):
            assert t['id']==tool['id'];executed.append(args)
            return json.dumps({'results':[{'title':'公开词条','url':'https://example.com/entry','content':'近期实际更新。'}]},ensure_ascii=False)
        monkeypatch.setattr('wechat_panel.ai.execute_tool',execute)
        run=ai.create_run(agent['id'],'最近涟漪圈大家都在聊什么？',dry_run=True)
        ai.execute(run['id']);final=ai.data.run(run['id'])
        assert len(seen)==(4 if discover else 2)
        assert final['status']==('completed' if discover else 'failed')
        assert executed==([{'term':'涟漪圈'}] if discover else [])
        assert not ai.store.pending()
        assert sum(t['step']=='能力发现补查' for t in ai.data.detail(run['id'])['trace'])==1
    finally:ai.stop()
