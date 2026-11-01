"""Evidence-backed display supplements; immutable translation inputs stay intact.

LaTeX is data, never executed. Only unambiguous zero-argument plain-text macros
are expanded. PDF references retain their original numbered text and page spans.
"""
from copy import deepcopy
import hashlib
import io
import json
import re

VERSION = "1.0.0"


def plain_macros(files: dict[str, str]) -> tuple[list[dict], list[str]]:
    definitions: dict[str, list[dict]] = {}
    declaration = re.compile(r"\\(?:newcommand|renewcommand|providecommand)\*?\s*\{\\([A-Za-z@]+)\}")
    for filename, source in files.items():
        clean = re.sub(r"(?<!\\)%[^\n]*", "", source)
        for match in declaration.finditer(clean):
            tail = clean[match.end():]
            value = None
            try:
                from .pdf_compat import args
                values, _ = args(tail, 0, 1)
                candidate = re.sub(r"\\xspace(?![A-Za-z@])", "", values[0])
                # Only discard declarative font wrappers; never evaluate nested commands.
                candidate = re.sub(r"\\(?:textbf|texttt|textit|textsf|textrm)(?![A-Za-z@])", "", candidate)
                candidate = candidate.replace('{', '').replace('}', '')
                if re.fullmatch(r"[A-Za-z0-9 .,:;()+/_-]{1,120}", candidate):
                    value = candidate
            except ValueError:
                pass
            entry = {"name": match[1], "definition": value,
                     "file": filename, "line": clean[:match.start()].count("\n") + 1}
            definitions.setdefault(match[1], []).append(entry)
        # Unsupported definitions are reported rather than executed or guessed.
        for match in re.finditer(r"\\(?:def|gdef|edef|xdef)\s*\\([A-Za-z@]+)|\\DeclareMathOperator\*?\s*\{\\([A-Za-z@]+)\}", clean):
            name = match[1] or match[2]
            definitions.setdefault(name, []).append({"name": name, "definition": None})
    safe, unsupported = [], []
    for name, entries in definitions.items():
        if len(entries) == 1 and entries[0]["definition"] is not None and re.fullmatch(r"[A-Za-z0-9 .,:;()+/_-]{1,120}", entries[0]["definition"]):
            safe.append(entries[0])
        else:
            unsupported.append(name)
    return safe, unsupported


def expand_text(text: str, macros: list[dict]) -> str:
    for macro in macros:
        pattern = r"\\" + re.escape(macro["name"]) + r"(?![A-Za-z@])(?:\{\})?"
        text = re.sub(pattern, lambda _: macro["definition"], text)
    return text


def reference_blocks(pages: list[str], pdf_url: str) -> list[dict]:
    """Require explicit References and following appendix headings and 1..N IDs."""
    started = ended = False
    entries: list[dict] = []
    for page_number, page in enumerate(pages, 1):
        lines = page.splitlines()
        for line_number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not started:
                if stripped == "References":
                    started = True
                continue
            if re.match(r"^[A-Z](?:\.\d+)?\s+[A-Z][A-Za-z ]+\s*$", stripped):
                ended = True
                break
            if not stripped or stripped == str(page_number):
                continue
            if "\ufffd" in line or any(ord(c) < 32 and c != "\t" for c in line) or "/uni000" in line:
                raise ValueError(f"参考文献第 {page_number} 页包含乱码，不猜补")
            match = re.match(r"^\[(\d+)\]\s*(?=\S)", stripped)
            if match:
                number = int(match[1])
                if number != len(entries) + 1:
                    raise ValueError("参考文献编号不连续或重复，需要人工核查")
                entries.append({"number": number, "segments": []})
            if not entries:
                raise ValueError("参考文献标题后未找到编号")
            entries[-1]["segments"].append({"page": page_number, "line": line_number, "text": line})
        if ended:
            break
    if not started or not ended or not entries:
        raise ValueError("无法可靠确定参考文献起止范围")
    output = []
    for entry in entries:
        raw = "\n".join(segment["text"] for segment in entry["segments"])
        text = re.sub(r"\s+", " ", raw).strip()
        text = re.sub(r"^(\[\d+\])\s*", r"\1 ", text)
        page_ids = sorted({segment["page"] for segment in entry["segments"]})
        page_label = "、".join(map(str, page_ids))
        output.append({"id": f"ref{entry['number']:04d}", "number": entry["number"],
                       "text": text, "raw_text": raw, "kind": "reference", "section": "参考文献（原 PDF）",
                       "location": f"原 PDF 第 {page_label} 页 · 文献 [{entry['number']}]",
                       "source_url": f"{pdf_url}#page={page_ids[0]}", "pages": page_ids,
                       "source_hash": hashlib.sha256(text.encode()).hexdigest(),
                       "segments": entry["segments"], "translatable": False,
                       "translation": text, "status": "preserved", "supplemental": True})
    return output


def build_enrichment(source_data: bytes, pdf_data: bytes, document: dict, pdf_url: str) -> dict:
    from pypdf import PdfReader
    from .fulltext import safe_members
    if hashlib.sha256(source_data).hexdigest() != document["source_hash"]:
        raise ValueError("源包与原解析版本不一致")
    macros, unsupported = plain_macros(safe_members(source_data))
    body = "\n".join(b["text"] + "\n" + b["section"] for b in document["blocks"])
    used_unsupported = [name for name in unsupported if re.search(r"\\" + re.escape(name) + r"(?![A-Za-z@])", body)]
    if used_unsupported:
        raise ValueError("正文存在尚不支持的自定义宏：" + ", ".join(used_unsupported))
    macros = [m for m in macros if re.search(r"\\" + re.escape(m["name"]) + r"(?![A-Za-z@])", body)]
    if not pdf_data.startswith(b"%PDF-"):
        raise ValueError("参考文献来源不是 PDF")
    reader = PdfReader(io.BytesIO(pdf_data))
    if reader.is_encrypted or len(reader.pages) > 150:
        raise ValueError("PDF 加密或超出页数上限")
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    references = reference_blocks(pages, pdf_url)
    result = {"version": VERSION, "base_document_hash": document["document_hash"],
              "source_sha256": document["source_hash"], "pdf_sha256": hashlib.sha256(pdf_data).hexdigest(),
              "pdf_url": pdf_url, "pdf_page_count": len(pages), "macros": macros, "references": references,
              "notes": [f"按源文件定义安全展开 {len(macros)} 个正文自定义宏；保留原始 LaTeX 和翻译检查点，未执行 TeX。",
                        f"从同版本原 PDF 补充 {len(references)} 条参考文献，保留英文原文和页码；仅合并排版空白，未补写缺失书目信息。",
                        "标准 LaTeX 公式、引用命令、表格和图像版式仍以原 PDF 为准；宏展开不等于完整 LaTeX 排版。"]}
    result["hash"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result


def display_blocks(blocks: list[dict], enrichment: dict | None) -> list[dict]:
    if not enrichment:
        return blocks
    output = []
    for original in blocks:
        block = dict(original)
        for key in ("text", "translation", "section"):
            if isinstance(block.get(key), str):
                expanded = expand_text(block[key], enrichment["macros"])
                if expanded != block[key]:
                    block[f"display_{key}"] = expanded
        output.append(block)
    output.extend(deepcopy(enrichment["references"]))
    return output


def display_document(document: dict | None) -> dict | None:
    if not document or not document.get("source_enrichment"):
        return document
    result = dict(document)
    result["blocks"] = display_blocks(document["blocks"], document["source_enrichment"])
    result["base_paragraph_count"] = document["paragraph_count"]
    result["paragraph_count"] = len(result["blocks"])
    return result
