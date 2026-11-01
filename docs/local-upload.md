# 本地 PDF 上传与翻译

侧栏“本地论文” → 选择 PDF → 上传并解析 → 打开已上传论文 → 核对原文摘要 → 摘要分析/摘要翻译，或在下方全文工作台精读、翻译 → 双语对照下载。发现论文页面保留在线检索，不再放置上传表单。

上传文件会自动保存至我的文库。重新打开文库可继续查看任务与检查点；任务取消、重试及 SSE 断线恢复沿用现有流程。没有模型配置时仍可上传/解析，翻译需要研究偏好中的模型配置。点击翻译后仅将待处理文本发送至所配置的模型服务。

支持：文本型 PDF、最多 20 MiB、150 页。超限、损坏、加密、扫描页和乱码明确报错或警告。扫描件尚不支持 OCR，Word/LaTeX 压缩包暂不支持本地导入。PDF 文本提取不能保证图表/公式/顺序；下载原 PDF 供核对，不提供保留原版式的纯中文版。本地译版使用黑色宋体；翻译完成后在双语对照页可下载“中文版 PDF（文字版）”、双语 PDF 和 Markdown。文字版仅输出已有译文，不重译、不调用模型，不含原文对照或任务说明；原图与原版式仍需查看原 PDF。英文原标题和需保留的专名、代码、参考文献不会强行改写。

原件存入现有 SQLite fulltext_assets，论文 ID 为 local-加 SHA-256；没有用户输入文件路径写入磁盘。相同内容复用论文、解析和翻译任务，文件改名不触发重译。上传自动收藏但不自动调用模型或提取术语。标题来自文件名，未核实元数据为空，未纳入 arXiv 时间过滤或 CCF 录用声称。

接口：POST /api/local-papers?filename=论文.pdf，Content-Type: application/pdf，正文为原始文件字节；流式检查大小，沿用跨站写入保护，不需 multipart 新依赖。响应 paper、job、reused。GET /api/local-papers/{paper_id}/file 返回原件。解析结果及任务继续通过既有 /api/fulltext、/api/jobs/{id}/alignment 和 events 读取。

验证：backend 运行 `python -m pytest tests/test_local_papers.py tests/test_fulltext.py tests/test_translation_downloads.py -q`。项目根目录运行 `backend/.venv/Scripts/python.exe scripts/verify_local_upload.py --live-model`，在独立临时数据库用独立 worker 调用当前模型翻译单段验收样例，不改变正式论文数据；不传 --live-model 时只验证上传解析。验收产物写入 output/validation/local-upload，密钥不写入产物。

## 本次验收（2026-10-25）

43 项相关测试通过，npm run build 通过。独立 worker 使用真实模型完成单段 PDF 样例翻译，双语对照 1/1，PDF 与 Markdown 下载均为 200，SSE 终态回放和重复提交复用均通过；正式 API 拒绝伪 PDF（422），worker 在线。浏览器验证文件选择、按钮启用及 390px 宽度无横向溢出。测试使用独立数据库，不更改原论文检查点。实际记录见 output/validation/local-upload/audit.json。

## 独立本地论文页面（2026-10-26）

已上传列表独立于收藏状态，取消收藏不会丢失上传记录。刷新页面后从列表重新打开论文即可恢复阅读和任务状态。本地页面、论文卡片和导出清单不展示推荐评分或推荐理由；在线论文继续使用原推荐功能。

摘要仅按明确的 Abstract/摘要标题识别候选，需要用户核对保存。后端验证文本确实是已解析原文中的连续摘录并记录来源；未识别时不会用正文猜补摘要。无模型时仍可上传、解析、保存摘要和阅读，模型按钮明确显示未配置。修改摘要仅创建对应的新摘要任务，不会改变已有全文翻译的任务标识和检查点。

新增接口：
- `GET /api/local-papers`：上传历史。
- `GET /api/local-papers/{paper_id}/abstract`：摘要候选、前三页原文及文档校验值。
- `PUT /api/local-papers/{paper_id}/abstract`：提交 `text` 与 `document_hash`，校验原文摘录后保存。

验证命令（backend 目录）：`./.venv/Scripts/python.exe -m pytest tests/test_local_papers.py tests/test_translation_downloads.py tests/test_core.py -q`，35 项通过。frontend 目录 `npm run build` 包含 TypeScript 检查。独立数据库实测 HTTP 上传、worker 解析、页面摘要确认保存和刷新恢复；390px 宽度无横向溢出。模型分析与翻译使用测试桩验证，此次未调用付费模型，也未修改正式论文或翻译检查点。

使用 PyCharm 时，重新运行模块 `uvicorn`（参数 `app.main:app --host 127.0.0.1 --port 8000`），并重新运行独立模块 `app.worker`；二者工作目录均为 backend、解释器均为后端 uv 环境。随后刷新前端页面。本次开发没有停止用户正在运行的 PyCharm 服务。

## 中文版文字下载验证（2026-10-31）

新增下载接口复用已有对齐检查点；无密钥仍可下载，未完成/部分完成任务拒绝完整导出。运行 python -m pytest tests/test_translation_downloads.py tests/test_local_papers.py -q，27 项通过；npm.cmd run build 通过。隔离 HTTP 测试实际返回有效中文 PDF，核对中文内容、无重复英文正文、文件名及下载前后模型调用数和完成检查点不变。本次模型使用测试桩，未调用付费模型。重启 API 并刷新前端即可使用；无需重译。



## 中文翻译与双语对照分离（2026-10-31）

全文工作台提供独立的“中文翻译”和“双语对照”标签，读取同一任务的段落检查点，不重复翻译。中文标签只显示译文（必要保护块保留原文）及中文版 PDF；双语标签显示原文/译文、双语 PDF 和双语 Markdown。

“全文获取 · 已完成”只表示原文已解析，不表示翻译完成。PDF 解析存在警告时，需在“开始全文翻译”附近勾选已核对警告；按钮旁明确显示缺少模型、尚未读取全文、等待其他任务等原因。失败/部分完成任务通过原任务恢复入口只补未完成部分。现有中文版文字 PDF 不保证图像、公式与原版式，原件入口保持可用。

验证：frontend 目录执行 `node --test tests/translationAction.test.ts tests/translationDownloads.test.mjs tests/jobState.test.ts`，13 项通过（包括组件实际服务端渲染、中文/双语链接隔离、未完成禁止下载、无密钥读取已有产物、警告确认和续译状态）；`npm.cmd run build` 通过。本次未调用模型、未修改收藏或译文检查点。前端刷新后生效，无需重启 worker。


## 保留原图的本地论文翻译

本地论文 → “恢复原图与源文件”（中文翻译标签也有“恢复原图并启用图文版”）→ 核对源文件解析警告 → 开始全文翻译 → 中文翻译生成图文 PDF／双语对照下载图文 PDF。

恢复任务由独立 worker 执行，不需要模型。仅根据 PDF 首页唯一带版本的 arXiv 标识获取同版本原 PDF，整文件 SHA-256 或逐页正文一致后才获取 LaTeX 源包。验证路径、大小和所有引用图片后更新当前文档；失败保留此前解析结果。原上传 PDF、收藏、历史翻译任务与检查点不删除。

源码分段和 PDF 按页提取不同，不能把整页译文拼到任意 LaTeX 段落。仅自动复用正文完全一致、同模型/provider/提示词版本/术语配置的检查点；其他段落需重新翻译。恢复过程不自动发起翻译。

恢复成功后沿用已有中文／双语单栏和双栏模板，按本地原 PDF 判定栏式，保留源包中的图、公式、表格和算法。当前无 arXiv 版本标识、源文件缺失、PDF 版本不一致或不支持的 TeX 结构会明确阻塞；不冒充图文版、不静默删图。文字版仅作为备用。

新增 Skill：skills/local_source_fetch/SKILL.md，执行逻辑 backend/app/local_source.py。更新后重启 API 和 worker，刷新前端。
