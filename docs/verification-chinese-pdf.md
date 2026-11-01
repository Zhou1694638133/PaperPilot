# 中文论文 PDF 实际验证

验证日期：2026-10-25。论文：G2PO，arXiv 2606.22995v2。

- 真实 worker 生成黑色宋体中文论文 PDF，原图、公式、算法和参考文献保留。
- G2PO 原 PDF 第 2–9 页正文识别为单栏，中文版跟随使用单栏；测试覆盖双栏、首页通栏摘要、显式配置回退、证据冲突和混合栏数拒绝。中文版自然分页，不强求与英文页数相同。
- 10 个原图文件与 arXiv 源包逐字节一致，64 条参考文献。
- 141 个原有正文翻译检查点的完整行数据 SHA-256 与修改前一致；没有重翻正文。
- 网络恢复后已完成真实模型补译；最终排版复用全部补译检查点，无新增模型请求。
- 下载 HTTP 200，字节范围请求 HTTP 206，通用 PDF 下载路由内容一致，重复提交返回原任务。
- 已移除重复章节页眉，页码保留在页脚；列表取消圆点并保持分项段落。验收脚本检查所有页面页眉区域无文字、无列表圆点。
- 已检查首页、图表、长公式、算法及全文缩略图；最终审计见 `output/validation/chinese-pdf-validation.json`。
- 开发过程中完整后端测试 116 项通过；最终中文 PDF 针对性回归测试 29 项通过。前端 `npm.cmd run build` 通过。完整后端套件未在最后的排版小改后重复执行。

## 复跑

从项目目录运行，保持 API、worker 正常运行：

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m pytest tests/test_chinese_pdf.py tests/test_paper_layout.py tests/test_translation_downloads.py -q --basetemp ('.test-chinese-' + [guid]::NewGuid().ToString())
.\.venv\Scripts\python.exe ..\scripts\verify_chinese_pdf.py
.\.venv\Scripts\python.exe ..\scripts\verify_translation_downloads.py
Pop-Location
Push-Location frontend
npm.cmd run build
Pop-Location
```

验证脚本读取 `data/chinese-validation-job.txt` 指向的已完成任务；包含本次论文的检查点和图片断言，不调用模型、不修改数据库，成功后保存下载的 PDF 和审计 JSON。

原图内部英文、作者姓名、必要缩写、代码标识以及参考文献保留。当前图文中文版要求可解析的 LaTeX 源文件；仅有 PDF 文本的论文暂不保证完整图文排版。

固定模板与最后验收的单栏、双栏导言区逐字一致；两份模板及 SHA-256 注册在 `skills/chinese_pdf/`。实际下载验证中文版 PDF、双语 PDF、Markdown 均返回“原论文标题翻译版”文件名（UTF-8 Content-Disposition），HTTP 200 / Range 206 正常，中文版 PDF 字节未变，无新任务。记录见 `output/validation/translation-download-names.json`。
