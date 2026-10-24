"""Unresolved plans cannot start effects, and approved reader guides can load."""
import pytest

from test_task_planning import env,step
from wechat_panel import task_planning as planning
from wechat_panel.skills import context_for_tool


@pytest.mark.parametrize('builtin',['weather','generate_image','set_reminder'])
def test_unresolved_plan_only_allows_capability_discovery(builtin):
    ai,run,state=env('查天气，并处理一个未配置的任务')
    state['task_plan']={'status':'needs_discovery','steps':[]}
    assert planning.before_tool(state,state['snapshot']['tools'][builtin],{})
    assert not planning.before_tool(state,state['snapshot']['tools']['search_capabilities'],{})


def test_unplanned_write_cannot_borrow_a_valid_query_plan():
    ai,run,state=env('查上海天气和当前时间')
    state['task_plan']={'status':'pending','steps':[{
        **step('weather','查询天气','query',['weather']),'status':'pending','evidence':[]}]}
    assert planning.before_tool(state,state['snapshot']['tools']['generate_image'],{})
    assert planning.before_tool(state,state['snapshot']['tools']['set_reminder'],{})
    assert not planning.before_tool(state,state['snapshot']['tools']['read_web'],{})


def test_standard_english_daily_news_package_gets_its_automatic_reader_guide():
    skill={'id':'daily','name':'daily-news-60s','enabled':True,'description':'60秒世界新闻',
           'instructions':'读取当天完整原文。','files':[]}
    state={'snapshot':{'skills':{'daily':skill},'tools':{
        'load':{'id':'load','builtin':'use_skill','enabled':True,'approval':'auto'}}},
        'capability_route':{'enabled':True,'candidate_skill_ids':['daily'],'news_families':['daily60'],
                            'expanded_tool_ids':[]},'active_skills':[]}
    guide=context_for_tool({'builtin':'read_web'},state)
    assert guide['skill']=='daily-news-60s' and '完整原文' in guide['instructions']
    assert state['active_skills']==['daily-news-60s']
