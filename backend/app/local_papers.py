"""Local PDF import: bounded binary upload, content identity and durable parse jobs."""
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import fulltext, jobs, repository as repo
from .exports import attachment_disposition

router = APIRouter(prefix='/api/local-papers', tags=['local-papers'])


def clean_filename(value: str) -> str:
    name = PurePosixPath(value.replace('\\', '/')).name
    name = ''.join(ch for ch in unicodedata.normalize('NFC', name) if not unicodedata.category(ch).startswith('C'))
    name = re.sub(r'[<>:"|?*]', '-', name).strip(' .')
    if not name.lower().endswith('.pdf') or not name[:-4].strip():
        raise HTTPException(415, '目前仅支持 PDF 文件，请选择 .pdf 论文。')
    return name[:180] if len(name) <= 180 else name[:-4][:176] + '.pdf'


def store_pdf(data: bytes, filename: str) -> dict:
    identifier = 'local-' + hashlib.sha256(data).hexdigest()
    url = f'/api/local-papers/{identifier}/file'
    now = repo.utc_now()
    paper = {'id':identifier, 'version':1, 'title':filename[:-4], 'title_source':'filename',
             'authors':[], 'abstract':'', 'published':'', 'updated':'', 'categories':['本地 PDF'],
             'url':url, 'pdf_url':url, 'source':'local', 'mode':'live', 'fetched_at':now,
             'uploaded_at':now, 'original_filename':filename, 'file_size':len(data)}
    # Store bytes and metadata together. Identical content keeps its original metadata and checkpoints.
    with repo.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existed = db.execute("SELECT 1 FROM papers WHERE mode='live' AND id=?", (identifier,)).fetchone() is not None
        if not existed:
            payload = json.dumps(paper, ensure_ascii=False)
            db.execute('INSERT INTO papers VALUES(?,?,?,?)', ('live', identifier, 1, payload))
            db.execute('INSERT INTO paper_versions VALUES(?,?,?,?,?)', ('live', identifier, 1, payload, now))
        db.execute('INSERT OR IGNORE INTO fulltext_assets VALUES(?,?,?)', (url, data, now))
        row = db.execute("SELECT payload FROM collections WHERE mode='live' AND paper_id=?", (identifier,)).fetchone()
        collection = json.loads(row['payload']) if row else repo.default_collection()
        collection['saved'] = True
        db.execute('INSERT OR REPLACE INTO collections VALUES(?,?,?,?)', ('live', identifier, json.dumps(collection, ensure_ascii=False), now))
    job = jobs.create_job(jobs.JobInput(skill_id='fulltext_fetch', paper_id=identifier, scope='fulltext'))
    from .main import public_paper
    return {'paper':public_paper(repo.get_paper(identifier)), 'job':job, 'reused':existed}


@router.post('', status_code=201)
async def upload(request: Request, filename: str = Query(min_length=1, max_length=500)):
    filename = clean_filename(filename)
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/pdf':
        raise HTTPException(415, '请以 PDF 文件上传。')
    length = request.headers.get('content-length')
    if length:
        if not length.isdigit():
            raise HTTPException(400, '文件长度无效。')
        if int(length) > fulltext.MAX_DOWNLOAD:
            raise HTTPException(413, 'PDF 超过 20 MiB 上传上限。')
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > fulltext.MAX_DOWNLOAD:
            raise HTTPException(413, 'PDF 超过 20 MiB 上传上限。')
        data.extend(chunk)
    if not data.startswith(b'%PDF-'):
        raise HTTPException(422, '文件为空或不是有效的 PDF，请勿仅修改扩展名。')
    return await run_in_threadpool(store_pdf, bytes(data), filename)


@router.get('/{paper_id}/file')
def original_file(paper_id: str):
    paper = repo.get_paper(paper_id)
    if not paper or paper.get('source') != 'local':
        raise HTTPException(404, '本地论文不存在。')
    data = fulltext.cached_asset(paper['pdf_url'])
    if data is None:
        raise HTTPException(410, '本地原文件不存在，请重新上传。')
    return Response(data, media_type='application/pdf', headers={
        'Content-Disposition':attachment_disposition(paper['original_filename']),
        'X-Content-Type-Options':'nosniff', 'Cache-Control':'private, no-store'})


@router.get('')
def list_local_papers():
    # Upload history is independent of the saved/unsaved collection toggle.
    with repo.connect() as db:
        rows = db.execute("SELECT id,payload FROM papers WHERE mode='live'").fetchall()
    papers = [repo.get_paper(row['id']) for row in rows if json.loads(row['payload']).get('source') == 'local']
    from .main import public_paper
    return {'papers': [public_paper(p) for p in sorted(papers, key=lambda p: p.get('uploaded_at', ''), reverse=True)]}


def local_document(paper_id):
    paper = repo.get_paper(paper_id)
    if not paper or paper.get('source') != 'local':
        raise HTTPException(404, '本地论文不存在。')
    document = fulltext.get_document(paper)
    return paper, document


def normalized_text(text):
    return ' '.join(text.split())


def abstract_candidate(text):
    # Only explicit headings, bounded by a following section; never guess from the first paragraph.
    start = re.search(r'(?im)^\s*(?:abstract|摘\s*要)\s*[:：.—-]?\s*', text)
    if not start:
        return ''
    tail = text[start.end():]
    end = re.search(r'(?im)^\s*(?:(?:1|I)[.\s]+)?(?:introduction|引言|绪论|keywords|key words|关键词)\b', tail)
    candidate = tail[:end.start()].strip() if end else ''
    return candidate if 40 <= len(candidate) <= 12000 else ''


@router.get('/{paper_id}/abstract')
def abstract_preview(paper_id: str):
    paper, document = local_document(paper_id)
    source = '\n\n'.join(b['text'] for b in document['blocks'] if b.get('page', 1) <= 3)[:30000] if document else ''
    return {'ready': bool(document), 'document_hash': document['document_hash'] if document else None,
            'candidate': abstract_candidate(source), 'source_text': source,
            'abstract': paper.get('abstract', ''), 'warnings': document.get('warnings', []) if document else [],
            'note': '仅展示前 3 页、最多 30,000 字符的已解析原文。请核对原 PDF 后选取摘要，保存不调用模型。'}


class AbstractInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=40, max_length=12000)
    document_hash: str = Field(min_length=1, max_length=128)


@router.put('/{paper_id}/abstract')
def save_abstract(paper_id: str, body: AbstractInput):
    paper, document = local_document(paper_id)
    if not document or document['document_hash'] != body.document_hash:
        raise HTTPException(409, '全文尚未解析或版本已变化，请刷新原文后重新核对。')
    text = normalized_text(body.text)
    source = normalized_text(' '.join(b['text'] for b in document['blocks']))
    if len(text) < 40 or text not in source:
        raise HTTPException(422, '摘要必须是已解析原文中的连续文本；请复制原文，不要补写或改写。')
    revision = hashlib.sha256(text.encode()).hexdigest()
    # Change only the confirmed abstract; preserve file identity, full-text and translation checkpoints.
    with repo.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = json.loads(db.execute("SELECT payload FROM papers WHERE mode='live' AND id=?", (paper_id,)).fetchone()['payload'])
        current.update(abstract=text, abstract_revision=revision, abstract_source={
            'method':'user_confirmed_pdf_excerpt', 'document_hash':document['document_hash'],
            'location':f"已解析全文规范化字符 {source.index(text)+1}–{source.index(text)+len(text)}", 'confirmed_at':repo.utc_now()})
        payload = json.dumps(current, ensure_ascii=False)
        db.execute("UPDATE papers SET payload=? WHERE mode='live' AND id=?", (payload,paper_id))
        db.execute("UPDATE paper_versions SET payload=? WHERE mode='live' AND id=? AND version=?", (payload,paper_id,current['version']))
    from .main import public_paper
    return public_paper(repo.get_paper(paper_id))
