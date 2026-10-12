# 独立裁判对照归档：20261011-qwen72b

结论与解读见 [独立裁判对照](../../../judge-comparison.md)。本目录是那次运行的原始材料。被评模型没有被再次调用：回答、contexts 和参考答案取自 [25 题正式运行](../../evidence/20261005T144504Z-full-39511424/README.md) 和冻结演示快照，两者都只读，未被修改。

| 文件 | 内容 |
|---|---|
| `request.json` | 分组、裁判模型、指标、预算上限，以及输入文件的 SHA-256 |
| `runtime.json` | 相关依赖版本、中文兼容层版本、Trace 提示词哈希 |
| `preflight.jsonl` | 开跑前每个裁判各调用一次，记录请求的模型名和实际返回的模型名 |
| `raw.jsonl` | 68 条裁判结果：Faithfulness、Context Precision、完整 Trace（含模型原始输出） |
| `discarded.jsonl` | 2 条作废结果（contexts 解析错误，修复后已重判） |
| `llm-events.jsonl` | 每次调用的提示词哈希、返回内容和 token 用量，不含密钥 |
| `summary.json` | 由 `analyze` 从 raw 离线重算的全部对照数字 |
| `usage.json` / `cost-estimate.json` | 用量汇总和费用估算，不是账单 |
| `SHA256SUMS.txt` | 本目录所有文件的哈希 |

重算命令：`python tools/competition/judge_compare.py analyze docs/competition/judge-comparison/20261011-qwen72b`
