import pytest
from app import llm, jobs, subscriptions as sub, repository as repo
from app.config import get_settings
from app.worker import process_one
from test_subscriptions import client,create,paper,feed,run


def configure(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY','test-only');monkeypatch.setenv('LLM_MODEL','fixture-model');get_settings.cache_clear()


def test_optin_budget_cache_feedback_and_topic(client,monkeypatch):
    configure(monkeypatch)
    papers=[paper('2601.00001'),paper('2601.00002'),paper('2601.00003')]
    monkeypatch.setattr(sub.conference_discovery,'search',lambda *a:feed(papers))
    calls=[]
    def model(system,data,request_id):
        assert data['research_interests']==['Agent 强化学习']
        calls.append(data)
        return {'interests':[{'interest':'Agent 强化学习','score':85,'reason':'直接研究交互策略训练。','evidence':['A001']}],'unknowns':['未复现实验']},'fixture',{}
    monkeypatch.setattr(llm,'request_json',model)
    s=create(client,model_enabled=True,model_limit=2)
    job=run(client,s['id']);process_one('weekly-model')
    result=jobs.get_job(job['id'])
    assert result['status']=='succeeded',result
    assert result['result']['model_calls']==2 and len(calls)==2
    assert sum(p['weekly_rank']['method']=='semantic' for p in result['result']['papers'])==2
    assert run(client,s['id'])['id']==job['id']
    # Same context in a separate report is reusable without paid calls.
    other=run(client,create(client,model_enabled=True,model_limit=2)['id']);process_one('weekly-model')
    again=jobs.get_job(other['id'])['result']
    assert again['model_calls']==0 and again['model_cache_hits']==2 and len(calls)==2
    target=again['papers'][0]['id']
    client.put('/api/feedback/'+target,json={'value':'relevant'})
    third=run(client,create(client,model_enabled=True,model_limit=2)['id']);process_one('weekly-model')
    ranked=jobs.get_job(third['id'])['result']['papers']
    assert next(p for p in ranked if p['id']==target)['weekly_rank']['score']==90


def test_failure_downgrade_without_rebilling(client,monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(sub.conference_discovery,'search',lambda *a:feed([paper()]))
    calls=[]
    def fail(*a,**kw):
        calls.append(1);raise llm.ModelError('模型响应未确认',ambiguous=True)
    monkeypatch.setattr(llm,'request_json',fail)
    job=run(client,create(client,model_enabled=True)['id']);process_one('weekly-failure')
    done=jobs.get_job(job['id'])
    assert done['status']=='succeeded',done
    assert done['result']['model_failures']==1 and len(calls)==1
    assert done['result']['papers'][0]['weekly_rank']['method']=='keyword'
    assert done['result']['papers'][0]['weekly_semantic']['ambiguous']


def test_optin_without_key_and_default_no_call(client,monkeypatch):
    monkeypatch.setattr(sub.conference_discovery,'search',lambda *a:feed([paper()]))
    monkeypatch.setattr(llm,'request_json',lambda *a,**kw:pytest.fail('No model call authorized'))
    job=run(client,create(client,model_enabled=True)['id']);process_one('weekly-nokey')
    result=jobs.get_job(job['id'])['result']
    assert result['model_calls']==0 and result['papers'][0]['weekly_semantic']['status']=='unavailable'
    configure(monkeypatch)
    job=run(client,create(client)['id']);process_one('weekly-default')
    assert jobs.get_job(job['id'])['result']['model_calls']==0
    assert client.post('/api/subscriptions',json={'name':'x','topic':'agent','model_limit':11}).status_code==422

def test_feedback_evaluation_never_invents_labels(client,monkeypatch):
    from app.feedback_evaluation import evaluate_feedback
    p=paper();repo.upsert_paper(p)
    assert evaluate_feedback()['metrics'] is None
    client.put('/api/feedback/'+p['id'],json={'value':'too_basic'})
    assert evaluate_feedback()['label_count']==0
    client.put('/api/feedback/'+p['id'],json={'value':'relevant'})
    result=evaluate_feedback()
    assert result['label_count']==1 and result['paired_count']==0 and result['metrics'] is None
    assert result['samples'][0]['semantic_score'] is None
    download=client.get('/api/feedback/evaluation.json')
    assert download.status_code==200 and download.json()['label_count']==1
