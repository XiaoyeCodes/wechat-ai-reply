"""Read public pages and platform subtitles without running model shell text."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit, urljoin

import httpx


class ReadUnavailable(RuntimeError):
    pass


def cli_json(name,args,timeout=40):
    executable=shutil.which(name)
    if not executable:raise ReadUnavailable(f'未安装 {name} 读取组件')
    if name in ('opencli','mcporter'):
        package='@jackwener/opencli/dist/src/main.js' if name=='opencli' else 'mcporter/dist/cli.js'
        script=Path(executable).parent/'node_modules'/package
        node=shutil.which('node')
        if not node or not script.is_file():raise ReadUnavailable(f'{name} 组件路径不可用')
        command=[node,str(script),*args]
    else:command=[executable,*args]
    try:
        result=subprocess.run(command,capture_output=True,timeout=timeout,encoding='utf-8',errors='replace',
            env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'},
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.TimeoutExpired):raise ReadUnavailable(f'{name} 读取超时或不可用') from None
    if result.returncode or len(result.stdout)>2_000_000:raise ReadUnavailable(f'{name} 未能读取内容，请检查组件和登录状态')
    try:return json.loads(result.stdout)
    except ValueError:
        # bili defaults to YAML; safe_load never executes object constructors.
        import yaml
        try:return yaml.safe_load(result.stdout)
        except yaml.YAMLError:raise ReadUnavailable(f'{name} 返回格式无效') from None


def bilibili(url):
    match=re.search(r'(BV[a-zA-Z0-9]{10})(?:[/?#]|$)',url)
    if not match:raise ReadUnavailable('请提供包含 BV 号的完整 B 站视频链接')
    bvid=match[1];source='https://www.bilibili.com/video/'+bvid
    result={'url':source,'kind':'video','title':'','description':'','transcript':'','coverage':'metadata_only'}
    try:
        raw=cli_json('bili',['video',bvid],20)
        if raw.get('ok'):
            data=raw.get('data',{});video=data.get('video',{})
            result.update(title=video.get('title',''),description=video.get('description',''),duration_seconds=video.get('duration_seconds'))
            result['transcript']=data.get('subtitle',{}).get('text','') or ''
    except ReadUnavailable:pass
    if not result['transcript']:
        try:
            subs=cli_json('opencli',['bilibili','subtitle',bvid,'-f','json','--window','background'],45)
            if isinstance(subs,list):result['transcript']='\n'.join(str(s.get('content','')) for s in subs if isinstance(s,dict))[:30000]
        except ReadUnavailable:
            # Older Browser Bridge versions can accept named browser sessions
            # while rejecting the CLI's adapter session pre-navigation.
            try:
                exe=shutil.which('opencli');node=shutil.which('node')
                if not exe or not node:raise ReadUnavailable('未安装浏览器读取组件')
                root=Path(exe).parent/'node_modules/@jackwener/opencli'
                run=subprocess.run([node,str(Path(__file__).with_name('bilibili_reader.mjs')),str(root),bvid],
                    capture_output=True,timeout=50,encoding='utf-8',errors='replace',
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if run.returncode==0 and len(run.stdout)<2_000_000:
                    subs=json.loads(run.stdout)
                    if isinstance(subs,list):result['transcript']='\n'.join(str(s.get('content','')) for s in subs if isinstance(s,dict))[:30000]
            except (ReadUnavailable,OSError,subprocess.TimeoutExpired,ValueError):pass
    if result['transcript'].strip():result['coverage']='subtitles';result['note']='根据字幕总结，不声称看过未提供的视频画面。'
    elif result['title']:result['note']='只获取到标题和简介，未取得字幕；不能据此编造视频全文。需要用户提供字幕或可用的音频转写服务。'
    else:raise ReadUnavailable('B 站视频信息与字幕读取失败，请检查浏览器连接或提供字幕')
    return result


def read_page(url, platform_routing=True):
    from .companion_services import public_url
    public_url(url)
    host=(urlsplit(url).hostname or '').lower()
    if platform_routing:
        from .platform_reader import platform_for_url, execute
        platform=platform_for_url(url)
        if platform and platform!='bilibili':
            return json.loads(execute('platform_read',{'platform':platform,'target':url}))
    if host=='b23.tv':
        try:
            with httpx.Client(timeout=15,follow_redirects=False) as client:r=client.get(url)
            target=urljoin(url,r.headers.get('location',''))
            if (urlsplit(target).hostname or '') not in ('www.bilibili.com','bilibili.com'):raise ValueError()
            return bilibili(target)
        except (httpx.HTTPError,ValueError):raise ReadUnavailable('B 站短链接无法解析，请发送完整视频链接') from None
    if host in ('www.bilibili.com','bilibili.com') and '/video/' in urlsplit(url).path:return bilibili(url)
    # Jina is preferred; public pages have a direct HTML fallback. Each redirect
    # is independently validated, never fetching a private/local address.
    try:
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            r=client.get('https://r.jina.ai/'+url)
            if r.status_code==200 and r.text.strip() and len(r.content)<2_000_000:
                return {'url':url,'kind':'web','coverage':'page','content':r.text[:24000]}
    except httpx.HTTPError:pass
    try:
        target=url
        with httpx.Client(timeout=20,follow_redirects=False,headers={'User-Agent':'Mozilla/5.0 RelayReader/1.0'}) as client:
            for _ in range(4):
                public_url(target)
                with client.stream('GET',target) as r:
                    if r.status_code in (301,302,303,307,308):target=urljoin(target,r.headers.get('location',''));continue
                    if r.status_code!=200:raise ReadUnavailable(f'网页读取返回 HTTP {r.status_code}；不能绕过登录或权限限制')
                    content=bytearray()
                    for chunk in r.iter_bytes():
                        content.extend(chunk)
                        if len(content)>2_000_000:raise ReadUnavailable('网页过大，请提供具体文章链接')
                    charset=r.encoding or 'utf-8';mime=r.headers.get('content-type','')
                text=content.decode(charset,errors='replace')
                if 'html' in mime:
                    from bs4 import BeautifulSoup
                    soup=BeautifulSoup(text,'html.parser')
                    for el in soup(['script','style','nav','footer','header','noscript']):el.decompose()
                    # AIHot /hot is a ranking with ten article/li cards. Taking
                    # its first article silently drops nine titles and heats.
                    ranking=host=='aihot.news' and urlsplit(target).path.rstrip('/')=='/hot'
                    body=(soup.find('main') or soup) if ranking else (soup.find('article') or soup.find('main') or soup)
                    text=body.get_text('\n',strip=True)
                elif not any(m in mime for m in ('text/','application/json')):raise ReadUnavailable('该链接不是可读取的网页正文')
                if len(text.strip())<30:raise ReadUnavailable('网页正文为空或需要登录，无法据此总结')
                return {'url':target,'kind':'web','coverage':'page','content':text[:24000]}
    except httpx.HTTPError:raise ReadUnavailable('网页连接失败，请稍后重试或粘贴正文') from None
    raise ReadUnavailable('网页跳转过多，未取得正文')
