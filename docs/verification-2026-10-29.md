# 2026-10-29 复查记录

## 修复范围

- 中文/双语补译共用 supplement_groups；每次递归切分裁剪代码范围并换算偏移，保留旧分组编号、文本和来源 hash。
- 拼接已有子块时不再额外插入换行，避免在 TeX 表格或代码中制造新段落。
- 跨段 tcolorbox 先重组成完整环境，再分别排版英文与中文；保留中间公式/代码，未闭合或双语边界不一致时拒绝输出。
- 无编号 align* 通过 aligned 和已有 fitdisplay 按栏宽缩放；有显式标签/编号的结构保持原样。
- plain/ICML 文献样式适配为 plainnat，保留原始 .bib 的作者元数据，修复 citet 显示 author?。
- 中文 PDF Skill 清单的模板版本和 SHA-256 与真实模板同步。
- 任务中心补上 chinese_pdf 的正确名称，避免误显示为“推荐依据说明”。
- 任务列表先选择最近 100 个 ID，再取完整行并在内存按时间排序，避免 SQLite 对整个冻结文档/结果排序；不新增索引或迁移数据库。

## 修改文件

- backend/app/chinese_pdf.py
- backend/app/bilingual_pdf.py
- backend/app/pdf_compat.py
- backend/app/jobs.py
- frontend/src/components.tsx
- backend/tests/test_pdf_compat.py
- backend/tests/test_bilingual_typesetting.py
- backend/tests/test_chinese_pdf.py
- backend/tests/test_jobs.py
- skills/chinese_pdf/manifest.json
- skills/chinese_pdf/SKILL.md
- skills/paper_translator/SKILL.md
- README.md

## 验证方式

```powershell
Set-Location 'E:\Agent\PaperAgent\PaperPilot\backend'
.\.venv\Scripts\python.exe -m pytest -q --basetemp .test-audit-verified --tb=short
Set-Location '..\frontend'
npm.cmd run build
Set-Location '..'
.\backend\.venv\Scripts\python.exe scripts/verify_translation_preflight.py
```

复现测试先失败后通过：子块额外换行、跨段提示框、无编号宽公式、参考文献样式。任务列表另验证最近 100 条顺序契约。
前端 build 包括 TypeScript 检查；真实界面验证任务名称和本地论文/任务中心导航。
缓存语料：5 篇论文、6 份文档、868 块，结构往返 0 失败、0 阻塞。
真实论文 2605.18077：中文 31 页，双语 70 页；原图保留，抽查表格和跨段提示框。作者引用占位检查为 0。
所有生成过程禁用模型入口；translation_chunks 和 analysis_chunks 全表内容 hash 前后相同。未重翻整篇或改写译文检查点。
机器可读结果位于 output/validation/audit-20261029/report.json，接口检查另见 http.json。

## 边界

本轮未重新调用外部模型或验证所有论文翻译语义。不同论文的未支持宏仍会明确报错，不能保证所有来源模板均兼容。
双语版表 B.3 留有约 1.13 pt 的内部竖向盒子警告；已渲染第 57 页核对，未见文字缺失或页面裁切。
任务列表在本机并发编译/测试时仍有读取延迟（单次约 23 秒）；查询优化不等于完成分页或所有性能优化。
渲染修订 1.0.1 独立进入任务快照和幂等键，付费译文缓存版本不变。已有历史 PDF 不自动覆盖；应重新生成排版产物以应用新修复，译文检查点继续复用。
临时验证 API 关闭启动写入、未启动 worker，不消费用户任务队列。当前目录不是 Git 仓库，没有提交或覆盖版本历史。

## 最终结果

- 完整后端回归：252 passed（568.75 秒）。渲染版本清单另行复核 9 passed。
- 最新排版相关回归：53 passed；含任务恢复/列表模块的回归 91 passed。
- npm.cmd run build：TypeScript 与 Vite 生产构建成功。
- 中文 PDF、Markdown、双语 PDF 的真实路由返回 200；双语 PDF Range 请求返回 206，响应文件 hash 与最终编译产物一致。
- API 健康、Skill、偏好接口和前端页面/代理返回 200。任务列表初次 15 秒请求超时，后续页面加载成功；保留性能限制记录，不将查询改写描述为彻底解决延迟。
- 临时服务与测试目录已清理；用户 worker 未启动，历史任务和译文未覆盖。

最终 PDF 位于 output/pdf/audit-20261029/chinese 与 bilingual，文件名均为原论文英文标题加“翻译版”。重新启动自己的 API 和 worker 后，重新提交 PDF 排版可使用新渲染修订；无需重新翻译全文。
