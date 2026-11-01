"""Verify live attachment names without submitting translation or rendering jobs."""
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import jobs
from app.exports import translation_filename


def fetch(path, headers=None):
    request = Request('http://127.0.0.1:8000/api' + path, headers=headers or {})
    with urlopen(request, timeout=180) as response:
        disposition = response.headers['Content-Disposition']
        assert disposition.startswith('attachment;') and "filename*=utf-8''" in disposition.lower()
        return response.status, unquote(disposition.split("''", 1)[1]), response.read()


def main():
    job_id = (ROOT / 'data/chinese-validation-job.txt').read_text(encoding='utf-8-sig').strip()
    record = jobs.job_record(job_id)
    title = record['paper']['title']
    translation_id = record['input']['translation_job_id']
    with jobs.connect() as db:
        count_before = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    status, name, pdf = fetch(f'/jobs/{job_id}/chinese.pdf')
    expected = translation_filename(title, 'pdf')
    assert status == 200 and name == expected and pdf.startswith(b'%PDF-')
    original_audit = json.loads((ROOT/'output/validation/chinese-pdf-validation.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(pdf).hexdigest() == original_audit['sha256']
    range_status, range_name, partial = fetch(f'/jobs/{job_id}/chinese.pdf', {'Range':'bytes=0-63'})
    assert range_status == 206 and range_name == expected and partial == pdf[:64]
    generic_status, generic_name, generic = fetch(f'/reports/pdf?job_id={job_id}')
    assert generic_status == 200 and generic_name == expected and generic == pdf
    bilingual_status, bilingual_name, bilingual = fetch(f'/reports/pdf?job_id={translation_id}')
    assert bilingual_status == 200 and bilingual_name == expected and bilingual.startswith(b'%PDF-')
    md_status, md_name, markdown = fetch(f'/jobs/{translation_id}/artifact')
    assert md_status == 200 and md_name == translation_filename(title, 'md')
    assert markdown.decode('utf-8') == jobs.get_job(translation_id)['result']['markdown']
    with jobs.connect() as db:
        count_after = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    assert count_before == count_after
    (ROOT/'output/pdf'/name).write_bytes(pdf)
    audit = {'chinese_pdf':name,'bilingual_pdf':bilingual_name,'markdown':md_name,
             'download_http':status,'range_http':range_status,'original_pdf_bytes_unchanged':True,
             'new_jobs':count_after-count_before,'generic_download_same_name_and_bytes':True}
    (ROOT/'output/validation/translation-download-names.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
