"""Same-source public reader recovery, with all external I/O stubbed out."""
import json

import pytest

from wechat_panel.companion_services import Services
from wechat_panel.web_reader import ReadUnavailable


URL = 'https://docs.python.org/3/library/json.html?audit=1#decoder'
CONTENT = 'The JSON module serializes Python objects into JSON and deserializes JSON documents.'


@pytest.fixture
def service(monkeypatch):
    services = Services(None)
    monkeypatch.setattr('wechat_panel.companion_services.public_url', lambda url: url)
    monkeypatch.setattr('wechat_panel.news_cards.read_source', lambda url: None)
    monkeypatch.setattr(services, 'get', lambda sid, category: {'driver': 'deepseek'})
    # Any accidental attempt to call the external readers is a test failure.
    def external(*args, **kwargs):raise AssertionError('Real network/CLI is prohibited in this suite')
    monkeypatch.setattr('wechat_panel.web_reader.httpx.Client', external)
    monkeypatch.setattr('wechat_panel.web_reader.cli_json', external)
    monkeypatch.setattr(services, 'read_web', external)
    monkeypatch.setattr(services, 'deepseek_call', external)
    return services


def test_deepseek_success_never_calls_public_fallback(service, monkeypatch):
    calls = []
    def fetch(config, operation, body):
        calls.append((operation, body))
        return {'title': 'JSON documentation', 'content': CONTENT}
    monkeypatch.setattr(service, 'deepseek_call', fetch)
    result = json.loads(service.fetch('configured', URL))
    assert calls == [('fetch', {'url': URL})]
    assert result['engine'] == 'deepseek_fetch' and result['url'] == URL
    assert result['content'] == CONTENT


@pytest.mark.parametrize('failure', [RuntimeError('temporary provider failure'), ReadUnavailable('no body'), None,
                                      {'content': ''}, {'title': 'Access Denied', 'content': CONTENT}])
def test_failure_uses_same_public_url_once_and_preserves_coverage(service, monkeypatch, failure):
    calls = []
    def fetch(config, operation, body):
        calls.append(('deepseek', body['url']))
        if isinstance(failure, Exception):raise failure
        return failure
    def public(url):
        calls.append(('public', url))
        return json.dumps({'url': url, 'title': 'JSON docs', 'coverage': 'page', 'content': CONTENT})
    monkeypatch.setattr(service, 'deepseek_call', fetch)
    monkeypatch.setattr(service, 'read_web', public)
    result = json.loads(service.fetch('configured', URL))
    assert calls == [('deepseek', URL), ('public', URL)]
    assert result['url'] == URL and result['coverage'] == 'page'
    assert result['engine'] == 'public_page_fallback' and result['content'] == CONTENT
    assert 'temporary provider failure' not in str(result)


@pytest.mark.parametrize('failure', [RuntimeError('timeout'), ReadUnavailable('403 Forbidden')])
def test_both_readers_fail_honestly_without_another_attempt(service, monkeypatch, failure):
    calls = []
    def fetch(*args):raise RuntimeError('provider unavailable')
    def public(url):
        calls.append(url)
        raise failure
    monkeypatch.setattr(service, 'deepseek_call', fetch)
    monkeypatch.setattr(service, 'read_web', public)
    with pytest.raises(ReadUnavailable, match='未取得可用正文'):
        service.fetch('configured', URL)
    assert calls == [URL]


@pytest.mark.parametrize('page', [
    {'url': URL, 'coverage': 'page', 'title': 'Access Denied', 'content': CONTENT},
    {'url': URL, 'coverage': 'metadata_only', 'content': CONTENT},
    {'url': URL, 'coverage': 'page', 'snippet': CONTENT},
    {'url': URL, 'coverage': 'page', 'content': '404 Not Found: this article is unavailable and cannot be retrieved.'},
    [], 'not-json',
])
def test_fallback_rejects_challenge_metadata_and_search_snippets(service, monkeypatch, page):
    def fetch(*args):raise ReadUnavailable('no body')
    monkeypatch.setattr(service, 'deepseek_call', fetch)
    monkeypatch.setattr(service, 'read_web', lambda url: json.dumps(page))
    with pytest.raises(ReadUnavailable, match='未取得可用正文'):
        service.fetch('configured', URL)


def test_original_url_stays_provenance_when_reader_resolves_canonical_location(service, monkeypatch):
    def fetch(*args):raise RuntimeError('unavailable')
    monkeypatch.setattr(service, 'deepseek_call', fetch)
    monkeypatch.setattr(service, 'read_web', lambda url: json.dumps({'url': URL.replace('?audit=1#decoder', ''),
                        'coverage': 'page', 'content': CONTENT}))
    result = json.loads(service.fetch('configured', URL))
    assert result['url'] == URL and result['resolved_url'] == 'https://docs.python.org/3/library/json.html'


def test_source_adapters_still_take_precedence_and_do_not_switch_sources(service, monkeypatch):
    source = {'url': URL, 'coverage': 'complete_latest_edition', 'data': {'items': ['Actual news']}}
    monkeypatch.setattr('wechat_panel.news_cards.read_source', lambda url: source)
    assert json.loads(service.fetch('configured', URL)) == source


def test_disabled_misconfigured_service_is_not_silently_bypassed(service, monkeypatch):
    def disabled(*args):raise ValueError('service disabled')
    monkeypatch.setattr(service, 'get', disabled)
    with pytest.raises(ValueError, match='service disabled'):
        service.fetch('configured', URL)


def test_invalid_public_target_is_rejected_before_either_reader(service, monkeypatch):
    def invalid(url):raise ValueError('private target')
    monkeypatch.setattr('wechat_panel.companion_services.public_url', invalid)
    with pytest.raises(ValueError, match='private target'):
        service.fetch('configured', URL)
