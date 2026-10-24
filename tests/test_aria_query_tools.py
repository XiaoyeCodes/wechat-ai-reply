"""Read-only query contracts; URI tool checks never open an external app."""
import json
from types import SimpleNamespace

import pytest

from wechat_panel import mcp_client
from wechat_panel.companion import CompanionRuntime
from wechat_panel.companion import _music_results
from wechat_panel.companion_services import Services


URI_CASES = [
    ('maps_schema_navi', {'lon': '116.481075', 'lat': '39.996743'}),
    ('maps_schema_take_taxi', {'dlon': '116.481075', 'dlat': '39.996743', 'dname': '公开地点测试'}),
    ('maps_schema_personal_map', {'orgName': '本地契约测试', 'lineList': [{'title': '公开地点', 'pointInfoList': [{'name': '望京SOHO', 'lon': 116.481075, 'lat': 39.996743, 'poiId': 'B000AAFAC5'}]}]}),
]


@pytest.mark.parametrize('name,arguments', URI_CASES)
def test_uri_tools_only_return_provider_content_and_never_open_apps(monkeypatch, name, arguments):
    import subprocess
    import webbrowser
    calls = []
    connection = {'enabled': True, 'url': 'https://mcp.example.com/mcp', 'auth': 'none', 'profile': 'amap'}
    data = SimpleNamespace(get=lambda *args, **kwargs: connection)
    tool = {'connection_id': 'isolated', 'remote_name': name}
    result = {'content': [{'type': 'text', 'text': 'amapuri://inert-local-contract-example'}], 'isError': False}

    def request(config, remote_name, args):
        assert config == connection and remote_name == name and args == arguments
        calls.append((remote_name, args))
        return result

    def must_not_open(*args, **kwargs):
        pytest.fail('Returning a URI must not launch an app or process')

    monkeypatch.setattr(mcp_client, 'request', request)
    monkeypatch.setattr(webbrowser, 'open', must_not_open)
    monkeypatch.setattr(subprocess, 'Popen', must_not_open)
    output = json.loads(mcp_client.execute(data, tool, arguments))
    assert output == {'ok': True, 'result': result}
    assert len(calls) == 1


@pytest.mark.parametrize('name,arguments', URI_CASES)
def test_uri_query_failure_is_not_replayed_or_claimed_success(monkeypatch, name, arguments):
    connection = {'enabled': True, 'url': 'https://mcp.example.com/mcp', 'auth': 'none', 'profile': 'amap'}
    data = SimpleNamespace(get=lambda *args, **kwargs: connection)
    calls = []

    def unavailable(*args):
        calls.append(args)
        raise RuntimeError('MCP request unavailable')

    monkeypatch.setattr(mcp_client, 'request', unavailable)
    output = json.loads(mcp_client.execute(data, {'connection_id': 'isolated', 'remote_name': name}, arguments))
    assert output['ok'] is False and 'result' not in output
    assert len(calls) == 1


def test_weather_uses_geocoded_coordinates_and_requests_seven_days(monkeypatch):
    from wechat_panel import companion_services
    calls = []
    dates = ['2026-10-' + str(day) for day in range(23, 30)]

    def request(url, params, **kwargs):
        calls.append((url, params, kwargs))
        if 'geocoding-api' in url:
            return {'results': [{'name': '北京', 'country': '中国', 'latitude': 39.9, 'longitude': 116.4}]}
        assert params['latitude'] == 39.9 and params['longitude'] == 116.4
        assert params['forecast_days'] == 7 and params['timezone'] == 'auto'
        return {'current': {'temperature_2m': 20, 'time': '2026-10-23T21:45'}, 'daily': {'time': dates}, 'timezone': 'Asia/Shanghai'}

    monkeypatch.setattr(companion_services, 'request_json', request)
    output = json.loads(Services.weather('北京'))
    assert output['city'] == '北京' and output['daily']['time'] == dates
    assert len(calls) == 2


@pytest.mark.parametrize('name', ['places_search', 'movie_search'])
def test_topic_queries_use_the_agents_selected_search_connection(name):
    calls = []
    runtime = CompanionRuntime.__new__(CompanionRuntime)
    runtime.services = SimpleNamespace(search=lambda sid, query, **kwargs: calls.append((sid, query, kwargs)) or '{"results":[{"title":"real result"}]}')
    state = {'snapshot': {'agent': {'companion': {'search_service_id': 'selected-search'}}}}
    result = runtime.tool({'builtin': name}, {'query': '公开资料测试'}, {'dry_run': True}, state)
    assert json.loads(result)['results'] and calls[0][0] == 'selected-search'
    assert calls[0][1].startswith('公开资料测试') and calls[0][2]['limit'] == 5


def test_music_search_does_not_present_news_lyrics_or_charts_as_a_song():
    raw = {'engine': 'configured-search', 'results': [
        {'title': '晴天歌词提交', 'url': 'https://github.com/example/lyrics/pull/123'},
        {'title': '周杰伦在网易云音乐的版权', 'url': 'https://mp.weixin.qq.com/s/article'},
        {'title': '周杰伦播放量排行', 'url': 'https://kworb.net/artist/jay.html'},
        {'title': 'Spotify 艺术家', 'url': 'https://open.spotify.com/artist/' + 'A' * 22},
    ]}
    result = json.loads(_music_results('周杰伦 晴天', json.dumps(raw)))
    assert result['results'] == [] and result['status'] == 'not_found'
    assert len(result['search_entries']) == 2
    assert all(entry['kind'] == 'search_entry' for entry in result['search_entries'])


def test_music_search_rejects_another_song_whose_sidebar_mentions_requested_song():
    raw = {'results': [{'title': '夜曲+竊愛 (Live)', 'url': 'https://open.spotify.com/track/' + 'A' * 22,
                       'summary': '周杰倫热门曲目：晴天、晴天 (Live)、稻香'}]}
    output = json.loads(_music_results('周杰伦 晴天 官方歌曲', json.dumps(raw)))
    assert output['results'] == [] and output['status'] == 'not_found'


def test_music_search_matches_track_title_before_artist_and_platform_suffix():
    raw = {'results': [{'title': '晴天 (Live) - 周杰伦 - 单曲 - 网易云音乐', 'url': 'https://music.163.com/song?id=186016'}]}
    output = json.loads(_music_results('周杰伦 晴天', json.dumps(raw)))
    assert output['status'] == 'found_track_links' and len(output['results']) == 1


@pytest.mark.parametrize('url', [
    'https://music.163.com/song?id=186016',
    'https://music.163.com/#/song?id=186016',
    'https://y.music.163.com/m/song?id=186016',
    'https://open.spotify.com/track/' + 'A' * 22,
    'https://open.spotify.com/intl-zh/track/' + 'A' * 22,
    'https://y.qq.com/n/ryqq/songDetail/001A2B3C4D5E6F',
    'https://i.y.qq.com/v8/playsong.html?songmid=001A2B3C4D5E6F',
])
def test_music_search_preserves_actual_platform_track_links(url):
    raw = {'results': [{'title': '晴天', 'url': url}, {'title': 'duplicate', 'url': url}]}
    result = json.loads(_music_results('晴天', json.dumps(raw)))
    assert result['status'] == 'found_track_links'
    assert result['results'] == [{'title': '晴天', 'url': url, 'kind': 'track_link'}]


@pytest.mark.parametrize('url', [
    'https://music.163.com.evil.example/song?id=186016',
    'https://music.163.com/playlist?id=186016',
    'https://open.spotify.com/search/晴天',
    'https://open.spotify.com/track/not-a-track',
    'https://example.org/?next=https://music.163.com/song?id=186016',
    'https://user:password@music.163.com/song?id=186016',
    'http://[malformed',
])
def test_music_search_rejects_non_track_urls_and_host_impersonation(url):
    result = json.loads(_music_results('晴天', json.dumps({'results': [{'title': 'song', 'url': url}]})))
    assert result['results'] == [] and result['status'] == 'not_found'


def test_music_queries_use_selected_service_and_target_music_domains():
    calls = []
    runtime = CompanionRuntime.__new__(CompanionRuntime)
    runtime.services = SimpleNamespace(search=lambda sid, query, **kwargs: calls.append((sid, query, kwargs)) or '{"results":[]}')
    state = {'snapshot': {'agent': {'companion': {'search_service_id': 'selected-search'}}}}
    output = json.loads(runtime.tool({'builtin': 'music_search'}, {'query': '周杰伦 晴天'}, {'dry_run': True}, state))
    assert output['status'] == 'not_found' and calls[0][0] == 'selected-search'
    assert 'site:music.163.com' in calls[0][1] and 'site:open.spotify.com' in calls[0][1]
    assert len(calls) == 1
