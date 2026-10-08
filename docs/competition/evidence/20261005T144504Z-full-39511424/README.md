# 25题正式运行：公开证据索引

- **范围**：单一《存款保险条例》节选的25题固定场景，不是跨领域Benchmark。
- **请求模型**：`deepseek-chat`；**实际返回模型**：`deepseek-flash`（325次调用）。
- **版本**：Ragas `0.1.21`；中文句子适配 `zh-sentence-v1`。
- **结果**：25/25回答完成，五项指标均25/25有效；Trace非空18、空7、缺失0。
- **门禁**：FAIL，直接原因是正向 Faithfulness `0.7375 < 0.8`；正向 Context Precision `0.9833 >= 0.8`。保留原始FAIL，不调整阈值。
- **用量**：325次完整调用，`558,958` token（prompt 530,906；completion 28,052；缓存命中262,907）。无账单，费用仅为估算。

## 阅读顺序

1. [25题离线分析表](analysis.md)（AI辅助分析、待人工确认）
2. [完整分析数据](analysis.json)（回答、contexts、指标、Trace及重点观察）
3. [运行清单](manifest.json)、[用量](usage.json)、[指标](metrics.json)、[回答](answers.json)、[Trace](traces.json)
4. [HTML报告](report/ragas_report.html)、[CSV](report/ragas_report_deepseek-chat.csv)、[图表](report/result_chart.png)
5. [输入](inputs/testset.json)、[作者确认清单](inputs/case-manifest.json)、[原文节选](inputs/deposit_insurance.txt)
6. [文件校验索引](public-manifest.json)和[SHA-256清单](SHA256SUMS.txt)

## 证据边界

本目录是从原始 run `work/competition/runs/20261005T144504Z-full-39511424` 生成的脱敏公开副本，原始目录未覆盖。脱敏删除机器本地绝对路径和凭据样式，公开副本重新计算哈希，并记录源文件对应关系；未复制 `.env`、向量库、缓存或虚拟环境。

“AI辅助分析、待人工确认”表示本轮新分析没有写入人工核验身份、日期或结论，也不会自动改变 `credibility-review.json`。旧冻结Demo的13条作者核验只对应旧快照。

这次是真实Web API单模型运行，不能证明总体准确率、跨领域泛化、独立裁判可靠性或异机复现。费用仍需账单确认，没有费用自动封顶功能，本次没有后续模型调用授权。
