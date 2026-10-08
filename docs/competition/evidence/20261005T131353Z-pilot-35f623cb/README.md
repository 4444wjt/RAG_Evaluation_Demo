# 修复后5题技术验证：公开证据

运行ID：`20261005T131353Z-pilot-35f623cb`。这是一次经用户单独授权的单模型5题草稿技术验证，**不是正式25题实验、作者人工确认或旧冻结Demo复跑**。没有为PASS修改结果；本轮运行完成，门禁为FAIL。

| 项目 | 原记录 |
|---|---|
| 范围 | D06、C02、C05、N01、F03 |
| 兼容层与依赖 | zh-sentence-v1，Ragas 0.1.21 |
| 请求／实际响应模型 | deepseek-chat／deepseek-flash，65次响应一致 |
| 执行 | HTTP 200，5条回答，81.33秒，65次完整配对调用 |
| 中文提示核对 | 15条句子抽取提示完整包含中文输入，5条参考答案均非空 |
| 覆盖 | 五指标各5/5有效；Trace 3条非空、2条空，共9条陈述 |
| 门禁 | 正向Faithfulness 0.6000低于0.8；正向Context Precision 0.9792达标 |
| 用量 | 输入106,679＋输出5,317＝111,996 token |
| 费用 | 按返回模型公开价估算约0.045元；无账单，实际扣费未知 |
| 人工确认 | 尚未完成；本轮AI离线分析不替代作者确认 |

## 阅读顺序

1. [简短分析](analysis.md)：FAIL输入、空Trace实际返回、新旧差异和适用限制。
2. [可回查分析数据](analysis.json)：完整拒答、检索上下文、调用ID、逐条分数和覆盖数量；属于离线分析，不是人工核验包。
3. [原运行manifest](manifest.json)、[授权范围](run-authorization.json)、[实际配置](runtime.json)、[提示词定义](prompt-definitions.json)：版本、模型、阈值、来源与预算边界。
4. [API返回](api-response.json)、[调用日志](llm-events.jsonl)、[运行日志](server.log)：原回答、机器判定、原始生成及警告。
5. [中文输入核对](technical-validation.json)、[用量](usage.json)、[费用估算](cost-estimate.json)、[结果覆盖](result-coverage.json)。
6. [HTML报告](project/ragas_report.html)、[逐题CSV](project/ragas_report_deepseek-chat.csv)、[汇总CSV](project/multi_model_summary.csv)、[图表](project/result_chart.png)。
7. [公开来源清单](public-manifest.json)、[外部公开校验清单](SHA256SUMS.txt)；全部公开文件都有校验，清单自身排除。

## 来源与校验

保留原目录布局，含本轮使用的backend及main源码、runner、依赖、冻结输入、请求、响应和完整结果。runner是在本次运行前已冻结的副本；不同于[旧版预跑](../20261005T101847Z-pilot-c72cb679/README.md)的事后hash匹配补存。

原run及其checksums.json不修改。原manifest和checksums.json逐字节保留，描述**未脱敏原件**；公开副本以public-manifest和SHA256SUMS为准，不能用原checksums直接验证脱敏文件。仅API的chart_path/html_path改为相对路径，server.log中4处机器归档根路径改为`<ARCHIVE>`。public-manifest分别记录原件与公开文件hash。

README、analysis及public-manifest是本次离线整理新增说明，不是新评测结果。不复制.env、凭据、Chroma、Embedding权重、venv或Python缓存，不上传远端。

## 仍未完成

25题作者确认和正式付费授权；独立第三方核验；模型不可变权重版本及Embedding实际加载来源证明；实际扣费账单。两条空Trace已有成功返回证据，仍不能据此证明拒答正确或裁判正确。

下一步仅按[25题最终启动清单](../../formal-run-checklist.md)核对；这次5题和2元期望预算授权不能自动扩展到25题。
