# 项级归属与正确位置回指：事后扩展

日期：2026-10-22。离线确定性规则，没有模型请求。不参与 Quality Gate。

原规则与两个已有归档保持不变。扩展单独位于 `backend/core/statute_locate.py`，
复算入口为 `tools/competition/statute_posthoc.py`。

## 预注册结果（不变）

以下为 `20261021-pinpoint-run/analysis/rules.json` 的原规则结果。

| 类型 | 材料条数 | 原规则检出或误报条数 |
|---|---:|---:|
| 同条存在的另一款 | 18 | 17 |
| 不存在的款 | 13 | 13 |
| 款级合计 | 31 | 30 |
| 同款存在的另一项 | 3 | 0 |
| 不存在的项 | 1 | 1 |
| 项级合计 | 4 | 1 |
| 全部错误 | 35 | 31 |
| 原陈述对照 | 14 | 0 |
| 正确款项定位对照 | 13 | 0 |
| 省略款号对照 | 4 | 0 |
| 支持对照合计 | 31 | 0 |

只有 error 级 finding 算检出或误报。warn、info 不计。

## 事后扩展（同一批材料，样本内）

| 类型 | 材料条数 | 扩展后检出或误报条数 | 新增 ID |
|---|---:|---:|---|
| 同条存在的另一款 | 18 | 17 | 无 |
| 不存在的款 | 13 | 13 | 无 |
| 款级合计 | 31 | 30 | 无 |
| 同款存在的另一项 | 3 | 0 | 无 |
| 不存在的项 | 1 | 1 | 无 |
| 项级合计 | 4 | 1 | 无 |
| 全部错误 | 35 | 31 | 无 |
| 原陈述对照 | 14 | 0 | 无 |
| 正确款项定位对照 | 13 | 0 | 无 |
| 省略款号对照 | 4 | 0 | 无 |
| 支持对照合计 | 31 | 0 | 无 |

本次实际没有达到参考预期的项级 4/4：仍为 1/4。没有调整阈值或加入总领句来凑数。
`item_existing-S26-I1`、`item_existing-S26-I2`、`item_existing-S26-I4` 仍漏检。
完整陈述去引用后有 45 个字符二元组，第三项正文覆盖 16 个，第一、二项各覆盖 11 个；
最佳项覆盖率为 16/45，低于现有 `ATTRIBUTION_MIN=0.6`，
最佳项与所引第一、二项的覆盖率差也低于 `ATTRIBUTION_GAP=0.25`。
陈述同时复述了第一款总领句和第三项；逐项比较只读取每项正文，没有把总领句拼入各项。

项级规则是在看到 S26 漏检后才编写，又在同一批材料上检验，不能当作独立验证。
只有补上时才可描述为“这类漏检在这批材料上可以由确定性规则补上”；本次结果不支持这句话。
新增单元测试中的 S26 型短句仅陈述破产申请这一项内容，可检出并回指第三项，
它是规则行为测试，不替换归档长句，也不计入上表。
项级材料只有 S26 一份底稿，三人历史标注不一致：V/A 判支持，B 判无法判断。

`paragraph_existing-S27-P1` 扩展后仍然漏检；本次保留“前款”交叉引用豁免。

## 回指结果

按被标记的材料计数，逐条引用 error 的回指见 `analysis/posthoc.json` 的 `recovery`。
回指目标是材料的 `sourceLocation`，包含条、款、项三个字段。
确定前缀已经冲突则记不一致；否则目标要求的任一层未确定，记 undetermined；其余记一致。
同一材料若有多个引用 error，任一不一致优先，否则任一 undetermined 即记 undetermined。
不存在项的款不要求项号；缺项号不等于把项号猜成零。

| 被标记材料 | 一致 | 不一致 | undetermined |
|---|---:|---:|---:|
| 错误材料 | 28 | 0 | 3 |
| 支持对照 | 0 | 0 | 0 |

| undetermined ID | 已确定回指 | 原因 |
|---|---|---|
| `paragraph_absent-S28` | 无 | 条级最佳覆盖率 6/7，次佳 2/3，差值小于 0.25 |
| `paragraph_absent-S26` | 第十九条第一款 | 项级最佳覆盖率 16/45，低于 0.6 |
| `item_absent-S26` | 第十九条第一款 | 项级最佳覆盖率 16/45，低于 0.6 |

`PROVISION_NOT_IN_SOURCE` 共 14 条，回指一致 11 条、undetermined 3 条、不一致 0 条。
其余 17 条已检出材料的引用 error 均为 `MISATTRIBUTED`，回指一致。
一致的 28 个 ID 及每层覆盖率全部列于 `analysis/posthoc.json`，未列为 undetermined 的被检出 ID 均属一致。

## formal 材料新增误报核对（独立列示）

第二份材料为 `20261021-formal/run/package.json`，42 条。
实际字段与款项材料相同：逐项读取 `items[].statement`、`contexts`、`question`、`expected`；
法规原文读取其自身归档的 `inputs/scenarios/deposit_insurance.txt`。
仅报告扩展相对原规则新增的标记，输出在 `formal-check/posthoc.json`，不与上表合并。

| 预期标签 | 材料条数 | 新增标记条数 | 新增 ID |
|---|---:|---:|---|
| supported | 13 | 0 | 无 |
| not_supported | 29 | 0 | 无 |

支持材料新增误报为 0 条。这只描述这份材料上的新增标记。

## 实现口径

- `check_answer_extended` 先原样调用 `statute_check.check_answer`，完整结果置于 `original`，
  新增项级发现置于 `extensionFindings`。`flagged` 为两者 error 发现的并集。
- `recoveries` 是并行列表，用 `origin` 和 `findingIndex` 指向发现；不在原 finding 上加字段，
  便于逐项核对原规则结果。未触发引用 error 不生成回指。
- 复用原模块的 `Provision`、`normalize`、`parse_provisions`、`REF`、`SENTENCE`、
  `_bigrams`、`_coverage`、`_quoted_spans`、`_inside`、`_ref_value`、`_matches`、`_mentions`、`_ref_label`，
  以及 `ATTRIBUTION_MIN` 和 `ATTRIBUTION_GAP`。没有新增阈值或数值常量。
- 项级核对沿用每句第一个非逐字引用中的引用；所引条款项须存在。
  在所引款内逐项比较正文，最佳项对所引项使用原规则同样的最小覆盖率和差值条件。
  未写款号且同条不同款的同号项无法唯一归属时跳过，不假定第一款。
- 最佳项原文通过 `_mentions` 提及所引条款，或提及本条时，保留交叉引用豁免。
  `_mentions` 本身只解析条、款，不增加对“前项”等表达的解析。
- 回指独立逐层选择最佳条、最佳条中的最佳款、最佳款中的最佳项。
  每层第一、二名差值不足 0.25 或最佳覆盖率不足 0.6 即停止，位置为 `None`；
  保留该层最高覆盖率供审计，下层未计算覆盖率为 `None`。无次佳候选时用空匹配覆盖率零比较。
- 分析先验证运行归档和原分析目录的校验清单，逐条重算原规则，
  要求 flagged 集合与 findings 和归档完全一致，否则不写输出。
  输入与使用的规则、工具模块 SHA-256 写入两份 `posthoc.json`。

## 复算与校验

输出目录必须不存在或为空。下列目录名用于新一轮复算时须保持为空。

```sh
venv/Scripts/python.exe -B tools/competition/statute_posthoc.py analyze --run docs/competition/statute-check/20261021-pinpoint-run/run --output work/posthoc-locate-replay/a
venv/Scripts/python.exe -B tools/competition/statute_posthoc.py analyze --run docs/competition/statute-check/20261021-pinpoint-run/run --output work/posthoc-locate-replay/b
cmp work/posthoc-locate-replay/a/posthoc.json work/posthoc-locate-replay/b/posthoc.json
cmp work/posthoc-locate-replay/a/SHA256SUMS.txt work/posthoc-locate-replay/b/SHA256SUMS.txt
cmp work/posthoc-locate-replay/a/posthoc.json docs/competition/statute-check/20261022-posthoc-locate/analysis/posthoc.json
cmp work/posthoc-locate-replay/a/SHA256SUMS.txt docs/competition/statute-check/20261022-posthoc-locate/analysis/SHA256SUMS.txt
venv/Scripts/python.exe -B tools/competition/statute_posthoc.py analyze --run docs/competition/statute-check/20261021-formal/run --output work/posthoc-locate-replay/formal
cmp work/posthoc-locate-replay/formal/posthoc.json docs/competition/statute-check/20261022-posthoc-locate/formal-check/posthoc.json
cmp work/posthoc-locate-replay/formal/SHA256SUMS.txt docs/competition/statute-check/20261022-posthoc-locate/formal-check/SHA256SUMS.txt
venv/Scripts/python.exe -B tools/competition/statute_pinpoint.py analyze --run docs/competition/statute-check/20261021-pinpoint-run/run --output work/posthoc-locate-replay/pinpoint
cmp work/posthoc-locate-replay/pinpoint/analysis.json docs/competition/statute-check/20261021-pinpoint-run/analysis/analysis.json
git diff --stat -- backend/core/statute_check.py
mkdir -p work/posthoc-locate-replay/pytest
venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests --basetemp work/posthoc-locate-replay/pytest/t
```

分别在本目录、`analysis/`、`formal-check/` 执行 `sha256sum -c SHA256SUMS.txt`。
顶层清单覆盖 README、两个子目录的 JSON 和校验清单，不包含自身。
新增文件均使用 UTF-8、LF；没有修改 `.gitattributes`，纳入 Git 前应由审查方补充显式路径规则。

2026-10-22 实际验证：本次新增专项测试 `20 passed in 2.60s`；
当时完整工作区的全量测试原始末行如下（包含同时存在的其他新增测试）：

```text
1140 passed in 326.00s (0:05:25)
```

上述全部 `cmp` 无输出、退出码为 0；原规则文件 `git diff --stat` 无输出。
顶层 5 条、子目录各 1 条 SHA-256 记录全部显示 `OK`。
两次复算的 JSON 与子目录校验清单均与本归档逐字节一致。

## 边界与可能漏检、误报

本批是定向合成扰动，单一法规节选，多条变体共用底稿，不是独立样本。
规则读取完整法规节选，裁判只读取检索片段，不能视为严格等信息条件比较。
二元组覆盖不是语义判断：同词极性反转可能漏检，长句同时复述总领句和具体项可能达不到项级阈值；
两个相似款项可能无法回指。原文的条款交叉引用豁免可能放过错引，
例如 `paragraph_existing-S27-P1`；未解析的“前项”引用也可能导致误报。
一整句同时谈多个位置时，单个最佳位置可能误导；本次仅保守停止，不扩展多位置推断。
