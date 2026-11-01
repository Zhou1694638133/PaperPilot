# 总体分析与模型周报验收记录

验收区间：2026-10-29 至 2026-10-31（北京时间）。所有真实模型验证使用独立数据库，未修改用户文库、订阅、研究偏好或翻译检查点。未新增依赖、未更改锁文件。工作目录不是 Git 仓库，未执行提交或推送。

## 交付内容

- `backend/app/paper_analysis.py`：`paper_analysis`（1.0.0）和 `analysis_pdf`（1.0.2）两个实际 runner。独立分析/排版，全文分块、综合、原文证据、收到响应先保存、按源文件定义还原安全宏。
- `backend/app/weekly_semantic.py`、`subscriptions.py`、`semantic.py`：默认关闭的模型增强周报、本订阅主题评分、1–10 候选上限、精确上下文缓存和失败降级。
- `backend/app/feedback_evaluation.py`：真实用户标签与当前关键词/语义结果的探索性对照。不足30个配对样本不显示指标，不替用户制造标签。
- `jobs.py`、`skills.py`、`reading_api.py`：任务 schema、注册表、runner 和独立 PDF 下载路由。
- `frontend/src/OverallAnalysis.tsx`、`ReadingWorkspace.tsx`、`StageThree.tsx`、`App.tsx`、`components.tsx`：总体分析标签、证据展开、任务恢复、下载、订阅模型配置和反馈对照。
- `skills/paper_analysis/SKILL.md`、`skills/analysis_pdf/SKILL.md`，更新 `skills/weekly_digest/SKILL.md`。
- `backend/tests/test_paper_analysis.py`、`test_weekly_semantic.py`；`scripts/verify_analysis_weekly.py`；操作说明 `docs/analysis-and-weekly-model.md`。

## 自动验证

1. `python -m pytest tests/test_paper_analysis.py tests/test_weekly_semantic.py tests/test_subscriptions.py tests/test_semantic.py -q`：首轮联合回归 41 passed。
2. 随后增加通用多证据、宏还原/响应缓存、反馈导出与受限公式排版测试。新功能两文件共11项，分批验证均通过。中途一项测试误用 `jobs.claim`，已改成实际接口 `claim_job`，复跑该项和公式测试为 2 passed；其余9项此前通过。
3. 最后定向重跑：两个模板、额外证据合法性、受限公式和周报日期/去重/无密钥/下载，5 passed。`python -m compileall -q app` 通过。
4. `npm.cmd run build`：TypeScript 检查及 Vite 生产构建通过。中途反馈类型闭合符错误已修复后重建。

以上不是声称全项目全量测试均执行。未增加测试专用依赖。

## 真实模型与论文

模型：用户已配置的 `deepseek-flash`；密钥只在进程内使用，没有写入验收文件。

论文：Group-Graph Policy Optimization for Long-Horizon Agentic Reinforcement Learning，arXiv 2606.22995 v2，首次公开 2026-06-22，处于默认近两年范围。

- 使用已有真实全文缓存，141个解析块，8组分块分析后分层综合。
- 最终包含研究问题、方法、作者创新主张、实验与结果、局限、可借鉴研究思路；引用66个原文块。
- 所有建议标记为 proposal，作者结果与分析推断分开；证据存在不等于事实已经独立验证。
- 真实验收暴露了过严的固定证据数量限制和原文自定义宏：已改为原文ID集合逐项校验，并安全展开真实定义。模型原始响应先持久保存，验证器修正复用响应和成功块。
- 重新编译 PDF 不再调用模型，实际无模型密钥环境下完成排版验证。

## PDF 与界面

- 原论文按正文识别为单栏，自动选择现有单栏翻译模板；实际导出5页。
- 双栏模板额外独立编译为4页，作为模板检查，不将它冒称该论文的自动选择结果。
- 两种模板均使用嵌入的 SimSun 宋体，黑色正文；数学上下标采用受限语法排版。证据章节名的自定义宏也已还原，不显示未排版 LaTeX 命令。
- 实际调用下载接口返回200、application/pdf、有效 `%PDF` 文件头；Content-Disposition 使用原论文名称 + `总体分析.pdf`。
- 在隔离界面4173实际验证：总体分析标签与六项结果、Markdown/PDF入口、模型开关默认关闭、默认近两年/CCF A、候选上限展开、反馈0样本时无指标。
- 浏览器实际点击总体分析 PDF 下载成功。最终修正版保存于本目录的 `论文总体分析.pdf`，以该文件为准。

## 周报

- Agent强化学习与后训练各一份，单份最多2次真实模型调用，两份共4次，均成功通过摘要证据校验。
- 每篇保留关键词分、主题语义分、推荐理由和原文证据；上限外明确显示关键词降级。CCF A 优先级和首次公开近730天过滤保留。
- 周报使用此前已检索的真实论文和会议证据快照回放，并未重新联网检索。报告显式记录该限制，模型请求是真实执行。
- 跨报告缓存复用、重复提交、反馈加分/隐藏、无密钥、模型失败由自动回归验证。

## 文件与边界

本次正式输出保留在 `output/analysis-weekly-verification/`。`analysis-job.json`、`analysis-pdf-job.json`、两份周报 JSON、下载响应记录、字体/内容检查及预览可核对。

模型增强需主动保存开启；更新后重启 API 和 worker，再刷新浏览器。原有订阅不会自动开启模型。仍需积累30–50篇真实用户标注，不能据4次模型调用声称推荐准确率已经提升。

总体分析覆盖已解析文本，不保证复杂图表、未解析内容或模型推断无误；混合栏式或无法可靠确认栏式时明确报错。PDF是分析报告，不是原论文逐页复刻。
