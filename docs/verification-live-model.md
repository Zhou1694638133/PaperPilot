# 真实模型端到端验收

验收日期：2026-10-25。用户已保存后端模型配置并明确授权实际连接、精读、翻译和导出。本次使用 `deepseek-flash`；没有读取、输出或修改用户密钥。

## 最终验收论文

**Group-Graph Policy Optimization for Long-Horizon Agentic Reinforcement Learning（G2PO）**，arXiv `2606.22995v2`，首次公开于 **2026-06-22**，符合近两年筛选条件。

- [arXiv 论文与许可入口](https://arxiv.org/abs/2606.22995v2)
- [CC BY 4.0 许可](https://creativecommons.org/licenses/by/4.0/)
- 作者：Yunan Wang、Minghui Song、Zihan Zhang、Shaohan Huang、Haizhen Huang、Furu Wei、Weiwei Deng、Feng Sun、Qi Zhang。

主题为长时程、多轮 LLM Agent 强化学习中的信用分配与状态图价值估计。没有把源文件模板名称当作会议录用证据；本次不新增 CCF A 录用标记。

初测使用过 GiGPO：精读已完成，部分翻译检查点保留。为交付有明确改编许可的完整双语文件，最终验收改用 G2PO；没有继续 GiGPO 的全文翻译。初测和失败记录保留在任务中心，不隐藏实际调用。

## 实际结果

| 验收项 | 结果 |
| --- | --- |
| 连接测试 | 真实 JSON 生成成功；返回 usage 共 149 tokens |
| 真实全文 | LaTeX 源文件优先；解析器 2.0.4；141 个正文/保护块 |
| 全文精读 | 8 组完成，全部 141 个已解析块进入分析；五个结构化字段可读取 |
| 证据核验 | 48 条引用全部精确对应原文段落，并核对字符偏移 |
| 全文翻译 | 141/141 对齐；93 个正文块有中文译文，48 个保护块原样保留 |
| 完整性 | 原文 hash、段落顺序、数字计数、所有保护内容逐项核对通过；无遗留占位符或替换乱码 |
| 术语版本 | 任务冻结术语表 v2；19 条术语提示保留，主要为 GRPO/DPO/RLHF 缩写未使用指定中文扩展名 |
| 断点恢复 | 曾发生中文数字误报及一次连接失败；最终成功。网络恢复前的 120 个块，其 hash、请求标识与创建时间均未变化 |
| SSE 恢复 | 从持久事件 401 后读取 402，断开后从 402 续接得到 403；没有使用前端模拟进度 |
| PDF | 实际调用导出 API；精读 19 页、双语 90 页，均附作者、来源与许可页 |
| 浏览器 | 结构化五字段、48 条证据、证据跳转原文、章节筛选和 141/141 双语状态均实际操作验证；桌面无横向溢出 |

精读任务：`558c9fb8-43e6-4caa-9cf6-b5186a62c6a2`，最终 `succeeded`，attempts=2。

翻译任务：`49da526c-22d9-403b-ad35-bff2944230b2`，最终 `succeeded`，attempts=3。

全文读取任务：`626807e8-d584-4b21-988e-a282b570e9f6`。

## 验收产物

- [精读 PDF](../output/pdf/g2po-fulltext-reading.pdf)
- [双语 PDF](../output/pdf/g2po-bilingual.pdf)
- [结构化验收数据](../output/validation/g2po-live-results.json)
- [原译文段落对齐数据](../output/validation/g2po-alignment.json)
- [精读 Markdown](../output/validation/g2po-reading.md)、[双语 Markdown](../output/validation/g2po-bilingual.md)
- [实际双语页面截图](screenshots/g2po-live-bilingual.png)

两份 PDF 均检查可提取的文本；双语 PDF 包含首尾段落编号。精读抽查封面、第 2 页及末页；双语抽查第 3、45、90 页，中文、位置标记和分页可读。未逐页人工校对全部内容。

## 真实运行发现并修复的问题

1. LaTeX 章节标题的贪婪匹配将相邻 `label` 并入标题。改为括号平衡解析，并保留标题同一行的正文。
2. DeepSeek 默认思考模式可能占满原有输出预算，导致精读被截断。依据[官方接口说明](https://api-docs.deepseek.com/guides/thinking_mode/)，仅对官方域名的 Flash/V4 模型默认发送 `thinking.type=disabled`；其他兼容服务不发送该字段，可用环境变量显式开启。
3. 数字完整性正则使用 Unicode 单词边界，误判紧邻中文的“1步”等数值。改为 ASCII 标识符边界，仍拒绝实际新增或丢失数字。
4. 模型复述引文可能改变原文格式。全文精读改为优先返回原文块 ID，后端提取原文；保留对精确引文的兼容，并继续拒绝无效证据。
5. 翻译提示增加必须保留的占位符列表，失败消息指出具体校验原因，不再只显示笼统错误。

涉及 `backend/app/fulltext.py`、`llm.py`、`skills.py`、`deep_reading.py`、`jobs.py`，以及相应测试、全文 Skill 版本和配置文档。没有新增依赖，没有改动密钥配置，没有提交或推送 Git。

## 回归命令

在 `backend` 目录：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_fulltext.py -q --basetemp '..\data\pytest-live-parser' --tb=short
.\.venv\Scripts\python.exe -m pytest tests\test_model_config.py -q --basetemp '..\data\pytest-live-model' --tb=short
.\.venv\Scripts\python.exe -m pytest tests\test_fulltext.py -q -k 'numeric_integrity or fulltext_reader' --basetemp '..\data\pytest-live-integrity' --tb=short
.\.venv\Scripts\python.exe -m pytest -q --basetemp '..\data\pytest-live-final' --tb=short
```

结果依次为 22 项、17 项、4 项通过；最后完整回归为 **76 passed in 235.41s**。测试临时目录和 PDF 栅格文件在检查后清理，业务数据、真实模型任务与最终产物保留。

## 实际边界

- 首次验收代表已解析内容的执行、对齐与结构校验通过，不代表逐句语义准确或独立复现实验。当时的 19 条术语提示已在下述局部校订中逐条处理。
- LaTeX 自定义宏保持原始标记，图像、复杂公式和版式以原 PDF 为准。本次源包未提取到可识别的参考文献正文，没有猜补；141/141 指已解析块，不表示参考文献或图像均已翻译。
- 费用应以提供方账单为准。精读已保存成功块 usage 合计 39,532 tokens，但不包含被截断、校验失败或初测任务的调用；翻译产物的 `usage_this_attempt` 仅覆盖最后一次恢复。不能将其当作整个验收的总费用。

## 2026-10-25 术语局部校订

按用户要求处理全部 19 条提示：16 处 GRPO 统一为“组相对策略优化（GRPO）”，DPO、RLHF 分别统一为“直接偏好优化（DPO）”“人类反馈强化学习（RLHF）”，“大型语言模型（LLMs）”统一为“大语言模型（LLMs）”。每个受影响段落只展开首次出现的缩写，其后缩写保留。没有重新翻译全文，也没有新增模型请求。

- 修改 16 个段落，另外 125 个块保持不变；复用全部 141 个检查点，48 个保护块逐字保留。
- 原文、原文哈希、模型请求 ID、原始生成时间、任务尝试次数（3）不变；所有数值及公式、引用、代码保护片段通过计数校验。
- 同步段落检查点、任务结果及结果检查点、Markdown 产物、SSE 事件和 LangGraph 最新快照。保留原始 LangGraph 快照和包含 19 项前后文本的 JSON 审计记录。
- 浏览器实际刷新后确认 141/141 完成，Introduction 和 Related work 的 GRPO、DPO、RLHF 中文译法已更新。
- 双语 PDF 更新后仍为 90 页；文本检查通过，渲染并查看第 3、11、89、90 页，中文、缩写、质量检查与校订记录无溢出。精读 PDF 未修改。
- 术语提示归零，仍保留自定义宏未展开、参考文献正文未提取两条源解析警告。

从项目根目录执行的验证：

```powershell
.\backend\.venv\Scripts\python.exe scripts\revise_g2po_terms.py
.\backend\.venv\Scripts\python.exe scripts\revise_g2po_terms.py --apply
.\backend\.venv\Scripts\python.exe scripts\verify_g2po_revision.py --export
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests\test_term_revision.py -q --basetemp '..\data\pytest-term-revision' --tb=short
```

只读预演和真实保存校验均通过；重复执行 `--apply` 检测已校订并保持不变；新增针对性测试 **4 passed in 0.79s**。本次没有重跑此前 76 项全量测试，没有修改运行中的服务代码、依赖或密钥。

逐项清单见 [术语校订记录](../output/validation/g2po-terminology-review.md)，完整前后文本与原检查点见 [审计 JSON](../output/validation/g2po-terminology-revision.json)。审计文件为本次校订的恢复依据，保留不清理。

后续已处理剩余两条源解析提示：按源定义展开 G2PO 宏、从原 PDF 补充 64 条参考文献，同时保留全部翻译检查点。最新双语 PDF 为 109 页，详见[源文件补全验收](verification-source-enrichment.md)。上文 90 页及两条源解析提示为当时验收记录。
