"""A skill is optional: ordinary approved search still serves user requests."""
import json

import pytest

from test_aria_conversation_tools import conversations,chat,no_duplicate
from test_routed_conversations import receive


def configure(env,monkeypatch,skill_mode):
    tools=env.ai.data.list('tools')
    env.model.names={t['builtin']:t['name'] for t in tools if t.get('builtin')}
    skills=[]
    if skill_mode!='none':
        skill=env.ai.data.save('skills',None,dict(name='Ai日报' if skill_mode=='disabled' else '菜谱整理',
            description='最新AI日报' if skill_mode=='disabled' else '按用户提供食材整理做饭步骤',
            instructions='按专用步骤处理。',enabled=skill_mode!='disabled'))
        skills=[skill['id']]
    body={k:v for k,v in env.agent.items() if k not in ('id','key_configured','secret')}
    body.update(skill_ids=skills,tool_ids=[t['id'] for t in tools if t.get('builtin') in ('web_search','read_web')],
                knowledge_ids=[],web_policy='model',max_tool_rounds=4)
    env.agent=env.ai.data.save('agents',env.agent['id'],body)
    searches=[]
    def search(sid,query,limit=5):
        searches.append(query)
        source=({'title':'上海明天天气预报','url':'https://example.com/shanghai-weather',
                 'snippet':'上海明天天气有雨，气温20℃，出门请带伞。'} if '天气' in query else
                {'title':'当期AI科技新闻','url':'https://example.com/ai-news',
                 'snippet':'最新AI新闻有技术发布、工具更新、研究进展。'})
        return json.dumps({'results':[source]},ensure_ascii=False)
    monkeypatch.setattr(env.ai.companion.services,'search',search)
    return searches


@pytest.mark.parametrize('kind',['direct','group'])
@pytest.mark.parametrize('skill_mode',['none','irrelevant','disabled'])
def test_web_search_works_without_a_matching_skill(conversations,monkeypatch,kind,skill_mode):
    env=conversations;searches=configure(env,monkeypatch,skill_mode);target=chat(env,kind)
    env.model.tool('web_search',{'query':'今天 最新AI新闻'},None)
    env.model.answer('最新三条AI消息是技术发布、工具更新、研究进展。')
    run=receive(env,target,'查一下最新AI新闻，先搜网上相关信息再回答，只要文字。')
    assert searches==['今天 最新AI新闻']
    assert not run['state'].get('active_skills') and not run['state'].get('task_failure')
    assert env.bridge.sent==[(target['id'],'最新三条AI消息是技术发布、工具更新、研究进展。')]
    assert any(x.get('status')=='ok' for x in run['state']['web_evidence'])
    no_duplicate(env,target)
    assert not env.model.steps


@pytest.mark.parametrize('kind',['direct','group'])
def test_compound_information_request_can_use_only_search(conversations,monkeypatch,kind):
    env=conversations;searches=configure(env,monkeypatch,'none');target=chat(env,kind)
    original=env.ai.model_call
    def with_planner(provider,messages,specs):
        if provider.get('_structured') and '任务计划' in messages[0].get('content',''):
            return {'content':'{}'},{}
        return original(provider,messages,specs)
    env.ai.model_call=with_planner
    env.model.tool('web_search',{'query':'上海 明天 天气 预报'},None)
    env.model.tool('web_search',{'query':'今天 最新AI新闻'},None)
    env.model.answer('上海明天有雨，请带伞；今天的AI消息包括技术发布、工具更新和研究进展。')
    run=receive(env,target,'查上海明天天气，并搜索今天AI新闻，只要文字。')
    assert len(searches)==2 and not run['state'].get('active_skills')
    assert run['state']['task_plan']['status']=='complete'
    assert all(step['status']=='done' and step['evidence'] for step in run['state']['task_plan']['steps'])
    assert len(env.bridge.sent)==1 and env.bridge.sent[0][0]==target['id']
    no_duplicate(env,target)
    assert not env.model.steps
