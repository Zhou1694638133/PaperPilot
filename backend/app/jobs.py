"""Durable single-user task queue and replayable SSE events.

SQLite transactions are the source of truth. No API-process background tasks
perform model calls; run ``python -m app.worker`` in a separate process.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time
from typing import Literal
import uuid

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings
from .repository import get_paper
from .exports import translation_filename, attachment_disposition

router = APIRouter(prefix="/api", tags=["tasks"])
TERMINAL = {"succeeded", "failed", "cancelled", "needs_confirmation", "partial"}
MAX_ATTEMPTS = 3  # Automatic lease recovery only; explicit user retries remain available.
LEASE_SECONDS = 30
PROMPT_VERSION = "2.0.1"


class Cancelled(Exception):
    pass


class LeaseLost(Exception):
    pass


class JobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: Literal["paper_reader", "paper_translator", "recommendation_explainer", "fulltext_fetch", "local_source_fetch", "semantic_ranker", "chinese_pdf", "glossary_extractor", "paper_analysis", "analysis_pdf"]
    paper_id: str = Field(min_length=1, max_length=100)
    mode: Literal["live", "demo"] = "live"
    idempotency_key: str | None = Field(default=None, max_length=100)
    language: Literal["zh-CN"] = "zh-CN"
    scope: Literal["abstract", "fulltext"] = "abstract"
    acknowledge_partial: bool = False
    glossary: dict[str, str] = Field(default_factory=dict, max_length=100)
    analysis_job_id: str | None = Field(default=None, max_length=100)
    translation_job_id: str | None = Field(default=None, max_length=100)


class RetryInput(BaseModel):
    confirm_duplicate_cost: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def connect() -> sqlite3.Connection:
    path = get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def init_jobs() -> None:
    with closing(connect()) as conn, conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            request_hash TEXT NOT NULL UNIQUE,
            idempotency_key TEXT UNIQUE,
            skill_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            input_json TEXT NOT NULL,
            paper_json TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            owner TEXT,
            lease_until REAL,
            model_inflight INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            checkpoint_json TEXT,
            result_json TEXT,
            thread_id TEXT NOT NULL,
            checkpoint_id TEXT
        );
        CREATE TABLE IF NOT EXISTS job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            created_at TEXT NOT NULL,
            data_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id, id);
        CREATE TABLE IF NOT EXISTS job_artifacts (
            job_id TEXT PRIMARY KEY REFERENCES jobs(id),
            media_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS translation_chunks (
            job_id TEXT NOT NULL REFERENCES jobs(id),
            paragraph_id TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            source TEXT NOT NULL,
            translation TEXT NOT NULL,
            provider_request_id TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY(job_id, paragraph_id)
        );
        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            id TEXT PRIMARY KEY,
            last_seen REAL NOT NULL,
            started_at TEXT NOT NULL
        );
        """)


def public_job(row: sqlite3.Row) -> dict:
    fields = ("id", "skill_id", "paper_id", "mode", "model", "prompt_version",
              "status", "stage", "progress", "created_at", "updated_at", "attempts",
              "error", "thread_id", "checkpoint_id")
    job = {key: row[key] for key in fields}
    job["can_retry"] = row["status"] in {"failed", "cancelled", "needs_confirmation", "partial"}
    job["cancel_requested"] = bool(row["cancel_requested"])
    job["requires_confirmation"] = bool(row["model_inflight"]) or row["status"] == "needs_confirmation"
    job["result"] = json.loads(row["result_json"]) if row["result_json"] else None
    checkpoint = json.loads(row['checkpoint_json']) if row['checkpoint_json'] else {}
    job['translation_state'] = checkpoint.get('translation_state')
    if job['translation_state']:
        state = job['translation_state']
        report = state.get('preflight', {})
        job['translation_state'] = {**state, 'preflight': {**report, 'items': [i for i in report.get('items', []) if i['kind'] in {'blocked', 'identifier'}]}}
    job["scope"] = json.loads(row["input_json"]).get("scope", "abstract")
    job["translation_job_id"] = json.loads(row["input_json"]).get("translation_job_id")
    snapshot = json.loads(row["paper_json"])
    job["paper_version"] = snapshot["version"]
    job["abstract_revision"] = snapshot.get("abstract_revision")
    return job


def _event(conn: sqlite3.Connection, job_id: str) -> dict:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    job = public_job(row)
    conn.execute("INSERT INTO job_events(job_id, created_at, data_json) VALUES(?,?,?)",
                 (job_id, utc_now(), encode(job)))
    return job


def job_record(job_id: str) -> dict:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "任务不存在")
    record = dict(row)
    record["input"] = json.loads(record["input_json"])
    record["paper"] = json.loads(record["paper_json"])
    record["checkpoint"] = json.loads(record["checkpoint_json"]) if record["checkpoint_json"] else None
    return record


def _owned(conn: sqlite3.Connection, job_id: str, owner: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or row["owner"] != owner or row["status"] != "running":
        raise LeaseLost("任务租约已由其他进程接管")
    if row["cancel_requested"]:
        raise Cancelled("用户取消了任务")
    return row


def update_progress(job_id: str, owner: str, stage: str, progress: int) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _owned(conn, job_id, owner)
        conn.execute("UPDATE jobs SET stage=?, progress=?, updated_at=? WHERE id=?",
                     (stage, max(0, min(99, progress)), utc_now(), job_id))
        _event(conn, job_id)


def ensure_running(job_id: str, owner: str) -> None:
    with closing(connect()) as conn:
        _owned(conn, job_id, owner)


def mark_model_started(job_id: str, owner: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _owned(conn, job_id, owner)
        conn.execute("UPDATE jobs SET model_inflight=1,updated_at=? WHERE id=?", (utc_now(), job_id))


def clear_model_started(job_id: str, owner: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("UPDATE jobs SET model_inflight=0 WHERE id=? AND owner=? AND status='running'", (job_id, owner))


def save_checkpoint(job_id: str, owner: str, result: dict) -> None:
    # Persist the response and clear the ambiguous-call flag atomically.
    # A cancellation requested during HTTP still saves the paid response first.
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT owner,status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["owner"] != owner or row["status"] != "running":
            raise LeaseLost()
        conn.execute("UPDATE jobs SET checkpoint_json=?,model_inflight=0,updated_at=? WHERE id=?",
                     (encode(result), utc_now(), job_id))


def load_chunk(job_id: str, paragraph_id: str, source_hash: str) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM translation_chunks WHERE job_id=? AND paragraph_id=? AND source_hash=?",
                           (job_id, paragraph_id, source_hash)).fetchone()
    return dict(row) if row else None


def save_chunk(job_id: str, owner: str, paragraph_id: str, source_hash: str,
               source: str, translation: str, provider_request_id: str | None) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT owner,status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["owner"] != owner or row["status"] != "running":
            raise LeaseLost()
        conn.execute("""INSERT OR REPLACE INTO translation_chunks
            (job_id,paragraph_id,source_hash,source,translation,provider_request_id,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (job_id, paragraph_id, source_hash, source, translation, provider_request_id, utc_now()))
        conn.execute("UPDATE jobs SET model_inflight=0,updated_at=? WHERE id=?", (utc_now(), job_id))


def recover_expired(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT * FROM jobs WHERE status='running' AND lease_until<?", (time.time(),)).fetchall()
    for row in rows:
        if row["model_inflight"]:
            status, stage, error = "needs_confirmation", "模型结果待确认", "模型请求已发出但结果未保存；请先核对提供方记录。再次运行可能重复计费。"
        elif row["cancel_requested"]:
            status, stage, error = "cancelled", "已取消", None
        elif row["attempts"] >= MAX_ATTEMPTS:
            status, stage, error = "failed", "恢复次数已达上限", "任务连续中断，已达到 3 次执行上限。"
        else:
            status, stage, error = "queued", "从持久检查点恢复", None
        conn.execute("UPDATE jobs SET status=?,stage=?,error=?,owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                     (status, stage, error, utc_now(), row["id"]))
        _event(conn, row["id"])


def claim_job(owner: str) -> dict | None:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        recover_expired(conn)
        # A global queue slot bounds concurrency even if two workers are started.
        if conn.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
            return None
        row = conn.execute("SELECT id FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            return None
        job_id = row["id"]
        conn.execute("""UPDATE jobs SET status='running',stage='读取持久检查点',owner=?,
            lease_until=?,attempts=attempts+1,error=NULL,updated_at=? WHERE id=?""",
            (owner, time.time() + LEASE_SECONDS, utc_now(), job_id))
        _event(conn, job_id)
    return job_record(job_id)


def heartbeat(owner: str) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("INSERT INTO worker_heartbeats(id,last_seen,started_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen",
                     (owner, time.time(), utc_now()))
        conn.execute("UPDATE jobs SET lease_until=? WHERE owner=? AND status='running'", (time.time() + LEASE_SECONDS, owner))


def worker_status() -> dict:
    init_jobs()
    with closing(connect()) as conn:
        row = conn.execute("SELECT MAX(last_seen) AS last_seen FROM worker_heartbeats").fetchone()
    last_seen = row["last_seen"]
    return {"online": bool(last_seen and time.time() - last_seen < LEASE_SECONDS),
            "last_seen": last_seen, "concurrency": 1}


def finish_job(job_id: str, owner: str, result: dict, checkpoint_id: str | None = None) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _owned(conn, job_id, owner)
        if row["skill_id"] == "glossary_extractor":
            from .terminology import merge_result
            result = merge_result(conn, row, result)
        partial = row['skill_id'] == 'paper_translator' and result.get('partial', False)
        if not partial:
            conn.execute("INSERT OR REPLACE INTO job_artifacts(job_id,media_type,content,created_at) VALUES(?,?,?,?)",
                         (job_id, "text/markdown", result["markdown"], utc_now()))
        failures = len(result.get('translation_state', {}).get('failures', []))
        progress = int(100 * len(result.get('alignment', [])) / max(1, result.get('qa', {}).get('paragraph_count', 1))) if partial else 100
        conn.execute("""UPDATE jobs SET status=?,stage=?,progress=?,error=?,
            result_json=?,checkpoint_id=?,model_inflight=0,owner=NULL,lease_until=NULL,updated_at=? WHERE id=?""",
            ('partial' if partial else 'succeeded', f'部分完成：{failures} 块待处理' if partial else '产物已保存', progress,
             f'{failures} 个块未完成，成功检查点已保存；可重试未完成部分。' if partial else None,
             encode(result), checkpoint_id, utc_now(), job_id))
        _event(conn, job_id)


def fail_job(job_id: str, owner: str, message: str, *, ambiguous: bool = False, cancelled: bool = False) -> None:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND status='running'", (job_id, owner)).fetchone()
        if row is None:
            return
        uncertain = ambiguous or bool(row["model_inflight"])
        status = "needs_confirmation" if uncertain else ("cancelled" if cancelled or row["cancel_requested"] else "failed")
        stage = {"needs_confirmation": "模型结果待确认", "cancelled": "已取消", "failed": "执行失败"}[status]
        conn.execute("UPDATE jobs SET status=?,stage=?,error=?,owner=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                     (status, stage, message[:800], utc_now(), job_id))
        _event(conn, job_id)


@router.get("/skills")
def list_skills() -> dict:
    from .skills import registry
    return {"skills": registry()}


@router.post("/jobs", status_code=201)
def create_job(body: JobInput) -> dict:
    from .skills import DEFINITIONS
    init_jobs()
    settings = get_settings()
    needs_model = DEFINITIONS[body.skill_id]["requires_model"]
    if needs_model and body.mode == "demo":
        raise HTTPException(409, "演示数据不执行付费模型任务；请切换真实模式并选择已检索的论文。")
    if body.skill_id in {"fulltext_fetch", "local_source_fetch"} and body.mode == "demo":
        raise HTTPException(409, "演示模式不下载真实全文，请切换真实模式。")
    if body.skill_id == "recommendation_explainer" and body.scope != "abstract":
        raise HTTPException(422, "推荐解释沿用标题与摘要排序特征，不支持全文范围。")
    if needs_model and not (settings.llm_api_key and settings.llm_model):
        raise HTTPException(503, "模型未配置：请在后端 .env 设置 LLM_API_KEY 与 LLM_MODEL。真实检索与收藏仍可使用。")
    if any(len(key) > 120 or len(value) > 240 or not key.strip() or not value.strip() for key, value in body.glossary.items()):
        raise HTTPException(422, "术语表条目必须非空，原文最多 120 字符、译文最多 240 字符。")
    paper = get_paper(body.paper_id, mode=body.mode)
    if paper is None:
        raise HTTPException(404, "请先检索并选择论文")
    if body.skill_id == "local_source_fetch" and paper.get("source") != "local":
        raise HTTPException(422, "源文件恢复仅用于本地 PDF")
    if paper.get("source") == "local" and body.skill_id not in {"fulltext_fetch", "local_source_fetch"}:
        if body.skill_id not in {"paper_reader", "paper_translator", "paper_analysis", "analysis_pdf", "chinese_pdf"}:
            raise HTTPException(422, "本地论文支持阅读与翻译，不执行推荐评分或推荐解释。")
        if body.scope == "abstract" and not (paper.get("abstract") and paper.get("abstract_revision")):
            raise HTTPException(422, "请先在本地论文页面核对并保存原文摘要。")
    if len(paper.get("abstract", "")) > 40000:
        raise HTTPException(422, "摘要超过阶段一处理上限（40,000 字符）")
    payload = body.model_dump(exclude={"idempotency_key"})
    if body.analysis_job_id is None:
        payload.pop("analysis_job_id")
    elif body.skill_id != "analysis_pdf":
        raise HTTPException(422, "analysis_job_id 仅用于总体分析排版")
    if body.skill_id in {"paper_analysis", "analysis_pdf"}:
        from .paper_analysis import VERSION as ANALYSIS_VERSION, snapshot
        if body.scope != "fulltext":
            raise HTTPException(422, "总体分析需要已解析全文")
        payload["_analysis_version"] = ANALYSIS_VERSION
        if body.skill_id == "paper_analysis":
            from .repository import get_profile
            payload["_analysis_interests"] = sorted(get_profile()["interests"])
        else:
            from .chinese_pdf import ENGINE
            if not ENGINE.is_file():
                raise HTTPException(503, "Tectonic 尚未安装")
            if not body.analysis_job_id:
                raise HTTPException(422, "请选择已完成的总体分析任务")
            payload["_analysis"] = snapshot(paper, body.analysis_job_id)
    if body.skill_id == "glossary_extractor":
        from .terminology import eligible, VERSION
        from .fulltext import init_documents
        if not eligible(paper):
            raise HTTPException(422, "请先收藏、标为已读或添加标签/笔记；不感兴趣的论文不提取术语。")
        if body.scope != "abstract" or body.glossary or body.acknowledge_partial:
            raise HTTPException(422, "术语提取使用标题与摘要，不接受全文范围或自定义术语。")
        init_documents()
        payload["_terminology_prompt_version"] = VERSION
    # Keep existing job identities stable when the new optional field is unused.
    if body.translation_job_id is None:
        payload.pop("translation_job_id")
    elif body.skill_id != "chinese_pdf":
        raise HTTPException(422, "translation_job_id 仅用于中文排版")
    if body.skill_id == "chinese_pdf":
        from .chinese_pdf import translation_snapshot, ENGINE
        if not ENGINE.is_file():
            raise HTTPException(503, "Tectonic 未安装，请运行 scripts/install-tectonic.ps1")
        if not body.translation_job_id or body.scope != "fulltext":
            raise HTTPException(422, "中文排版需要完整译文任务编号")
        payload["_chinese"] = translation_snapshot(paper, body.translation_job_id)
    if body.skill_id == "semantic_ranker":
        from .semantic import context, VERSION
        from .repository import get_profile
        interests = sorted(get_profile()["interests"])
        if body.scope != "abstract" or not interests or not paper.get("abstract", "").strip():
            raise HTTPException(422, "语义评分需要非空研究兴趣与摘要，仅支持摘要范围。")
        payload["_research_interests"] = interests
        payload["_semantic_context"] = context(paper, interests)
        payload["_semantic_prompt_version"] = VERSION
    from .fulltext import get_document, glossary_get, PARSER_VERSION
    if body.skill_id in {"fulltext_fetch", "local_source_fetch"}:
        payload["scope"] = "fulltext"
        payload["parser_version"] = PARSER_VERSION
    elif body.scope == "fulltext":
        document = get_document(paper)
        if not document:
            raise HTTPException(409, "请先完成全文读取任务。")
        if document["status"] != "ready" and not body.acknowledge_partial and body.skill_id not in {"chinese_pdf", "analysis_pdf"}:
            raise HTTPException(409, "解析存在警告。核对后需明确接受仅处理已解析内容，不能猜补缺失内容。")
        payload["_document"] = document
    if body.skill_id == "paper_translator":
        glossary = glossary_get()
        payload["glossary"] = {**glossary["terms"], **body.glossary}
        payload["glossary_version"] = glossary["version"]
    payload["_provider_url"] = settings.llm_base_url if needs_model else "local"
    identity = {"input": payload, "version": paper["version"], "title": paper["title"], "abstract": "" if paper.get("source") == "local" and payload["scope"] == "fulltext" else paper["abstract"],
                "research_interests": (paper.get("recommendation") or {}).get("interests", []) if body.skill_id == "paper_reader" else None,
                "recommendation": paper.get("recommendation") if not needs_model else None,
                "model": settings.llm_model if needs_model else "rules", "provider": settings.llm_base_url if needs_model else "local", "prompt": PROMPT_VERSION}
    # Volatile run identifiers do not make duplicate clicks chargeable again.
    if identity["recommendation"]:
        identity["recommendation"] = {k: v for k, v in identity["recommendation"].items() if k not in {"run_id", "created_at"}}
    digest = hashlib.sha256(encode(identity).encode()).hexdigest()
    job_id = str(uuid.uuid4())
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if body.idempotency_key:
            old = conn.execute("SELECT * FROM jobs WHERE idempotency_key=?", (body.idempotency_key,)).fetchone()
            if old and old["request_hash"] != digest:
                raise HTTPException(409, "该幂等键已用于不同输入")
            if old:
                return public_job(old)
        old = conn.execute("SELECT * FROM jobs WHERE request_hash=?", (digest,)).fetchone()
        if old:
            return public_job(old)
        now = utc_now()
        conn.execute("""INSERT INTO jobs(id,request_hash,idempotency_key,skill_id,paper_id,mode,input_json,
            paper_json,model,prompt_version,status,stage,created_at,updated_at,thread_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, digest, body.idempotency_key, body.skill_id, body.paper_id, body.mode, encode(payload),
             encode(paper), settings.llm_model if needs_model else "rules", PROMPT_VERSION,
             "queued", "等待独立 worker", now, now, job_id))
        return _event(conn, job_id)


@router.get("/jobs")
def list_jobs() -> dict:
    init_jobs()
    with closing(connect()) as conn:
        # Sort only IDs in SQLite, not multi-megabyte frozen documents/results.
        rows = conn.execute("SELECT * FROM jobs WHERE id IN (SELECT id FROM jobs ORDER BY created_at DESC LIMIT 100)").fetchall()
    rows.sort(key=lambda row: row["created_at"], reverse=True)
    return {"jobs": [public_job(row) for row in rows], "worker": worker_status()}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "任务不存在")
    return public_job(row)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "任务不存在")
        if row["status"] in {"succeeded", "cancelled"}:
            return public_job(row)
        if row["status"] == "running":
            conn.execute("UPDATE jobs SET cancel_requested=1,stage='取消已请求，等待当前步骤结束',updated_at=? WHERE id=?", (utc_now(), job_id))
        else:
            conn.execute("UPDATE jobs SET cancel_requested=1,status='cancelled',stage='已取消',updated_at=? WHERE id=?", (utc_now(), job_id))
        return _event(conn, job_id)


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, body: RetryInput | None = None) -> dict:
    with closing(connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "任务不存在")
        if row["status"] not in {"failed", "cancelled", "needs_confirmation", "partial"}:
            raise HTTPException(409, "只有失败、部分完成、取消或结果待确认的任务可以恢复")
        if (row["status"] == "needs_confirmation" or row["model_inflight"]) and not (body and body.confirm_duplicate_cost):
            raise HTTPException(409, "需要明确确认可能重复计费：confirm_duplicate_cost=true")
        if row["skill_id"] == "weekly_digest":
            # Explicit retry is a manual request, even if the original schedule is now paused.
            payload = json.loads(row["input_json"])
            payload["trigger"] = "manual"
            conn.execute("UPDATE jobs SET input_json=? WHERE id=?", (encode(payload), job_id))
        conn.execute("""UPDATE jobs SET status='queued',stage='等待从检查点恢复',cancel_requested=0,
            model_inflight=0,owner=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?""", (utc_now(), job_id))
        return _event(conn, job_id)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, after: int = Query(default=0, ge=0),
                     last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    get_job(job_id)
    try:
        last_id = int(last_event_id or "0")
        if last_id < 0:
            raise ValueError()
        cursor = max(after, last_id)
    except ValueError:
        raise HTTPException(422, "Last-Event-ID 必须为非负整数") from None
    if cursor < 0:
        raise HTTPException(422, "Last-Event-ID 必须为非负整数")

    async def stream():
        nonlocal cursor
        last_ping = time.monotonic()
        while not await request.is_disconnected():
            with closing(connect()) as conn:
                rows = conn.execute("SELECT id,data_json FROM job_events WHERE job_id=? AND id>? ORDER BY id LIMIT 200", (job_id, cursor)).fetchall()
            for row in rows:
                cursor = row["id"]
                yield f"id: {cursor}\ndata: {row['data_json']}\n\n"
            snapshot = get_job(job_id)
            if snapshot["status"] in TERMINAL and len(rows) < 200:
                if not rows or json.loads(rows[-1]["data_json"]) != snapshot:
                    yield f"data: {encode(snapshot)}\n\n"
                return
            if time.monotonic() - last_ping > 15:
                yield ": keep-alive\n\n"
                last_ping = time.monotonic()
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/jobs/{job_id}/artifact")
def artifact(job_id: str) -> Response:
    job = get_job(job_id)
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM job_artifacts WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "该任务尚无完整产物；已完成翻译块保存在检查点中")
    filename = f"paperpilot-{job_id}.md"
    if job["skill_id"] in {"paper_translator", "chinese_pdf"}:
        filename = translation_filename(job_record(job_id)["paper"]["title"], "md")
    if job["skill_id"] in {"paper_analysis", "analysis_pdf"}:
        from .paper_analysis import filename
        filename = filename(job_record(job_id)["paper"]["title"], "md")
    return Response(row["content"], media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": attachment_disposition(filename)})




