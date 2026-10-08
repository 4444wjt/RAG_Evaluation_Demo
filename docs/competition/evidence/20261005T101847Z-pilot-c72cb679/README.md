# 5题真实 Web API 预跑：公开证据副本

## 这份材料证明什么

这是一轮真实模型调用的原始材料公开副本，来自运行 `20261005T101847Z-pilot-c72cb679`。当前 Web API 通过 HTTP `POST /evaluate` 完成5题，生成回答、五项指标、Trace、门禁、CSV及HTML报告。

**身份：中文句子兼容修正前（pre-fix）的5题草稿消耗预跑。** 用户授权用待确认题目观察调用量和token；本轮不能当作25题正式实验、中文兼容修正后的验证或总体准确率证明。

| 项目 | 实际记录 |
|---|---|
| 运行日期 | 2026-10-05；开始与结束时间以原manifest中的UTC时间为准 |
| API状态 | HTTP 200，5条回答完整返回 |
| 题目ID | D06、C02、C05、N01、F03 |
| 请求模型ID | `deepseek-chat` |
| 提供方返回模型名 | 本轮64个完成事件均为 `deepseek-flash` |
| 模型调用 | 64个唯一、完整配对的start/end；未记录error事件 |
| token | 输入102,375；输出4,580；合计106,955 |
| 输入缓存 | 命中40,955；未命中61,420；缓存数据来自原事件记录 |
| 实际费用 | 未取得账单，不用token记录代替扣费证明 |
| 参考材料人工确认 | 原case-manifest及run manifest均为pending／未确认 |
| Faithfulness | 5条中2条缺失；4条正向回答中1条缺失 |
| Trace | 5条中2条为空，其余3条合计7条机器陈述 |
| 门禁 | 保存原PASS结果；没有调整阈值或改写机器判定 |

生成回答、Ragas指标裁判和Trace裁判使用同一个模型，存在self-Judge偏差。本轮未进行独立第三方复核或真实费用确认。

## 快速回查

1. 阅读[原运行manifest](manifest.json)，确认运行身份、参数、版本、限制与源码哈希。
2. 阅读[公开副本manifest](public-manifest.json)，查看原件／公开副本哈希、路径脱敏及runner补存说明。
3. 在[API返回](api-response.json)中逐条查看question、answer、ground_truth、contexts、五项指标和hallucination_trace。
4. 在[模型调用记录](llm-events.jsonl)中按runId配对start/end，核对实际消息、生成文本、返回模型名及token。
5. 打开[HTML报告](project/ragas_report.html)；报告媒体已嵌入，不依赖本地机器路径。
6. 用[外部SHA-256清单](SHA256SUMS.txt)核对公开副本全部文件。清单不包含自身，避免自引用。

## 材料索引

| 材料 | 内容与来源 |
|---|---|
| [manifest.json](manifest.json) | 原run manifest逐字节复制；没有事后添加新版本号或改写参数 |
| [public-manifest.json](public-manifest.json) | 本公开副本来源、每个原件及公开文件hash、脱敏范围；不是一次新评测 |
| [request.json](request.json) | 当时实际API请求，单模型、原0.8门槛 |
| [api-response.json](api-response.json) | 原结果，仅chart_path和html_path改为可移植相对路径 |
| [llm-events.jsonl](llm-events.jsonl) | 原始本地回调记录，保留完整提示和模型输出 |
| [server.log](server.log) | 原运行日志，只将4处归档绝对根路径替换为`<ARCHIVE>` |
| [packages.json](packages.json) | 当时实际Python依赖版本；包含Ragas 0.1.21及pysbd 0.3.4 |
| [requirements.txt](requirements.txt)、[requirements_new.txt](requirements_new.txt) | 当时复制的依赖声明；不将其描述为完整环境锁文件 |
| [原文](project/scenarios/deposit_insurance.txt) | 本轮冻结的金融法规节选 |
| [5题输入](project/scenarios/competition_25/pilot.json) | 实际题目及参考答案原副本 |
| [25题草稿](project/scenarios/competition_25/testset.json)、[案例索引](project/scenarios/competition_25/case-manifest.json) | 当时冻结的来源材料及pending人工确认状态；25题没有在本轮运行 |
| project/backend/ | 原run保存的后端源码副本，保持原字节；不以当前修复后代码冒充旧代码 |
| [逐题CSV](project/ragas_report_deepseek-chat.csv)、[汇总CSV](project/multi_model_summary.csv) | 原流水线生成的结果 |
| [历史记录](project/eval_history.json)、[图表](project/result_chart.png) | 本轮隔离目录产生的记录与图表 |
| [runner源码](runner/run_evidence.py) | 事后按原manifest已有SHA-256匹配补存；旧run当时只有runner hash，没有源码副本 |

## PASS与缺失值边界

本轮PASS只说明现有门禁对保存样本的规则检查通过，**不表示5条回答都正确**，也不构成发布认证。Faithfulness均值排除了缺失值；“拒答准确率100%”对应1条负向题，不能据此判断正向题没有过度拒答。

空Trace不证明回答无幻觉，也不能单凭空结果认定调用失败。引用出现在上下文中，仅能证明引用出处，还需要检查它是否支持对应陈述。

本轮包含D06低Answer Correctness、C02指标与Trace解释边界、F03过度拒答疑点等可回查现象。中文分句兼容问题是在预跑之后发现的；这些原评分保留，不以修正后的预期分数替换。

## 来源、脱敏与校验

旧运行原件没有修改。本公开副本只有两类内容变换：

- API返回的两个展示文件路径改为`project/result_chart.png`及`project/ragas_report.html`。
- server.log中4处归档根路径改为`<ARCHIVE>`，警告及其他日志内容保留。

其他复制文件保持原字节。runner是事后补存，但文件hash与原manifest的runnerSha256一致。README、public-manifest和SHA256SUMS是公开整理时新增的说明及校验材料，不是原run当时生成的内容。

原manifest的sourceFiles与artifacts仍使用原运行语义。公开副本中文件校验以public-manifest及SHA256SUMS为准；脱敏文件的公开hash与原件hash不同，不能混用。

不包含.env、凭据、venv、Embedding权重、Chroma数据库、缓存或其他run。文件hash证明材料对应关系，不能替代独立认证。

## 复现与人审限制

- 本轮是归档工具真实HTTP调用API，不是工作台按钮到页面结果的浏览器全过程证明。
- 原运行使用同机既有Python环境和Embedding缓存，不是另一台机器或全新操作系统验证。
- 原manifest只记录Embedding名称，没有不可变权重revision；提供方返回模型名也不代表不可变权重版本。
- 温度0不保证逐字复现；观察的逻辑调用数可能不覆盖HTTP内部重试。
- runner源码用于审计，不应在公开副本内直接运行；其原目录定位依赖项目结构。
- 旧13条冻结Demo作者核验是另一批材料，不继承到这5题，也不把本轮pending升级为人工完成。
- 当前中文兼容版本和未来新实验必须单独归档。本材料不被重新标成修正后的run。

进一步说明见[复现实验文档](../../reproducibility.md)及[可信度说明](../../../evaluation-credibility.md)。
