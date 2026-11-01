from datetime import datetime, timezone, timedelta
import json

import pytest
from fastapi.testclient import TestClient
from app import conference_discovery as c, repository as repo
from app.config import get_settings
from app.main import app
from app.research import publication, catalog
from app.sources import SourceError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'test.db'))
    monkeypatch.setenv('LLM_API_KEY','');monkeypatch.setenv('LLM_MODEL','')
    get_settings.cache_clear();repo.init_db()
    yield
    get_settings.cache_clear()


def paper(identifier='2601.12345',title='Agent Reinforcement Learning',days=30):
    date=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    return {'id':identifier,'version':1,'title':title,'authors':['Alice Smith'],'abstract':'Agent reinforcement learning for language models.',
            'published':date,'updated':datetime.now(timezone.utc).isoformat(),'mode':'live','source':'arXiv','categories':['cs.AI'],
            'url':'https://arxiv.org/abs/'+identifier,'pdf_url':'https://arxiv.org/pdf/'+identifier,'fetched_at':repo.utc_now()}


def record(title='Agent Reinforcement Learning'):
    return {'title':title,'venue':'NeurIPS','year':2025,'evidence_url':'https://proceedings.neurips.cc/paper_files/paper/2025/hash/abc-Abstract-Conference.html','index_url':'https://proceedings.neurips.cc/','evidence_text':'Main Conference','verified_at':repo.utc_now()}


def test_index_excludes_non_main_tracks_and_external_hosts():
    html=''.join(f'<a href="{url}">Title</a>' for url in [
        '/paper_files/paper/2025/hash/abc-Abstract-Conference.html',
        '/paper_files/paper/2025/hash/abc-Abstract-Datasets_and_Benchmarks_Track.html',
        'https://evil.example/paper_files/paper/2025/hash/abc-Abstract-Conference.html'])
    rows=c.parse_index('NeurIPS',2025,'https://proceedings.neurips.cc/paper_files/paper/2025',html)
    assert len(rows)==1 and rows[0]['evidence_url'].startswith('https://proceedings.neurips.cc/')
    acl=''.join(f'<a href="/{id}/">Paper title</a>' for id in ['2025.acl-long.1','2025.acl-short.1','2025.findings-acl.1','2025.workshop.1'])
    assert len(c.parse_index('ACL',2025,'https://aclanthology.org/events/acl-2025/',acl))==1


def test_icml_index_requires_official_volume_and_excludes_position_papers():
    html='International Conference on Machine Learning'+''.join('<div class="paper"><p class="title">'+title+'</p><p class="links"><a href="https://proceedings.mlr.press/v267/a25.html">abs</a></p></div>' for title in ['Agent Training','Position: Agent Ethics'])
    rows=c.parse_index('ICML',2025,'https://proceedings.mlr.press/v267/',html)
    assert len(rows)==1 and rows[0]['title']=='Agent Training'
    assert c.parse_index('ICML',2025,'https://proceedings.mlr.press/v267/',html.replace('International Conference on Machine Learning','Workshop'))==[]


def test_evidence_requires_title_and_author_and_persists(monkeypatch):
    p=paper();r=record()
    monkeypatch.setattr(c,'html_get',lambda _: '<meta name="citation_title" content="Agent Reinforcement Learning"><meta name="citation_author" content="Smith, Alice">')
    assert c.verify_entry(p,r)
    assert publication(p)['status']=='verified'
    assert publication({**p,'authors':['Different Author']})['status']=='unverified'
    assert publication({**p,'title':'Other Paper'})['status']=='unverified'
    assert publication({**p,'mode':'demo'})['status']=='unverified'
    assert c.verify_entry({**p,'authors':['Different Author']},r) is None


def test_dynamic_recall_keeps_query_and_dates_deduplicates_and_caches(monkeypatch):
    base=paper();extra=paper('2602.12345','Agent Policy Optimization')
    records=[record(base['title']),record(extra['title'])]
    monkeypatch.setattr(c,'official_index',lambda _: (records,[{'source':'NeurIPS','status':'available','count':2}]))
    monkeypatch.setattr(c,'verify_entry',lambda *a:None)
    calls=[]
    def search(self,query,start,limit,days,sort):
        calls.append((query,days))
        return {'papers':[base] if len(calls)==1 else [base,extra,extra], 'query':query,'total':100,'cached':False,'fetched_at':repo.utc_now()}
    monkeypatch.setattr(c.ArxivSource,'search',search)
    query='all:"agent" ANDNOT all:"robot"'
    first=c.search(query,730);second=c.search(query,730)
    assert len(first['papers'])==2 and second['cached'] and len(calls)==2
    assert calls[1][0].startswith('('+query+') AND (') and calls[1][1]==730
    assert first['discovery']['indexed_papers']==2 and first['discovery']['queries']
    assert any('scholar.google.com' in link['url'] and not link['automatic'] for link in first['discovery']['links'])


def test_source_failure_keeps_arxiv_and_unknown_status(monkeypatch):
    p=paper();monkeypatch.setattr(c,'official_index',lambda _: ([],[{'source':'ACL','status':'unavailable','message':'timeout'}]))
    monkeypatch.setattr(c.ArxivSource,'search',lambda *a,**k:{'papers':[p],'query':'q','total':1,'fetched_at':repo.utc_now()})
    result=c.search('all:"agent"',730)
    assert result['papers'] and '暂不可用' in result['warning'] and publication(p)['status']=='unverified'


def test_all_sources_failure_is_explicit(monkeypatch):
    def failed(*a,**k):raise SourceError('arXiv unavailable')
    monkeypatch.setattr(c.ArxivSource,'search',failed)
    monkeypatch.setattr(c,'official_index',lambda _: ([],[]))
    with pytest.raises(SourceError):c.search('all:"agent"',730)


def test_priority_pagination_date_filter_and_provenance(monkeypatch):
    items=[paper('2601.00001','Agent New'),paper('2601.00002','Agent Verified'),paper('2301.00001','Agent Old',900),paper('2601.00003','Agent Other')]
    r=record('Agent Verified');r.update(method='official_title_and_author',matched_authors=[['alice','smith']])
    c.cache_put(c.VERSION+':evidence:'+c.normalized_title(r['title']),r,60)
    discovery={'queries':['original query','extra recall query'],'sources':[],'note':'test'}
    monkeypatch.setattr(c,'search',lambda *a: {'papers':items,'query':'original query','total':4,'discovery':discovery})
    with TestClient(app) as client:
        a=client.get('/api/papers',params={'query':'agent','limit':2,'days':730}).json()
        b=client.get('/api/papers',params={'query':'agent','limit':2,'days':730,'start':2}).json()
        assert a['papers'][0]['title']=='Agent Verified' and a['total']==3 and a['has_more']
        assert len(b['papers'])==1 and not b['has_more']
        assert not set(p['id'] for p in a['papers']).intersection(p['id'] for p in b['papers'])
        assert all(p['id']!='2301.00001' for p in a['papers']+b['papers'])
        with repo.connect() as db:
            params=json.loads(db.execute('SELECT parameters FROM search_runs WHERE id=?',(a['run_id'],)).fetchone()['parameters'])
        assert params['discovery']['queries']==discovery['queries']


def test_no_separate_ccfa_seed_module():
    value=catalog()
    assert {r['id'] for r in value['tracks']}=={'agent_rl','post_training'}
    assert 'query' not in value['ccf'] and 'verified_count' not in value['ccf']
    assert value['ccf']['strategy']=='topic_first_official_evidence'


def test_zero_verified_papers_returns_arxiv_instead_of_empty(monkeypatch):
    p=paper()
    monkeypatch.setattr(c,'search',lambda *a: {'papers':[p],'query':'q','total':1,'discovery':{}})
    with TestClient(app) as client:
        response=client.get('/api/papers',params={'query':'agent','days':730})
        assert response.status_code==200
        result=response.json()
        assert result['papers'][0]['id']==p['id']
        assert result['discovery']['priority_status']=='arxiv_fallback'
        assert result['discovery']['verified_count']==0
        assert '已优先展示相关 arXiv' in result['warning']


def test_valid_empty_arxiv_search_is_not_a_source_error(monkeypatch):
    monkeypatch.setattr(c.ArxivSource,'search',lambda *a,**k:{'papers':[],'query':'q','total':0,'fetched_at':repo.utc_now()})
    monkeypatch.setattr(c,'official_index',lambda _: ([],[{'source':'ACL','status':'unavailable','message':'timeout'}]))
    result=c.search('all:"nonexistent topic"',730)
    assert result['papers']==[] and result['total']==0
    assert '暂不可用' in result['warning']


def test_slow_supplemental_verification_keeps_arxiv(monkeypatch):
    p=paper()
    ticks=iter([0,61])
    monkeypatch.setattr(c.time,'monotonic',lambda:next(ticks,61))
    monkeypatch.setattr(c.ArxivSource,'search',lambda *a,**k:{'papers':[p],'query':'q','total':1,'fetched_at':repo.utc_now()})
    monkeypatch.setattr(c,'official_index',lambda _: ([record()],[{'source':'NeurIPS','status':'available','count':1}]))
    def no_verify(*args):raise AssertionError('verification should stop after budget')
    monkeypatch.setattr(c,'verify_entry',no_verify)
    result=c.search('all:"agent"',730)
    assert result['papers']==[p] and '时间已达上限' in result['warning']
