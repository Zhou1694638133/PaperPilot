"""Paginated Chinese research reports, preserving source text as escaped content."""
import io
import json
import re
from typing import Literal
import os
from pathlib import Path
from xml.sax.saxutils import escape


def render_pdf(markdown: str, *, profile: Literal["report", "bilingual"] = "report", title: str | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    if profile not in {"report", "bilingual"}:
        raise ValueError("Unknown PDF profile")
    bilingual = profile == "bilingual"
    font = "PaperPilotCJK"
    if font not in pdfmetrics.getRegisteredFontNames():
        path = Path(os.getenv("PDF_FONT_PATH", "C:/Windows/Fonts/simsun.ttc"))
        if path.is_file():
            pdfmetrics.registerFont(TTFont(font, str(path), subfontIndex=0))
        else:
            font = "STSong-Light"
            pdfmetrics.registerFont(UnicodeCIDFont(font))
    english = english_bold = font
    specification = None
    if bilingual:
        from .config import ROOT
        specification = json.loads((ROOT / "skills/paper_translator/templates/bilingual-style.json").read_text(encoding="utf-8-sig"))
        english, english_bold = "Times-Roman", "Times-Bold"
        for name, filename in (("PaperPilotTimes", "times.ttf"), ("PaperPilotTimesBold", "timesbd.ttf")):
            path = Path("C:/Windows/Fonts") / filename
            if path.is_file():
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, str(path)))
                if name == "PaperPilotTimes":
                    english = name
                else:
                    english_bold = name
    base = ParagraphStyle("Body", fontName=font, fontSize=10.5, leading=17, spaceAfter=9,
                          textColor=colors.HexColor("#172d39"), wordWrap="CJK", alignment=TA_LEFT,
                          splitLongWords=True)
    heading = ParagraphStyle("Heading", parent=base, fontSize=16, leading=23, spaceBefore=14, spaceAfter=10,
                             textColor=colors.HexColor("#13695f"), keepWithNext=True)
    quote = ParagraphStyle("Quote", parent=base, leftIndent=12, borderPadding=8,
                           backColor=colors.HexColor("#eef5f3"))
    location = ParagraphStyle("Location", parent=base, keepWithNext=True)
    subheading = label = heading
    if specification:
        base.fontSize = specification["body_size"]
        base.leading = specification["body_leading"]
        base.textColor = colors.black
        base.firstLineIndent = specification["first_line_indent"]
        base.spaceAfter = specification["paragraph_space_after"]
        heading.fontSize = specification["title_size"]
        heading.leading = specification["title_size"] + 8
        heading.textColor = colors.black
        heading.firstLineIndent = 0
        subheading = ParagraphStyle("BilingualSection", parent=heading, fontSize=specification["section_size"], leading=23)
        label = ParagraphStyle("BilingualLabel", parent=heading, fontSize=specification["label_size"], leading=20, spaceBefore=8, spaceAfter=4)
        quote = ParagraphStyle("BilingualQuote", parent=base)
        location = ParagraphStyle("BilingualLocation", parent=base, firstLineIndent=0, fontSize=10, leading=16, spaceAfter=6, keepWithNext=True)
    def content(value, is_heading=False):
        if not bilingual:
            return escape(value).replace("\n", "<br/>")
        # Escape user text before adding trusted font spans; mixed English/CJK stays legible.
        chunks = re.split(r"([\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]+)", value)
        result = []
        for chunk in chunks:
            if not chunk:
                continue
            cjk = bool(re.match(r"[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]", chunk))
            selected = font if cjk else english_bold if is_heading else english
            size = "" if cjk or is_heading else f' size="{specification["english_body_size"]}"'
            result.append(f'<font name="{selected}"{size}>' + escape(chunk).replace("\n", "<br/>") + "</font>")
        return "".join(result)
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=48, bottomMargin=44,
                            title=title or ("双语对照" if bilingual else "PaperPilot Research Report"), author="" if bilingual else "PaperPilot")
    story = []
    for paragraph in markdown.split("\n\n"):
        if not paragraph.strip():
            continue
        is_heading = paragraph.startswith("#")
        style = (label if bilingual and paragraph.startswith("###") else subheading if bilingual and paragraph.startswith("##") else heading) if is_heading else quote if paragraph.startswith("> ") else location if paragraph.startswith(("位置：", "来源：")) else base
        text = paragraph.lstrip("# ") if is_heading else paragraph[2:] if style is quote else paragraph
        # Bounded flowables allow long abstracts, formulas and URLs to paginate safely.
        for start in range(0, len(text), 2500):
            story.append(Paragraph(content(text[start:start + 2500], is_heading), style))
    if not story:
        story.append(Spacer(1, 12))
    def footer(canvas, document):
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.black if bilingual else colors.HexColor("#60717c"))
        if bilingual:
            canvas.drawCentredString(A4[0] / 2, 25, str(document.page))
        else:
            canvas.drawString(42, 25, "PaperPilot | 来源证据与研究笔记 · 未独立复现实验")
            canvas.drawRightString(A4[0] - 42, 25, str(document.page))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
