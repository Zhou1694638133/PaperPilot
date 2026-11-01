# 阶段三：已配置模型的真实验证（2026-10-29）

## 范围与环境

使用用户已保存在后端的 deepseek-flash 配置。凭据仅在进程内读取，未复制密钥文件、未输出密钥、未更换服务商或持久网络配置。正式 API/worker 保持运行，正式数据库、偏好、收藏和翻译检查点未修改。

独立数据库生成 Agent 强化学习、大模型后训练两份真实周报，每份选取 4 篇。首次公开日期默认 730 天，CCF A 正式会议证据优先，其他论文保持 arXiv / 录用未核实。不将更新日期或会议年份替代首次公开日期。

初始连接经环境代理 TLS 失败，直连也超时；用户明确回复网络已恢复后，同一配置连接成功。提供方响应记录与脱敏错误见 output/stage3-model-verification/connection*.json。

## 实测与修复

1. 初版模型将 PostTrainBench 评为 Agent 强化学习 62 分，理由实际描述的是“用 Agent 做后训练”，并无训练 Agent 策略的摘要证据。补充具体领域边界后，降至 12 分，后训练方向保留 88 分。这是此次小样本的方向区分改善，不是统计准确率结论。
2. Rubric-ARM、DistFlow 初次输出将论文标题作为摘要引文，被校验拒绝。semantic-v2 改为优先选择摘要证据编号，由普通代码还原原文和位置；标题、未知编号和伪造引文仍拒绝。
3. 一次 PMD-mean 输出包含 4 条真实引文，被原先 3 条硬上限拒绝。目标仍为 1–3 条，允许最多 8 条且全部逐一校验；不截断隐藏不合法的末尾证据。修正后直接复核捕获的真实响应，保留原提供方 request ID，无新模型调用。
4. 中途有一次 TLS 故障发生在连接建立前，失败状态明确保留；仅对失败项显式重试，成功结果不重复计费。

最终 8 篇全部成功，16 项研究兴趣判断，45 条原文证据校验通过。8 个最终保留响应共 13,932 token；这是最终响应的用量，不包含所有调试请求，不能当作总账单。

| 论文 | Agent RL | 后训练 | 摘要复核判断 |
| --- | ---: | ---: | --- |
| GiGPO | 95 | 78 | 多轮 Agent RL 与细粒度信用分配 |
| SkillScaffold | 95 | 78 | 技能效用、选择性注入与 Agent RL |
| TreeHCA | 92 | 85 | 多轮探索与信用分配 |
| My FAULT | 92 | 70 | 自诊断引导的 Agent RL 信用重分配 |
| PostTrainBench | 12 | 88 | 后训练自动化评测，不是 Agent RL 算法 |
| PMD-mean | 10 | 88 | 通用 LLM 后训练策略优化，缺少交互 Agent 场景证据 |
| Rubric-ARM | 15 | 88 | 偏好反馈下的奖励建模 |
| DistFlow | 15 | 78 | 分布式后训练基础设施 |

四篇 Agent RL 论文在该方向均为核心相关；后训练四篇涵盖算法、奖励建模、系统、评测，应依据具体课题进一步筛选。分数尚未用研究者标注集校准；不能把单次 8 篇复核描述为整体准确率。

## 来源复核

GiGPO 的 NeurIPS 2025 主会来源：
https://proceedings.neurips.cc/paper_files/paper/2025/hash/420c9f777c0b4f78d515e53cf74d58b2-Abstract-Conference.html

后训练四篇的 ICML 2026 正式来源：
- https://proceedings.mlr.press/v306/rank26a.html
- https://proceedings.mlr.press/v306/xu26am.html
- https://proceedings.mlr.press/v306/xu26ai.html
- https://proceedings.mlr.press/v306/wang26jl.html

三篇未核实会议的论文保持 arXiv：2610.18807、2610.12828、2610.01161。首次公开日期和摘要另以原 arXiv 页面核对。

注意：PostTrainBench、PMD-mean 的会议摘要与所取 arXiv 版本有文本/数值差别。会议证据用于核实录用，模型分析仍基于所保存的 arXiv 摘要，不混用两个版本的实验结论。

## 反馈与周报验证方法

两份初始周报通过真实来源适配器、持久队列、Supervisor 和 worker 生成；重复提交返回同一任务。模型评分也经实际队列和 runner 执行，每篇同时评估两个方向，并验证再次提交复用同一语义任务。

反馈对照固定为各份周报已经抓取的 4 篇真实论文快照，重放检索边界以排除新论文、分页及来源缓存刷新造成的变化。不是演示论文，也没有伪造模型分数。实际反馈 API、数据库、评分函数、检索排序、worker、新周报生成仍正常执行。

最终反馈断言结果见 output/stage3-model-verification/feedback-checks.json。测试通过相关反馈 +5、撤回恢复、下一配置版本周报隐藏和撤回恢复、历史 Markdown 不被改写等断言。原始关键词和语义分不变。

## 重要产品边界

自动周报目前仍是规则周报，模型已配置也不会自动付费调用。此次语义结果是用户主动要求的模型复核补充，下载文件明确注明这一点，并未悄悄加入订阅定时调用。

反馈是逐篇排序加减或隐藏，不是模型在线训练；不会因此让模型自动学会用户的总体偏好。不同兴趣的聚合分用于发现页，复核表提供各方向独立分数，避免把总体分误认为特定方向分数。

当前来源适配器是有界候选池，NeurIPS/ICML/ACL 覆盖并非完整 CCF A 目录；部分年份来源不可用等警告保留在原报告。相关性不能替代论文质量判断或全文实验复核。

## 文件与验证命令

修改 backend/app/semantic.py、backend/app/skills.py、backend/tests/test_semantic.py；新增 skills/semantic_ranker/SKILL.md 与显式联网验证脚本 scripts/verify_stage3_model.py，更新 docs/semantic-matching.md。无新依赖、无数据库结构变更，未修改前端。

backend 目录：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_semantic.py tests/test_subscriptions.py -q --basetemp=.test-stage3-live-model --tb=short
# 31 passed in 568.03s
.\.venv\Scripts\python.exe -m pytest tests/test_semantic.py -k extra_evidence -q --basetemp=.test-stage3-extra-evidence --tb=short
# 3 passed, 16 deselected in 46.64s
```

项目根目录（模型阶段可能计费，需显式 --run-model）：

```powershell
.\backend\.venv\Scripts\python.exe -X utf8 scripts/verify_stage3_model.py --phase reports
.\backend\.venv\Scripts\python.exe -X utf8 scripts/verify_stage3_model.py --phase model --run-model
.\backend\.venv\Scripts\python.exe -X utf8 scripts/verify_stage3_model.py --phase feedback
```

诊断与 replay 阶段只供复核已保存响应，不会把未校验内容当作成功结果。

升级后需重启 API 与 worker；已有旧提示版本结果保留，新语义任务需用户主动点击运行。不要为此次更新重翻译已有论文。


## 最终反馈实测结果

| 对照 | 原排序分 | 相关反馈后 | 撤回后 | 名次变化 |
| --- | ---: | ---: | ---: | --- |
| My FAULT（Agent RL） | 88.7 | 93.7 | 88.7 | 4 → 2 → 4 |
| DistFlow（后训练） | 68.5 | 73.5 | 68.5 | 4 → 4 → 4 |

DistFlow 加分后仍低于同组其他论文，所以名次没有变化，这是正常排序结果；并非反馈无效。两项的原始语义结果完全不变。

固定 Agent RL 候选集中，将唯一已核实 CCF A 的 GiGPO 标记不相关后，新周报只保留 arXiv 论文并明确回退；撤回后恢复。后训练组隐藏 DistFlow 后后续报告不再收录，撤回后恢复。两份历史周报 Markdown 原样保留。

4 份后续周报快照保存在 output/stage3-model-verification/feedback-report-snapshots.json。结束时隔离数据库内没有活动任务，也没有未撤回的测试反馈。临时数据库与测试目录随后清理；正式后端与 worker 继续运行。
