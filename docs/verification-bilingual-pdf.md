# 双语 PDF 字体与排版验收

日期：2026-10-25。论文：Group-Graph Policy Optimization for Long-Horizon Agentic Reinforcement Learning，arXiv 2606.22995v2。

- 后端针对性测试：`python -m pytest tests/test_bilingual_typesetting.py tests/test_bilingual_pdf_style.py tests/test_translation_downloads.py -q`，17 passed。
- 实际验收：`backend/.venv/Scripts/python.exe scripts/verify_bilingual_pdf_style.py`，HTTP 200、Range 206、重复下载字节一致，文件名为原论文标题加“翻译版.pdf”。
- 排版：单栏 49 页，中文 SimSun 12 pt、英文 Times New Roman 11.5 pt、20 pt 行距；新排文字黑色，页脚仅页码；无 LaTeX 源码命令、无 Overfull 警告。
- 保留 10 张原图，逐字节校验一致；64 条参考文献；92 组正文双语对照。
- 原翻译 141 条检查点哈希与修改前一致；本次导出无模型调用、无新任务；原中文版 PDF 哈希不变。
- 渲染检查：正文、图表、参考文献/附录页面，预览位于 output/validation/bilingual-preview。
- 产物：output/bilingual/Group-Graph Policy Optimization for Long-Horizon Agentic Reinforcement Learning翻译版.pdf。

完整审计数据：output/validation/bilingual-pdf-style.json。图内文字和参考文献保留原文；自然分页不保证与原论文页数一致。其他只有 PDF 文本的论文仍使用文本导出路径，不保证图表/数学版式。
