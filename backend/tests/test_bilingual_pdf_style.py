import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.config import ROOT
from app.pdf_export import render_pdf


def test_bilingual_profile_uses_black_song_and_latin_fonts_without_report_footer():
    markdown = '# Original Title 原论文标题\n\n## 研究方法\n\n### 原文\n\nWe compare Agent A and Agent B, x < 3 & y > 2.\n\n### 译文\n\n我们比较智能体 A 与智能体 B，数值保持一致。'
    reader = PdfReader(io.BytesIO(render_pdf(markdown, profile='bilingual', title='Original Title')))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert '我们比较智能体 A' in text and 'x < 3 & y > 2' in text
    assert text.index('We compare') < text.index('我们比较')
    assert 'PaperPilot' not in text and '未独立复现实验' not in text
    assert reader.metadata.title == 'Original Title'
    fonts = {str(f.get_object().get('/BaseFont')) for page in reader.pages for f in page['/Resources']['/Font'].values()}
    if Path('C:/Windows/Fonts/simsun.ttc').is_file():
        assert any('SimSun' in font for font in fonts)
    assert any('Times' in font for font in fonts)
    for page in reader.pages:
        for operands, operator in page.get_contents().operations:
            if operator in {b'rg', b'g'}:
                assert all(float(value) == 0 for value in operands)


def test_frozen_bilingual_style_and_existing_report_are_distinct():
    style = json.loads((ROOT/'skills/paper_translator/templates/bilingual-style.json').read_text(encoding='utf-8-sig'))
    assert style['body_size'] == 12 and style['first_line_indent'] == 24
    assert style['text_color'] == '#000000' and style['footer'] == 'page_number_only'
    reader = PdfReader(io.BytesIO(render_pdf('# 研究报告\n\n保留报告样式。')))
    assert '未独立复现实验' in reader.pages[0].extract_text()
    with pytest.raises(ValueError):
        render_pdf('text', profile='unknown')
