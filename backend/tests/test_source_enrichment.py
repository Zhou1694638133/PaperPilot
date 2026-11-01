from copy import deepcopy

import pytest

from app.source_enrichment import plain_macros, expand_text, reference_blocks, display_blocks, display_document


def test_only_unambiguous_plain_macros_are_expanded():
    macros, unsupported = plain_macros({"main.tex": r"""% \newcommand{\Ignored}{NO}
\newcommand{\MethodName}{G2PO}
\newcommand{\Argument}[1]{#1}
\newcommand{\Danger}{\input{secret}}
\def\Other{value}
"""})
    assert [m["name"] for m in macros] == ["MethodName"]
    assert set(unsupported) == {"Argument", "Danger", "Other"}
    assert macros[0]["line"] == 2
    assert expand_text(r"\MethodName{} / \MethodName / \MethodNameExtra", macros) == r"G2PO / G2PO / \MethodNameExtra"


def test_duplicate_definitions_are_not_guessed():
    safe, unsupported = plain_macros({"a.tex": r"\newcommand{\M}{A}", "b.tex": r"\renewcommand{\M}{B}"})
    assert not safe and unsupported == ["M"]


def test_pdf_number_may_touch_author_name():
    refs = reference_blocks(["References\n[1]Google. Title.\n[2]OpenAI. Title.\nA Limitations"], "https://arxiv.org/pdf/test")
    assert [r["number"] for r in refs] == [1, 2]
    assert refs[0]["text"] == "[1] Google. Title."
    assert refs[0]["raw_text"] == "[1]Google. Title."


def test_references_keep_page_spans_and_exclude_footer_and_appendix():
    pages = ["Body\nReferences\n[1] Author. Title 2025.\n1", "  continuation.\n[2] Other. Article.\nA Limitations\nNot a reference\n2"]
    refs = reference_blocks(pages, "https://arxiv.org/pdf/2606.22995v2")
    assert len(refs) == 2
    assert refs[0]["pages"] == [1, 2]
    assert refs[0]["text"] == "[1] Author. Title 2025. continuation."
    assert refs[0]["segments"][1] == {"page": 2, "line": 1, "text": "  continuation."}
    assert refs[1]["translation"] == refs[1]["text"]
    assert not refs[1]["translatable"]
    assert refs[1]["source_url"].endswith("#page=2")


@pytest.mark.parametrize("text", [
    "References\n[1] A.\n[3] C.\nA Limitations",
    "References\n[1] A.\n[1] B.\nA Limitations",
    "References\n[1] A.",
    "References\n[1] A\ufffd\nA Limitations",
    "References\n[1] /uni00000013\nA Limitations",
    "Body only",
])
def test_incomplete_or_damaged_references_fail_closed(text):
    with pytest.raises(ValueError):
        reference_blocks([text], "https://arxiv.org/pdf/test")


def test_display_enrichment_never_rewrites_checkpoint_or_document_hash():
    blocks = [{"id": "p0001", "text": r"\MethodName{} method", "translation": r"\MethodName{} 方法",
               "section": r"Training \MethodName{}", "source_hash": "original"}]
    original = deepcopy(blocks)
    enrichment = {"macros": [{"name": "MethodName", "definition": "G2PO"}], "references": [{"id": "ref0001", "text": "[1] A"}]}
    document = {"blocks": blocks, "paragraph_count": 1, "document_hash": "base", "source_enrichment": enrichment}
    display = display_document(document)
    assert blocks == original and len(document["blocks"]) == 1
    assert display["document_hash"] == "base" and display["base_paragraph_count"] == 1
    assert display["paragraph_count"] == 2
    assert display["blocks"][0]["text"] == original[0]["text"]
    assert display["blocks"][0]["display_translation"] == "G2PO 方法"
    assert display["blocks"][0]["source_hash"] == "original"
    assert display_blocks(blocks, None) == blocks


@pytest.mark.parametrize("stale", [False, True])
def test_alignment_api_separates_supplements_from_checkpoints(monkeypatch, stale):
    import sqlite3
    from fastapi import HTTPException
    from app import reading_api
    block = {"id": "p0001", "text": r"\MethodName{}", "section": "Intro", "location": "main.tex:1", "translatable": True}
    document = {"blocks": [block], "document_hash": "base"}
    enrichment = {"base_document_hash": "other" if stale else "base",
                  "macros": [{"name": "MethodName", "definition": "G2PO"}],
                  "references": [{"id": "ref0001", "text": "[1] A", "translation": "[1] A", "status": "preserved", "supplemental": True}]}
    record = {"skill_id": "paper_translator", "input": {"_document": document, "glossary": {}}}
    monkeypatch.setattr(reading_api.jobs, "job_record", lambda _: record)
    monkeypatch.setattr(reading_api.jobs, "get_job", lambda _: {"result": {"source_enrichment": enrichment}})
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        db.execute("CREATE TABLE translation_chunks (job_id,paragraph_id,source_hash,source,translation)")
        db.execute("INSERT INTO translation_chunks VALUES (?,?,?,?,?)", ("job", "p0001", "hash", block["text"], r"\MethodName{} 方法"))
        db.commit()
        monkeypatch.setattr(reading_api.repo, "connect", lambda: db)
        if stale:
            with pytest.raises(HTTPException) as exc:
                reading_api.alignment("job")
            assert exc.value.status_code == 409
        else:
            response = reading_api.alignment("job")
            assert response["checkpoint_total"] == response["checkpoint_completed"] == 1
            assert response["total"] == response["completed"] == 2
            assert response["supplemental_count"] == 1
            assert response["blocks"][0]["display_translation"] == "G2PO 方法"
            assert db.execute("SELECT translation FROM translation_chunks").fetchone()[0] == r"\MethodName{} 方法"
    finally:
        db.close()
