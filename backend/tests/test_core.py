from datetime import datetime, timezone
import json
import time

import httpx
import pytest

from app import config, repository as repo
from app.demo import demo_papers
from app.exports import export_bibtex
from app.ranking import expand_query, matches_filters, rank_paper
from app.sources import ArxivSource, SourceError, canonical_id, parse_feed, with_date_filter


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config.get_settings.cache_clear()
    repo.init_db()
    from app import conference_discovery
    monkeypatch.setattr(conference_discovery, 'official_index', lambda days: ([], []))
    yield
    config.get_settings.cache_clear()


def atom_entry(version=1, published="2023-01-01T00:00:00Z", updated="2026-01-01T00:00:00Z"):
    return f'''<entry><id>http://arxiv.org/abs/2301.00001v{version}</id><title>Agent benchmark</title>
      <summary>A benchmark for agent evaluation. Code availability is unknown.</summary><author><name>A Author</name></author>
      <published>{published}</published><updated>{updated}</updated><category term="cs.AI"/></entry>'''


def atom_feed(entries):
    return ('''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <opensearch:totalResults>2</opensearch:totalResults>''' + entries + "</feed>").encode()


def test_versions_merge_without_destroying_annotations():
    old, newer = parse_feed(atom_feed(atom_entry(1) + atom_entry(2)))["papers"]
    repo.upsert_paper(old)
    repo.update_collection(old["id"], "live", {"saved": True, "notes": "keep", "tags": ["important"]})
    repo.upsert_paper(newer)
    repo.upsert_paper(old)
    paper = repo.get_paper(old["id"])
    assert paper["version"] == 2
    assert paper["collection"]["notes"] == "keep"
    assert paper["collection"]["saved"]
    with repo.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_versions").fetchone()[0] == 2
    repo.init_db()
    assert repo.get_paper(old["id"])["collection"]["tags"] == ["important"]


def test_date_filter_uses_first_publication_not_update():
    paper = parse_feed(atom_feed(atom_entry()))["papers"][0]
    assert not matches_filters(paper, repo.get_profile(), 30, datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert matches_filters(paper, repo.get_profile(), 0)
    assert "submittedDate:[" in with_date_filter('all:"agent"', 7)
    assert paper["published"] != paper["updated"]


def test_canonical_id_accepts_legacy_and_versions():
    assert canonical_id("http://arxiv.org/abs/2301.00001v12") == ("2301.00001", 12)
    assert canonical_id("https://arxiv.org/abs/hep-th/9901001v2") == ("hep-th/9901001", 2)
    with pytest.raises(SourceError):
        canonical_id("https://evil.test/foo")


def test_invalid_source_dates_are_not_silently_invented():
    with pytest.raises(SourceError, match="日期"):
        parse_feed(atom_feed(atom_entry(published="")))
    with pytest.raises(SourceError):
        parse_feed(b"<html>blocked</html>")
    with pytest.raises(SourceError):
        parse_feed(b'<!DOCTYPE feed [<!ENTITY x "danger">]><feed/>')


def test_query_mapping_and_manual_expression():
    expanded = expand_query("推荐多智能体协作与评测")
    assert "multi-agent" in expanded["keywords"]
    assert "benchmark" in expanded["keywords"]
    assert "all:" in expanded["query"]
    assert expand_query('ti:"planning" AND cat:cs.AI')["method"] == "manual"
    assert expand_query("未知中文研究主题")["query"] == ""


def test_ranking_references_only_actual_source_text():
    paper = parse_feed(atom_feed(atom_entry()))["papers"][0]
    rec = rank_paper(paper, repo.get_profile(), "run-1")
    assert rec["coverage"] == "abstract"
    assert rec["method"] == "rules-v1"
    assert rec["evidence"]
    assert all(e["text"] in (paper["abstract"] + paper["title"]) for e in rec["evidence"])
    assert rec["score"] == pytest.approx(sum(f["value"] * f["weight"] for f in rec["features"]), abs=.1)
    assert "代码及数据可用性未核实" in rec["unknowns"]


def test_include_exclude_and_mode_isolation():
    profile = repo.get_profile()
    paper = demo_papers()[0]
    repo.upsert_paper(paper)
    repo.update_collection(paper["id"], "demo", {"saved": True})
    assert repo.list_collections("live") == []
    assert len(repo.list_collections("demo")) == 1
    profile["include_keywords"] = ["unmatched"]
    assert not matches_filters(paper, profile, 0)
    profile["include_keywords"] = []
    profile["exclude_keywords"] = ["AutoGen"]
    assert not matches_filters(paper, profile, 0)


def test_source_cache_prevents_duplicate_requests(monkeypatch):
    calls = []
    def response(self, url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, content=atom_feed(atom_entry()))
    monkeypatch.setattr(httpx.Client, "get", response)
    first = ArxivSource().search('all:"agent"', 0, 10, 0, "match")
    second = ArxivSource().search('all:"agent"', 0, 10, 0, "match")
    assert len(calls) == 1
    assert not first["cached"] and second["cached"]
    assert first["papers"] == second["papers"]


def test_rate_limit_is_explicit_without_demo_fallback(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **k: httpx.Response(429, headers={"Retry-After": "5"}))
    with pytest.raises(SourceError) as caught:
        ArxivSource().search('all:"agent"', 0, 10, 0, "match")
    assert caught.value.status == 429
    with repo.connect() as db:
        lock = db.execute("SELECT * FROM source_locks WHERE name='arxiv'").fetchone()
        assert lock["next_allowed"] > time.time()
        assert lock["owner"] is None


def test_stale_cache_has_visible_failure_and_timestamp(monkeypatch):
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **k: httpx.Response(200, content=atom_feed(atom_entry())))
    result = ArxivSource().search('all:"agent"', 0, 10, 0, "match")
    with repo.connect() as db:
        db.execute("UPDATE source_cache SET expires_at=0")
        db.execute("UPDATE source_locks SET next_allowed=0")
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **k: httpx.Response(429))
    stale = ArxivSource().search('all:"agent"', 0, 10, 0, "match")
    assert stale["stale"] and stale["cached"]
    assert stale["fetched_at"] == result["fetched_at"]
    assert "过期缓存" in stale["warning"]


def test_export_uses_first_year_and_escapes_bibtex():
    paper = demo_papers()[0]
    paper["title"] = r"A {test} & 100%"
    result = export_bibtex([paper])
    assert "year = {2023}" in result
    assert r"\{test\} \& 100\%" in result


def test_api_no_key_persistence_validation_and_demo_dates():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert not health["model_configured"]
        assert "llm_api_key" not in json.dumps(health)
        response = client.get("/api/papers", params={"mode": "demo", "query": "agent", "days": 7})
        assert response.status_code == 200
        assert response.json()["papers"] == []
        result = client.get("/api/papers", params={"mode": "demo", "query": "AutoGen", "days": 0}).json()
        assert len(result["papers"]) == 1
        paper_id = result["papers"][0]["id"]
        saved = client.put(f"/api/collections/{paper_id}?mode=demo", json={"saved": True, "notes": "中文笔记"})
        assert saved.status_code == 200
        assert saved.json()["collection"]["saved"]
        assert client.get("/api/collections?mode=live").json()["papers"] == []
        assert len(client.get("/api/collections?mode=demo").json()["papers"]) == 1
        export = client.get("/api/export", params={"mode": "demo", "format": "markdown"})
        assert export.status_code == 200 and "中文笔记" in export.text
        assert client.get("/api/papers?limit=999").status_code == 422
        assert client.put("/api/profile", headers={"Origin": "https://untrusted.example"}, json={}).status_code == 403
    with TestClient(app) as client:
        assert client.get(f"/api/papers/{paper_id}?mode=demo").json()["collection"]["notes"] == "中文笔记"


def test_search_uses_evidence_from_displayed_latest_version(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    older = parse_feed(atom_feed(atom_entry(1)))["papers"][0]
    newer = {**older, "version": 2, "abstract": "A new agent evaluation benchmark with updated results."}
    with TestClient(app) as client:
        repo.upsert_paper(newer)
        monkeypatch.setattr(ArxivSource, "search", lambda *a, **k: {
            "papers": [older], "total": 1, "query": "all:agent", "cached": True,
            "stale": False, "fetched_at": older["fetched_at"]})
        paper = client.get("/api/papers?query=agent&days=0").json()["papers"][0]
        assert paper["version"] == 2
        assert paper["abstract"] == newer["abstract"]
        assert all(e["text"] in paper["abstract"] + paper["title"] for e in paper["recommendation"]["evidence"])


def test_missing_abstract_and_naive_dates_are_source_errors():
    with pytest.raises(SourceError):
        parse_feed(atom_feed(atom_entry().replace("<summary>A benchmark for agent evaluation. Code availability is unknown.</summary>", "")))
    with pytest.raises(SourceError):
        parse_feed(atom_feed(atom_entry(published="2023-01-01")))
