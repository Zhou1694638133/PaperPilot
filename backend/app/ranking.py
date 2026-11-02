"""Deterministic query expansion and evidence-backed ranking, version rules-v1."""
from datetime import datetime, timezone, timedelta
import re

TOPICS = {
    "多智能体协作": ["multi-agent", "multi agent", "agent collaboration", "autogen", "metagpt"],
    "Agent 评测": ["agent evaluation", "agent benchmark", "agentbench", "benchmark", "swe-bench"],
    "大语言模型": ["large language model", "LLM", "language models"],
    "需求理解": ["requirement understanding", "requirements analysis", "intent understanding"],
    "约束抽取": ["constraint extraction", "constraint satisfaction"],
    "方案生成": ["planning", "plan generation", "program synthesis"],
    "闭环校验": ["verification", "self-reflection", "self-correction", "feedback"],
    "检索增强": ["retrieval augmented generation", "RAG"],
    "智能体应用": ["autonomous agent", "tool use", "software engineering"],
    "Agent 强化学习": ["agent reinforcement learning", "agentic reinforcement learning", "reinforcement learning", "credit assignment", "trajectory optimization", "agent lightning", "ragen"],
    "大模型后训练": ["post-training", "post training", "RLHF", "RLVR", "GRPO", "DPO", "supervised fine-tuning", "reward model", "preference optimization"],
}
ALIASES = {"多智能体": "多智能体协作", "多代理": "多智能体协作", "协作": "多智能体协作", "评测": "Agent 评测", "评估": "Agent 评测", "大模型": "大语言模型", "需求": "需求理解", "约束": "约束抽取", "规划": "方案生成", "闭环": "闭环校验", "检索增强": "检索增强", "应用": "智能体应用"}
ALIASES.update({"强化学习": "Agent 强化学习", "后训练": "大模型后训练", "偏好优化": "大模型后训练", "奖励建模": "大模型后训练"})


def topic_keywords(text: str) -> list[str]:
    found = []
    for name, words in TOPICS.items():
        if name.lower() in text.lower() or any(word.lower() in text.lower() for word in words):
            found.extend(words)
    for alias, topic in ALIASES.items():
        if alias in text:
            found.extend(TOPICS[topic])
    return list(dict.fromkeys(found))


def expand_query(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("请输入研究兴趣或英文检索式")
    if re.search(r"\b(?:all|ti|abs|au|cat|id|submittedDate):", text):
        return {"keywords": re.findall(r'"([^\"]+)"', text), "query": text, "method": "manual", "warnings": []}
    keywords = topic_keywords(text)
    if not keywords:
        if re.search(r"[\u4e00-\u9fff]", text):
            return {"keywords": [], "query": "", "method": "dictionary", "warnings": ["该中文主题尚无词典映射，请手动填写英文关键词；未调用模型。"]}
        keywords = [part.strip() for part in re.split(r"[,;，；\n]", text) if part.strip()]
    keywords = keywords[:20]
    query = " OR ".join(f'all:"{word.replace(chr(34), chr(32))}"' for word in keywords)
    return {"keywords": keywords, "query": query, "method": "dictionary", "warnings": ["词典建议可编辑；未使用语义模型。"]}


def keyword_present(keyword: str, text: str) -> bool:
    normalized = re.sub(r"[-–—]", " ", text.lower())
    needle = re.sub(r"[-–—]", " ", keyword.lower()).strip()
    return bool(re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", normalized))


def matches_filters(paper: dict, profile: dict, days: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    published = datetime.fromisoformat(paper["published"].replace("Z", "+00:00"))
    if published > now or (days and published < now - timedelta(days=days)):
        return False
    text = paper["title"] + " " + paper["abstract"]
    def matches_term(term: str) -> bool:
        return any(keyword_present(word, text) for word in (topic_keywords(term) or [term]))
    if any(not matches_term(term) for term in profile["include_keywords"]):
        return False
    if any(matches_term(term) for term in profile["exclude_keywords"]):
        return False
    return True


def rank_paper(paper: dict, profile: dict, run_id: str) -> dict:
    abstract, title = paper["abstract"], paper["title"]
    hits = []
    matched_terms = []
    for interest in profile["interests"]:
        terms = topic_keywords(interest) or [interest]
        matching = [term for term in terms if keyword_present(term, abstract + " " + title)]
        if matching:
            hits.append(interest)
            matched_terms.extend(matching)
    # Each interest contributes equally. Unknown experimental quality is not scored.
    topic = len(hits) / max(1, len(profile["interests"])) * 100
    title_score = 100 * sum(keyword_present(term, title) for term in set(matched_terms)) / max(1, len(set(matched_terms)))
    weight = profile.get("topic_weight", 0.75)
    score = round(weight * topic + (1 - weight) * title_score, 1)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n\n+", abstract) if s.strip()]
    evidence = [{"text": sentence, "location": f"摘要第 {i + 1} 句"} for i, sentence in enumerate(sentences)
                if any(keyword_present(term, sentence) for term in matched_terms)][:3]
    title_hit = any(keyword_present(term, title) for term in matched_terms)
    if title_hit:
        evidence.append({"text": title, "location": "论文标题"})
    unknowns = ["全文尚未解析", "代码及数据可用性未核实", "实验完整性与可复现性未知", "会议录用状态未核实"]
    from .research import publication
    verified_publication = publication(paper)
    if verified_publication["status"] == "verified":
        unknowns.remove("会议录用状态未核实")
    if paper["mode"] == "demo":
        unknowns.insert(0, "演示样本：摘要为原文节选，非完整摘要")
    return {"score": score, "interests": hits, "evidence": evidence, "coverage": "abstract",
            "features": [{"name": "研究兴趣命中比例", "value": round(topic, 1), "weight": weight},
                         {"name": "命中词的标题覆盖率", "value": round(title_score, 1), "weight": round(1 - weight, 4)}],
            "unknowns": unknowns, "method": "rules-v1", "run_id": run_id,
            "reason": ("与「" + "、".join(hits) + "」存在关键词关联。" if hits else "未找到研究兴趣的直接关键词证据；作为多样性候选。") + "仅根据标题与摘要，匹配分不代表学术质量概率。",
            "semantic_enabled": False, "model_rerank_enabled": False}
