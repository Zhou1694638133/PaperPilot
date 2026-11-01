# 本地论文真实翻译与 PDF 验证（2026-10-31）

论文：Why Do Multi-Agent LLM Systems Fail?（2503.13657v3）。原上传 PDF 已核对并恢复同版本结构化源文件。用户已授权首次模型翻译。

## 交付产物

- 中文版：E:\Agent\PaperAgent\PaperPilot\output\pdf\local-mast\中文版\Why Do Multi-Agent LLM Systems Fail-翻译版.pdf；42 页；SHA256 `6d518cd99165a8c8bfed102862cd84ecfe1b70bb35caeb0747da2d3e45d11d5c`。
- 双语版：E:\Agent\PaperAgent\PaperPilot\output\pdf\local-mast\双语版\Why Do Multi-Agent LLM Systems Fail-翻译版.pdf；83 页；SHA256 `fd2c1d3e14a62a2432066d4526678068b00b443c1fd77f27ce852fc6c8483034`。

## 实际验证

- 已配置模型完成 229 个正文检查点；排版复用 104 个补译检查点，8 处人工核对修订独立保存在来源 hash 绑定的审校记录中。
- 229 个正文检查点在排版前后 hash 一致；备份中的 406 个历史翻译检查点逐条一致。
- 两版均保留 11 个逐字节一致的原始矢量 PDF 图件、9 张表格及其数字、29 处源文行内公式。
- 中文保留 3 个灯泡图标；双语原译配对保留 6 个。SimSun、FontAwesome 字体已嵌入。
- 双语导出 model_calls = 0。未为最后几轮排版重复翻译正文。
- 全页联系表检查并重点核对图、表、图标、正文第一页、附录代码和修订标题。未发现图件缺失或可见文字裁切。
- 中文编译无布局警告；双语两张表存在约 0.94–5.19 pt 的局部 hbox 警告，记录保留于 final-audit.json，视觉检查未见裁切。

## 修复范围

- backend/app/skills.py、translation_policy.py：LaTeX 引号、模型标识、保护与译文校验。
- backend/app/chinese_pdf.py、bilingual_pdf.py、pdf_compat.py：原文表格/提示框参数恢复、术语宏补译、作者姓名保护、来源绑定审校、代码样式参数解析及保护范围隔离、双语模型编号完整合并。
- backend/tests/test_translation_syntax.py、test_pdf_compat.py、test_chinese_pdf.py、test_bilingual_typesetting.py：对应回归覆盖。
- skills/paper_translator、skills/chinese_pdf：同步规范；未修改用户已有外部 Skill。
- scripts/verify_local_pdf_delivery.py、scripts/render_local_pdf_review.py：可重复执行的实际产物审计及页面渲染。

## 执行命令

在项目根目录 PowerShell 执行：

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_pdf_compat.py backend/tests/test_chinese_pdf.py backend/tests/test_bilingual_typesetting.py --basetemp tmp/pytest-delivery-final-53 -q
.\backend\.venv\Scripts\python.exe scripts/verify_local_pdf_delivery.py
.\backend\.venv\Scripts\python.exe scripts/render_local_pdf_review.py
```

最终相关回归：53 passed。系统默认 pytest 临时目录最初权限错误，改用项目内 basetemp 后全部通过。两份 PDF 由实际 Tectonic 编译。

## 保留与限制

- 原图内部的英文、参考文献、代码、提示词及运行日志保持原样；中文正文、标题、图注等采用黑色宋体。
- 原文没有独立 equation/algorithm 环境；此次数学验证针对实际存在的 29 处行内公式，不虚报算法排版验证。
- 本次是结构、来源、数值、字体及视觉校验，并非逐句人工翻译认证。
- 不依赖重新翻译即可复现本次最终排版。译文、源文件、任务历史及数据库备份均保留。
- 后端及 worker 已重新启动加载修复。未进行浏览器自动化验收。
