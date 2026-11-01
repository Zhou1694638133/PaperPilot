"""arXiv Atom adapter. Policy: https://info.arxiv.org/help/api/tou.html
Single outbound connection across processes sharing this database, >=3 seconds apart.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
import time
from typing import Protocol
from uuid import uuid4
import xml.etree.ElementTree as ET

import httpx

from .config import get_settings
from .repository import connect, utc_now

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
OPEN_SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"


class SourceError(Exception):
    def __init__(self, message: str, status: int = 502):
        self.status = status
        super().__init__(message)


class PaperSource(Protocol):
    def search(self, query: str, start: int, limit: int, days: int, sort: str) -> dict: ...


def canonical_id(value: str) -> tuple[str, int]:
    raw = value.split("/abs/")[-1]
    match = re.fullmatch(r"(\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v(\d+))?", raw)
    if not match:
        raise SourceError("arXiv 返回了无法识别的论文 ID。")
    return match.group(1), int(match.group(2) or 1)


def parse_feed(content: bytes) -> dict:
    if len(content) > 10 * 1024 * 1024 or b"<!DOCTYPE" in content.upper():
        raise SourceError("arXiv 响应过大或格式不安全。")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise SourceError("arXiv 返回了无效 XML，请稍后重试。") from error
    if root.tag != ATOM + "feed":
        raise SourceError("arXiv 未返回 Atom feed。")
    fetched_at = utc_now()
    papers = []
    for entry in root.findall(ATOM + "entry"):
        def value(name: str, namespace: str = ATOM) -> str:
            return (entry.findtext(namespace + name) or "").strip()
        identifier = value("id")
        if "/api/errors" in identifier or value("title").lower() == "error":
            raise SourceError("arXiv 拒绝了检索式，请检查字段与括号格式。", 400)
        paper_id, version = canonical_id(identifier)
        published, updated = value("published"), value("updated")
        if not value("title") or not value("summary"):
            raise SourceError("arXiv 返回缺失标题或摘要，未生成替代内容。")
        try:
            published_date = datetime.fromisoformat(published.replace("Z", "+00:00"))
            updated_date = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if published_date.tzinfo is None or updated_date.tzinfo is None:
                raise ValueError("Missing date timezone")
        except ValueError as error:
            raise SourceError("arXiv 返回缺失或无效的日期，未将更新日期替代首次公开日期。") from error
        papers.append({"id": paper_id, "version": version, "title": " ".join(value("title").split()),
                       "authors": [" ".join((a.findtext(ATOM + "name") or "").split()) for a in entry.findall(ATOM + "author")],
                       "abstract": " ".join(value("summary").split()), "published": published, "updated": updated,
                       "categories": [c.attrib["term"] for c in entry.findall(ATOM + "category") if "term" in c.attrib],
                       "url": f"https://arxiv.org/abs/{paper_id}v{version}", "pdf_url": f"https://arxiv.org/pdf/{paper_id}v{version}",
                       "doi": value("doi", ARXIV) or None, "source": "arXiv", "mode": "live", "fetched_at": fetched_at,
                       "publication_status": "未核实", "publication_evidence": None,
                       "journal_reference": value("journal_ref", ARXIV) or None,
                       "raw_entry": ET.tostring(entry, encoding="unicode")})
    try:
        total = int(root.findtext(OPEN_SEARCH + "totalResults") or len(papers))
    except ValueError as error:
        raise SourceError("arXiv 返回无效结果计数。") from error
    return {"papers": papers, "total": total, "fetched_at": fetched_at}


def acquire_source_lock(owner: str) -> None:
    settings = get_settings()
    deadline = time.time() + settings.arxiv_timeout + 35
    while True:
        now = time.time()
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            lock = db.execute("SELECT * FROM source_locks WHERE name='arxiv'").fetchone()
            wait = max(lock["lease_until"] - now, lock["next_allowed"] - now)
            if wait <= 0:
                db.execute("UPDATE source_locks SET owner=?,lease_until=?,next_allowed=? WHERE name='arxiv'",
                           (owner, now + settings.arxiv_timeout + 10, now + settings.arxiv_min_interval))
                return
        if time.time() >= deadline:
            raise SourceError("arXiv 检索队列繁忙，请稍后重试。", 429)
        time.sleep(min(max(wait, 0.05), 0.25))


def release_source_lock(owner: str) -> None:
    with connect() as db:
        db.execute("UPDATE source_locks SET owner=NULL,lease_until=0 WHERE name='arxiv' AND owner=?", (owner,))


def with_date_filter(query: str, days: int) -> str:
    if not days:
        return query
    now = datetime.now(timezone.utc)
    after = now - timedelta(days=days)
    return f"({query}) AND submittedDate:[{after:%Y%m%d%H%M} TO {now:%Y%m%d%H%M}]"


class ArxivSource:
    def search(self, query: str, start: int, limit: int, days: int, sort: str) -> dict:
        settings = get_settings()
        actual_query = with_date_filter(query, days)
        params = {"search_query": actual_query, "start": start, "max_results": limit,
                  "sortBy": "submittedDate" if sort == "newest" else "relevance", "sortOrder": "descending"}
        cache_key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        with connect() as db:
            cached = db.execute("SELECT * FROM source_cache WHERE cache_key=?", (cache_key,)).fetchone()
        if cached and cached["expires_at"] > time.time():
            return {**json.loads(cached["payload"]), "query": actual_query, "cached": True, "stale": False}
        error = SourceError("arXiv 暂时不可达，请稍后重试。")
        # A bounded retry only for transient network/server responses, never a tight request loop.
        for attempt in range(2):
            owner = str(uuid4())
            acquire_source_lock(owner)
            try:
                with httpx.Client(timeout=settings.arxiv_timeout, limits=httpx.Limits(max_connections=1), follow_redirects=False,
                                  headers={"User-Agent": "PaperPilot/0.1 (local research client)", "Accept": "application/atom+xml"}) as client:
                    response = client.get(ARXIV_ENDPOINT, params=params)
                if response.status_code == 429:
                    error = SourceError("arXiv 请求受到限流，请稍后重试。", 429)
                    retry_after = min(30, max(3, int(response.headers.get("Retry-After", "6")) if response.headers.get("Retry-After", "6").isdigit() else 6))
                    with connect() as db:
                        db.execute("UPDATE source_locks SET next_allowed=MAX(next_allowed,?) WHERE name='arxiv'", (time.time() + retry_after,))
                    break
                if response.status_code >= 500:
                    error = SourceError(f"arXiv 服务暂时出错（HTTP {response.status_code}），请重试。")
                elif response.status_code != 200:
                    raise SourceError(f"arXiv 返回 HTTP {response.status_code}，请检查检索式或稍后重试。", 502)
                else:
                    result = parse_feed(response.content)
                    with connect() as db:
                        db.execute("INSERT INTO source_cache VALUES(?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at,expires_at=excluded.expires_at",
                                   (cache_key, json.dumps(result, ensure_ascii=False), result["fetched_at"], time.time() + settings.cache_ttl))
                    return {**result, "query": actual_query, "cached": False, "stale": False}
            except httpx.RequestError:
                error = SourceError("无法连接 arXiv 或请求超时；请检查网络后重试。")
            finally:
                release_source_lock(owner)
            if attempt == 0:
                time.sleep(3)
        if cached:
            return {**json.loads(cached["payload"]), "query": actual_query, "cached": True, "stale": True,
                    "warning": str(error) + " 当前显示过期缓存，获取时间见列表。"}
        raise error


class OpenReviewSource:
    def search(self, query: str, start: int, limit: int, days: int, sort: str) -> dict:
        raise NotImplementedError("OpenReview 适配器尚未接入，不能推断会议录用状态。")


class SemanticScholarSource:
    def search(self, query: str, start: int, limit: int, days: int, sort: str) -> dict:
        raise NotImplementedError("Semantic Scholar 适配器尚未接入。")
