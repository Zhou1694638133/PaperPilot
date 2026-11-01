---
name: paper_analysis
description: 对已解析论文全文生成有原文证据的总体分析，包括研究问题、方法、创新、实验、局限与可借鉴研究思路。配套可执行后端 runner 和独立中文 PDF 排版。
version: 1.0.0
---

# 论文总体分析

应用内注册 ID：`paper_analysis`；入口：论文详情 → 全文研究工作台 → 总体分析。亦可从 Skills 工作台对当前论文运行。

## 执行约定

- 实际 runner：`backend/app/paper_analysis.py:run`，由持久 LangGraph Supervisor 和独立 worker 执行。此文档不执行外部代码。
- 输入：`POST /api/jobs`，`skill_id=paper_analysis`、`paper_id`、`mode=live`、`scope=fulltext`。全文有解析警告时必须由用户核对并传 `acknowledge_partial=true`。
- 无模型密钥、无全文或演示模式明确拒绝；不得用摘要冒充全文，不得伪造分析。
- 固定论文版本、文档 hash、模型服务与模型名称、研究兴趣和本 Skill 版本；相同输入复用任务。模型密钥仅由后端读取，不进任务快照或日志。
- 全文按不超过 10,000 字符的正文组分析，参考文献、乱码和超长保护块明确列为未分析内容。收到的模型原始结构化响应先保存再校验，验证器修复可复用；分块成功即存 `analysis_chunks`，失败/取消后复用检查点。
- 多块采用每次最多 4 组的分层综合，综合只能引用该组笔记已有的原文段落 ID；禁止虚构或引用其他块 ID。收到结果后先保存再处理取消；结果不确定遵循现有重复计费确认流程。

## 输出 schema

`AnalysisOutput` / `AnalysisResult` 定义于 runner，并通过 `/api/skills` 暴露 JSON Schema。六个必填列表：

1. `problem`：研究问题与适用范围。
2. `method`：方法机制、输入输出、训练/推理流程。
3. `innovation`：作者主张的贡献；不得表述为已经独立查新确认。
4. `experiments`：作者报告的实验、基线与结果；不得编造数值。
5. `limitations`：作者承认的局限与分析推断，明确区分。
6. `research_ideas`：可借鉴的研究方向，须给出适配方式、基线/实验及可检验条件。

每点为 `{text, basis, evidence}`。`basis` 必须是 `author_claim`、`analysis`、`proposal` 或 `unknown`。研究思路仅允许 `proposal/unknown`。非未知项优先引用 1–4 个真实原文块 ID；额外证据全部逐项校验，受本次可用原文 ID 集合和结构化响应大小限制（防异常输入上限 200），不因多于 4 个有效引用丢弃已付费结果。缺证据项不得当作事实放行。未知项统一为“当前原文未提供，尚未核实”。中文正文保留必要英文缩写，不输出 LaTeX 命令。

结果同时含 `document_hash`、`analyzed_paragraph_ids`、`evidence_locations`、`unknowns` 与 Markdown；证据包含原文、章节与位置。证据存在性校验不能替代事实核查或实验复现。

## 格式与导出

- Markdown：`GET /api/jobs/{analysis_id}/artifact`；文件名为原论文名称 + `总体分析.md`。
- 中文 PDF 单独调用 `analysis_pdf`，复用完成的分析，不重新调用模型。
- 仅复用现有两种翻译模板：`skills/chinese_pdf/templates/single-column.tex` 与 `two-column.tex`，不复制模板形成第三套。
- 栏式根据原 PDF 正文识别；明确的 LaTeX 栏式为后备依据。混合或无法可靠识别时明确报错，不猜测。
- 黑色宋体、现有标题层级、页边距和段落格式；原文单栏则单栏，双栏则双栏。导出的是分析报告，不改写译文或移除原译文中的图片。
- PDF 仅包含论文分析、必要的主张/建议区分、证据位置和覆盖范围；不附加产品广告、任务日志或无关说明。所有生成文字先做 TeX 转义，不执行模型提供的排版命令。

## 回归验证

`backend/tests/test_paper_analysis.py` 覆盖无密钥、错误证据、研究建议标记、检查点恢复、版本绑定、下载与两种模板。
