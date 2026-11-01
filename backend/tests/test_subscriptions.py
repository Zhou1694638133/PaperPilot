from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app import jobs, repository as repo, subscriptions as sub
from app.config import get_settings
from app.worker import process_one

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    repo.init_db()
    jobs.init_jobs()
    app = FastAPI()
    app.include_router(sub.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()

def create(client, **kw):
    r = client.post("/api/subscriptions", json={"name":"Agent RL 周报", "topic":"Agent 强化学习", **kw})
    assert r.status_code == 201, r.text
    return r.json()

def paper(pid="2601.00001", age=60, version=1):
    now = datetime.now(timezone.utc)
    return dict(id=pid, version=version, mode="live", source="arxiv", title="Agent reinforcement learning",
                abstract="Agent reinforcement learning improves credit assignment. Experiments remain to be verified.",
                authors=["Test"], categories=["cs.AI"], url=f"https://arxiv.org/abs/{pid}",
                pdf_url=f"https://arxiv.org/pdf/{pid}", published=(now-timedelta(days=age)).isoformat(),
                updated=now.isoformat(), fetched_at=now.isoformat())

def feed(papers):
    return {"papers":papers,"query":'all:"reinforcement learning" AND submittedDate:[20241029 TO 20261029]',
            "total":len(papers),"cached":False,"fetched_at":jobs.utc_now()}

def run(client, sid):
    r = client.post(f"/api/subscriptions/{sid}/run")
    assert r.status_code == 200, r.text
    return r.json()["job"]

def test_opt_in_validation_schedule_and_idempotence(client):
    assert sub.enqueue_due() == []
    s = create(client)
    assert s["enabled"] is False and s["next_run"] is None and s["days"] == 730
    assert sub.enqueue_due() == []
    assert client.post("/api/subscriptions",json={"name":"x","topic":"x","days":731}).status_code == 422
    assert client.post("/api/subscriptions",json={"name":" ","topic":"x"}).status_code == 422
    assert "reinforcement learning" in s["query"] and "AND" in s["query"]
    j = run(client,s["id"])
    assert run(client,s["id"])["id"] == j["id"]
    client.put(f'/api/subscriptions/{s["id"]}/enabled',json={"enabled":True})
    with repo.connect() as db:
        db.execute("UPDATE subscriptions SET next_run=? WHERE id=?", ("2000-01-01T00:00:00+00:00",s["id"]))
    assert sub.enqueue_due() == [j["id"]]
    assert sub.enqueue_due() == []
    assert sub.next_run({"weekday":0,"hour":9},datetime(2026,10,25,23,tzinfo=timezone.utc)) == "2026-10-26T01:00:00+00:00"
    assert sub.week_key(datetime(2026,10,25,17,tzinfo=timezone.utc)) == "2026-10-26"

def test_weekly_worker_no_model_dates_dedup_evidence_download(client, monkeypatch):
    s=create(client)
    monkeypatch.setattr(sub.conference_discovery,"search",lambda *a:feed([paper(version=1),paper(version=2),paper("2201.00001",900),paper("2701.00001",-2)]))
    from app import llm
    monkeypatch.setattr(llm,"request_json",lambda *a,**kw:pytest.fail("weekly digest must not call a model"))
    j=run(client,s["id"])
    assert process_one("weekly-test")
    done=jobs.get_job(j["id"])
    assert done["status"] == "succeeded",done
    response=client.get(f'/api/weekly-reports/{j["id"]}')
    assert response.status_code==200,response.text
    report=response.json()
    assert report["paper_count"] == 1 and report["papers"][0]["version"]==2
    assert report["model_calls"]==0 and not report["model_configured"]
    assert "模型未配置" in report["note"]
    assert report["papers"][0]["recommendation"]["evidence"]
    assert report["verified_count"]==0 and "回退" in report["warnings"][-1]
    assert report["new_count"]==1 and report["read_at"] is None
    assert client.put(f'/api/weekly-reports/{j["id"]}/read').status_code==200
    assert client.get(f'/api/weekly-reports/{j["id"]}').json()["read_at"]
    artifact=client.get(f'/api/jobs/{j["id"]}/artifact')
    assert artifact.status_code==200 and "Agent RL" in artifact.text
    assert "attachment" in artifact.headers["content-disposition"]

def test_feedback_persistence_undo_and_hidden_weekly(client,monkeypatch):
    p=paper();repo.upsert_paper(p)
    original=repo.get_profile()
    r=client.put("/api/feedback/"+p["id"],json={"value":"not_relevant","reason":"偏离方向"})
    assert r.status_code==200
    assert sub.feedback_for(p["id"])["value"]=="not_relevant"
    assert sub.feedback_for(p["id"],"demo")["value"]=="none"
    monkeypatch.setattr(sub.conference_discovery,"search",lambda *a:feed([p]))
    j=run(client,create(client)["id"]);process_one("feedback-test")
    assert client.get(f'/api/weekly-reports/{j["id"]}').json()["paper_count"]==0
    r=client.put("/api/feedback/"+p["id"],json={"value":"lacks_experiments"})
    assert r.json()["adjustment"]==-3
    assert repo.get_profile()==original
    assert client.get("/api/feedback").json()["feedback"][0]["context"]["subjective"] is True
    client.put("/api/feedback/"+p["id"],json={"value":"none"})
    assert client.get("/api/feedback").json()["feedback"]==[]

def test_failure_retry_uses_source_checkpoint(client,monkeypatch):
    s=create(client)
    calls=[]
    monkeypatch.setattr(sub.conference_discovery,"search",lambda *a:(calls.append(1) or feed([paper()])))
    actual=repo.save_recommendations
    monkeypatch.setattr(repo,"save_recommendations",lambda *a:(_ for _ in ()).throw(ValueError("test interruption")))
    j=run(client,s["id"]);process_one("retry-test")
    assert jobs.get_job(j["id"])["status"]=="failed"
    assert client.get(f'/api/weekly-reports/{j["id"]}').status_code==409
    assert run(client,s["id"])["id"]==j["id"]
    monkeypatch.setattr(repo,"save_recommendations",actual)
    retry=client.post(f'/api/jobs/{j["id"]}/retry')
    assert retry.status_code==200,retry.text
    process_one("retry-test")
    assert jobs.get_job(j["id"])["status"]=="succeeded",jobs.get_job(j["id"])
    assert len(calls)==1

def test_pausing_scheduled_job_and_manual_still_allowed(client):
    s=create(client,enabled=True)
    with repo.connect() as db:
        db.execute("UPDATE subscriptions SET next_run='2000-01-01T00:00:00+00:00'")
    jid=sub.enqueue_due()[0]
    client.put(f'/api/subscriptions/{s["id"]}/enabled',json={"enabled":False})
    process_one("pause-test")
    assert jobs.get_job(jid)["status"]=="cancelled"
    assert sub.enqueue_due()==[]


def test_verified_priority_and_concurrent_submission(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.research import VERIFIED
    verified=paper("2505.10978");verified["title"]=VERIFIED["2505.10978"]["title"]
    unknown=paper()
    monkeypatch.setattr(sub.conference_discovery,"search",lambda *a:feed([unknown,verified]))
    s=create(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda _:sub.run_now(s["id"])["job"]["id"],range(4)))
    assert len(set(ids))==1
    process_one("priority-test")
    report=sub.get_report(ids[0])
    assert report["papers"][0]["id"]==verified["id"]
    assert report["verified_count"]==1
    assert report["papers"][0]["publication"]["evidence_url"].startswith("https://proceedings.neurips.cc/")
    assert "会议录用状态未核实" not in report["papers"][0]["recommendation"]["unknowns"]

def test_source_failure_and_explicit_retry_paused_subscription(client,monkeypatch):
    s=create(client,enabled=True)
    with repo.connect() as db:
        db.execute("UPDATE subscriptions SET next_run='2000-01-01T00:00:00+00:00'")
    jid=sub.enqueue_due()[0]
    client.put(f'/api/subscriptions/{s["id"]}/enabled',json={"enabled":False})
    process_one("paused")
    assert jobs.get_job(jid)["status"]=="cancelled"
    client.post(f"/api/jobs/{jid}/retry")
    monkeypatch.setattr(sub.conference_discovery,"search",lambda *a:(_ for _ in ()).throw(sub.SourceError("upstream unavailable")))
    process_one("failed")
    assert jobs.get_job(jid)["status"]=="failed"
    assert "upstream unavailable" in jobs.get_job(jid)["error"]
    assert client.get(f"/api/jobs/{jid}/artifact").status_code==404
    monkeypatch.setattr(sub.conference_discovery,"search",lambda *a:feed([]))
    client.post(f"/api/jobs/{jid}/retry")
    process_one("retry")
    assert jobs.get_job(jid)["status"]=="succeeded"
    assert sub.get_report(jid)["paper_count"]==0

def test_revision_and_first_seen_per_subscription(client,monkeypatch):
    s=create(client,source="arxiv")
    monkeypatch.setattr(sub.ArxivSource,"search",lambda *a:feed([paper()]))
    j=run(client,s["id"]);process_one("first")
    body={k:v for k,v in s.items() if k in sub.SubscriptionInput.model_fields}
    body["name"]="新名称"
    same=client.put("/api/subscriptions/"+s["id"],json=body).json()
    assert same["revision"]==1 and run(client,s["id"])["id"]==j["id"]
    body["limit"]=2
    changed=client.put("/api/subscriptions/"+s["id"],json=body).json()
    assert changed["revision"]==2
    j2=run(client,s["id"]);process_one("second")
    assert j2["id"]!=j["id"] and sub.get_report(j2["id"])["new_count"]==0
    assert sub.get_report(j["id"])["new_count"]==1

def test_discovery_feedback_keeps_base_score(client,monkeypatch):
    from app.main import app
    from app import main
    from app.fulltext import init_documents
    init_documents()
    p=paper()
    monkeypatch.setattr(main.ArxivSource,"search",lambda *a:feed([p]))
    with TestClient(app) as http:
        a=http.get("/api/papers",params={"query":"all:agent","sort":"match"}).json()["papers"][0]
        http.put("/api/feedback/"+p["id"],json={"value":"relevant"})
        b=http.get("/api/papers",params={"query":"all:agent","sort":"match"}).json()["papers"][0]
        assert a["recommendation"]["score"]==b["recommendation"]["score"]
        assert main.match_score(b)==main.match_score(a)+5
        http.put("/api/feedback/"+p["id"],json={"value":"not_relevant"})
        assert http.get("/api/papers",params={"query":"all:agent","sort":"match"}).json()["papers"]==[]
        http.put("/api/feedback/"+p["id"],json={"value":"none"})
        assert len(http.get("/api/papers",params={"query":"all:agent","sort":"match"}).json()["papers"])==1

def test_edit_name_does_not_postpone_due_subscription(client):
    s = create(client, enabled=True)
    due = "2000-01-01T00:00:00+00:00"
    with repo.connect() as db:
        db.execute("UPDATE subscriptions SET next_run=? WHERE id=?", (due, s["id"]))
    body = {k:v for k,v in s.items() if k in sub.SubscriptionInput.model_fields}
    body["name"] = "只修改显示名称"
    updated = client.put("/api/subscriptions/" + s["id"], json=body).json()
    assert updated["next_run"] == due
    assert len(sub.enqueue_due()) == 1

def test_subscription_create_retries_share_identity(client):
    from concurrent.futures import ThreadPoolExecutor
    body = {"name":"重复提交验收","topic":"Agent 强化学习"}
    def submit(_):
        r = client.post("/api/subscriptions",json=body,headers={"Idempotency-Key":"same-create-request"})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(submit, range(4)))
    assert len(set(ids)) == 1
    conflict = client.post("/api/subscriptions",json={**body,"topic":"大模型后训练"},headers={"Idempotency-Key":"same-create-request"})
    assert conflict.status_code == 409

def test_scheduler_runs_while_execution_is_busy(client, monkeypatch):
    import signal
    import threading
    from app import worker
    handlers, calls = {}, []
    tick_during_execution = threading.Event()
    busy = threading.Event()
    monkeypatch.setattr(worker, "SCHEDULE_INTERVAL", 0.01, raising=False)
    monkeypatch.setattr("sys.argv", ["worker"])
    monkeypatch.setattr(signal, "signal", lambda signum, handler: handlers.update({signum:handler}))
    def schedule():
        calls.append(True)
        if busy.is_set():
            tick_during_execution.set()
    monkeypatch.setattr(sub, "enqueue_due", schedule)
    def execute(_):
        busy.set()
        observed = tick_during_execution.wait(0.5)
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return observed
    monkeypatch.setattr(worker, "process_one", execute)
    worker.main()
    assert tick_during_execution.is_set(), "long task blocked subscription scheduler"

def test_relevant_feedback_restores_legacy_ignored_paper(client, monkeypatch):
    from app.main import app
    from app import main
    p = paper()
    repo.upsert_paper(p)
    repo.update_collection(p["id"],"live",{"interested":False,"saved":True,"notes":"保留笔记","tags":["test"]})
    monkeypatch.setattr(main.ArxivSource,"search",lambda *a:feed([p]))
    with TestClient(app) as http:
        assert http.get("/api/papers",params={"query":"all:agent","sort":"match"}).json()["papers"] == []
        assert http.put("/api/feedback/"+p["id"],json={"value":"relevant"}).status_code == 200
        visible = http.get("/api/papers",params={"query":"all:agent","sort":"match"}).json()["papers"]
        assert len(visible) == 1
        assert visible[0]["collection"]["notes"] == "保留笔记"
        assert visible[0]["collection"]["saved"] and visible[0]["collection"]["tags"] == ["test"]

def test_writes_from_local_frontend_5174(client):
    from app.main import app
    with TestClient(app) as http:
        response = http.post("/api/subscriptions",json={"name":"备用端口验收","topic":"Agent 强化学习"},
                             headers={"Origin":"http://127.0.0.1:5174"})
        assert response.status_code == 201, response.text
        assert http.post("/api/subscriptions",json={"name":"x","topic":"x"},
                         headers={"Origin":"https://untrusted.example"}).status_code == 403

def test_feedback_changes_actual_weekly_order_and_undo(client,monkeypatch):
    a, b = paper("2601.00001"), paper("2601.00002")
    b["published"] = a["published"]
    repo.upsert_papers([a,b])
    monkeypatch.setattr(sub.ArxivSource,"search",lambda *args:feed([a,b]))
    client.put("/api/feedback/"+b["id"],json={"value":"relevant"})
    j = run(client, create(client,source="arxiv")["id"]); process_one("feedback-order")
    first = sub.get_report(j["id"])
    assert [p["id"] for p in first["papers"]] == [b["id"],a["id"]]
    assert first["papers"][0]["recommendation"]["score"] == first["papers"][1]["recommendation"]["score"]
    client.put("/api/feedback/"+b["id"],json={"value":"none"})
    j2 = run(client,create(client,source="arxiv")["id"]); process_one("feedback-undo")
    assert [p["id"] for p in sub.get_report(j2["id"])["papers"]] == [a["id"],b["id"]]
    # Historical order is frozen even though current feedback is shown.
    assert [p["id"] for p in sub.get_report(j["id"])["papers"]] == [b["id"],a["id"]]

