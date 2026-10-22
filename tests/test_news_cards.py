import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from wechat_panel import news_cards
from wechat_panel.companion_services import Services


API='https://60s.viki.moe/v2/60s?date=2026-10-22'
ARCHIVE='https://60s-static.viki.moe/60s/2026-10-22.json'
NEWS={'date':'2026-10-22','news':['第一条新闻：数字 123','第二条新闻'],'tip':'每日微语'}


def install(monkeypatch,handler,check=lambda url:url):
    client=httpx.Client
    monkeypatch.setattr(news_cards.httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr('wechat_panel.companion_services.public_url',check)


def test_native_json_preserves_all_items_without_proxy_rewriting(monkeypatch):
    install(monkeypatch,lambda req:httpx.Response(200,json={'code':200,'data':NEWS}))
    services=Services(None)
    monkeypatch.setattr(services,'get',lambda *args:pytest.fail('JSON source must not be rewritten by Fetch'))
    result=json.loads(services.fetch('configured-search',API))
    assert result['data']==NEWS and result['url']==API and not result['archive_fallback']


def test_unavailable_api_uses_only_same_publisher_dated_archive(monkeypatch):
    calls=[]
    def get(req):
        calls.append(str(req.url))
        return httpx.Response(403) if len(calls)==1 else httpx.Response(200,json=NEWS)
    install(monkeypatch,get)
    result=news_cards.read_source(API)
    assert calls==[API,ARCHIVE]
    assert result['archive_fallback'] and result['url']==ARCHIVE and result['data']==NEWS


@pytest.mark.parametrize('code,body',[(404,None),(200,{**NEWS,'date':'2026-10-21'})])
def test_missing_or_wrong_date_archive_never_becomes_today(monkeypatch,code,body):
    install(monkeypatch,lambda req:httpx.Response(403) if str(req.url)==API else httpx.Response(code,json=body))
    with pytest.raises((RuntimeError,ValueError)):news_cards.read_source(API)


def test_redirect_to_private_host_stops_before_request(monkeypatch):
    calls=[]
    def get(req):
        calls.append(str(req.url));return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    def check(url):
        if '127.0.0.1' in url:raise ValueError('private host')
    install(monkeypatch,get,check)
    with pytest.raises(ValueError,match='private host'):news_cards.read_source(API)
    assert calls==[API]


def test_oversize_source_and_invalid_date_are_rejected(monkeypatch):
    install(monkeypatch,lambda req:httpx.Response(200,content=b'x'*2_000_001))
    with pytest.raises(ValueError,match='过大'):news_cards.read_source(API)
    with pytest.raises(ValueError):news_cards.read_source(API.replace('2026-10-22','bad-date'))


@pytest.mark.parametrize('url',['https://example.org/hot','https://aihot.news.evil/hot',
                              'https://aihot.news:444/hot','https://user:pass@aihot.news/hot'])
def test_only_exact_public_source_routes_are_handled(url):
    assert news_cards.read_source(url) is None


def test_hot_source_preserves_all_ten_ranks_and_actual_heats(monkeypatch):
    html='10月22日 22:00 更新 过去48小时'
    for n in range(1,11):
        html+=f'<article><span class="mono">{n:02}</span><h2><a href="/story/{n}">新闻{n}</a></h2><span class="mono">{451-n}</span></article>'
    install(monkeypatch,lambda req:httpx.Response(200,text=html))
    monkeypatch.setattr(news_cards,'datetime',SimpleNamespace(now=lambda tz:datetime(2026,10,22,22,5,tzinfo=tz)))
    result=news_cards.read_source('https://aihot.news/hot')
    assert [x['rank'] for x in result['data']['items']]==list(range(1,11))
    assert [x['heat'] for x in result['data']['items']]==list(range(450,440,-1))
    assert result['data']['updated_at']=='2026-10-22T22:00:00+08:00'


def test_daily_ai_source_reads_only_latest_edition_including_weekend(monkeypatch):
    def item(title):
        return f'<div class="news-item"><div class="news-content"><h2><a href="/article">{title}</a></h2><p>完整正文<span class="news-time">来源：原始来源</span></p></div></div>'
    html='<div class="news-date">10月22日 周四</div>'+item('本期一')+item('本期二')
    html+='<div class="news-date">10月21日 周三</div>'+item('往期不能混入')
    install(monkeypatch,lambda req:httpx.Response(200,text=html))
    monkeypatch.setattr(news_cards,'datetime',SimpleNamespace(now=lambda tz:datetime(2026,10,24,12,tzinfo=tz)))
    result=news_cards.read_source('https://ai-bot.cn/daily-ai-news/')
    assert result['data']['date']=='2026-10-22' and not result['data']['is_current_day']
    assert [x['title'] for x in result['data']['items']]==['本期一','本期二']
    assert all(x['source']=='原始来源' for x in result['data']['items'])
