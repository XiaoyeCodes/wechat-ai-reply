"""Lay out authorized full text without summarizing, rewriting, or reordering it."""
import argparse
import os
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer,
    PageBreak, NextPageTemplate, Image,
)
from pypdf import PdfReader
from workflow import article_text, CST

ROOT = Path(os.environ.get('JIN10_DAILY_WORKSPACE', Path.cwd())).resolve()
PAPER = colors.HexColor('#F4F1EA')
INK = colors.HexColor('#252820')
MUTED = colors.HexColor('#77796D')
ACCENT = colors.HexColor('#AC624D')
RULE = colors.HexColor('#D4D0C4')
W, H = 595.276, 841.89

pdfmetrics.registerFont(TTFont('Song', os.environ.get('JIN10_SONG_FONT', 'C:/Windows/Fonts/simsun.ttc'), subfontIndex=0))
pdfmetrics.registerFont(TTFont('Hei', os.environ.get('JIN10_HEI_FONT', 'C:/Windows/Fonts/simhei.ttf')))
pdfmetrics.registerFontFamily('Song', normal='Song', bold='Hei', italic='Song', boldItalic='Hei')


def english(c, text, x, y, size=8, color=MUTED, spacing=1.6):
    t = c.beginText(x, y)
    t.setFont('Helvetica', size)
    t.setFillColor(color)
    t.setCharSpace(spacing)
    t.textOut(text)
    c.drawText(t)


def chinese(c, text, x, y, size=12, font='Song', color=INK):
    c.setFillColor(color)
    c.setFont(font, size)
    c.drawString(x, y, text)


def motif(c):
    """Original vector drawing: a globe, capital-flow curves and a skyline."""
    c.saveState()
    c.translate(323, 566)
    c.setStrokeColor(INK)
    c.setLineWidth(.65)
    c.circle(75, 85, 81, stroke=1, fill=0)
    for radius in (26, 53):
        c.ellipse(75-radius, 4, 75+radius, 166, stroke=1, fill=0)
    for offset in (-43, -22, 0, 22, 43):
        width = math.sqrt(81**2-offset**2)
        c.line(75-width, 85+offset, 75+width, 85+offset)
    c.setFillColor(PAPER)
    c.rect(-26, -24, 205, 58, fill=1, stroke=0)
    c.setStrokeColor(INK)
    heights = (28, 47, 35, 70, 54, 94, 116)
    for i, height in enumerate(heights):
        x = -10+i*23
        c.rect(x, -4, 13, height, fill=0, stroke=1)
        for j in range(1, int(height/8)):
            c.line(x+3, -4+j*8, x+10, -4+j*8)
    for j in range(6):
        p = c.beginPath()
        p.moveTo(-54, 16+j*5)
        p.curveTo(0, 10+j*4, 45, 66+j*5, 98, 72+j*5)
        p.curveTo(132, 76+j*5, 151, 63+j*4, 183, 96+j*5)
        c.setStrokeColor(ACCENT if j == 5 else INK)
        c.drawPath(p)
    c.restoreState()


def cover(c, doc, date, preview, markets=None):
    c.setFillColor(PAPER)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    english(c, 'FINANCIAL', 46, 792, 10)
    english(c, 'MORNING EDITION', 46, 775, 8)
    c.setStrokeColor(INK)
    c.setLineWidth(.7)
    c.line(46, 757, W-46, 757)
    chinese(c, '每日财经', 43, 665, 48)
    chinese(c, '阅读世界，理解市场。', 47, 630, 13, color=MUTED)
    motif(c)
    c.setStrokeColor(RULE)
    c.line(46, 518, W-46, 518)
    english(c, date.strftime('%Y / %m / %d'), 47, 492, 12, INK, 1.3)
    collected = datetime.fromisoformat(markets['generated_at']).astimezone(CST).strftime('%H:%M') if markets else '--:--'
    chinese(c, '北京时间 '+collected, 420, 493, 10, color=MUTED)
    market_table(c,markets)
    c.setStrokeColor(INK)
    c.line(46, 100, W-46, 100)
    if preview:
        chinese(c, '版式样张 · 正文待提供', 47, 78, 9, color=MUTED)
    english(c, 'DESIGN STUDY / 01' if preview else 'FULL TEXT EDITION', 47, 54, 7)


def centered_cell(c,lines,x,center,align='left'):
    """Center the actual font bounds of a one- or two-line cell vertically."""
    extents=[]
    for i,line in enumerate(lines):
        for text,font,size,color in line:
            ascent,descent=pdfmetrics.getAscentDescent(font,size)
            extents.append((-i*11+ascent,-i*11+descent))
    shift=center-(max(a for a,b in extents)+min(b for a,b in extents))/2
    for i,line in enumerate(lines):
        width=sum(pdfmetrics.stringWidth(text,font,size) for text,font,size,color in line)
        cursor=x-width if align=='right' else x-width/2 if align=='center' else x
        for text,font,size,color in line:
            c.setFont(font,size)
            c.setFillColor(color)
            t=c.beginText(cursor,shift-i*11)
            t.setCharSpace(0)
            t.textOut(text)
            c.drawText(t)
            cursor+=pdfmetrics.stringWidth(text,font,size)


def market_table(c,markets):
    chinese(c,'市场行情',46,451,20)
    english(c,'MARKET WATCH',391,455,8,ACCENT,1.2)
    if markets:
        at=datetime.fromisoformat(markets['generated_at']).strftime('%m-%d %H:%M')
        chinese(c,'采集 '+at+' · 北京时间',46,432,8,color=MUTED)
    rows=markets['rows'] if markets else [dict(name=s,price=None,status='missing') for s in ['纳斯达克综合','标普500','MAGS','上证指数','恒生指数','比特币现货','现货黄金','布伦特原油','10年期美债收益率']]
    if len(rows)!=9:
        raise ValueError('Market cover requires exactly nine assets')
    def value_text(row):
        price=row.get('price')
        if price is None:
            return '待核实'
        return f'{price:.4f}%' if row.get('key')=='us10y' else ('$' if row.get('unit','').startswith('美元') else '')+f'{price:,.2f}'
    price_x,change_x=281,520
    anchor_row=next((row for row in rows if row.get('key')=='nasdaq'),rows[0])
    price_width=pdfmetrics.stringWidth(value_text(anchor_row),'Song' if anchor_row.get('price') is None else 'Times-Roman',10 if anchor_row.get('price') is None else 13)
    price_header_x=price_x+price_width/2
    chinese(c,'资产 / 报价口径',46,413,8,color=MUTED)
    for x,label in [(price_header_x,'最新值'),(change_x,'日内变动')]:
        centered_cell(c,[[(label,'Song',8,MUTED)]],x,416,'center')
    c.setStrokeColor(INK)
    c.setLineWidth(.5)
    c.line(46,406,W-46,406)
    for i,row in enumerate(rows):
        top=406-i*29
        bottom=top-29
        center=(top+bottom)/2
        state=row.get('status')
        status_note=''
        if state=='cached':
            status_note='缓存'
        elif state=='older-session':
            status_note='较早交易日'
        sub=status_note
        name_lines=[[(row['name'],'Song',10,INK)]]
        if sub:
            name_lines.append([(sub,'Song',7,MUTED)])
        centered_cell(c,name_lines,46,center)
        if row.get('price') is None:
            centered_cell(c,[[('待核实','Song',10,MUTED)]],price_x,center)
            centered_cell(c,[[('--','Helvetica',10,MUTED)]],change_x,center,'center')
        else:
            price_text=value_text(row)
            price_line=[(price_text,'Times-Roman',13,INK)]
            centered_cell(c,[price_line],price_x,center)
            change=row['change_pct']
            change_color=colors.HexColor('#9A5141') if change>0 else colors.HexColor('#416653') if change<0 else MUTED
            change_lines=[[(f'{change:+.2f}%','Helvetica',10.5,change_color)]]
            centered_cell(c,change_lines,change_x,center,'center')
        if row.get('source_url'):
            c.linkURL(row['source_url'],(45,bottom,W-45,top),relative=0)
        c.setStrokeColor(RULE)
        c.setLineWidth(.35)
        c.line(46,bottom,W-46,bottom)
    chinese(c,'股票/股指/黄金比前收；原油比前结；各市场报价时点不同。',46,119,7.3,color=MUTED)
    chinese(c,'美债显示到期收益率及其相对涨跌幅。',46,107,7.3,color=MUTED)


def interior(c, doc, date, preview):
    c.setFillColor(PAPER)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    english(c, 'FINANCIAL / MORNING EDITION', 46, 804, 7, INK, 1.2)
    english(c, date.isoformat(), W-112, 804, 8, MUTED, .5)
    c.setStrokeColor(INK)
    c.setLineWidth(.5)
    c.line(46, 791, W-46, 791)
    c.setStrokeColor(RULE)
    c.line(W/2, 83, W/2, 772)
    c.line(46, 63, W-46, 63)
    chinese(c, '内页版式示意 · 非真实新闻' if preview else '原文阅读版 · 全球财经早餐', 46, 45, 8, color=MUTED)
    english(c, f'{doc.page:02}', W-66, 44, 9, INK, .6)


SAMPLE = '''今日优选
正文将在这里开始
本页仅展示字号、行距、段落与双栏阅读效果，不包含真实新闻。收到你提供的全文后，正文会按原有顺序逐段排入。
原有标题、日期、数字、标点和段落内容保持不变。内容较长时，系统自动增加页数，不以缩字或删文换取更少页面。

市场盘点
面向长文的阅读节奏
内页使用暖白纸色、宋体正文和清晰的分栏。每一栏都保留充足的行间距，适合持续阅读。
段落使用自然留白区分。重要内容的视觉层级来自原有标题，不额外生成摘要、解读或观点。

国际要闻
保留原文的叙述与次序
这里将排入你提供的对应原文。样张中的说明只是为了展示正文的长度与疏密，不会作为新闻内容推送。
条目不会为了版式而改写，也不会按主题重新拼接。原标题存在时采用标题样式，正文保持为正文。

国内要闻
让信息有秩序地呈现
较长的报道会自然延续至下一栏或下一页。页眉用于识别刊物与日期，页脚用于定位页码。
整期文件保留来源与原文入口，方便对照阅读。排版完成后，会检查文字完整性与页面显示。

风险预警
当天日期单独核对
日报使用北京时间标记日期。若尚未收到当天全文，将明确提示内容缺失，不用旧一期冒充当日内容。
每天八点是计划生成与推送的时间。正式启用前，需要确定后续全文的提供方式。
'''


def normalized(text):
    return re.sub(r'\s+', '', text)


def display_title(issue):
    # A presentation-only title change requested by the user; archived source stays intact.
    return re.sub(r'^金十数据\s*', '', issue['title'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path)
    parser.add_argument('--issue', type=Path, help='Full structured issue from fetch_latest.py')
    parser.add_argument('--markets',type=Path,help='Validated live market snapshot for cover')
    parser.add_argument('--date', default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    date = datetime.strptime(args.date, '%Y-%m-%d').date()
    if not args.preview and not args.input and not args.issue:
        parser.error('--input or --issue is required for a real issue')
    issue = json.loads(args.issue.read_text(encoding='utf-8')) if args.issue else None
    market_path=args.markets or ROOT/'source'/args.date/'markets.json'
    markets=json.loads(market_path.read_text(encoding='utf-8')) if market_path.exists() else None
    if markets and not args.markets and markets['edition_date']!=args.date:
        raise ValueError('Market snapshot edition does not match report date')
    if issue and issue['date'] != args.date:
        raise ValueError('Source issue date and output date disagree')
    text = (display_title(issue)+'\n'+issue['subtitle']+'\n'+'\n'.join(b['text'] for b in issue['blocks'] if 'text' in b)) if issue else (SAMPLE if args.preview else args.input.read_text(encoding='utf-8-sig'))
    if not text.strip():
        raise ValueError('Provided full text is empty')
    outdir = ROOT / 'output' / args.date
    outdir.mkdir(parents=True, exist_ok=True)
    suffix = 'layout-preview' if args.preview else 'financial-morning'
    output = outdir / f'{suffix}.pdf'
    doc = BaseDocTemplate(str(output), pagesize=(W, H), title='每日财经 · 版式样张' if args.preview else f'每日财经 {args.date}', author='Personal daily edition')
    gutter = 26
    colw = (W-92-gutter)/2
    intro_height = 0
    if issue and issue.get('content_policy') == 'news-text-only-v2':
        intro_height = source_heading_height(issue)
    doc.addPageTemplates([
        PageTemplate(id='cover', frames=[Frame(46, 100, W-92, 650)], onPage=lambda c,d: cover(c,d,date,args.preview,markets)),
        PageTemplate(id='body', frames=[
            Frame(46, 79, colw, 696, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
            Frame(46+colw+gutter, 79, colw, 696, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
        ], onPage=lambda c,d: interior(c,d,date,args.preview)),
        PageTemplate(id='wide', frames=[Frame(46,79,W-92,696,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=lambda c,d: wide_page(c,d,date,args.preview)),
        PageTemplate(id='first-body',frames=[
            Frame(46,79,colw,696-intro_height,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0),
            Frame(46+colw+gutter,79,colw,696-intro_height,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0),
        ],onPage=lambda c,d: first_body_page(c,d,date,args.preview,issue)),
    ])
    normal = ParagraphStyle('body', fontName='Song', fontSize=10.2, leading=17.3, textColor=INK, wordWrap='CJK', spaceAfter=7.2, allowWidows=0, allowOrphans=0)
    heading = ParagraphStyle('section', parent=normal, fontName='Hei', fontSize=17, leading=24, spaceBefore=19, spaceAfter=13, keepWithNext=True)
    known = {'今日优选','市场盘点','国际要闻','国内要闻','风险预警'}
    if issue:
        flow = rich_flow(issue, normal, heading)
    else:
        flow = [Spacer(1,1), NextPageTemplate('body'), PageBreak()]
        for line in text.splitlines():
            if not line.strip():
                flow.append(Spacer(1,5))
                continue
            flow.append(Paragraph(escape(line), heading if line.strip() in known else normal))
    doc.build(flow)
    reader = PdfReader(str(output))
    extracted = article_text(reader)
    # Each source line must survive rendering intact apart from layout whitespace.
    norm_extracted = normalized(extracted)
    missing = [line for line in text.splitlines() if line.strip() and normalized(line) not in norm_extracted]
    if missing:
        raise RuntimeError(f'Text preservation check failed: {len(missing)} lines')
    if issue:
        cursor = 0
        for block in [dict(text=display_title(issue)),dict(text=issue['subtitle'])]+issue['blocks']:
            if block.get('text'):
                needle = normalized(block['text'])
                pos = norm_extracted.find(needle,cursor)
                if pos < 0:
                    raise RuntimeError('Text order preservation check failed')
                cursor = pos+len(needle)
        pdf_image_count = sum(len(page.images) for page in reader.pages)
        if pdf_image_count != issue['original_image_count']:
            raise RuntimeError('Not all original images embedded in PDF')
        all_links = [a.get_object().get('/A',{}).get('/URI','') for page in reader.pages for a in page.get('/Annots',[])]
        for block in issue['blocks']:
            for link in block.get('links',[]):
                if link['url'] not in all_links:
                    raise RuntimeError('Original link missing from PDF')
    meta = {'date':args.date,'preview':args.preview,'pages':len(reader.pages),'input_sha256':hashlib.sha256(text.encode()).hexdigest(),'input_characters':len(text),'preserved':True,'output':str(output)}
    if markets:
        cover_text=normalized(reader.pages[0].extract_text() or '')
        if any(normalized(r['name']) not in cover_text for r in markets['rows']):
            raise RuntimeError('A requested market row is missing from the PDF cover')
        meta['markets']={'available':markets['available_count'],'total':len(markets['rows']),'generated_at':markets['generated_at'],'sources':sorted(set(r['source'] for r in markets['rows'] if r.get('source'))),'display_policy':'nasdaq-centered-header-v8'}
    if issue:
        meta.update({'source_url':issue['url'],'display_title':display_title(issue),'display_policy':'clean-titles-v1','original_images':pdf_image_count,'original_links':issue['audio_link_count'],'text_order_preserved':True,'source_sha256':issue['sha256'],'content_policy':issue.get('content_policy'),'excluded_source_media':issue.get('excluded_source_media',{})})
    (outdir / f'{suffix}.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(meta,ensure_ascii=False))


def wide_page(c,doc,date,preview):
    interior(c,doc,date,preview)
    # Remove the two-column separator on full-width image and source pages.
    c.setStrokeColor(PAPER)
    c.setLineWidth(2)
    c.line(W/2,83,W/2,772)


def source_heading_paragraphs(issue):
    title=ParagraphStyle('article-title',fontName='Hei',fontSize=19,leading=29,textColor=INK,wordWrap='CJK')
    subtitle=ParagraphStyle('article-subtitle',fontName='Song',fontSize=10.2,leading=17.3,textColor=MUTED,wordWrap='CJK')
    return Paragraph(escape(display_title(issue)),title),Paragraph(escape(issue['subtitle']),subtitle)


def source_heading_height(issue):
    p,q=source_heading_paragraphs(issue)
    return p.wrap(W-92,700)[1]+q.wrap(W-92,700)[1]+34


def first_body_page(c,doc,date,preview,issue):
    interior(c,doc,date,preview)
    # Clear the column separator before drawing full-width title text.
    c.setStrokeColor(PAPER)
    c.setLineWidth(2)
    c.line(W/2,775-source_heading_height(issue)+5,W/2,772)
    p,q=source_heading_paragraphs(issue)
    height=p.wrap(W-92,700)[1]
    p.drawOn(c,46,775-height)
    second=q.wrap(W-92,700)[1]
    q.drawOn(c,46,775-height-12-second)


def inline_markup(fragment):
    """Retain source emphasis and anchors, with no text generation."""
    from html.parser import HTMLParser
    class Inline(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag in ('b','strong'):
                self.out.append('<b>')
            elif tag == 'a':
                href=attrs.get('href','')
                if not href.startswith('https://'):
                    raise ValueError('Unexpected non-HTTPS article link')
                self.out.append('<a href="'+escape(href,{'"':'&quot;'})+'" color="#AC624D">')
            elif tag == 'br':
                self.out.append('<br/>')
        def handle_endtag(self,tag):
            if tag in ('b','strong'):
                self.out.append('</b>')
            elif tag=='a':
                self.out.append('</a>')
        def handle_data(self,data):
            self.out.append(escape(data))
    p=Inline()
    p.feed(fragment)
    return ''.join(p.out)


def rich_flow(issue, normal, heading):
    if issue.get('content_policy') == 'news-text-only-v2':
        flow=[Spacer(1,1),NextPageTemplate('first-body'),PageBreak(),NextPageTemplate('body')]
        for block in issue['blocks']:
            if block['type']=='image' or block.get('audio'):
                raise ValueError('Excluded source media is present in text-only issue')
            if block.get('text','').strip():
                flow.append(Paragraph(inline_markup(block['html']),heading if block['type']=='heading' else normal))
        return flow
    flow=[Spacer(1,1),NextPageTemplate('wide'),PageBreak()]
    title_style=ParagraphStyle('source-title',parent=heading,fontSize=22,leading=31,spaceBefore=4,spaceAfter=16)
    intro_style=ParagraphStyle('source-intro',parent=normal,fontSize=11.5,leading=21,spaceAfter=20)
    audio_style=ParagraphStyle('audio',parent=normal,fontSize=9.5,leading=16,spaceAfter=6)
    flow.append(Paragraph(escape(display_title(issue)),title_style))
    flow.append(Paragraph(escape(issue['subtitle']),intro_style))
    in_intro=True
    for index,block in enumerate(issue['blocks']):
        if block['type']=='heading' and in_intro:
            flow.extend([NextPageTemplate('body'),PageBreak()])
            in_intro=False
        if block['type']=='image':
            width,height=block['width'],block['height']
            if in_intro:
                scale=min((W-92)/width,170/height)
                img=Image(block['path'],width=width*scale,height=height*scale)
                img.hAlign='CENTER'
                flow.extend([img,Spacer(1,16)])
            else:
                flow.extend([NextPageTemplate('wide'),PageBreak()])
                scale=min((W-92)/width,680/height)
                img=Image(block['path'],width=width*scale,height=height*scale)
                img.hAlign='CENTER'
                flow.append(img)
                if index < len(issue['blocks'])-1:
                    flow.extend([NextPageTemplate('body'),PageBreak()])
        elif block.get('text','').strip():
            style=heading if block['type']=='heading' else audio_style if block.get('audio') else normal
            flow.append(Paragraph(inline_markup(block['html']),style))
    return flow


if __name__ == '__main__':
    main()
