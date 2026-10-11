# 门禁回放

`scripts/gate_replay.py` 读取一份已归档的评测结果，用正式评测里同一个 `check_quality_gate`（[`backend/core/gate.py`](../backend/core/gate.py)）重新裁决。不调用模型，不需要密钥，几秒内跑完。

用途：

- **复查归档结论**：确认归档里的 PASS / FAIL 能用同一套门禁逻辑重新得出；
- **接入 CI**：裁决不通过时退出码为 1，CI 步骤随之失败，可以当作合并或发布前的卡口；
- **试算阈值**：换一个阈值看同一批结果会不会被拦，不必重新调用模型。

## 用法

```bash
python scripts/gate_replay.py docs/competition/evidence/20261005T144504Z-full-39511424/api-response.json
```

```bash
python scripts/gate_replay.py frontend/src/fixtures/demo.json --faith 0.85 --ctx 0.8
```

| 退出码 | 含义 |
|---|---|
| 0 | 通过（PASS） |
| 1 | 拦截（BLOCK） |
| 2 | 输入无法读取，或缺少门禁所需的 `summary` |

脚本最后会把本次回放结果和归档里记录的 `gate_passed` 对照输出。使用默认阈值 0.8 时，两份归档的回放结论与原记录一致：

| 输入 | 正向忠实度 / 正向上下文精度 | 回放结论 |
|---|---|---|
| 25 题正式运行（2026-10-05） | 0.7375 / 0.9833 | FAIL，退出码 1 |
| 无密钥演示快照 `20260930-1958` | 1.0 / 1.0 | PASS，退出码 0 |

[`tests/test_gate_replay.py`](../tests/test_gate_replay.py) 锁定了上述结论，并在日常 CI 中运行。

## CI 示例

[`.github/workflows/quality-gate-example.yml`](../.github/workflows/quality-gate-example.yml) 只能手动触发：在 GitHub 的 Actions 页面选择 “Quality gate example” → “Run workflow”，再选输入文件和阈值。默认输入是 25 题正式运行，所以默认会以失败结束，用来演示拦截效果；换成演示快照则会通过。

接入自己的流程时，把评测结果 JSON 的路径换成本次运行的输出即可。

## 边界

- 回放只重新执行**门禁裁决**，不重新计算指标，也不重新判定逐句 Trace；指标的可信度仍取决于原评测（见 [评测结果可信度与限制](evaluation-credibility.md)）。
- 门禁取第一个有效模型的正向样本均值，规则与正式评测相同；PASS 只表示对应样本达到阈值，不代表每条回答正确。
