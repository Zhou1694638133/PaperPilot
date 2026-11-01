# API 简表

所有路径以 `/api` 开头，默认回环地址 `127.0.0.1:8000`。完整实时 schema 见 `/docs`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | API、模式、模型是否配置、worker 心跳、来源适配状态 |
| GET / PUT | `/profile` | 结构化偏好，修改需 JSON |
| POST | `/query` | `{text}` 转词典关键词、同义词与可编辑检索式 |
| GET | `/papers` | `query,start,limit,days,mode,sort` 检索 |
| GET | `/papers/{id}` | 已检索论文详情；`mode=live|demo` |
| GET | `/collections` | 当前模式的收藏清单 |
| PUT | `/collections/{id}` | 部分更新 `saved,read,interested,tags,notes` |
| GET | `/export` | `ids` 逗号分隔、`format=markdown|bibtex`、`mode`；空 ids 导出文库 |
| GET | `/skills` | 可信 runner 注册表、版本、可用状态和 schema |
| POST | `/jobs` | `skill_id,paper_id,mode`，可附 `idempotency_key,glossary,language,scope` |
| GET | `/jobs` | 最近 100 个任务和 worker 状态 |
| GET | `/jobs/{id}` | 任务快照、真实状态与产物 |
| GET | `/jobs/{id}/events` | SSE：`id` 与完整 JSON 快照，`after`/`Last-Event-ID` 重放 |
| POST | `/jobs/{id}/cancel` | 取消排队任务或请求停止当前任务 |
| POST | `/jobs/{id}/retry` | 有限重试，结果待确认时需 `{confirm_duplicate_cost:true}` |
| GET | `/jobs/{id}/artifact` | 已完成 Markdown 产物 |

`days=0` 表示全部时间，其余按首次公开日期过滤；`sort=match` 只对当前来源页排序。`total` 在 live 模式为 arXiv 候选总数，`filtered_count` 是本页筛选结果数，分页使用 `has_more`/`next_start`。实际含日期约束的检索式、获取时间、缓存和过期状态均随结果返回。

任务状态为 `queued / running / succeeded / failed / cancelled / needs_confirmation`。`progress` 由已执行后台步骤决定，不表示模型内部完成比例或预计剩余时长。

API 错误使用 HTTP 状态和 `detail`：例如输入无效 422、来源限流 429、来源错误 502、模型未配置 503、输入冲突 409。未知录用、代码、数据和实验信息不会在缺失时补造。


## 阶段二增量接口

- `GET /api/research-modules`：Agent 强化学习、后训练检索式，以及有来源证据的 CCF A 种子查询。
- `GET /api/papers` 默认 `days=730, sort=ccfa`；`ccfa` 仅在当前来源页内优先核实会议，匹配分不改变。`days=0` 可显式查询历史论文。返回 `publication` 附正式会议证据与目录来源。
- `POST /api/jobs` 增加 `skill_id=fulltext_fetch` 和 `scope=fulltext`。全文 Reader/Translator 要先读取文档；有警告时必须 `acknowledge_partial=true`。未配置模型返回 503；演示全文返回 409。
- `GET /api/fulltext?paper_id=...&mode=live`：当前论文版本与解析器版本的文档和已有精读。没有文档时 `document=null`。
- `GET/PUT /api/glossary`：`{version, terms}`；保存递增版本，旧版本提交返回 409。
- `POST /api/glossary/learn`：无需请求体，为已收藏、已读或有标签/笔记的真实论文补交术语任务；排除不感兴趣论文，返回 `{eligible_count, jobs, warnings}`。已有任务按输入复用，失败任务通过原重试接口处理。
- `PUT /api/collections/{id}`：标记保存后自动尝试提交术语提取，返回论文并附 `terminology: {job, message}`；未配置模型不影响保存标记。
- `POST /api/jobs` 支持 `skill_id=glossary_extractor`：仅处理已标记真实论文的标题与摘要；每篇最多提取 20 个术语，校验原文依据后去重合并。完成结果包含 `terms, added, conflicts, skipped_capacity, glossary_version`，每个候选包含 `source, target, evidence, location`。已有译法保留，达到共享术语表 100 条上限时不覆盖其他条目。
- `GET /api/jobs/{id}/alignment`：冻结原文、已完成译文、每块状态、术语版本、真实 completed/total；未译块 `translation=null`。
- `POST /api/compare`：`{ids: [2–4 个不同 ID], mode}`；返回元数据、摘要、已有精读与覆盖范围，无模型也可用。
- `GET /api/reports/pdf?paper_id=...&mode=live`：论文研究报告。加 `scope=fulltext` 包含已解析全文。
- `GET /api/reports/pdf?job_id=...`：已完成任务的 PDF 产物。
- `GET /api/reports/pdf?ids=id1,id2&mode=live`：对比 PDF。三个选择参数互斥。

旧 `/api/export` Markdown/BibTeX、收藏、任务取消/重试和 SSE 路由不变。已保存的密钥不会回传到前端。

## 模型配置

- `GET /api/model-config`：返回 `base_url, model, has_api_key, configured, revision, source`，不返回密钥或掩码片段。
- `PUT /api/model-config`：提交 `{base_url, model, api_key?, clear_key?, revision}`，原子保存到数据库同目录的 `model-config.json`。空密钥保留旧值，清除优先；更换地址不能复用留空的旧密钥。`revision` 使用上次 GET 返回的值，冲突返回 409。
- `POST /api/model-config/test`：同一输入 schema，测试表单草稿或当前后端密钥，20 秒超时、128 输出 token、不自动重试、不保存配置。成功返回 `{ok:true,message,usage}`；鉴权失败、限流、超时或不兼容返回脱敏错误。

配置写入和测试仅接受 JSON，拒绝跨站 Origin；验证错误不会回显提交值。连接测试可能产生少量费用，不证明长文任务的最终质量。API 和 worker 在读取模型设置时即时读取后端配置；`.env` 仍为未保存页面配置时的默认来源。
