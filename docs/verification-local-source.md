# 本地保图源文件恢复验证 · 2026-10-31

正式库只读取得本地原件，在 output/local-source-verification/runtime 独立数据库执行，未修改正式收藏、论文、模型配置和译文检查点，模型调用为 0。

真实 PDF：arXiv:2503.13657v3，共47页。核对同版本 PDF 后成功取得源包，解析229段/保护块，保存11个原图 PDF资产。原上传字节完全保留。结果与图片清单见 output/local-source-verification/result.json。

更新：backend/app/local_source.py 与对应 Skill、任务注册/runner、本地中文和双语排版的原 PDF 选择、严格上下文下逐字相同检查点复用、前端恢复按钮及任务标签。新增引用格式的安全适配：纯字体包装宏、行内 verb 代码、unsrtnat、BibTeX 条目外杂文剥离和生成 bbl 的重复排版避免。

验证命令（backend）：
- python -m pytest tests/test_local_source.py tests/test_source_enrichment.py tests/test_chinese_pdf.py -q：38通过。
- python -m pytest tests/test_pdf_compat.py tests/test_bilingual_typesetting.py tests/test_bilingual_pdf_style.py tests/test_translation_downloads.py -q：45通过。

验证命令（frontend）：
- node --test tests/translationAction.test.ts tests/translationDownloads.test.mjs：10通过。
- npm.cmd run build：通过。

2026-10-31：用户已允许保留图标所需字体包。Tectonic 实际加载 fontawesome，中文模板和双语模板均成功编译该论文源文件，分别为48页和96页，均嵌入11个原始PDF图像资源，字体包含SimSun和FontAwesome。实际检查原图页和灯泡页，图像及图标可见。修复了上传文件名下划线造成标题编译失败、自定义提示框颜色缺失，以及双语大字号下模型名称行宽溢出。冻结模板保持1.0.8；渲染修订为1.0.2，双语渲染为1.0.7。

复现：项目根目录执行 `backend\.venv\Scripts\python.exe scripts/verify_local_source_layout.py`（先运行源文件恢复验证脚本生成隔离缓存）。报告见 `output/local-source-verification/layout-verification.json`。这是以原文填充的**排版验证件**，不是中文译文；不调用模型、不写入翻译检查点或正式数据库。双语测试的“中文”位置使用原文，只验证版式和资源管线，不证明翻译质量。

剩余排版警告：少数原文表格/长字符串有约0.94–5.25 pt轻微行宽提示（两轮编译重复记录）；大幅行宽溢出已消除。编译未出现缺字或未解析引用错误。任意LaTeX论文仍可能含其他不支持命令；没有可核验arXiv同版本源文件时明确失败，不声称文字版能完整保图。


最终后端回归：`python -m pytest tests/test_local_source.py tests/test_source_enrichment.py tests/test_chinese_pdf.py tests/test_pdf_compat.py tests/test_bilingual_typesetting.py tests/test_bilingual_pdf_style.py tests/test_translation_downloads.py -q`：86通过（140.63秒）。随后新增软换行测试单独执行通过：`python -m pytest tests/test_local_source.py::test_long_literal_identifiers_can_wrap_without_changing_text -q`：1通过。共87项相关测试通过。

更新后重启API和worker并刷新前端；本地论文在“中文翻译”选择“恢复原图并启用图文版”，核对恢复完成后继续全文翻译/图文PDF生成。完全相同正文检查点复用，无法逐字对应的新分块不会猜测继承。此次验证不自动为用户发起新的付费翻译。
