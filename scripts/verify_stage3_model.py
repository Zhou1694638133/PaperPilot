"""Explicit live audit: real sources/model, isolated DB; never copies keys to disk.
Run with backend/.venv/Scripts/python.exe scripts/verify_stage3_model.py --phase reports|model|feedback.
The model phase requires --run-model and may incur provider charges.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import threading
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import get_settings


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["reports", "model", "diagnose", "replay", "feedback"], required=True)
    parser.add_argument("--run-model", action="store_true")
    args = parser.parse_args()
    if args.phase in {"model", "diagnose"} and not args.run_model:
        parser.error("Actual model calls require --run-model")
    # Read the configured service before switching to the isolated database.
    settings = get_settings()
    runtime = ROOT / ".stage3-model-verify"
    output = ROOT / "output" / "stage3-model-verification"
    output.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ.update(DATABASE_PATH=str(runtime / "test.db"), LLM_API_KEY=settings.llm_api_key,
                      LLM_BASE_URL=settings.llm_base_url, LLM_MODEL=settings.llm_model)
    get_settings.cache_clear()
    assert get_settings().database_path.parent == runtime
    from fastapi.testclient import TestClient
    from app.main import app
    from app import jobs, repository as repo, subscriptions as sub
    from app.worker import process_one
    from app.research import TRACKS
    from app.schemas import Profile
    owner = "stage3-live-audit"
    stop = threading.Event()

    def pulse():
        while not stop.is_set():
            jobs.heartbeat(owner)
            stop.wait(5)

    def complete(jid):
        job = jobs.get_job(jid)
        if job["status"] == "queued":
            assert process_one(owner)
            job = jobs.get_job(jid)
        print(json.dumps({"job": jid, "status": job["status"], "error": job["error"]}, ensure_ascii=False), flush=True)
        return job

    def request(client, method, url, **kwargs):
        response = client.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {url}: {response.status_code}: {response.text[:500]}")
        return response.json()

    with TestClient(app) as client:
        repo.save_profile(Profile(interests=["Agent 强化学习", "大模型后训练"], days=730))
        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            if args.phase == "reports":
                for track in TRACKS:
                    body = {"name": track["name"] + " · 真实模型验收", "topic": track["name"],
                            "source": "ccfa_first", "days": 730, "limit": 4, "enabled": False}
                    subscription = request(client, "POST", "/api/subscriptions", json=body,
                                           headers={"Idempotency-Key": "live-model-audit-20261029-" + track["id"]})
                    jid = request(client, "POST", f"/api/subscriptions/{subscription['id']}/run")["job"]["id"]
                    print("Generating " + track["id"], flush=True)
                    job = complete(jid)
                    if job["status"] != "succeeded":
                        write(output / (track["id"] + "-error.json"), job)
                        continue
                    report = request(client, "GET", f"/api/weekly-reports/{jid}")
                    start = datetime.fromisoformat(report["window_start"])
                    end = datetime.fromisoformat(report["window_end"])
                    assert all(start <= datetime.fromisoformat(p["published"].replace("Z", "+00:00")) <= end for p in report["papers"])
                    flags = [p["publication"]["status"] == "verified" for p in report["papers"]]
                    assert flags == sorted(flags, reverse=True)
                    duplicate = request(client, "POST", f"/api/subscriptions/{subscription['id']}/run")
                    assert duplicate["job"]["id"] == jid
                    write(output / (track["id"] + "-report.json"), report)
                    (output / (track["id"] + "-rules.md")).write_text(report["markdown"], encoding="utf-8")
                    print(json.dumps({"track": track["id"], "papers": [{"id": p["id"], "title": p["title"], "score": p["recommendation"]["score"], "venue": p["publication"]["status"]} for p in report["papers"]], "warnings": report["warnings"]}, ensure_ascii=False), flush=True)
            elif args.phase == "model":
                connection_path = output / "connection.json"
                if not connection_path.exists() or not json.loads(connection_path.read_text(encoding="utf-8-sig")).get("ok"):
                    from app.model_config import public_config
                    config = public_config()
                    connected = request(client, "POST", "/api/model-config/test", json={
                        "base_url": config["base_url"], "model": config["model"], "revision": config["revision"]})
                    write(connection_path, {"model": config["model"], "tested_at": jobs.utc_now(), **connected})
                    print("Connection: " + str(connected["ok"]), flush=True)
                connection = json.loads(connection_path.read_text(encoding="utf-8-sig"))
                if not connection.get("ok"):
                    raise RuntimeError("Connection test has not succeeded; no paper model calls will be submitted")
                papers = {}
                for track in TRACKS:
                    path = output / (track["id"] + "-report.json")
                    if path.exists():
                        for paper in json.loads(path.read_text(encoding="utf-8"))["papers"]:
                            papers[paper["id"]] = paper
                if not papers:
                    raise RuntimeError("No real weekly reports available")
                records = []
                for pid, paper in papers.items():
                    batch = request(client, "POST", "/api/semantic/rank", json={"paper_ids": [pid], "mode": "live"})
                    if not batch["jobs"]:
                        raise RuntimeError("Model unavailable")
                    job = complete(batch["jobs"][0]["id"])
                    record = {"paper": paper, "job": job}
                    if job["status"] == "succeeded":
                        result = job["result"]
                        assert all(e["text"] in paper["abstract"] for interest in result["interests"] for e in interest["evidence"])
                        again = request(client, "POST", "/api/semantic/rank", json={"paper_ids": [pid], "mode": "live"})
                        assert again["jobs"][0]["id"] == job["id"]
                        print(json.dumps({"paper": pid, "scores": [{"topic": i["interest"], "score": i["score"], "reason": i["reason"]} for i in result["interests"]], "usage": result["usage"]}, ensure_ascii=False), flush=True)
                    records.append(record)
                    write(output / "semantic-results.json", records)
            elif args.phase == "diagnose":
                from app import llm
                original_call = llm.request_json
                captures = []
                def capture(system, data, request_id, **kwargs):
                    response = original_call(system, data, request_id, **kwargs)
                    captures.append({"request": request_id, "output": response[0], "provider_request_id": response[1], "usage": response[2]})
                    write(output / "validation-diagnostics.json", captures)
                    return response
                llm.request_json = capture
                try:
                    records = json.loads((output / "semantic-results.json").read_text(encoding="utf-8"))
                    for entry in records:
                        job = jobs.get_job(entry["job"]["id"])
                        if job["status"] == "failed" and not job["requires_confirmation"]:
                            request(client, "POST", "/api/jobs/" + job["id"] + "/retry")
                            entry["job"] = complete(job["id"])
                    write(output / "semantic-results.json", records)
                finally:
                    llm.request_json = original_call
            elif args.phase == "replay":
                # Revalidate captured real responses, retaining their provider IDs and usage; no HTTP/model request.
                from app import llm
                captures = {entry["request"]: entry for entry in json.loads((output / "validation-diagnostics.json").read_text(encoding="utf-8"))}
                original_call = llm.request_json
                def replay(system, data, request_id, **kwargs):
                    saved = captures[request_id]
                    return saved["output"], saved["provider_request_id"], saved["usage"]
                llm.request_json = replay
                try:
                    records = json.loads((output / "semantic-results.json").read_text(encoding="utf-8"))
                    for entry in records:
                        job = jobs.get_job(entry["job"]["id"])
                        if job["status"] == "failed" and job["id"] + "-semantic-v2" in captures:
                            request(client, "POST", "/api/jobs/" + job["id"] + "/retry")
                            entry["job"] = complete(job["id"])
                            entry["revalidated_captured_response"] = True
                    write(output / "semantic-results.json", records)
                finally:
                    llm.request_json = original_call
            else:
                # Fixed, genuinely retrieved paper snapshots isolate feedback from source drift.
                # Only the retrieval boundary is replayed. API, scoring, DB, worker and model results are real.
                from app import conference_discovery
                snapshots = {}
                for track in TRACKS:
                    saved_report = json.loads((output / (track["id"] + "-report.json")).read_text(encoding="utf-8"))
                    snapshots[saved_report["subscription"]["query"]] = {
                        "papers": saved_report["papers"], "query": saved_report["query"],
                        "total": saved_report["paper_count"], "cached": True, "stale": saved_report["stale"],
                        "fetched_at": saved_report["source_fetched_at"], "discovery": saved_report["discovery"],
                        "warning": "验收固定候选：复用本次真实抓取的周报论文，不重复联网召回。"}
                def fixed_source(query, days):
                    assert days == 730
                    return snapshots[query]
                conference_discovery.search = fixed_source
                # Reset only the isolated audit feedback before a repeatable comparison.
                with repo.connect() as db:
                    db.execute("DELETE FROM recommendation_feedback")
                checks = []
                for track in TRACKS:
                    report = json.loads((output / (track["id"] + "-report.json")).read_text(encoding="utf-8"))
                    if not report["papers"]:
                        continue
                    target = report["papers"][-1]
                    pid = target["id"]
                    url = "/api/feedback/" + pid + "?mode=live"
                    params = {"query": report["subscription"]["query"], "days": 730, "sort": "ccfa", "limit": 50, "mode": "live"}
                    initial_search = request(client, "GET", "/api/papers", params=params)
                    initial_order = [p["id"] for p in initial_search["papers"]]
                    before = request(client, "GET", "/api/papers/" + pid)
                    from app.main import match_score
                    baseline = match_score(before)
                    request(client, "PUT", url, json={"value": "relevant", "reason": "隔离验收：排序调整验证，不写正式偏好"})
                    after = request(client, "GET", "/api/papers/" + pid)
                    assert match_score(after) == baseline + 5
                    assert after["semantic"] == before["semantic"]
                    boosted_search = request(client, "GET", "/api/papers", params=params)
                    boosted_order = [p["id"] for p in boosted_search["papers"]]
                    assert boosted_order.index(pid) <= initial_order.index(pid)
                    request(client, "PUT", url, json={"value": "none"})
                    restored = request(client, "GET", "/api/papers/" + pid)
                    assert match_score(restored) == baseline
                    undo_order = [p["id"] for p in request(client, "GET", "/api/papers", params=params)["papers"]]
                    assert undo_order == initial_order
                    # If this topic has one verified paper, hide it to exercise arXiv fallback after personal filtering.
                    hidden_id = report["papers"][0]["id"] if report["verified_count"] == 1 else pid
                    hidden_url = "/api/feedback/" + hidden_id + "?mode=live"
                    request(client, "PUT", hidden_url, json={"value": "not_relevant", "reason": "隔离验收：隐藏与撤回验证"})
                    body = {k:v for k,v in report["subscription"].items() if k in sub.SubscriptionInput.model_fields}
                    current_subscription = next(x for x in request(client, "GET", "/api/subscriptions")["subscriptions"] if x["id"] == report["subscription_id"])
                    body["limit"] = 5 if current_subscription["limit"] != 5 else 7
                    request(client, "PUT", "/api/subscriptions/" + report["subscription_id"], json=body)
                    newer = request(client, "POST", f"/api/subscriptions/{report['subscription_id']}/run")["job"]
                    # source cache remains real; run_digest uses the same query and adapter cache.
                    assert complete(newer["id"])["status"] == "succeeded"
                    hidden = request(client, "GET", "/api/weekly-reports/" + newer["id"])
                    assert hidden_id not in [p["id"] for p in hidden["papers"]]
                    request(client, "PUT", hidden_url, json={"value": "none"})
                    body["limit"] = 6
                    request(client, "PUT", "/api/subscriptions/" + report["subscription_id"], json=body)
                    restored_job = request(client, "POST", f"/api/subscriptions/{report['subscription_id']}/run")["job"]
                    assert complete(restored_job["id"])["status"] == "succeeded"
                    unhidden = request(client, "GET", "/api/weekly-reports/" + restored_job["id"])
                    assert hidden_id in [p["id"] for p in unhidden["papers"]]
                    original = request(client, "GET", "/api/weekly-reports/" + report["id"])
                    assert original["markdown"] == report["markdown"]
                    checks.append({"topic": track["name"], "paper": pid, "base_rank_score": baseline, "ranking_method": "semantic" if before["semantic"]["result"] else "keyword_fallback",
                        "with_feedback": baseline + 5, "undo": baseline, "semantic_unchanged": True,
                        "candidate_scope": len(initial_order), "replayed_real_source_snapshot": True,
                        "rank_before": initial_order.index(pid) + 1, "rank_after": boosted_order.index(pid) + 1, "undo_order_restored": True,
                        "hidden_paper": hidden_id, "arxiv_fallback_after_filter": hidden["verified_count"] == 0,
                        "hidden_in_new_report": True, "restored_in_next_report": True, "historical_report_unchanged": True})
                    write(output / "feedback-checks.json", checks)
                    print(json.dumps(checks[-1], ensure_ascii=False), flush=True)
        finally:
            stop.set()
            thread.join(timeout=6)
            with repo.connect() as db:
                db.execute("DELETE FROM worker_heartbeats WHERE id=?", (owner,))


if __name__ == "__main__":
    main()
