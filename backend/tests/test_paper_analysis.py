from copy import deepcopy
import json
import pytest
from fastapi.testclient import TestClient
from app import jobs, llm, fulltext, repository as repo, paper_analysis as analysis
from app.config import get_settings
from app.main import app
from app.worker import process_one
from app.demo import demo_papers


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path/'test.db'))
    monkeypatch.setenv('LLM_API_KEY', 'test-only')
    monkeypatch.setenv('LLM_MODEL', 'fixture-model')
    get_settings.cache_clear()
    with TestClient(app) as client:
        paper=deepcopy(demo_papers()[0]); paper['mode']='live';repo.upsert_paper(paper)
        blocks=[{'id':f'p{i:04d}','text':'We learn interactive agent policies from sparse rewards. '*110,
                 'kind':'prose','section':'Methods','location':f'paper.tex:{i}','translatable':True} for i in (1,2)]
        document={'paper_id':paper['id'],'version':paper['version'],'status':'ready','method':'latex','document_hash':'test-hash',
                  'source_url':'fixture.tex','source_hash':'fixture','warnings':[],'blocks':blocks}
        fulltext.save_document(paper,document)
        yield client,paper,document
    get_settings.cache_clear()


def response(ids):
    return {**{field:[{'text':'论文使用交互奖励优化策略；可在相同环境比较基线并检验成功率。','basis':'proposal' if field=='research_ideas' else 'author_claim','evidence':ids[:1]}] for field in analysis.HEADINGS},'unknowns':[]}


def submit(client,paper,**extra):
    return client.post('/api/jobs',json={'skill_id':'paper_analysis','paper_id':paper['id'],'scope':'fulltext',**extra})


def test_validation_and_no_key(setup,monkeypatch):
    client,paper,doc=setup
    assert submit(client,paper,scope='abstract').status_code==422
    monkeypatch.setenv('LLM_API_KEY','');get_settings.cache_clear()
    assert submit(client,paper).status_code==503
    value=response(['missing'])
    with pytest.raises(ValueError):analysis.validate_output(value,doc['blocks'])
    value=response(['p0001']);value['research_ideas'][0]['basis']='author_claim'
    with pytest.raises(ValueError):analysis.validate_output(value,doc['blocks'])
    value=response(['p0001']);value['method'][0]['text']=r'错误 \input{secrets}'
    with pytest.raises(ValueError):analysis.validate_output(value,doc['blocks'])


def test_checkpoint_resume_synthesis_and_download(setup,monkeypatch):
    client,paper,doc=setup
    calls=[]
    def model(system,data,request_id):
        calls.append(data)
        if len(calls)==2:raise llm.ModelError('fixture interruption')
        ids=[b['id'] for b in data['blocks']] if 'blocks' in data else data['allowed_evidence_ids']
        return response(ids),'fixture',{'total_tokens':20}
    monkeypatch.setattr(llm,'request_json',model)
    first=submit(client,paper).json()
    assert submit(client,paper).json()['id']==first['id']
    process_one('overall-test')
    assert jobs.get_job(first['id'])['status']=='failed'
    assert client.post('/api/jobs/'+first['id']+'/retry',json={}).status_code==200
    process_one('overall-test')
    done=jobs.get_job(first['id'])
    assert done['status']=='succeeded',done
    assert len(calls)==4 # map 1 reused; map 2 retried; then one synthesis.
    assert done['result']['document_hash']==doc['document_hash']
    assert done['result']['analyzed_paragraph_ids']==['p0001','p0002']
    assert done['result']['research_ideas'][0]['basis']=='proposal'
    assert all(e['quote'] in [b['text'] for b in doc['blocks']] for e in done['result']['evidence_locations'])
    artifact=client.get('/api/jobs/'+first['id']+'/artifact')
    assert artifact.status_code==200 and '研究方法' in artifact.text
    assert '%E6%80%BB%E4%BD%93%E5%88%86%E6%9E%90' in artifact.headers['content-disposition']
    assert client.get('/api/reports/pdf?job_id='+first['id']).status_code==409
    assert analysis.snapshot(paper,first['id'])['result']['document_hash']==doc['document_hash']
    doc['document_hash']='changed';fulltext.save_document(paper,doc)
    assert client.post('/api/jobs',json={'skill_id':'analysis_pdf','paper_id':paper['id'],'scope':'fulltext','analysis_job_id':first['id']}).status_code==409


@pytest.mark.parametrize('columns',[1,2])
def test_shared_template_safe_text(columns):
    data=response(['p0001'])
    data.update(evidence_locations=[{'paragraph_id':'p0001','section':'研究方法','location':'原文第2页'}],unknowns=['未独立复现实验'])
    tex=analysis.render_tex(data,'测试 & 方法',columns)
    assert 'SimSun' in tex and 'hidelinks' in tex
    assert ('10pt,twocolumn' in tex)==(columns==2)
    assert r'测试 \& 方法' in tex and r'\section{研究方法}' in tex
    assert analysis.filename('A/B','pdf')=='A-B总体分析.pdf'


def test_many_valid_evidence_ids_and_unknown_rejected():
    blocks=[{'id':f'p{i:04d}'} for i in range(12)]
    value=response([b['id'] for b in blocks])
    value['experiments'][0]['evidence']=[b['id'] for b in blocks]
    assert len(analysis.validate_output(value,blocks)['experiments'][0]['evidence'])==12
    value['experiments'][0]['evidence'].append('invented')
    with pytest.raises(ValueError):analysis.validate_output(value,blocks)

def test_saved_raw_response_is_revalidated_without_model(setup,monkeypatch):
    client,paper,doc=setup
    job=submit(client,paper).json()
    record=jobs.claim_job('raw-cache-test')
    assert record['id']==job['id']
    calls=[]
    def model(system,data,request_id):
        calls.append(1);value=response(['p0001']);value['method'][0]['text']=r'论文中方法名为 \MethodName。'
        return value,'fixture',{}
    monkeypatch.setattr(llm,'request_json',model)
    from app import chinese_pdf
    monkeypatch.setattr(chinese_pdf,'source_files',lambda doc:({'main.tex':r'\newcommand{\MethodName}{G2PO}'},b''))
    data={'title':paper['title'],'blocks':doc['blocks']}
    first=analysis.cached_step(record,'raw-cache-test','test-raw',data,doc['blocks'],'')
    assert 'G2PO' in first['method'][0]['text'] and len(calls)==1
    assert analysis.cached_step(record,'raw-cache-test','test-raw',data,doc['blocks'],'')['method']==first['method']
    assert len(calls)==1


def test_controlled_math_formatting():
    assert analysis.format_text('方法 γ^{T-j+1}R 与 A^EC_raw、G_k') == r'方法 $\gamma^{T-j+1}$R 与 $A^{EC}_{raw}$、$G_{k}$'
    assert analysis.format_text(r'\input{secret}') == r'\textbackslash{}input\{secret\}'
    assert analysis.format_text('actor_loss') == r'actor\_loss'
