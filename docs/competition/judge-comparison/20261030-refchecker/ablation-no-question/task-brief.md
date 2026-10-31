# 任务单：去掉题面对照（T3 离线实现与干跑；T4 付费运行由主 Agent 执行）

公共要求：
- 口径以同目录 `preregistration-2.md` 为准，不得修改；无法执行时停下并写明，不自行调整。
- 只写本目录 `ablation-no-question/`。上级目录（主实验）的全部文件只读，**不得修改**。需要复用的函数用 import 调用，不复制改写，也不 monkeypatch 主实验的模块。
- 解释器：只用上级目录的 `venv-ragchecker/Scripts/python.exe`，不得 pip install。
- 不联网，不调用任何模型 API，不读取 `.env`；禁止 git add、commit、push、stash、checkout。
- 停止条件：主实验文件哈希变化（用上级 `SHA256SUMS.txt` 核对）、写到范围外、依赖缺失、需要联网，遇到任一即停。

## T3（本次派发）

1. 新建 `run_ablation.py`：
   - 复用 `run_compare.call_b`、`run_compare.DeepSeekClient`、`run_compare.load_b`，以及 `compare_common` 里的输入核对、确定集核对和分组函数。
   - B' 调用 `call_b` 时传 `question=None`；哨兵沿用原题面。
   - 每轮先跑 B' 的 52 条，再跑哨兵 S02、S05、S06；共 3 轮。请求 ID 为 `Bq0{轮}-{序号:03}` 和 `S{轮}-{陈述id}`。
   - 预算台账写入本目录的 `budget-ledger.jsonl`，硬停 ¥1：「已结算 + 未结算预留 + 本次预留 ≥ 1」时停止。可以继承或包装 `compare_common.BudgetLedger`，但不得修改它。
   - 原始记录写入本目录 `raw/`，结果写入 `results.jsonl`，字段沿用主实验。
   - 两种模式：`--dry-run` 不联网，列出 165 个请求和预留总额；`--run` 必须同时设置 `VS_ALLOW_PAID=1`。
   - 干跑时核对：B' 每条提示与主实验 `dry-run-B.json` 对应提示去掉「题面 + 空格」前缀后完全一致；哨兵提示与主实验对应提示完全一致。不一致就停。
2. 新建 `analyze_ablation.py`：按预登记 2 第 4、5 节输出 `summary.json` 和 `report.md`。判定只依据第 4 节，必须先核对哨兵，再判断假设。
3. 新建 `test_ablation.py`，用假 LLM 测试。至少覆盖：
   - 三种判定（支持、否定、不确定）及哨兵漂移；
   - ¥1 硬停；
   - 未设 `VS_ALLOW_PAID` 时拒绝执行；
   - 提示中不含题面；
   - 解析失败记为无结果；
   - 主实验文件未被修改。
4. 依次执行：测试、`--dry-run`、主实验 `sha256sum -c ../SHA256SUMS.txt`。

**T3 验收标准**：
- 测试全部通过，日志落盘；
- 干跑共 165 个请求，提示前缀核对通过；
- 干跑写明两个数：
  - 全部预留的总和（最坏情况，按每次输出 2048 tokens 估算，约 ¥3.8）。它只需 ≤ 剩余授权 ¥9.7，**不要求 ≤ ¥1**；
  - 单次最大预留。
  - 说明：¥1 硬停按「已结算 + 未结算预留 + 本次预留」滚动检查，结算后预留即释放，所以不会因为预留总和超过 ¥1 而提前停止；
- 预计实际费用参照主实验 B 臂的实际 usage 估算，写明估算依据；
- 主实验哈希 237/237 一致；
- 写出 `T3-report.md`，写明改动的文件、执行的命令、结果、证据路径、未验证项。
