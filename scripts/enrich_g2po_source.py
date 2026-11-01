"""Audited local source supplement for G2PO; no model calls or retranslation.

Default: inspect/verify only. --apply: persist. --export: export a saved supplement.
"""
import argparse
from contextlib import closing
from copy import deepcopy
import hashlib
import io
import json
import re
import urllib.request

from revise_g2po_terms import ROOT, JOB_ID, jobs, get_settings
from app import fulltext
from app.skills import _header
from app.source_enrichment import build_enrichment, display_blocks
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver

REVISION = "g2po-source-supplement-2026-10-25"
AUDIT = ROOT / "output/validation/g2po-source-enrichment-audit.json"
PDF_URL = "https://arxiv.org/pdf/2606.22995v2"


def build_markdown(record, result, enrichment):
    text = _header(record, "全文双语对照") + "## 来源补全说明\n\n" + "\n".join(enrichment["notes"]) + "\n\n"
    blocks = [{"id": b["paragraph_id"], "text": b["source"], "translation": b["translation"],
               "section": b["section"], "location": b["location"], "preserved": b["preserved"]} for b in result["alignment"]]
    for block in display_blocks(blocks, enrichment):
        text += f"## {block['id']} · {block.get('display_section', block['section'])}\n\n位置：{block['location']}\n\n"
        if block.get("supplemental"):
            text += f"来源：{block['source_url']}\n\n### 参考文献原文（不翻译）\n\n{block['text']}\n\n"
        else:
            text += f"### 原文\n\n{block.get('display_text', block['text'])}\n\n### {'保留原文（宏按定义展开）' if block['preserved'] else '译文'}\n\n{block.get('display_translation', block['translation'])}\n\n"
    text += "## 质量检查\n\n原有 141 个翻译检查点逐字保留，未新增模型调用；显示层仅展开源文件明确声明的宏。新增 64 条参考文献保留英文原文，带原 PDF 页码。原文和译文的数值、公式、引用及请求记录未改写。\n\n结构检查不代表逐句语义准确或独立复现实验。参考文献仅合并空白，断词和原 PDF 排版造成的字间距问题未猜补。标准 LaTeX 公式、引用和图表版式仍需对照原 PDF。\n\n"
    if "## 术语校订记录" in result["markdown"]:
        text += "## 术语校订记录" + result["markdown"].split("## 术语校订记录", 1)[1]
    return text


def apply_revision(record, document, enrichment):
    previous = json.loads(record["result_json"])
    result = deepcopy(previous)
    result["source_enrichment"] = enrichment
    result["qa"]["warnings"] = [w for w in result["qa"]["warnings"] if w not in document["warnings"]]
    result["qa"]["supplemental_reference_count"] = len(enrichment["references"])
    result["markdown"] = build_markdown(record, previous, enrichment)
    stamp = jobs.utc_now()
    result.setdefault("editorial_revisions", []).append({"id": REVISION, "revised_at": stamp,
        "method": "source-backed-display-enrichment", "model_calls": 0,
        "parent_checkpoint_id": record["checkpoint_id"], "enrichment_hash": enrichment["hash"]})
    updated_document = deepcopy(document)
    updated_document["source_enrichment"] = enrichment
    # Only the two reviewed warnings are resolved; any other warning must survive.
    resolved = {"LaTeX 自定义命令保留为原始标记；未执行宏，需对照原 PDF 检查排版和公式。",
                "源包未包含可识别的参考文献正文，请查看原 PDF 的参考文献页。"}
    if set(document["warnings"]) != resolved:
        raise ValueError("Source warnings differ from the reviewed pair")
    updated_document["warnings"] = [w for w in document["warnings"] if w not in resolved]
    updated_document["status"] = "needs_review" if updated_document["warnings"] else "ready"
    updated_document["note"] = "原始 141 个文本/保护块保持不变；已补充宏展开显示与 PDF 参考文献。图像及复杂公式的版式仍以原 PDF 为准。"
    graph_path = get_settings().database_path.with_name("checkpoints.sqlite")
    with SqliteSaver.from_conn_string(str(graph_path)) as saver:
        snapshot = saver.get_tuple({"configurable": {"thread_id": JOB_ID}})
        if snapshot is None or snapshot.checkpoint["channel_values"]["result"] != previous:
            raise ValueError("Graph result differs from task")
        checkpoint = deepcopy(snapshot.checkpoint)
        fresh = empty_checkpoint()
        checkpoint.update(id=fresh["id"], ts=fresh["ts"], updated_channels=["result"])
        checkpoint["channel_values"]["result"] = result
        checkpoint["channel_versions"]["result"] = saver.get_next_version(checkpoint["channel_versions"].get("result"), None)
        kind, serialized = saver.serde.dumps_typed(checkpoint)
        metadata = {**snapshot.metadata, "source": "update", "step": snapshot.metadata["step"] + 1, "editorial_revision": REVISION}
    with closing(jobs.connect()) as db, db:
        db.execute("ATTACH DATABASE ? AS graph_db", (str(graph_path),))
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM jobs WHERE id=?", (JOB_ID,)).fetchone()
        if current["result_json"] != record["result_json"] or current["status"] != "succeeded" or current["owner"]:
            raise ValueError("Task changed or is running")
        row = db.execute("SELECT payload FROM documents WHERE mode=? AND paper_id=? AND version=? AND parser_version=?",
                         (document["mode"], document["paper_id"], document["version"], document["parser_version"])).fetchone()
        if not row or json.loads(row["payload"]) != document:
            raise ValueError("Document changed during inspection")
        latest = db.execute("SELECT checkpoint_id FROM graph_db.checkpoints WHERE thread_id=? AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1", (JOB_ID,)).fetchone()
        if latest[0] != record["checkpoint_id"]:
            raise ValueError("Graph changed during inspection")
        chunks = [dict(r) for r in db.execute("SELECT * FROM translation_chunks WHERE job_id=? ORDER BY paragraph_id", (JOB_ID,))]
        audit = {"id": REVISION, "revised_at": stamp, "original_document": document,
                 "original_result": previous, "original_chunks": chunks, "original_input_json": record["input_json"],
                 "attempts": record["attempts"], "parent_checkpoint_id": record["checkpoint_id"],
                 "new_checkpoint_id": checkpoint["id"], "enrichment": enrichment}
        with AUDIT.open("x", encoding="utf-8") as file:
            json.dump(audit, file, ensure_ascii=False, indent=2)
        db.execute("UPDATE documents SET payload=? WHERE mode=? AND paper_id=? AND version=? AND parser_version=?",
                   (jobs.encode(updated_document), document["mode"], document["paper_id"], document["version"], document["parser_version"]))
        db.execute("UPDATE jobs SET result_json=?,checkpoint_json=?,checkpoint_id=?,updated_at=?,stage=? WHERE id=?",
                   (jobs.encode(result), jobs.encode(result), checkpoint["id"], stamp, "源文件补全已保存（宏与64条参考文献）", JOB_ID))
        db.execute("UPDATE job_artifacts SET content=? WHERE job_id=?", (result["markdown"], JOB_ID))
        db.execute("INSERT INTO graph_db.checkpoints (thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,type,checkpoint,metadata) VALUES (?,?,?,?,?,?,?)",
                   (JOB_ID, "", checkpoint["id"], record["checkpoint_id"], kind, serialized, jobs.encode(metadata).encode()))
        jobs._event(db, JOB_ID)


def verify(enrichment):
    audit = json.loads(AUDIT.read_text("utf-8"))
    record = jobs.job_record(JOB_ID)
    result = json.loads(record["result_json"])
    document = fulltext.get_document(record["paper"])
    with jobs.connect() as db:
        chunks = [dict(r) for r in db.execute("SELECT * FROM translation_chunks WHERE job_id=? ORDER BY paragraph_id", (JOB_ID,))]
    assert chunks == audit["original_chunks"] and len(chunks) == 141
    assert record["input_json"] == audit["original_input_json"]
    assert record["attempts"] == audit["attempts"] and record["status"] == "succeeded"
    assert document["blocks"] == audit["original_document"]["blocks"]
    assert document["document_hash"] == audit["original_document"]["document_hash"]
    assert result["alignment"] == audit["original_result"]["alignment"]
    assert result["source_enrichment"] == document["source_enrichment"] == enrichment
    assert record["checkpoint"] == result
    assert not result["qa"]["warnings"] and not document["warnings"]
    assert "\\MethodName" not in result["markdown"]
    with SqliteSaver.from_conn_string(str(get_settings().database_path.with_name("checkpoints.sqlite"))) as saver:
        current = saver.get_tuple({"configurable": {"thread_id": JOB_ID}})
        assert current.checkpoint["channel_values"]["result"] == result
        prior = saver.get_tuple({"configurable": {"thread_id": JOB_ID, "checkpoint_id": audit["parent_checkpoint_id"]}})
        assert prior.checkpoint["channel_values"]["result"] == audit["original_result"]
    return {"unchanged_translation_checkpoints": 141, "supplemental_references": 64,
            "reference_pdf_pages": [10, 11, 12, 13, 14], "resolved_custom_macros": enrichment["macros"],
            "source_macro_occurrences": sum(len(re.findall(r"\\MethodName(?![A-Za-z@])", b["text"])) for b in document["blocks"]),
            "model_calls": 0, "original_input_and_source_hash_retained": True,
            "original_request_ids_timestamps_and_attempts_retained": True, "remaining_original_warnings": 0}


def export(enrichment, metrics):
    from app.pdf_export import render_pdf
    from pypdf import PdfReader, PdfWriter
    def fetch(path):
        return urllib.request.urlopen("http://127.0.0.1:8000/api" + path, timeout=90).read()
    aligned = json.loads(fetch(f"/jobs/{JOB_ID}/alignment"))
    document = json.loads(fetch("/fulltext?paper_id=2606.22995&mode=live"))["document"]
    assert aligned["checkpoint_completed"] == aligned["checkpoint_total"] == 141
    assert aligned["completed"] == aligned["total"] == 205 and aligned["supplemental_count"] == 64
    assert document["paragraph_count"] == 205 and document["base_paragraph_count"] == 141
    assert [b["text"] for b in aligned["blocks"][-64:]] == [b["text"] for b in enrichment["references"]]
    assert aligned["blocks"][0]["display_translation"].count("G2PO") > 0
    result = aligned["job"]["result"]
    assert fetch(f"/jobs/{JOB_ID}/artifact").decode() == result["markdown"]
    license_note = (ROOT / "output/validation/g2po-license.md").read_text("utf-8")
    license_note = license_note.replace("公式、代码及保护块保留原文；自定义宏未执行，参考文献正文未从源包识别。", "原始公式、代码及翻译检查点保留；展示层依据源文件定义将 MethodName 展开为 G2PO，参考文献从原 PDF 第 10–14 页补充。未重新翻译正文。")
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(render_pdf(license_note))))
    writer.append(PdfReader(io.BytesIO(fetch(f"/reports/pdf?job_id={JOB_ID}"))))
    output = ROOT / "output/pdf/g2po-bilingual.pdf"
    temporary = output.with_suffix(".revised.pdf")
    writer.write(temporary)
    pdf = PdfReader(temporary)
    text = "\n".join(p.extract_text() for p in pdf.pages)
    assert "\\MethodName" not in text
    for number in range(1, 65):
        assert f"ref{number:04d}" in text
    assert "参考文献正文未从源包识别" not in text
    metrics["pdf_pages"] = len(pdf.pages)
    metrics["api_alignment_total"] = 205
    temporary.replace(output)
    folder = ROOT / "output/validation"
    (folder / "g2po-bilingual.md").write_text(license_note + "\n\n" + result["markdown"], encoding="utf-8")
    (folder / "g2po-alignment.json").write_text(json.dumps(aligned, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "g2po-source-enrichment.json").write_text(json.dumps(enrichment, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "g2po-source-enrichment-results.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = folder / "g2po-live-results.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["source_enrichment"] = metrics
    report["translation_warnings"] = []
    report["bilingual_pdf_pages"] = len(pdf.pages)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(apply, export_pdf):
    record = jobs.job_record(JOB_ID)
    document = fulltext.get_document(record["paper"])
    if record["status"] != "succeeded" or record["model_inflight"]:
        raise ValueError("Task must be idle and complete")
    source = fulltext.cached_asset(document["source_url"])
    pdf = fulltext.cached_asset(PDF_URL)
    if not source or not pdf:
        raise ValueError("Exact source/PDF must already be downloaded and cached")
    enrichment = document.get("source_enrichment")
    if enrichment:
        # Re-export the verified supplement without parsing the whole paper again.
        assert hashlib.sha256(source).hexdigest() == enrichment["source_sha256"]
        assert hashlib.sha256(pdf).hexdigest() == enrichment["pdf_sha256"]
        content = {k: v for k, v in enrichment.items() if k != "hash"}
        assert hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest() == enrichment["hash"]
    else:
        enrichment = build_enrichment(source, pdf, document, PDF_URL)
    assert [(m["name"], m["definition"]) for m in enrichment["macros"]] == [("MethodName", "G2PO")]
    assert len(enrichment["references"]) == 64
    assert sorted({p for b in enrichment["references"] for p in b["pages"]}) == [10, 11, 12, 13, 14]
    if not document.get("source_enrichment"):
        if not apply:
            print("Read-only preview: MethodName -> G2PO; 64 references from PDF pages 10-14; zero model calls.")
            return
        apply_revision(record, document, enrichment)
    metrics = verify(enrichment)
    if export_pdf:
        export(enrichment, metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    main(args.apply, args.export)
