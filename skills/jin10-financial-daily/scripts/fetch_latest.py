"""Fetch the configured breakfast source and preserve its original provenance."""
import argparse
import os
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(os.environ.get('JIN10_DAILY_WORKSPACE', Path.cwd())).resolve()
TOPIC = 'https://xnews.jin10.com/topic/343'
CST = timezone(timedelta(hours=8))
CONTENT_POLICY = 'news-text-only-v2'


def remove_unwanted_media(article):
    """User excludes source banners, market/calendar pictures and audio entries."""
    counts = {'images':len(article.select('img')), 'audio_entries':0}
    audio_nodes = list(article.select('p.insert-audio'))
    for a in article.select('a[href]'):
        if re.search(r'\.(mp3|m4a|wav|ogg)(?:[?#]|$)',a.get('href',''),re.I):
            container = a.find_parent('p') or a
            if container not in audio_nodes:
                audio_nodes.append(container)
    for node in audio_nodes:
        if node.parent is not None:
            counts['audio_entries'] += 1
            node.decompose()
    for node in list(article.select('figure:has(img), img, video, audio')):
        if node.parent is not None:
            node.decompose()
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='Optional expected edition date, YYYY-MM-DD')
    args = ap.parse_args()
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': TOPIC})
    session.mount('https://', HTTPAdapter(max_retries=Retry(total=2, connect=2, read=1, backoff_factor=.3, status_forcelist=[429,500,502,503,504], allowed_methods=['GET'])))
    def get(url):
        response = session.get(url, timeout=(10, 35))
        response.raise_for_status()
        return response

    topic_response = get(TOPIC)
    topic = BeautifulSoup(topic_response.content, 'html.parser')
    candidates = []
    for a in topic.select('a[href]'):
        label = a.get_text(' ', strip=True)
        match = re.search(r'金十数据全球财经早餐\s*[|｜]\s*(\d{4})年(\d{1,2})月(\d{1,2})日', label)
        url = urljoin(TOPIC, a['href'])
        if match and urlparse(url).hostname == 'xnews.jin10.com' and re.fullmatch(r'/details/\d+', urlparse(url).path):
            date = datetime(*map(int, match.groups())).date().isoformat()
            candidates.append((date, url))
    if not candidates:
        raise RuntimeError('No dated breakfast articles found; source layout may have changed')
    date, url = max(candidates)
    if date > datetime.now(CST).date().isoformat():
        raise RuntimeError('Source edition date is in the future')
    if args.date and date != args.date:
        raise RuntimeError(f'Expected {args.date}; latest source edition is {date}. Do not relabel old news.')
    response = get(url)
    soup = BeautifulSoup(response.content, 'html.parser')
    article = soup.select_one('.jin10-news-cdetails-article')
    title_node = soup.select_one('.jin10-news-cdetails-title')
    if not article or not title_node or len(article.get_text(strip=True)) < 500:
        raise RuntimeError('Article body missing or suspiciously short')
    title = title_node.get_text(strip=True)
    if not all(str(int(x)) in title for x in date.split('-')):
        raise RuntimeError('Article title date disagrees with topic list')
    excluded = remove_unwanted_media(article)
    issue_dir = ROOT / 'source' / date
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / 'page.html').write_bytes(response.content)
    (issue_dir / 'topic.html').write_bytes(topic_response.content)
    (issue_dir / 'article.html').write_text(str(article), encoding='utf-8')
    description = soup.select_one('meta[name="description"]')
    subtitle = description.get('content', '') if description else ''
    blocks = []
    asset_index = 0
    def walk(node):
        nonlocal asset_index
        if isinstance(node, NavigableString):
            if node.strip():
                blocks.append({'type':'p','text':str(node),'html':str(node)})
            return
        if not isinstance(node, Tag):
            return
        if node.name in ('script','style'):
            raise RuntimeError('Unexpected active content inside article; inspect before rendering')
        if node.name == 'img':
            raise RuntimeError('Source image survived the text-only filter')
        elif node.name in ('p','h1','h2','h3','h4','li','figcaption'):
            text = node.get_text('', strip=False)
            if node.find('img'):
                # Uncommon mixed image/text paragraphs must be handled without dropping either.
                for child in node.children:
                    walk(child)
            elif text.strip():
                blocks.append({'type':'heading' if node.name.startswith('h') else 'p','text':text,'html':node.decode_contents(),'links':[{'text':a.get_text(),'url':urljoin(url,a['href'])} for a in node.select('a[href]')], 'audio':bool(node.select('video,audio'))})
        else:
            for child in node.children:
                walk(child)
    for child in article.children:
        walk(child)
    norm = lambda s: re.sub(r'\s+', '', s)
    original_text = article.get_text('',strip=False)
    rendered_text = ''.join(b.get('text','') for b in blocks)
    if norm(original_text) != norm(rendered_text):
        raise RuntimeError('Source block extraction changed article text')
    if len(article.select('img')) != sum(b['type']=='image' for b in blocks):
        raise RuntimeError('Image preservation check failed')
    headings = [b['text'] for b in blocks if b['type']=='heading']
    required = {'今日优选','市场盘点','国际要闻','国内要闻','风险预警'}
    if not required.issubset(set(headings)):
        raise RuntimeError(f'Sections missing: {required-set(headings)}')
    raw = title+'\n\n'+subtitle+'\n\n'+'\n\n'.join(b['text'] for b in blocks if 'text' in b)
    fingerprint = hashlib.sha256((CONTENT_POLICY+title+subtitle+original_text+'|'.join(b.get('url','') for b in blocks)).encode()).hexdigest()
    result = {'date':date,'title':title,'subtitle':subtitle,'url':url,'topic_url':TOPIC,'retrieved_at':datetime.now(CST).isoformat(),'content_policy':CONTENT_POLICY,'excluded_source_media':excluded,'blocks':blocks,'body_characters':len(original_text),'text_integrity':True,'original_image_count':asset_index,'audio_link_count':len(article.select('a[href]')),'headings':headings,'sha256':fingerprint}
    (issue_dir / 'issue.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    input_dir = ROOT / 'input'
    input_dir.mkdir(exist_ok=True)
    (input_dir / f'{date}.txt').write_text(raw,encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('blocks','authorization','subtitle')},ensure_ascii=False))


if __name__ == '__main__':
    main()
