import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from wechat_panel.skills import explicitly_requested

spec=importlib.util.spec_from_file_location('bot_card',Path(__file__).resolve().parents[1]/'skills/ai-bot-daily-news/scripts/prepare_card.py')
card=importlib.util.module_from_spec(spec)
spec.loader.exec_module(card)


def page(label='10月19·周一', count=3):
    rows=''.join(f'<div class="news-item"><div class="news-content"><h2><a href="https://example.org/{i}">最新事件{i}</a></h2>'
                 f'<p>新闻{i}的内容<span class="news-time">来源：新闻源{i}</span></p></div></div>' for i in range(count))
    # The real site nests older news lists inside the latest list.
    return '<div class="news-list"><div class="news-date">'+label+'</div>'+rows+(
        '<div class="news-list"><div class="news-date">10月16·周五</div><div class="news-item"><div class="news-content">'
        '<h2><a href="https://example.org/old">旧新闻</a></h2><p>不要混入<span class="news-time">来源：旧来源</span></p></div></div></div></div>')


def test_only_latest_group_in_nested_lists_is_used():
    data=card.latest_edition(page(),datetime(2026,10,19,18,tzinfo=card.CN))
    assert data['date']=='2026-10-19' and data['is_current_day']
    assert len(data['items'])==3 and all('旧' not in str(row) for row in data['items'])
    assert data['items'][0]['content']=='新闻0的内容'
    assert data['items'][0]['source']=='新闻源0'


@pytest.mark.parametrize('fetched',[
    datetime(2026,10,24,12,tzinfo=card.CN),datetime(2026,10,25,12,tzinfo=card.CN),datetime(2026,10,28,12,tzinfo=card.CN),
])
def test_weekend_and_delayed_updates_keep_latest_actual_date(fetched):
    data=card.latest_edition(page('10月23·周五'),fetched)
    prompt=card.build_prompt(data)
    assert data['date']=='2026-10-23' and not data['is_current_day']
    assert '2026.10.23' in prompt and '网站最近一期' in prompt
    assert f'截至{fetched.date().isoformat()}' in prompt
    assert '旧新闻' not in prompt


def test_latest_edition_can_cross_year_boundary():
    data=card.latest_edition(page('12月31·周四'),datetime(2027,1,2,12,tzinfo=card.CN))
    assert data['date']=='2026-12-31'


@pytest.mark.parametrize('html',[
    page(count=0),page().replace('10月19·周一','10月19·周二'),
    page().replace('来源：新闻源0',''),page().replace('class="news-date"','class="missing"'),
])
def test_empty_or_unreliable_latest_group_does_not_silently_use_older_news(html):
    with pytest.raises(ValueError):card.latest_edition(html,datetime(2026,10,19,18,tzinfo=card.CN))


@pytest.mark.parametrize('name,query,expected',[
    ('Ai热点新闻','看看今天的AI热点',True),('Ai热点新闻','给我每日AI新闻',False),
    ('每日AI新闻','给我最新AI资讯',True),('每日AI新闻','AI热点有哪些',False),
    ('每日AI新闻','读取AIBase日报',False),('Ai热点新闻','AIHot榜单',True),
])
def test_source_specific_news_intents_do_not_get_captured_by_generic_shortcuts(name,query,expected):
    state={'snapshot':{'skills':{'s':{'name':name,'enabled':True}}}}
    assert explicitly_requested(state,query) is expected
