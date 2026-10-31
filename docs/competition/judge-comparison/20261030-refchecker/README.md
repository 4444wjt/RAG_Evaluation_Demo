# 与 RefChecker 核查器的同条件对照（2026-10-30）

RAGChecker 是陈述级核查上最接近本项目的已有工作。本目录把本项目门禁所用的陈述核查步骤和它的核查器放在同一条件下比较，事先登记了判定规则，并请未参与实现的模型做了独立复核。

## 结论

| | 本项目（Ragas 0.1.21 NLI 核查，中文兼容层） | RefChecker 0.2.17 整句核查器 |
|---|---|---|
| 人工确定集一致（共 46 条，三轮） | 45、44、45 | 43、43、43 |
| 人工支持类（共 40 条） | 39、38、39 | 40、40、40 |
| 人工不支持类（共 6 条） | 6、6、6 | 3、3、3 |

- 对照对象是 RAGChecker 0.1.9 所用核查器的整句路径，不是 RAGChecker 默认的三元组联合配置。
- 两边条件相同：20 题、52 条陈述、同一检索原文、同一模型（`deepseek-flash`，非思考，temperature 0），每边 3 轮。
- 不支持类只有 6 条，按预登记规则，这一类的差异不能作为优势依据，**结论为「无可证优势」**。
- RefChecker 漏判的 3 条出自同一道题：陈述里的金额只出现在题面，检索原文中没有。
- 追加的预登记「去掉题面」对照中，这 3 条里 2 条改判为 Neutral，1 条仍为 Entailment，登记判定为「不确定」。题面是否被当作证据，仍是待验证的假设。
- 证据等级 E：没有非开发者复现。

## 边界

- 只比较「给定陈述 → 是否被原文支持」这一步，不比较陈述抽取。
- 单一法规节选、单一模型、单一时段；RefChecker 的提示词是英文，输入是中文。
- S52 的陈述在归档时被误脱敏，见 `inputs/judge-annotation.json` 的 note。
- 人工参照来自 [第二轮人工标注](../../judge-calibration/20261021-annotation-AB/README.md)；标注人的独立性只来自自述。
- 机器生成的 `summary.json` 和 `report.md` 写的是「A 臂有可证优势」。这一输出漏掉了预登记第 6 节的最后一句，登记结论以 `conclusion-main-agent.md` 为准。两份独立审核都确认了这一点。

## 文件

| 文件 | 内容 |
|---|---|
| `preregistration.md`、`preregistration-amendment-1.md` | 运行前登记的问题、两臂、指标、判定规则与停止条件。修订 1 的起因见 `T1-report-r1-stopped.md` 和 `T1-blocker-source.txt` |
| `inputs/` | 5 份固定输入，与提交 `05099e7` 的 git blob 逐字节一致 |
| `raw/`、`results.jsonl`、`budget-ledger.jsonl` | 216 次请求的完整提示与原始响应、逐条判定，以及预算台账 |
| `conclusion-main-agent.md`、`review-solmax.md` | 主结论，以及独立审核（GPT-6.1 SOL Max，只读）的记录 |
| `ablation-no-question/` | 去掉题面对照：预登记 2、165 次请求的原始记录与结果、结论，以及独立审核（Astra Max，只读）的记录 |
| `SHA256SUMS.txt`（两份） | 完整性清单 |

估算费用：主对照约 ¥0.27，去题面对照约 ¥0.08。按 usage 和登记单价估算，不是供应商账单。

## 复算

```bash
cd docs/competition/judge-comparison/20261030-refchecker
sha256sum -c SHA256SUMS.txt
(cd ablation-no-question && sha256sum -c SHA256SUMS.txt)
python -I -S -B analyze.py --results results.jsonl
```

- `analyze.py` 只用标准库，会先核对输入 blob 和确定集，再计算指标。它会重写本目录的 `summary.json` 和 `report.md`，在 Windows 上与原文件逐字节一致（已验证），其他系统上只有换行符不同。复算会留下一个空的 `T1-tmp/` 目录，可以删掉。
- 重新发请求需要设置 `VS_ALLOW_PAID=1`，并按 `freeze-venv-*.txt` 重建两个运行环境。

审核记录和任务单里出现的 `D:/RAG_Evaluation_Demo/work/competition/ragchecker-checker-compare-20261030/`，是作者本机的原工作目录，对应本目录。
