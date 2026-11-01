import io

import pytest
from reportlab.pdfgen import canvas

from app import chinese_pdf as c
from app.paper_layout import detect_columns, source_hint, text_columns


def paper_pdf(columns, cover=False):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    if cover:
        pdf.setFont('Times-Roman', 10)
        for line in range(20):
            pdf.drawString(40, 730-line*15, 'This is an abstract with enough long prose to describe research methods and experiments in detail.')
        pdf.showPage()
    for page in range(2):
        pdf.setFont('Times-Roman', 10)
        for line in range(25):
            if columns == 2:
                pdf.drawString(40, 730-line*15, 'We study reliable models and evaluate these methods.')
                pdf.drawString(320, 730-line*15, 'The results support our conclusions across many tasks.')
            else:
                pdf.drawString(40, 730-line*15, 'We study reliable language models and evaluate these methods across many practical tasks and domains.')
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.mark.parametrize('columns', [1, 2])
def test_detect_original_pdf_columns(columns):
    result = detect_columns({'main.tex': r'\documentclass{article}'}, paper_pdf(columns, cover=True))
    assert result['columns'] == columns
    assert result['method'] == 'original_pdf'
    assert all(item['page'] > 1 for item in result['pages'])


def test_unknown_and_explicit_layouts():
    assert source_hint({'main.tex': '% \\twocolumn\n' + r'\documentclass[onecolumn]{article}'})['columns'] == 1
    assert detect_columns({'main.tex': r'\documentclass[twocolumn]{article}'}, None)['columns'] == 2
    with pytest.raises(ValueError, match='无法可靠确认'):
        detect_columns({'main.tex': r'\documentclass{unknown}'}, None)
    assert text_columns('Method       Score\nModel        0.95\n'*20)['columns'] is None


def test_single_column_keeps_wide_formula_in_flow():
    source = r'\begin{figure*}[t]\includegraphics[width=\columnwidth]{figure/a.pdf}\end{figure*}' + r'\begin{equation}\resizebox{0.9\textwidth}{!}{$x=3$}\end{equation}'
    result = c.layout(source, columns=1)
    assert 'twocolumn' not in c.preamble(1)
    assert 'twocolumn' in c.preamble(2)
    assert r'\begin{figure}[htbp]' in result and 'figure*' not in result
    assert result.endswith(r'\begin{equation}\resizebox{0.9\textwidth}{!}{$x=3$}\end{equation}')
    with pytest.raises(ValueError):
        c.preamble(3)

def test_pdf_evidence_overrides_unused_class_option():
    result = detect_columns({'main.tex': r'\documentclass[twocolumn]{article}'}, paper_pdf(1))
    assert result['columns'] == 1 and result['method'] == 'original_pdf'


def test_mixed_body_columns_are_not_silently_flattened():
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for mode in [1, 2]:
        for page in PdfReader(io.BytesIO(paper_pdf(mode))).pages:
            writer.add_page(page)
    mixed = io.BytesIO()
    writer.write(mixed)
    with pytest.raises(ValueError, match='混合'):
        detect_columns({}, mixed.getvalue())
