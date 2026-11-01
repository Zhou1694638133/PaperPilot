"""Audit a completed Chinese PDF job without model calls or database writes."""
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import jobs, chinese_pdf as c
from pypdf import PdfReader


def fetch(path, *, data=None, headers=None):
    request = urllib.request.Request('http://127.0.0.1:8000/api' + path, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.status, response.headers, response.read()


def main():
    job_id = (ROOT / 'data/chinese-validation-job.txt').read_text(encoding='utf-8-sig').strip()
    record = jobs.job_record(job_id)
    assert record['status'] == 'succeeded', record['status']
    result = json.loads(record['result_json'])
    status, headers, pdf = fetch(f'/jobs/{job_id}/chinese.pdf')
    assert status == 200 and headers.get_content_type() == 'application/pdf'
    range_status, _, partial = fetch(f'/jobs/{job_id}/chinese.pdf', headers={'Range': 'bytes=0-63'})
    assert range_status == 206 and partial == pdf[:64]
    assert fetch(f'/reports/pdf?job_id={job_id}')[2] == pdf
    body = {k: record['input'][k] for k in ('skill_id', 'paper_id', 'mode', 'scope', 'translation_job_id')}
    duplicate = json.loads(fetch('/jobs', data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})[2])
    assert duplicate['id'] == job_id
    folder = c.directory(job_id)
    _, blob = c.source_files(record['input']['_chinese']['document'])
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        members = {m.name.removeprefix('./'):m for m in archive.getmembers()}
        for name in result['images']:
            assert (folder/name).read_bytes() == archive.extractfile(members[name]).read()
    reader = PdfReader(io.BytesIO(pdf))
    texts = [page.extract_text() for page in reader.pages]
    text = '\n'.join(texts)
    assert not re.search(r'\\(?:begin|end|frac|cite|includegraphics|textbf)\b', text)
    assert not any(word in text for word in ('PaperPilot','未独立复现实验','翻译检查点'))
    tex = (folder / 'paper.tex').read_text(encoding='utf-8')
    assert 'PPO（带 critic）' not in tex
    assert result['column_layout']['columns'] == 1
    assert result['column_layout']['method'] == 'original_pdf'
    assert 'twocolumn' not in tex
    assert r'\begin{figure*}' not in tex
    header_words = []
    for page in reader.pages:
        height = float(page.mediabox.height)
        def visit_header(value, cm, tm, font, size):
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            if value.strip() and y > height - 40:
                header_words.append(value.strip())
        page.extract_text(visitor_text=visit_header)
    assert not header_words, header_words
    assert '\u2022' not in text, 'Unexpected list bullets'
    fonts = sorted({str(f.get_object().get('/BaseFont')) for page in reader.pages for f in page['/Resources'].get('/Font',{}).values()})
    assert any('SimSun' in font for font in fonts)
    with jobs.connect() as db:
        chunks = [dict(row) for row in db.execute('SELECT * FROM translation_chunks WHERE job_id=? ORDER BY paragraph_id',(record['input']['translation_job_id'],))]
        old = {row['chunk_id']:row['payload'] for row in db.execute('SELECT chunk_id,payload FROM analysis_chunks WHERE job_id=?',('1a2697f8-0f64-417d-861e-06914c123fa3',))}
        current = {row['chunk_id']:row['payload'] for row in db.execute('SELECT chunk_id,payload FROM analysis_chunks WHERE job_id=?',(job_id,))}
    assert current == old, 'Final render must reuse all supplements without model calls'
    baseline = json.loads((ROOT/'output/validation/chinese-checkpoints-before.json').read_text())
    digest = hashlib.sha256(json.dumps(chunks,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    assert {'count':len(chunks),'sha256':digest} == baseline
    audit = dict(job_id=job_id,status='succeeded',pages=len(reader.pages),bytes=len(pdf),sha256=hashlib.sha256(pdf).hexdigest(),
                 original_images_byte_identical=len(result['images']),references=(folder/'paper.bbl').read_text().count('\\bibitem'),
                 original_translation_checkpoints_unchanged=baseline,raw_latex_visible=False,app_notes_visible=False,fonts=fonts,
                 download_http=status,range_http=range_status,generic_route_same_bytes=True,idempotent_submission=True,
                 layout_warnings=result['layout_warnings'],final_render_model_requests=0,
                 running_header_words=header_words,list_bullets=0,column_layout=result['column_layout'],
                 algorithm_pages=[i+1 for i,t in enumerate(texts) if '算法 1' in t or '算法1' in t])
    (ROOT/'output/pdf/g2po-chinese.pdf').write_bytes(pdf)
    (ROOT/'output/validation/chinese-pdf-validation.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in audit.items() if k!='fonts'},ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
