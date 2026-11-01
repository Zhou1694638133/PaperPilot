import hashlib
import json
import re
import sqlite3
from urllib.parse import unquote

import pytest

from app import chinese_pdf as c, jobs, reading_api, pdf_export
from app.exports import translation_filename, attachment_disposition


def filename(response):
    return unquote(response.headers['content-disposition'].split("''", 1)[1])


def test_two_frozen_skill_templates_match_runner_and_manifest():
    manifest_path = c.TEMPLATE_ROOT.parent / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['template_version'] == c.LAYOUT_VERSION
    assert manifest['render_version'] == c.RENDER_VERSION
    assert len(manifest['templates']) == 2
    for item in manifest['templates']:
        path = manifest_path.parent / item['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        assert c.preamble(item['columns']) == path.read_text(encoding='utf-8')
        assert c.TEMPLATE_IDS[item['columns']] == item['id']
    assert c.preamble(2) == c.preamble(1).replace('fontset=none,10pt', 'fontset=none,10pt,twocolumn')


@pytest.mark.parametrize('extension', ['pdf', 'md'])
def test_filename_keeps_original_title_and_unicode(extension):
    name = translation_filename('An Agent Study 中文', extension)
    assert name == f'An Agent Study 中文翻译版.{extension}'
    assert unquote(attachment_disposition(name).split("''", 1)[1]) == name


def test_filename_removes_paths_controls_and_bounds_unicode_length():
    title = '../Agent: A/B\\C?*<>|"\r\nInjected\u202e.'
    name = translation_filename(title, 'pdf')
    assert not re.search(r'[<>:"/\\|?*\r\n\u202e]', name)
    assert not name.startswith('.')
    assert len(translation_filename('长标题' * 300, 'pdf').encode('utf-8')) < 240
    assert translation_filename(' ... ', 'md') == '论文翻译版.md'
    with pytest.raises(ValueError):
        translation_filename('Title', '../pdf')


def test_chinese_pdf_download_header_uses_original_snapshot_title(tmp_path, monkeypatch):
    (tmp_path / 'paper.pdf').write_bytes(b'%PDF-1.7 test')
    monkeypatch.setattr(c, 'directory', lambda _: tmp_path)
    monkeypatch.setattr(jobs, 'get_job', lambda _: {'skill_id':'chinese_pdf', 'status':'succeeded'})
    monkeypatch.setattr(jobs, 'job_record', lambda _: {'paper':{'title':'Original Paper Title'}})
    response = reading_api.chinese_pdf_download('job')
    assert filename(response) == 'Original Paper Title翻译版.pdf'


def test_bilingual_pdf_download_uses_same_title_convention(monkeypatch):
    monkeypatch.setattr(jobs, 'get_job', lambda _: {'skill_id':'paper_translator', 'result':{'markdown':'译文'}})
    monkeypatch.setattr(jobs, 'job_record', lambda _: {'paper':{'title':'Original Title'}, 'input':{}})
    def render(markdown, *, profile, title):
        assert profile == 'bilingual' and title == 'Original Title'
        return b'%PDF-1.7 test'
    monkeypatch.setattr(pdf_export, 'render_pdf', render)
    response = reading_api.report_pdf(job_id='job', ids='')
    assert filename(response) == 'Original Title翻译版.pdf'
    assert response.body == b'%PDF-1.7 test'


@pytest.mark.parametrize('skill', ['paper_translator', 'chinese_pdf'])
def test_translation_markdown_download_uses_title(tmp_path, monkeypatch, skill):
    path = tmp_path / 'artifact.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE job_artifacts (job_id TEXT, content TEXT)')
        db.execute('INSERT INTO job_artifacts VALUES (?,?)', ('job', '# 译文'))
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setattr(jobs, 'connect', connect)
    monkeypatch.setattr(jobs, 'get_job', lambda _: {'skill_id':skill})
    monkeypatch.setattr(jobs, 'job_record', lambda _: {'paper':{'title':'Original Title'}, 'input':{}})
    response = jobs.artifact('job')
    assert filename(response) == 'Original Title翻译版.md'
    assert response.body.decode() == '# 译文'


def test_latex_bilingual_uses_typesetter_and_surfaces_failure(monkeypatch):
    from app import bilingual_pdf
    from fastapi import HTTPException
    monkeypatch.setattr(jobs, 'get_job', lambda _: {'skill_id':'paper_translator', 'result':{'markdown':'译文'}})
    monkeypatch.setattr(jobs, 'job_record', lambda _: {'input':{'_document':{'method':'latex'}}})
    marker=object()
    monkeypatch.setattr(bilingual_pdf, 'export', lambda _: marker)
    assert reading_api.report_pdf(job_id='job', ids='') is marker
    def fail(_):
        raise ValueError('排版失败')
    monkeypatch.setattr(bilingual_pdf, 'export', fail)
    with pytest.raises(HTTPException) as exc:
        reading_api.report_pdf(job_id='job', ids='')
    assert exc.value.status_code == 409 and exc.value.detail == '排版失败'



def test_chinese_text_pdf_contains_only_saved_translation(monkeypatch):
    import io
    from pypdf import PdfReader
    from app import llm
    monkeypatch.setattr(jobs, 'get_job', lambda _: {'skill_id':'paper_translator', 'status':'succeeded', 'result':{'markdown':'ignored'}})
    monkeypatch.setattr(jobs, 'job_record', lambda _: {'paper':{'title':'Local Agent'}, 'input':{'_document':{'method':'pdf'}}})
    monkeypatch.setattr(reading_api, 'alignment', lambda _: {'completed':1, 'total':1, 'blocks':[{'source':'English source must not be included', 'translation':'智能体通过环境反馈学习策略。'}]})
    monkeypatch.setattr(llm, 'request_json', lambda *a, **kw: pytest.fail('Downloads must not call a model'))
    response = reading_api.chinese_text_pdf_download('job')
    assert response.media_type == 'application/pdf'
    assert filename(response) == 'Local Agent翻译版.pdf'
    text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(response.body)).pages)
    assert '智能体通过环境反馈学习策略' in text
    assert 'English source must not be included' not in text
    assert 'PaperPilot' not in text and '原文' not in text


@pytest.mark.parametrize('status,partial,completed,method', [
    ('failed',False,1,'pdf'), ('running',False,1,'pdf'),
    ('succeeded',True,1,'pdf'), ('succeeded',False,0,'pdf'),
    ('succeeded',False,1,'latex'),
])
def test_chinese_text_pdf_rejects_unfinished_or_structured_source(monkeypatch,status,partial,completed,method):
    from fastapi import HTTPException
    monkeypatch.setattr(jobs, 'get_job', lambda _: {'skill_id':'paper_translator','status':status,'result':{'partial':partial}})
    monkeypatch.setattr(jobs, 'job_record', lambda _: {'input':{'_document':{'method':method}}})
    monkeypatch.setattr(reading_api, 'alignment', lambda _: {'completed':completed,'total':1,'blocks':[{}]})
    with pytest.raises(HTTPException) as error:
        reading_api.chinese_text_pdf_download('job')
    assert error.value.status_code == 409
