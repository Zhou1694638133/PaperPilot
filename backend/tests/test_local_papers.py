import io
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app import fulltext, jobs, llm, repository as repo
from app.config import get_settings
from app.main import app
from app.worker import process_one

TEXT = ('This is a local upload verification sample, not a published research paper. '
        'An agent learns a policy from environment feedback. We describe the research question, '
        'training method and evaluation constraints. The translation must preserve the source '
        'paragraph and must not invent experimental results or publication dates.')


def pdf(pages=1):
    out=io.BytesIO(); c=canvas.Canvas(out)
    for _ in range(pages):
        t=c.beginText(50,750)
        for line in [TEXT[i:i+70] for i in range(0,len(TEXT),70)]:
            t.textLine(line)
        c.drawText(t); c.showPage()
    c.save(); return out.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path/'test.db'))
    monkeypatch.setenv('LLM_API_KEY', '')
    monkeypatch.setenv('LLM_MODEL', '')
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def upload(client, data=None, filename='Local Agent.pdf', **headers):
    return client.post('/api/local-papers', params={'filename':filename}, content=pdf() if data is None else data,
                       headers={'Content-Type':'application/pdf',**headers})


def test_upload_persistence_dedup_source_and_no_model(client, monkeypatch):
    data=pdf(); first=upload(client,data, '../中文论文.pdf'); assert first.status_code==201,first.text
    first=first.json(); paper=first['paper']; identifier=paper['id']
    assert paper['source']=='local' and paper['published']==paper['updated']==paper['abstract']==''
    assert paper['title']=='中文论文' and paper['collection']['saved'] and paper['recommendation'] is None
    assert not first['reused']
    second=upload(client,data,'Different Name.pdf').json()
    assert second['reused'] and second['job']['id']==first['job']['id'] and second['paper']['title']==paper['title']
    download=client.get(paper['pdf_url'])
    assert download.content==data and unquote(download.headers['content-disposition'].split("''")[1])=='中文论文.pdf'
    monkeypatch.setattr(fulltext,'download',lambda *args:pytest.fail('Local files must not contact arXiv'))
    monkeypatch.setattr(llm,'request_json',lambda *args:pytest.fail('Parsing must not call models'))
    assert process_one('local-parser')
    assert jobs.get_job(first['job']['id'])['status']=='succeeded'
    doc=client.get('/api/fulltext',params={'paper_id':identifier}).json()['document']
    assert doc['method']=='pdf' and doc['status']=='needs_review' and doc['blocks']
    assert repo.get_paper(identifier)['collection']['saved']
    assert client.post('/api/jobs',json={'paper_id':identifier,'skill_id':'paper_translator','scope':'fulltext','acknowledge_partial':True}).status_code==503
    bib=client.get('/api/export',params={'ids':identifier,'mode':'live','format':'bibtex'})
    assert bib.status_code==200 and 'arXiv' not in bib.text and 'Local PDF upload' in bib.text
    assert client.get('/api/local-papers/not-found/file').status_code==404


@pytest.mark.parametrize('data,name,status', [(b'','empty.pdf',422),(b'not a PDF','wrong.pdf',422),(b'%PDF- fake','wrong.docx',415)])
def test_reject_invalid_uploads(client,data,name,status):
    assert upload(client,data,name).status_code==status


def test_limit_streamed_body_and_cross_origin(client,monkeypatch):
    monkeypatch.setattr(fulltext,'MAX_DOWNLOAD',10)
    assert upload(client,b'%PDF-'+b'x'*20).status_code==413
    response=client.post('/api/local-papers?filename=x.pdf',headers={'Content-Type':'application/pdf'},content=iter([b'%PDF-',b'x'*20]))
    assert response.status_code==413
    assert upload(client,b'%PDF-',Origin='https://untrusted.example').status_code==403
    assert client.post('/api/local-papers?filename=x.pdf',content=b'%PDF-',headers={'Content-Type':'text/plain'}).status_code==415


@pytest.mark.parametrize('kind',['damaged','encrypted','scan','too_many_pages'])
def test_worker_reports_unreadable_pdf(client,kind):
    if kind=='damaged': data=b'%PDF-broken'
    else:
        writer=PdfWriter()
        for _ in range(151 if kind=='too_many_pages' else 1): writer.add_blank_page(600,800)
        if kind=='encrypted': writer.encrypt('password')
        out=io.BytesIO(); writer.write(out); data=out.getvalue()
    result=upload(client,data).json(); process_one('bad-parser')
    job=jobs.get_job(result['job']['id'])
    assert job['status']=='failed' and job['error']
    assert fulltext.get_document(result['paper']) is None
    assert client.get(result['paper']['pdf_url']).content==data


def test_local_translation_resume_and_download(client,monkeypatch):
    imported=upload(client,pdf(2)).json(); identifier=imported['paper']['id']; process_one('parse')
    monkeypatch.setenv('LLM_API_KEY','test-only');monkeypatch.setenv('LLM_MODEL','test-model');get_settings.cache_clear()
    request={'paper_id':identifier,'skill_id':'paper_translator','scope':'fulltext'}
    assert client.post('/api/jobs',json=request).status_code==409
    assert client.post('/api/jobs',json={**request,'scope':'abstract'}).status_code==422
    assert client.post('/api/jobs',json={**request,'skill_id':'chinese_pdf'}).status_code==422
    request['acknowledge_partial']=True
    created=client.post('/api/jobs',json=request).json()
    assert client.post('/api/jobs',json=request).json()['id']==created['id']
    doc=fulltext.get_document(repo.get_paper(identifier))
    excerpt=' '.join(doc['blocks'][0]['text'].split())
    assert client.put(f'/api/local-papers/{identifier}/abstract',json={'text':excerpt,'document_hash':doc['document_hash']}).status_code==200
    assert client.post('/api/jobs',json=request).json()['id']==created['id']  # Same full-text checkpoints after abstract confirmation.
    calls=[]; failing=[True]
    def model(system,payload,request_id):
        calls.append(payload['paragraph_id'])
        if payload['paragraph_id']=='p0002' and failing[0]: raise llm.ModelError('test failure')
        return {'translation':'这是隔离测试中的模拟译文。'+payload['paragraph']},'test',{}
    monkeypatch.setattr(llm,'request_json',model)
    process_one('translate'); assert jobs.get_job(created['id'])['status']=='failed'
    assert client.get(f"/api/jobs/{created['id']}/alignment").json()['completed']==1
    failing[0]=False; assert client.post(f"/api/jobs/{created['id']}/retry").status_code==200
    process_one('resume'); assert jobs.get_job(created['id'])['status']=='succeeded'
    assert calls.count('p0001')==1 and calls.count('p0002')==2
    alignment=client.get(f"/api/jobs/{created['id']}/alignment").json()
    assert alignment['completed']==alignment['total']==2
    response=client.get('/api/reports/pdf',params={'job_id':created['id']})
    assert response.status_code==200 and len(PdfReader(io.BytesIO(response.content)).pages)>0
    assert unquote(response.headers['content-disposition'].split("''")[1])=='Local Agent翻译版.pdf'
    assert client.get(f"/api/jobs/{created['id']}/artifact").status_code==200
    call_count=len(calls)
    monkeypatch.setenv('LLM_API_KEY','');monkeypatch.setenv('LLM_MODEL','');get_settings.cache_clear()
    chinese=client.get(f"/api/jobs/{created['id']}/chinese-text.pdf")
    assert chinese.status_code==200,chinese.text if chinese.status_code!=200 else ''
    assert chinese.content.startswith(b'%PDF') and len(PdfReader(io.BytesIO(chinese.content)).pages)>0
    assert unquote(chinese.headers['content-disposition'].split("''")[1])=='Local Agent翻译版.pdf'
    assert len(calls)==call_count
    assert client.get(f"/api/jobs/{created['id']}/alignment").json()['completed']==2



def abstract_pdf():
    out=io.BytesIO(); c=canvas.Canvas(out); t=c.beginText(50,750)
    for line in ['Local verification sample','Abstract',TEXT,'1 Introduction','This is the introduction and must not become part of the abstract.']:
        t.textLine(line)
    c.drawText(t); c.save(); return out.getvalue()


def test_local_history_and_confirmed_abstract_tasks(client,monkeypatch):
    imported=upload(client,abstract_pdf()).json(); pid=imported['paper']['id']
    assert client.get(f'/api/local-papers/{pid}/abstract').json()['ready'] is False
    process_one('parse-abstract')
    preview=client.get(f'/api/local-papers/{pid}/abstract').json()
    assert preview['candidate']==TEXT and 'introduction' not in preview['candidate']
    response=client.put(f'/api/local-papers/{pid}/abstract',json={'text':preview['candidate'],'document_hash':preview['document_hash']})
    assert response.status_code==200,response.text
    paper=response.json(); assert paper['abstract']==TEXT and paper['abstract_source']['method']=='user_confirmed_pdf_excerpt'
    assert paper['recommendation'] is None and paper['semantic'] is None
    repo.update_collection(pid,'live',{'saved':False})
    assert client.get('/api/local-papers').json()['papers'][0]['id']==pid
    assert client.post('/api/jobs',json={'paper_id':pid,'skill_id':'paper_translator'}).status_code==503
    monkeypatch.setenv('LLM_API_KEY','test-only');monkeypatch.setenv('LLM_MODEL','test-model');get_settings.cache_clear()
    for skill in ['recommendation_explainer','semantic_ranker']:
        assert client.post('/api/jobs',json={'paper_id':pid,'skill_id':skill}).status_code==422
    calls=[]
    def model(system,payload,request_id):
        calls.append(payload)
        if 'abstract' in payload:
            section={'text':'隔离测试：该样例描述智能体学习，不声称真实实验结论。','evidence':[TEXT.split('. ')[0]+'.']}
            return {**{key:section for key in ['research_question','method','experiments','limitations','research_relation']},'unknowns':['测试样例，不是真实论文分析。']},'test',{}
        return {'translation':'这是用于验证本地摘要任务的模拟译文。'+payload['paragraph']},'test',{}
    monkeypatch.setattr(llm,'request_json',model)
    job=client.post('/api/jobs',json={'paper_id':pid,'skill_id':'paper_translator'}).json()
    assert job['abstract_revision']==paper['abstract_revision']
    process_one('abstract-translator')
    assert jobs.get_job(job['id'])['status']=='succeeded' and len(calls)==1
    assert client.get(f"/api/jobs/{job['id']}/artifact").status_code==200
    assert client.get('/api/reports/pdf',params={'job_id':job['id']}).status_code==200
    reader=client.post('/api/jobs',json={'paper_id':pid,'skill_id':'paper_reader'}).json()
    assert reader['scope']=='abstract' and jobs.job_record(reader['id'])['paper']['abstract']==TEXT
    process_one('abstract-reader')
    assert jobs.get_job(reader['id'])['status']=='succeeded'
    assert jobs.get_job(reader['id'])['result']['coverage']=='abstract'


def test_abstract_rejects_invented_text_stale_document_and_unknown_paper(client):
    imported=upload(client).json(); pid=imported['paper']['id'];process_one('parse')
    preview=client.get(f'/api/local-papers/{pid}/abstract').json()
    assert preview['candidate']==''
    assert client.put(f'/api/local-papers/{pid}/abstract',json={'text':'Invented abstract that cannot be located in the uploaded source PDF.','document_hash':preview['document_hash']}).status_code==422
    assert client.put(f'/api/local-papers/{pid}/abstract',json={'text':TEXT,'document_hash':'outdated'}).status_code==409
    assert client.get('/api/local-papers/missing/abstract').status_code==404
    assert repo.get_paper(pid)['abstract']==''
