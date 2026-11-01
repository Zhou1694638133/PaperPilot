# 研究方向内的 CCF A 优先检索

“CCF A 已核实入口”卡片已移除。发现论文默认选择“跨来源检索 · CCF A 优先”，两个重点研究模块点击后也使用该策略。

1. 按完整主题表达式和日期条件，从 arXiv 获取最多 50 篇相关候选。
2. 索引近三年的 NeurIPS 主会、ICML 正式论文集（排除 Position 标题）和 ACL 主会长文；实际年份窗口受日期条件限制。PMLR 卷号从官方目录发现，不猜测年份对应的卷号。
3. 根据主题在论文集标题中召回最多 12 个补充候选，并用“完整原查询 AND 候选标题”请求 arXiv。未成功匹配 arXiv 的论文不伪造摘要、作者或首次公开日期，不直接加入阅读列表。
4. 在正式论文页面核对标题和至少一位完整作者姓名后，保存主会证据；排除 Workshop、Findings、短文和其他轨道。原有人工核实记录可继续作为已有证据，但不再是单独检索入口。
5. 按 arXiv ID 合并版本，遵守首次公开日期、排除词及不感兴趣反馈；在本轮有界候选池内将已核实 CCF A 排在前面，再按兴趣匹配分排序与分页。
6. 没有已核实且满足筛选条件的 CCF A 论文时，自动显示相关 arXiv 论文，标明本轮回退，不推断全网不存在相关会议论文。全部来源失败时明确显示错误。

页面的来源记录列出论文集可用状态、索引数量、实际补充检索式和召回数量。检索记录持久保存这些信息。本轮候选池不代表完整全网结果，列表总数指筛选后的候选数量。选择其他排序方式时仍可使用原有 arXiv 分页。

## 来源与边界

- NeurIPS：https://proceedings.neurips.cc/ ，仅明确主会路径 Abstract-Conference。
- ICML：https://proceedings.mlr.press/ ，目录声明 International Conference on Machine Learning 的正式卷。
- ACL：https://aclanthology.org/ ，仅 acl-long 标识。
- CCF 分级沿用第七版（2026）目录：https://www.ccf.org.cn/Academic_Evaluation/By_category/ 。本实现尚未覆盖全部 CCF A 会议。
- Google Scholar：https://scholar.google.com/intl/uk/scholar/help.html 。遵守其自动访问限制，提供同主题链接供用户打开，不声称自动抓取、核实或导入了结果。

官方索引缓存 24 小时，失败短缓存 5 分钟；主题候选池缓存 30 分钟；基础 arXiv 请求失败时仅缓存 5 分钟。每个来源限制并发、响应大小与下载时间，重定向限同一允许域名。来源不执行代码、不触发模型调用、不新增依赖。某年论文集尚未发布或网络不可用时保留 arXiv 结果并显示覆盖缺口。

对“全部时间”检索，arXiv 可返回较旧论文，但自动会议索引仅覆盖最近三个会议年份；未匹配的论文保持录用未核实。录用优先不是质量概率。

## 本次验证（2026-10-25）

- `python -m pytest tests/test_conference_discovery.py tests/test_core.py tests/test_fulltext.py -q`：46 项通过（最初回归批次）。
- `python -m pytest tests/test_conference_discovery.py -q`：最终 10 项通过，补充无已核实会议论文回退和正常零结果测试。
- `npm.cmd run build`：TypeScript 与 Vite 构建通过。
- 真实 Agent 强化学习检索：索引 23,920 条正式论文记录，返回 53 篇去重候选，其中 6 篇具备核实证据。原始覆盖记录见 `output/validation/conference-discovery.json`。
- 实际覆盖缺口：NeurIPS 2026 指定主会索引返回 404；ACL 2026 事件索引超过 16 MiB 下载上限。这些来源未被推断为已核实。首次索引和核验可能较慢，后续复用缓存。

- 运行中的 HTTP 接口实测 `id:2606.22995`：200，返回 1 篇，`priority_status=arxiv_fallback`，明确显示回退原因。记录见 `output/validation/conference-fallback-api.json`。

补充核验采用 60 秒阶段预算，已发出的单次请求仍受自身超时约束。预算用尽后不再发起额外核验，保留 arXiv 结果；这不是整个 HTTP 请求的硬性 60 秒超时。候选论文和评分分别批量保存，减少大候选池的数据库写锁竞争。

最终批量持久化回归：`pytest tests/test_conference_discovery.py tests/test_core.py tests/test_fulltext.py -q` 共 48 项通过。补充耗时预算后会议检索专项 11 项通过。浏览器实测已显示 53 条候选，前 6 条为有正式来源证据的 CCF A 论文，之后保留未核实 arXiv 论文。

联调期间并发冷启动曾出现 SQLite 锁冲突和 HTTP 等待超时；已改为批量持久化、缓存有效候选池及补充核验预算。冷缓存完整来源索引仍可能较慢，尚未声明端到端延迟保证。无 CCF A 的回退不取消日期及个人筛选，也不能在 arXiv 本身失败且无缓存时伪造结果。
