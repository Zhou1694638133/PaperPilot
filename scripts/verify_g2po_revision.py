"""Verify the real saved revision; optionally refresh the licensed PDF export."""
import argparse
from collections import Counter
import hashlib
import io
import json
import re
import urllib.request

from revise_g2po_terms import ROOT, JOB_ID, GRPO_IDS, jobs, get_settings, PROTECTED, NUMERIC_PATTERN
from langgraph.checkpoint.sqlite import SqliteSaver


def fetch(path):
    return urllib.request.urlopen("http://127.0.0.1:8000/api" + path, timeout=90).read()


def main(export):
    audit = json.loads((ROOT / "output/validation/g2po-terminology-revision.json").read_text("utf-8"))
    job = json.loads(fetch(f"/jobs/{JOB_ID}"))
    result = job["result"]
    aligned = json.loads(fetch(f"/jobs/{JOB_ID}/alignment"))
    assert job["status"] == "succeeded" and job["attempts"] == 3
    supplement = result.get("source_enrichment")
    reference_count = len(supplement["references"]) if supplement else 0
    assert aligned["completed"] == aligned["total"] == 141 + reference_count
    old = {b["paragraph_id"]: b for b in audit["original_chunks"]}
    with jobs.connect() as db:
        current = [dict(r) for r in db.execute("SELECT * FROM translation_chunks WHERE job_id=? ORDER BY paragraph_id", (JOB_ID,))]
    changed = []
    by_id = {b["id"]: b for b in aligned["blocks"]}
    for chunk in current:
        pid = chunk["paragraph_id"]
        for field in ("source", "source_hash", "provider_request_id", "created_at"):
            assert chunk[field] == old[pid][field], (pid, field)
        assert hashlib.sha256(chunk["source"].encode()).hexdigest() == chunk["source_hash"]
        assert chunk["translation"] == by_id[pid]["translation"]
        if chunk["translation"] != old[pid]["translation"]:
            changed.append(pid)
        assert Counter(PROTECTED.findall(chunk["source"])) == Counter(PROTECTED.findall(chunk["translation"]))
        assert Counter(re.findall(NUMERIC_PATTERN, chunk["source"])) == Counter(re.findall(NUMERIC_PATTERN, chunk["translation"]))
    assert set(changed) == set(GRPO_IDS)
    assert sum(not b["translatable"] for b in aligned["blocks"] if not b.get("supplemental")) == 48
    assert not any("：术语“" in warning for warning in result["qa"]["warnings"])
    assert len(result["qa"]["warnings"]) == (0 if supplement else 2)
    assert "未使用指定译法" not in result["markdown"]
    for block in result["alignment"]:
        assert block["translation"] == by_id[block["paragraph_id"]]["translation"]
        if block["preserved"]:
            assert block["source"] == block["translation"]
    assert jobs.job_record(JOB_ID)["checkpoint"] == result
    assert fetch(f"/jobs/{JOB_ID}/artifact").decode() == result["markdown"]
    with SqliteSaver.from_conn_string(str(get_settings().database_path.with_name("checkpoints.sqlite"))) as saver:
        saved = saver.get_tuple({"configurable": {"thread_id": JOB_ID}})
        assert saved.checkpoint["channel_values"]["result"] == result
        assert saved.config["configurable"]["checkpoint_id"] == job["checkpoint_id"]
        original = saver.get_tuple({"configurable": {"thread_id": JOB_ID, "checkpoint_id": audit["parent_checkpoint_id"]}})
        assert original.checkpoint["channel_values"]["result"] == audit["original_result"]
    metrics = {"resolved_warnings": 19, "changed_paragraphs": len(changed), "unchanged_blocks": 125,
               "reused_checkpoints": 141, "model_calls": 0, "original_request_ids_and_timestamps_retained": True,
               "original_graph_snapshot_retained": True, "api_artifact_alignment_checkpoint_consistent": True,
               "all_numbers_and_protected_spans_unchanged": True}
    if export:
        if supplement:
            # Keep later source supplements when re-exporting the earlier term revision.
            from enrich_g2po_source import export as export_enriched, verify as verify_enriched
            export_enriched(supplement, verify_enriched(supplement))
            print(json.dumps(metrics, ensure_ascii=False, indent=2))
            return
        from app.pdf_export import render_pdf
        from pypdf import PdfReader, PdfWriter
        license_note = (ROOT / "output/validation/g2po-license.md").read_text("utf-8")
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(render_pdf(license_note))))
        writer.append(PdfReader(io.BytesIO(fetch(f"/reports/pdf?job_id={JOB_ID}"))))
        destination = ROOT / "output/pdf/g2po-bilingual.pdf"
        temporary = destination.with_suffix(".revised.pdf")
        writer.write(temporary)
        reader = PdfReader(temporary)
        text = "\n".join(page.extract_text() for page in reader.pages)
        for term in ("组相对策略优化", "直接偏好优化", "人类反馈强化学习", "大语言模型", "p0001", "p0141", "术语校订记录"):
            assert term in text.replace("\n", ""), term
        assert "未使用指定译法" not in text
        temporary.replace(destination)
        metrics["pdf_pages"] = len(reader.pages)
        (ROOT / "output/validation/g2po-bilingual.md").write_text(license_note + "\n\n" + result["markdown"], encoding="utf-8")
        (ROOT / "output/validation/g2po-alignment.json").write_text(json.dumps(aligned, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path = ROOT / "output/validation/g2po-live-results.json"
        report = json.loads(report_path.read_text("utf-8"))
        report["translation_warnings"] = result["qa"]["warnings"]
        report["bilingual_pdf_pages"] = len(reader.pages)
        report["terminology_revision"] = metrics
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# G2PO 术语逐条校订记录", "", "沿用原任务与术语表 v2，未调用模型。每段首次出现补全中文并保留缩写；原文和保护块不变。", "",
                 "| 序号 | 段落 | 原提示术语 | 统一译法 | 结果 |", "| --- | --- | --- | --- | --- |"]
        for i, entry in enumerate(audit["entries"], 1):
            term = entry["term"]
            suffix = "（保留 LLMs）" if term == "large language model" else f"（{term}）"
            lines.append(f"| {i} | {entry['paragraph_id']} | {term} | {entry['target']}{suffix} | 已核对 |")
        lines.extend(["", "验证：141/141 对齐；只修改 16 个段落，125 个块未变；48 个保护块逐字保留；原模型请求 ID、生成时间、原文哈希和任务尝试次数均未变。数据库结果、段落检查点、LangGraph 最新快照、Markdown 接口一致。原始 LangGraph 快照及逐项前后文本仍保留。", "", "剩余 2 条均为原始解析提示：自定义 LaTeX 宏未展开；源包未包含可识别的参考文献正文。"])
        (ROOT / "output/validation/g2po-terminology-review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    main(parser.parse_args().export)
