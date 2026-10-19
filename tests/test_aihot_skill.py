import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from wechat_panel.image_requests import execute
from wechat_panel.skills import explicitly_requested
from wechat_panel.web_policy import WebPolicy
from wechat_panel.web_reader import read_page


spec=importlib.util.spec_from_file_location('aihot_card',Path(__file__).resolve().parents[1]/'skills/ai-hot-news/scripts/prepare_card.py')
card=importlib.util.module_from_spec(spec)
spec.loader.exec_module(card)


def ranking(stamp='10月19日 16:50 更新'):
    rows=[]
    for rank in range(1,11):
        tag='article' if rank<=3 else 'li'
        rows.append(f'<{tag}><span class="mono">{rank:02}</span><h2><a href="/story/{rank}">事件{rank}</a></h2>'
                    f'<span class="mono">{300-rank}</span><span>7 个来源</span></{tag}>')
    return f'<main><p>过去48小时 {stamp}</p>'+''.join(rows)+'</main>'


def test_snapshot_keeps_display_heat_not_participants_and_changes_daily_theme():
    now=datetime(2026,10,19,17,tzinfo=card.CN)
    data=card.parse_hot(ranking(),now)
    assert [x['heat'] for x in data['items']]==list(range(299,289,-1))
    assert data['updated_at']=='2026-10-19T16:50:00+08:00' and not data['stale']
    assert card.theme_for(now.date())!=card.theme_for((now+timedelta(days=1)).date())
    prompt=card.build_prompt(data)
    assert all(f"热度 {i['heat']}" in prompt for i in data['items'])


@pytest.mark.parametrize('html',[
    ranking().replace('<span class="mono">299</span>',''),
    ranking().replace('<span class="mono">299</span>','<span class="mono">299</span><span class="mono">300</span>'),
    ranking().replace('/story/10','/story/9'),
    ranking().replace('<span class="mono">10</span>','<span class="mono">09</span>'),
    '<p>接口只有排名，没有热度</p>',
])
def test_incomplete_or_ambiguous_snapshot_is_rejected(html):
    with pytest.raises(ValueError):card.parse_hot(html,datetime(2026,10,19,17,tzinfo=card.CN))


def test_new_year_resolves_old_snapshot_and_labels_it_stale():
    data=card.parse_hot(ranking('12月30日 16:50 更新'),datetime(2027,1,2,12,tzinfo=card.CN))
    assert data['updated_at'].startswith('2026-12-30') and data['stale']
    assert '历史快照' in card.build_prompt(data)


def test_relay_fallback_reads_all_ten_cards(monkeypatch):
    real_client=httpx.Client
    def response(request):
        if request.url.host=='r.jina.ai':return httpx.Response(503)
        return httpx.Response(200,headers={'Content-Type':'text/html; charset=utf-8'},text=ranking())
    monkeypatch.setattr('wechat_panel.companion_services.public_url',lambda url:None)
    monkeypatch.setattr('wechat_panel.web_reader.httpx.Client',lambda **kw:real_client(transport=httpx.MockTransport(response),**kw))
    result=read_page(card.SOURCE,platform_routing=False)
    assert all(f'事件{i}' in result['content'] for i in range(1,11))
    assert '290' in result['content']


def test_named_bound_skill_prevents_generating_a_news_image_before_fetching():
    state={'snapshot':{'agent':{'kind':'workflow'},'skills':{'s':{'name':'Ai热点新闻','enabled':True}}}}
    run={'input':'帮我生成Ai热点新闻的图片'}
    assert explicitly_requested(state,run['input'])
    assert execute(None,run,state) is False
    assert WebPolicy(None).prepare(run,state) is True
    assert not explicitly_requested(state,'帮我画一只猫')
    state['snapshot']['skills']['s']['enabled']=False
    assert not explicitly_requested(state,run['input'])
