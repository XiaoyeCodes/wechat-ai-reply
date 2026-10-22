"""Verify preserved article, displayed quotes, alignment and clickable PDF links."""
import argparse
import re
from pathlib import Path

import pdfplumber
from pypdf import PdfReader
from workflow import KEYS, read_json, write_json, article_text


def norm(text):
    return re.sub(r'\s+', '', text)


def verify(issue, markets, pdf):
    reader = PdfReader(str(pdf))
    if len(reader.pages) < 2:
        raise ValueError('Report has no article pages')
    body = norm(article_text(reader))
    title = re.sub(r'^金十数据\s*', '', issue['title'])
    cursor = 0
    for text in [title, issue['subtitle']] + [b.get('text', '') for b in issue['blocks']]:
        if text.strip():
            index = body.find(norm(text), cursor)
            if index < 0:
                raise ValueError('Article text missing or reordered')
            cursor = index + len(norm(text))
    if sum(len(p.images) for p in reader.pages) != 0:
        raise ValueError('Unexpected source images in text-only newspaper')
    cover = reader.pages[0].extract_text() or ''
    for hidden in ('盎司', '/桶', 'BTC/USD', '24h', 'UTC日内', '近月连续', ' bp', 'Coinbase', '东方财富', '新浪财经'):
        if hidden in cover:
            raise ValueError('Hidden cover label returned: ' + hidden)
    rows = markets['rows']
    if len(rows) != 9 or {r['key'] for r in rows} != set(KEYS):
        raise ValueError('Wrong market instruments')
    annotations = [a.get_object() for a in reader.pages[0].get('/Annots', [])]
    with pdfplumber.open(str(pdf)) as document:
        words = document.pages[0].extract_words()
    anchors = []
    for i, row in enumerate(rows):
        if norm(row['name']) not in norm(cover):
            raise ValueError('Missing asset: ' + row['key'])
        price = row.get('price')
        value = '待核实' if price is None else (f'{price:.4f}%' if row['key'] == 'us10y' else ('$' if row['unit'].startswith('美元') else '') + f'{price:,.2f}')
        change = '--' if price is None else f"{row['change_pct']:+.2f}%"
        row_top = 841.89 - (406 - i * 29)
        row_words = [w for w in words if row_top <= w['top'] < row_top + 29]
        v = next((w for w in row_words if w['text'] == value and abs(w['x0'] - 281) < .1), None)
        c = next((w for w in row_words if w['text'] == change and abs((w['x0'] + w['x1']) / 2 - 520) < .1), None)
        if not v or not c:
            raise ValueError('Value/change or alignment mismatch: ' + row['key'])
        if row['key'] == 'nasdaq':
            anchors.append((v['x0'] + v['x1']) / 2)
        urls = [a.get('/A', {}).get('/URI') for a in annotations if abs(float(a['/Rect'][1]) - (406 - (i + 1) * 29)) < .1]
        if row['source_url'] not in urls:
            raise ValueError('Market hyperlink missing: ' + row['key'])
        if row['key'] == 'hsi' and 'hk/zsHSI.html' in row['source_url']:
            raise ValueError('Obsolete HSI hyperlink')
    header = next(w for w in words if w['text'] == '最新值')
    if abs((header['x0'] + header['x1']) / 2 - anchors[0]) > .1:
        raise ValueError('Latest-value header is not centered over Nasdaq value')
    # No quote-row timestamps or a fourth column may appear.
    if any(w['x0'] > 340 and re.fullmatch(r'\d{2}:\d{2}', w['text']) and 435 < w['top'] < 700 for w in words):
        raise ValueError('Per-row time column returned')
    return {'passed': True, 'pages': len(reader.pages), 'market_rows': 9,
            'text_order_preserved': True, 'source_images': 0, 'quote_links': 9,
            'layout': 'nasdaq-centered-header-v8'}


if __name__ == '__main__':
    import json
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('issue', 'markets', 'pdf', 'output'):
        ap.add_argument('--' + name, required=True, type=Path)
    args = ap.parse_args()
    result = verify(read_json(args.issue), read_json(args.markets), args.pdf)
    write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
