"""Research tracks and a small, explicitly sourced publication evidence registry."""
import re

CCF_SOURCE = "https://www.ccf.org.cn/Academic_Evaluation/By_category/"
CCF_EDITION = "CCF 第七版（2026）"

TRACKS = [
    {"id": "agent_rl", "name": "Agent 强化学习", "description": "交互轨迹、信用分配、工具调用与多轮策略优化。", "keywords": ["agent reinforcement learning", "agentic reinforcement learning", "credit assignment", "trajectory optimization"], "query": '(all:"agent" OR all:"agentic") AND (all:"large language model" OR all:"LLM" OR all:"language agents") AND (all:"reinforcement learning" OR all:"policy optimization" OR all:"credit assignment")'},
    {"id": "post_training", "name": "大模型后训练", "description": "SFT、偏好优化、RLHF / RLVR、GRPO、奖励建模与蒸馏。", "keywords": ["post-training", "RLHF", "RLVR", "DPO", "GRPO", "supervised fine-tuning", "reward model"], "query": '(all:"language model" OR all:"LLM") AND (all:"post-training" OR all:"RLHF" OR all:"RLVR" OR all:"DPO" OR all:"GRPO")'},
]

# These are verified examples, not an exhaustive live proceedings index.
VERIFIED = {
    "2505.10978": {"title": "Group-in-Group Policy Optimization for LLM Agent Training", "venue": "NeurIPS", "year": 2025, "evidence_url": "https://proceedings.neurips.cc/paper_files/paper/2025/hash/420c9f777c0b4f78d515e53cf74d58b2-Abstract-Conference.html", "evidence_text": "NeurIPS 2025 Main Conference Track"},
    "2503.09501": {"title": "ReMA: Learning to Meta-Think for LLMs with Multi-agent Reinforcement Learning", "venue": "NeurIPS", "year": 2025, "evidence_url": "https://proceedings.neurips.cc/paper_files/paper/2025/hash/b83514e453ff03741a5fb3e5e49f709b-Abstract-Conference.html", "evidence_text": "NeurIPS 2025 Main Conference Track"},
    "2503.14476": {"title": "DAPO: An Open-Source LLM Reinforcement Learning System at Scale", "venue": "NeurIPS", "year": 2025, "evidence_url": "https://proceedings.neurips.cc/paper_files/paper/2025/hash/a4277440d50f1f15d2cb4c14f7e0c0d2-Abstract-Conference.html", "evidence_text": "NeurIPS 2025 Main Conference Track"},
    "2305.18290": {"title": "Direct Preference Optimization: Your Language Model is Secretly a Reward Model", "venue": "NeurIPS", "year": 2023, "evidence_url": "https://proceedings.neurips.cc/paper_files/paper/2023/hash/a85b405ed65c6477a4fe8302b5e06ce7-Abstract.html", "evidence_text": "Advances in Neural Information Processing Systems 36 (NeurIPS 2023) Main Conference Track"},
}


def publication(paper: dict) -> dict:
    item = VERIFIED.get(paper["id"])
    if paper.get('mode') == 'live' and paper.get('source') != 'local':
        from .conference_discovery import evidence_for
        item = evidence_for(paper) or item
    normalize = lambda text: re.sub(r"\W+", "", text).casefold()
    if paper.get("mode") == "live" and item and normalize(item["title"]) == normalize(paper["title"]):
        return {**item, "status": "verified", "ccf_rank": "A", "catalog_edition": CCF_EDITION,
                "catalog_url": CCF_SOURCE, "verified_at": item.get("verified_at", "2026-10-25"), "note": "按当前目录标注会议；不代表单篇论文质量。arXiv 与会议版本内容可能不同。"}
    return {"status": "unverified", "ccf_rank": None, "venue": None, "evidence_url": None,
            "note": "没有核实正式长文录用来源；arXiv 收录或摘要提及会议不等于 CCF A 论文。",
            "author_claim": paper.get("journal_reference")}


def catalog() -> dict:
    return {"tracks": TRACKS, "ccf": {"edition": CCF_EDITION, "source_url": CCF_SOURCE,
            "coverage": "每个研究方向均优先检索并核验 CCF A 主会论文；已接入 NeurIPS、ICML、ACL 正式论文集，覆盖并非完整 CCF A 目录。",
            "strategy": "topic_first_official_evidence", "venues": ["NeurIPS", "ICML", "ACL"]}}
