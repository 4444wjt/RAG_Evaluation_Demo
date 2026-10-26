# 外部 RAG 端到端评测

检索方：被测应用。平台未检索、未重新生成回答。

题目 25；已评测 25；失败 0；未尝试 0。
门禁：FAIL。⛔ 质量门禁拦截，终止发布

## 五项指标

| 指标 | 均值 | 有效数量 |
|---|---|---|
| faithfulness | 0.8023 | 25 / 25 |
| context_precision | 0.8 | 25 / 25 |
| answer_relevancy | 0.6725 | 25 / 25 |
| answer_correctness | 0.5715 | 25 / 25 |
| context_recall | 0.8 | 25 / 25 |

## 逐题记录

| 题号 | 应用状态 | 实际资料段数 | Trace 状态 | 拒答识别 |
|---|---|---|---|---|
| D01 | answered | 2 | ok | 一致 |
| D02 | answered | 2 | ok | 一致 |
| D03 | answered | 2 | partial | 一致 |
| D04 | answered | 2 | ok | 一致 |
| D05 | answered | 2 | ok | 一致 |
| D06 | answered | 2 | ok | 一致 |
| D07 | answered | 2 | ok | 一致 |
| D08 | answered | 2 | call_error | 一致 |
| D09 | answered | 2 | call_error | 一致 |
| D10 | answered | 2 | call_error | 一致 |
| C01 | answered | 2 | ok | 一致 |
| C02 | answered | 2 | call_error | 一致 |
| C03 | answered | 2 | call_error | 一致 |
| C04 | answered | 2 | ok | 一致 |
| C05 | answered | 2 | partial | 一致 |
| N01 | answered | 2 | call_error | 一致 |
| N02 | answered | 2 | call_error | 一致 |
| N03 | answered | 2 | partial | 一致 |
| N04 | answered | 2 | call_error | 一致 |
| N05 | answered | 2 | call_error | 一致 |
| F01 | answered | 2 | ok | 一致 |
| F02 | answered | 2 | call_error | 一致 |
| F03 | answered | 2 | partial | 一致 |
| F04 | answered | 2 | ok | 一致 |
| F05 | answered | 2 | ok | 一致 |

## 失败与限制

- 失败题不计入指标均值，未填零；应同时查看有效数量与未完成数量。
- contexts 由被测应用声明为实际生成资料，平台未独立验证应用内部链路。
- 外部应用与平台检索运行分别归档，检索条件不同不能直接排名。
- 实际费用未知；没有费用自动封顶。

完整应用请求、响应与异常见 records.jsonl；模型判定不是人工认证。
