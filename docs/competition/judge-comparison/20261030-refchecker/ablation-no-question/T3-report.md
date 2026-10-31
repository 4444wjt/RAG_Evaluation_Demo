# T3 离线实现、测试与干跑报告

日期：2026-10-30。状态：**T3 验收通过；未执行 T4**。口径沿用本目录 `preregistration-2.md`，该文件未修改。

## 验收结果

| 任务书要求 | 实际结果 | 证据 |
|---|---|---|
| 测试全部通过、日志落盘 | 35/35 通过，0 失败；含完整 165 次假响应及分析器 CLI | `T3-tests-r1.log` |
| 干跑 165 请求，提示核对通过 | Bq0 156 条、哨兵 9 条；165/165 提示核对通过，每轮先 52 条 Bq0，再 S02/S05/S06 | `dry-run-plan.json`、`T3-dry-run.log` |
| 最坏预留总和、单次最大预留及滚动预算 | 总和 ¥3.696324 ≤ ¥9.7；单次最大 ¥0.022940；运行硬停 ¥1 | 同上；预算测试见测试日志 |
| 参照主实验 B 实际 usage 估算费用 | ¥0.08446656，约 ¥0.08447；仅为历史 usage 代理估算 | `dry-run-plan.json` 的 `expected_actual_cost` |
| 主实验哈希一致 | 开工、测试、干跑及收尾均核对通过；命令行校验 237/237 OK，退出码 0 | `T3-initial-integrity.json`、`T3-parent-sha256sum.log` |
| T3 报告完整 | 文件、命令、结果、证据及未验证项见本报告 | 本文件；机器摘要 `T3-acceptance.json` |

六项验收均通过。本轮研究新增确认分 **[0, 0]**：只完成离线工程验收，没有假设实测结果或算法优势证据。

## 改动文件与实现

- `run_ablation.py`：import 复用 `run_compare.call_b`、`DeepSeekClient`、`load_b` 和 `compare_common` 的输入核对、确定集核对、分组、价格及台账逻辑。Bq0 传 `question=None`，哨兵传原题面；请求 ID 分别为 `Bq0{轮}-{序号:03}`、`S{轮}-{陈述id}`。`AblationLedger` 继承原台账，只收紧至 ¥1。未 monkeypatch 主实验模块；`call_b` 内部原有的第三方发送函数替换沿用原实现。
- `analyze_ablation.py`：先核对 9 条哨兵，再按第 4 节输出支持假设、否定假设或不确定；任何哨兵非 Entailment（包括无结果）均输出“存在时间漂移，不确定”，不进入假设判定。保留 46/40/6 分母、翻转、6 条未决、解析失败、接口错误、返回模型、逐请求 usage 和费用；缺失 usage 不填零。真实结果不齐全时停止，不生成假设结论。
- `test_ablation.py`：仅使用假发送器；覆盖三种假设判定、每个哨兵的否决作用、提示与请求顺序、¥1 等号硬停、结算释放、缺失 usage、付费开关、原生解析、错误不重试，以及范围、`.env` 和网络访问拦截。
- 新增验收产物：`T3-initial-integrity.json`、`T3-preflight.log`、`T3-tests-r1.log`、`T3-dry-run.log`、`T3-parent-sha256sum.log`、`dry-run-plan.json`、`T3-acceptance.json`、本报告，以及 `tmp/` 下的构造测试产物。

全部写入都在 `ablation-no-question/` 内。未安装或调整依赖。未执行 git add/commit/push/stash/checkout；未更新范围外的 STATE 或其他文件。

## 费用口径

每次预留按原函数估算：输入 tokens = 提示字符数 × 2，输出上限 2048 tokens；输入 ¥2/百万 tokens、输出 ¥8/百万 tokens。165 次预留总和为 **¥3.696324**，最大一次为 **¥0.022940**。

¥1 的运行硬停检查是：**已结算 + 未结算预留 + 本次预留 ≥ 1，则不发本次请求并停止**。结算后释放该请求的预留，改计 usage 估算实际费用。因此预留总和超过 ¥1 不导致干跑失败，也不预先截断请求清单。真实运行仍可能因实际 usage 或其他停止条件而不足 165 次。

费用代理估算逐一匹配主实验 B 的同轮、同陈述 usage：156 条 Bq0 复用全部 156 条 B 的 usage，再加 S02/S05/S06 的 9 条同轮 usage。登记价格为输入未命中缓存 ¥2/百万 tokens、命中缓存 ¥0.04/百万 tokens、输出 ¥8/百万 tokens。主实验 B 156 条合计 **¥0.08048232**，加哨兵代理费用 **¥0.00398424**，得到 **¥0.08446656**。未扣除去题面节省的输入 tokens；未来缓存及输出长度可能变化，此数不是费用上界或供应商账单。独立直接算式复核与原 `usage_cost` 结果一致。

## 执行命令

以下命令均以**上级主实验目录**为工作目录，严格按测试 → 干跑 → 哈希校验执行，退出码均为 0。日志写入本目录：

```powershell
& 'venv-ragchecker/Scripts/python.exe' -I -S -B ablation-no-question/test_ablation.py
& 'venv-ragchecker/Scripts/python.exe' -I -S -B ablation-no-question/run_ablation.py --dry-run
& 'C:/Program Files/Git/usr/bin/sha256sum.exe' -c SHA256SUMS.txt
```

SHA 清单中的路径相对主实验目录，因此校验在该目录执行（清单参数为 `SHA256SUMS.txt`）。对应日志为 `T3-tests-r1.log`、`T3-dry-run.log`、`T3-parent-sha256sum.log`。

此前已用同一个解释器及 `-I -S -B` 做只读输入、包导入前置核查，日志为 `T3-preflight.log`。确认 RefChecker 0.2.17 / RAGChecker 0.1.9 可用，5 个固定 git blob 匹配，确定集 46 条（支持 40、不支持 6），未决集合完全一致。运行时审计钩子限制写入目录、阻断网络和 `.env` 读取；使用本目录临时路径，禁用字节码落盘及库的在线下载。

## 构造分析产物与未验证项

完整假 LLM 测试实际产生了 165 份原始记录、结果、台账及分析输出，集中在：

`tmp/test_full_165_fake_requests_and_actual_analysis_cli-mtkrti0u/`

其中 `summary.json`、`report.md` 和 `analyze-cli.log` 验证了分析器实际 CLI 链路；返回模型名明确为 `synthetic-llm-no-api`。这些是构造测试数据，其中的“支持假设”及费用数字只用于测试，不是本对照实测结果。

本轮网络连接、模型 API 调用、实际 `.env` 读取均为 **0**。测试中主动尝试的网络、越界写入和 `.env` 打开均在访问前被拦截。未执行真实 `--run`，本目录根层没有创建实际运行的 `raw/`、`results.jsonl`、`budget-ledger.jsonl`、`summary.json` 或 `report.md`。

真实服务兼容性、实际返回模型/usage/账单、能否在 ¥1 内完成、时间漂移及假设判定均未验证。未来 `--run` 仍须 `VS_ALLOW_PAID=1`，并从进程环境提供 `OPENAI_API_KEY`，不会回退读取 `.env`。即使将来支持假设，也只适用于同一道题的这 3 条陈述，非独立样本、最多 E 级；不比较与 A 的优劣，不改写主实验结论。
