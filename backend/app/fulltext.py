"""Bounded source-first acquisition. Source code is read as data, never executed."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import posixpath
import re
import tarfile
import time
from uuid import uuid4
from urllib.parse import urlparse, urljoin

import httpx

from . import repository as repo
from .config import get_settings
from .sources import canonical_id, acquire_source_lock, release_source_lock

PARSER_VERSION = "2.0.4"
MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_EXPANDED = 60 * 1024 * 1024
MAX_BLOCK = 6000


class FulltextRateLimited(ValueError):
    pass


def init_documents() -> None:
    with repo.connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            mode TEXT NOT NULL,paper_id TEXT NOT NULL,version INTEGER NOT NULL,
            parser_version TEXT NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(mode,paper_id,version,parser_version));
        CREATE TABLE IF NOT EXISTS glossary (
            id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS analysis_chunks (
            job_id TEXT NOT NULL,chunk_id TEXT NOT NULL,source_hash TEXT NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(job_id,chunk_id));
        CREATE TABLE IF NOT EXISTS fulltext_assets (
            url TEXT PRIMARY KEY,content BLOB NOT NULL,fetched_at TEXT NOT NULL);
        """)
        db.execute("INSERT OR IGNORE INTO schema_version VALUES(2,?)", (repo.utc_now(),))


def get_document(paper: dict) -> dict | None:
    init_documents()
    with repo.connect() as db:
        row = db.execute("SELECT payload FROM documents WHERE mode=? AND paper_id=? AND version=? AND parser_version=?",
                         (paper["mode"], paper["id"], paper["version"], PARSER_VERSION)).fetchone()
    return json.loads(row["payload"]) if row else None


def save_document(paper: dict, document: dict) -> None:
    init_documents()
    with repo.connect() as db:
        db.execute("INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)",
                   (paper["mode"], paper["id"], paper["version"], PARSER_VERSION, json.dumps(document, ensure_ascii=False)))


def cached_asset(url: str) -> bytes | None:
    with repo.connect() as db:
        row = db.execute("SELECT content FROM fulltext_assets WHERE url=?", (url,)).fetchone()
    return bytes(row["content"]) if row else None


def save_asset(url: str, data: bytes) -> None:
    with repo.connect() as db:
        db.execute("INSERT OR REPLACE INTO fulltext_assets VALUES(?,?,?)", (url, data, repo.utc_now()))


def glossary_get() -> dict:
    from .skills import DEFAULT_GLOSSARY
    init_documents()
    with repo.connect() as db:
        row = db.execute("SELECT * FROM glossary WHERE id=1").fetchone()
    return {"version": row["version"] if row else 1, "terms": json.loads(row["payload"]) if row else DEFAULT_GLOSSARY,
            "updated_at": row["updated_at"] if row else None}


def glossary_save(terms: dict[str, str], expected_version: int) -> dict:
    from fastapi import HTTPException
    init_documents()
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT version FROM glossary WHERE id=1").fetchone()
        version = row["version"] if row else 1
        if version != expected_version:
            raise HTTPException(409, "术语表已被其他页面修改，请重新加载后保存。")
        db.execute("INSERT OR REPLACE INTO glossary VALUES(1,?,?,?)",
                   (version + 1, json.dumps(terms, ensure_ascii=False), repo.utc_now()))
    return glossary_get()


def download(url: str, check_cancel=lambda: None) -> bytes:
    """Only fixed arXiv HTTPS hosts; each redirect is checked before requesting it."""
    settings = get_settings()
    for _ in range(4):
        parts = urlparse(url)
        if parts.scheme != "https" or parts.hostname not in {"arxiv.org", "export.arxiv.org"} or parts.port not in {None, 443} or parts.username:
            raise ValueError("全文下载来源不在 arXiv HTTPS 允许列表中")
        check_cancel()
        owner = str(uuid4())
        acquire_source_lock(owner)
        with repo.connect() as db:
            db.execute("UPDATE source_locks SET lease_until=? WHERE name='arxiv' AND owner=?",
                       (time.time() + settings.fulltext_timeout + 10, owner))
        started = time.monotonic()
        last_check = started
        try:
            with httpx.Client(timeout=settings.fulltext_timeout, follow_redirects=False,
                              headers={"User-Agent": "PaperPilot/0.2 local research reader"}) as client:
                with client.stream("GET", url) as response:
                    if response.status_code == 429:
                        header = response.headers.get("Retry-After", "30")
                        delay = min(300, max(3, int(header) if header.isdigit() else 30))
                        with repo.connect() as db:
                            db.execute("UPDATE source_locks SET next_allowed=MAX(next_allowed,?) WHERE name='arxiv'", (time.time() + delay,))
                        raise FulltextRateLimited(f"arXiv 全文请求被限流，请至少等待 {delay} 秒后重试；未立即请求 PDF。")
                    if response.status_code in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise ValueError(f"全文来源返回 HTTP {response.status_code}")
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        if time.monotonic() - last_check > 1:
                            check_cancel()
                            last_check = time.monotonic()
                        if len(content) + len(chunk) > MAX_DOWNLOAD:
                            raise ValueError("全文文件超过 20 MiB 下载上限")
                        if time.monotonic() - started > settings.fulltext_timeout:
                            raise ValueError("全文下载超过总时间上限")
                        content.extend(chunk)
                    return bytes(content)
        except httpx.RequestError:
            raise ValueError("无法连接全文来源或下载超时") from None
        finally:
            release_source_lock(owner)
    raise ValueError("全文来源重定向次数过多")


def safe_members(data: bytes) -> dict[str, str]:
    if data.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(MAX_EXPANDED + 1)
        except (OSError, EOFError):
            raise ValueError("源文件压缩包损坏") from None
    if len(data) > MAX_EXPANDED:
        raise ValueError("源文件解压大小超过上限")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    except tarfile.ReadError:
        if b"\\documentclass" not in data or len(data) > 5 * 1024 * 1024:
            raise ValueError("未取得可识别的 LaTeX 源文件") from None
        return {"main.tex": data.decode("utf-8", errors="replace")}
    files, total = {}, 0
    with archive:
        for index, member in enumerate(archive):
            name = member.name.replace("\\", "/")
            if index >= 1000 or member.size > 20 * 1024 * 1024:
                raise ValueError("源文件数量或单文件大小超过上限")
            if name.startswith("/") or ".." in name.split("/") or ":" in name or member.issym() or member.islnk():
                raise ValueError("源压缩包含不安全路径或链接")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("源压缩包含不支持的文件类型")
            total += member.size
            if total > MAX_EXPANDED:
                raise ValueError("源文件解压总大小超过上限")
            if name.endswith((".tex", ".bbl")):
                raw = archive.extractfile(member)
                if raw:
                    files[posixpath.normpath(name)] = raw.read().decode("utf-8", errors="replace")
    if not files:
        raise ValueError("源压缩包没有 LaTeX 正文")
    return files


def split_prose(text: str, limit: int = MAX_BLOCK) -> list[str]:
    """Split only at whitespace outside protected math/citation spans; never drop text."""
    from .skills import PROTECTED
    parts = []
    while len(text) > limit:
        spans = [(m.start(), m.end()) for m in PROTECTED.finditer(text)]
        choices = [m.start() for m in re.finditer(r"\s+", text[:limit + 1])
                   if m.start() > 0 and not any(a <= m.start() < b for a, b in spans)]
        if not choices:
            break
        at = choices[-1]
        parts.append(text[:at])
        text = text[at:].lstrip()
    if text.strip():
        parts.append(text.strip())
    return parts


def section_heading(line: str) -> tuple[str, str] | None:
    start = re.match(r"\s*\\(?:sub)*section\*?(?:\[[^]]*\])?\{", line)
    if not start:
        return None
    depth = 1
    for index in range(start.end(), len(line)):
        if index and line[index - 1] == "\\":
            continue
        depth += (line[index] == "{") - (line[index] == "}")
        if depth == 0:
            tail = re.sub(r"^\s*\\label\{[^}]*\}", "", line[index + 1:])
            return line[start.end():index], tail
    return None


def is_layout_only(text: str) -> bool:
    """Recognize complete, allowlisted TeX configuration commands without executing them."""
    arities = {"renewcommand": 2, "newcommand": 2, "providecommand": 2,
               "setcounter": 2, "addtocounter": 2, "setlength": 2, "addtolength": 2}
    index, count = 0, 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            break
        command = re.match(r"\\([A-Za-z]+)\*?", text[index:])
        if not command or command[1] not in arities:
            return False
        index += command.end()
        for _ in range(arities[command[1]]):
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text) or text[index] != "{":
                return False
            depth = 1
            index += 1
            while index < len(text) and depth:
                if text[index] == "\\":
                    index += 2
                    continue
                depth += (text[index] == "{") - (text[index] == "}")
                index += 1
            if depth:
                return False
        count += 1
    return count > 0


def parse_latex(data: bytes) -> tuple[list[dict], list[str]]:
    files = safe_members(data)
    roots = [name for name, text in files.items() if re.search(r"\\documentclass(?:\[.*?\])?\{", text)]
    if len(roots) != 1:
        raise ValueError(f"无法唯一确定 LaTeX 主文档（找到 {len(roots)} 个）")
    warnings: list[str] = []
    lines: list[tuple[str, int, str]] = []
    visited: set[str] = set()

    def expand(name: str, stack: tuple[str, ...] = ()):
        if name in stack or len(stack) > 15:
            raise ValueError("LaTeX 引用循环或嵌套过深")
        visited.add(name)
        for number, line in enumerate(files[name].splitlines(), 1):
            line = re.sub(r"(?<!\\)%.*", "", line)
            matches = list(re.finditer(r"\\(?:input|include)\{([^}]+)\}", line))
            offset = 0
            for match in matches:
                lines.append((name, number, line[offset:match.start()]))
                # TeX resolves input paths from the main working directory, not the including file.
                include = match[1] if match[1].endswith(".tex") else match[1] + ".tex"
                target = posixpath.normpath(posixpath.join(posixpath.dirname(roots[0]), include))
                relative = posixpath.normpath(posixpath.join(posixpath.dirname(name), include))
                if target not in files and relative in files:
                    target = relative
                if target in files:
                    expand(target, (*stack, name))
                else:
                    warnings.append(f"缺少引用文件：{target}")
                    lines.append((name, number, f"[缺失源文件：{target}]"))
                offset = match.end()
            lines.append((name, number, line[offset:]))

    expand(roots[0])
    # Bibliography is immutable source material, never invented or translated.
    for name in files:
        if name.endswith(".bbl") and name not in visited:
            lines.extend((name, index, line) for index, line in enumerate(files[name].splitlines(), 1))
    blocks, buffer = [], []
    section, active, environment, kind = "前言", False, None, "prose"
    math_open_line = False
    location = ""

    def flush():
        nonlocal buffer
        raw = "\n".join(buffer).strip()
        buffer = []
        if not raw:
            return
        text = raw
        if kind == "prose":
            # Keep unknown commands instead of guessing custom macro expansion.
            text = re.sub(r"\\(?:textbf|textit|emph|texttt)\{([^{}]*)\}", r"\1", text)
            text = re.sub(r"\\(?:noindent|maketitle|tableofcontents)\b", "", text)
            text = re.sub(r"\s+", " ", text).strip()
        for part in split_prose(text) if kind == "prose" else [text]:
            if part:
                from .skills import PROTECTED
                prose_content = PROTECTED.sub("", part)
                translatable = kind == "prose" and not is_layout_only(part) and len(part) <= MAX_BLOCK and bool(re.search(r"[A-Za-z\u3400-\u9fff]", prose_content))
                blocks.append({"text": part, "kind": "protected" if kind == "prose" and not translatable else kind,
                               "section": section, "location": location, "translatable": translatable})

    for name, number, line in lines:
        math_open_line = False
        if not active and "\\begin{document}" in line:
            active = True
            line = line.split("\\begin{document}", 1)[1]
        if name.endswith(".bbl"):
            if kind != "reference":
                flush()
                kind, section = "reference", "参考文献"
            active = True
        if not active:
            continue
        if "\\end{document}" in line:
            line = line.split("\\end{document}", 1)[0]
            if line.strip():
                buffer.append(line)
            flush()
            active = False
            continue
        if not environment:
            if line.strip().startswith("$$") or line.strip().startswith("\\["):
                flush()
                environment = "$$" if line.strip().startswith("$$") else "\\["
                kind = "protected"
                math_open_line = True
            heading = section_heading(line)
            if heading:
                flush()
                section, line = heading
                kind = "prose"
                if not line.strip():
                    continue
            env = re.search(r"\\begin\{(equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|eqnarray\*?|displaymath|table\*?|figure\*?|tabular\*?|verbatim\*?|lstlisting|minted|algorithm\*?|algorithmic|tikzpicture|thebibliography)\}", line)
            if env:
                flush()
                environment = env[1]
                kind = "reference" if environment == "thebibliography" else "protected"
        if not buffer:
            location = f"{name}:{number}"
        if not line.strip() and not environment:
            flush()
        else:
            buffer.append(line)
        if environment and (f"\\end{{{environment}}}" in line or
                            environment == "$$" and "$$" in line and (not math_open_line or line.count("$$") >= 2) or
                            environment == "\\[" and "\\]" in line):
            flush()
            environment, kind = None, "prose"
    flush()
    if environment:
        warnings.append(f"未闭合的 LaTeX 环境：{environment}")
    if any("\ufffd" in b["text"] for b in blocks):
        warnings.append("源文件包含无法解码字符，可能存在乱码；请核对原 PDF。")
    if any("\\" in b["text"] for b in blocks if b["kind"] == "prose"):
        warnings.append("LaTeX 自定义命令保留为原始标记；未执行宏，需对照原 PDF 检查排版和公式。")
    if any(len(b["text"]) > MAX_BLOCK and b["kind"] == "prose" for b in blocks):
        warnings.append("超长不可安全切分段落保留原文，不调用模型。")
    if not any(b["kind"] == "reference" for b in blocks):
        warnings.append("源包未包含可识别的参考文献正文，请查看原 PDF 的参考文献页。")
    return blocks, list(dict.fromkeys(warnings))


def parse_pdf(data: bytes, check_cancel=lambda: None) -> tuple[list[dict], list[str]]:
    from pypdf import PdfReader
    if not data.startswith(b"%PDF-"):
        raise ValueError("回退来源没有返回 PDF")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or len(reader.pages) > 150:
        raise ValueError("PDF 加密或超过 150 页处理上限")
    blocks, warnings = [], ["PDF 文本回退：阅读顺序、公式、表格和图像不能保证完整；须对照原 PDF，禁止猜补。"]
    for page_number, page in enumerate(reader.pages, 1):
        check_cancel()
        text = page.extract_text() or ""
        if len(text.strip()) < 40 or "\ufffd" in text or "\x00" in text:
            warnings.append(f"第 {page_number} 页文本缺失/乱码或可能为扫描页，未执行 OCR。")
        if len(text) > 100000:
            raise ValueError("PDF 单页文本异常大")
        for paragraph in re.split(r"\n\s*\n", text):
            for part in split_prose(paragraph.strip()):
                blocks.append({"text": part, "kind": "prose", "section": f"第 {page_number} 页",
                               "location": f"PDF 第 {page_number} 页", "page": page_number,
                               "translatable": len(part) <= MAX_BLOCK and "\ufffd" not in part and "\x00" not in part})
    return blocks, warnings


def make_document(paper: dict, blocks: list[dict], warnings: list[str], method: str, url: str, data: bytes) -> dict:
    if not blocks or sum(len(b["text"]) for b in blocks) < 200:
        raise ValueError("没有足够可读取正文，解析失败；不能以摘要替代全文。")
    if len(blocks) > 2000 or sum(len(b["text"]) for b in blocks) > 1500000:
        raise ValueError("全文超出 2,000 段或 150 万字符处理上限")
    for i, block in enumerate(blocks):
        block["id"] = f"p{i + 1:04d}"
        block["source_hash"] = hashlib.sha256(block["text"].encode()).hexdigest()
    content_hash = hashlib.sha256(json.dumps(blocks, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"paper_id": paper["id"], "version": paper["version"], "mode": paper["mode"], "parser_version": PARSER_VERSION,
            "source_url": url, "source_hash": hashlib.sha256(data).hexdigest(), "document_hash": content_hash,
            "method": method, "status": "needs_review" if warnings else "ready", "warnings": warnings,
            "blocks": blocks, "paragraph_count": len(blocks), "fetched_at": repo.utc_now(),
            "coverage": "extracted_fulltext", "note": "仅覆盖已解析文本。图像与版式请查看原 PDF；未做语义完整性保证。"}


def fetch_fulltext(record: dict, owner: str) -> dict:
    from . import jobs
    paper = record["paper"]
    cached = get_document(paper)
    if cached:
        document = cached
    elif paper.get("source") == "local":
        jobs.update_progress(record["id"], owner, "解析本地 PDF（不请求外部来源）", 25)
        data = cached_asset(paper["pdf_url"])
        if data is None:
            raise ValueError("本地 PDF 原文件不存在，请重新上传。")
        try:
            blocks, warnings = parse_pdf(data, lambda: jobs.ensure_running(record["id"], owner))
        except (jobs.Cancelled, jobs.LeaseLost):
            raise
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(f"本地 PDF 解析失败（{type(error).__name__}）；请检查是否损坏或使用不支持的编码。") from None
        document = make_document(paper, blocks, warnings, "pdf", paper["pdf_url"], data)
        jobs.ensure_running(record["id"], owner)
        save_document(paper, document)
    else:
        identifier, version = canonical_id(f"{paper['id']}v{paper['version']}")
        check = lambda: jobs.ensure_running(record["id"], owner)
        source_url = f"https://arxiv.org/src/{identifier}v{version}"
        jobs.update_progress(record["id"], owner, "获取 LaTeX 结构化源文件", 20)
        try:
            data = cached_asset(source_url) or download(source_url, check)
            blocks, warnings = parse_latex(data)
            document = make_document(paper, blocks, warnings, "latex", source_url, data)
        except FulltextRateLimited:
            raise
        except ValueError as error:
            source_error = str(error)
            jobs.update_progress(record["id"], owner, "源文件不可用，回退 PDF 文本解析", 45)
            source_url = f"https://arxiv.org/pdf/{identifier}v{version}"
            data = cached_asset(source_url) or download(source_url, check)
            try:
                blocks, warnings = parse_pdf(data)
            except Exception as error:
                raise ValueError(f"源文件失败：{source_error}；PDF 解析失败（{type(error).__name__}），未生成替代正文。") from None
            document = make_document(paper, blocks, [f"源文件：{source_error}", *warnings], "pdf", source_url, data)
        jobs.ensure_running(record["id"], owner)
        save_asset(source_url, data)
        save_document(paper, document)
    markdown = f"# 全文读取 · {paper['title']}\n\n来源：{document['source_url']}\n\n解析方式：{document['method']}；段落：{document['paragraph_count']}\n\n"
    markdown += "\n".join(f"- {warning}" for warning in document["warnings"])
    for block in document["blocks"]:
        markdown += f"\n\n## {block['id']} · {block['section']}\n\n位置：{block['location']}\n\n{block['text']}"
    result = {"coverage": "fulltext", "mode": paper["mode"], "skill_version": PARSER_VERSION,
              "source": document["source_url"], "markdown": markdown, "document_hash": document["document_hash"],
              "paragraph_count": document["paragraph_count"], "warnings": document["warnings"]}
    jobs.save_checkpoint(record["id"], owner, result)
    return result
