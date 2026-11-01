# G2PO 源文件补全验收（2026-10-25）

论文：Group-Graph Policy Optimization for Long-Horizon Agentic Reinforcement Learning，arXiv:2606.22995v2。

## 本次结果

- `neurips_2026.tex:51` 明确声明 `\newcommand{\MethodName}{G2PO}`。正文/保护块中 67 次使用在展示层安全展开；章节标题同步显示 G2PO。没有执行 TeX 或加载源包代码。
- 从[同版本原 PDF](https://arxiv.org/pdf/2606.22995v2) 第 10–14 页提取完整编号 `[1]`–`[64]`。每条保存原始提取行、页码、规范化文本哈希和原 PDF 链接；不翻译、不推断遗漏年份或出版信息。
- 原有 141 条 `translation_chunks` 全字段逐字未变，包括源文本、译文、源哈希、请求 ID 和生成时间。冻结的任务输入和文档原始块、文档哈希也保持不变，尝试次数仍为 3。本次模型调用为 0。
- 以独立 `source_enrichment` 补充来源数据；API 明确区分原检查点 141/141 与 64 条参考文献，合计 205 个展示块。显示层展开不覆写原文与译文；页面提供“查看原始 LaTeX 与检查点”控件。
- 两条原解析提示已解决，实际限制转为明确的补全说明。原始 LangGraph 快照、术语校订快照、当前结果及审计数据均保留；任务、产物和 SSE 事件已同步更新。
- 最新双语 PDF 为 **109 页**，保留作者、来源和 CC BY 4.0 说明。改善参考文献分页，使标题、页码、来源与正文连接。原精读 PDF 未改动。

## 验证

在项目根目录：

```powershell
.\backend\.venv\Scripts\python.exe scripts\enrich_g2po_source.py
.\backend\.venv\Scripts\python.exe scripts\enrich_g2po_source.py --apply
.\backend\.venv\Scripts\python.exe scripts\enrich_g2po_source.py --export
.\backend\.venv\Scripts\python.exe scripts\verify_g2po_revision.py
.\backend\.venv\Scripts\python.exe -m compileall -q backend\app\source_enrichment.py backend\app\reading_api.py scripts\enrich_g2po_source.py scripts\verify_g2po_revision.py
```

只读预演、保存后逐字段比对、实际 API 对齐和导出检查均通过。复用已保存补全时校验源包、原 PDF 和补全数据哈希，不重新解析或调用模型。之前的 19 条术语校订也再次通过核验。

在 `backend` 目录：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_source_enrichment.py tests\test_fulltext.py tests\test_term_revision.py -q --basetemp '..\data\pytest-source-final' --tb=short
```

在 `frontend` 目录：`npm.cmd run build` 成功，包含 TypeScript 类型检查。

最终上述后端回归结果为 **41 passed in 120.24s**。此前源补全针对性检查为 11 项通过；本次最终回归已包括额外的 API 补充计数和版本拒绝测试。没有重跑无关的全量检索测试。

浏览器实际验证：参考文献章节有 64 项，首尾编号正确、页码链接指向原 PDF；宏展开后正文不显示 `\MethodName`，原始检查点折叠区仍保留该原始标记；手机宽度无横向溢出。原结构化精读 5 个栏目、48 条证据仍可查看。

原 PDF 第 10–14 页已渲染核对；最终双语 PDF 全文文本检查通过，并渲染抽查第 1、2、3、90、94、109 页。首尾参考文献和宏还原中文正文可读，无剪裁或重叠。未逐页人工校对全部 109 页。

## 涉及文件

- `backend/app/source_enrichment.py`：受限的宏定义解析、参考文献边界/编号检查、可追溯展示补充。
- `backend/app/reading_api.py`：全文与双语 API、全文 PDF 使用同一展示补充。
- `backend/app/pdf_export.py`：来源段与后续正文保持分页关联。
- `frontend/src/ReadingWorkspace.tsx`、`frontend/src/types.ts`：补全说明、参考文献来源、原检查点查看和计数区分。
- `scripts/enrich_g2po_source.py`、`scripts/verify_g2po_revision.py`：审核后应用、导出与验证；没有数据库 schema 迁移。
- `backend/tests/test_source_enrichment.py`：安全宏、异常参考文献、实际机构作者紧贴编号的 PDF 情况、检查点不变及版本不匹配拒绝。

没有新增依赖、改变模型密钥、提交或推送 Git。只重启本项目 API 加载变更，worker 保留运行。

## 产物和边界

- [双语 PDF](../output/pdf/g2po-bilingual.pdf)、[双语 Markdown](../output/validation/g2po-bilingual.md)、[双语对齐数据](../output/validation/g2po-alignment.json)
- [源补全数据](../output/validation/g2po-source-enrichment.json)、[实际核验指标](../output/validation/g2po-source-enrichment-results.json)
- [补全前后审计](../output/validation/g2po-source-enrichment-audit.json)、[缓存原 PDF](../output/validation/g2po-source.pdf)

安全展开仅支持无参数的明确纯文本宏；不支持、重定义或歧义宏拒绝猜测。标准 LaTeX 公式、引用命令和图表未编译成完整排版。参考文献仅规范空白，PDF 原有断词和字体切换造成的字间距问题保留；原始提取行和 PDF 页码可复核。新增文献没有重新送入模型精读，原精读仍覆盖原来的 141 个解析块。
