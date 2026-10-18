"""Local, deterministic typesetting of a completed research answer."""
from datetime import datetime
from html import escape
from pathlib import Path
import re
from zoneinfo import ZoneInfo


def wants_pdf(text):
    # Mentioning an input PDF or asking what it is does not request a file reply.
    clauses = re.split(r'[。！？；;\n]', text)
    for clause in clauses:
        if re.search(r'(不要|不用|无需|别|不需要).{0,12}pdf|(?:no|without)\s+(?:a\s+)?pdf', clause, re.I):
            continue
        if re.search(r'(生成|导出|制作|做成|转成|转换成|整理成|保存为|输出|提供).{0,24}pdf|(?:给我|我要)(?:一份|一个|份)?\s*pdf|(?:以|用)\s*pdf|pdf\s*(?:格式)?\s*(?:发给|发送|版本)|(?:generate|export|create|send).{0,24}\bpdf\b', clause, re.I):
            return True
    return False


def report_path(data_dir, run_id):
    import uuid
    safe = str(uuid.UUID(run_id))
    return Path(data_dir).resolve() / 'reports' / safe / ('金融研究报告-' + safe[:8] + '.pdf')


def render_report(markdown, path, *, created_at=None, sample=False):
    from markdown_it import MarkdownIt
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, HRFlowable, Flowable, CondPageBreak
    from reportlab.platypus import paragraph as paragraph_engine
    from reportlab.lib import textsplit
    import unicodedata
    # ReportLab's Japanese default omits Chinese full-width comma/colon and
    # curly closing quotes. Permit hanging punctuation inside the cell padding.
    for module in (paragraph_engine, textsplit):
        module.ALL_CANNOT_START = ''.join(dict.fromkeys(module.ALL_CANNOT_START + '，；：！？）】》〉”’'))
    fonts = Path('C:/Windows/Fonts')
    if 'Research' not in pdfmetrics.getRegisteredFontNames():
        regular = fonts / 'msyh.ttc'
        bold = fonts / 'msyhbd.ttc'
        pdfmetrics.registerFont(TTFont('Research', str(regular if regular.exists() else fonts / 'simhei.ttf')))
        pdfmetrics.registerFont(TTFont('ResearchBold', str(bold if bold.exists() else fonts / 'simhei.ttf')))
        pdfmetrics.registerFontFamily('Research', normal='Research', bold='ResearchBold', italic='Research', boldItalic='ResearchBold')
        pdfmetrics.registerFont(TTFont('ResearchMono', str(fonts / 'consola.ttf')))
        pdfmetrics.registerFont(TTFont('ResearchSymbol', str(fonts / 'seguisym.ttf')))
    navy, gold, ink, muted = map(colors.HexColor, ['#10283F', '#B59A62', '#263848', '#71808E'])
    paper = colors.HexColor('#F2F5F8')
    styles = {
        'body': ParagraphStyle('body', fontName='Research', fontSize=9.5, leading=15.5, textColor=ink, spaceAfter=8, wordWrap='CJK', allowWidows=0, allowOrphans=0),
        'title': ParagraphStyle('title', fontName='ResearchBold', fontSize=23, leading=32, textColor=navy, spaceAfter=12, wordWrap='CJK', keepWithNext=True),
        'h2': ParagraphStyle('h2', fontName='ResearchBold', fontSize=14, leading=21, textColor=navy, spaceBefore=17, spaceAfter=7, keepWithNext=True, wordWrap='CJK'),
        'h3': ParagraphStyle('h3', fontName='ResearchBold', fontSize=10.5, leading=17, textColor=navy, spaceBefore=11, spaceAfter=7, keepWithNext=True, wordWrap='CJK'),
        'cell': ParagraphStyle('cell', fontName='Research', fontSize=8.5, leading=13.5, textColor=ink, wordWrap='CJK'),
        'th': ParagraphStyle('th', fontName='ResearchBold', fontSize=8.5, leading=13.5, textColor=colors.white, wordWrap='CJK'),
        'quote': ParagraphStyle('quote', fontName='Research', fontSize=10, leading=17, textColor=navy, backColor=paper, borderColor=gold, borderWidth=.6, borderPadding=11, spaceBefore=8, spaceAfter=14, wordWrap='CJK'),
    }
    def clean(text):
        # Preserve semantic status, including cells containing only an icon.
        for icon, label in {'✅':'✓', '❌':'×', '⏳':'待定', '🔄':'进行中', '⚠':'注意'}.items():
            text = re.sub(re.escape(icon) + r'\uFE0F?\s*', lambda m: label + ' ', text)
        return re.sub('[\U0001F000-\U0001FAFF\uFE0F\u200D]', '', text)
    def glyphs(text):
        regular = pdfmetrics.getFont('Research').face.charWidths
        symbols = pdfmetrics.getFont('ResearchSymbol').face.charWidths
        return ''.join(('<font name="ResearchSymbol">' + escape(ch) + '</font>')
                       if ord(ch) not in regular and ord(ch) in symbols else escape(ch) for ch in clean(text))
    def text_markup(text):
        # CommonMark rejects bold next to some CJK punctuation. Repair balanced
        # literal markers left in TEXT tokens, never code or arithmetic.
        parts = re.split(r'(\*\*[^*\n]+\*\*)', text)
        return ''.join('<b>' + glyphs(p[2:-2]) + '</b>' if p.startswith('**') and p.endswith('**') else glyphs(p) for p in parts)
    def inline(token):
        out = []
        for t in token.children or []:
            if t.type == 'text': out.append(text_markup(t.content))
            elif t.type == 'code_inline': out.append(glyphs(t.content))
            elif t.type == 'softbreak': out.append(' ')
            elif t.type == 'hardbreak': out.append('<br/>')
            elif t.type == 'strong_open': out.append('<b>')
            elif t.type == 'strong_close': out.append('</b>')
            elif t.type == 'em_open': out.append('<i>')
            elif t.type == 'em_close': out.append('</i>')
            elif t.type == 'link_open':
                url = t.attrGet('href') or ''
                out.append('<a href="' + escape(url, quote=True) + '" color="#37627F">' if url.startswith(('https://', 'http://')) else '<span>')
            elif t.type == 'link_close':
                # markdown-it rejects unsafe links; keep relative references as text.
                opening = next((x for x in reversed(out) if x.startswith(('<a ', '<span>'))), '<span>')
                out.append('</a>' if opening.startswith('<a ') else '</span>')
            elif t.type == 'image': out.append(escape(clean(t.content)))
        return ''.join(out) or '&#160;'
    tokens = MarkdownIt('commonmark', {'html': False}).enable('table').parse(markdown)
    first_title = next((i for i, t in enumerate(tokens) if t.type == 'heading_open' and t.tag == 'h1'), None)
    title = clean(tokens[first_title + 1].content) if first_title is not None else '金融市场研究报告'
    stamp = datetime.fromisoformat(created_at).astimezone(ZoneInfo('Asia/Shanghai')) if created_at else datetime.now(ZoneInfo('Asia/Shanghai'))
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=44, leftMargin=44, topMargin=74, bottomMargin=57,
                            title=title, author='Vibe Research', pageCompression=1)
    story = [Paragraph('FINANCIAL RESEARCH' + (' / DESIGN PREVIEW' if sample else ''), styles['h3']),
             Paragraph(escape(title), styles['title']),
             Paragraph(stamp.strftime('%Y.%m.%d %H:%M'), styles['body']), Spacer(1,8)]
    class Diagram(Flowable):
        """Fixed cell grid preserves ASCII charts without guessing their data."""
        def __init__(self, content, size=8.5):
            super().__init__()
            self.lines = content.expandtabs(4).rstrip().splitlines() or ['']
            units = lambda line: sum(2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in line)
            self.size = min(size, (doc.width - 28) / max(1, max(map(units, self.lines))) / .6)
            self.width = doc.width
            self.height = len(self.lines) * (self.size * 1.65) + 22
            self.spaceBefore = 4; self.spaceAfter = 12
        def split(self, aw, ah):
            count = int((ah - 22) // (self.size * 1.65))
            if count < 2:return []
            return [Diagram('\n'.join(self.lines[:count]), self.size), Diagram('\n'.join(self.lines[count:]), self.size)]
        def draw(self):
            c = self.canv
            c.setFillColor(paper); c.roundRect(0,0,self.width,self.height,3,fill=1,stroke=0)
            c.setStrokeColor(gold); c.setLineWidth(1.5); c.line(0,6,0,self.height-6)
            for n,line in enumerate(self.lines):
                y = self.height - 14 - self.size - n*self.size*1.65
                text = c.beginText(14,y); text.setFillColor(ink)
                for ch in line:
                    wide = unicodedata.east_asian_width(ch) in ('W','F')
                    family = 'Research' if wide else 'ResearchMono'
                    if ord(ch) not in pdfmetrics.getFont(family).face.charWidths:family = 'ResearchSymbol'
                    text.setFont(family, self.size)
                    text.setCharSpace(self.size * .6 * (2 if wide else 1) - pdfmetrics.stringWidth(ch,family,self.size))
                    text.textOut(ch)
                c.drawText(text)
    lists, quote, i, item_first = [], False, 0, False
    while i < len(tokens):
        t = tokens[i]
        if t.type == 'heading_open':
            if i != first_title:
                story.append(Paragraph(inline(tokens[i+1]), styles['h2' if t.tag in ('h1','h2') else 'h3']))
                if t.tag in ('h1','h2'):
                    rule = HRFlowable(width='100%', thickness=.45, color=gold, spaceAfter=9)
                    rule.keepWithNext = True
                    story.append(rule)
            i += 3; continue
        if t.type == 'table_open':
            rows, row, cell, header = [], [], '', False
            i += 1
            while i < len(tokens) and tokens[i].type != 'table_close':
                q = tokens[i]
                if q.type == 'tr_open': row = []
                elif q.type in ('th_open','td_open'): header = q.type == 'th_open'; cell = ''
                elif q.type == 'inline': cell += inline(q)
                elif q.type in ('th_close','td_close'): row.append((cell, header))
                elif q.type == 'tr_close': rows.append(row)
                i += 1
            if rows:
                cols = max(map(len, rows))
                panels = [list(range(cols))] if cols <= 6 else [[0] + list(range(j, min(j+5,cols))) for j in range(1,cols,5)]
                for panel in panels:
                    weights = [min(48, max(10, max(len(re.sub('<[^>]+>', '', r[c][0])) if c < len(r) else 0 for r in rows))) ** .8 for c in panel]
                    widths = [doc.width * w / sum(weights) for w in weights]
                    numeric = {c for c in panel if all(re.fullmatch(r'[\s\d.,%+−–—\-x倍$¥€()（）/]+', re.sub('<[^>]+>', '', r[c][0])) for r in rows[1:] if c < len(r))}
                    data = [[Paragraph(r[c][0] if c < len(r) else '', ParagraphStyle('numeric',parent=styles['th' if n == 0 else 'cell'],alignment=TA_RIGHT) if c in numeric else styles['th' if n == 0 else 'cell']) for c in panel] for n,r in enumerate(rows)]
                    table = LongTable(data, colWidths=widths, repeatRows=1, hAlign='LEFT', splitByRow=1, splitInRow=1)
                    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy), ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,paper]),
                        ('LINEBELOW',(0,0),(-1,0),1,gold), ('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#DCE3E9')),
                        ('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(-1,-1),9), ('RIGHTPADDING',(0,0),(-1,-1),9),
                        ('TOPPADDING',(0,0),(-1,-1),7), ('BOTTOMPADDING',(0,0),(-1,-1),7)]))
                    story.extend([table, Spacer(1,12)])
            i += 1; continue
        if t.type in ('bullet_list_open','ordered_list_open'): lists.append(int(t.attrGet('start') or 1) if t.type == 'ordered_list_open' else None)
        elif t.type in ('bullet_list_close','ordered_list_close'): lists.pop()
        elif t.type == 'list_item_open':item_first = True
        elif t.type == 'blockquote_open': quote = True
        elif t.type == 'blockquote_close': quote = False
        elif t.type == 'inline':
            prefix = ''
            if lists and item_first:
                prefix = '• ' if lists[-1] is None else str(lists[-1]) + '. '
                if lists[-1] is not None:lists[-1] += 1
            style = styles['quote' if quote else 'body']
            if lists:style = ParagraphStyle('list', parent=style, leftIndent=12*len(lists), firstLineIndent=-10)
            story.append(Paragraph(prefix + inline(t), style))
            item_first = False
        elif t.type in ('fence', 'code_block'):
            story.append(Diagram(t.content))
        elif t.type == 'hr': story.append(HRFlowable(width='100%', thickness=.5, color=gold, spaceBefore=12, spaceAfter=12))
        i += 1
    # Reserve enough space for a heading/short introduction plus the first data
    # row, not an entire table. Long tables remain free to split and repeat headers.
    laid_out = []
    for n, block in enumerate(story):
        if (isinstance(block, Paragraph) and (block.getKeepWithNext() or len(block.getPlainText()) < 100)
                and (n == 0 or not story[n-1].getKeepWithNext())):
            reserve = 0
            for following in story[n:n+6]:
                if following is not block and isinstance(following, Paragraph) and following.style.name == 'h2':break
                if isinstance(following, LongTable):
                    following.wrap(doc.width, doc.height)
                    reserve += sum(following._rowHeights[:2])
                    laid_out.append(CondPageBreak(min(reserve, doc.height * .65)))
                    break
                _, height = following.wrap(doc.width, doc.height)
                reserve += height + following.getSpaceBefore() + following.getSpaceAfter()
        laid_out.append(block)
    story = laid_out
    def furniture(canvas, document):
        w,h = A4
        canvas.saveState(); canvas.setFillColor(navy); canvas.rect(0,h-8,w,8,fill=1,stroke=0)
        canvas.setFont('ResearchBold',9); canvas.drawString(44,h-39,'VIBE / RESEARCH')
        canvas.setFillColor(gold); canvas.rect(44,h-53,24,2,fill=1,stroke=0)
        canvas.setStrokeColor(gold); canvas.setLineWidth(.45); canvas.line(76,h-52,w-44,h-52)
        canvas.setFont('Research',8); canvas.setFillColor(muted)
        canvas.drawRightString(w-44,h-39,'金融研究 · 版式预览' if sample else '金融研究')
        canvas.setStrokeColor(colors.HexColor('#DCE3E9')); canvas.line(44,42,w-44,42)
        canvas.setFont('Research',7); canvas.drawString(44,28,stamp.strftime('%Y.%m.%d') + '  |  VIBE RESEARCH')
        canvas.setFillColor(gold); canvas.drawRightString(w-44,28,f'{document.page:02d}')
        canvas.restoreState()
    temporary = path.with_suffix('.tmp.pdf')
    doc.filename = str(temporary)
    doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
    temporary.replace(path)
    return path
