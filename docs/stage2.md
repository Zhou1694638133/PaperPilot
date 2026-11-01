# 阶段二：全文研究工作台

阶段一检索、收藏、摘要任务与导出保留。默认只匹配近两年（730 天），以 arXiv `published` 首次公开时间为准，不以更新时间伪装新论文。可手动扩大日期范围。

## 使用路径

1. 在发现页选择 **Agent 强化学习**、**大模型后训练**或 **CCF A 已核实入口**。实际 arXiv 表达式与日期条件可展开查看。
2. 打开论文详情，点击 **读取全文**。独立 worker 优先取得 LaTeX 源包，失败后使用 PDF 文本，显示来源、版本、章节/页码及解析警告。
3. 查看原文并核对警告。模型未配置时，正文阅读、术语表、对比和 PDF 导出仍可使用。
4. 配置后端模型后，勾选接受解析范围（如有警告），运行 **结构化精读** 或 **翻译已解析全文**。任务进度来自 SSE，不使用假进度。
5. 在 **双语对照** 中按章节阅读。未完成块显示“待翻译”；保护块显示“原文保留”。任务取消/失败后点击 **从检查点继续**。
6. 从发现列表或详情将 2–4 篇论文加入对比。对比并列真实摘要和已有精读证据；没有精读时明确未知。
7. 下载论文报告、已解析正文、精读/翻译产物或对比 PDF。Markdown/BibTeX 继续可用。

## 范围与真实性

| 功能 | 实现与边界 |
| --- | --- |
| 全文读取 | 固定 arXiv HTTPS 来源；LaTeX 主文件与 input/include 解析，PDF 回退；不是任意 URL 抓取器 |
| 全文结构化精读 | 分块分析研究问题、方法、实验陈述、局限与研究关联；逐条精确引文核验并定位段落；各块结果确定性汇总，不伪装成独立复现 |
| 翻译 | 正文逐块调用模型；术语快照；原译文一一对齐；公式、引用、代码、表格及参考文献保护块保持原文 |
| 术语表 | 本地 SQLite 持久版本；乐观并发检查；旧任务保留旧术语快照，新版本创建独立任务 |
| 恢复 | 论文版本、文档 hash、术语版本、模型和提示词版本参与任务身份；已完成块不重复请求；不确定计费状态要求明确确认 |
| 论文对比 | 2–4 篇已有论文与已有分析并列；不拿不同基准数字判定胜负；对比选择保留于当前页面会话 |
| PDF | ReportLab 分页，Windows 默认嵌入宋体；正文、来源、未知项与已有产物导出；LaTeX 公式按原始文本保留，不伪称完整 TeX 排版 |
| 无模型密钥 | 不产生精读或翻译结果；真实元数据、全文、规则推荐、收藏、术语和导出可用 |

LaTeX 不执行命令、不运行 TeX、不猜测自定义宏；图像/复杂表格/公式视觉布局请对照原 PDF。源文件缺失、未闭合环境、无法解码字符和 PDF 空白/扫描页会产生警告。PDF 回退不能保证栏序、数学和图表完整性；没有 OCR 或猜补。全文最多 2,000 块、150 万字符；PDF 最多 150 页。

下载默认总时限 120 秒（`FULLTEXT_TIMEOUT`），20 MiB 上限；源包展开上限 60 MiB、1,000 个文件，拒绝绝对路径、路径穿越和符号链接，不解包到用户文件系统。原始源包保存在本地私有 SQLite 缓存，解析器升级可以复用下载结果。收到 429 后遵守限流，不立即转向 PDF 继续请求。

## CCF A 与研究模块

CCF A 属于会议目录标签，不代表单篇论文质量。本版有 **4 篇人工核实的种子论文**，并非完整自动会议索引。只有 arXiv ID、标题和保存的正式会议来源对应时才显示 A 类标记；未核实条目保持未知。默认近两年会过滤历史 DPO（2023），不会将其 arXiv 更新时间当作新发表时间。

- CCF 目录：[总目录](https://www.ccf.org.cn/Academic_Evaluation/By_category/)、[人工智能目录](https://www.ccf.org.cn/Academic_Evaluation/AI/)。核对日期 2026-10-25；目录总页与分类页可能存在更新不同步，当前种子仅使用已核对的 NeurIPS A 类映射。
- DAPO，arXiv 2503.14476：[NeurIPS 2025 主会来源](https://proceedings.neurips.cc/paper_files/paper/2025/hash/a4277440d50f1f15d2cb4c14f7e0c0d2-Abstract-Conference.html)。
- GiGPO，arXiv 2505.10978：[NeurIPS 2025 主会来源](https://proceedings.neurips.cc/paper_files/paper/2025/hash/420c9f777c0b4f78d515e53cf74d58b2-Abstract-Conference.html)。
- ReMA，arXiv 2503.09501：[NeurIPS 2025 主会来源](https://proceedings.neurips.cc/paper_files/paper/2025/hash/b83514e453ff03741a5fb3e5e49f709b-Abstract-Conference.html)。
- DPO，arXiv 2305.18290：[NeurIPS 2023 主会来源](https://proceedings.neurips.cc/paper_files/paper/2023/hash/a85b405ed65c6477a4fe8302b5e06ce7-Abstract.html)。

Agent 强化学习模块检索 agent/agentic 与 RL、策略优化、信用分配的组合；后训练模块检索 LLM 与 post-training、RLHF、RLVR、DPO、GRPO。关键词可编辑。默认排序在当前来源页内优先已核实 CCF A，再按原有匹配分；会议优先不加进匹配分，不冒称全库最优排序。

## 数据与升级

数据库增量创建 `documents`、`glossary`、`analysis_chunks`、`fulltext_assets`，不删除原论文、收藏、笔记或阶段一任务。此次升级前已用 SQLite backup 保存业务库与 LangGraph checkpoint 到 `data/backups/phase1-before-stage2-*`。阶段二首次升级将默认时间设为 730 天，并补充两个研究方向；之后用户修改偏好不会每次启动被重置。

新增依赖经用户明确同意：pypdf 6.21.2、reportlab 4.5.1（Pillow 为传递依赖）。复现请执行 `uv sync --locked --python 3.12` 与 `npm.cmd ci`，具体启动命令沿用 README。

模型密钥未配置，本轮不能验证真实付费模型的语义完整性与翻译质量。模型相关自动化测试使用标明的 Mock，验证 schema、证据、保护块与恢复协议；不得把它们当作真实模型输出。订阅周报、全量会议采集、OpenReview/Semantic Scholar、语义召回和模型重排尚未接入。
