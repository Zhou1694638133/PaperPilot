from contextlib import closing
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import get_settings
from app import jobs, llm, skills
from app.repository import init_db
from app.worker import process_one


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    init_db()
    jobs.init_jobs()
    paper = {
        "id": "2401.00001", "version": 1, "title": "Agent Test",
        "abstract": "We evaluate multi-agent systems on 3 tasks with $x^2$ [1].",
        "url": "https://arxiv.org/abs/2401.00001", "mode": "live",
        "recommendation": {"score": 80, "interests": ["多智能体协作"],
            "evidence": [{"text": "We evaluate multi-agent systems on 3 tasks with $x^2$ [1].", "location": "摘要第 1 句"}],
            "features": [{"name": "主题", "value": 80, "weight": 1}], "unknowns": ["代码未核实"], "method": "rules-v1"},
    }
    monkeypatch.setattr(jobs, "get_paper", lambda paper_id, mode="live": paper if paper_id == paper["id"] else None)
    app = FastAPI()
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield client, paper
    get_settings.cache_clear()


def submit(client, skill="recommendation_explainer", **kwargs):
    return client.post("/api/jobs", json={"skill_id": skill, "paper_id": "2401.00001", **kwargs})


def configured(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "unit-test-only")
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    get_settings.cache_clear()


def expire(job_id):
    with closing(jobs.connect()) as conn, conn:
        conn.execute("UPDATE jobs SET lease_until=? WHERE id=?", (time.time() - 1, job_id))


def test_no_key_real_model_is_unavailable_and_rules_work(setup):
    client, _ = setup
    response = submit(client, "paper_translator")
    assert response.status_code == 503
    definitions = client.get("/api/skills").json()["skills"]
    assert next(item for item in definitions if item["id"] == "paper_translator")["available"] is False
    assert submit(client).status_code == 201


def test_demo_never_makes_paid_model_request(setup, monkeypatch):
    client, _ = setup
    configured(monkeypatch)
    assert submit(client, "paper_reader", mode="demo").status_code == 409


def test_request_hash_and_idempotency_key_protect_duplicates(setup):
    client, _ = setup
    first = submit(client, idempotency_key="button-1").json()
    second = submit(client, idempotency_key="button-2").json()
    assert first["id"] == second["id"]
    conflict = submit(client, idempotency_key="button-1", glossary={"agent": "智能体"})
    assert conflict.status_code == 409
    assert len(client.get("/api/jobs").json()["jobs"]) == 1


def test_global_worker_slot_atomic_lease_and_recovery(setup):
    client, _ = setup
    first = submit(client).json()
    assert jobs.claim_job("one")["id"] == first["id"]
    assert jobs.claim_job("two") is None
    expire(first["id"])
    recovered = jobs.claim_job("two")
    assert recovered["id"] == first["id"]
    assert recovered["attempts"] == 2
    with pytest.raises(jobs.LeaseLost):
        jobs.save_checkpoint(first["id"], "one", {"markdown": "stale"})


def test_ambiguous_call_requires_confirmation_before_retry(setup):
    client, _ = setup
    job_id = submit(client).json()["id"]
    jobs.claim_job("one")
    jobs.mark_model_started(job_id, "one")
    expire(job_id)
    assert jobs.claim_job("two") is None
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "needs_confirmation"
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/retry", json={"confirm_duplicate_cost": True}).status_code == 200


def test_cancel_and_resume_only_saved_progress(setup):
    client, _ = setup
    job_id = submit(client).json()["id"]
    cancelled = client.post(f"/api/jobs/{job_id}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert jobs.claim_job("one") is None
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    assert jobs.claim_job("one")["id"] == job_id
    client.post(f"/api/jobs/{job_id}/cancel")
    with pytest.raises(jobs.Cancelled):
        jobs.ensure_running(job_id, "one")


def test_rule_worker_durable_graph_artifact_and_sse_replay(setup):
    client, _ = setup
    job_id = submit(client).json()["id"]
    assert process_one("one")
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert job["progress"] == 100
    assert job["checkpoint_id"]
    assert get_settings().database_path.with_name("checkpoints.sqlite").exists()
    assert "We evaluate multi-agent" in job["result"]["markdown"]
    assert client.get(f"/api/jobs/{job_id}/artifact").status_code == 200
    events = client.get(f"/api/jobs/{job_id}/events").text
    ids = [int(line[4:]) for line in events.splitlines() if line.startswith("id: ")]
    assert len(ids) > 2
    replay = client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": str(ids[-2])}).text
    assert f"id: {ids[-1]}\n" in replay
    assert f"id: {ids[0]}\n" not in replay
    # A new API connection and worker view both retain the same durable artifact.
    assert jobs.job_record(job_id)["checkpoint"]["coverage"] == "abstract"


def test_translation_protects_math_citations_and_numbers():
    source = "Results: 17.5% with $x^2$ [3, 4] and `foo()` at https://example.org."
    masked, values = skills.protect(source)
    assert skills.restore(masked, values) == source
    with pytest.raises(ValueError):
        skills.restore(masked.replace("__PP_PROTECTED_0000__", ""), values)
    with pytest.raises(ValueError):
        skills.restore(masked + " __PP_PROTECTED_0000__", values)


def test_translation_failure_resumes_completed_chunks_only(setup, monkeypatch):
    client, paper = setup
    configured(monkeypatch)
    paper["abstract"] = "First result: 3 tasks.\n\nSecond result: 5 agents."
    calls = []
    fail_second = True

    def fake_request(system, data, request_id):
        nonlocal fail_second
        calls.append(data["paragraph_id"])
        if data["paragraph_id"] == "abstract-p002" and fail_second:
            fail_second = False
            raise llm.ModelError("模型限流")
        return {"translation": "中文译文：" + data["paragraph"]}, "fixture-response", {"total_tokens": 10}

    monkeypatch.setattr(llm, "request_json", fake_request)
    job_id = submit(client, "paper_translator").json()["id"]
    assert process_one("one")
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    assert process_one("two")
    final = client.get(f"/api/jobs/{job_id}").json()
    assert final["status"] == "succeeded"
    assert calls.count("abstract-p001") == 1
    assert calls.count("abstract-p002") == 2
    assert len(final["result"]["alignment"]) == 2
    assert final["result"]["qa"]["protected_tokens"] == "passed"


def test_reader_rejects_invented_evidence(setup, monkeypatch):
    client, _ = setup
    configured(monkeypatch)
    section = {"text": "虚构结论", "evidence": ["This quote does not exist."]}
    output = {key: section for key in ["research_question", "method", "experiments", "limitations", "research_relation"]}
    output["unknowns"] = []
    monkeypatch.setattr(llm, "request_json", lambda *args: (output, "fixture", {}))
    job_id = submit(client, "paper_reader").json()["id"]
    process_one("one")
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["result"] is None
    assert "引文校验" in job["error"]


def test_reader_missing_sections_remain_unknown(setup, monkeypatch):
    client, paper = setup
    configured(monkeypatch)
    output = {key: {"text": "不能信任的无证据结论", "evidence": []} for key in ["research_question", "method", "experiments", "limitations", "research_relation"]}
    output["research_question"] = {"text": "作者评估多智能体系统。", "evidence": [paper["abstract"]]}
    output["unknowns"] = []
    monkeypatch.setattr(llm, "request_json", lambda *args: (output, "fixture", {}))
    job_id = submit(client, "paper_reader").json()["id"]
    process_one("one")
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert job["result"]["experiments"]["text"] == "摘要未提供，尚未核实"


def test_transport_uses_opt_in_provider_capabilities_and_redacts_errors(setup, monkeypatch):
    configured(monkeypatch)
    observed = {}

    def handler(request):
        observed.update(json.loads(request.content))
        return httpx.Response(401, text="secret-token-do-not-display")

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(llm.ModelError) as error:
        llm.request_json("JSON only", {"abstract": "test"}, "fixture-id")
    assert "secret-token" not in str(error.value)
    assert "response_format" not in observed
    assert "temperature" not in observed
    assert observed["max_tokens"] <= 8192


def test_manual_retry_remains_available_after_three_attempts(setup):
    client, _ = setup
    job_id = submit(client).json()["id"]
    for attempt in range(4):
        assert jobs.claim_job("one")
        jobs.fail_job(job_id, "one", "fixture failure")
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job['attempts'] == attempt + 1
        assert job['can_retry']
        assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200


def test_automatic_recovery_still_stops_after_three_attempts(setup):
    client, _ = setup
    job_id = submit(client).json()['id']
    for _ in range(3):
        assert jobs.claim_job('worker')
        expire(job_id)
    assert jobs.claim_job('next') is None
    assert jobs.get_job(job_id)['status'] == 'failed'
    assert jobs.get_job(job_id)['attempts'] == 3
    assert jobs.get_job(job_id)['can_retry']


def test_manual_retry_after_limit_still_requires_cost_confirmation(setup):
    client, _ = setup
    job_id = submit(client).json()['id']
    with closing(jobs.connect()) as conn, conn:
        conn.execute("UPDATE jobs SET status='needs_confirmation',attempts=5,model_inflight=1 WHERE id=?", (job_id,))
    job = jobs.get_job(job_id)
    assert job['can_retry'] and job['requires_confirmation']
    assert client.post(f'/api/jobs/{job_id}/retry').status_code == 409
    assert client.post(f'/api/jobs/{job_id}/retry',json={'confirm_duplicate_cost':True}).status_code == 200
    assert jobs.get_job(job_id)['attempts'] == 5


def test_real_ranker_title_evidence_is_supported(setup):
    from app.ranking import rank_paper
    from app.schemas import Profile
    client, paper = setup
    paper["title"] = "Multi-agent collaboration and benchmark"
    paper["recommendation"] = rank_paper(paper, Profile().model_dump(), "integration-run")
    assert any(item["location"] == "论文标题" for item in paper["recommendation"]["evidence"])
    job_id = submit(client).json()["id"]
    process_one("one")
    result = client.get(f"/api/jobs/{job_id}").json()
    assert result["status"] == "succeeded"
    assert "分析覆盖：标题与摘要" in result["result"]["markdown"]
    assert "Multi-agent collaboration and benchmark" in result["result"]["markdown"]


def test_reader_handles_no_recommendation(setup, monkeypatch):
    client, paper = setup
    configured(monkeypatch)
    paper["recommendation"] = None
    output = {key: {"text": "未知", "evidence": []} for key in ["research_question", "method", "experiments", "limitations", "research_relation"]}
    output["unknowns"] = []
    monkeypatch.setattr(llm, "request_json", lambda *args: (output, "fixture", {}))
    job_id = submit(client, "paper_reader").json()["id"]
    process_one("one")
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "succeeded"


def test_changed_provider_blocks_old_checkpoint_model_calls(setup, monkeypatch):
    client, _ = setup
    configured(monkeypatch)
    job_id = submit(client, "paper_translator").json()["id"]
    monkeypatch.setenv("LLM_BASE_URL", "https://another-provider.example/v1")
    get_settings.cache_clear()
    monkeypatch.setattr(llm, "request_json", lambda *args: pytest.fail("changed provider must not be called"))
    process_one("one")
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert "服务地址已改变" in job["error"]


def test_separate_worker_process_reads_persistent_queue(setup):
    from pathlib import Path
    import subprocess
    import sys
    client, _ = setup
    job_id = submit(client).json()["id"]
    completed = subprocess.run([sys.executable, "-m", "app.worker", "--once"],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True,
                               text=True, timeout=45)
    assert completed.returncode == 0, completed.stderr
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert job["result"]["coverage"] == "abstract"
    assert "Worker ready" in completed.stderr
    assert client.get(f"/api/jobs/{job_id}/artifact").status_code == 200


def test_translation_rejects_new_numbers(setup, monkeypatch):
    client, _ = setup
    configured(monkeypatch)
    def invent_number(system, data, request_id):
        return {"translation": "中文译文：" + data["paragraph"] + " 额外准确率 999%"}, "fixture", {}
    monkeypatch.setattr(llm, "request_json", invent_number)
    job_id = submit(client, "paper_translator").json()["id"]
    process_one("one")
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "partial"
    assert job["result"]["alignment"] == []
    assert job["translation_state"]["failures"]


def test_protection_keeps_signs_and_scientific_notation():
    source = "The changes are -12.5%, +3.2 and -1.2e-3."
    masked, values = skills.protect(source)
    assert values == ["-12.5%", "+3.2", "-1.2e-3"]
    assert "-__PP" not in masked
    assert skills.restore(masked, values) == source


def test_sse_terminal_snapshot_is_not_lost_between_event_read_and_state_read(setup, monkeypatch):
    client, _ = setup
    job_id = submit(client).json()["id"]
    original = jobs.get_job
    reads = 0
    def raced_snapshot(target_id):
        nonlocal reads
        reads += 1
        if reads == 2:
            jobs.cancel_job(job_id)
        return original(target_id)
    monkeypatch.setattr(jobs, "get_job", raced_snapshot)
    response = client.get(f"/api/jobs/{job_id}/events")
    snapshots = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert snapshots[0]["status"] == "queued"
    assert snapshots[-1]["status"] == "cancelled"


def test_changed_reader_interests_have_distinct_idempotency_hash(setup, monkeypatch):
    client, paper = setup
    configured(monkeypatch)
    first = submit(client, "paper_reader").json()["id"]
    paper["recommendation"]["interests"] = ["Agent 评测"]
    second = submit(client, "paper_reader").json()["id"]
    assert first != second



ICML_TITLE = r"\twocolumn[ \icmltitle{LLM-Guided Communication for Cooperative Multi-Agent \texorpdfstring{\\}{ } Reinforcement Learning}"


def test_latex_translation_preserves_template_in_code():
    segments, pieces = skills.latex_translation_plan(ICML_TITLE)
    assert len(segments) == 2
    assert all('\\' not in segment['text'] for segment in segments)
    output = {'segments': [{'id': item['id'], 'translation': '中文：' + item['text']} for item in reversed(segments)]}
    restored = skills.restore_latex_segments(output, segments, pieces)
    assert restored.startswith(r'\twocolumn[ \icmltitle{')
    assert r'\texorpdfstring{\\}{ }' in restored
    assert restored.endswith('Learning}')
    # Round trip is exact when prose is unchanged, including whitespace and delimiters.
    original = {'segments': [{'id': item['id'], 'translation': item['text']} for item in segments]}
    assert skills.restore_latex_segments(original, segments, pieces) == ICML_TITLE


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'extra', 'macro', 'placeholder'])
def test_latex_translation_rejects_corrupt_segments(fault):
    segments, pieces = skills.latex_translation_plan(ICML_TITLE)
    output = {'segments': [{'id': item['id'], 'translation': '中文译文'} for item in segments]}
    if fault == 'missing':
        output['segments'].pop()
    elif fault == 'duplicate':
        output['segments'].append(output['segments'][0])
    elif fault == 'extra':
        output['segments'].append({'id': 'unknown', 'translation': '多余'})
    else:
        output['segments'][0]['translation'] += r'\cite{fake}' if fault == 'macro' else '__PP_PROTECTED_0000__'
    with pytest.raises(ValueError):
        skills.restore_latex_segments(output, segments, pieces)


def test_latex_translation_retry_keeps_completed_chunks(setup, monkeypatch):
    client, paper = setup
    configured(monkeypatch)
    paper['abstract'] = ICML_TITLE + '\n\nSecond result: 5 agents.'
    calls = []
    def fake_request(system, data, request_id):
        calls.append(data['paragraph_id'])
        if 'segments' in data:
            return {'segments': [{'id': item['id'], 'translation': '中文：' + item['text']} for item in data['segments']]}, 'test-title', {}
        if calls.count(data['paragraph_id']) == 1:
            raise llm.ModelError('测试：第二段暂时限流')
        return {'translation': '中文：' + data['paragraph']}, 'test-body', {}
    monkeypatch.setattr(llm, 'request_json', fake_request)
    job_id = submit(client, 'paper_translator').json()['id']
    assert process_one('one')
    assert client.get(f'/api/jobs/{job_id}').json()['status'] == 'failed'
    assert client.post(f'/api/jobs/{job_id}/retry').status_code == 200
    assert process_one('two')
    final = client.get(f'/api/jobs/{job_id}').json()
    assert final['status'] == 'succeeded'
    assert calls.count('abstract-p001') == 1
    assert calls.count('abstract-p002') == 2
    assert final['result']['alignment'][0]['source'] == ICML_TITLE
    assert r'\twocolumn' in final['result']['alignment'][0]['translation']


LAYOUT_ONLY = r"\renewcommand{\theequation}{A.\arabic{equation}} \renewcommand{\theHequation}{A.\arabic{equation}} \setcounter{equation}{0}"


def test_nested_layout_commands_are_not_translated(setup, monkeypatch):
    from app.fulltext import is_layout_only, parse_latex
    client, paper = setup
    configured(monkeypatch)
    assert is_layout_only(LAYOUT_ONLY)
    assert not is_layout_only(LAYOUT_ONLY + ' Actual prose must not disappear.')
    assert not is_layout_only(LAYOUT_ONLY[:-1])
    assert not is_layout_only(r'\unknown{A}')
    source = ('\\documentclass{article}\n\\begin{document}\n' + LAYOUT_ONLY + '\n\\end{document}').encode()
    blocks, _ = parse_latex(source)
    assert len(blocks) == 1 and not blocks[0]['translatable']
    # A legacy snapshot marked it as prose: execution must still preserve it.
    paper['abstract'] = LAYOUT_ONLY
    monkeypatch.setattr(llm, 'request_json', lambda *args: pytest.fail('layout must not call a model'))
    job_id = submit(client, 'paper_translator').json()['id']
    assert process_one('layout-test')
    result = jobs.get_job(job_id)
    assert result['status'] == 'succeeded'
    assert result['result']['alignment'][0]['translation'] == LAYOUT_ONLY
    assert result['result']['alignment'][0]['preserved']
    from app.reading_api import alignment
    aligned = alignment(job_id)['blocks'][0]
    assert aligned['status'] == 'preserved' and not aligned['translatable']


def test_explicit_validation_retry_preserves_attempt_history(setup):
    client, _ = setup
    job_id = submit(client).json()['id']
    with closing(jobs.connect()) as conn, conn:
        conn.execute("UPDATE jobs SET skill_id='paper_translator', status='failed', attempts=3, model_inflight=0, error=? WHERE id=?", ('p0086 未通过译文校验：译文未包含中文', job_id))
    assert jobs.get_job(job_id)['can_retry']
    assert client.post(f'/api/jobs/{job_id}/retry').status_code == 200
    assert jobs.get_job(job_id)['attempts'] == 3
    with closing(jobs.connect()) as conn, conn:
        conn.execute("UPDATE jobs SET status='failed',model_inflight=1 WHERE id=?", (job_id,))
    assert jobs.get_job(job_id)['can_retry']
    assert jobs.get_job(job_id)['requires_confirmation']
    assert client.post(f'/api/jobs/{job_id}/retry').status_code == 409



def test_cross_block_code_is_preserved_without_model_calls(setup, monkeypatch):
    client, paper = setup
    configured(monkeypatch)
    fence = "'" * 3
    paper['abstract'] = r'{\begingroup \parindent=0pt ' + fence + r'python def fn(): \# comment' + '\n\n' + r'return message\_description ' + fence + r' \endgroup}'
    monkeypatch.setattr(llm, 'request_json', lambda *args: pytest.fail('source code must not call a model'))
    job_id = submit(client, 'paper_translator').json()['id']
    assert process_one('code-test')
    result = jobs.get_job(job_id)
    assert result['status'] == 'succeeded', result['error']
    assert len(result['result']['alignment']) == 2
    assert all(b['preserved'] and b['source'] == b['translation'] for b in result['result']['alignment'])
    from app.reading_api import alignment
    assert all(b['status'] == 'preserved' for b in alignment(job_id)['blocks'])



def test_validation_partial_continues_and_retries_only_missing(setup, monkeypatch):
    from app.reading_api import alignment
    client, paper = setup
    configured(monkeypatch)
    paper['abstract'] = 'First prose reports 3 tasks.\n\nMiddle prose reports 4 tasks.\n\nFinal prose reports 5 tasks.'
    calls = []
    fixed = False
    def model(system, payload, request_id):
        calls.append(payload['paragraph_id'])
        if payload['paragraph_id'] == 'abstract-p002' and not fixed:
            return {'translation': 'English output without Chinese'}, 'bad-response', {}
        return {'translation':'中文译文：' + payload['paragraph']}, 'valid-response', {}
    monkeypatch.setattr(llm, 'request_json', model)
    job_id = submit(client,'paper_translator').json()['id']
    assert process_one('first')
    partial = jobs.get_job(job_id)
    assert partial['status'] == 'partial'
    assert partial['can_retry']
    assert len(partial['translation_state']['failures']) == 1
    assert calls == ['abstract-p001','abstract-p002','abstract-p003']
    aligned = alignment(job_id)
    assert aligned['completed'] == 2
    assert aligned['blocks'][1]['status'] == 'failed'
    assert aligned['blocks'][1]['translation'] is None
    assert client.get(f'/api/jobs/{job_id}/artifact').status_code == 404
    assert 'partial' in client.get(f'/api/jobs/{job_id}/events').text
    fixed = True
    progress = []
    original_progress = jobs.update_progress
    def track_progress(job_id, owner, stage, percentage):
        progress.append(stage)
        return original_progress(job_id, owner, stage, percentage)
    monkeypatch.setattr(jobs, 'update_progress', track_progress)
    assert client.post(f'/api/jobs/{job_id}/retry').status_code == 200
    assert process_one('second')
    assert '处理段落 1/3' not in progress
    assert '段落检查点已保存 3/3' not in progress
    assert '段落检查点已保存 2/3' in progress
    final = jobs.get_job(job_id)
    assert final['status'] == 'succeeded'
    assert final['translation_state']['failures'] == []
    assert calls.count('abstract-p001') == calls.count('abstract-p003') == 1
    assert calls.count('abstract-p002') == 2
    assert client.get(f'/api/jobs/{job_id}/artifact').status_code == 200


@pytest.mark.parametrize('ambiguous', [False, True])
def test_network_failure_still_stops_and_retains_preflight(setup, monkeypatch, ambiguous):
    client, paper = setup
    configured(monkeypatch)
    paper['abstract'] = 'First useful prose.\n\nSecond useful prose.'
    calls = []
    def model(system, payload, request_id):
        calls.append(payload['paragraph_id'])
        raise llm.ModelError('网络故障测试', ambiguous=ambiguous)
    monkeypatch.setattr(llm,'request_json',model)
    job_id=submit(client,'paper_translator').json()['id']
    process_one('network-test')
    result=jobs.get_job(job_id)
    assert result['status'] == ('needs_confirmation' if ambiguous else 'failed')
    assert result['translation_state']['preflight']['total'] == 2
    assert calls == ['abstract-p001']


def test_identifier_is_preserved_but_ordinary_english_is_not(setup, monkeypatch):
    client,paper=setup
    configured(monkeypatch)
    paper['abstract']=r'SMAC-Comm: bane\_vs\_hM'+'\n\nWe evaluate agents on challenging tasks.'
    calls=[]
    def model(system,payload,request_id):
        calls.append(payload['paragraph_id'])
        return {'translation':payload['paragraph']},'untranslated',{}
    monkeypatch.setattr(llm,'request_json',model)
    job_id=submit(client,'paper_translator').json()['id'];process_one('names')
    result=jobs.get_job(job_id)
    assert result['status']=='partial'
    assert calls==['abstract-p002']
    assert result['result']['alignment'][0]['preserved']
    assert result['result']['alignment'][0]['translation']==r'SMAC-Comm: bane\_vs\_hM'


def test_invalid_json_isolated_and_unclosed_code_prechecked(setup, monkeypatch):
    client,paper=setup;configured(monkeypatch)
    paper['abstract']='First prose for validation.\n\nSecond valid prose.\n\n'+"'"*3+'python incomplete'
    calls=[]
    def model(system,payload,request_id):
        calls.append(payload['paragraph_id'])
        if payload['paragraph_id']=='abstract-p001':raise llm.ModelOutputError('无效 JSON')
        return {'translation':'中文：'+payload['paragraph']},'ok',{}
    monkeypatch.setattr(llm,'request_json',model)
    job_id=submit(client,'paper_translator').json()['id'];process_one('json-test')
    result=jobs.get_job(job_id)
    assert result['status']=='partial'
    assert calls==['abstract-p001','abstract-p002']
    assert len(result['translation_state']['failures'])==2
    assert result['translation_state']['preflight']['counts']['blocked']==1



def test_minipage_layout_recovery_preserves_existing_checkpoints(setup, monkeypatch):
    client, paper = setup
    configured(monkeypatch)
    header = r'\begin{minipage}[c]{0.54\textwidth} \centering'
    paper['abstract'] = 'First useful paragraph.\n\n' + header + '\n\n' + header + r' Actual prose. \end{minipage}'
    calls = []
    def model(system, payload, request_id):
        calls.append(payload['paragraph_id'])
        if 'segments' in payload:
            assert [s['text'] for s in payload['segments']] == ['Actual prose.']
            return {'segments':[{'id':'s0000','translation':'实际正文。'}]}, 'body', {}
        return {'translation':'首段正文。'}, 'first', {}
    monkeypatch.setattr(llm, 'request_json', model)
    job_id = submit(client, 'paper_translator').json()['id']
    assert process_one('layout-header')
    result = jobs.get_job(job_id)
    assert result['status'] == 'succeeded'
    assert calls == ['abstract-p001', 'abstract-p003']
    assert result['result']['alignment'][1]['preserved']
    assert result['result']['alignment'][1]['translation'] == header
    assert result['result']['alignment'][2]['translation'] == header + r' 实际正文。 \end{minipage}'


def test_task_list_keeps_latest_hundred_in_descending_order(setup):
    client, _ = setup
    original_id = submit(client).json()["id"]
    with closing(jobs.connect()) as conn, conn:
        original = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (original_id,)).fetchone())
        conn.execute("DELETE FROM job_events")
        conn.execute("DELETE FROM jobs")
        for i in range(105):
            row = {**original, "id": f"task-{i:03d}", "request_hash": f"hash-{i}",
                   "created_at": f"2026-01-01T00:{i//60:02d}:{i%60:02d}+00:00"}
            conn.execute("INSERT INTO jobs (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    result = client.get("/api/jobs")
    assert result.status_code == 200
    assert [row["id"] for row in result.json()["jobs"]] == [f"task-{i:03d}" for i in range(104, 4, -1)]
