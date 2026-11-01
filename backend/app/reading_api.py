"""Full-text, glossary, aligned checkpoints, comparison and PDF report API."""
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator

from . import repository as repo, jobs, fulltext
from .research import catalog, publication
from .exports import translation_filename, attachment_disposition
from .source_enrichment import display_blocks, display_document

router = APIRouter(prefix="/api", tags=["reading"])


@router.get("/jobs/{job_id}/chinese.pdf")
def chinese_pdf_download(job_id: str):
    from .chinese_pdf import directory
    job = jobs.get_job(job_id)
    if job["skill_id"] != "chinese_pdf" or job["status"] != "succeeded":
        raise HTTPException(409, "中文版 PDF 尚未完成，请查看排版任务状态")
    path = directory(job_id) / "paper.pdf"
    if not path.is_file():
        raise HTTPException(410, "排版产物不存在，请重新生成")
    filename = translation_filename(jobs.job_record(job_id)["paper"]["title"], "pdf")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@router.get("/jobs/{job_id}/chinese-text.pdf")
def chinese_text_pdf_download(job_id: str):
    """PDF text fallback: saved translations only, never call a model or alter checkpoints."""
    from .pdf_export import render_pdf
    job = jobs.get_job(job_id)
    if job['skill_id'] != 'paper_translator' or job['status'] != 'succeeded' or not job.get('result') or job['result'].get('partial'):
        raise HTTPException(409, '请先完成翻译，再下载中文版文字 PDF')
    record = jobs.job_record(job_id)
    if record['input'].get('_document', {}).get('method') == 'latex':
        raise HTTPException(409, '该论文已有结构化源文件，请使用中文版排版功能保留图表和公式')
    aligned = alignment(job_id)
    if not aligned['blocks'] or aligned['completed'] != aligned['total']:
        raise HTTPException(409, '译文检查点不完整，请先恢复未完成部分')
    paragraphs = []
    for block in aligned['blocks']:
        text = block.get('translation')
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(409, '译文检查点缺失，不能生成完整中文版')
        paragraphs.append(text)
    title = record['paper']['title']
    # No task diagnostics or duplicated English body in the downloaded paper.
    markdown = '# ' + title + '\n\n' + '\n\n'.join(paragraphs)
    try:
        body = render_pdf(markdown, profile='bilingual', title=title)
    except ImportError:
        raise HTTPException(503, 'PDF 依赖未安装，请运行 uv sync --locked') from None
    return Response(body, media_type='application/pdf', headers={
        'Content-Disposition': attachment_disposition(translation_filename(title, 'pdf'))})


@router.get("/jobs/{job_id}/analysis.pdf")
def analysis_pdf_download(job_id: str):
    from .chinese_pdf import directory
    from .paper_analysis import filename
    job = jobs.get_job(job_id)
    if job["skill_id"] != "analysis_pdf" or job["status"] != "succeeded":
        raise HTTPException(409, "总体分析 PDF 尚未完成，请先运行排版任务")
    path = directory(job_id) / "paper.pdf"
    if not path.is_file():
        raise HTTPException(410, "总体分析 PDF 文件不存在")
    return FileResponse(path, media_type="application/pdf", filename=filename(jobs.job_record(job_id)["paper"]["title"], "pdf"))


class GlossaryInput(BaseModel):
    version: int = Field(ge=1)
    terms: dict[str, str] = Field(max_length=100)

    @field_validator("terms")
    @classmethod
    def validate_terms(cls, value):
        if any(not k.strip() or not v.strip() or len(k) > 120 or len(v) > 240 for k, v in value.items()):
            raise ValueError("术语必须非空；英文不超过 120、中文不超过 240 字符")
        if len({k.strip().casefold() for k in value}) != len(value):
            raise ValueError("术语重复（不区分大小写）")
        return {k.strip(): v.strip() for k, v in value.items()}


class CompareInput(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=4)
    mode: Literal["live", "demo"] = "live"


def require_paper(paper_id: str, mode: str) -> dict:
    paper = repo.get_paper(paper_id, mode)
    if paper is None:
        raise HTTPException(404, "论文不存在，请先检索。")
    return paper


def latest_reading(paper: dict, document: dict | None) -> dict | None:
    with repo.connect() as db:
        rows = db.execute("SELECT result_json,paper_json FROM jobs WHERE paper_id=? AND mode=? AND skill_id='paper_reader' AND status='succeeded' ORDER BY updated_at DESC",
                          (paper["id"], paper["mode"])).fetchall()
    for row in rows:
        snapshot = json.loads(row["paper_json"])
        result = json.loads(row["result_json"])
        if snapshot["version"] == paper["version"] and (result["coverage"] == "abstract" or document and result.get("source_hash") == document["document_hash"]):
            return result
    return None


@router.get("/research-modules")
def modules():
    return catalog()


@router.get("/glossary")
def get_glossary():
    return fulltext.glossary_get()


@router.put("/glossary")
def put_glossary(body: GlossaryInput):
    return fulltext.glossary_save(body.terms, body.version)


@router.get("/fulltext")
def get_fulltext(paper_id: str, mode: Literal["live", "demo"] = "live"):
    paper = require_paper(paper_id, mode)
    document = fulltext.get_document(paper)
    return {"document": display_document(document), "reading": latest_reading(paper, document)}


@router.get("/jobs/{job_id}/alignment")
def alignment(job_id: str):
    record = jobs.job_record(job_id)
    if record["skill_id"] != "paper_translator":
        raise HTTPException(422, "该任务不是翻译任务")
    document = record["input"].get("_document")
    if document:
        blocks = document["blocks"]
    else:
        import re
        blocks = [{"id": f"abstract-p{i + 1:03d}", "text": part.strip(), "section": "摘要", "location": f"摘要段落 {i + 1}", "translatable": True}
                  for i, part in enumerate(re.split(r"\n\s*\n", record["paper"]["abstract"])) if part.strip()]
    with repo.connect() as db:
        completed = {r["paragraph_id"]: dict(r) for r in db.execute("SELECT paragraph_id,source_hash,source,translation FROM translation_chunks WHERE job_id=?", (job_id,))}
    from .translation_policy import preflight
    report = preflight(blocks)
    kinds = {item['paragraph_id']: item['kind'] for item in report['items']}
    state = (record.get('checkpoint') or {}).get('translation_state', {})
    failures = {item['paragraph_id']: item for item in state.get('failures', [])}
    output = []
    for block in blocks:
        cached = completed.get(block["id"])
        if cached and cached["source"] != block["text"]:
            cached = None
        translatable = kinds[block['id']] == 'prose'
        output.append({**block, "classification": kinds[block['id']], "failure": failures.get(block['id']) if not cached else None,
                       "translatable": translatable, "translation": cached["translation"] if cached else None,
                       "status": "preserved" if cached and not translatable else "completed" if cached else "failed" if block["id"] in failures else "pending"})
    checkpoint_completed = sum(b["status"] in {"preserved", "completed"} for b in output)
    checkpoint_total = len(output)
    job = jobs.get_job(job_id)
    enrichment = (job.get("result") or {}).get("source_enrichment")
    if enrichment and (not document or enrichment["base_document_hash"] != document["document_hash"]):
        raise HTTPException(409, "补充来源与任务原文版本不匹配")
    output = display_blocks(output, enrichment)
    return {"job": job, "preflight": report, "failures": list(failures.values()), "blocks": output, "completed": sum(b["status"] in {"preserved", "completed"} for b in output),
            "checkpoint_completed": checkpoint_completed, "checkpoint_total": checkpoint_total,
            "supplemental_count": len(output) - checkpoint_total,
            "total": len(output), "glossary": record["input"].get("glossary", {}), "glossary_version": record["input"].get("glossary_version", 1),
            "document_hash": document["document_hash"] if document else None}


def build_comparison(body: CompareInput) -> dict:
    if len(set(body.ids)) != len(body.ids):
        raise HTTPException(422, "请选择 2–4 篇不同的论文")
    entries, markdown = [], "# PaperPilot 论文对比\n\n仅并列已有证据，不跨不同基准推断性能优劣。未知信息保持未知。\n\n"
    for identifier in body.ids:
        paper = require_paper(identifier, body.mode)
        document = fulltext.get_document(paper)
        reading = latest_reading(paper, document)
        entry = {"id": paper["id"], "title": paper["title"], "url": paper["url"], "published": paper["published"],
                 "version": paper["version"], "publication": publication(paper),
                 "coverage": reading["coverage"] if reading else "metadata_and_abstract", "reading": reading,
                 "abstract": paper["abstract"], "fulltext_status": document["status"] if document else "not_loaded"}
        entries.append(entry)
        coverage = {"abstract": "摘要精读", "fulltext": "已解析全文精读", "metadata_and_abstract": "元数据与摘要"}[entry["coverage"]]
        markdown += f"## {paper['title']}\n\n来源：{paper['url']}\n\n首次公开：{paper['published']}；版本 v{paper['version']}；对比覆盖：{coverage}\n\n"
        if reading:
            for key, heading in [("research_question", "研究问题"), ("method", "方法"), ("experiments", "实验"), ("limitations", "局限")]:
                markdown += f"### {heading}\n\n{reading[key]['text']}\n\n" + "\n\n".join(f"> {quote}" for quote in reading[key]["evidence"]) + "\n\n"
        else:
            markdown += f"摘要原文：{paper['abstract']}\n\n研究问题/方法/实验/局限：尚未生成精读结果；不从标题猜测。\n\n"
    return {"papers": entries, "markdown": markdown, "method": "evidence-side-by-side", "warning": "模型未配置时仍可对比真实元数据与摘要；实验优劣不作自动判定。"}


@router.post("/compare")
def compare(body: CompareInput):
    return build_comparison(body)


@router.get("/reports/pdf")
def report_pdf(paper_id: str = "", job_id: str = "", ids: str = Query("", max_length=500), mode: Literal["live", "demo"] = "live",
               scope: Literal["paper", "fulltext"] = "paper"):
    from .pdf_export import render_pdf
    from .exports import export_markdown
    download_name = "paperpilot-report.pdf"
    pdf_profile = "report"
    pdf_title = None
    if sum(bool(value) for value in [paper_id, job_id, ids]) != 1:
        raise HTTPException(422, "请只选择论文、任务或对比集合之一。")
    if ids:
        identifiers = ids.split(",")
        if not 2 <= len(identifiers) <= 4:
            raise HTTPException(422, "对比导出需要 2–4 篇论文")
        markdown = build_comparison(CompareInput(ids=identifiers, mode=mode))["markdown"]
    elif job_id:
        job = jobs.get_job(job_id)
        if job["skill_id"] == "analysis_pdf":
            return analysis_pdf_download(job_id)
        if job["skill_id"] == "paper_analysis":
            raise HTTPException(409, "请先生成总体分析 PDF；按原文栏式排版后可下载")
        if job["skill_id"] == "chinese_pdf":
            return chinese_pdf_download(job_id)
        if not job["result"]:
            raise HTTPException(409, "任务尚未生成完整产物；请在双语页查看已完成段落。")
        if job["skill_id"] == "paper_translator" and job["result"].get("partial"):
            raise HTTPException(409, "译文尚有未完成块，请完成后再下载完整 PDF")
        markdown = job["result"]["markdown"]
        if job["skill_id"] == "paper_translator":
            record = jobs.job_record(job_id)
            if record["input"].get("_document", {}).get("method") == "latex":
                from .bilingual_pdf import export as export_bilingual
                try:
                    return export_bilingual(job_id)
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from None
            # Export the paper content only; task diagnostics remain in the UI/Markdown artifact.
            if job['result'].get('alignment'):
                markdown = '# ' + record['paper']['title'] + '\n\n'
                for block in job['result']['alignment']:
                    markdown += '### 原文\n\n' + block['source'] + '\n\n### 译文\n\n' + block['translation'] + '\n\n'
            pdf_profile = "bilingual"
            pdf_title = record["paper"]["title"]
            download_name = translation_filename(pdf_title, "pdf")
    else:
        paper = require_paper(paper_id, mode)
        markdown = export_markdown([paper])
        if scope == "fulltext":
            document = fulltext.get_document(paper)
            if not document:
                raise HTTPException(409, "请先读取全文")
            document = display_document(document)
            markdown += "\n\n# 已解析全文\n\n" + document["note"] + "\n\n" + "\n".join(document["warnings"])
            if document.get("source_enrichment"):
                markdown += "\n\n" + "\n".join(document["source_enrichment"]["notes"])
            for block in document["blocks"]:
                markdown += f"\n\n## {block['id']} · {block.get('display_section', block['section'])}\n\n位置：{block['location']}\n\n{block.get('display_text', block['text'])}"
        reading = latest_reading(paper, fulltext.get_document(paper))
        if reading:
            markdown += "\n\n" + reading["markdown"]
    try:
        body = render_pdf(markdown, profile=pdf_profile, title=pdf_title)
    except ImportError:
        raise HTTPException(503, "PDF 依赖未安装，请运行 uv sync --locked") from None
    return Response(body, media_type="application/pdf", headers={"Content-Disposition": attachment_disposition(download_name)})


@router.get('/translation/preflight')
def translation_preflight(paper_id: str, mode: Literal['live', 'demo'] = 'live'):
    from .translation_policy import preflight
    paper = require_paper(paper_id, mode)
    document = fulltext.get_document(paper)
    if not document:
        raise HTTPException(409, '请先读取全文，再进行翻译预检')
    return preflight(document['blocks'])
