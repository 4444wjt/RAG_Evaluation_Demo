# T1 报告：前置检查发现预登记执行冲突，已停止

日期：2026-10-30。状态：**STOPPED_PREREGISTRATION_NOT_EXECUTABLE；T1 未通过验收，T2 未执行。**

按 task-brief.md 的“发现它无法执行时停下报告，不自行调整口径”停止。没有修改预登记，没有自行切换 B 臂的核查方式。全程无联网、无模型调用，未读取 `.env`；实际费用 ¥0。未执行任何 Git 状态写入命令。

## 1. 已完成的前置检查

- 已读取仓库根 AGENTS.md、docs/STATE.md、task-brief.md 和 preregistration.md。实际 HEAD 为 `936f3a9f9bb697ae21ffc636b54a841a0b13f3f3`；工作区原有大量改动，未使用其中的产品源码。
- 五份固定输入全部通过 `git show 05099e7:<路径>` 的二进制 stdout 取得；用 `git hash-object --stdin` 核对，每份均与预登记 blob 一致。完整来源、实际 blob、SHA256 和字节数见 [inputs/manifest.json](inputs/manifest.json)。固定提交解析为 `05099e7365fd734b26aa5737e10d5579033def2b`。
- 两个指定解释器文件均存在。静态读取安装包 METADATA：A 为 Ragas 0.1.21；B 为 ragchecker 0.1.9、refchecker 0.2.17。**未导入运行这些包，未验证全部运行依赖可用**，未安装或调整依赖。
- 离线 Python 命令使用 `-I -S -B`，并设置 `PYTHONDONTWRITEBYTECODE=1`、`LITELLM_LOCAL_MODEL_COST_MAP=True`，只执行标准库文件与 Git 字节核对。

## 2. 首要阻断：固定整句与 B 臂默认联合核查不兼容

预登记第 1、3 节要求两臂使用同一批固定陈述，不做陈述抽取；B 臂采用 RefChecker 默认联合分组，原提示词和解析冻结，只替换发送函数。

本机指定版本的实际源码为（下列路径均相对本目录）：

| 位置 | 实际行为 |
|---|---|
| `venv-ragchecker/Lib/site-packages/refchecker/checker/checker_base.py:49-50` | 公共入口默认 `is_joint=True`、`joint_check_num=5`。 |
| `venv-ragchecker/Lib/site-packages/ragchecker/evaluator.py:52-53` | RAGChecker 同样默认联合核查、每组 5 条。 |
| `venv-ragchecker/Lib/site-packages/refchecker/checker/checker_prompts.py:2` | 联合提示明确要求 `(subject, predicate, object)` 三元组。 |
| `venv-ragchecker/Lib/site-packages/refchecker/checker/llm_checker.py:104-105` | 每条 claim 用 `c[0]`、`c[1]`、`c[2]` 构造三元组；没有整句字符串分支。 |

固定输入 S01 是完整字符串“同一存款人在同一家投保机构所有被保险存款账户的存款本金和利息需合并计算。”。直接送入该联合分支，claim 位置会变成 `("同", "一", "存")`，完整陈述没有传给模型。这是源码及字符串索引的静态核对，**没有发出模型请求，也没有运行 RefChecker**。

改用单句模式会改变预登记的联合核查方式；先抽三元组会违反“不比较陈述抽取”；自行把整句塞进三元组某个槽位则是未登记的输入适配。均不能擅自采用。仅替换发送函数也不能修复发送之前已经发生的陈述截断。

证据：[T1-blocker-source.txt](T1-blocker-source.txt)，保留相应源码行号、源文件 SHA256 和 S01 静态示例；[T1-preflight.json](T1-preflight.json) 保存停止状态及版本信息。

## 3. 另需明确的原返回行为

`refchecker/checker/llm_checker.py:233-237` 用正则提取标签；`:144-150` 随后把少于 claim 数量的标签补成 `Neutral`，多出的标签截断。例如无法提取任何标签的非空文本，会先解析为 `[]`，再被补为一组 `Neutral`。若直接使用这个最终返回值，就会把解析失败记成“不支持”，与预登记“无结果”的要求冲突。

需要明确是否允许在保留原解析函数的同时，在外围检测缺失/多余标签并记录“无结果”；本轮未实现这种覆盖处理，也未修改原包。

原联合分支调用底层发送函数的位置是 `venv-ragchecker/Lib/site-packages/refchecker/checker/llm_checker.py:124-134`，其中 `:133` 传递 `custom_llm_api_func`。**这只是待接入位置，本轮没有替换发送函数。RefChecker 提示词与解析均未改动。**

## 4. T1 验收逐项状态

| 验收项 | 本轮结果 |
|---|---|
| 五份固定输入与 manifest | 已完成，5/5 blob 一致。 |
| 两个解释器分别测试、测试全部通过及日志 | 未执行；在前置源码核对时触发停止。没有测试通过声明。 |
| `--dry-run` 请求总数与预留总额 ≤ ¥8 | 未执行；请求总数及预留总额均为 `null`（未知），不能填 0 或认定预算验收通过。 |
| B 臂发送替换位置与原提示/解析不变 | 原调用位置已定位；替换未实施，原包未修改。 |
| T1-report.md | 本文件；明确记录阻断、范围和未验证项。 |

未创建 `run_compare.py`、`analyze.py`、`test_compare.py`、`raw/`、`results.jsonl` 或预算台账。没有生成模型实验结果、优势结论、summary.json、T2 report.md 或 T2 SHA256SUMS.txt。

## 5. 执行命令与证据

所有命令从仓库根执行。只用了文件读取、Git 只读命令和目录内的报告/输入写入：

1. `Get-Content -LiteralPath AGENTS.md -Raw`、`Get-Content -LiteralPath docs/STATE.md -Raw`、`Get-Content -LiteralPath work/competition/ragchecker-checker-compare-20261030/task-brief.md -Raw`、相同方式读取 `preregistration.md`。
2. `git --no-optional-locks status --short --branch`、`git log -1 --format='%H %s'`：核对实际状态，未改变分支或索引。
3. `Get-ChildItem`：检查任务目录和沿途的补充 AGENTS.md；`Get-Content` / `Select-String`：读取指定环境中的实际包源码。部分较长的终端输出被显示层截断，报告引用的关键代码另已完整摘录到 `T1-blocker-source.txt`。
4. `venv/Scripts/python.exe -I -S -B -`（标准库脚本经 stdin）：对五个预登记路径逐一运行 `git show 05099e7:<路径>`、`git hash-object --stdin`，通过后写出 `inputs/` 和 manifest；用 `git rev-parse 05099e7` 记录完整提交。源码、输入 JSON 和安装元数据只作静态读取。
5. 相同 Python 命令：保存源码片段及 SHA256、版本和停止记录；`apply_patch`：写本报告、校正静态示例的源码行号。
6. 同样的标准库命令做最终产物复核：重新核对 5 份输入 blob/SHA256、原任务文件及相关包源码 SHA256，检查未出现运行器、结果、台账等未执行产物。

辅助检索中 `rg` 不可用，已改用 PowerShell `Select-String`，没有安装工具；记忆索引没有本任务的匹配记录，未据此采用任何历史结论。

新增文件仅为：`inputs/` 中五份固定输入、`inputs/manifest.json`、`T1-blocker-source.txt`、`T1-preflight.json`、`T1-report.md`。没有修改 task-brief.md、preregistration.md、现有虚拟环境包、产品代码、冻结证据或 docs/STATE.md。

## 6. 未验证项与待决定事项

- **先由预登记制定方处理 B 臂输入表示与联合模式的冲突**；本轮不修改登记、不继续实现。原解析缺失标签的外围处理边界也需明确。
- 确定集 46 条、40/6 类别计数及指定六条未决集尚未执行核对；全部指标、预算到线、付费开关、泄漏检查、思考内容检测与假 LLM 测试均未验证。
- DeepSeek 官方文档未联网核实，非思考模式的具体关闭字段尚未实现或确认；标记为“待 T2 首个请求核验”，本轮没有官方来源核验结果。
- 请求计划、预留总额、真实模型名、usage 和返回结构均未知。当前实际模型调用为 0、实际费用 ¥0，不能据此推断后续预算是否满足。
- 本轮只是输入锁定及阻断核查，新增已确认研究分为 **[0,0]**；未计算项目总分或预期收益分。

恢复工作须先有可执行的任务/登记口径。本报告不视为 T1 通过，也不构成 T2 执行授权。
