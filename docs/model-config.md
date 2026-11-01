# 页面模型配置与字体调整验收

日期：2026-10-25。

## 使用方式

打开「研究偏好 → 模型与数据连接 → 大模型配置」，填写 API 基地址、模型名称和密钥。先测试，再保存；测试使用表单草稿，保存成功后清空密钥输入。留空保留已有密钥，勾选清除并保存可停用模型能力。

测试会发起一次真实的短 JSON 生成调用，可能产生少量费用，不自动重试。地址变更不能复用留空的旧密钥。配置冲突时需重新加载；重新加载会丢弃当前未保存草稿。

密钥只持久保存在后端数据库同目录的 `model-config.json`。默认位于已被 Git 忽略的 `data/`；文件包含明文凭据，按 `.env` 同等保护。已保存密钥不会通过 GET、健康检查、Skill 列表或错误响应回传。前端不写浏览器存储，只在输入期间临时持有该值。

保存后的配置优先于 `.env` 的 API 地址、模型名称、密钥三项，API 和独立 worker 即时读取。其他模型协议开关仍在 `.env` 设置并通过重启生效。请勿在运行长文任务时切换模型；已有任务保留模型和服务地址身份。

## 改动文件

- `backend/app/model_config.py`：配置查询、写入、真实连接测试、版本冲突与脱敏验证。
- `backend/app/config.py`：读取后端配置，独立 worker 动态感知；密钥从 Settings repr 排除。
- `backend/app/llm.py`：复用已有传输逻辑测试草稿，支持注入独立设置。
- `backend/app/main.py`：注册配置路由。
- `frontend/src/ModelSettings.tsx`、`App.tsx`：表单、加载/错误状态、测试/保存/清除操作。
- `frontend/src/styles.css`：基础字体 14px，摘要 14px，全文 15px，桌面输入框 14px、手机输入框 16px；标题和按钮适度增大。
- `backend/tests/test_model_config.py`：新增 15 项测试；没有新增依赖或改动锁文件。

## 实际验证

在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_model_config.py -q --basetemp '..\data\pytest-model-settings' --tb=short
.\.venv\Scripts\python.exe -m pytest -q --basetemp '..\data\pytest-model-regression' --tb=short
```

结果分别为 **15 passed in 18.15s**、**71 passed in 82.51s**。覆盖密钥持久化且不回传、独立子进程重新读取、空值保留/清除、并发版本冲突、地址变更保护、非法输入不回显、跨站请求拒绝，以及连接成功、401/429/500/重定向/超时等协议结果。成功场景使用明确的 MockTransport，不代表真实服务认证成功。

前端执行 `npm.cmd run build`，类型检查和生产构建通过。实际重启本项目 API 与 worker，现有论文与任务数据保持不变。

Chrome 实际验证：

- 配置页加载当前后端状态，原有配置仍为未配置，无真实密钥被读取或修改。
- 使用临时测试值连接本机关闭的端口，出现「连接测试中…」，随后显示真实连接失败提示；没有保存草稿。
- 点击重新加载后，地址恢复、密钥输入为空。
- 桌面内容宽度 1521px，无横向溢出；390×844 手机视口下内容宽度 375px，同样无横向溢出，输入字体 16px。
- [手机截图](screenshots/model-settings-mobile.png)。测试结束恢复默认视口，页面保留在研究偏好。

真实模型密钥尚未配置，因此没有进行付费服务的成功连接或论文翻译验收。测试临时数据在交付前清理，现有业务数据和阶段二交付产物保留。
