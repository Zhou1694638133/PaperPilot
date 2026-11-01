from contextlib import asynccontextmanager
import json
import re
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse

from .config import get_settings
from .demo import demo_papers
from .exports import export_bibtex, export_markdown
from . import repository as repo
from .ranking import expand_query, keyword_present, matches_filters, rank_paper
from .schemas import Profile, CollectionPatch, QueryInput
from .sources import ArxivSource, SourceError
from .jobs import router as jobs_router, init_jobs, worker_status
from .reading_api import router as reading_router
from .local_papers import router as local_papers_router
from .fulltext import init_documents
from .research import publication
from . import conference_discovery
from .model_config import router as model_config_router
from .semantic import router as semantic_router, snapshot as semantic_snapshot
from .terminology import router as terminology_router, enqueue as enqueue_terminology

from .subscriptions import router as subscriptions_router, feedback_for

Mode = Literal["live", "demo"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    repo.init_db()
    init_jobs()
    init_documents()
    for paper in demo_papers():
        repo.upsert_paper(paper)
    yield


app = FastAPI(title="PaperPilot API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174", "http://localhost:5174", "http://127.0.0.1:4173", "http://localhost:4173"],
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"])
app.include_router(jobs_router)
app.include_router(reading_router)
app.include_router(local_papers_router)
app.include_router(model_config_router)
app.include_router(semantic_router)
app.include_router(terminology_router)
app.include_router(subscriptions_router)


@app.middleware("http")
async def protect_local_writes(request: Request, call_next):
    # CORS controls reading; explicitly reject cross-site writes, including simple form requests.
    allowed = {"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174", "http://localhost:5174", "http://127.0.0.1:4173", "http://localhost:4173",
               "http://127.0.0.1:8000", "http://localhost:8000"}
    origin = request.headers.get("origin")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and origin not in allowed:
        return JSONResponse({"detail": "本地应用拒绝来自其他站点的写入请求。"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
def health():
    settings = get_settings()
    return {"status": "ok", "mode": settings.mode, "model_configured": bool(settings.llm_api_key and settings.llm_model),
            "model": settings.llm_model, "worker": worker_status(),
            "sources": {"arxiv": "available", "openreview": "not_implemented", "semantic_scholar": "not_implemented"},
            "semantic_enabled": bool(settings.llm_api_key and settings.llm_model), "model_rerank_enabled": bool(settings.llm_api_key and settings.llm_model)}


@app.get("/api/profile")
def profile_get():
    return repo.get_profile()


@app.put("/api/profile")
def profile_put(profile: Profile):
    return repo.save_profile(profile)


@app.post("/api/query")
def query_expand(payload: QueryInput):
    try:
        return expand_query(payload.text)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def public_paper(paper: dict) -> dict:
    if paper.get("source") == "local":
        return {**{key: value for key, value in paper.items() if key != "raw_entry"},
                "recommendation": None, "semantic": None, "publication": None}
    public = {**{key: value for key, value in paper.items() if key != "raw_entry"}, "publication": publication(paper), "semantic": semantic_snapshot(paper)}
    # A library/detail view can outlive the search that produced its lexical score.
    # Compare both methods against the current saved interests, without rewriting historical runs.
    if paper.get("recommendation"):
        public["recommendation"] = {**rank_paper(paper, repo.get_profile(), paper["recommendation"].get("run_id", "")),
                                    "profile_scope": "current"}
    public["feedback"] = feedback_for(paper["id"], paper["mode"])
    return public


def match_score(paper: dict) -> float:
    semantic = paper.get("semantic", {}).get("result")
    base = semantic["score"] if semantic else (paper.get("recommendation") or {}).get("score", 0)
    return base + (paper.get("feedback") or {}).get("adjustment", 0)


@app.get("/api/papers")
def search(query: str = Query(default='', max_length=4000), start: int = Query(0, ge=0, le=10000),
           limit: int = Query(10, ge=1, le=50), days: int = Query(730, ge=0, le=36500),
           mode: Mode = "live", sort: Literal["match", "newest", "ccfa"] = "ccfa"):
    profile = repo.get_profile()
    if not query.strip():
        query = expand_query(" ".join(profile["interests"]) or "large language model")["query"]
    elif not re.search(r"\b(?:all|ti|abs|au|cat|id|submittedDate):", query):
        expanded = expand_query(query)
        query = expanded["query"]
    if not query.strip():
        raise HTTPException(422, "此中文主题尚无词典映射，请填写英文关键词或手动检索式。")
    run_id = str(uuid4())
    repo.save_run(run_id, query, mode, {"start": start, "limit": limit, "days": days, "sort": sort, "profile": profile})
    if mode == "live":
        try:
            source = conference_discovery.search(query, days) if sort == 'ccfa' else ArxivSource().search(query, start, limit, days, sort)
        except SourceError as error:
            raise HTTPException(error.status, str(error)) from error
        # Save the actual expression, including submittedDate, for this run.
        with repo.connect() as db:
            db.execute("UPDATE search_runs SET query=? WHERE id=?", (source["query"], run_id))
    else:
        candidates = demo_papers()
        # Offline samples support term OR matching; live mode supports full arXiv grammar.
        terms = re.findall(r'"([^\"]+)"', query) or [re.sub(r"\w+:", "", query)]
        candidates = [p for p in candidates if any(keyword_present(t, p["title"] + " " + p["abstract"]) for t in terms)]
        candidates = [p for p in candidates if matches_filters(p, profile, days)]
        if sort == "newest":
            candidates.sort(key=lambda p: p["published"], reverse=True)
        source = {"papers": candidates[start:start + limit], "total": len(candidates), "query": query,
                  "cached": False, "stale": False, "fetched_at": "2026-10-25T00:00:00Z",
                  "warning": "演示：历史论文元数据与摘要节选。离线检索仅支持关键词 OR 匹配，完整查询语法请切换真实模式。"}
    unique = {}
    for paper in source["papers"]:
        if paper["id"] not in unique or paper["version"] > unique[paper["id"]]["version"]:
            unique[paper["id"]] = paper
    repo.upsert_papers(unique.values())
    results = []
    recommendations = []
    for source_paper in unique.values():
        # Existing records can be newer than a stale source page; rank exactly what is displayed.
        paper = repo.get_paper(source_paper["id"], mode)
        if not matches_filters(paper, profile, days):
            continue
        rec = rank_paper(paper, profile, run_id)
        recommendations.append((paper, rec))
        persisted = paper
        if persisted["collection"]["interested"] and feedback_for(paper["id"], mode)["value"] != "not_relevant":
            # Use this response's recommendation, not a concurrent query's newer result.
            persisted["recommendation"] = rec
            results.append(public_paper(persisted))
    repo.save_recommendations(run_id, recommendations)
    if sort == "ccfa":
        results.sort(key=lambda p: (p["publication"]["status"] == "verified", match_score(p)), reverse=True)
    else:
        results.sort(key=lambda p: p["published"] if sort == "newest" else match_score(p), reverse=True)
    pooled = mode == 'live' and sort == 'ccfa'
    if pooled:
        verified_count = sum(p['publication']['status'] == 'verified' for p in results)
        discovery = {**source.get('discovery', {}), 'verified_count': verified_count,
                     'priority_status': 'verified_found' if verified_count else 'arxiv_fallback'}
        source['discovery'] = discovery
        if not verified_count and results:
            source['warning'] = '；'.join(filter(None, [source.get('warning'), '本轮未找到已核实且符合筛选条件的 CCF A 论文，已优先展示相关 arXiv 论文。']))
        source['total'] = len(results)
        # Persist every actual recall query and source status alongside the search run.
        with repo.connect() as db:
            row = db.execute('SELECT parameters FROM search_runs WHERE id=?',(run_id,)).fetchone()
            parameters = json.loads(row['parameters'])
            parameters['discovery'] = source.get('discovery', {})
            db.execute('UPDATE search_runs SET parameters=? WHERE id=?', (json.dumps(parameters,ensure_ascii=False),run_id))
        results = results[start:start + limit]
    return {**{k: v for k, v in source.items() if k != "papers"}, "papers": results, "start": start, "limit": limit,
            "mode": mode, "run_id": run_id, "filtered_count": len(results),
            "total_is_source_count": mode == "live" and not pooled, "ranking_scope": "candidate_pool" if pooled else "current_page", "next_start": start + limit,
            "has_more": start + limit < source["total"]}


@app.get("/api/collections")
def collections(mode: Mode = "live"):
    return {"papers": [public_paper(p) for p in repo.list_collections(mode)]}


@app.put("/api/collections/{paper_id:path}")
def collection_put(paper_id: str, patch: CollectionPatch, mode: Mode = "live"):
    try:
        paper = repo.update_collection(paper_id, mode, patch.model_dump(exclude_unset=True))
        return {**public_paper(paper), "terminology": enqueue_terminology(paper)}
    except KeyError as error:
        raise HTTPException(404, "论文尚未检索或不存在。") from error


@app.get("/api/export")
def export(ids: str = Query(default="", max_length=10000), format: Literal["markdown", "bibtex"] = "markdown", mode: Mode = "live"):
    if ids:
        paper_ids = list(dict.fromkeys(part.strip() for part in ids.split(",") if part.strip()))
        if len(paper_ids) > 100:
            raise HTTPException(422, "单次最多导出 100 篇论文。")
        papers = [repo.get_paper(paper_id, mode) for paper_id in paper_ids]
        if any(p is None for p in papers):
            raise HTTPException(404, "部分论文不存在，请重新选择。")
    else:
        papers = repo.list_collections(mode)
    if not papers:
        raise HTTPException(422, "请先收藏或选择论文。")
    body = export_bibtex(papers) if format == "bibtex" else export_markdown(papers)
    extension = "bib" if format == "bibtex" else "md"
    return Response(body, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="paperpilot.{extension}"'})


@app.get("/api/papers/{paper_id:path}")
def paper_get(paper_id: str, mode: Mode = "live"):
    paper = repo.get_paper(paper_id, mode)
    if paper is None:
        raise HTTPException(404, "论文不存在，请先在线检索。")
    return public_paper(paper)
