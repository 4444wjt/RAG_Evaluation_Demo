**独立审核结果：①—④通过；⑤局部不通过。登记结论“不确定”正确，主实验“无可证优势”应保持。** 本次从原始 `response_text` 独立复算，未运行原分析器；全程未修改文件、未联网、未调用模型、未做 Git 写操作。

| 核查项 | 判定 | 结果与证据 |
|---|---|---|
| ① 提示、解析与完整性 | **通过** | 156/156 个 Bq0 请求仅删除 reference 内的“题面＋一个空格”；9/9 个哨兵提示与原 B 相同。请求参数符合登记，RefChecker 四份相关源码匹配 T1 锁，27 份包源码匹配本地 RECORD。主实验 **237/237**、对照 **178/178** 哈希在审核开始和结束均通过。[调用位置](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/run_ablation.py:223)、[源码锁](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/T1-source-integrity.json)、[主实验清单](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/SHA256SUMS.txt)、[对照清单](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/SHA256SUMS.txt)。 |
| ② 原始响应复算 | **通过** | 重建的 165 条结果与 `results.jsonl` 完全一致；确定集、分项、翻转、未决项和 usage 均与汇总一致。解析失败、接口错误、无结果均为 **0**。[原始响应目录](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/raw/)、[汇总](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/summary.json:426)。 |
| ③ 预算、思考与密钥痕迹 | **通过，限已保存记录** | 165 次预留、结算、结果记录逐次对齐；估算 **¥0.08234904**，未结算预留 0，最高滚动占用 **¥0.10482968＜¥1**。两实验合计估算 **¥0.35259080**。165 份响应均仅返回标签，未见思考字段或标记；扫描对照目录 378 份文本，常见凭据模式 0 命中。[台账](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/budget-ledger.jsonl)、[usage 汇总](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/summary.json:612)。 |
| ④ “不确定”判定 | **通过** | 哨兵 **9/9 Entailment**；目标 S02、S05 各为 **Neutral×3**，S06 为 **Entailment×3**。目标既非全部不支持，也非全部支持，符合“其他情况一律不确定”。[预登记第4节](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/preregistration-2.md:22)。 |
| ⑤ 主 Agent 结论 | **局部不通过** | 数字、登记判定及不改变主实验结论的边界正确；“没有时间漂移”和 S08 的确定性归因超出证据，具体分歧见下。[结论](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/conclusion-main-agent.md)。 |

独立复算的分项为：

| 轮次 | 确定集一致 | 人工支持类 | 人工不支持类 |
|---|---:|---:|---:|
| 1 | 45/46 | 40/40 | 5/6 |
| 2 | 44/46 | 39/40 | 5/6 |
| 3 | 44/46 | 39/40 | 5/6 |

轮间翻转 **1/52，仅 S08：Entailment→Neutral→Neutral**。六条未决保留：S12 三轮均 Neutral；S14、S16、S18、S26、S35 三轮均 Entailment。usage 为输入 **83,547**、缓存命中 **45,176**、输出 **475** tokens；返回模型名全部为 `deepseek-flash`。

**分歧集中在解释措辞，没有数值分歧：**

- [结论第5行](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/conclusion-main-agent.md:5)“没有时间漂移”应改为“**本组哨兵未检出标签漂移，按登记进入假设判定**”。三个哨兵全部稳定，仍不能排除其他陈述发生变化。
- [结论第20行](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/ablation-no-question/conclusion-main-agent.md:20)“这说明去掉题面也会影响其他陈述”归因过强。**反例解释**：S08 的变化可以来自重复调用波动；相同去题面提示已经产生 E、N、N，且没有同期带题面的 S08 哨兵。宜仅写“观察到 S08 在去题面设置下轮间翻转，原因未确定”；“题面只用来帮助理解”也未被本对照验证。

S02、S05 的改判为题面敏感性提供描述性支持；S06 是“去题面后三条全部改判”预测的反例，不能据此断定题面从未参与其原判定。保留“机制待验证、三条同属一道题、最多 E 级”是合适的。

另需保留主实验的既有区别：其 `summary.json` 仍写“A 臂有可证优势”，但 A 的领先完全依赖六条不支持类；完整预登记禁止以该类差异作为优势依据，因此主 Agent 的“无可证优势”成立。本次也复算确认了这些分项。[主实验说明](D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/conclusion-main-agent.md:7)。

**未验证项：**供应商实际账单及计价、底层精确模型版本与内部思考状态、登记时间的可信先后、标注人的实际独立性；未联网核验发行包，未读取凭据做真实密钥精确匹配。历史未改动的证明限于哈希清单及源码锁覆盖范围。