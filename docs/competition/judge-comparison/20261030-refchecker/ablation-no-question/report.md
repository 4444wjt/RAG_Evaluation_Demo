# 去题面对照

不确定

哨兵先行核对：True；进入假设判定：True。

| 轮次 | 确定集一致 | 人工支持类 | 人工不支持类 | 解析失败 | 接口错误 | 无结果 |
|---|---|---|---|---|---|---|
| 1 | 45/46 | 40/40 | 5/6 | 0 | 0 | 0 |
| 2 | 44/46 | 39/40 | 5/6 | 0 | 0 | 0 |
| 3 | 44/46 | 39/40 | 5/6 | 0 | 0 | 0 |

二元翻转 1/52；至少一次无结果 0/52。
52条中三轮同时出现支持和不支持的陈述数；无结果不归入任一类，另列状态变化与缺失。

| 未决陈述 | 第1轮 | 第2轮 | 第3轮 |
|---|---|---|---|
| S12 | Neutral / 不支持 | Neutral / 不支持 | Neutral / 不支持 |
| S14 | Entailment / 支持 | Entailment / 支持 | Entailment / 支持 |
| S16 | Entailment / 支持 | Entailment / 支持 | Entailment / 支持 |
| S18 | Entailment / 支持 | Entailment / 支持 | Entailment / 支持 |
| S26 | Entailment / 支持 | Entailment / 支持 | Entailment / 支持 |
| S35 | Entailment / 支持 | Entailment / 支持 | Entailment / 支持 |

解析失败：{'Bq0': 0, 'sentinel': 0}；接口错误：{'Bq0': 0, 'sentinel': 0}。
实际返回模型名：['deepseek-flash']；模型名缺失请求 0 条。
usage合计：{'prompt_tokens': 83547, 'completion_tokens': 475, 'prompt_cache_hit_tokens': 45176}；逐请求usage见summary.json。
usage估算总费用：0.08234904 元；费用未知请求 0 条。
已结算估算：0.08234904 元；未结算预留：0 元。
费用为登记价格与usage的估算，不是供应商账单。

仅适用于RefChecker整句核查器的去题面对照；三个目标陈述来自同一道题，非独立样本，最多E级。
支持假设也只说明这3条去题面后改判；其他陈述的变化仅描述，不比较Bq0与A，不改写主实验无可证优势结论。
