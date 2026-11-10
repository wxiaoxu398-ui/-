"""Render the reviewed Markdown and actual UI evidence into an A4 technical PDF."""
import argparse
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, Image, KeepTogether, Preformatted,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
GREEN = colors.HexColor('#245F55')
INK = colors.HexColor('#263D35')
MUTED = colors.HexColor('#62776B')
PALE = colors.HexColor('#EFF5F0')
LINE = colors.HexColor('#DCE7DF')
PAGE_W, PAGE_H = A4
MARGIN = 48
WIDTH = PAGE_W - 2 * MARGIN


def inline(text):
    text = escape(text.replace('\u2013', '-').replace('\u2014', '-').replace('\u2011', '-'))
    text = re.sub(r'`([^`]+)`', r'<font color="#245F55">\1</font>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'<link href="\2" color="#245F55">\1</link>', text)
    return text


def architecture():
    drawing = Drawing(WIDTH, 170)
    gap = 14
    box_w = (WIDTH - gap * 3) / 4
    labels = [('01', '逐页解析', 'PDF / 原页 / 指纹'), ('02', '混合检索', 'BGE + BM25 / RRF'),
              ('03', '证据限定生成', 'DeepSeek / 引用检查'), ('04', '前端核对', 'SSE / 原文 / 自测')]
    for index, (number, title, note) in enumerate(labels):
        x = index * (box_w + gap)
        drawing.add(Rect(x, 66, box_w, 86, rx=6, ry=6, fillColor=PALE, strokeColor=LINE))
        drawing.add(String(x + 10, 131, number, fontName='YaHei', fontSize=8, fillColor=MUTED))
        drawing.add(String(x + 10, 107, title, fontName='YaHeiBold', fontSize=10, fillColor=GREEN))
        drawing.add(String(x + 10, 85, note, fontName='YaHei', fontSize=7.2, fillColor=MUTED))
        if index < 3:
            right = x + box_w
            drawing.add(Line(right + 2, 108, right + gap - 3, 108, strokeColor=GREEN))
            drawing.add(Polygon([right + gap - 3, 108, right + gap - 7, 111, right + gap - 7, 105], fillColor=GREEN, strokeColor=GREEN))
    drawing.add(Rect(0, 8, WIDTH, 37, rx=5, ry=5, fillColor=colors.white, strokeColor=LINE))
    drawing.add(String(12, 30, '本机存储：SQLite 会话与原文 / Chroma 向量索引 / PDF 与来源许可', fontName='YaHei', fontSize=9, fillColor=INK))
    drawing.add(String(12, 16, '生成调用发送问题、最近上下文与本轮检索片段；资料不足时拒答。', fontName='YaHei', fontSize=8, fillColor=MUTED))
    return drawing


class TechnicalDoc(BaseDocTemplate):
    def __init__(self, filename):
        super().__init__(filename, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=49, bottomMargin=45, title='MedRAG 医学教材智能问答助手 - 技术文档',
                         author='MedRAG 项目团队（实名待补）')
        frame = Frame(MARGIN, 45, WIDTH, PAGE_H - 94, leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0, id='content')
        self.addPageTemplates(PageTemplate(id='report', frames=frame, onPage=self.decorate))

    def decorate(self, canvas, doc):
        if doc.page == 1:
            return
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(MARGIN, PAGE_H - 33, PAGE_W - MARGIN, PAGE_H - 33)
        canvas.setFont('YaHei', 7.6)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, PAGE_H - 25, 'MedRAG / 医学教材智能问答助手')
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 25, '技术文档 · 2026-11-10')
        canvas.line(MARGIN, 33, PAGE_W - MARGIN, 33)
        canvas.drawString(MARGIN, 20, '仅供医学学习参考，不构成医疗建议。')
        canvas.drawRightString(PAGE_W - MARGIN, 20, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        bookmark = getattr(flowable, '_bookmark', None)
        if bookmark:
            level = 0 if flowable.style.name == 'Chapter' else 1
            title = flowable.getPlainText()
            self.canv.bookmarkPage(bookmark)
            self.canv.addOutlineEntry(title, bookmark, level=level, closed=False)
            if level == 0:
                self.notify('TOCEntry', (level, title, self.page, bookmark))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / 'submission/technical-report.md')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/pdf/MedRAG-技术文档.pdf')
    parser.add_argument('--font', default='C:/Windows/Fonts/msyh.ttc')
    parser.add_argument('--bold-font', default='C:/Windows/Fonts/msyhbd.ttc')
    parser.add_argument('--screenshots-dir', type=Path, default=ROOT / '.local')
    args = parser.parse_args()
    pdfmetrics.registerFont(TTFont('YaHei', args.font, subfontIndex=0))
    pdfmetrics.registerFont(TTFont('YaHeiBold', args.bold_font, subfontIndex=0))
    pdfmetrics.registerFontFamily('YaHei', normal='YaHei', bold='YaHeiBold', italic='YaHei', boldItalic='YaHeiBold')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('BodyCN', fontName='YaHei', fontSize=10, leading=17, textColor=INK,
                              wordWrap='CJK', spaceAfter=7, allowWidows=0, allowOrphans=0))
    styles.add(ParagraphStyle('Chapter', parent=styles['BodyCN'], fontName='YaHeiBold', fontSize=16,
                              leading=24, textColor=GREEN, spaceBefore=19, spaceAfter=10, keepWithNext=True))
    styles.add(ParagraphStyle('SectionCN', parent=styles['BodyCN'], fontName='YaHeiBold', fontSize=12,
                              leading=19, spaceBefore=13, spaceAfter=7, keepWithNext=True))
    styles.add(ParagraphStyle('CellCN', parent=styles['BodyCN'], fontSize=8.3, leading=13, spaceAfter=0))
    styles.add(ParagraphStyle('CaptionCN', parent=styles['BodyCN'], fontSize=8.2, leading=13,
                              textColor=MUTED, spaceBefore=6, spaceAfter=15))
    styles.add(ParagraphStyle('CoverBrand', fontName='YaHeiBold', fontSize=42, leading=56, textColor=GREEN))
    styles.add(ParagraphStyle('CoverTitle', parent=styles['BodyCN'], fontName='YaHeiBold', fontSize=24,
                              leading=38, spaceBefore=12, spaceAfter=18))
    styles.add(ParagraphStyle('TOCTitle', parent=styles['Chapter'], fontSize=24, leading=34))
    styles.add(ParagraphStyle('CodeCN', fontName='YaHei', fontSize=8, leading=12,
                              textColor=INK, backColor=PALE, borderPadding=9, spaceBefore=4, spaceAfter=12))
    story = []
    p = lambda text, style='BodyCN': Paragraph(inline(text), styles[style])

    def table(rows):
        cols = len(rows[0])
        widths = [145, WIDTH - 145] if cols == 2 else [100, 210, WIDTH - 310] if cols == 3 else [WIDTH / cols] * cols
        cells = [[p(value, 'CellCN') for value in row] for row in rows]
        result = Table(cells, colWidths=widths, repeatRows=1, hAlign='LEFT')
        result.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PALE), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ('LINEBELOW', (0, 0), (-1, 0), 0.8, LINE), ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINE),
            ('BOX', (0, 0), (-1, -1), 0.5, LINE),
        ]))
        return result

    team_path = ROOT / 'submission/team-info.json'
    if not team_path.exists():
        team_path = ROOT / 'submission/team-info.example.json'
    team = json.loads(team_path.read_text(encoding='utf-8'))
    story += [Spacer(1, 58), p('MedRAG', 'CoverBrand'), p('医学教材智能问答助手', 'CoverTitle'),
              p('技术文档 / 参赛材料核对版', 'SectionCN'),
              p('让问题回到教材，让结论可以核对。'), Spacer(1, 25),
              table([['信息', '记录'], ['文档日期', '2026-11-10'], ['应用实现基线', '587391d'],
                     ['团队名称', team.get('team_name') or '待团队填写'], ['学校 / 组别', (team.get('school') or '待填写') + ' / ' + (team.get('competition_group') or '待填写')],
                     ['仓库与归档版本', '具体评审仓库待填写；归档提交号见材料包清单。']]), Spacer(1, 25),
              p('提交前须补齐真实团队信息并终审；文中区分检索命中、引用格式和语义准确性，不将尚未完成人工核验的结果写为准确率。', 'CaptionCN'), PageBreak()]
    story.append(p('目录与阅读提示', 'TOCTitle'))
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle('TOC0', fontName='YaHei', fontSize=10, leading=18, textColor=GREEN,
                                     leftIndent=0, firstLineIndent=0, spaceBefore=5),
                       ParagraphStyle('TOC1', fontName='YaHei', fontSize=8.8, leading=15, textColor=MUTED,
                                      leftIndent=14, firstLineIndent=0, spaceBefore=1)]
    story += [toc, Spacer(1, 20), p('阅读提示', 'SectionCN'),
              p('本报告描述本地可运行实现，后端绑定回环地址。资料和历史在本机保存，生成时按需调用 DeepSeek。评估结果为 2026-11-10 的有限样本，不构成医学正确性或稳定性能的保证。'),
              table([['已验证项目', '结果'], ['后端自动化检查', '24 项通过'], ['中文全库评估', '11/11 完成；预期原页命中 7/8；资料外拒答 3/3'], ['引用映射核对', '18 个片段的文件指纹、物理页与原文通过核对'], ['尚待完成', '团队实名、评审仓库、讲解视频、人工语义核验与团队终审']]), PageBreak()]

    lines = args.source.read_text(encoding='utf-8').splitlines()
    index, heading_count = 0, 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or (index == 0 and line.startswith('# ')) or line.startswith('版本记录：') or line.startswith('参赛团队、学校'):
            index += 1
            continue
        if line.startswith('<!-- figure: architecture'):
            story.extend([architecture(), p('图 1. 从 PDF 原页到可核对回答的工作流。图中阶段为确定性处理过程，不代表自主 Agent 或模型内部思维。', 'CaptionCN')])
        elif line.startswith('<!-- figure: study'):
            path = args.screenshots_dir / 'medrag-competition-study.png'
            from PIL import Image as PILImage
            with PILImage.open(path) as source_image:
                w, h = source_image.size
            story.append(KeepTogether([Image(str(path), width=WIDTH, height=WIDTH * h / w),
                                       p('图 2. 实际桌面学习界面：自测题、可展开答案，以及来源片段和原页入口。截图为本机运行样本。', 'CaptionCN')]))
        elif line.startswith('```'):
            code = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith('```'):
                code.append(lines[index])
                index += 1
            if not any('PDF 与来源许可' in value for value in code):
                story.append(Preformatted('\n'.join(code), styles['CodeCN']))
        elif line.startswith('|'):
            rows = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                row = [cell.strip() for cell in lines[index].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[:\-\s]+', cell) for cell in row):
                    rows.append(row)
                index += 1
            story.extend([table(rows), Spacer(1, 10)])
            continue
        elif line.startswith('## '):
            heading_count += 1
            item = p(line[3:], 'Chapter')
            item._bookmark = 'chapter-' + str(heading_count)
            story.append(item)
        elif line.startswith('### '):
            heading_count += 1
            item = p(line[4:], 'SectionCN')
            item._bookmark = 'section-' + str(heading_count)
            story.append(item)
        elif line.startswith('- '):
            story.append(Paragraph(inline(line[2:]), styles['BodyCN'], bulletText='-'))
        else:
            parts = [line]
            while index + 1 < len(lines) and lines[index + 1].strip() and not lines[index + 1].lstrip().startswith(('#', '|', '```', '<!--', '- ')):
                index += 1
                parts.append(lines[index].strip())
            story.append(p(' '.join(parts)))
        index += 1

    story.append(PageBreak())
    appendix = p('附录 A. 实际界面与资料快照', 'Chapter')
    appendix._bookmark = 'appendix-a'
    story.append(appendix)
    story.append(p('A.1 作品说明与资料范围', 'SectionCN'))
    from PIL import Image as PILImage
    for name, width, caption in [
        ('medrag-competition-project.png', WIDTH, '图 A1. 作品说明页展示 7 份资料、4,335 页和 13,711 个片段；这些是记录时的本机资料快照。'),
        ('medrag-competition-mobile.png', 225, '图 A2. 375 像素宽度的实际窄屏学习页。参考答案折叠显示，API 配置与主要操作入口可用。'),
    ]:
        path = args.screenshots_dir / name
        with PILImage.open(path) as source_image:
            w, h = source_image.size
        story.append(KeepTogether([Image(str(path), width=width, height=width * h / w), p(caption, 'CaptionCN')]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    TechnicalDoc(str(args.output)).multiBuild(story)
    pages = len(PdfReader(args.output).pages)
    if not 1 <= pages <= 30:
        raise ValueError('Technical PDF page count must be between 1 and 30.')
    print(json.dumps({'pdf': str(args.output.resolve()), 'pages': pages, 'bytes': args.output.stat().st_size}, ensure_ascii=False))


if __name__ == '__main__':
    main()
