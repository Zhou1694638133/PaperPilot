"""Apply the reviewed G2PO terminology edits without running a model.

Run from the project root with backend/.venv/Scripts/python.exe.
Default is read-only; --apply writes an audited, atomic revision to both databases.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import jobs  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.skills import NUMERIC_PATTERN, PROTECTED  # noqa: E402
from langgraph.checkpoint.base import empty_checkpoint  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

JOB_ID = "49da526c-22d9-403b-ad35-bff2944230b2"
REVISION = "g2po-terminology-2026-10-25"
GRPO_IDS = "p0001 p0003 p0008 p0009 p0012 p0026 p0034 p0035 p0041 p0046 p0050 p0059 p0061 p0064 p0088 p0107".split()
EDITS = [(pid, "GRPO", "组相对策略优化") for pid in GRPO_IDS] + [
    ("p0001", "large language model", "大语言模型"),
    ("p0012", "DPO", "直接偏好优化"),
    ("p0012", "RLHF", "人类反馈强化学习"),
]


def revise_text(text: str, term: str, target: str) -> str:
    """Change the first prose occurrence, never a math/code/citation span."""
    old = "大型语言模型" if term == "large language model" else term
    replacement = target if term == "large language model" else f"{target}（{term}）"
    pattern = re.compile(re.escape(old) if term == "large language model" else rf"(?<![A-Za-z]){term}(?![A-Za-z])")
    protected = [(m.start(), m.end()) for m in PROTECTED.finditer(text)]
    for match in pattern.finditer(text):
        if not any(start < match.end() and end > match.start() for start, end in protected):
            revised = text[:match.start()] + replacement + text[match.end():]
            if Counter(PROTECTED.findall(text)) != Counter(PROTECTED.findall(revised)):
                raise ValueError("Protected spans changed")
            if Counter(re.findall(NUMERIC_PATTERN, text)) != Counter(re.findall(NUMERIC_PATTERN, revised)):
                raise ValueError("Numbers changed")
            return revised
    raise ValueError(f"No editable occurrence: {term}")


def main(apply: bool) -> None:
    record = jobs.job_record(JOB_ID)
    result = json.loads(record["result_json"])
    if any(item.get("id") == REVISION for item in result.get("editorial_revisions", [])):
        print("Revision already applied; no changes or model calls.")
        return
    if record["status"] != "succeeded" or record["owner"] or record["model_inflight"]:
        raise ValueError("Only an idle, successfully completed task may be revised")
    if record["checkpoint"] != result:
        raise ValueError("Task checkpoint does not match result")
    previous = deepcopy(result)
    alignment = {b["paragraph_id"]: b for b in result["alignment"]}
    expected = {f'{pid}：术语“{term}”未使用指定译法“{target}”，请人工复核。' for pid, term, target in EDITS}
    warnings = result["qa"]["warnings"]
    if {w for w in warnings if "：术语“" in w} != expected:
        raise ValueError("The warnings differ from the reviewed 19-item revision")
    audit_entries = []
    for pid, term, target in EDITS:
        block = alignment[pid]
        if block["preserved"] or result["glossary"][term] != target:
            raise ValueError("Protected block or changed glossary")
        before = block["translation"]
        block["translation"] = revise_text(before, term, target)
        audit_entries.append({"paragraph_id": pid, "term": term, "target": target,
                              "before": before, "after": block["translation"]})
    changed = set(GRPO_IDS)
    markdown = result["markdown"]
    for old in previous["alignment"]:
        new = alignment[old["paragraph_id"]]
        if old["paragraph_id"] in changed:
            needle = f"### 译文\n\n{old['translation']}\n\n"
            if markdown.count(needle) != 1:
                raise ValueError("Ambiguous Markdown paragraph")
            markdown = markdown.replace(needle, f"### 译文\n\n{new['translation']}\n\n", 1)
        if Counter(PROTECTED.findall(new["source"])) != Counter(PROTECTED.findall(new["translation"])):
            raise ValueError(f"Source protection failed: {new['paragraph_id']}")
        if Counter(re.findall(NUMERIC_PATTERN, new["source"])) != Counter(re.findall(NUMERIC_PATTERN, new["translation"])):
            raise ValueError(f"Source numeric check failed: {new['paragraph_id']}")
    remaining = []
    for block in result["alignment"]:
        for term, target in result["glossary"].items():
            if not block["preserved"] and term.lower() in block["source"].lower() and target not in block["translation"]:
                remaining.append((block["paragraph_id"], term))
    if remaining:
        raise ValueError(f"Unresolved terms: {remaining}")
    for warning in expected:
        markdown = markdown.replace(f"- {warning}\n", "")
    stamp = jobs.utc_now()
    revision = {"id": REVISION, "revised_at": stamp, "method": "reviewed-local-terminology-edits",
                "resolved_warnings": 19, "changed_paragraphs": sorted(changed), "model_calls": 0,
                "parent_checkpoint_id": record["checkpoint_id"]}
    result["qa"]["warnings"] = [w for w in warnings if w not in expected]
    result["editorial_revisions"] = [revision]
    result["markdown"] = markdown + "\n\n## 术语校订记录\n\n已逐条处理 19 条术语提示，局部修订 16 个段落。复用全部 141 个检查点，未调用模型重新翻译。\n\n统一译法：组相对策略优化（GRPO）、直接偏好优化（DPO）、人类反馈强化学习（RLHF）、大语言模型（保留 LLM/LLMs）。公式、引用、代码和数值保持原样。\n"
    audit = {**revision, "job_id": JOB_ID, "original_warnings": warnings,
             "remaining_warnings": result["qa"]["warnings"], "entries": audit_entries}
    print(json.dumps({"apply": apply, "resolved": 19, "changed_blocks": len(changed),
                      "aligned": len(alignment), "remaining_source_warnings": len(result["qa"]["warnings"]),
                      "model_calls": 0}, ensure_ascii=False))
    if not apply:
        return
    graph_path = get_settings().database_path.with_name("checkpoints.sqlite")
    with SqliteSaver.from_conn_string(str(graph_path)) as saver:
        snapshot = saver.get_tuple({"configurable": {"thread_id": JOB_ID}})
        if snapshot is None or snapshot.checkpoint["channel_values"]["result"] != previous:
            raise ValueError("LangGraph result differs from reviewed result")
        checkpoint = deepcopy(snapshot.checkpoint)
        fresh = empty_checkpoint()
        checkpoint.update(id=fresh["id"], ts=fresh["ts"], updated_channels=["result"])
        checkpoint["channel_values"]["result"] = result
        checkpoint["channel_versions"]["result"] = saver.get_next_version(checkpoint["channel_versions"].get("result"), None)
        kind, serialized = saver.serde.dumps_typed(checkpoint)
        metadata = {**snapshot.metadata, "source": "update", "step": snapshot.metadata["step"] + 1,
                    "editorial_revision": REVISION}
    # ATTACH keeps task, chunks, artifact, event and graph snapshot in one transaction.
    # Original graph snapshot and full before/after text remain available for audit.
    with closing(jobs.connect()) as conn, conn:
        conn.execute("ATTACH DATABASE ? AS graph_db", (str(graph_path),))
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM jobs WHERE id=?", (JOB_ID,)).fetchone()
        if current["result_json"] != record["result_json"] or current["status"] != "succeeded":
            raise ValueError("Task changed during review")
        latest = conn.execute("SELECT checkpoint_id FROM graph_db.checkpoints WHERE thread_id=? AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1", (JOB_ID,)).fetchone()
        if latest[0] != record["checkpoint_id"]:
            raise ValueError("Graph changed during review")
        chunks = [dict(row) for row in conn.execute("SELECT * FROM translation_chunks WHERE job_id=? ORDER BY paragraph_id", (JOB_ID,))]
        if len(chunks) != len(alignment):
            raise ValueError("Incomplete saved checkpoints")
        old_blocks = {b["paragraph_id"]: b for b in previous["alignment"]}
        for chunk in chunks:
            old = old_blocks[chunk["paragraph_id"]]
            if any(chunk[key] != old[key] for key in ("source", "source_hash", "translation")):
                raise ValueError("Saved paragraph differs from result")
            if hashlib.sha256(chunk["source"].encode()).hexdigest() != chunk["source_hash"]:
                raise ValueError("Source hash mismatch")
        audit["original_chunks"] = chunks
        audit["original_result"] = previous
        audit["new_checkpoint_id"] = checkpoint["id"]
        audit_path = ROOT / "output/validation/g2po-terminology-revision.json"
        with audit_path.open("x", encoding="utf-8") as file:
            json.dump(audit, file, ensure_ascii=False, indent=2)
        for pid in changed:
            conn.execute("UPDATE translation_chunks SET translation=? WHERE job_id=? AND paragraph_id=?",
                         (alignment[pid]["translation"], JOB_ID, pid))
        conn.execute("UPDATE jobs SET result_json=?,checkpoint_json=?,checkpoint_id=?,updated_at=?,stage=? WHERE id=?",
                     (jobs.encode(result), jobs.encode(result), checkpoint["id"], stamp, "术语校订已保存（19 条）", JOB_ID))
        conn.execute("UPDATE job_artifacts SET content=? WHERE job_id=?", (result["markdown"], JOB_ID))
        conn.execute("INSERT INTO graph_db.checkpoints (thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,type,checkpoint,metadata) VALUES (?,?,?,?,?,?,?)",
                     (JOB_ID, "", checkpoint["id"], record["checkpoint_id"], kind, serialized, jobs.encode(metadata).encode()))
        jobs._event(conn, JOB_ID)
    print("Saved audited revision; provider request IDs, original timestamps and attempts retained.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
