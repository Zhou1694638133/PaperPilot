---
name: analysis_pdf
description: 将已完成的论文总体分析按原文单栏或双栏翻译模板导出为黑色宋体 PDF，无需再次调用模型。
version: 1.0.2
---

# 总体分析 PDF

runner：`backend/app/paper_analysis.py:export_pdf`；输入 schema：`jobs.JobInput`，输出 schema：`skills.SkillResult`（附 `analysis_job_id`、`template_id`、`column_layout`、`pdf_url` 与排版警告）。

调用 `POST /api/jobs`：`skill_id=analysis_pdf`、`paper_id`、`scope=fulltext`、`analysis_job_id`。必须是当前论文版本和全文 hash 的已完成 `paper_analysis` 任务。无需模型密钥；编译依赖沿用已经安装的项目内 Tectonic。

严格遵守 [总体分析规范](../paper_analysis/SKILL.md) 的格式约定。实际读取 `chinese_pdf/templates/single-column.tex` 或 `two-column.tex`；黑色宋体、原文栏式，不另造模板。仅转义后的纯文字进入 TeX，不接受模型命令。

编译失败只重试排版，不重做总体分析。下载 `GET /api/jobs/{pdf_job_id}/analysis.pdf`；文件名原论文名称 + `总体分析.pdf`。未完成不提供伪成功文件。

简单变量上下标只按应用固定语法排版（如 A^EC、G_k），其他文本严格转义；不执行模型生成的 TeX 命令。排版版本独立进入任务身份，更新模板不会重做模型分析。

证据章节名也按原文定义还原安全宏；不能还原的命令使用“原文章节（见文件位置）”，保留精确文件位置而不泄露未排版源码。
