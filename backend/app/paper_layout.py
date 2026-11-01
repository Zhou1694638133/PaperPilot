"""Read column evidence without executing an untrusted paper template."""
from __future__ import annotations

import io
import re

from pypdf import PdfReader


def text_columns(text: str) -> dict:
    """Vote only on long prose lines; ignore short captions, numbers and tables."""
    single = double = 0
    for line in text.splitlines():
        line = line.strip()
        letters = sum(char.isalpha() for char in line)
        if letters < 65 or letters < len(line.replace(' ', '')) * 0.65:
            continue
        parts = re.split(r' {6,}', line)
        prose = [part for part in parts if sum(char.isalpha() for char in part) >= 25]
        if len(prose) == 2:
            double += 1
        elif len(parts) == 1:
            single += 1
    total = single + double
    columns = None
    if double >= 8 and double / max(total, 1) >= 0.45:
        columns = 2
    elif single >= 12 and double / max(total, 1) <= 0.15:
        columns = 1
    return {'columns': columns, 'single_lines': single, 'double_lines': double}


def source_hint(files: dict[str, str]) -> dict | None:
    for name, source in files.items():
        source = re.sub(r'(?<!\\)%[^\n]*', '', source)
        declaration = re.search(r'\\documentclass(?:\[([^]]*)\])?\{([^}]+)\}', source)
        if not declaration:
            continue
        options = (declaration[1] or '').split(',')
        modes = {2 if option.strip() == 'twocolumn' else 1 for option in options
                 if option.strip() in {'onecolumn', 'twocolumn'}}
        modes.update(2 if command == 'twocolumn' else 1 for command in re.findall(r'\\(onecolumn|twocolumn)\b', source))
        if len(modes) == 1:
            return {'columns': modes.pop(), 'method': 'latex_explicit', 'source_file': name}
    return None


def detect_columns(files: dict[str, str], pdf: bytes | None, check_cancel=lambda: None) -> dict:
    evidence = []
    if pdf:
        try:
            reader = PdfReader(io.BytesIO(pdf))
            # A full-width title/abstract must not decide a two-column paper's layout.
            start = 1 if len(reader.pages) > 1 else 0
            for index in range(start, min(len(reader.pages), start + 8)):
                check_cancel()
                vote = text_columns(reader.pages[index].extract_text(extraction_mode='layout'))
                evidence.append({'page': index + 1, **vote})
        except (ValueError, OSError) as exc:
            raise ValueError('原 PDF 栏数识别失败，请检查文件是否完整') from exc
        votes = {item['columns'] for item in evidence if item['columns']}
        if len(votes) == 1:
            return {'columns': votes.pop(), 'method': 'original_pdf', 'pages': evidence}
        if len(votes) > 1:
            raise ValueError('原文检测到混合单栏/双栏正文，暂不自动套用统一模板，请核对分节版式')
    hint = source_hint(files)
    if hint:
        return {**hint, 'pages': evidence}
    raise ValueError('无法可靠确认原文单栏或双栏，请获取可提取正文的原 PDF 后重试；未默认套用模板')
