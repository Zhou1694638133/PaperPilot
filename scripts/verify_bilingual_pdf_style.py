"""Audit a real bilingual PDF export without changing translation checkpoints."""
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
from urllib.parse import unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from app import jobs, chinese_pdf as c
from app.exports import translation_filename
from pypdf import PdfReader


def main():
    typeset_id = (ROOT/'data/chinese-validation-job.txt').read_text(encoding='utf-8-sig').strip()
    translation_id = jobs.job_record(typeset_id)['input']['translation_job_id']
    record = jobs.job_record(translation_id)
    with jobs.connect() as db:
        before = [dict(row) for row in db.execute('SELECT * FROM translation_chunks WHERE job_id=? ORDER BY paragraph_id',(translation_id,))]
        count_before = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    with urlopen(f'http://127.0.0.1:8000/api/reports/pdf?job_id={translation_id}', timeout=240) as response:
        assert response.status == 200 and response.headers.get_content_type() == 'application/pdf'
        name = unquote(response.headers['Content-Disposition'].split("''",1)[1])
        pdf = response.read()
    assert name == translation_filename(record['paper']['title'], 'pdf')
    reader = PdfReader(io.BytesIO(pdf))
    texts = [page.extract_text() for page in reader.pages]
    text = '\n'.join(texts)
    assert 'Group-based Reinforcement Learning' in text
    assert '基于组的强化学习' in re.sub(r'\s+', '', text)
    assert 'PaperPilot | 来源证据与研究笔记' not in text
    assert reader.metadata.title == record['paper']['title']
    fonts = sorted({str(font.get_object().get('/BaseFont')) for page in reader.pages for font in page['/Resources']['/Font'].values()})
    assert any('SimSun' in font for font in fonts)
    assert any('TimesNewRoman' in font for font in fonts)
    for page in reader.pages:
        colour, stack = True, []
        for operands, operator in page.get_contents().operations:
            if operator == b'q':
                stack.append(colour)
            elif operator == b'Q':
                colour = stack.pop() if stack else True
            elif operator in {b'rg', b'g'}:
                colour = all(float(value) == 0 for value in operands)
            elif operator == b'k':
                colour = list(map(float, operands)) == [0, 0, 0, 1]
            elif operator in {b'Tj', b'TJ', b"'", b'"'}:
                assert colour, 'Non-black newly typeset text'
    assert not re.search(r"\\(?:begin|end|includegraphics|textbf|label|cite|frac)\b", text)
    url=f'http://127.0.0.1:8000/api/reports/pdf?job_id={translation_id}'
    with urlopen(Request(url, headers={'Range':'bytes=0-1023'}), timeout=60) as response:
        assert response.status == 206 and response.read() == pdf[:1024]
    with urlopen(url, timeout=60) as response:
        assert response.read() == pdf
    cache_path=Path((ROOT/'data/bilingual-validation-path.txt').read_text(encoding='utf-8').strip())
    metadata=json.loads(cache_path.with_name('metadata.json').read_text(encoding='utf-8'))
    assert not metadata['layout_warnings'], metadata['layout_warnings']
    assert len(metadata['images']) == 10 and metadata['paired_blocks'] == 92
    assert hashlib.sha256(pdf).hexdigest() == metadata['sha256']
    snapshot=c.translation_snapshot(record['paper'],translation_id)
    _,blob=c.source_files(snapshot['document'])
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:*') as archive:
        members={member.name.removeprefix('./'):member for member in archive.getmembers() if member.isfile()}
        for image_name in metadata['images']:
            original=archive.extractfile(members[image_name]).read()
            assert (cache_path.parent/image_name).read_bytes() == original
    bbl=cache_path.with_suffix('.bbl').read_text(encoding='utf-8')
    assert len(re.findall(r'\\bibitem\b',bbl)) == 64

    with jobs.connect() as db:
        after = [dict(row) for row in db.execute('SELECT * FROM translation_chunks WHERE job_id=? ORDER BY paragraph_id',(translation_id,))]
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == count_before
    assert before == after
    digest=hashlib.sha256(json.dumps(after,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    baseline=json.loads((ROOT/'output/validation/chinese-checkpoints-before.json').read_text(encoding='utf-8'))
    assert digest==baseline['sha256']
    destination=ROOT/'output/bilingual'/name
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(pdf)
    audit={'job_id':translation_id,'filename':name,'pages':len(reader.pages),'bytes':len(pdf),'fonts':fonts,
           'new_text_black':True,'raw_latex_absent':True,'range_http':206,'cached_repeat_identical':True,'typesetting':metadata,'report_footer_removed':True,'download_http':200,
           'checkpoints_unchanged':len(after),'checkpoint_sha256':digest,'new_jobs':0,'original_images_identical':10,'references':64,'path':str(destination)}
    (ROOT/'output/validation/bilingual-pdf-style.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
