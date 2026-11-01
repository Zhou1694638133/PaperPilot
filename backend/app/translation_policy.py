"""Conservative source-based classification; no paper IDs or provider output heuristics."""
import hashlib
import re
from collections import Counter

VERSION = "1.1"


def identifier_only(text, segments):
    # Require explicit technical syntax, not merely uppercase English prose.
    marked = bool(re.search(r"[A-Za-z0-9]+(?:\\_|_)[A-Za-z0-9]+|\b[A-Z]{2,}[-.]\d", text))
    remaining = ' '.join(item['text'] for item in segments)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]*", remaining)
    if re.fullmatch(r"[A-Z]{2,}[-.]\d+(?:[.\dA-Za-z-]*)", text.strip()):
        return True
    return marked and (not tokens or ':' in remaining) and len(tokens) <= 4 and all(re.fullmatch(r"[A-Z]{2,}(?:-[A-Za-z0-9]+)*", token) for token in tokens)


def preflight(blocks):
    from .skills import code_literal_ranges, code_fence_scan, latex_translation_plan
    from .fulltext import is_layout_only
    ranges = code_literal_ranges(blocks)
    _, opened = code_fence_scan(blocks)
    items, offset = [], 0
    for block in blocks:
        text = block['text']
        kind, reason = 'prose', '正文需要中文翻译；数值与保护结构须一致'
        try:
            segments, _ = latex_translation_plan(text, ranges[block['id']])
            if '\ufffd' in text or '\x00' in text:
                kind, reason = 'blocked', '原文含乱码，需核对来源，不能猜补'
            elif opened and offset + len(text) > opened[1]:
                kind, reason = 'blocked', '原文代码围栏未闭合，需核对来源'
            elif not block.get('translatable', True) or is_layout_only(text):
                kind, reason = 'protected', '代码、公式、引用或排版结构原样保留'
            elif identifier_only(text, segments):
                kind, reason = 'identifier', '仅含缩写/专名标记与显式技术标识符，原样保留'
            elif not segments:
                kind, reason = 'protected', '代码、公式、引用或排版结构原样保留'
        except ValueError as exc:
            kind, reason = 'blocked', str(exc)
        items.append({'paragraph_id': block['id'], 'kind': kind, 'reason': reason,
                      'location': block.get('location', ''), 'source_hash': hashlib.sha256(text.encode()).hexdigest()})
        offset += len(text) + 2
    return {'version': VERSION, 'total': len(items), 'counts': dict(Counter(i['kind'] for i in items)),
            'items': items, 'note': '预检验证结构与保留规则，不保证语义翻译准确；未识别专名仍按正文校验。'}
