"""Persistence boundary; SQL and transactions are isolated for a future PostgreSQL adapter."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from collections.abc import Iterator

from .config import get_settings
from .schemas import Profile


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS papers(mode TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
              payload TEXT NOT NULL, PRIMARY KEY(mode,id));
            CREATE TABLE IF NOT EXISTS paper_versions(mode TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,
              payload TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(mode,id,version));
            CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS collections(mode TEXT NOT NULL,paper_id TEXT NOT NULL,payload TEXT NOT NULL,
              updated_at TEXT NOT NULL, PRIMARY KEY(mode,paper_id));
            CREATE TABLE IF NOT EXISTS recommendations(run_id TEXT NOT NULL,mode TEXT NOT NULL,paper_id TEXT NOT NULL,
              payload TEXT NOT NULL,created_at TEXT NOT NULL, PRIMARY KEY(run_id,mode,paper_id));
            CREATE TABLE IF NOT EXISTS search_runs(id TEXT PRIMARY KEY,query TEXT NOT NULL,mode TEXT NOT NULL,
              parameters TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_cache(cache_key TEXT PRIMARY KEY,payload TEXT NOT NULL,
              fetched_at TEXT NOT NULL,expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS source_locks(name TEXT PRIMARY KEY,owner TEXT,lease_until REAL NOT NULL DEFAULT 0,
              next_allowed REAL NOT NULL DEFAULT 0);
        """)
        db.execute("INSERT OR IGNORE INTO schema_version VALUES(1,?)", (utc_now(),))
        db.execute("INSERT OR IGNORE INTO profile VALUES(1,?)", (Profile().model_dump_json(),))
        db.execute("INSERT OR IGNORE INTO source_locks(name) VALUES('arxiv')")
        if not db.execute("SELECT 1 FROM schema_version WHERE version=3").fetchone():
            row = db.execute("SELECT payload FROM profile WHERE id=1").fetchone()
            profile = json.loads(row["payload"])
            profile["days"] = 730
            for topic in ["Agent 强化学习", "大模型后训练"]:
                if topic not in profile["interests"] and len(profile["interests"]) < 20:
                    profile["interests"].append(topic)
            db.execute("UPDATE profile SET payload=? WHERE id=1", (json.dumps(profile, ensure_ascii=False),))
            db.execute("INSERT INTO schema_version VALUES(3,?)", (utc_now(),))

    from .subscriptions import init_stage3
    init_stage3()


def get_profile() -> dict:
    with connect() as db:
        row = db.execute("SELECT payload FROM profile WHERE id=1").fetchone()
        return json.loads(row["payload"]) if row else Profile().model_dump()


def save_profile(profile: Profile) -> dict:
    with connect() as db:
        db.execute("INSERT INTO profile VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (profile.model_dump_json(),))
    return profile.model_dump()


def upsert_paper(paper: dict) -> None:
    upsert_papers([paper])


def upsert_papers(papers) -> None:
    # One short transaction for a search batch; never hold it during network calls.
    with connect() as db:
        for paper in papers:
            payload = json.dumps(paper, ensure_ascii=False)
            db.execute("INSERT INTO paper_versions VALUES(?,?,?,?,?) ON CONFLICT(mode,id,version) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at",
                       (paper["mode"], paper["id"], paper["version"], payload, paper["fetched_at"]))
            db.execute("INSERT INTO papers VALUES(?,?,?,?) ON CONFLICT(mode,id) DO UPDATE SET version=excluded.version,payload=excluded.payload WHERE excluded.version >= papers.version",
                       (paper["mode"], paper["id"], paper["version"], payload))


def default_collection() -> dict:
    return {"saved": False, "read": False, "interested": True, "tags": [], "notes": ""}


def get_paper(paper_id: str, mode: str = "live") -> dict | None:
    with connect() as db:
        row = db.execute("SELECT payload FROM papers WHERE mode=? AND id=?", (mode, paper_id)).fetchone()
        if row is None:
            return None
        paper = json.loads(row["payload"])
        collection = db.execute("SELECT payload FROM collections WHERE mode=? AND paper_id=?", (mode, paper_id)).fetchone()
        recommendation = db.execute("SELECT payload FROM recommendations WHERE mode=? AND paper_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (mode, paper_id)).fetchone()
        paper["collection"] = json.loads(collection["payload"]) if collection else default_collection()
        paper["recommendation"] = json.loads(recommendation["payload"]) if recommendation else None
        return paper


def update_collection(paper_id: str, mode: str, patch: dict) -> dict:
    # Merge under one write transaction, so simultaneous notes/save actions cannot lose fields.
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM papers WHERE mode=? AND id=?", (mode, paper_id)).fetchone():
            raise KeyError(paper_id)
        row = db.execute("SELECT payload FROM collections WHERE mode=? AND paper_id=?", (mode, paper_id)).fetchone()
        value = json.loads(row["payload"]) if row else default_collection()
        value.update({k: v for k, v in patch.items() if v is not None})
        db.execute("INSERT INTO collections VALUES(?,?,?,?) ON CONFLICT(mode,paper_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                   (mode, paper_id, json.dumps(value, ensure_ascii=False), utc_now()))
    return get_paper(paper_id, mode)


def list_collections(mode: str) -> list[dict]:
    with connect() as db:
        ids = [r["paper_id"] for r in db.execute("SELECT paper_id FROM collections WHERE mode=? ORDER BY updated_at DESC", (mode,))]
    papers = [get_paper(paper_id, mode) for paper_id in ids]
    return [p for p in papers if p and p["collection"]["saved"]]


def save_recommendation(run_id: str, paper: dict, recommendation: dict) -> None:
    save_recommendations(run_id, [(paper, recommendation)])


def save_recommendations(run_id: str, items) -> None:
    with connect() as db:
        db.executemany("INSERT OR REPLACE INTO recommendations VALUES(?,?,?,?,?)",
                       [(run_id, p["mode"], p["id"], json.dumps(rec, ensure_ascii=False), utc_now()) for p, rec in items])


def save_run(run_id: str, query: str, mode: str, parameters: dict) -> None:
    with connect() as db:
        db.execute("INSERT INTO search_runs VALUES(?,?,?,?,?)", (run_id, query, mode, json.dumps(parameters, ensure_ascii=False), utc_now()))
