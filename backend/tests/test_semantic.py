from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from app import jobs, llm, repository as repo
from app.config import get_settings
from app.demo import demo_papers
from app.main import app, match_score
from app.schemas import Profile
from app.semantic import snapshot
from app.worker import process_one


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_API_KEY", "test-only")
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    get_settings.cache_clear()
    with TestClient(app) as client:
        paper = deepcopy(demo_papers()[0])
        paper.update(mode="live", abstract="We train agents with delayed rewards. The policy learns to use tools across long interaction trajectories.")
        repo.upsert_paper(paper)
        repo.save_profile(Profile(interests=["Agent 强化学习", "需求理解"]))
        yield client, paper
    get_settings.cache_clear()


def output():
    return {"interests": [
        {"interest": "Agent 强化学习", "score": 90, "reason": "长程交互中的延迟奖励与策略学习直接相关。", "evidence": ["We train agents with delayed rewards."]},
        {"interest": "需求理解", "score": 0, "reason": "摘要未描述需求分析。", "evidence": []},
    ], "unknowns": ["实验细节未核实"]}


def submit(client, paper):
    response = client.post("/api/semantic/rank", json={"paper_ids": [paper["id"]]})
    assert response.status_code == 200, response.text
    return response.json()


def test_no_model_and_demo_fall_back_without_jobs(setup, monkeypatch):
    client, paper = setup
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    data = submit(client, paper)
    assert not data["jobs"]
    assert data["papers"][0]["semantic"]["status"] == "unavailable"
    assert "关键词" in data["papers"][0]["semantic"]["message"]
    response = client.post("/api/semantic/rank", json={"paper_ids": [demo_papers()[0]["id"]], "mode": "demo"})
    assert response.status_code == 200 and not response.json()["jobs"]


def test_evidence_scoring_all_interests_cache_and_sse(setup, monkeypatch):
    client, paper = setup
    calls = []
    def model(system, data, request_id):
        assert "untrusted DATA" in system
        assert set(data["research_interests"]) == {"Agent 强化学习", "需求理解"}
        assert "keyword_score" not in data  # Independent assessment, no anchoring to lexical score.
        calls.append(data)
        return output(), "fixture-request", {"total_tokens": 100}
    monkeypatch.setattr(llm, "request_json", model)
    first = submit(client, paper)["jobs"][0]
    assert submit(client, paper)["jobs"][0]["id"] == first["id"]
    assert process_one("semantic-test")
    state = client.get(f"/api/papers/{paper['id']}").json()["semantic"]
    assert state["status"] == "succeeded"
    assert state["result"]["score"] == 76.5  # 90 * .7 + 45 * .3, not supplied by the model.
    assert state["result"]["interests"][0]["evidence"][0]["location"].startswith("摘要字符 1")
    assert submit(client, paper)["jobs"][0]["id"] == first["id"]
    assert len(calls) == 1 and not process_one("duplicate")
    events = client.get(f"/api/jobs/{first['id']}/events").text
    assert '"status": "succeeded"' in events and 'id:' in events
    assert client.get(f"/api/jobs/{first['id']}/artifact").status_code == 200
    assert match_score({"semantic": state, "recommendation": {"score": 20}}) == 76.5
    assert match_score({"semantic": {"result": None}, "recommendation": {"score": 20}}) == 20


@pytest.mark.parametrize("invalid", ["quote", "missing", "duplicate", "positive_no_evidence", "out_of_range"])
def test_reject_unverifiable_model_output(setup, monkeypatch, invalid):
    client, paper = setup
    result = output()
    if invalid == "quote": result["interests"][0]["evidence"] = ["Invented experimental results."]
    if invalid == "missing": result["interests"].pop()
    if invalid == "duplicate": result["interests"][1] = result["interests"][0]
    if invalid == "positive_no_evidence": result["interests"][0]["evidence"] = []
    if invalid == "out_of_range": result["interests"][0]["score"] = 110
    monkeypatch.setattr(llm, "request_json", lambda *args: (result, "fixture-request", {}))
    submit(client, paper)
    assert process_one("invalid-output")
    state = snapshot(paper)
    assert state["status"] == "failed" and state["result"] is None
    assert "回退关键词" in state["message"]
    assert not state["job"]["requires_confirmation"]


def test_ambiguous_timeout_falls_back_and_never_auto_retries(setup, monkeypatch):
    client, paper = setup
    def fail(*args):
        raise llm.ModelError("请求结果待确认", ambiguous=True)
    monkeypatch.setattr(llm, "request_json", fail)
    first = submit(client, paper)["jobs"][0]
    process_one("timeout")
    state = snapshot(paper)
    assert state["status"] == "needs_confirmation" and state["result"] is None
    assert submit(client, paper)["jobs"][0]["id"] == first["id"]
    assert client.post(f"/api/jobs/{first['id']}/retry").status_code == 409


def test_cache_invalidates_on_interest_source_and_model_changes(setup, monkeypatch):
    client, paper = setup
    first = submit(client, paper)["jobs"][0]
    repo.save_profile(Profile(interests=["需求理解", "Agent 强化学习"]))
    assert submit(client, paper)["jobs"][0]["id"] == first["id"]
    repo.save_profile(Profile(interests=["Agent 强化学习"]))
    assert snapshot(paper)["status"] == "not_started"
    second = submit(client, paper)["jobs"][0]
    assert second["id"] != first["id"]
    changed = {**paper, "version": paper["version"] + 1, "abstract": paper["abstract"] + " Updated evidence."}
    repo.upsert_paper(changed)
    assert snapshot(changed)["status"] == "not_started"
    third = submit(client, changed)["jobs"][0]
    assert third["id"] != second["id"]
    monkeypatch.setenv("LLM_MODEL", "another-model")
    get_settings.cache_clear()
    assert snapshot(changed)["status"] == "not_started"


def test_paid_response_checkpoint_survives_recovery_without_model_recall(setup, monkeypatch):
    from app.semantic import run
    from app.skills import run_skill
    client, paper = setup
    monkeypatch.setattr(llm, "request_json", lambda *args: (output(), "fixture-request", {}))
    job_id = submit(client, paper)["jobs"][0]["id"]
    jobs.claim_job("checkpoint-owner")
    first = run(jobs.job_record(job_id), "checkpoint-owner")
    def unexpected(*args):
        pytest.fail("Saved semantic checkpoint must not call model again")
    monkeypatch.setattr(llm, "request_json", unexpected)
    recovered = run_skill(jobs.job_record(job_id), "checkpoint-owner")
    assert first["score"] == recovered["score"]


def test_batch_bounds_empty_interests_and_read_only_get(setup):
    client, paper = setup
    assert client.get(f"/api/papers/{paper['id']}").status_code == 200
    assert not client.get("/api/jobs").json()["jobs"]
    assert client.post("/api/semantic/rank", json={"paper_ids": [paper["id"]] * 11}).status_code == 422
    repo.save_profile(Profile(interests=[]))
    assert not submit(client, paper)["jobs"]
    response = client.post("/api/jobs", json={"skill_id": "semantic_ranker", "paper_id": paper["id"], "scope": "fulltext"})
    assert response.status_code == 422


def test_batch_dedup_and_keyword_comparison_follows_saved_interests(setup, monkeypatch):
    from app.ranking import rank_paper
    client, paper = setup
    repo.save_recommendation("old-search", paper, rank_paper(paper, {"interests": ["agents"]}, "old-search"))
    repo.save_profile(Profile(interests=["不存在的研究方向"]))
    refreshed = client.get(f"/api/papers/{paper['id']}").json()
    assert refreshed["recommendation"]["score"] == 0
    monkeypatch.setattr(llm, "request_json", lambda *args: ({"interests": [
        {"interest": "不存在的研究方向", "score": 0, "reason": "摘要未提供关联证据。", "evidence": []}], "unknowns": []}, "fixture-request", {}))
    response = client.post("/api/semantic/rank", json={"paper_ids": [paper["id"], paper["id"]]}).json()
    assert len(response["jobs"]) == 1
    process_one("zero-score")
    assert snapshot(paper)["result"]["score"] == 0
    assert match_score({"semantic": snapshot(paper), "recommendation": {"score": 90}}) == 0


def test_source_span_ids_resolve_to_original_abstract(setup, monkeypatch):
    client, paper = setup
    def model(system, data, request_id):
        assert "NEVER the title" in system
        assert "NOT thereby an RL-trained agent" in data["interest_guidance"]["Agent 强化学习"]
        spans = data["evidence_spans"]
        assert all(span["text"] in paper["abstract"] for span in spans)
        result = output()
        result["interests"][0]["evidence"] = [spans[0]["id"]]
        return result, "span-request", {}
    monkeypatch.setattr(llm, "request_json", model)
    submit(client, paper)
    process_one("span-test")
    result = snapshot(paper)["result"]
    assert result["method"] == "semantic-v2"
    assert result["interests"][0]["evidence"][0]["text"] == "We train agents with delayed rewards."
    assert "摘要字符 1" in result["interests"][0]["evidence"][0]["location"]


@pytest.mark.parametrize("quote", ["A999", "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation"])
def test_unknown_span_and_title_are_not_abstract_evidence(setup, monkeypatch, quote):
    client, paper = setup
    result = output()
    result["interests"][0]["evidence"] = [quote]
    monkeypatch.setattr(llm, "request_json", lambda *args: (result, "bad-span", {}))
    submit(client, paper)
    process_one("bad-span-test")
    assert snapshot(paper)["status"] == "failed"
    assert snapshot(paper)["result"] is None


def test_semantic_registry_exposes_actual_prompt_version(setup):
    from app.skills import registry
    from app.semantic import VERSION
    entry = next(item for item in registry() if item["id"] == "semantic_ranker")
    assert entry["version"] == VERSION == "semantic-v2"


@pytest.mark.parametrize("count,bad_quote,expected", [(4, False, "succeeded"), (4, True, "failed"), (9, False, "failed")])
def test_extra_evidence_is_bounded_and_every_quote_checked(setup, monkeypatch, count, bad_quote, expected):
    client, paper = setup
    result = output()
    result["interests"][0]["evidence"] = ["A001"] * count
    if bad_quote:
        result["interests"][0]["evidence"][-1] = "Invented fourth evidence must not be silently dropped."
    monkeypatch.setattr(llm, "request_json", lambda *args: (result, "extra-evidence", {}))
    submit(client, paper)
    process_one("extra-evidence-test")
    assert snapshot(paper)["status"] == expected
