"""Opt-in subscriptions and evidence-backed weekly digests, using the durable job queue."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import jobs, repository as repo
from .config import get_settings
from .ranking import expand_query, matches_filters, rank_paper
from .schemas import Profile
from .sources import ArxivSource, SourceError
from . import conference_discovery
from .research import publication, TRACKS

router = APIRouter(prefix="/api", tags=["subscriptions"])
LOCAL = timezone(timedelta(hours=8))
VERSION = "1.1.0"
FEEDBACK_WEIGHTS = {"relevant": 5, "not_relevant": 0, "too_basic": -5, "lacks_experiments": -3, "none": 0}


def init_stage3():
    # Additive tables only. Existing papers, collections and checkpoints are untouched.
    with repo.connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0, next_run TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id TEXT PRIMARY KEY REFERENCES jobs(id), subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
            week TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT, read_at TEXT,
            UNIQUE(subscription_id,week,revision)
        );
        CREATE TABLE IF NOT EXISTS recommendation_feedback (
            mode TEXT NOT NULL, paper_id TEXT NOT NULL, value TEXT NOT NULL,
            reason TEXT NOT NULL, context_json TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(mode,paper_id)
        );
        """)


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=160)
    query: str = Field(default="", max_length=4000)
    source: Literal["ccfa_first", "arxiv"] = "ccfa_first"
    days: int = Field(default=730, ge=1, le=730)
    limit: int = Field(default=10, ge=1, le=30)
    weekday: int = Field(default=0, ge=0, le=6)
    hour: int = Field(default=9, ge=0, le=23)
    model_enabled: bool = False
    model_limit: int = Field(default=5, ge=1, le=10)
    enabled: bool = False
    include_keywords: list[str] = Field(default_factory=list, max_length=30)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("name", "topic")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("名称和研究主题不能为空")
        return value

    @model_validator(mode="after")
    def validate_query(self):
        default_query = next((track["query"] for track in TRACKS if track["name"] == self.topic), self.topic)
        self.query = expand_query(self.query.strip() or default_query)["query"]
        if not self.query.strip():
            raise ValueError("该主题没有英文词典映射，请填写可编辑的英文检索式")
        profile = Profile(include_keywords=self.include_keywords, exclude_keywords=self.exclude_keywords)
        self.include_keywords = profile.include_keywords
        self.exclude_keywords = profile.exclude_keywords
        return self


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["relevant", "not_relevant", "too_basic", "lacks_experiments", "none"]
    reason: str = Field(default="", max_length=1000)


class EnableInput(BaseModel):
    enabled: bool


def next_run(config, now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(LOCAL)
    candidate = local.replace(hour=config["hour"], minute=0, second=0, microsecond=0)
    candidate += timedelta(days=(config["weekday"] - local.weekday()) % 7)
    if candidate <= local:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc).isoformat()


def week_key(now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(LOCAL)
    return (local.date() - timedelta(days=local.weekday())).isoformat()


def public_subscription(row):
    return {**json.loads(row["payload"]), "id": row["id"], "revision": row["revision"],
            "enabled": bool(row["enabled"]), "next_run": row["next_run"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "timezone": "UTC+08:00"}


@router.get("/subscriptions")
def list_subscriptions():
    with repo.connect() as db:
        rows = db.execute("SELECT * FROM subscriptions ORDER BY created_at DESC").fetchall()
    return {"subscriptions": [public_subscription(row) for row in rows], "worker": jobs.worker_status()}


@router.post("/subscriptions", status_code=201)
def create_subscription(body: SubscriptionInput, idempotency_key: str | None = Header(default=None, min_length=1, max_length=100)):
    # Stable request identity without changing existing tables or subscription IDs.
    now = jobs.utc_now()
    sid = str(uuid5(NAMESPACE_URL, "paperpilot:subscription:" + idempotency_key)) if idempotency_key else str(uuid4())
    payload = body.model_dump()
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
        if existing:
            if json.loads(existing["payload"]) != payload:
                raise HTTPException(409, "同一创建请求的内容已改变，请刷新订阅列表后编辑已有订阅")
            return public_subscription(existing)
        if db.execute("SELECT count(*) FROM subscriptions").fetchone()[0] >= 50:
            raise HTTPException(409, "最多保存 50 个订阅；可编辑或暂停已有订阅")
        db.execute("INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?)",
                   (sid, 1, jobs.encode(payload), int(body.enabled),
                    next_run(payload) if body.enabled else None, now, now))
        return public_subscription(db.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone())


@router.put("/subscriptions/{sid}")
def update_subscription(sid: str, body: SubscriptionInput):
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "订阅不存在")
        payload = body.model_dump()
        if json.loads(row["payload"]) == payload:
            return public_subscription(row)
        # Toggling the schedule does not create another report for the same week.
        old = json.loads(row["payload"])
        content_changed = any(old.get(key) != payload[key] for key in payload if key not in {"enabled", "hour", "weekday", "name"})
        schedule_changed = any(old.get(key) != payload[key] for key in ("enabled", "hour", "weekday"))
        due = (next_run(payload) if schedule_changed else row["next_run"]) if body.enabled else None
        db.execute("UPDATE subscriptions SET revision=?,payload=?,enabled=?,next_run=?,updated_at=? WHERE id=?",
                   (row["revision"] + int(content_changed), jobs.encode(payload), int(body.enabled),
                    due, jobs.utc_now(), sid))
        return public_subscription(db.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone())


@router.put("/subscriptions/{sid}/enabled")
def enable_subscription(sid: str, body: EnableInput):
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "订阅不存在")
        if bool(row["enabled"]) == body.enabled:
            return public_subscription(row)
        payload = {**json.loads(row["payload"]), "enabled": body.enabled}
        db.execute("UPDATE subscriptions SET payload=?,enabled=?,next_run=?,updated_at=? WHERE id=?",
                   (jobs.encode(payload), int(body.enabled), next_run(payload) if body.enabled else None, jobs.utc_now(), sid))
        return public_subscription(db.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone())


def enqueue(db, row, trigger, now):
    week = week_key(now)
    old = db.execute("SELECT id FROM weekly_reports WHERE subscription_id=? AND week=? AND revision=?",
                     (row["id"], week, row["revision"])).fetchone()
    if old:
        return old["id"]
    jid, stamp = str(uuid4()), now.isoformat()
    config = public_subscription(row)
    settings = get_settings()
    model_ready = bool(config.get("model_enabled") and settings.llm_api_key and settings.llm_model)
    payload = {"scope": "fulltext", "_subscription": config, "week": week, "trigger": trigger,
               "_provider_url": settings.llm_base_url if model_ready else "local"}
    paper = {"id": "subscription:" + row["id"], "version": row["revision"], "title": config["name"],
             "authors": [], "abstract": config["topic"], "url": "https://arxiv.org/", "mode": "live"}
    digest = hashlib.sha256(jobs.encode([row["id"], week, row["revision"]]).encode()).hexdigest()
    db.execute("""INSERT INTO jobs(id,request_hash,skill_id,paper_id,mode,input_json,paper_json,model,
                  prompt_version,status,stage,created_at,updated_at,thread_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (jid, "weekly:" + digest, "weekly_digest", paper["id"], "live", jobs.encode(payload),
                jobs.encode(paper), settings.llm_model if model_ready else "rules", VERSION, "queued", "等待独立 worker 生成周报", stamp, stamp, jid))
    db.execute("INSERT INTO weekly_reports(id,subscription_id,week,revision) VALUES(?,?,?,?)",
               (jid, row["id"], week, row["revision"]))
    jobs._event(db, jid)
    return jid


@router.post("/subscriptions/{sid}/run")
def run_now(sid: str):
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "订阅不存在")
        jid = enqueue(db, row, "manual", datetime.now(timezone.utc))
    return {"job": jobs.get_job(jid), "message": "同一订阅配置在本周只生成一次；失败或取消后请在任务中心显式重试。"}


def enqueue_due(now=None):
    now = now or datetime.now(timezone.utc)
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT * FROM subscriptions WHERE enabled=1 AND next_run<=?", (now.isoformat(),)).fetchall()
        ids = []
        for row in rows:
            ids.append(enqueue(db, row, "scheduled", now))
            db.execute("UPDATE subscriptions SET next_run=? WHERE id=?", (next_run(json.loads(row["payload"]), now), row["id"]))
    return ids


def feedback_for(paper_id, mode="live"):
    with repo.connect() as db:
        row = db.execute("SELECT value,reason,updated_at FROM recommendation_feedback WHERE mode=? AND paper_id=?",
                         (mode, paper_id)).fetchone()
    result = dict(row) if row else {"value": "none", "reason": "", "updated_at": None}
    return {**result, "adjustment": FEEDBACK_WEIGHTS[result["value"]], "note": "个人反馈仅影响排序/隐藏，不代表论文质量或已核实事实。"}


@router.put("/feedback/{paper_id:path}")
def save_feedback(paper_id: str, body: FeedbackInput, mode: Literal["live", "demo"] = "live"):
    paper = repo.get_paper(paper_id, mode)
    if not paper or paper.get("source") == "local":
        raise HTTPException(404, "请先检索在线论文；本地论文不参与推荐反馈")
    context = {"title": paper["title"], "version": paper["version"], "recommendation": paper.get("recommendation"),
               "scope": "abstract", "subjective": True}
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT value FROM recommendation_feedback WHERE mode=? AND paper_id=?",
                              (mode, paper_id)).fetchone()
        # An explicit positive/ranking judgment supersedes the legacy hide flag.
        # Undo only restores visibility when there was an actual hide feedback.
        restore = body.value not in {"none", "not_relevant"} or (
            body.value == "none" and previous is not None and previous["value"] == "not_relevant")
        if restore:
            collection = db.execute("SELECT payload FROM collections WHERE mode=? AND paper_id=?",
                                    (mode, paper_id)).fetchone()
            if collection:
                value = json.loads(collection["payload"])
                value["interested"] = True
                db.execute("UPDATE collections SET payload=?,updated_at=? WHERE mode=? AND paper_id=?",
                           (jobs.encode(value), jobs.utc_now(), mode, paper_id))
        db.execute("""INSERT INTO recommendation_feedback VALUES(?,?,?,?,?,?)
                    ON CONFLICT(mode,paper_id) DO UPDATE SET value=excluded.value,reason=excluded.reason,
                    context_json=excluded.context_json,updated_at=excluded.updated_at""",
                   (mode, paper_id, body.value, body.reason.strip(), jobs.encode(context), jobs.utc_now()))
    return feedback_for(paper_id, mode)


@router.get("/feedback")
def list_feedback(mode: Literal["live", "demo"] = "live", limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with repo.connect() as db:
        rows = db.execute("SELECT * FROM recommendation_feedback WHERE mode=? AND value!='none' ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                          (mode, limit, offset)).fetchall()
    return {"feedback": [{**dict(row), "context": json.loads(row["context_json"])} for row in rows], "offset": offset}


@router.get("/feedback/evaluation")
def feedback_evaluation(mode: Literal["live", "demo"] = "live"):
    from .feedback_evaluation import evaluate_feedback
    return evaluate_feedback(mode)


@router.get("/feedback/evaluation.json")
def feedback_evaluation_download(mode: Literal["live", "demo"] = "live"):
    from fastapi.responses import Response
    from .feedback_evaluation import evaluate_feedback
    return Response(jobs.encode(evaluate_feedback(mode)), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="paperpilot-feedback-evaluation.json"'})


@router.get("/weekly-reports")
def list_reports(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0)):
    with repo.connect() as db:
        rows = db.execute("""SELECT r.id,r.subscription_id,r.week,r.revision,r.read_at,j.status,j.stage,j.error,j.progress,
                          json_extract(j.paper_json,'$.title') AS name,
                          json_extract(r.payload,'$.paper_count') AS paper_count,
                          json_extract(r.payload,'$.new_count') AS new_count
                          FROM weekly_reports r JOIN jobs j ON j.id=r.id
                          ORDER BY j.created_at DESC LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
    return {"reports": [dict(row) for row in rows], "offset": offset}


@router.get("/weekly-reports/{rid}")
def get_report(rid: str):
    with repo.connect() as db:
        row = db.execute("SELECT r.*,j.status FROM weekly_reports r JOIN jobs j ON j.id=r.id WHERE r.id=?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "周报不存在")
    if row["status"] != "succeeded" or not row["payload"]:
        raise HTTPException(409, "周报尚未完成；请查看任务中心的进度或错误")
    result = json.loads(row["payload"])
    for paper in result["papers"]:
        paper["feedback"] = feedback_for(paper["id"])
    return {**result, "read_at": row["read_at"]}


@router.put("/weekly-reports/{rid}/read")
def read_report(rid: str):
    get_report(rid)
    with repo.connect() as db:
        db.execute("UPDATE weekly_reports SET read_at=COALESCE(read_at,?) WHERE id=?", (jobs.utc_now(), rid))
    return {"read": True}


def run_digest(record, owner):
    config = record["input"]["_subscription"]
    with repo.connect() as db:
        row = db.execute("SELECT * FROM subscriptions WHERE id=?", (config["id"],)).fetchone()
        existing = db.execute("SELECT payload FROM weekly_reports WHERE id=?", (record["id"],)).fetchone()
    if not row:
        raise ValueError("订阅已不存在")
    if record["input"]["trigger"] == "scheduled" and not row["enabled"]:
        raise jobs.Cancelled()
    if existing and existing["payload"]:
        return json.loads(existing["payload"])
    jobs.update_progress(record["id"], owner, "检索主题论文与正式会议来源", 20)
    source = (record.get("checkpoint") or {}).get("weekly_source")
    if source is None:
        try:
            source = (conference_discovery.search(config["query"], config["days"]) if config["source"] == "ccfa_first"
                      else ArxivSource().search(config["query"], 0, 50, config["days"], "newest"))
        except SourceError as exc:
            raise ValueError("周报检索失败：" + str(exc)) from None
        jobs.ensure_running(record["id"], owner)
        jobs.save_checkpoint(record["id"], owner, {"weekly_source": source})
    jobs.update_progress(record["id"], owner, "按首次公开日期、研究兴趣与反馈筛选", 55)
    profile = Profile(interests=[config["topic"]], include_keywords=config["include_keywords"],
                      exclude_keywords=config["exclude_keywords"], days=config["days"]).model_dump()
    run_id = record["id"]
    with repo.connect() as db:
        db.execute("INSERT OR IGNORE INTO search_runs VALUES(?,?,?,?,?)",
                   (run_id, source["query"], "live", jobs.encode({"subscription": config, "week": record["input"]["week"],
                    "discovery": source.get("discovery")}), jobs.utc_now()))
    unique = {}
    for paper in source["papers"]:
        if paper["id"] not in unique or paper["version"] > unique[paper["id"]]["version"]:
            unique[paper["id"]] = paper
    repo.upsert_papers(unique.values())
    papers, recommendations = [], []
    now = datetime.now(timezone.utc)
    for candidate in unique.values():
        jobs.ensure_running(record["id"], owner)
        paper = repo.get_paper(candidate["id"], "live")
        if not matches_filters(paper, profile, config["days"], now):
            continue
        # Future public dates are not eligible, even if a source feed is malformed.
        if datetime.fromisoformat(paper["published"].replace("Z", "+00:00")) > now:
            continue
        feedback = feedback_for(paper["id"])
        if not paper["collection"]["interested"] or feedback["value"] == "not_relevant":
            continue
        recommendation = rank_paper(paper, profile, run_id)
        recommendations.append((paper, recommendation))
        papers.append({**{key: value for key, value in paper.items() if key != "raw_entry"},
                       "recommendation": recommendation, "publication": publication(paper), "feedback": feedback})
    repo.save_recommendations(run_id, recommendations)
    papers.sort(key=lambda p: (config["source"] == "ccfa_first" and p["publication"]["status"] == "verified",
                               p["recommendation"]["score"] + p["feedback"]["adjustment"], p["published"]), reverse=True)
    from .weekly_semantic import enrich
    model_stats = enrich(record, owner, papers, config, source)
    papers = papers[:config["limit"]]
    with repo.connect() as db:
        seen = {row[0] for row in db.execute("""SELECT json_extract(p.value,'$.id') FROM weekly_reports r
                JOIN jobs j ON j.id=r.id JOIN json_each(r.payload,'$.papers') p
                WHERE r.subscription_id=? AND j.status='succeeded' AND r.id!=?""", (config["id"], record["id"]))}
    for paper in papers:
        paper["new_to_subscription"] = paper["id"] not in seen
    verified = sum(p["publication"]["status"] == "verified" for p in papers)
    model_ready = bool(get_settings().llm_api_key and get_settings().llm_model)
    warnings = list(filter(None, [source.get("warning")]))
    if config["source"] == "ccfa_first" and not verified:
        warnings.append("本期未找到符合条件且已核实的 CCF A 论文，已回退相关 arXiv 论文；并不表示所有会议均无相关论文。")
    note = model_stats["model_note"]
    note += f" 本期模型请求尝试 {model_stats['model_calls']} 次，缓存复用 {model_stats['model_cache_hits']} 篇，成功评估 {model_stats['model_successes']} 篇。"
    if not model_ready:
        note += "模型未配置，自动降级关键词评分。"
    result = {"id": record["id"], "subscription_id": config["id"], "name": config["name"], "week": record["input"]["week"],
              "coverage": "abstract", "mode": "live", "skill_version": VERSION, "source": "https://arxiv.org/",
              "generated_at": jobs.utc_now(), "window_start": (now - timedelta(days=config["days"])).isoformat(),
              "window_end": now.isoformat(), "query": source["query"], "subscription": config,
              "papers": papers, "paper_count": len(papers), "new_count": sum(p["new_to_subscription"] for p in papers),
              "verified_count": verified, "warnings": warnings, "discovery": source.get("discovery", {}),
              "source_fetched_at": source.get("fetched_at"), "stale": source.get("stale", False), "note": note,
              "model_configured": model_ready, **model_stats,
              "deduplication": "同一 arXiv ID 保留最高版本；首次公开日期过滤；新收录指此前成功周报未出现，不代表本周新发表。",
              "recall_limit": "有界候选池，非全量文献综述。每期重检近两年候选，不仅检索本周新论文。"}
    lines = [f"# {config['name']} · {result['week']} 周报", note,
             f"首次公开日期窗口：{result['window_start']} 至 {result['window_end']}",
             f"实际检索式：{result['query']}", result["deduplication"], result["recall_limit"], *warnings]
    if not papers:
        lines.append("本期没有符合条件的论文；检索结果为空，不用演示论文补齐。")
    for paper in papers:
        lines.append(f"## {paper['title']}")
        semantic_result = paper.get("weekly_semantic", {}).get("result")
        if semantic_result:
            lines.extend([f"主题语义分：{semantic_result['score']}；关键词对照：{paper['recommendation']['score']}",
                          *[item["reason"] for item in semantic_result["interests"]],
                          *[f"> {e['text']}\n\n{e['location']}" for item in semantic_result["interests"] for e in item["evidence"]],
                          "未核实：" + "；".join(semantic_result["unknowns"])])
        else:
            lines.append(paper.get("weekly_semantic", {}).get("message", "关键词降级"))
        rec, pub = paper["recommendation"], paper["publication"]
        lines.extend([f"来源：{paper['url']} · 首次公开 {paper['published']} · 更新 {paper['updated']}",
                      f"关键词分 {rec['score']}；个人反馈调整 {paper['feedback']['adjustment']}；仅用于排序。",
                      rec["reason"], f"会议状态：{pub['status']}；依据：{pub.get('evidence_url') or '未核实'}",
                      *[f"> {e['text']}\n\n位置：{e['location']}" for e in rec["evidence"]],
                      "尚待核实：" + "；".join(rec["unknowns"])])
    result["markdown"] = "\n\n".join(lines)
    jobs.update_progress(record["id"], owner, "保存站内周报与来源证据", 85)
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        jobs._owned(db, record["id"], owner)
        db.execute("UPDATE weekly_reports SET payload=? WHERE id=?", (jobs.encode(result), record["id"]))
    jobs.save_checkpoint(record["id"], owner, result)
    return result

