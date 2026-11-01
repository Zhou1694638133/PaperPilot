"""Cached bilingual typesetting from existing source and paid checkpoints only."""
import hashlib
import json
from pathlib import Path
import re
from threading import Lock

from fastapi import HTTPException
from fastapi.responses import FileResponse

from . import chinese_pdf as c, fulltext, jobs, repository as repo
from .config import ROOT, get_settings
from .exports import translation_filename
from .paper_layout import detect_columns
from .source_enrichment import expand_text, plain_macros

VERSION = '1.0.11'
_compile_lock = Lock()


def cached_supplement(item: dict, cache: dict) -> str:
    review = c.reviewed_text(item, cache)
    if review is not None:
        return review
    text = item['text']
    if item['kind'] in {'section', 'subsection', 'subsubsection'} and text in c.SECTION_TERMS:
        return c.SECTION_TERMS[text]
    if not item['translate']:
        return text
    digest = hashlib.sha256((c.VERSION + text).encode()).hexdigest()
    hit = cache.get((item['id'], digest))
    if hit:
        return hit['translation']
    groups = c.supplement_groups(item)
    if groups:
        return ''.join(cached_supplement(child, cache) for child in groups)
    # Missing caption/header supplements stay in the source language. Never call a model on download.
    return text


def merge_protected(source: str, translated: str) -> str:
    """Share figures/formulas/numbers once, with original and Chinese text in each cell/caption."""
    if source == translated:
        return source
    original, tokens = c.mask(source, grouping=True)
    target, target_tokens = c.mask(translated, grouping=True)
    if tokens != target_tokens:
        raise ValueError('双语图表的保护结构不一致，不能猜测或丢弃原文')
    # Numbers inside prose belong to each language's phrase (e.g. GPT-4).
    # Pure numeric cells still compare equal and are emitted only once.
    from .skills import NUMERIC_PATTERN
    for index, token in enumerate(tokens):
        if re.fullmatch(NUMERIC_PATTERN, token):
            original = original.replace(f'⟦{index}⟧', token)
            target = target.replace(f'⟦{index}⟧', token)
    left = re.split(r'(⟦\d+⟧)', original)
    right = re.split(r'(⟦\d+⟧)', target)
    for index in range(0, len(left), 2):
        en, zh = left[index].strip(), right[index].strip()
        if en and zh and en != zh:
            left[index] = left[index].replace(en, en + ' / ' + zh)
    merged = ''.join(left)
    return re.sub(r'⟦(\d+)⟧', lambda match: tokens[int(match[1])], merged)


def flatten_itemize(text: str) -> str:
    # Paragraph checkpoints may split a list around a display equation.
    text = re.sub(r"\\begin\{itemize\}(?:\[[^]]*\])?|\\end\{itemize\}", "", text)
    return re.sub(r"\\item(?:\[([^]]*)\])?", lambda m: r"\par " + (m[1] or "") + " ", text)


def paragraph_pair(source: str, translated: str) -> str:
    c.validate_tex(source)
    c.validate_tex(translated)
    source, translated = flatten_itemize(source), flatten_itemize(translated)
    # Only the translated copy owns labels; inline citations remain in both languages.
    source = re.sub(r'\\label\{[^}]+\}', '', source)
    return (r'\par\noindent{\small\bfseries English}\par\nopagebreak[4]' + '\n'
            + r'{\fontsize{11.5}{20}\selectfont ' + source + r'\par}' + '\n'
            + r'\smallskip\noindent{\small\bfseries 中文}\par\nopagebreak[4]' + '\n'
            + r'{\fontsize{12}{20}\selectfont ' + translated + r'\par}\medskip' + '\n')


def build_body(snapshot: dict, paper: dict, cache: dict) -> tuple[str, str, str]:
    files, _ = c.source_files(snapshot['document'])
    macros, _ = plain_macros(files)
    blocks = {block['id']:block for block in snapshot['blocks'] if not block.get('supplemental')}
    output, title, authors = [], c.escape(paper['title']), c.escape(' · '.join(paper['authors']))
    pending_box: list[tuple[str, str]] = []
    box_depth = 0
    original_items = {item['id']: item for item in c.parts(snapshot, paper)}
    macro_values = {name: cached_supplement(item, cache) for name, item in c.macro_supplements(snapshot).items()}
    for item in c.parts(snapshot, paper, macro_values):
        value = c.normalize_table_terms(item['text'], c.restore_table_headers(item['text'], c.restore_table_rules(item['text'], cached_supplement(item, cache))))
        c.validate_tex(value)
        if pending_box and item['kind'] != 'body':
            raise ValueError('跨段提示框内出现章节或元数据，需核对结构')
        if item['kind'] == 'title':
            title = item['text']
            if value != title:
                title += r'\\[0.5em]' + value
        elif item['kind'] == 'author':
            authors = value
        elif item['kind'] == 'metadata':
            output.append(value)
        elif item['kind'] in {'section', 'subsection', 'subsubsection'}:
            heading = item['text'] if item['text'] == value else item['text'] + ' / ' + value
            c.validate_tex(heading)
            output.append(f"\\{item['kind']}{{{heading}}}" + item.get('labels', ''))
        else:
            block = blocks[item['id']]
            from .pdf_compat import normalize
            source = (normalize(expand_text(block.get('display_text') or block['text'], macros))
                      if block.get('translatable', True) else original_items[item['id']]['text'])
            # A language font group must never close inside an open tcolorbox.
            # Collect the complete box, including protected formula/code blocks.
            boundaries = re.findall(r"\\(begin|end)\{tcolorbox\}", source)
            if pending_box or boundaries:
                if boundaries != re.findall(r"\\(begin|end)\{tcolorbox\}", value):
                    raise ValueError('双语提示框边界与原文不一致')
                for boundary in boundaries:
                    box_depth += 1 if boundary == 'begin' else -1
                    if box_depth < 0:
                        raise ValueError('提示框结束标记缺少对应起点')
                pending_box.append((source, value))
                if box_depth == 0:
                    output.append(paragraph_pair('\n\n'.join(pair[0] for pair in pending_box),
                                                 '\n\n'.join(pair[1] for pair in pending_box)))
                    pending_box = []
                continue
            if block.get('translatable', True):
                for environment, heading in (("abstract", "Abstract / 摘要"), ("ack", "Acknowledgements / 致谢")):
                    if f"\\begin{{{environment}}}" in source:
                        output.append(r"\section*{" + heading + "}")
                    for command in (f"\\begin{{{environment}}}", f"\\end{{{environment}}}"):
                        source, value = source.replace(command, ""), value.replace(command, "")
                if r'\includegraphics' in source:
                    shared = merge_protected(source, value)
                    c.validate_tex(shared)
                    output.append(shared)
                else:
                    output.append(paragraph_pair(source, value))
            else:
                # Source is already macro-expanded and normalized by the Chinese runner.
                shared = merge_protected(original_items[item['id']]['text'], value)
                c.validate_tex(shared)
                output.append(shared)
    if pending_box:
        raise ValueError('跨段提示框未闭合，不能输出不完整双语 PDF')
    return title, authors, flatten_itemize('\n\n'.join(output))


TABLE_FIT = r"\newcommand{\fitbilingualtable}[1]{\sbox0{#1}\ifdim\wd0>\linewidth\resizebox{\linewidth}{!}{\usebox0}\else\usebox0\fi}"


def fit_tables(body: str) -> str:
    """Shrink only tables wider than their available column; preserve every cell."""
    return re.sub(r"\\begin\{tabular\*?\}.*?\\end\{tabular\*?\}",
                  lambda match: r"\fitbilingualtable{" + match[0] + "}", body, flags=re.S)


def export(job_id: str) -> FileResponse:
    record = jobs.job_record(job_id)
    if record['skill_id'] != 'paper_translator' or record['status'] != 'succeeded':
        raise HTTPException(409, '请先完成全文翻译，再下载双语 PDF')
    snapshot = c.translation_snapshot(record['paper'], job_id)
    cache = {}
    with repo.connect() as db:
        candidates = db.execute("SELECT id,input_json FROM jobs WHERE skill_id='chinese_pdf' AND status='succeeded' AND json_extract(input_json,'$.translation_job_id')=? ORDER BY created_at DESC", (job_id,)).fetchall()
        for row in candidates:
            previous = json.loads(row['input_json'])['_chinese']
            if previous['glossary'] != snapshot['glossary'] or previous['document']['document_hash'] != snapshot['document']['document_hash']:
                continue
            for chunk in db.execute("SELECT * FROM analysis_chunks WHERE job_id=? AND chunk_id NOT LIKE 'rejected-%'", (row['id'],)):
                cache.setdefault((chunk['chunk_id'], chunk['source_hash']), json.loads(chunk['payload']))
    files, blob = c.source_files(snapshot['document'])
    source_pdf = fulltext.cached_asset(record["paper"]["pdf_url"] if record["paper"].get("source") == "local" else f"https://arxiv.org/pdf/{record['paper']['id']}v{record['paper']['version']}")
    columns = detect_columns(files, source_pdf)
    style_path = ROOT / 'skills/paper_translator/templates/bilingual-style.json'
    identity = {'version':VERSION, 'snapshot':snapshot, 'cache':sorted((a,b,v['translation']) for (a,b),v in cache.items()),
                'template':c.preamble(columns['columns']), 'style':style_path.read_text(encoding='utf-8-sig')}
    fingerprint = hashlib.sha256(jobs.encode(identity).encode()).hexdigest()
    folder = get_settings().database_path.parent / 'bilingual-pdf' / job_id / fingerprint
    metadata_path = folder / 'metadata.json'
    with _compile_lock:
        if not (metadata_path.is_file() and (folder/'paper.pdf').is_file()):
            folder.mkdir(parents=True, exist_ok=True)
            title, authors, body = build_body(snapshot, record['paper'], cache)
            images = c.assets(blob, files, folder, body)
            # The two frozen column templates stay unchanged; this export adds paired-language typography.
            preamble = c.render_preamble(columns['columns'], body).replace(
                r'\begin{document}', TABLE_FIT + '\n' +
                r'\ctexset{section={format=\fontsize{15}{22}\selectfont\bfseries},subsection={format=\fontsize{12}{20}\selectfont\bfseries}}' + '\n' +
                r'\begin{document}\linespread{1}\fontsize{12}{20}\selectfont\setlength{\parindent}{24pt}')
            preamble += '\n' + r'\hypersetup{pdftitle={' + c.escape(record['paper']['title']) + '}}\n'
            source = preamble + r'\title{' + title + '}\n' + r'\author{\parbox{0.95\textwidth}{\centering\small ' + authors + '}}\n\\date{}\n\\maketitle\n' + fit_tables(c.layout(body, columns['columns'])) + '\n\\end{document}\n'
            (folder/'paper.tex').write_text(source, encoding='utf-8')
            warnings = c.compile_pdf(folder, lambda: None)
            from pypdf import PdfReader
            metadata = {'version':VERSION, 'translation_job_id':job_id, 'columns':columns['columns'], 'images':images,
                        'pages':len(PdfReader(folder/'paper.pdf').pages), 'layout_warnings':warnings,
                        'paired_blocks':body.count(r'\bfseries English'),
                        'model_calls':0, 'sha256':hashlib.sha256((folder/'paper.pdf').read_bytes()).hexdigest()}
            temporary = folder/'metadata.tmp'
            temporary.write_text(jobs.encode(metadata), encoding='utf-8')
            temporary.replace(metadata_path)
    return FileResponse(folder/'paper.pdf', media_type='application/pdf', filename=translation_filename(record['paper']['title'], 'pdf'))
