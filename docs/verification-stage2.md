# 阶段二实际验收记录

验证日期：2026-10-25。Windows PowerShell；Node.js 24.14.0、npm 11.9.0、Python 3.12.13。API、独立 worker 与 Vite 均实际启动。阶段一历史验收记录保留于 [verification.md](verification.md)。

## 自动化验证

从 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp '..\data\pytest-stage2-release' --tb=short
```

结果：**56 passed in 84.07s**。保留原有阶段一用例，新增 21 项全文相关用例。历史年份的两项旧用例显式使用 `days=0`，避免与新的 730 天默认值冲突。

随后调整 PDF 标题与位置行的分页，以及强化学习模块的 LLM 查询约束，运行相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_fulltext.py -q -k 'comparison_unknowns_and_pdf_chinese or two_year_default' --basetemp '..\data\pytest-pdf-final' --tb=short
```

结果：**2 passed, 19 deselected in 6.55s**。最终全文 Skill 注册版本与解析器 2.0.3 对齐。

从 `frontend` 目录执行 `npm.cmd run build`：**通过，9.56 秒**，包括 `tsc --noEmit` 类型检查。Vite 插件耗时提示不影响构建。

覆盖内容包括：

- 阶段一日期过滤、去重、来源证据、收藏持久化、无密钥降级、接口错误、任务恢复与 SSE。
- 源包路径穿越、符号链接、展开上限、相对 input/include、长段落与公式保护。
- PDF 空白页警告、下载重定向限制、429 后不继续尝试 PDF。
- 无密钥全文 worker；版本变化使旧文档失效；解析警告确认。
- 术语表版本冲突与任务快照；断点续译仅请求未完成块；保护块不调用模型。
- 精读引文精确匹配与伪造引文拒绝；对比未知项；中文 PDF。

## 真实网络与运行数据

| 项目 | 实际结果 |
| --- | --- |
| 默认日期 | 偏好与 API 默认 730 天，按 `published` 首次公开时间；重启后仍为 730 |
| CCF A 入口 | arXiv 实际返回 ReMA、DAPO、GiGPO 共 3 篇；2023 年 DPO 被时间条件排除 |
| Agent 强化学习模块 | UI 触发真实检索，返回 10 篇/来源候选 2,248；实际表达式包含 agent、LLM、RL 条件及 730 天日期范围 |
| 后训练模块 | UI 触发真实检索，返回 10 篇/来源候选 6,537；实际表达式包含 LLM 与 post-training/RLHF/RLVR/DPO/GRPO 及日期范围 |
| 来源证据 | 三篇均显示正式 NeurIPS 2025 来源链接；其余论文保持录用未核实 |
| DAPO 全文 | arXiv 2503.14476v2；最终解析器 2.0.3；124 块，其中 73 个可翻译正文块、51 个保护块 |
| 全文任务 | `f1506c7b-a17c-4eb4-b4e8-67860ed6059d`，独立 worker 执行成功，attempt=1 |
| 检查点 | `1f1d03c5-d0f5-6e12-8003-b3755e021b4f`；任务及正文重启后可读取 |
| 解析范围 | LaTeX 源文件及章节/行号；自定义宏保留警告可见；没有伪补缺失图像或公式排版 |
| 回退与错误 | 较早低超时配置触发 PDF 回退并显示警告；曾遇真实 arXiv 429，后续请求成功；当前下载总时限为 120 秒 |
| 术语表 | 通过 UI 保存为 v2，API 重启后持久化；既有任务冻结其创建时版本 |
| 模型状态 | `model_configured=false`；没有真实付费模型调用，没有伪造精读或译文 |

真实 CCF A 检索运行记录 `909b2db1-73ad-4e6b-a534-3238d8a17ad6` 保存的表达式为：

```text
(id:2505.10978 OR id:2503.09501 OR id:2503.14476 OR id:2305.18290) AND submittedDate:[202410250616 TO 202610250616]
```

CCF A 入口为四篇人工核实种子，不能据此宣称覆盖全部 CCF A 会议论文。时间条件随每次检索更新。

两个研究模块实际检索记录分别为 `d2c8af20-25e7-4af0-a60a-d5c2a46721c2` 与 `cb6a3c80-b4e8-4deb-81e6-9934a04f551d`；候选数是上游当时响应的总量，不代表已全部获取或评估。

## 界面与 PDF

实际使用 Chrome 验证发现页、详情全文、章节筛选、双语对照、术语表保存、论文对比、任务状态及手机导航。模型按钮在无密钥时明确禁用并说明原因；待译块保持空白，不显示演示译文。

1440×1000 桌面与 390×844 手机视口均无页面横向溢出。修复了长正文卡片在滚动容器中被压缩的问题。

- [桌面截图](screenshots/stage2-desktop.jpg)
- [手机截图](screenshots/stage2-mobile.jpg)
- [两篇论文的 PDF 对比示例](../output/pdf/paperpilot-stage2-comparison.pdf)：2 页，中文、摘要、会议来源与未知项可提取，两页均已渲染检查。
- DAPO 全文报告实际生成，最终为 38 页；包含首尾段落 ID、来源位置和解析警告。抽查第 3 页并修正标题、位置行与正文的分页衔接。全文未逐页人工校对，也不是原论文版式重建。

## 仍需凭据或后续工作的内容

真实模型精读与翻译需要后端 `LLM_API_KEY`、`LLM_MODEL`。模拟测试证明 schema、证据校验、逐块持久化和恢复协议能执行，**不证明真实模型的语义正确性或翻译质量**。配置后应先用摘要或短论文进行真实验收。

复杂 TeX 宏、图表视觉布局和 PDF 扫描页不做猜测；当前无 OCR、完整 TeX 排版、全量会议采集、语义召回和模型重排。阶段三订阅周报未实现。

验证临时数据库与 PDF 渲染文件在交付前清理；业务数据库、阶段一备份、运行日志、最终 PDF 和截图保留。未执行 Git commit 或 push。
