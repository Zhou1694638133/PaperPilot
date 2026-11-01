# PaperPilot 架构与阶段边界

阶段一是绑定回环地址的单用户研究工作台。React/TypeScript/Vite 前端只访问同源 `/api`，开发时由 Vite 代理 FastAPI。密钥只由后端 `.env` 读取，不进入页面、构建产物或 API 响应。现有根目录 `index.html` 仍是原始静态演示参考；实际应用入口为 `frontend/`。

## 数据与推荐

arXiv 适配器读取 Atom 元数据，保存原始字段、版本、首次公开时间与更新时间。canonical ID 不带版本；收藏、标签和笔记独立存储，刷新元数据不会覆盖它们。live 与 demo 数据以模式隔离。没有网络结果时显示错误，不能用示例替代。日期筛选只使用 `published`。

推荐先执行日期、包含词和排除词等规则，再按可解释的关键词特征排序。分数只表示当前偏好下的排序信号。每条结果保存研究兴趣、原始摘要句子、分项权重、算法版本、检索运行 ID 和未知信息。阶段一不宣称已做 embedding 语义召回、模型重排或全文实验核验。OpenReview 与 Semantic Scholar 使用独立适配器边界，当前尚未启用。

## 持久任务

FastAPI 只创建任务。独立 `python -m app.worker` 进程原子领取任务，持有可续期租约，向 SQLite 写入真实事件。SSE 读取事件日志，支持事件游标重放，页面恢复时可以先取任务快照。取消、失败、待确认、完成是不同状态。后台没有依赖浏览器定时器的模拟进度。

Supervisor 使用 LangGraph 和 SQLite checkpointer；任务 ID 与 graph thread ID 关联。偏好和文库由业务数据库长期保存，不依赖图内存。摘要翻译按段保存稳定标识、原文、译文和检查点，输入与版本变化使缓存失效。

模型请求超时、进程在请求发出后退出等情况可能无法判断外部服务是否已计费。此时进入 `needs_confirmation`，不会自动再次收费调用；用户明确确认后才能重试。该设计不承诺跨外部服务“绝对一次”执行。

## SQLite 与迁移方向

业务数据库访问封装在 repository 与 jobs 模块，API 不直接依赖数据库连接。schema version 管理当前结构，论文版本、收藏、推荐、任务、事件、产物独立保存。第一版 worker 并发限定为 1；这不是高并发生产调度器。

迁移 PostgreSQL 时替换连接和参数绑定层、迁移各表及 JSON 数据，将任务原子领取改为事务内 `FOR UPDATE SKIP LOCKED`，替换共享限流锁与 LangGraph checkpointer。上线多用户前还需认证、用户隔离、HTTPS、迁移工具和部署级访问控制。当前不应直接绑定公网。

## 后续阶段

- 阶段二已实现：结构化源文件优先、PDF 回退、全文解析质量检查、分块精读、术语表版本管理、全文段落对齐与断点续译、论文比较和 PDF 导出。详细边界见 [stage2.md](stage2.md)。
- 阶段三：用户主动配置的主题订阅、站内周报、推荐反馈闭环。当前不发送邮件，不创建外部订阅。

## 核对的官方协议

- [arXiv API 查询手册](https://info.arxiv.org/help/api/user-manual.html)：Atom、分页、submittedDate、版本与返回字段。
- [arXiv API 使用要求](https://info.arxiv.org/help/api/tou.html)：单连接与共享请求节奏。多个独立安装无法由本应用协调，部署时仍需全局遵守访问限制。
- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)：checkpointer 保存 thread 状态，长期业务数据另行保存。
- [Vite 运行时要求](https://vite.dev/guide/) 与 [Tailwind Vite 插件](https://tailwindcss.com/docs/installation/using-vite)。

实际依赖版本以 `frontend/package-lock.json` 和 `backend/uv.lock` 为准。


## 阶段二实现

`fulltext.py` 负责受限下载、私有源缓存、安全源包读取、LaTeX/PDF 块与文档 hash；`deep_reading.py` 负责模型引文校验与分块精读检查点；`reading_api.py` 提供文档、术语、对齐、对比和报告入口。`pdf_export.py` 用 ReportLab 生成中文分页报告。前端 `ReadingWorkspace.tsx` 承载全文、术语、对比和研究模块。

全文与摘要共用持久队列和 LangGraph Supervisor，任务创建时冻结文档与术语快照。`analysis_chunks` 保存精读块，`translation_chunks` 保存原译对齐；所有有计费歧义的请求继续沿用阶段一的确认机制。
