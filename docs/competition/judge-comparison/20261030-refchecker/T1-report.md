# T1 报告：修订 1 离线实现、测试与干跑

日期：2026-10-30。状态：**T1_ACCEPTANCE_PASSED；仅完成 T1，T2 未执行。**

依据未修改的 `preregistration.md` 与已批准的 `preregistration-amendment-1.md`。本轮覆盖重写本报告；上一轮 `T1-report-r1-stopped.md`、旧 `T1-preflight.json` 和 `T1-blocker-source.txt` 保留。

全程不联网、无真实模型调用，未读取 `.env`，实际 API 费用 **¥0**。未安装依赖、未执行 Git 写入命令。只写本任务目录；按本轮更窄授权，未更新 `docs/STATE.md`、`.gitattributes` 或其他仓库文件。

## 1. T1 验收结果

| 验收项 | 结果与证据 |
|---|---|
| 固定输入 | 复用 inputs，5/5 重新通过 `git hash-object --stdin`，并与 `git show 05099e7:<路径>` 二进制结果逐字节相同。原 `inputs/manifest.json` 保留；再核结果见 `T1-preflight-r2b.json`、`T1-acceptance.json`。 |
| 人工确定集 | 重新计算为 46 条：支持 40、不支持 6；未决恰为 S12、S14、S16、S18、S26、S35。逐条与原 analysis.json 的 state/human 一致。 |
| 两套环境测试 | A **26/26**、B **28/28**，失败/跳过均 0。共 54 次执行、32 个不同测试，其中 22 个共享测试在两环境各执行一次。日志：`T1-test-A-r3.log`、`T1-test-B-r3.log`。 |
| 干跑 | **216 次请求，总预留 ¥6.249168 ≤ ¥8**。全部请求、完整提示、payload、字符数、逐项预留见 `dry-run-plan.json`，可读清单见 `T1-dry-run-r2.log`。 |
| B 发送替换 | 仅在进程内替换原底层发送函数，位置见第 3 节；原提示与解析文件未改。 |
| 可复算验收 | `verify_t1.py` 核输入、提示绑定、预算、日志、保护文件、真实运行产物缺席。结果与交付文件 SHA256 见 `T1-acceptance.json`。 |

本轮新增已确认研究分 **[0,0]**。这是已定方案的执行准备，未产生比较优势；必做项按输入、实现、测试、干跑、复核的依赖顺序执行。

## 2. 请求计划与费用

| 臂 | 每轮请求 | 三轮请求 | 三轮提示字符数 | 三轮预留 |
|---|---:|---:|---:|---:|
| A：ChineseFaithfulness 原 NLI | 20 | 60 | 438780 | ¥2.738160 |
| B：RefChecker 原生整句核查 | 52 | 156 | 238776 | ¥3.511008 |
| 合计 | 72 | **216** | 677556 | **¥6.249168** |

顺序为 A1、B1、A2、B2、A3、B3。完整运行将产生 312 条陈述级结果（52×2×3），不能与请求数混用。两次干跑计划逐字节相同，见 `T1-dry-repeat.json`。

预留严格采用 `提示字符数 × 2 × 2/1000000 + 2048 × 8/1000000`，以 Decimal 计算。每次发送前检查「已结算实际估算＋未结算预留＋本次预留」，达到 ¥8 即拒绝发送。结算释放该请求预留，缓存命中/未命中/输出分别按 ¥0.04/¥2/¥8 每百万 token 计算。缺 usage 保留未知与未结算预留，不填零。**计划预留不是实付。**

## 3. 实现与原实现边界

**A 臂。** `run_compare.py:26` 加载固定 blob 副本 `inputs/ragas_compat.py`；`:41` 调用继承的 `_create_nli_prompt`（安装包 `ragas/metrics/_faithfulness.py:197`）；`:45` 调用原 `_faithfulness_output_parser.parse`。跳过陈述抽取，不调用带 LLM 修复重试的 aparse。原 contexts JSON 转义及陈述 JSON 字符串的再次序列化保留，不为降低预留改提示。

**B 臂。** `run_compare.py:79` 调用原 `LLMChecker.check(..., is_joint=False, merge_psg=False, max_reference_segment_length=0)`。每条 claim 是完整 str；4 段 contexts 以换行拼为单一 reference。原包 `llm_checker.py:192` 进入整句分支，使用原 SUBSENTENCE_CLAIM_CHECKING_PROMPT，并按原实现把题面前置到 reference。

**B 臂发送替换的精确位置：** `run_compare.py:77` 的 `patch.object(module, 'get_model_batch_response', replacement)`，对应本目录 `venv-ragchecker/Lib/site-packages/refchecker/checker/llm_checker.py:206` 的原底层发送调用。只在该调用作用域替换并自动恢复；没有改包文件、提示、check/_check 或原标签优先级（原解析从 llm_checker.py:222 开始）。

M2 的两个结果分别保存：原解析返回/异常、外围 judgment。B 无标签文本的原解析可能为 Neutral，外围仍记「无结果」，不算不支持。A 原解析异常或条数不符时，该请求对应陈述记无结果；缺 verdict 引发的原 Pydantic 异常保留。计分只使用外围 judgment。空 content 计接口错误；B 原空字符串触发 TypeError 的行为也已保留测试。

M3：共同 payload 固定 deepseek-flash、temperature 0、max_tokens 2048。B 原路径传入的 max_new_tokens=10 作为元数据记录，由共同客户端覆盖为 2048。HTTP 无重试，不自动跟随重定向。

`--run` 在导入第三方包、读取输入/密钥前检查 VS_ALLOW_PAID=1。接口错误率以每臂跨三轮累计请求为分母，不用陈述数；恰好 10% 不停，超过 10% 停。预算到线、显式思考字段/标签、密钥回显触发停止；密钥回显先脱敏，不能写入 raw。

**B 仅代表 RefChecker 0.2.17 整句核查器，不是 RAGChecker 默认的“三元组＋联合”配置。** A 原 NLI 不用题面，B 按已批准 M1 使用题面；未擅自消除这项上游行为差异。

7 份相关 Ragas/RefChecker 源文件与安装 RECORD 的 SHA256 一致；上轮登记的 4 份 RefChecker/RAGChecker 源码 SHA256 也一致。证据：`T1-source-integrity.json`、`T1-acceptance.json`。

## 4. 执行命令、测试与改动文件

以下主要命令均从仓库根执行，标准库父进程保存 UTF-8 stdout/stderr、原命令与退出码：

```powershell
& 'venv/Scripts/python.exe' -I -S -B 'work/competition/ragchecker-checker-compare-20261030/T1-preflight-r2.py' r2b
& 'venv/Scripts/python.exe' -I -S -B 'work/competition/ragchecker-checker-compare-20261030/test_compare.py' --arm A
& 'work/competition/ragchecker-checker-compare-20261030/venv-ragchecker/Scripts/python.exe' -I -S -B 'work/competition/ragchecker-checker-compare-20261030/test_compare.py' --arm B
& 'venv/Scripts/python.exe' -I -S -B 'work/competition/ragchecker-checker-compare-20261030/run_compare.py' --dry-run
& 'venv/Scripts/python.exe' -I -S -B 'work/competition/ragchecker-checker-compare-20261030/verify_t1.py'
```

辅助操作为 Get-Content/Select-String 只读检索、`git --no-optional-locks status --short`、`git rev-parse HEAD`、输入用 git show/git hash-object，以及标准库哈希和内存 AST 核查。HEAD 实查为 936f3a9f9bb697ae21ffc636b54a841a0b13f3f3；未以任务单的分支领先/落后文字作为依据。

测试覆盖原解析与映射、无标签/缺 verdict/条数错位/空响应、确定集及原分析冲突、预算到线/释放/缺 usage、付费开关、提示防泄漏、接口错误率、无重试、明文及 JSON 转义密钥回显、思考字段及非 JSON 思考标签、全部指标与优势规则、不完整结果拒绝比较。

另外，用共同客户端的假 transport 完整执行 A 的 20 个请求、B 的 52 个请求，各生成 52 条构造结果，检查 raw、原解析、外围结果、results 和台账的连接。全部测试阻止 socket connect/DNS/sendto；假调用不会触及真实模型。测试中的 VS_ALLOW_PAID 仅在 mock 作用域设置，不读 .env。假 raw/results/ledger 只在 `T1-tmp/fake-*`，日志标记 SYNTHETIC_ONLY，不属于实验数据。

| 新增或更新 | 用途 |
|---|---|
| run_compare.py、compare_common.py | 两臂适配、共同客户端、预算、停止条件和编排。 |
| analyze.py | 先核确定集，再算三轮一致条数、40/6 命中、翻转、6 未决逐条判定、解析失败、模型名及 usage。严格执行均值与最差一轮两项优势条件。无结果保留分母；翻转按全部 52 条二元判定统计，另列含无结果的状态变化。 |
| test_compare.py、verify_t1.py | 假 LLM 测试与可复算验收。 |
| T1-preflight-r2.py、T1-preflight-r2*.json、T1-import-*-r2*.log | 再核 blob、导入与预检过程。 |
| dry-run-A.json、dry-run-B.json、dry-run-plan.json | 原提示捕获与全部请求计划。 |
| T1-test-*-r1/r2/r3.log、T1-dry-run*.log、T1-dry-repeat.json | 所有测试轮次、干跑和一致性记录。 |
| T1-source-integrity.json、T1-acceptance.json | 包完整性、验收和交付哈希。 |
| T1-tmp/ | 本目录内的临时数据和明确标记的假响应产物。 |
| T1-report.md | 本轮覆盖重写。 |

失败保留：首轮 A 为 22 通过/1 失败，新增测试误按单层 JSON 查找提示；改为两次解码逐字核对，原提示未改。首轮 B 25/25；第二轮 A 24/24、B 26/26；补充密钥转义、非 JSON 思考两个反例后的最终结果为 A 26/26、B 28/28。原失败日志未覆盖。

最初 B 导入触发 dotenv 自动读取，被审计钩子在读取前阻止；使用已安装 python-dotenv 自带的 PYTHON_DOTENV_DISABLED=1 开关后，两环境导入通过，未读取密钥。报告初次写入遇到 PowerShell 管道中文编码问题，已使用原生 UTF-8 写入并回读核对。

## 5. 未验证项与待决定事项

1. **非思考关闭字段：待 T2 首个请求核验。** 实现采用 `thinking: {"type": "disabled"}`；待核官方出处为 [DeepSeek 思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode)。本轮未访问网页，不能声明已核实当前 deepseek-flash/V4.1 的适用性。检测非空 reasoning_content/reasoning/thinking 和明确思考标签；无标记自然语言不能可靠自动识别，“未检出”不证明无思考。
2. 真实网络、鉴权、供应商当前模型版本、返回模型名、真实 usage、限流、截断及账单均未验证；缺失值保持未知。本轮 ¥0 不是 T2 费用预测。
3. 只用构造响应核对分析公式，没有真实比较指标或优势结论，没有执行分析脚本生成 T2 报告。结果不完整时，分析脚本拒绝给出比较结论。
4. T1 当前无须进一步改登记口径；是否审核通过并派发 T2，由任务制定方决定。本报告不扩大为 T2 授权。

本目录根部未生成真实 raw/、results.jsonl、budget-ledger.jsonl，也未生成 T2 的 summary.json、report.md、SHA256SUMS.txt。两份预登记、任务单、5 份固定输入和上一轮停止报告保持原字节。
