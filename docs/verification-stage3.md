# 阶段三验收 · 2026-10-29

## 实现

- 三个独立页面：主题订阅、站内周报、推荐反馈；桌面与手机布局。
- 主动开启每周计划、暂停/恢复、配置编辑、手动生成。默认首次公开近 730 天。
- Agent RL / 后训练聚焦检索式；CCF A 正式来源优先，未找到时回退 arXiv。
- 独立 worker + LangGraph + SQLite + 持久 SSE；事务幂等、来源检查点、重启补本周、取消/显式重试。
- 周报保留日期、版本、实际检索式、会议来源、证据、关键词分、未知项；可阅读、标已读、下载 Markdown、打开既有论文工作台。
- 可撤回反馈独立影响排序/隐藏；原关键词分和语义分不变；本地论文不参与。
- 新 Skill weekly_digest v1.0.0 已注册实际 runner 与输入/输出 schema。

## 自动验证

所有测试使用独立数据库，无付费模型调用。

| 命令 | 结果 |
| --- | --- |
| backend: .venv/Scripts/python.exe -m pytest -q --basetemp=.test-stage3-full | 261 passed，268.71 秒 |
| 最终改动后：.venv/Scripts/python.exe -m pytest tests/test_subscriptions.py -q --basetemp=.test-stage3-final | 9 passed，31.25 秒 |
| frontend: npm.cmd run build | TypeScript 检查及 Vite 生产构建通过 |

新测试覆盖默认不启用、近两年边界、未来论文排除、首次日期而非更新日期过滤、版本去重、UTC+8 周次、并发幂等、CCF 来源优先、反馈撤回及 live/demo 隔离、失败不产生假周报、检索检查点复用、暂停后显式重试、内容版本和新收录去重、原评分保留。

## 实际在线运行

单独启动 API 8000、Vite 5173、独立 worker，连接 .stage3-live/test.db；模型配置为空，不使用用户 data/model-config.json。

1. Agent RL / CCF A：真实查询 id:2505.10978，首次公开 2025-05-16，v3，GiGPO；核验 NeurIPS 2025 正式页面，生成 1 篇周报。
2. 后训练 / arXiv：真实查询 id:2503.14476，首次公开 2025-03-18，v2，DAPO；生成 1 篇周报。
3. 两次查询均保存 submittedDate 日期范围；原文摘要证据可追溯。NeurIPS 2026 来源 404 被保留为覆盖警告，未虚构完整覆盖。
4. 两份 Markdown 下载接口返回成功，分别 1997 / 1633 字节。浏览器实际下载后训练报告到 Downloads，下载入口正常。
5. 两任务各有 8 个持久 SSE 事件；使用 Last-Event-ID 恢复后收到成功状态。
6. 无密钥的真实 Reader 提交返回 503；周报仍可正常生成，明确显示规则周报且未调用模型。
7. 重启本次测试 API / worker：到期订阅生成一份本周报告，重复手动提交返回同一任务；最后所有测试订阅已暂停。

真实证据：[live-verification.json](../output/stage3/live-verification.json)、[restart-verification.json](../output/stage3/restart-verification.json)。

## 浏览器验证

- 创建暂停订阅、生成英文检索式（含后训练预设）、提交周报、SSE 状态更新。
- 阅读已完成报告、已读状态、来源链接、推荐依据、Markdown 下载。
- 保存相关性反馈、独立反馈列表持久记录、撤回后的空列表。
- 从周报打开论文详情；原摘要、全文读取、翻译及导出入口保留；缺密钥按钮禁用并说明。
- 手机 390 × 844：文档宽度 375，小于视口 390，无横向溢出；桌面 1440 × 1000 显示三项导航。
- 浏览器 error 日志为空。移动与桌面截图保存在 output/stage3。

## 变更范围与边界

核心新增：backend/app/subscriptions.py、backend/tests/test_subscriptions.py、frontend/src/StageThree.tsx、frontend/src/FeedbackControl.tsx、skills/weekly_digest/SKILL.md。
集成：repository.py、main.py、worker.py、jobs.py、skills.py、App.tsx、components.tsx、types.ts、styles.css；README 与阶段三文档同步。
没有新增依赖或改锁文件；没有重翻论文、删除文库、修改模型配置或已有检查点。未初始化 Git、commit 或 push。

周报当前采用证据规则汇总，不自动生成模型周总结；模型精读/翻译仍需用户主动配置并运行。反馈是每篇论文的可解释调整，并非训练推荐模型。CCF A 仅覆盖已接入的正式来源；有界候选池不保证检索完整性。测试服务和临时数据库会在验收后停止/清理，使用真实文库前请按 docs/stage3.md 重启 API、worker、前端。

