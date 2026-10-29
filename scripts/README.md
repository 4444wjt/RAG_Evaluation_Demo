# 零依赖脚本

这些脚本不装评测库、不用密钥、不进虚拟环境，用 `python -I -S -B -X utf8` 就能直接跑；每个脚本的 docstring 第一段就是它自己的用法，改动前先读那一段。
它们做的是「拿已有材料复算一件事」，不重新调用模型，也不改已归档的结论。

## 想自己验一件事，就跑这个

先按你要验的事找，再去看脚本。括号里是输入从哪来。

| 你想验的事 | 跑哪个脚本（输入从哪来） | 输出与退出码 |
|---|---|---|
| 这条归档到底 PASS 还是 BLOCK | `gate_replay.py`（运行目录里的 `api-response.json`，或演示快照） | 逐指标对照表；0 通过、1 拦截、2 读不出输入 |
| 我自己的资料有没有条款引用错 | `statute_diagnostic.py`（按 `examples/statute_check/` 契约写的 UTF-8 JSON） | 结论与诊断清单，`--json` 出结构化报告；0 / 1 / 2 见下 |
| 门禁盯住的那些性质还成不成立 | `mutation_summary.py`（`mutmut junitxml` 导出的 JUnit XML） | Markdown 摘要，可直接写进作业摘要；0 正常、2 参数不对 |
| 引用版本对不对 | `citation_version_check.py`（CLM v1 请求 JSON，或此前导出的自包含复核包） | 三轴七分项的差异清单；0 正常、2 输入无效、3 复核重算不一致 |
| 资料被改动后应用会不会被带偏 | `robustness_check.py`（平台评测结果，或每行一条的题目 JSONL） | 四个子命令 `prepare` / `authorize` / `run` / `analyze`，产出材料包与报告 |
| 冻结的父条声明有没有绑定上 | `parent_context.py`（原文、分段 JSON、检索记录） | `binding.json` 与位点核对；0 已创建、2 未创建 |

## 完整清单

按功能分三组，组内不排序——增删脚本时不用回来改编号。

**门禁**

- `gate_replay.py`：用与正式评测同一个门禁函数离线重跑裁决，可选与基线逐题对比；想把门禁接进 CI、或想换个阈值试算的人用。
- `mutation_summary.py`：把变异测试报告整理成人能读的存活清单；改门禁代码、想知道测试还盯不盯得住的人用。

**核对与诊断**

- `statute_diagnostic.py`：自带资料与回答，离线核对条号归属、数额期限与情态词；有法规原文、想自查回答引用的人用。
- `robustness_check.py`：打乱、加噪、移除依据、反事实四种资料扰动，检验生成侧与核对侧；想检验自己的 RAG 应用是否真依据资料的人用。
- `citation_version_check.py`：批量检查引用版本，并离线重放复核包；引用会随法规修订失效、需要定期复查的人用。

**证据与格式**

- `parent_context.py`：准备冻结的父上下文，或为一个数据集生成绑定；需要固定「这一段属于哪一条」的人用。

## 退出码约定

退出码按脚本实际实现写，不按约定俗成的猜测：

| 退出码 | 含义 |
|---|---|
| 0 | 没有检出问题（`statute_diagnostic` 是「当前规则未检出」），或门禁裁决通过（`gate_replay`） |
| 1 | 检出问题，或门禁裁决拦截；CI 步骤会因此失败 |
| 2 | 输入无效、需要人工复核，或文件读不出来；**不是**「没有问题」，而是这次没验成 |

`citation_version_check.py` 另外用 3 表示复核包重算结果与保存报告不一致。`statute_diagnostic` 的 0 只表示现有规则没检出，不等于事实有依据，也不等于门禁 PASS。

## 输入从哪来：示例与契约

| 例子 | 示范什么 | 配哪个脚本 |
|---|---|---|
| `examples/anythingllm_shim/` | 把 AnythingLLM 包成仅回环的端到端适配层 | 配合鲁棒性体检接入第三方应用 |
| `examples/external_rag_app/` | 一个自己检索、自己生成的示例 RAG 应用，按契约返回回答与实际资料 | 配合端到端接入与鲁棒性体检 |
| `examples/parent_context/` | 原文、分段、检索记录与绑定文件的格式，含一份故意无效的检索记录 | `parent_context.py` |
| `examples/statute_check/` | 法条诊断请求的最小样例，故意引用不存在的第三款 | `statute_diagnostic.py` |

输入输出的字段契约在 `schemas/`，字段定义见各自文档；`examples/` 里的样例都按契约写，可以先照抄再改。法条诊断的完整说明见[法条结构核对](../docs/competition/statute-check/README.md)。

## tools/ 与 scripts/ 的分工

`scripts/` 给外部人复算：零依赖、固定输入、退出码可当 CI 步骤用。
`tools/competition/` 给作者生成证据：需要完整评测环境、会调用模型并产生费用，产出就是 `docs/competition/` 下的归档。
