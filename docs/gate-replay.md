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

## CI 报告：JUnit XML 与作业摘要

两个可选参数把裁决结果交给 CI 页面展示，不影响裁决与退出码：

```bash
python scripts/gate_replay.py result.json --junit gate.junit.xml --summary "$GITHUB_STEP_SUMMARY"
```

- `--junit PATH`：每个门禁指标对应一个测试用例，未达标的用例写明实际值、阈值和差值，例如「正向忠实度 0.7375 低于阈值 0.8（差 0.0625）」。GitLab、Jenkins 等可直接读取 JUnit；GitHub 需要配合报告类 Action 展示（见下方示例工作流）。输入无法读取时写一个出错用例。
- `--summary PATH`：向文件追加一段 Markdown 表格。传入 `$GITHUB_STEP_SUMMARY` 时，表格会显示在 GitHub Actions 运行页的作业摘要里：

| 指标 | 实际值 | 阈值 | 结果 |
|---|---|---|---|
| 正向忠实度 `pos_faithfulness` | 0.7375 | 0.8 | ❌ 差 0.0625 |
| 正向上下文精度 `pos_context_precision` | 0.9833 | 0.8 | ✅ 达标 |

（上表为 25 题正式运行的实际输出。）报告里的逐项结果与门禁裁决一致，由 [`tests/test_gate_replay.py`](../tests/test_gate_replay.py) 校验。

## 失败归因：该修检索还是生成

门禁拦截时，回放会对主评测模型的正向题逐题归因（[`backend/core/attribution.py`](../backend/core/attribution.py)），并写入作业摘要：

| 类别 | 判定条件 | 对应环节 |
|---|---|---|
| 检索未找全 | context_recall < 0.5 | 检索 |
| 检索排序差 | 召回足够，context_precision < 0.5 | 检索 |
| 生成拒答 | 检索正常，faithfulness < 0.5 且回答是拒答句 | 生成 |
| 生成无依据 | 检索正常，faithfulness < 0.5 | 生成 |

每题对正向忠实度差距的贡献为 (1 − faithfulness) / 题数，按类别相加，判断主要问题在哪个环节。指标缺失的题列为「无法判断」，不补 0。归因只读已有指标，不改变裁决与退出码；0.5 是诊断用的经验值，不是门禁阈值。

**25 题正式运行**：拦截完全来自 4 道「生成拒答」题——检索召回均为 1.0，原文已经找到，模型仍回答「无法回答」，合计拉低正向忠实度 0.2。其余 16 题拉低 0.0625。也就是说，这次 FAIL 该修的是生成环节的拒答条件，不是检索。这 4 道题的参考答案都是「不对 / 不支持」，即问题带错误前提；问答提示词只给了「无法回答」一个出口，没有让模型依据原文纠正前提。工作台「门禁审计」页列出了这 4 道题的问题、回答与参考答案。

## 回归对比：这次比上次差了吗

门禁只看本次分数是否达到阈值。加上 `--baseline` 后，回放还会把本次与基线（例如上一个版本的评测结果）逐题配对，回答「这次比上次差了吗」：

```bash
python scripts/gate_replay.py new.json --baseline old.json --summary "$GITHUB_STEP_SUMMARY"
```

逻辑在 [`backend/core/regression.py`](../backend/core/regression.py)：

1. 两次运行都取门禁使用的主评测模型，按题目文本配对正向题；只统计两次都有分数的题，只有一侧有分数的题单独列出。
2. 对每个门禁指标求逐题平均变化，并按题有放回重抽样 10000 次（固定种子）得到 95% 区间。
3. 判定：

| 判定 | 条件 | 门禁 |
|---|---|---|
| 确定退步 | 区间整体低于 0，**且**平均下降超过同一裁判重判的波动 | 拦截（退出码 1） |
| 可能退步 | 平均下降，但区间跨过 0，或下降没有超过裁判波动 | 只提示 |
| 未退步 | 平均变化不小于 0 | — |

裁判波动的默认值来自实测：同一裁判对 25 题正式运行重判一次，逐题分数平均变动正向忠实度 0.0167、上下文精度 0.04（[独立裁判对照](judge-comparison.md)；数值由测试与归档核对）。可用 `--noise-faith`、`--noise-ctx` 覆盖。这样，「题目抽样带来的波动」（[门禁可信度 ④](competition/gate-credibility.md)）和「裁判自身的波动」（②）都在 CI 里被考虑：退步小于这两类波动时只提示，不拦截。

**真实样例**：两次 5 题预跑是同一组题目，第一次在中文兼容层修正之前，第二次在修正之后。

```bash
python scripts/gate_replay.py docs/competition/evidence/20261005T131353Z-pilot-35f623cb/api-response.json --baseline docs/competition/evidence/20261005T101847Z-pilot-c72cb679/api-response.json
```

| 指标 | 基线 | 本次 | 配对题数 | 逐题平均变化 | 95% 区间 | 判定 |
|---|---|---|---|---|---|---|
| 正向忠实度 | 0.8333 | 0.6000 | 3 | −0.0333 | [−0.1000, 0.0000] | 可能退步 |
| 正向上下文精度 | 0.8542 | 0.9792 | 4 | +0.1250 | [0.0000, 0.3750] | 未退步 |

门禁结论从 PASS 变为 FAIL，正向忠实度均值看上去降了 0.23；逐题配对后，可比较的 3 题只平均降了 0.033，差异主要来自一道题——基线里它的忠实度缺失（不计入均值），修正后被判为 0。回归对比把这类「口径变化」和「回答变差」分开列出。本例只有 3–4 道可配对的题，区间很宽，只能作为演示，不能据此判断修正前后哪次更准确。

边界：裁判波动来自一次重判、一个裁判、一个领域；重抽样只反映题目抽样，配对题数很少时区间不可靠。

## 在其他仓库中使用：GitHub Action

仓库根目录的 [`action.yml`](../action.yml) 把门禁回放打包成复合 Action（composite action）。其他仓库的工作流加一步即可接入：

```yaml
- uses: actions/checkout@v4
- uses: 4444wjt/RAG_Evaluation_Demo@master   # 有新发布后建议改为固定的 tag
  with:
    result: path/to/evaluation-result.json   # 本次评测输出的 JSON
    faith: "0.8"                             # 可选，正向忠实度阈值
    ctx: "0.8"                               # 可选，正向上下文精度阈值
    junit: quality-gate.junit.xml            # 可选，留空则不输出 JUnit
    baseline: path/to/previous-result.json   # 可选，与基线逐题对比
```

| 输入 | 默认值 | 说明 |
|---|---|---|
| `result` | 必填 | 评测结果 JSON（相对于工作区） |
| `faith` / `ctx` | `0.8` / `0.8` | 两个门禁阈值 |
| `baseline` | 空 | 基线评测结果 JSON；填写后逐题对比，确定退步时也拦截 |
| `junit` | 空 | JUnit XML 输出路径 |
| `summary` | `true` | 是否把结果表写入作业摘要 |
| `python-version` | `3.11` | 运行门禁的 Python 版本 |

输出 `passed`（`true` / `false`）与 `exit-code`（0 / 1 / 2）。门禁拦截时该步骤失败，后续步骤默认不再执行；需要继续时在该步骤上设置 `continue-on-error: true`，再读取 `steps.<id>.outcome`。

日常 CI 的 `gate-action` 任务会自检这个 Action：演示快照应放行，以自身为基线时也应放行，25 题正式运行应拦截，两份 JUnit 报告的失败数应分别为 0 和 1。

## CI 示例

[`.github/workflows/quality-gate-example.yml`](../.github/workflows/quality-gate-example.yml) 只能手动触发：在 GitHub 的 Actions 页面选择 “Quality gate example” → “Run workflow”，再选输入文件和阈值。它使用本仓库的 Action，把结果表写入作业摘要，并用 [publish-unit-test-result-action](https://github.com/EnricoMi/publish-unit-test-result-action) 把 JUnit 报告发布为检查结果 “Quality gate report”。默认输入是 25 题正式运行，所以默认会以失败结束，用来演示拦截效果；换成演示快照则会通过。

接入自己的流程时，把评测结果 JSON 的路径换成本次运行的输出即可。

## 边界

- 回放只重新执行**门禁裁决**，不重新计算指标，也不重新判定逐句 Trace；指标的可信度仍取决于原评测（见 [评测结果可信度与限制](evaluation-credibility.md)）。
- 门禁取第一个有效模型的正向样本均值，规则与正式评测相同；PASS 只表示对应样本达到阈值，不代表每条回答正确。
