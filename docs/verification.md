# 阶段一实际验收记录

日期：2026-10-25（Asia/Shanghai）。这是已执行记录，不是计划或模拟成绩。

## 自动化验证

| 检查 | 实际命令 / 方法 | 结果 |
| --- | --- | --- |
| 后端全部回归 | `.venv\Scripts\python.exe -m pytest -q --basetemp ..\data\pytest-final --tb=short`（在 backend 执行） | 35 passed，50.96 秒 |
| 前端类型 | `npm.cmd run typecheck`（frontend） | 通过 |
| 生产构建 | `npm.cmd run build`（内含 `tsc --noEmit` 与 `vite build`） | 通过，18 modules |
| 前端依赖审计 | `npm.cmd install` 的 npm audit | 0 vulnerabilities |
| 后端导入 | Python 3.12 导入 FastAPI、Pydantic、HTTPX、LangGraph、SqliteSaver | 通过 |
| 独立 worker | pytest 启动实际 `python -m app.worker --once` 子进程读取持久队列 | 通过 |

测试覆盖 canonical ID/版本去重、首次公开日期过滤、非法来源字段、中文词典与手动检索式、引用原文核对、模式隔离、收藏重启保留、缓存与限流、过期缓存提示、BibTeX 转义、无密钥降级、跨站写入、请求幂等、租约恢复、取消与重试、最多三次执行、模型结果待确认、持久 LangGraph checkpoint、SSE 游标重放及终态竞态、逐段翻译恢复、伪造引用拒绝、公式/引用/数字保护、负号与科学计数法、模型接口失败脱敏、模型提供方或研究兴趣变化。

系统默认 TEMP 目录曾返回 ACL 权限拒绝；改用项目内 `--basetemp` 后测试正常。测试临时目录在验收结束后清理。

## 真实外部数据

以下请求确实访问 arXiv，并非 Mock 或示例替换：

- `id:2308.08155`：返回 AutoGen v2，首次公开 2023-08-16、更新 2023-10-03，明确区分两种日期。
- `all:"multi-agent"`，按首次公开时间排序，`start=0/3, limit=3`：两页 HTTP 200，当时来源候选总数 18,148，跨页 ID 无重复。
- `all:"agent"`，`days=7, limit=2`：HTTP 200，实际表达式包含 `submittedDate:[202610180543 TO 202610250543]`，返回论文的首次公开时间均在窗口内。
- 浏览器默认 LLM 多智能体组合检索：返回 10 篇完整真实摘要，匹配分按当前页的规则计算。

来源数量和排序会随 arXiv 更新而改变；这些数字只记录本次观察，不作为产品固定指标。

## 运行与浏览器联调

API、独立 worker 和 Vite 在回环地址运行。健康检查返回 `status=ok`、`worker.online=true`、`model_configured=false`。服务重启后再次读取，收藏、已读、标签、笔记和已完成任务仍然存在。

浏览器实际操作：

- 真实论文打开详情，核对作者、摘要、首次公开/更新日期、来源版本链接和未核实录用提示。
- 收藏、标为已读、保存标签与笔记；刷新浏览器后仍存在。
- 运行规则推荐解释；UI 经 SSE 从排队切到“已完成 / 100%”。产物有持久 checkpoint ID。
- Markdown 文库导出、BibTeX 导出和任务 Markdown 产物接口均 HTTP 200，下载响应包含 Content-Disposition。浏览器导出点击已发出 HTTP 200；自动化工具的 download 事件等待曾超时，因此未依赖该回调判断文件下载成功。
- Skills 页显示两个模型能力未配置，规则能力可用；模型按钮禁用并有配置说明。
- 研究偏好逐字输入 `large language model, agent`，空格正常保留，保存成功。验收后已恢复原始空包含词。
- Live/Demo 明确切换，演示旧论文选择最近 7 天后得到 0 条。
- 手机导航抽屉、Copilot 抽屉和关闭操作可用。
- 桌面 1440×1000：document/body scrollWidth=1425（含浏览器竖向滚动条差异），无横向溢出。
- 手机 390×844：document/body scrollWidth=375，无横向溢出；标题、摘要、Skill 与操作按钮均可阅读。
- 正常交互时浏览器控制台未观察到应用 error/warn。重启 API 时 Vite 记录过一次预期的 ECONNRESET，重连后接口恢复。

验收写入的临时收藏、已读、标签和笔记已撤销。真实检索缓存与一份实际规则解释任务留作可检查的运行结果。

截图：[桌面工作台](screenshots/desktop-discover.jpg)、[手机 Copilot](screenshots/mobile-copilot.jpg)。

## 尚未验证 / 未实现

- 没有用户模型密钥，因此**未执行真实收费模型调用**；翻译与 Reader 的协议、schema、恢复和保护验证使用 Mock，不能代表真实模型输出质量。
- 推荐质量没有研究者人工标注集，不报告虚构的 Precision@K、nDCG、token 成本或优越性结论。
- 全文读取/翻译、PDF 导出、论文比较、embedding、模型重排、额外数据源、订阅和周报尚未完成。
- PostgreSQL 是已划分的迁移边界，不是已运行的第二数据库；多用户、公网认证和货币预算执行器尚未实现。
