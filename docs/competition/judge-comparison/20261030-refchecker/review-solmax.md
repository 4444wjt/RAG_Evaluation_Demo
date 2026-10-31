审核结果：**原始数据与汇总数字通过；机器报告的“A 臂有可证优势”不通过。主 Agent 的“无可证优势”成立，但机制解释应降为假设。** 全程只读，未联网、未调用模型、未做 Git 写操作。

[SHA256SUMS.txt](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/SHA256SUMS.txt) 的 **235/235 项通过**，审核结束时复核仍一致。

| 核查项 | 判定 | 核查结果与证据 |
|---|---|---|
| ① A 臂提示与解析 | **通过** | 5 个输入均与 `05099e7` 对应 blob 逐字节一致。用原包方法重建的 **60/60 个提示一致**；直接调用原 `parse`，未走 `aparse` 模型修复。60 份响应均能严格解析，无需修补。见 [run_compare.py:26](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/run_compare.py:26)、[Ragas 原方法:197](D:/RAG_Evaluation_Demo/venv/Lib/site-packages/ragas/metrics/_faithfulness.py:197)。 |
| ② B 臂替换范围 | **通过** | 仅替换 `get_model_batch_response`；包源码与本地 RECORD、T1 源码锁一致。原生 `is_joint=False`、`merge_psg=False`、不切分 reference 路径重放，**156/156 个提示及解析一致**。原发送上限 10 覆盖为 2048，符合修订 M3。见 [run_compare.py:63](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/run_compare.py:63)、[RefChecker:192](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/venv-ragchecker/Lib/site-packages/refchecker/checker/llm_checker.py:192)。 |
| ③ 原始响应复算 | **通过（数值）** | 从 216 份原始响应重建 312 条结果，与 `results.jsonl`、`summary.json` 的计数、分项、翻转、未决项逐项一致。见 [summary.json:17](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/summary.json:17)、[原始响应示例](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/raw/A1-001.json)。 |
| ④ 预算与敏感内容 | **通过** | 216 次预留、结算、结果记录全部对齐，未结算为 0；估算费用 **¥0.27024176**。最高“已结算＋未结算预留＋本次预留”为 ¥0.29386580，低于 ¥8。216 份响应未见思考字段或标记；扫描 517 份文本产物，当前指定密钥精确匹配及常见凭据模式均为 0 命中。见 [budget-ledger.jsonl](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/budget-ledger.jsonl)。 |
| ⑤ 主 Agent 结论 | **判定通过；因果归因不通过** | “无可证优势”符合第 6 节全文；但“这三条差异来自输入设计”的确定性表述超过现有证据。见 [结论:16](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/conclusion-main-agent.md:16)、[机制解释:22](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/conclusion-main-agent.md:22)。 |

独立复算结果如下，三项数字依次对应第 1、2、3 轮：

| 臂 | 人工一致／46 | 支持命中／40 | 不支持命中／6 | 翻转 |
|---|---|---|---|---|
| A | 45、44、45 | 39、38、39 | 6、6、6 | S52：支持→不支持→支持 |
| B | 43、43、43 | 40、40、40 | 3、3、3 | 0 |

解析失败、无结果、接口错误均为 **0**。六条未决始终保留：S12 三轮均为 A=`0`、B=`Neutral`；S14、S16、S18、S26、S35 各自三轮均为 A=`1`、B=`Entailment`。usage 合计输入 **273,609** tokens，其中缓存命中 **182,894**；输出 **10,187**。

主要分歧有两处：

- **机器结论漏用了登记限制。** A 的不支持类三轮累计比 B 多一致 9 条，支持类累计少一致 4 条，净领先 5 条全部依赖不支持类差异。[第 6 节](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/preregistration.md:57) 明定这 6 条的差异不能作为优势依据；[analyze.py:73](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/analyze.py:73) 仅实现均值和最差轮次条件，因此 `summary.json`、`report.md` 的优势结论不通过。
- **S02/S05/S06 有支持机制假设的证据，但未证明因果。** 人工规则明确排除题面金额，A 的拒绝理由也明确指出原文缺少金额；B 确实把“49 万本金、2 万利息”前置进 reference，并三轮都输出 `Entailment`。但 B 只有标签，没有理由；缺少移除题面的对照，尚不能排除提示格式、分组方式或忽略数字核实等解释。这三条还同属一道题。见 [人工规则:31](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/inputs/judge-annotation.json:31)、[A 原始理由:54](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/raw/A1-001.json:54)、[B 原始响应](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/raw/B1-002.json)。

**未验证项：**服务商账单、`deepseek-flash` 别名背后的精确版本、登记与批准的可信时间先后、标注人的实际独立性，以及上述机制的反事实因果关系。包源码已核本地 RECORD，未联网核对上游发行包。另需披露：[S52 含 `ri<REDACTED_LABEL>`](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/inputs/judge-annotation.json:501)，不能将其翻转归因于英文；`conclusion-main-agent.md` 本身未纳入 SHA256 清单。

