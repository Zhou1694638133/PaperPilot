import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import fulltext, jobs, llm, repository as repo
from app.config import get_settings
from app.main import app
from app.pdf_export import render_pdf
from app.research import publication, VERIFIED
from app.worker import process_one


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    from app import conference_discovery
    monkeypatch.setattr(conference_discovery, 'official_index', lambda days: ([], []))
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def paper(identifier="2503.14476"):
    return {"id": identifier, "version": 1, "title": VERIFIED["2503.14476"]["title"],
            "abstract": "We train agents with reinforcement learning and evaluate 3 tasks.",
            "published": "2025-03-18T00:00:00Z", "updated": "2026-01-01T00:00:00Z",
            "authors": ["A Author"], "categories": ["cs.AI"], "url": f"https://arxiv.org/abs/{identifier}v1",
            "pdf_url": f"https://arxiv.org/pdf/{identifier}v1", "source": "arXiv", "mode": "live",
            "fetched_at": "2026-10-25T00:00:00Z"}


def test_heading_labels_nested_macros_and_same_line_text():
    source = archive({"main.tex": r"""\documentclass{article}
\begin{document}
\section{Training with \methodname{}}\label{sec:train} Same-line prose stays available.

Another paragraph about policy training and evaluation.
\end{document}
"""})
    blocks, _ = fulltext.parse_latex(source)
    assert blocks[0]["section"] == r"Training with \methodname{}"
    assert blocks[0]["text"] == "Same-line prose stays available."
    assert "label" not in blocks[0]["section"]


def archive(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, value in files.items():
            data = value.encode()
            item = tarfile.TarInfo(name); item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    return out.getvalue()


def latex():
    return archive({"main.tex": r"""\documentclass{article}
\begin{document}
\section{Introduction}
We train agents with reinforcement learning. This is a faithful source paragraph about policies, environment feedback, evaluation and evidence.

\input{sections/method}
\begin{table}
Value & 3.14 \\
\end{table}
\begin{equation}
x = -3.2e-5
\end{equation}
\end{document}
""", "sections/method.tex": "The method assigns rewards to trajectories. We evaluate 3 tasks and preserve $x^2$ and \\cite{smith}. This is the second paragraph, with explicit source locations and independently checkable quotations.",
                   "main.bbl": r"\begin{thebibliography}{9}" + "\n" + r"\bibitem{smith} Smith. Reference. 2025." + "\n" + r"\end{thebibliography}"})


def stored_document():
    p = paper(); repo.upsert_paper(p)
    blocks, warnings = fulltext.parse_latex(latex())
    document = fulltext.make_document(p, blocks, warnings, "latex", p["url"], latex())
    fulltext.save_document(p, document)
    return p, document


def configured(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-only")
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    get_settings.cache_clear()


def submit(client, skill="paper_translator", **kwargs):
    return client.post("/api/jobs", json={"skill_id": skill, "paper_id": "2503.14476", "scope": "fulltext", "acknowledge_partial": True, **kwargs})


@pytest.mark.parametrize("name", ["../escape.tex", "/absolute.tex", "C:/bad.tex"])
def test_archive_traversal_rejected(name):
    with pytest.raises(ValueError, match="不安全"):
        fulltext.safe_members(archive({name: "danger"}))


def test_archive_symlink_rejected():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        item = tarfile.TarInfo("link.tex"); item.type = tarfile.SYMTYPE; item.linkname = "../secret"
        tar.addfile(item)
    with pytest.raises(ValueError, match="不安全"):
        fulltext.safe_members(out.getvalue())


def test_source_includes_locations_and_protected_references():
    blocks, warnings = fulltext.parse_latex(latex())
    assert any(b["location"].startswith("sections/method.tex:") for b in blocks)
    assert any(b["kind"] == "reference" and "Smith" in b["text"] and not b["translatable"] for b in blocks)
    assert any("3.14" in b["text"] and b["kind"] == "protected" for b in blocks)
    assert any("x = -3.2e-5" in b["text"] and not b["translatable"] for b in blocks)
    assert any("自定义命令" in warning for warning in warnings)


def test_missing_source_and_ambiguous_root_are_not_invented():
    data = archive({"main.tex": r"\documentclass{article}"+"\n"+r"\begin{document}"+"\n"+r"\input{missing}"+"\n"+r"\end{document}"})
    _, warnings = fulltext.parse_latex(data)
    assert any("缺少引用文件" in warning for warning in warnings)
    with pytest.raises(ValueError, match="唯一"):
        fulltext.parse_latex(archive({"a.tex": r"\documentclass{a}", "b.tex": r"\documentclass{b}"}))


def test_nested_inputs_resolve_from_main_working_directory():
    data = archive({"main.tex": "\\documentclass{article}\n\\begin{document}\n\\input{sections/body}\n\\end{document}",
                    "sections/body.tex": "\\input{tables/results}", "tables/results.tex": "Exact root-relative results."})
    blocks, warnings = fulltext.parse_latex(data)
    assert any("Exact root-relative results." in b["text"] for b in blocks)
    assert not any("缺少引用" in warning for warning in warnings)


def test_block_split_preserves_math_and_all_words():
    source = "word " * 1500 + "$x = -12.5$ " + "tail " * 800
    chunks = fulltext.split_prose(source)
    assert " ".join(" ".join(chunks).split()) == " ".join(source.split())
    assert any("$x = -12.5$" in part for part in chunks)


def test_layout_commands_and_display_math_do_not_call_translator():
    data = ("\\documentclass{article}\n\\begin{document}\n\\vspace{-20pt}\n\n$$\nx = 3\n\n+y\n$$\n\\end{document}").encode()
    blocks, _ = fulltext.parse_latex(data)
    assert len(blocks) == 2
    assert all(not b["translatable"] for b in blocks)
    assert "x = 3\n\n+y" in blocks[1]["text"]


def test_archive_expansion_bound(monkeypatch):
    monkeypatch.setattr(fulltext, "MAX_EXPANDED", 100)
    with pytest.raises(ValueError, match="解压大小"):
        fulltext.safe_members(archive({"a.tex": "x" * 200}))


def test_no_key_fetch_runs_through_worker_and_model_degrades(client, monkeypatch):
    repo.upsert_paper(paper())
    monkeypatch.setattr(fulltext, "download", lambda url, check: latex())
    created = submit(client, "fulltext_fetch")
    assert created.status_code == 201
    assert process_one("test-fetch")
    result = client.get(f"/api/jobs/{created.json()['id']}").json()
    assert result["status"] == "succeeded", result
    assert client.get("/api/fulltext?paper_id=2503.14476").json()["document"]["method"] == "latex"
    assert submit(client).status_code == 503
    assert submit(client, "fulltext_fetch", mode="demo").status_code == 409


def test_glossary_version_conflict_and_job_snapshot(client, monkeypatch):
    stored_document(); configured(monkeypatch)
    initial = client.get("/api/glossary").json()
    first = submit(client).json()
    update = {"version": initial["version"], "terms": {"agent": "自主智能体"}}
    assert client.put("/api/glossary", json=update).status_code == 200
    assert client.put("/api/glossary", json=update).status_code == 409
    second = submit(client).json()
    assert first["id"] != second["id"]
    assert jobs.job_record(first["id"])["input"]["glossary_version"] == initial["version"]
    assert jobs.job_record(second["id"])["input"]["glossary"] == {"agent": "自主智能体"}


def test_review_acknowledgement_and_version_invalidate(client, monkeypatch):
    p, _ = stored_document(); configured(monkeypatch)
    assert submit(client, acknowledge_partial=False).status_code == 409
    old = submit(client).json()
    p["version"] = 2; repo.upsert_paper(p)
    assert submit(client).status_code == 409
    assert client.get("/api/fulltext?paper_id=2503.14476").json()["document"] is None
    assert jobs.job_record(old["id"])["paper"]["version"] == 1


def test_translation_partial_checkpoints_resume_only_missing(client, monkeypatch):
    _, document = stored_document(); configured(monkeypatch)
    calls = []
    fail = [True]
    def model(system, payload, request):
        calls.append(payload["paragraph_id"])
        if payload["paragraph_id"] == document["blocks"][1]["id"] and fail[0]:
            raise llm.ModelError("测试失败")
        if "segments" in payload:
            return {"segments": [{"id": item["id"], "translation": "中文译文 " + item["text"]}
                                 for item in payload["segments"]]}, "test-request", {}
        return {"translation": "中文译文 " + payload["paragraph"]}, "test-request", {}
    monkeypatch.setattr(llm, "request_json", model)
    job = submit(client).json()
    assert process_one("translator-1")
    assert jobs.get_job(job["id"])["status"] == "failed"
    partial = client.get(f"/api/jobs/{job['id']}/alignment").json()
    assert partial["completed"] == 1
    assert partial["blocks"][1]["translation"] is None
    fail[0] = False
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 200
    assert process_one("translator-2")
    result = jobs.get_job(job["id"])
    assert result["status"] == "succeeded", result
    assert calls.count(document["blocks"][0]["id"]) == 1
    aligned = client.get(f"/api/jobs/{job['id']}/alignment").json()
    assert aligned["completed"] == len(document["blocks"])
    for block in aligned["blocks"]:
        if not block["translatable"]:
            assert block["translation"] == block["text"]
            assert block["status"] == "preserved"


@pytest.mark.parametrize("use_id", [False, True])
def test_fulltext_reader_exact_quote_and_location(client, monkeypatch, use_id):
    _, document = stored_document(); configured(monkeypatch)
    quote = document["blocks"][0]["text"]
    def model(system, payload, request):
        result = {key: {"text": "作者报告此方法。", "evidence": [document["blocks"][0]["id"] if use_id else quote]} for key in ["research_question", "method", "experiments", "limitations", "research_relation"]}
        return {**result, "unknowns": ["未独立复现"]}, "reader-request", {}
    monkeypatch.setattr(llm, "request_json", model)
    created = submit(client, "paper_reader").json()
    process_one("reader")
    job = jobs.get_job(created["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["evidence_locations"][0]["paragraph_id"] == "p0001"
    assert job["result"]["coverage"] == "fulltext"


def test_numeric_integrity_accepts_chinese_adjacent_digits():
    import re
    from collections import Counter
    from app.skills import NUMERIC_PATTERN, protect, restore
    source = "The improvement is 22.2% using a 1-step update with Qwen2.5."
    protected, values = protect(source)
    assert "__PP_PROTECTED_0000__" in protected
    translated = restore("使用__PP_PROTECTED_0001__步更新后提升__PP_PROTECTED_0000__，模型为Qwen2.5。", values)
    assert Counter(re.findall(NUMERIC_PATTERN, source)) == Counter(re.findall(NUMERIC_PATTERN, translated))
    assert Counter(re.findall(NUMERIC_PATTERN, source)) != Counter(re.findall(NUMERIC_PATTERN, translated + "增加7项"))


def test_fulltext_reader_hallucinated_quote_rejected(client, monkeypatch):
    stored_document(); configured(monkeypatch)
    output = {key: {"text": "虚构结果", "evidence": ["INVENTED QUOTE"]} for key in ["research_question", "method", "experiments", "limitations", "research_relation"]}
    monkeypatch.setattr(llm, "request_json", lambda *args: ({**output, "unknowns": []}, "r", {}))
    job = submit(client, "paper_reader").json(); process_one("reader")
    assert jobs.get_job(job["id"])["status"] == "failed"
    assert jobs.get_job(job["id"])["result"] is None


def test_comparison_unknowns_and_pdf_chinese(client):
    repo.upsert_paper(paper()); other = paper("2503.00002"); other["title"] = "Another paper"; repo.upsert_paper(other)
    result = client.post("/api/compare", json={"ids": ["2503.14476", "2503.00002"]}).json()
    assert all(p["reading"] is None for p in result["papers"])
    assert result["papers"][0]["publication"]["status"] == "verified"
    response = client.get("/api/reports/pdf?ids=2503.14476,2503.00002")
    assert response.status_code == 200 and response.content.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(response.content))
    assert "论文对比" in "".join(page.extract_text() for page in reader.pages)
    assert client.post("/api/compare", json={"ids": ["2503.14476", "2503.14476"]}).status_code == 422


def test_pdf_missing_page_warnings():
    from reportlab.pdfgen.canvas import Canvas
    stream = io.BytesIO(); canvas = Canvas(stream)
    canvas.drawString(40, 700, "This page contains enough readable text to verify extraction of actual PDF content.")
    canvas.showPage(); canvas.showPage(); canvas.save()
    blocks, warnings = fulltext.parse_pdf(stream.getvalue())
    assert blocks[0]["page"] == 1
    assert any("第 2 页" in warning for warning in warnings)


def test_download_forbids_local_and_redirect_host(client, monkeypatch):
    with pytest.raises(ValueError, match="允许列表"):
        fulltext.download("http://127.0.0.1/private")
    real = httpx.Client
    requests = []
    def route(request):
        requests.append(str(request.url)); return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(route), **kw))
    with pytest.raises(ValueError, match="允许列表"):
        fulltext.download("https://arxiv.org/src/2503.14476v1")
    assert len(requests) == 1


def test_two_year_default_publication_evidence_and_research_modules(client):
    assert client.get("/api/profile").json()["days"] == 730
    assert client.get("/api/papers?mode=demo&query=AutoGen").json()["papers"] == []
    assert client.get("/api/papers?mode=demo&query=AutoGen&days=0").json()["papers"]
    modules = client.get("/api/research-modules").json()
    assert {item["id"] for item in modules["tracks"]} == {"agent_rl", "post_training"}
    fake = paper(); fake["title"] = "Wrong paper title"
    assert publication(fake)["status"] == "unverified"
    fake = paper(); fake["mode"] = "demo"
    assert publication(fake)["status"] == "unverified"
    assert "reinforcement learning" in client.post("/api/query", json={"text": "Agent 强化学习"}).json()["keywords"]


def test_fulltext_rate_limit_does_not_immediately_fallback(client, monkeypatch):
    repo.upsert_paper(paper())
    calls = []
    def limited(url, check):
        calls.append(url)
        raise fulltext.FulltextRateLimited("等待 30 秒后重试")
    monkeypatch.setattr(fulltext, "download", limited)
    job = submit(client, "fulltext_fetch").json(); process_one("limited")
    assert jobs.get_job(job["id"])["status"] == "failed"
    assert len(calls) == 1


def test_preflight_endpoint_without_model(client):
    stored_document()
    response=client.get('/api/translation/preflight?paper_id=2503.14476')
    assert response.status_code==200
    report=response.json()
    assert report['total']>0
    assert report['counts']['prose']>0
    assert report['counts']['protected']>0
    assert client.get('/api/translation/preflight?paper_id=missing').status_code==404
