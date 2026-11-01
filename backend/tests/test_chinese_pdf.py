import hashlib
import json

import pytest
from fastapi import HTTPException

from app import chinese_pdf as c, fulltext, jobs, repository as repo
from app.config import get_settings
from app.reading_api import chinese_pdf_download


@pytest.mark.parametrize("source", [r"\input{secret}", r"\write18{cmd}", r"\csname input\endcsname", r"\includegraphics{../secret.pdf}", r"\url{file:///secret}", "^^5cinput{secret}", r"\begin{filecontents}{evil.tex}"])
def test_untrusted_commands_are_rejected(source):
    with pytest.raises(ValueError):
        c.validate_tex(source)


def test_masks_keep_math_numbers_references_and_table_structure():
    source = r"\caption{Score at 95.3\%} \begin{tabular}{lr} Method & $x^2$ \\ \end{tabular} \label{tab:x}"
    masked, tokens = c.mask(source)
    result = c.unmask(masked.replace("Score at", "得分为").replace("Method", "方法"), tokens)
    assert result == source.replace("Score at", "得分为").replace("Method", "方法")
    with pytest.raises(ValueError):
        c.unmask(masked.replace("⟦0⟧", ""), tokens)
    with pytest.raises(ValueError):
        c.unmask(masked + r"\input{secret}", tokens)
    equation = r"\begin{equation}A = \text{mean}(X)\end{equation}"
    assert c.mask(equation)[0] == "⟦0⟧"


def test_multiline_options_and_code_identifiers_are_not_translated():
    source = "\\begin{tcolorbox}[colback=gray!5,\ntitle={测试}]\nprint(apis.api\\_docs.show())\nUse G2PO with task\\_description."
    masked, tokens = c.mask(source)
    assert "colback" not in masked and "title=" not in masked
    assert "apis" not in masked and "task" not in masked and "G2PO" not in masked
    assert c.unmask(masked, tokens) == source


def test_layout_preserves_figures_algorithms_and_formulas():
    source = r"\begin{wrapfigure}{r}{0.3\textwidth}\includegraphics{figure/a.pdf}\caption{图示}\end{wrapfigure}" + r"\begin{algorithm}[h]\STATE 测试 $x=3$\end{algorithm}\begin{equation}x=3\label{eq:x}\end{equation}"
    result = c.layout(source, columns=2)
    assert r"\includegraphics{figure/a.pdf}" in result
    assert r"\begin{algorithm*}" in result and r"\STATE 测试 $x=3$" in result
    assert r"\fitdisplay{x=3}\label{eq:x}" in result
    assert "wrapfigure" not in result
    assert r"\setCJKmainfont{SimSun}" in c.PREAMBLE
    assert "PaperPilot" not in c.PREAMBLE


def test_failed_supplement_reuses_saved_chunk(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    repo.init_db(); jobs.init_jobs(); fulltext.init_documents()
    text = r"\caption{A caption}"
    with repo.connect() as db:
        # analysis_chunks deliberately supports independent analysis caches.
        db.execute("INSERT INTO analysis_chunks VALUES(?,?,?,?)", ("job", "caption", hashlib.sha256((c.VERSION + text).encode()).hexdigest(), json.dumps({"translation": r"\caption{图注}"})))
    from app import skills
    monkeypatch.setattr(skills, "_call", lambda *args: pytest.fail("Cache hit must not call the model"))
    assert c.supplement({"id": "job"}, "owner", {"id": "caption", "text": text, "translate": True}) == r"\caption{图注}"
    get_settings.cache_clear()


def test_download_rejects_incomplete_and_missing_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "get_job", lambda _: {"skill_id": "chinese_pdf", "status": "running"})
    with pytest.raises(HTTPException) as exc:
        chinese_pdf_download("job")
    assert exc.value.status_code == 409
    monkeypatch.setattr(jobs, "get_job", lambda _: {"skill_id": "chinese_pdf", "status": "succeeded"})
    monkeypatch.setattr(c, "directory", lambda _: tmp_path)
    with pytest.raises(HTTPException) as exc:
        chinese_pdf_download("job")
    assert exc.value.status_code == 410


def test_fragment_translation_keeps_algorithm_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    repo.init_db(); jobs.init_jobs(); fulltext.init_documents()
    record = {"id": "job", "paper": {"title": "Test", "authors": []}, "model": "test-model",
              "input": {"translation_job_id": "translation", "_provider_url": "https://example.com", "_chinese": {"glossary": {}}}}
    from app import skills
    called = []
    # Stop at the transaction after testing the exact request/response assembly.
    def call(record, owner, system, data, suffix):
        called.append(suffix)
        assert data["segments"] == {"2": "Sample actions", "4": "to"}
        return {"translations": {"2": "采样动作", "4": "至"}}, "request", {}
    monkeypatch.setattr(skills, "_call", call)
    monkeypatch.setattr(jobs, "update_progress", lambda *args: None)
    # A missing lease must never publish the model response as an artifact.
    with pytest.raises(jobs.LeaseLost):
        c.supplement(record, "owner", {"id": "algorithm", "translate": True, "text": r"\STATE Sample actions $x$ to $T$"})
    assert called == ["algorithm"]
    get_settings.cache_clear()


def test_table_terms_follow_source_cells_without_changing_values():
    source = r"\begin{tabular}{llr}Pick2 & Method & Score \\ RL Training & PPO (with critic) & 19.1 \\ \end{tabular}"
    translated = source.replace("Pick2", "拾取 2").replace("RL Training", "不应展开的论文标题 RL 训练").replace("PPO (with critic)", "PPO（带 critic）")
    result = c.normalize_table_terms(source, translated)
    assert result == source.replace("Pick2", "拾取 2").replace("RL Training", "RL 训练").replace("PPO (with critic)", "PPO（含价值网络）")
    assert c.normalize_table_terms("Prose", "正文") == "正文"


def test_layout_omits_running_headings_and_keeps_list_content():
    source = r"\section{实验}\begin{itemize}[leftmargin=15pt,itemsep=0pt]\item 第一项 $x=2$。\item 第二项\cite{test}。\end{itemize}"
    result = c.layout(source)
    assert r"\pagestyle{plain}" in c.PREAMBLE
    assert r"\section{实验}" in result
    assert "label={},leftmargin=0pt,labelsep=0pt,itemindent=2em" in result
    assert "leftmargin=15pt" not in result
    assert result.count(r"\item ") == 2
    assert r"第一项 $x=2$。" in result and r"第二项\cite{test}。" in result


def test_unnumbered_aligned_equations_fit_column_without_changing_rows():
    inner = r"x &= f(x) \\ y &= g(y)"
    source = r"\begin{align*}" + inner + r"\end{align*}"
    result = c.layout(source, columns=2)
    assert result == r"\[\fitdisplay{\begin{aligned}" + inner + r"\end{aligned}}\]"
    numbered = r"\begin{align}x &= y\label{eq:test}\end{align}"
    assert c.layout(numbered, columns=2) == numbered


def test_local_author_identity_is_protected_without_metadata_names():
    author = r"Mert Cemri$^{1*}$ \quad \textbf{Melissa Z. Pan}$^{1*}$ \\ $^1$UC Berkeley \quad *Equal Contribution"
    names = c.source_author_names(author)
    assert names == ['Mert Cemri', 'Melissa Z. Pan']
    masked,tokens = c.mask(author,names)
    assert all(name not in masked for name in names)
    assert 'UC Berkeley' in masked and c.unmask(masked,tokens)==author


def test_nested_table_column_dimensions_are_protected_and_cached_layout_repaired():
    source = r"\begin{tabular}[t]{p{2.5cm} >{\raggedright\arraybackslash}p{4cm}} Method & Score \\ A & 95.3 \\ \end{tabular}"
    masked,tokens = c.mask(source)
    assert 'cm' not in masked and 'raggedright' not in masked
    assert 'Method' in masked and 'Score' in masked
    assert c.unmask(masked,tokens)==source
    cached=source.replace('cm','厘米').replace('Method','方法').replace('Score','得分')
    restored=c.restore_table_headers(source,cached)
    assert restored==source.replace('Method','方法').replace('Score','得分')
    assert '95.3' in restored
    with pytest.raises(ValueError):c.restore_table_headers(source,cached.replace('tabular','array'))
    wide=r"\begin{tabular*}{0.9\textwidth}[b]{p{2cm}lr}"
    assert c.unmask(*c.mask(wide))==wide and c.mask(wide)[0]=='⟦0⟧'
