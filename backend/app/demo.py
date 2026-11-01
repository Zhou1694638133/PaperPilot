"""Small, explicitly labelled historical metadata excerpts for offline UI exploration.
These are never a fallback for live searches. Complete abstracts are retrieved in live mode.
"""
from copy import deepcopy

# Metadata and short abstract excerpts checked against linked arXiv records.
DEMO_PAPERS = [
    {"id": "2308.08155", "version": 2,
     "title": "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation",
     "authors": ["Qingyun Wu", "Gagan Bansal", "Jieyu Zhang", "Yiran Wu", "Beibin Li", "Erkang Zhu", "Li Jiang", "Xiaoyun Zhang", "Shaokun Zhang", "Jiale Liu", "Ahmed Hassan Awadallah", "Ryen W White", "Doug Burger", "Chi Wang"],
     "abstract": "AutoGen is an open-source framework that allows developers to build LLM applications via multiple agents.",
     "published": "2023-08-16T05:57:52Z", "updated": "2023-10-03T20:47:10Z", "categories": ["cs.AI", "cs.CL"]},
    {"id": "2308.03688", "version": 3, "title": "AgentBench: Evaluating LLMs as Agents",
     "authors": ["Xiao Liu", "Hao Yu", "Hanchen Zhang", "Yifan Xu", "Xuanyu Lei", "Hanyu Lai", "Yu Gu", "Hangliang Ding", "Kaiwen Men", "Kejuan Yang", "Shudan Zhang", "Xiang Deng", "Aohan Zeng", "Zhengxiao Du", "Chenhui Zhang", "Sheng Shen", "Tianjun Zhang", "Yu Su", "Huan Sun", "Minlie Huang", "Yuxiao Dong", "Jie Tang"],
     "abstract": "We present AgentBench, a multi-dimensional benchmark that consists of 8 distinct environments.",
     "published": "2023-08-07T16:08:11Z", "updated": "2025-10-04T03:54:18Z", "categories": ["cs.AI", "cs.CL", "cs.LG"]},
    {"id": "2210.03629", "version": 3, "title": "ReAct: Synergizing Reasoning and Acting in Language Models",
     "authors": ["Shunyu Yao", "Jeffrey Zhao", "Dian Yu", "Nan Du", "Izhak Shafran", "Karthik Narasimhan", "Yuan Cao"],
     "abstract": "In this paper, we explore the use of LLMs to generate both reasoning traces and task-specific actions.",
     "published": "2022-10-06T01:00:32Z", "updated": "2023-03-10T01:00:17Z", "categories": ["cs.CL", "cs.AI", "cs.LG"]},
    {"id": "2310.06770", "version": 3, "title": "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?",
     "authors": ["Carlos E. Jimenez", "John Yang", "Alexander Wettig", "Shunyu Yao", "Kexin Pei", "Ofir Press", "Karthik Narasimhan"],
     "abstract": "We find real-world software engineering to be a rich, sustainable, and challenging testbed.",
     "published": "2023-10-10T16:47:29Z", "updated": "2024-11-11T23:05:04Z", "categories": ["cs.CL", "cs.AI", "cs.SE"]},
]


def demo_papers() -> list[dict]:
    papers = deepcopy(DEMO_PAPERS)
    for paper in papers:
        paper.update(source="arXiv", mode="demo", doi=None, fetched_at="2026-10-25T00:00:00Z",
                     publication_status="未核实", publication_evidence=None, journal_reference=None,
                     abstract_is_excerpt=True, demo_note="历史论文元数据与摘要节选；仅用于离线交互演示。",
                     url=f"https://arxiv.org/abs/{paper['id']}v{paper['version']}",
                     pdf_url=f"https://arxiv.org/pdf/{paper['id']}v{paper['version']}")
    return papers
