"""Bounded read-only adapters for Agent Reach's supported platforms."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import httpx

from .platform_catalog import PLATFORMS
from .web_reader import ReadUnavailable, cli_json

DOMAINS = {
    'bilibili': ('bilibili.com', 'b23.tv'), 'youtube': ('youtube.com', 'youtu.be'),
    'github': ('github.com',), 'twitter': ('x.com', 'twitter.com'), 'reddit': ('reddit.com', 'redd.it'),
    'xiaohongshu': ('xiaohongshu.com', 'xhslink.com'), 'facebook': ('facebook.com',),
    'instagram': ('instagram.com',), 'linkedin': ('linkedin.com',), 'boss': ('zhipin.com',),
    'xueqiu': ('xueqiu.com',), 'v2ex': ('v2ex.com',), 'xiaoyuzhou': ('xiaoyuzhoufm.com',),
}
BROWSER_LOCK = threading.Lock()


def platform_for_url(url):
    host = (urlsplit(url).hostname or '').lower()
    return next((p for p, domains in DOMAINS.items()
                 if any(host == d or host.endswith('.' + d) for d in domains)), None)


def value(text, allow_empty=False):
    if not isinstance(text, str) or len(text) > 2000 or any(ord(c) < 32 for c in text):
        raise ValueError('请输入有效的查询词、公开链接或平台ID')
    text = text.strip()
    if (not text and not allow_empty) or text.startswith('-'):
        raise ValueError('查询内容不能为空或命令选项')
    return text


def checked_url(url, platform=None):
    from .companion_services import public_url
    value(url)
    if platform and platform_for_url(url) != platform:
        raise ValueError('链接域名与指定平台不符')
    return public_url(url)


def identifier(target, pattern=r'[A-Za-z0-9_.-]{1,100}'):
    target = value(target)
    if not re.fullmatch(pattern, target):raise ValueError('平台ID或用户名格式不正确')
    return target


def fetch(url, limit=2_000_000):
    """Bound each redirect and response, including feed/subtitle URLs."""
    with httpx.Client(timeout=25, follow_redirects=False, headers={'User-Agent': 'RelayReader/1.0'}) as client:
        for _ in range(4):
            checked_url(url)
            with client.stream('GET', url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('location', ''))
                    continue
                if response.status_code != 200:raise ReadUnavailable(f'来源返回 HTTP {response.status_code}，未取得内容')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:raise ReadUnavailable('来源返回内容过大')
                return bytes(data), url
    raise ReadUnavailable('来源跳转次数过多')


def require_content(data):
    if data is None or data == '' or data == [] or data == {}:
        raise ReadUnavailable('平台未返回内容；可能无结果、未登录或连接不可用')
    if isinstance(data, dict):
        if data.get('ok') is False or data.get('success') is False or data.get('error') or data.get('isError'):
            raise ReadUnavailable('平台读取未成功，请检查浏览器连接、登录和平台限制')
        for key in ('data', 'results', 'items', 'entries'):
            if key in data and data[key] in (None, [], {}, ''):
                raise ReadUnavailable('平台返回空内容，不能据此生成摘要')
    return data


def transcript_text(data):
    """A successful command/metadata envelope is not a transcript."""
    if isinstance(data, str):return data.strip()
    if isinstance(data, list):
        return '\n'.join(filter(None, (transcript_text(x) for x in data)))[:30000]
    if isinstance(data, dict):
        for key in ('transcript', 'text', 'content', 'segments', 'subtitles'):
            if key in data:
                text = transcript_text(data[key])
                if text:return text
    return ''


def browser(site, command, target='', limit=None, option=None):
    args = [site, command]
    if target:
        value(target)
        if target.startswith(('http://', 'https://')):checked_url(target, site)
        args += ([option, target] if option else [target])
    if limit is not None:args += ['--limit', str(limit)]
    args += ['-f', 'json', '--window', 'background']
    # A single browser bridge is shared with existing WeChat video readers.
    with BROWSER_LOCK:
        try:
            if site == 'xiaohongshu' and command == 'search':
                executable, node = shutil.which('opencli'), shutil.which('node')
                if not executable or not node:raise ReadUnavailable('缺少OpenCLI浏览器组件')
                root = Path(executable).parent / 'node_modules/@jackwener/opencli'
                result = subprocess.run([node, str(Path(__file__).with_name('xiaohongshu_reader.mjs')), str(root), target, str(limit or 5)],
                                        capture_output=True, timeout=60, encoding='utf-8', errors='replace',
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode or len(result.stdout) > 2_000_000:
                    raise ReadUnavailable('小红书未取得搜索内容，请检查现有Chrome登录、浏览器桥接或页面验证')
                return require_content(json.loads(result.stdout))
            return require_content(cli_json('opencli', args, 70))
        finally:time.sleep(2)


def github_read(target):
    if target.startswith(('http://', 'https://')):
        checked_url(target, 'github'); parts = urlsplit(target).path.strip('/').split('/')
    else:parts = target.split('/')
    if len(parts) < 2:raise ValueError('请提供 GitHub owner/repo 或仓库链接')
    repo = identifier('/'.join(parts[:2]), r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
    if len(parts) == 2:
        info = cli_json('gh', ['repo', 'view', repo, '--json', 'nameWithOwner,description,url,homepageUrl,stargazerCount,primaryLanguage'])
        readme = cli_json('gh', ['api', f'repos/{repo}/readme'])
        import base64
        info['readme'] = base64.b64decode(readme.get('content', '')).decode('utf-8', errors='replace')[:24000]
        return info
    if len(parts) == 4 and parts[2] in ('issues', 'pull'):
        number = identifier(parts[3], r'\d{1,12}')
        kind = 'issue' if parts[2] == 'issues' else 'pr'
        return cli_json('gh', [kind, 'view', number, '--repo', repo, '--json', 'title,body,url,state,comments'])
    from .web_reader import read_page
    return read_page('https://github.com/' + '/'.join(parts), platform_routing=False)


def youtube_url(target):
    checked_url(target, 'youtube')
    parsed = urlsplit(target)
    video_id = parsed.path.strip('/') if parsed.hostname == 'youtu.be' else parse_qs(parsed.query).get('v', [''])[0]
    if not video_id and parsed.path.startswith(('/shorts/', '/embed/')):video_id = parsed.path.split('/')[2]
    return 'https://www.youtube.com/watch?v=' + identifier(video_id, r'[A-Za-z0-9_-]{11}')


def youtube_read(target):
    url = youtube_url(target)
    exe = shutil.which('yt-dlp')
    metadata = {}
    if exe:
        with tempfile.TemporaryDirectory(prefix='relay-youtube-') as folder:
            args = [exe, '--ignore-config', '--no-playlist', '--skip-download', '--write-info-json',
                    '--write-subs', '--write-auto-subs', '--sub-langs', 'zh-Hans,zh,en', '--sub-format', 'json3',
                    '--socket-timeout', '15', '--retries', '1', '--paths', folder, '-o', '%(id)s.%(ext)s', url]
            try:
                subprocess.run(args, capture_output=True, timeout=100, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                for path in Path(folder).glob('*.info.json'):
                    if path.stat().st_size < 2_000_000:
                        raw = json.loads(path.read_text('utf-8'))
                        metadata = {k: raw.get(k) for k in ('title', 'description', 'duration')}
                for path in sorted(Path(folder).glob('*.json3'), key=lambda p: '.zh' not in p.name):
                    if path.stat().st_size > 2_000_000:continue
                    raw = json.loads(path.read_text('utf-8'))
                    lines = []
                    for event in raw.get('events', []):
                        text = ''.join(s.get('utf8', '') for s in event.get('segs', [])).strip()
                        if text and (not lines or text != lines[-1]):lines.append(text)
                    transcript = '\n'.join(lines)[:30000]
                    if transcript:return dict(url=url, **metadata, transcript=transcript, coverage='subtitles')
            except (OSError, subprocess.TimeoutExpired, ValueError):pass
    try:
        data = browser('youtube', 'transcript', url)
        text = transcript_text(data)
        if not text:raise ReadUnavailable('YouTube未返回非空字幕')
        return dict(url=url, **metadata, transcript=text, coverage='subtitles')
    except ReadUnavailable:
        if metadata.get('title'):
            return dict(url=url, **metadata, coverage='metadata_only', note='未取得字幕；不能把简介当作视频全文。')
        raise ReadUnavailable('YouTube字幕读取失败，请检查网络、浏览器连接或提供字幕') from None


def rss_read(target):
    import feedparser
    raw, url = fetch(checked_url(target))
    feed = feedparser.parse(raw)
    entries = [{'title': x.get('title', ''), 'url': x.get('link', ''), 'published': x.get('published', ''),
                'summary': x.get('summary', '')[:3000]} for x in feed.entries[:10]]
    require_content(entries)
    return dict(url=url, title=feed.feed.get('title', ''), entries=entries, note='本次仅阅读订阅源，未创建持续订阅。')


def podcast_read(eid):
    url = 'https://www.xiaoyuzhoufm.com/episode/' + eid
    try:
        with tempfile.TemporaryDirectory(prefix='relay-podcast-') as folder:
            data = cli_json('opencli', ['xiaoyuzhou', 'transcript', eid, '--output', folder, '-f', 'json'], 80)
            require_content(data)
            transcript = Path(folder) / eid / 'transcript.txt'
            if transcript.is_file() and transcript.stat().st_size < 2_000_000:
                text = transcript.read_text('utf-8').strip()[:30000]
                if text:return dict(url=url, transcript=text, coverage='transcript')
    except (ReadUnavailable, OSError):pass
    try:data = cli_json('opencli', ['xiaoyuzhou', 'episode', eid, '-f', 'json'], 40)
    except ReadUnavailable:
        from .web_reader import read_page
        data = read_page(url, platform_routing=False)
    require_content(data)
    return dict(url=url, data=data, coverage='metadata_only',
                note='仅取得单集简介；未取得全文转写，不能声称听完播客。')


def transcribe(platform, target):
    if platform not in ('youtube','xiaoyuzhou'):raise ValueError('转写仅支持YouTube与小宇宙')
    url = youtube_url(target) if platform == 'youtube' else checked_url(target, platform)
    # Inspect only explicitly configured transcription keys; never read browser cookies.
    import yaml
    config_path = Path.home()/'.agent-reach/config.yaml'
    config = yaml.safe_load(config_path.read_text('utf-8')) if config_path.is_file() else {}
    config = config or {}
    groq = bool(os.environ.get('GROQ_API_KEY') or config.get('groq_api_key'))
    openai = bool(os.environ.get('OPENAI_API_KEY') or config.get('openai_api_key'))
    if not groq and (platform == 'xiaoyuzhou' or not openai):
        raise ReadUnavailable('尚未配置音频转写服务；请在本机运行 agent-reach configure groq-key 配置，勿把密钥发到微信。')
    with tempfile.TemporaryDirectory(prefix='relay-transcription-') as folder:
        output = Path(folder)/'transcript.txt'
        if platform == 'youtube':
            executable = shutil.which('agent-reach')
            if not executable:raise ReadUnavailable('缺少Agent Reach转写组件')
            command = [executable,'transcribe',url,'-o',str(output)]
        else:
            post_id(platform,url)
            script = Path.home()/'.agent-reach/tools/xiaoyuzhou/transcribe.sh'
            git = shutil.which('git')
            bash = Path(git).parent.parent/'bin/bash.exe' if git else None
            if not bash or not bash.is_file() or not script.is_file():raise ReadUnavailable('缺少Git Bash或小宇宙转写组件')
            command = [str(bash),str(script),url,str(output)]
        result = subprocess.run(command,capture_output=True,timeout=600,
                                env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'},
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not output.is_file() or output.stat().st_size > 2_000_000:
            raise ReadUnavailable('音频转写未完成，请检查转写服务、配额及目标音频；未生成全文。')
        text = output.read_text('utf-8').strip()[:30000]
        if not text:raise ReadUnavailable('转写服务未返回正文')
        return dict(url=url,transcript=text,coverage='transcript')


def post_id(platform, target):
    if target.startswith(('http://', 'https://')):
        checked_url(target, platform)
        path = urlsplit(target).path
        patterns = {'twitter': r'/status/(\d+)', 'reddit': r'/comments/([a-z0-9]+)',
                    'v2ex': r'/t/(\d+)', 'xiaoyuzhou': r'/(?:episode|podcast)/([a-f0-9]+)',
                    'bilibili': r'/video/(BV[A-Za-z0-9]{10})'}
        match = re.search(patterns[platform], path)
        if platform == 'reddit' and urlsplit(target).hostname == 'redd.it':return identifier(path.strip('/'))
        if not match:raise ValueError('请提供完整的平台内容链接')
        target = match[1]
    patterns = {'twitter': r'\d{1,30}', 'reddit': r'[a-z0-9]{1,20}', 'v2ex': r'\d{1,15}',
                'xiaoyuzhou': r'[a-f0-9]{24}', 'bilibili': r'BV[A-Za-z0-9]{10}'}
    return identifier(target, patterns[platform])


def v2ex(action, target='', limit=5):
    root = 'https://www.v2ex.com/api/'
    paths = {'hot': 'topics/hot.json', 'user': 'members/show.json?username=' + quote(target, safe=''),
             'read': 'topics/show.json?id=' + quote(target, safe=''),
             'comments': 'replies/show.json?topic_id=' + quote(target, safe='')}
    raw, url = fetch(root + paths[action])
    data = require_content(json.loads(raw))
    return dict(url=url, data=data[:limit] if isinstance(data, list) else data)


def read(platform, target):
    value(target)
    if platform == 'youtube':return youtube_read(target)
    if platform == 'github':return github_read(target)
    if platform == 'rss':return rss_read(target)
    if platform == 'bilibili':
        from .web_reader import read_page
        if target.startswith(('http://', 'https://')):checked_url(target, platform)
        else:target = 'https://www.bilibili.com/video/' + post_id(platform, target)
        return read_page(target, platform_routing=False)
    if platform in ('web', 'exa_search'):
        from .web_reader import read_page
        return read_page(checked_url(target), platform_routing=False)
    if platform == 'v2ex':return v2ex('read', post_id(platform, target))
    if platform == 'twitter':return browser(platform, 'thread', post_id(platform, target), 5)
    if platform == 'reddit':return browser(platform, 'read', post_id(platform, target), 5)
    if platform == 'xiaohongshu':
        checked_url(target, platform)
        if 'xsec_token' not in parse_qs(urlsplit(target).query):
            raise ValueError('小红书阅读需要搜索结果中的完整链接（含 xsec_token），请先搜索笔记')
        return browser(platform, 'note', target)
    if platform == 'linkedin':
        checked_url(target, platform)
        if '/jobs/view/' in target:return browser(platform, 'job-detail', target)
        if '/company/' in target:return browser(platform, 'company', identifier(urlsplit(target).path.strip('/').split('/')[-1]))
        return browser(platform, 'profile-read', target, option='--profile-url')
    if platform in ('facebook', 'instagram'):
        if target.startswith(('http://', 'https://')):
            checked_url(target, platform)
            parts = urlsplit(target).path.strip('/').split('/')
            if len(parts) != 1:raise ValueError('此平台当前支持用户主页读取；请提供用户名或主页链接')
            target = parts[0]
        return browser(platform, 'profile', identifier(target))
    if platform == 'xueqiu':
        if target.startswith(('http://', 'https://')):
            checked_url(target, platform);target = urlsplit(target).path.strip('/').split('/')[-1]
        return browser(platform, 'stock', identifier(target, r'[A-Za-z0-9.]{1,20}'))
    if platform == 'xiaoyuzhou':
        return podcast_read(post_id(platform, target))
    if platform == 'boss':
        if target.startswith(('http://', 'https://')):
            checked_url(target, platform)
            from .web_reader import read_page
            return read_page(target, platform_routing=False)
        return browser(platform, 'detail', identifier(target, r'[A-Za-z0-9_=-]{1,512}'))
    raise ValueError('不支持的平台')


def search(platform, query, limit, services=None, search_service_id=''):
    value(query)
    if platform in ('web', 'exa_search', 'v2ex', 'xiaoyuzhou'):
        if services is None:raise ReadUnavailable('未配置联网搜索服务')
        suffix = {'v2ex': ' site:v2ex.com', 'xiaoyuzhou': ' site:xiaoyuzhoufm.com'}.get(platform, '')
        return dict(backend='web_search', note='全网搜索结果，非平台内部检索',
                    data=json.loads(services.search(search_service_id, query + suffix, limit)))
    if platform == 'bilibili':return cli_json('bili', ['search', query, '--type', 'video', '-n', str(limit)])
    if platform == 'github':return cli_json('gh', ['search', 'repos', query, '--limit', str(limit), '--json', 'fullName,description,url,stargazersCount'])
    if platform == 'youtube':
        try:
            return cli_json('yt-dlp', ['--ignore-config', '--flat-playlist', '--dump-single-json', '--skip-download',
                                      '--socket-timeout', '15', '--retries', '1', f'ytsearch{limit}:{query}'], 70)
        except ReadUnavailable:return browser(platform, 'search', query, limit)
    if platform == 'boss':
        # The installed OpenCLI adapter reuses the user's existing Chrome,
        # without launching a separate remote-debugging browser or importing cookies.
        return browser(platform, 'search', query, limit)
    if platform == 'rss':raise ValueError('RSS请提供订阅源URL并使用 platform_read')
    return browser(platform, 'search', query, limit)


BROWSE = {
    'twitter': {'hot': ('trending', False, True), 'feed': ('timeline', False, True), 'user': ('tweets', True, True), 'comments': ('thread', True, True)},
    'reddit': {'hot': ('hot', False, True), 'feed': ('popular', False, True), 'user': ('user-posts', True, True), 'comments': ('read', True, True)},
    'xiaohongshu': {'feed': ('feed', False, True), 'user': ('user', True, True), 'comments': ('comments', True, True)},
    'facebook': {'feed': ('feed', False, True), 'user': ('profile', True, False)},
    'instagram': {'hot': ('explore', False, True), 'user': ('user', True, True)},
    'linkedin': {'company': ('company', True, False)},
    'xueqiu': {'hot': ('hot', False, True), 'hot_stock': ('hot-stock', False, True), 'stock': ('stock', True, False), 'comments': ('comments', True, True)},
    'youtube': {'comments': ('comments', True, True)},
    'bilibili': {'comments': ('comments', True, True), 'user': ('feed', True, True)},
    'xiaoyuzhou': {'podcast': ('podcast-episodes', True, True)},
}


def browse(platform, action, target, limit):
    value(target, allow_empty=True)
    if platform == 'bilibili' and action == 'hot':return cli_json('bili', ['hot', '-n', str(limit)])
    if platform == 'v2ex' and action in ('hot', 'comments', 'user'):
        return v2ex(action, post_id(platform, target) if action == 'comments' else identifier(target) if action == 'user' else '', limit)
    if platform == 'github' and action == 'user':
        return cli_json('gh', ['repo', 'list', identifier(target), '--visibility', 'public', '--limit', str(limit), '--json', 'nameWithOwner,description,url'])
    if platform == 'linkedin' and action == 'user':return read(platform, target)
    spec = BROWSE.get(platform, {}).get(action)
    if spec is None:raise ValueError('该平台不支持此浏览操作，请使用技能中列出的操作')
    command, needs_target, has_limit = spec
    if needs_target:
        if platform in ('twitter', 'reddit', 'bilibili') and action == 'comments':target = post_id(platform, target)
        elif platform == 'youtube':target = youtube_url(target)
        elif platform == 'xiaoyuzhou':target = post_id(platform, target)
        elif platform == 'xiaohongshu' and action == 'comments' and target.startswith('http'):
            checked_url(target, platform);target = urlsplit(target).path.strip('/').split('/')[-1]
        else:target = identifier(target.lstrip('@'))
    return browser(platform, command, target if needs_target else '', limit if has_limit else None)


def execute(name, args, services=None, search_service_id=''):
    platform = args.get('platform')
    if platform not in PLATFORMS:raise ValueError('未知平台')
    limit = args.get('limit', 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:raise ValueError('条数应为1到10')
    try:
        if name == 'platform_search':data = search(platform, args['query'], limit, services, search_service_id)
        elif name == 'platform_read':data = read(platform, args['target'])
        elif name == 'platform_browse':data = browse(platform, args['action'], args['target'], limit)
        elif name == 'platform_transcribe':data = transcribe(platform, args['target'])
        else:raise ValueError('未知平台工具')
        require_content(data)
        result = dict(ok=True, platform=platform, coverage=data.get('coverage', 'content') if isinstance(data, dict) else 'content', data=data)
        # Preserve valid JSON and metadata even when an upstream payload is large.
        encoded = json.dumps(result, ensure_ascii=False)
        if len(encoded) > 35000:
            result['data'] = json.dumps(data, ensure_ascii=False)[:30000]
            result['truncated'] = True
        return json.dumps(result, ensure_ascii=False)
    except (ReadUnavailable, httpx.HTTPError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if isinstance(exc, (ReadUnavailable, ValueError)) else '平台连接失败，请检查网络和组件'
        return json.dumps(dict(ok=False, platform=platform, error=message[:500],
                              instruction='本次未取得可靠内容；不要伪造结果，不要自动登录或反复重试验证码/风控。'), ensure_ascii=False)
