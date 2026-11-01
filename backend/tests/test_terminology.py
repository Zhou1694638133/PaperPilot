import pytest
from fastapi.testclient import TestClient

from app import fulltext, jobs, llm, repository as repo, skills
from app.config import get_settings
from app.main import app
from app.worker import process_one


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_API_KEY", "test-only")
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    get_settings.cache_clear()
    with TestClient(app) as client:
        repo.upsert_paper({"id": "2501.00001", "version": 1, "mode": "live", "title": "Agent training",
            "abstract": "We use advantage estimation for reinforcement learning.", "authors": [],
            "published": "2025-01-01T00:00:00Z", "updated": "2025-01-01T00:00:00Z",
            "categories": ["cs.AI"], "url": "https://arxiv.org/abs/2501.00001", "fetched_at": "2026-10-25T00:00:00Z"})
        yield client
    get_settings.cache_clear()


def mark(client, **patch):
    response = client.put("/api/collections/2501.00001", json=patch)
    assert response.status_code == 200
    return response.json()["terminology"]


def term(source="advantage estimation", target="优势估计"):
    return {"source": source, "target": target,
            "evidence": "We use advantage estimation for reinforcement learning.", "location": "abstract"}


def mock_model(monkeypatch, terms):
    calls = []
    def request(system, payload, request_id):
        calls.append(payload)
        return {"terms": terms}, "fixture-request", {}
    monkeypatch.setattr(llm, "request_json", request)
    return calls


@pytest.mark.parametrize("patch", [{"saved": True}, {"read": True}, {"tags": ["RL"]}, {"notes": "方法有参考价值"}])
def test_positive_marks_enqueue_and_batch_reuses_job(client, patch):
    first = mark(client, **patch)["job"]
    assert first["skill_id"] == "glossary_extractor"
    second = client.post("/api/glossary/learn").json()
    assert second["eligible_count"] == 1
    assert second["jobs"][0]["id"] == first["id"]
    assert mark(client, read=True)["job"]["id"] == first["id"]


def test_unmarked_ignored_demo_and_missing_model(client, monkeypatch):
    assert client.post("/api/glossary/learn").json()["eligible_count"] == 0
    response = client.post("/api/jobs", json={"skill_id": "glossary_extractor", "paper_id": "2501.00001"})
    assert response.status_code == 422
    assert mark(client, saved=True, interested=False)["job"] is None
    demo = client.get("/api/papers", params={"mode": "demo", "days": 0, "query": "all:agent"}).json()["papers"][0]
    result = client.put(f"/api/collections/{demo['id']}?mode=demo", json={"saved": True})
    assert result.json()["terminology"]["job"] is None
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    state = mark(client, interested=True)
    assert state["job"] is None and "模型未配置" in state["message"]
    assert client.post("/api/glossary/learn").json()["warnings"]


def test_merge_preserves_manual_terms_and_translation_snapshots(client, monkeypatch):
    initial = fulltext.glossary_get()
    first_translation = client.post("/api/jobs", json={"skill_id": "paper_translator", "paper_id": "2501.00001"}).json()
    client.post(f"/api/jobs/{first_translation['id']}/cancel")
    extraction = mark(client, saved=True)["job"]
    calls = mock_model(monkeypatch, [term(), term("reinforcement learning", "增强学习")])
    fulltext.glossary_save({**initial["terms"], "manual term": "人工术语"}, initial["version"])
    assert process_one("test-worker")
    job = jobs.get_job(extraction["id"])
    assert job["status"] == "succeeded"
    result = job["result"]
    current = fulltext.glossary_get()
    assert current["terms"]["advantage estimation"] == "优势估计"
    assert current["terms"]["reinforcement learning"] == "强化学习"
    assert current["terms"]["manual term"] == "人工术语"
    assert result["conflicts"][0]["existing_target"] == "强化学习"
    assert result["added"] == ["advantage estimation"]
    assert result["terms"][0]["evidence"] in result["markdown"]
    assert jobs.job_record(first_translation["id"])["input"]["glossary_version"] == initial["version"]
    second = client.post("/api/jobs", json={"skill_id": "paper_translator", "paper_id": "2501.00001"}).json()
    assert jobs.job_record(second["id"])["input"]["glossary"]["advantage estimation"] == "优势估计"
    assert client.post("/api/glossary/learn").json()["jobs"][0]["id"] == extraction["id"]
    assert len(calls) == 1
    assert client.put("/api/glossary", json={"version": initial["version"], "terms": {}}).status_code == 409


@pytest.mark.parametrize("terms", [
    [term("fabricated term")], [term(target="English only")],
    [term(), term("ADVANTAGE ESTIMATION")], [term(source="advantage\nestimation")],
    [{**term(), "evidence": "This quote does not occur."}],
])
def test_invalid_model_output_never_changes_glossary(client, monkeypatch, terms):
    initial = fulltext.glossary_get()
    job = mark(client, saved=True)["job"]
    mock_model(monkeypatch, terms)
    assert process_one("test-worker")
    assert jobs.get_job(job["id"])["status"] == "failed"
    assert fulltext.glossary_get() == initial


def test_checkpoint_resume_merges_without_second_model_call(client, monkeypatch):
    job = mark(client, saved=True)["job"]
    calls = mock_model(monkeypatch, [term()])
    record = jobs.claim_job("worker")
    skills.run_skill(record, "worker")
    record = jobs.job_record(job["id"])
    result = skills.run_skill(record, "worker")
    initial = fulltext.glossary_get()
    fulltext.glossary_save({**initial["terms"], "ADVANTAGE ESTIMATION": "人工优势估计"}, initial["version"])
    jobs.finish_job(job["id"], "worker", result)
    assert len(calls) == 1
    current = fulltext.glossary_get()
    assert "advantage estimation" not in current["terms"]
    assert current["version"] == initial["version"] + 1
    assert jobs.get_job(job["id"])["result"]["conflicts"]


@pytest.mark.parametrize("change", ["unmark", "ignore", "version"])
def test_changed_paper_during_model_call_cannot_merge(client, monkeypatch, change):
    initial = fulltext.glossary_get()
    job = mark(client, saved=True)["job"]
    def request(*args):
        if change == "version":
            paper = repo.get_paper("2501.00001")
            repo.upsert_paper({**paper, "version": 2})
        else:
            repo.update_collection("2501.00001", "live", {"saved": False} if change == "unmark" else {"interested": False})
        return {"terms": [term()]}, None, {}
    monkeypatch.setattr(llm, "request_json", request)
    assert process_one("test-worker")
    assert jobs.get_job(job["id"])["status"] == "failed"
    assert fulltext.glossary_get() == initial


def test_full_glossary_keeps_candidates_without_overflow(client, monkeypatch):
    fulltext.glossary_save({f"term{i}": f"术语{i}" for i in range(100)}, 1)
    job = mark(client, read=True)["job"]
    mock_model(monkeypatch, [term()])
    assert process_one("test-worker")
    result = jobs.get_job(job["id"])["result"]
    assert result["skipped_capacity"] == ["advantage estimation"]
    assert result["glossary_version"] == 2
    assert len(fulltext.glossary_get()["terms"]) == 100


def test_empty_extraction_does_not_bump_version(client, monkeypatch):
    job = mark(client, read=True)["job"]
    mock_model(monkeypatch, [])
    assert process_one("test-worker")
    assert jobs.get_job(job["id"])["status"] == "succeeded"
    assert fulltext.glossary_get()["version"] == 1
