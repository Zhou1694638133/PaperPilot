"""Recover exact-version arXiv sources for a local PDF without trusting its filename."""
import hashlib
import io
import json
import re

from pypdf import PdfReader
from . import fulltext, jobs, repository as repo

VERSION = '1.0.0'


def arxiv_reference(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    if not reader.pages or len(reader.pages) > 150:
        raise ValueError('本地 PDF 页数无效')
    refs = set(re.findall(r'arXiv\s*:\s*(\d{4}\.\d{4,5}v[1-9]\d*)\b', reader.pages[0].extract_text() or '', re.I))
    if len(refs) != 1:
        raise ValueError('PDF 首页没有唯一、带版本的 arXiv 标识。请提供对应版本的 arXiv 原 PDF；不会根据文件名猜测源文件。')
    return refs.pop()


def verify_pdf(local: bytes, remote: bytes, check=lambda: None) -> None:
    if hashlib.sha256(local).digest() == hashlib.sha256(remote).digest():
        return
    left, right = PdfReader(io.BytesIO(local)), PdfReader(io.BytesIO(remote))
    if len(left.pages) != len(right.pages):
        raise ValueError('同版本 arXiv PDF 与本地文件页数不一致，未替换原文')
    for a, b in zip(left.pages, right.pages):
        check()
        normalize = lambda page: re.sub(r'\s+', '', page.extract_text() or '')
        original, candidate = normalize(a), normalize(b)
        if len(original) < 40 or original != candidate:
            raise ValueError('本地 PDF 与同版本 arXiv 正文不一致或无法完整核对，未替换原文')


def run(record: dict, owner: str) -> dict:
    paper = record['paper']
    check = lambda: jobs.ensure_running(record['id'], owner)
    data = fulltext.cached_asset(paper['pdf_url'])
    if not data:
        raise ValueError('本地原 PDF 缓存缺失，请重新上传')
    reference = arxiv_reference(data)
    pdf_url, source_url = f'https://arxiv.org/pdf/{reference}', f'https://arxiv.org/src/{reference}'
    current = fulltext.get_document(paper)
    if current and current['method'] == 'latex' and current.get('source_url') == source_url:
        document = current
    else:
        jobs.update_progress(record['id'], owner, '核对同版本 arXiv PDF 与本地原件', 15)
        remote = fulltext.cached_asset(pdf_url) or fulltext.download(pdf_url, check)
        verify_pdf(data, remote, check)
        check()
        fulltext.save_asset(pdf_url, remote)
        jobs.update_progress(record['id'], owner, '获取含原图的 LaTeX 源文件', 40)
        source = fulltext.cached_asset(source_url) or fulltext.download(source_url, check)
        blocks, warnings = fulltext.parse_latex(source)
        from .pdf_compat import inline_verbatim
        for block in blocks:
            block['text'] = inline_verbatim(block['text'])
        document = fulltext.make_document(paper, blocks, warnings, 'latex', source_url, source)
        document['source_link'] = {'arxiv_reference':reference, 'pdf_url':pdf_url, 'verification':'all_pdf_pages_text_or_sha256', 'local_pdf_sha256':hashlib.sha256(data).hexdigest()}
        document['note'] = '已核对同版本 arXiv 原 PDF 并恢复结构化源文件；中文和双语 PDF 使用原有保图排版模板。原上传文件与历史译文检查点保留。'
        # Check all referenced images before changing the current document. Never publish a text-only success.
        from .chinese_pdf import source_files, assets, directory
        check()
        fulltext.save_asset(source_url, source)
        files, blob = source_files(document)
        folder = directory(record['id'])
        folder.mkdir(parents=True, exist_ok=True)
        images = assets(blob, files, folder, '\n'.join(b['text'] for b in blocks))
        document['source_link']['image_assets'] = images
        check()
        fulltext.save_document(paper, document)
    result = {'coverage':'fulltext','mode':paper['mode'],'skill_version':VERSION,
              'document_hash':document['document_hash'],'paragraph_count':document['paragraph_count'],
              'source':source_url,'warnings':document['warnings'],
              'markdown':f"# 原图与源文件恢复 · {paper['title']}\n\n来源：{source_url}\n\n已核对同版本原 PDF，原图和结构化源文件已保存。旧译文检查点保留；正文完全相同且模型、术语配置相同的段落才自动复用。其余段落需按结构化源文件翻译。"}
    jobs.save_checkpoint(record['id'], owner, result)
    return result


def reusable_chunks(record: dict) -> dict:
    """Reuse only byte-identical paragraphs and identical translation context."""
    if record['paper'].get('source') != 'local' or not record['input'].get('_document', {}).get('source_link'):
        return {}
    output = {}
    with repo.connect() as db:
        rows = db.execute("SELECT id,input_json FROM jobs WHERE paper_id=? AND mode=? AND skill_id='paper_translator' AND model=? AND prompt_version=? AND id<>? ORDER BY created_at DESC", (record['paper']['id'],record['mode'],record['model'],record['prompt_version'],record['id'])).fetchall()
        for row in rows:
            previous = json.loads(row['input_json'])
            if previous.get('scope') != 'fulltext' or any(previous.get(key) != record['input'].get(key) for key in ('glossary','glossary_version','_provider_url')):
                continue
            for chunk in db.execute('SELECT * FROM translation_chunks WHERE job_id=?', (row['id'],)):
                output.setdefault((chunk['source_hash'],chunk['source']),dict(chunk))
    return output
