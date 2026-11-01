import io
import json
import tarfile
import hashlib

import pytest
from reportlab.pdfgen import canvas
from app import fulltext, jobs, local_source, repository as repo
from app.config import get_settings
from app.worker import process_one
from test_local_papers import client, upload, TEXT


def local_pdf(reference='2503.13657v3', extra=''):
    out=io.BytesIO(); c=canvas.Canvas(out)
    c.drawString(40,780,'arXiv:'+reference)
    t=c.beginText(40,740)
    for text in [TEXT[i:i+65] for i in range(0,len(TEXT),65)]:t.textLine(text)
    t.textLine(extra);c.drawText(t);c.save();return out.getvalue()


def archive(missing=False):
    tex=r"\documentclass[onecolumn]{article}\begin{document}\section{Method}"+'\n'+TEXT+'\n'+r"\begin{figure}\includegraphics{figure.pdf}\caption{Example}\end{figure}\end{document}"
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w:gz') as tar:
        for name,content in [('main.tex',tex.encode()),*([] if missing else [('figure.pdf',local_pdf())])]:
            item=tarfile.TarInfo(name);item.size=len(content);tar.addfile(item,io.BytesIO(content))
    return out.getvalue()


def test_identifier_requires_unique_version_and_pdf_text_matches():
    assert local_source.arxiv_reference(local_pdf())=='2503.13657v3'
    with pytest.raises(ValueError):local_source.arxiv_reference(local_pdf('2503.13657'))
    with pytest.raises(ValueError):local_source.arxiv_reference(local_pdf(extra='arXiv:1234.12345v1'))
    local_source.verify_pdf(local_pdf(),local_pdf())
    with pytest.raises(ValueError):local_source.verify_pdf(local_pdf(),local_pdf(extra='changed body'))


def setup_local(client):
    data=local_pdf();imported=upload(client,data).json();assert process_one('parse-local')
    paper=repo.get_paper(imported['paper']['id']);return data,paper,fulltext.get_document(paper)


def test_recovery_keeps_original_and_restores_images_without_model(client,monkeypatch):
    data,paper,before=setup_local(client)
    monkeypatch.setattr(fulltext,'download',lambda url,check: data if '/pdf/' in url else archive())
    body={'paper_id':paper['id'],'skill_id':'local_source_fetch','scope':'fulltext'}
    response=client.post('/api/jobs',json=body);assert response.status_code==201,response.text
    job=response.json();assert client.post('/api/jobs',json=body).json()['id']==job['id']
    assert process_one('source-worker')
    assert jobs.get_job(job['id'])['status']=='succeeded',jobs.get_job(job['id'])
    document=fulltext.get_document(paper)
    assert document['method']=='latex' and document['document_hash']!=before['document_hash']
    assert document['source_link']['image_assets']==['figure.pdf']
    assert fulltext.cached_asset(paper['pdf_url'])==data
    assert repo.get_paper(paper['id'])['collection']['saved']
    assert client.get('/api/fulltext',params={'paper_id':paper['id']}).json()['document']['method']=='latex'


@pytest.mark.parametrize('problem',['mismatch','missing_image','network'])
def test_failed_recovery_keeps_previous_document(client,monkeypatch,problem):
    data,paper,before=setup_local(client)
    def download(url,check):
        if problem=='network':raise ValueError('offline')
        if '/pdf/' in url:return local_pdf(extra='different paper') if problem=='mismatch' else data
        return archive(missing=True)
    monkeypatch.setattr(fulltext,'download',download)
    job=client.post('/api/jobs',json={'paper_id':paper['id'],'skill_id':'local_source_fetch','scope':'fulltext'}).json()
    assert process_one('source-failure')
    assert jobs.get_job(job['id'])['status']=='failed'
    assert fulltext.get_document(paper)['document_hash']==before['document_hash']
    assert fulltext.cached_asset(paper['pdf_url'])==data


def test_reuse_is_exact_and_context_bound(client,monkeypatch):
    _,paper,_=setup_local(client)
    monkeypatch.setenv('LLM_API_KEY','test');monkeypatch.setenv('LLM_MODEL','test');get_settings.cache_clear()
    job=client.post('/api/jobs',json={'paper_id':paper['id'],'skill_id':'paper_translator','scope':'fulltext','acknowledge_partial':True}).json()
    record=jobs.job_record(job['id']);source='Exact paragraph';digest=hashlib.sha256(source.encode()).hexdigest()
    with repo.connect() as db:
        db.execute('INSERT INTO translation_chunks VALUES(?,?,?,?,?,?,?)',(job['id'],'old-id',digest,source,'已有中文译文',None,repo.utc_now()))
    current={**record,'id':'new-job','input':{**record['input'],'_document':{'source_link':{'arxiv_reference':'2503.13657v3'}}}}
    assert local_source.reusable_chunks(current)[(digest,source)]['translation']=='已有中文译文'
    current['input']['glossary_version']=987
    assert local_source.reusable_chunks(current)=={}



def test_bibliography_normalization_does_not_allow_unsafe_entry_commands():
    from app.chinese_pdf import bibliography_entries, validate_tex
    source=r"\solutions @article{key,title={Don{\textquoteright}t},year={2025}}"
    normalized=bibliography_entries(source)
    assert 'solutions' not in normalized and '2025' in normalized
    validate_tex(normalized)
    with pytest.raises(ValueError):validate_tex(bibliography_entries(r'@article{x,title={\input{secret}}}'))
    with pytest.raises(ValueError):bibliography_entries('@article{x,title={unclosed}')
    with pytest.raises(ValueError):bibliography_entries('@preamble{unsafe}')



def test_format_only_macros_expand_without_executing_tex():
    from app.source_enrichment import plain_macros,expand_text
    safe,unknown=plain_macros({'macros.tex':r"\newcommand{\dataset}{\textbf{\texttt{{MAST-Data}}}\xspace}\newcommand{\number}{14\xspace}\newcommand{\unsafe}{\input{secret}}"})
    assert expand_text(r'\dataset{} \number',safe)=='MAST-Data 14'
    assert unknown==['unsafe']


def test_inline_verbatim_and_bbl_reference_rendering():
    from app.pdf_compat import inline_verbatim,adapt
    assert inline_verbatim(r'Use \verb|pip install agentdash|')==r'Use \texttt{pip install agentdash}'
    blocks=[{'id':'bbl','location':'main.bbl:1','text':'generated'}]
    items=[{'id':'bib','text':r'\bibliography{main}','kind':'body','translate':False}, {'id':'bbl','text':'generated','kind':'body','translate':False}]
    assert len(adapt(items,blocks,{'main.bib':'@article{x,title={Test}}'}))==1



def test_fontawesome_is_loaded_only_for_allowlisted_source_icon():
    from app.chinese_pdf import render_preamble,preamble,validate_tex
    for columns in (1,2):
        assert render_preamble(columns,'正文')==preamble(columns)
        value=render_preamble(columns,r'\faLightbulbO')
        assert value.count(r'\usepackage{fontawesome}')==1
        assert value.index(r'\usepackage{fontawesome}') < value.index(r'\begin{document}')
    validate_tex(r'\faLightbulbO')
    with pytest.raises(ValueError):validate_tex(r'\faUntrustedCommand')


def test_publisher_box_colours_use_black_text_template():
    from app.pdf_compat import normalize
    source = r"\begin{tcolorbox}[colback=customblue!5!white, colframe=customblue!100!black, coltext=customblue, boxrule=0.5mm]Kept \faLightbulbO\end{tcolorbox}"
    value = normalize(source)
    assert 'colback=white' in value and 'colframe=black' in value and 'coltext=black' in value
    assert 'boxrule=0.5mm' in value and r'Kept \faLightbulbO' in value
    assert normalize(value) == value


def test_local_source_title_and_filename_fallback_are_safe(monkeypatch):
    from app import chinese_pdf as c, bilingual_pdf as b
    paper = {'source': 'local', 'title': 'R1_2503_Article', 'authors': []}
    snapshot = {'document': {}, 'blocks': []}
    monkeypatch.setattr(c, 'source_files', lambda _: ({'main.tex': r"\documentclass{article}\title{Real Paper Title}"}, b''))
    assert c.parts(snapshot, paper)[0]['text'] == 'Real Paper Title'
    assert b.build_body(snapshot, paper, {})[0] == 'Real Paper Title'
    monkeypatch.setattr(c, 'source_files', lambda _: ({'main.tex': r"\documentclass{article}"}, b''))
    assert c.parts(snapshot, paper)[0]['text'] == r'R1\_2503\_Article'
    assert b.build_body(snapshot, paper, {})[0] == r'R1\_2503\_Article'


def test_long_literal_identifiers_can_wrap_without_changing_text():
    from app.chinese_pdf import layout
    source = r"Use \texttt{Qwen2.5-Coder-32B-Instruct} and \texttt{task\_name}. $x=3$"
    result = layout(source)
    assert r'-\allowbreak{}' in result and r'\_\allowbreak{}' in result
    assert result.replace(r'\allowbreak{}', '') == source
    assert layout(result) == result
