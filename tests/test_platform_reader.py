import json
from pathlib import Path

import pytest

from wechat_panel import platform_reader as reader
from wechat_panel.platform_catalog import PLATFORMS, BUILTINS
from wechat_panel.web_policy import allowed, decide, evidence, ONLINE


@pytest.fixture
def calls(monkeypatch):
    calls = []
    monkeypatch.setattr(reader, 'checked_url', lambda url, platform=None: url)
    monkeypatch.setattr(reader.time, 'sleep', lambda _: None)
    def cli(name, args, timeout=40):
        calls.append((name, args))
        return [{'text': '实际读取的内容', 'url': 'https://example.com/source'}]
    monkeypatch.setattr(reader, 'cli_json', cli)
    return calls


@pytest.mark.parametrize('platform', ['twitter','reddit','facebook','instagram','linkedin','xueqiu','bilibili','youtube','github','boss'])
def test_search_routes_to_read_only_fixed_commands(calls, platform):
    result = json.loads(reader.execute('platform_search', dict(platform=platform, query='OpenAI', limit=2)))
    assert result['ok']
    name, args = calls[-1]
    assert 'search' in args or 'ytsearch2:OpenAI' in args
    assert not any(x in args for x in ['login','post','send','write','delete'])
    if platform == 'boss':assert name == 'opencli' and args[:2] == ['boss','search']


def test_xiaohongshu_uses_bounded_read_only_browser_reader(calls,monkeypatch):
    from types import SimpleNamespace
    commands=[]
    monkeypatch.setattr(reader.shutil,'which',lambda name:'C:/tools/'+name)
    def run(args,**kwargs):
        commands.append(args)
        assert kwargs['timeout']==60
        return SimpleNamespace(returncode=0,stdout='[{"title":"咖啡","url":"https://www.xiaohongshu.com/explore/test"}]')
    monkeypatch.setattr(reader.subprocess,'run',run)
    result=json.loads(reader.execute('platform_search',dict(platform='xiaohongshu',query='咖啡',limit=2)))
    assert result['ok'] and commands[0][-2:]==['咖啡','2']
    assert commands[0][1].endswith('xiaohongshu_reader.mjs')


@pytest.mark.parametrize('platform,action,target,command', [
    ('twitter','user','OpenAI','tweets'), ('twitter','comments','12345','thread'),
    ('reddit','comments','abc123','read'), ('instagram','user','nasa','user'),
    ('xiaohongshu','comments','abc123','comments'), ('xueqiu','stock','NVDA','stock'),
    ('linkedin','company','openai','company'), ('facebook','feed','','feed'),
    ('bilibili','hot','','hot'), ('xiaoyuzhou','podcast','69dd0c98e2c8be31551f6a33','podcast-episodes'),
])
def test_browse_fixed_allowlist(calls, platform, action, target, command):
    result = json.loads(reader.execute('platform_browse', dict(platform=platform, action=action, target=target, limit=2)))
    assert result['ok'] and command in calls[-1][1]


@pytest.mark.parametrize('query', ['--output=/tmp/file', '-x', '\x00', 'hello\nworld'])
def test_cli_option_injection_is_rejected(calls, query):
    result = json.loads(reader.execute('platform_search', dict(platform='github',query=query,limit=2)))
    assert not result['ok'] and not calls


def test_shell_metacharacters_remain_a_single_argument(calls):
    query = 'hello; $(whoami) & test'
    reader.execute('platform_search', dict(platform='github',query=query,limit=2))
    assert calls[-1][1][2] == query


@pytest.mark.parametrize('target', ['https://github.com.evil.example/a/b','http://localhost/a','file:///C:/secret'])
def test_cross_platform_urls_are_rejected(target):
    with pytest.raises(ValueError):reader.checked_url(target, 'github')


def test_local_addresses_rejected_even_for_generic_feed():
    with pytest.raises(ValueError):reader.checked_url('http://127.0.0.1/admin')


def test_write_and_unknown_actions_are_not_exposed(calls):
    result = json.loads(reader.execute('platform_browse', dict(platform='twitter', action='post', target='text', limit=1)))
    assert not result['ok'] and not calls


@pytest.mark.parametrize('data', [[],{},None,{'ok':False},{'error':'login required'},{'items':[]}])
def test_empty_and_failure_results_are_not_success(monkeypatch, data):
    monkeypatch.setattr(reader,'search',lambda *args:data)
    result = reader.execute('platform_search', dict(platform='github',query='test',limit=2))
    assert json.loads(result)['ok'] is False
    assert evidence(result)['status']=='error'


def test_rss_returns_content_without_creating_subscription(monkeypatch):
    monkeypatch.setattr(reader,'checked_url',lambda u:u)
    monkeypatch.setattr(reader,'fetch',lambda u:(b'<rss version="2.0"><channel><title>News</title><item><title>Item</title><link>https://example.com/1</link><description>Body</description></item></channel></rss>',u))
    result = json.loads(reader.execute('platform_read',dict(platform='rss',target='https://example.com/feed')))
    assert result['ok'] and result['data']['entries'][0]['summary']=='Body'
    assert '未创建' in result['data']['note']


def test_youtube_reads_actual_subtitle_file(monkeypatch, calls):
    monkeypatch.setattr(reader.shutil,'which',lambda _: 'yt-dlp')
    def run(args,**kwargs):
        folder=Path(args[args.index('--paths')+1])
        (folder/'test.info.json').write_text(json.dumps({'title':'Test','description':'Intro'}),'utf-8')
        (folder/'test.en.json3').write_text(json.dumps({'events':[{'segs':[{'utf8':'Subtitle'}]},{'segs':[{'utf8':'Subtitle'}]}]}),'utf-8')
        assert '--ignore-config' in args and '--skip-download' in args
    monkeypatch.setattr(reader.subprocess,'run',run)
    result=reader.youtube_read('https://www.youtube.com/watch?v=abcdefghijk')
    assert result['coverage']=='subtitles' and result['transcript']=='Subtitle'


def test_youtube_does_not_claim_metadata_as_subtitles(monkeypatch,calls):
    monkeypatch.setattr(reader.shutil,'which',lambda _:None)
    monkeypatch.setattr(reader,'browser',lambda *a:[{'title':'Only title','status':'ok'}])
    result=json.loads(reader.execute('platform_read',dict(platform='youtube',target='https://youtu.be/abcdefghijk')))
    assert not result['ok']


def test_podcast_reads_transcript_in_temporary_directory(monkeypatch):
    def cli(name,args,timeout):
        folder=Path(args[args.index('--output')+1])/args[2];folder.mkdir()
        (folder/'transcript.txt').write_text('播客的真实转写','utf-8')
        return [{'status':'success','text_file':str(folder/'transcript.txt')}]
    monkeypatch.setattr(reader,'cli_json',cli)
    result=reader.podcast_read('69dd0c98e2c8be31551f6a33')
    assert result['transcript']=='播客的真实转写'


def test_specialized_urls_route_without_recursive_reading(monkeypatch):
    from wechat_panel import web_reader
    from wechat_panel import companion_services
    monkeypatch.setattr(companion_services,'public_url',lambda u:u)
    monkeypatch.setattr(reader,'execute',lambda name,args:json.dumps({'ok':True,'platform':args['platform'],'data':'content'}))
    assert web_reader.read_page('https://youtu.be/abcdefghijk')['platform']=='youtube'


def test_web_policy_respects_off_and_selects_platform_tools():
    state={'snapshot':{'agent':{'web_policy':'auto'},'tools':{'p':{'builtin':'platform_search','enabled':True}}}}
    run={'input':'帮我在小红书上搜咖啡'}
    assert not decide(run,state)['required']
    assert '平台' in decide(run,state)['reason']
    state['snapshot']['agent']['web_policy']='off'
    assert not allowed(state,run)
    assert {b[0] for b in BUILTINS} <= ONLINE
    assert len(PLATFORMS)==16


def test_tools_register_and_offline_execution_is_blocked(tmp_path):
    from wechat_panel.ai import AIService
    from wechat_panel.store import Store
    ai=AIService(Store(tmp_path/'panel.sqlite3'))
    try:
        provider=ai.data.save('providers',None,dict(name='test',model='test',base_url='http://localhost:9999'))
        tool=next(t for t in ai.data.list('tools') if t.get('builtin')=='platform_search')
        agent=ai.data.save('agents',None,dict(name='test',provider_id=provider['id'],tool_ids=[tool['id']],web_policy='off'))
        run=ai.create_run(agent['id'],'搜索GitHub',dry_run=True);state=ai.data.run(run['id'])['state']
        result=ai._tool(run,state,{'id':'test','tool_id':tool['id'],'arguments':{'platform':'github','query':'test','limit':1}})
        assert not json.loads(result)['ok']
    finally:ai.stop()


def test_transcription_without_credentials_never_downloads(monkeypatch,tmp_path):
    monkeypatch.setattr(reader.Path,'home',lambda:tmp_path)
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setattr(reader,'checked_url',lambda url,platform=None:url)
    monkeypatch.setattr(reader.subprocess,'run',lambda *a,**k:pytest.fail('No download without configured service'))
    result=json.loads(reader.execute('platform_transcribe',dict(platform='youtube',target='https://youtu.be/abcdefghijk')))
    assert not result['ok'] and '配置' in result['error']


def test_enable_script_preserves_memory_and_existing_configuration(tmp_path):
    import importlib.util
    from wechat_panel.ai import AIService
    from wechat_panel.store import Store
    path=tmp_path/'panel.sqlite3';ai=AIService(Store(path))
    try:
        p=ai.data.save('providers',None,dict(name='test',model='test',base_url='http://localhost:9999'))
        a=ai.data.save('agents',None,dict(name='Aria',provider_id=p['id'],prompt='保留角色提示词',companion={'memory':{'enabled':True}}))
        contact=ai.store.add_contact(dict(lookup='客户',nickname='客户',wechat_id='test',account_id='self'))
        ai.store.update_contact(contact['id'],ai_agent_id=a['id'],listening=True,mode='ai_auto')
        ai.store.ingest(contact['id'],[dict(key='history',direction='incoming',kind='text',text='旧消息')])
        boundary=ai.store.contact(contact['id'])['ai_context_start_message']
        scope=ai.companion.memory.scope(a['id'],session_id='test')
        ai.companion.memory.profile(scope,a['id'])
        ai.companion.memory.save_memory(scope,{'key':'宠物','content':'猫叫团子'})
        spec=importlib.util.spec_from_file_location('enable_platform_tools',Path(__file__).resolve().parents[1]/'scripts/enable-platform-tools.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        first=module.enable(path,'Aria');second=module.enable(path,'Aria')
        assert first['memory_unchanged'] and second['memory_unchanged']
        saved=ai.data.get('agents',a['id'])
        assert saved['prompt']==a['prompt'] and saved['companion']==a['companion']
        assert len(saved['tool_ids'])==len(BUILTINS)
        assert len(saved['skill_ids'])==1
        assert ai.companion.memory.list_memories(scope)[0]['content']=='猫叫团子'
        assert ai.store.contact(contact['id'])['ai_context_start_message']==boundary
    finally:ai.stop()


@pytest.mark.parametrize('data',[{'status':'error','message':'Login required'},
    {'status':'blocked','content':'Please sign in'},[{'ok':False,'content':'Authentication required'}],'   '])
def test_failure_envelopes_are_not_successful_content(data):
    from wechat_panel.web_reader import ReadUnavailable
    with pytest.raises(ReadUnavailable):reader.require_content(data)


def test_transcript_ignores_failure_envelopes_but_preserves_real_segments():
    assert reader.transcript_text([{'status':'error','content':'Login required'}])==''
    assert reader.transcript_text([{'error':'blocked','content':'Please verify'},
        {'text':'This speaker explains CAPTCHA and access denied errors.'}])=='This speaker explains CAPTCHA and access denied errors.'
