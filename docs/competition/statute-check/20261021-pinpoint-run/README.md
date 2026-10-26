# 款项定位双提示运行（2026-10-21）

同一批款、项定位材料分别使用原提示与增加定位核对要求的提示，交给两个通用裁判作单次配对判断。本目录保存已完成运行与离线分析的原样副本，不发起新的模型请求。材料与规则离线结果见 [准备归档](../20261021-pinpoint-prep/README.md)。

**款级错误 31 条：规则检出 30 条，DeepSeek 从 1 条升到 22 条，但在支持对照上的误报从 2 条升到 7 条。项级「同款存在的另一项」3 条，规则全部漏检，DeepSeek 两种提示都检出。** Qwen2.5-72B 款级两种提示各检出 1 条，项级为 0 条与 1 条。结果不用于改变门禁或历史 FAIL。

## 材料、口径与模型

口径在运行前锁定于 [`run/package.json`](run/package.json) 的 `analysisPlan`。规则只把 error 级发现计为检出；裁判判 `contradicted` 或 `insufficient` 计为检出，`supported` 计为未检出。支持对照中同样的标记计为误报；失败与未执行单列，不填零冒充判断。

两种提示使用相同陈述、问题、原检索片段、模型参数与顺序配对；裁判只收到 question、statement、contexts，不收到预期标签、类型、规则结果或人审标签。提示词原文见 [`prompt.original.txt`](run/prompt.original.txt) 与 [`prompt.pinpoint.txt`](run/prompt.pinpoint.txt)。DeepSeek 请求名为 `deepseek-chat`，实际返回模型名为 `deepseek-flash`；另一裁判为 `Qwen/Qwen2.5-72B-Instruct`。

## 授权、执行停止规则与费用

授权人 Ll，日期 2026-10-21，预算 ¥2。授权原文见 [`run/authorization.json`](run/authorization.json) 的 `note`，照录如下：

> 作者授权原文：款项定位运行授权：授权人 Ll，日期 2026-10-21。预算上限 ¥2，按每次请求前预留计费，到上限即停，不重试。材料为 work/statute-injection/pinpoint-20261021/confirmed/，须校验 packageSha256 与授权绑定一致、67 处 review 全为 confirmed。不覆盖已有归档，输出到新的运行目录。 执行口径：每次请求发送前按「消息 UTF-8 字节数 + 256」作输入 token、maxTokens 作输出 token，以授权单价预留本次费用；已累计费用加本次预留超过 budgetCny 即停止、不发送。返回 usage 有效时按实际 usage 与授权单价累计；usage 缺失（含调用失败）时按本次预留额累计并停止；实际模型缺失、或实际计费超过本次预留时同样停止。不重试。按授权单价估算，不是供应商实际扣费；供应商侧没有费用自动封顶。

执行器每次发送前按「消息 UTF-8 字节数 + 256」预留输入 token、按 maxTokens 预留输出 token；累计估算加本次预留超过预算就停止、不发送。有效 usage 按授权单价累计；usage 缺失（含调用失败）按预留额累计并停止；实际模型缺失或按 usage 算出的费用超过预留额也停止。不重试。这里的预算守卫不代表供应商侧费用自动封顶。

[`manifest.json`](run/manifest.json) 记录 `stopReason=completed`；[`analysis.json`](analysis/analysis.json) 中 264 条记录全部有效，失败 0 条、未执行 0 条。按授权单价与返回 usage 估算 ¥0.64859010，`actualBillCny=null`，不是实际账单。单价与授权绑定哈希见授权文件。

## 结果

下表只报条数，错误行是检出条数，对照行是误报条数。

| 材料 | 条数 | 规则 | DeepSeek 原提示 | DeepSeek 加核对提示 | Qwen2.5-72B 原提示 | Qwen2.5-72B 加核对提示 |
|---|---:|---:|---:|---:|---:|---:|
| 同条存在的另一款 | 18 | 17 | 0 | 11 | 0 | 0 |
| 不存在的款 | 13 | 13 | 1 | 11 | 1 | 1 |
| 款级错误合计 | 31 | 30 | 1 | 22 | 1 | 1 |
| 同款存在的另一项 | 3 | 0 | 3 | 3 | 0 | 1 |
| 不存在的项 | 1 | 1 | 1 | 1 | 0 | 0 |
| 项级错误合计 | 4 | 1 | 4 | 4 | 0 | 1 |
| 原陈述对照（误报） | 14 | 0 | 1 | 2 | 1 | 1 |
| 正确定位对照（误报） | 13 | 0 | 1 | 4 | 1 | 1 |
| 省去款号对照（误报） | 4 | 0 | 0 | 1 | 0 | 0 |
| 支持对照合计（误报） | 31 | 0 | 2 | 7 | 2 | 2 |

从原提示到加核对提示的配对变化如下；全部对应 ID 见 `analysis.json` 的 `paired`。

| 裁判 | 新增检出 | 丢失检出 | 新增误报 | 减少误报 |
|---|---:|---:|---:|---:|
| DeepSeek | 21 | 0 | 5 | 0 |
| Qwen2.5-72B | 2 | 1 | 0 | 0 |

Qwen2.5-72B 新增检出 `paragraph_absent-S19`、`item_existing-S26-I1`，丢失 `paragraph_absent-S36`，误报无变化。

**任一检出的并集。** 规则与两种提示下两个裁判的并集，在错误 35 条中检出 34 条；唯一漏检为 `paragraph_existing-S27-P1`。规则因「前款」交叉引用豁免，两个裁判两种提示也都判为支持；规则机制见 [法条结构核对说明](../README.md#款项定位规则离线结果与双提示运行)。并集在支持对照上误报 7 条，全部来自裁判。

**S36 的 ID 层面事实。** 原提示下两个裁判唯一检出的款级错误都是 `paragraph_absent-S36`，而同一底稿的原陈述 `control-S36` 与正确定位对照 `control_pinpoint-S36` 也被两者判为不支持。逐条理由见 [`run/records.json`](run/records.json)：它们仍把「只说人民币存款」判为矛盾，与 [上一批正式运行](../20261021-formal/README.md#c-s36两个裁判把不完整判成矛盾) 记录的错法相同。因此，这条检出不能归因于核对了款号；不据此推断其他 ID 的原因。

## 剔除 S26 全部变体

按事先固定的敏感性口径，款级错误剩 30 条：规则检出 29 条，DeepSeek 原提示 1 条、核对提示 21 条，Qwen2.5-72B 两种提示各 1 条；并集检出 29 条。项级不剩任何材料。完整条数与 ID 见 `analysis.json` 的 `withoutS26`。

## 边界

- 定向合成扰动，只涉及单一法规节选，多条变体共用底稿，不是独立样本，不能推广总体效果。
- 项级只有 S26 一份底稿，且其三人历史标注不一致：V/A 为「支持」，B 为「无法判断」；作者此次确认预期不抹去历史分歧。
- 规则读取完整节选，裁判只读原检索片段，信息条件不同，不是严格等信息比较。
- 单次运行、不重试，裁判重判可能波动；只报条数，不作总体统计推断。
- 费用是按授权单价的估算，不是账单；DeepSeek 实际返回模型为 `deepseek-flash`。

## 文件与离线复算

`run/` 为运行原样副本，包含材料、授权、双提示、调度、逐次事件与原始响应、逐条结果、环境、输入和源码快照；`analysis/` 为离线分析原样副本。两目录分别保留自己的 `SHA256SUMS.txt`。本目录顶层清单覆盖 README.md、两个子清单及所有子目录文件，唯独不覆盖自身。

在本目录及两个子目录分别执行：

```bash
sha256sum -c SHA256SUMS.txt
```

在仓库根目录执行（输出须为新的空目录；不调用模型）：

```bash
venv/Scripts/python.exe -B tools/competition/statute_pinpoint.py analyze --run docs/competition/statute-check/20261021-pinpoint-run/run --output work/pinpoint-replay-new
```

复算的 `analysis.json` 应与归档逐字节相同；`rules.json` 与子清单也可逐字节比对。
