"""Exports only persisted source metadata and user annotations; no generated publication claims."""
import re
import unicodedata
from urllib.parse import quote


def bibtex_escape(value: str) -> str:
    replacements = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "%": r"\%", "&": r"\&", "_": r"\_", "#": r"\#", "$": r"\$"}
    return "".join(replacements.get(char, char) for char in value)


def export_bibtex(papers: list[dict]) -> str:
    entries = []
    for paper in papers:
        key = ("local" if paper.get("source") == "local" else "arxiv") + re.sub(r"[^A-Za-z0-9]", "", paper["id"])
        fields = {"title": paper["title"], "author": " and ".join(paper["authors"]), "year": paper["published"][:4],
                  "eprint": paper["id"], "archivePrefix": "arXiv", "primaryClass": paper["categories"][0] if paper["categories"] else "",
                  "url": paper["url"], "note": f"arXiv v{paper['version']}; publication status unverified" + ("; DEMO excerpt" if paper["mode"] == "demo" else "")}
        if paper.get("source") == "local":
            fields = {"title":paper["title"], "note":"Local PDF upload; title from filename; authors, date and publication status unverified"}
        if paper.get("doi"):
            fields["doi"] = paper["doi"]
        entries.append("@misc{" + key + ",\n" + ",\n".join(f"  {name} = {{{bibtex_escape(value)}}}" for name, value in fields.items()) + "\n}")
    return "\n\n".join(entries) + "\n"


def md_escape(value: str) -> str:
    return value.replace("<", "&lt;").replace(">", "&gt;")


def export_markdown(papers: list[dict]) -> str:
    blocks = ["# PaperPilot 论文阅读清单" + ("\n\n匹配分仅用于排序。会议录用、代码、数据与实验完整性均需另行核验。" if any(p.get("source") != "local" for p in papers) else "")]
    for paper in papers:
        rec = paper.get("recommendation") or {}
        collection = paper["collection"]
        source_label = "本地 PDF" if paper.get("source") == "local" else f"arXiv:{paper['id']}v{paper['version']}"
        blocks.append(f"## {md_escape(paper['title'])}\n\n" +
                      f"- 来源：[{source_label}]({paper['url']})\n" +
                      f"- 作者：{md_escape(', '.join(paper['authors']))}\n" +
                      f"- 首次公开：{paper['published'] or '未知'}；更新：{paper['updated'] or '未知'}\n" +
                      f"- 模式：{paper['mode']}；分析范围：摘要{'节选（演示）' if paper['mode'] == 'demo' else ''}\n" +
                      f"- 标签：{md_escape(', '.join(collection['tags']))}\n\n" +
                      md_escape(paper["abstract"]) + "\n\n" +
                      (f"推荐：{md_escape(rec.get('reason', ''))}\n\n" if rec else "") +
                      "\n\n".join("> " + md_escape(e["text"]) + " — " + e["location"] for e in rec.get("evidence", [])) +
                      ("\n\n个人笔记：\n\n" + md_escape(collection["notes"]) if collection["notes"] else ""))
    return "\n\n---\n\n".join(blocks) + "\n"


def translation_filename(title: str, extension: str) -> str:
    """Preserve the original title while producing a portable attachment name."""
    if extension not in {"pdf", "md"}:
        raise ValueError("Unsupported translation extension")
    title = unicodedata.normalize("NFC", title)
    title = "".join(" " if unicodedata.category(char).startswith("C") else char for char in title)
    title = re.sub(r'[<>:"/\\|?*]', "-", title)
    title = re.sub(r"\s+", " ", title).strip(" .")
    title = title.encode("utf-8")[:200].decode("utf-8", errors="ignore").rstrip(" .") or "论文"
    return f"{title}翻译版.{extension}"


def attachment_disposition(filename: str) -> str:
    return "attachment; filename*=UTF-8''" + quote(filename, safe="")
