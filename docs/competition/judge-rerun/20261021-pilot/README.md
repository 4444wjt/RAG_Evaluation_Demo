# 冻结回答多裁判重复评分试跑（2026-10-21）

冻结回答不重新生成即可独立重评分；本轮同一裁判重判两次，在固定的 10 题正向子集上，Qwen 与 GLM 的均值跨过 0.8 阈值，使探索性结论从 FAIL 变为 PASS。

这是已完成试跑的静态归档。整理归档时没有联网、调用模型或重新生成回答，也没有重算正式门禁。

## 范围与方法

只读使用 [AnythingLLM 正式归档](../../external-rag/20261021-anythingllm-qwen72b/README.md)的已评测回答（源文件哈希见 freeze.json），从该批 20 条正向样本中，按固定种子与题号的 SHA256 排序选取 10 条，未依据原分数选题。样本为 C03、F05、C04、F01、F04、C01、C05、D05、D04、D07。

Qwen2.5-72B、DeepSeek Flash、GLM-4-32B 各重复两次，每次独立进行陈述抽取与 NLI。冻结原 ChineseFaithfulness 提示、解析与公式，不修复转义格式，不运行其他指标或 Trace。配置为 temperature=0、max_tokens=2048，每阶段最多一次请求，无解析修复、无 HTTP 重试。与历史完整评测配置存在差异，不能据此替换旧分数。

共完成 60/60 个组合，获得 60 个可解析的数值评分（不代表语义正确）。模型请求及返回均为 120 次；输入 529182 tokens，输出 34714 tokens，用量未知请求为 0 次。按冻结最高价估算费用上限为 ¥1.62230796，用量不明的占用预留为 ¥0，预算为 ¥5。实际账单为空，估算费用不是实付金额。

每次请求前按完整上下文上限与最大输出的最高价持久化预留，完整 usage 返回后才释放差额；缺失 usage 保留预留。DeepSeek 缓存与闲时优惠未用于降低该估算。

## 实测结果

均值保留六位小数，完整数值见 summary.json。题级变化表示同题两次分数不相等的题数。

| 裁判 | 第一次 10 题均值 | 第二次 10 题均值 | 0.8 阈值（仅子集） | 题级评分变化 | 估算费用上限 |
|---|---:|---:|---|---:|---:|
| qwen72b | 0.670357 | 0.835714 | FAIL / PASS | 4/10 | ¥0.86422315 |
| deepseek | 0.914603 | 0.925714 | PASS / PASS | 1/10 | ¥0.411782 |
| glm32b | 0.781548 | 0.854762 | FAIL / PASS | 3/10 | ¥0.34630281 |

三裁判简单平均（仅描述）：第一次为 0.788836，子集观察为 FAIL；第二次为 0.872063，子集观察为 PASS。

数值评分算术独立复核：60 项一致，不一致项为空。机械字段核对发现，19 个组合的 NLI 返回陈述文本与抽取列表不完全相同，1 个组合的条数不同。文本差异不自动等同语义错误，本轮未改原评分或过滤这些组合。表中保留原流程的全部数值，包括上述中间步骤异常；这些探索性 PASS 不是经新增契约校验后可用于放行的结论。

HTTP 响应耗时：p50 约 3.40 秒，p95（最近秩、描述性）约 17.27 秒；精确值见 analysis.json。

## 解释边界

以下保留原完成报告「解释边界」的全部限制：

这轮能证明冻结回答可不重新生成而被独立重评分，并测得调用用量与重复结果。不能证明多数或平均裁判更正确，也不能证明减少误报。

算术复核只说明分子分母计算一致，不证明抽取忠实、NLI语义正确或评分能代表回答整体质量。

每次陈述抽取和NLI使用同一裁判，跨裁判差异同时包含抽取陈述集合差异与蕴含判定差异，不能只归因于NLI裁判质量。

无独立正确性标签，accuracy、真实误报率与裁判质量排名均为空。两次重复与单批10题不支持稳定性总体估计或显著性结论。

均值基于这10条正向子集，不是原正式20条正向样本的门禁；其PASS/FAIL仅为固定0.8阈值的探索性观察。原正式FAIL与归档分数不变。

裁判服务名可指向更新后的模型权重，实际返回model/system_fingerprint保留在原始响应中，不宣称锁定不可变模型。

另须保留配置与费用限制：本轮配置与历史完整评测不同，不能替换历史结果；估算费用上限不是服务商账单。

## 失败与隔离

最初的目录 judge-gate-pilot-20261021 的 60 个组合均在本地组装记录时因重复关键字失败，模型请求为 0、估算费用为 ¥0。原失败记录保留，修复后在 judge-gate-pilot-20261021-r2 使用相同样本执行；两目录不是两轮付费采样。

归档整理时只读核对了前次 summary.json 与 results.jsonl：所有失败均记录为 METRIC_EXCEPTION_TypeError。其汇总和原文件哈希记录在 [archive-audit.json](archive-audit.json) 的 prior_unpaid_attempt 中；前次失败目录原件未收录于本归档。因此公开读者能核对本次记录的汇总，不能只凭本包重新审计前次全部失败原件；重复关键字的具体原因沿用原完成报告的说明。

r2 原离线安全检查 10 项通过，含真实适配器的模拟 HTTP 路径与独占锁；不是效果准确率测试。原始输出见 [offline-tests.txt](offline-tests.txt)。此次归档未重新运行这些 Python 测试：原脚本按原目录位置推导仓库根目录，放在本归档位置不能直接运行。归档内容改由 verify_archive.js 离线复核（见下文）。

原试跑记录为「受保护文件哈希一致：True；Git 状态前后一致：False」。Git 状态不一致意味着工作区整体隔离项未通过。运行期间其他路径的新增改动见 [final-audit.json](final-audit.json)，不能从状态变化直接推断改动者。原试跑未安装依赖、未改生产代码或配置、未提交或推送。以上是原试跑的历史记录，不代表当前工作区状态。

## 复核入口与完整清单

| 文件 | 用途 |
|---|---|
| [freeze.json](freeze.json) | 固定样本、抽样规则、配置、原文件哈希与历史隔离基线 |
| [inputs.jsonl](inputs.jsonl) | 冻结的问题、回答、上下文与输入哈希 |
| [schedule.json](schedule.json) | 组合及请求顺序 |
| [prompts.json](prompts.json) | 原提示定义 |
| [pricing.json](pricing.json) | 冻结价表、用量上限、历史价格来源链接（本次未联网复查） |
| [budget-ledger.jsonl](budget-ledger.jsonl) | 逐请求预留、响应信息、usage 与估算结算 |
| requests/ | 全部 240 个请求体及原始响应；文件名见哈希清单 |
| [results.jsonl](results.jsonl) | 每个组合的评分、状态与请求标识 |
| [arithmetic-audit.json](arithmetic-audit.json) | 抽取、NLI 陈述及逐项算术复核 |
| [analysis.json](analysis.json) | 中间字段差异、用量、耗时与描述性平均 |
| [summary.json](summary.json) | 三裁判各次均值、题级差异、费用与历史隔离结果 |
| [final-audit.json](final-audit.json) | 原试跑最终审计及 Git 状态差异 |
| [execution.json](execution.json) | 原试跑起止时间与完成状态 |
| [connectivity.json](connectivity.json) | 原试跑前连通性记录；非本次联网检查 |
| [offline-tests.txt](offline-tests.txt) | 原离线安全测试输出 |
| [console.log](console.log) | 原试跑逐组合日志 |
| [run_pilot.py](run_pilot.py) | 原执行脚本快照，含可发起付费请求的入口；仅作为审计材料 |
| [test_pilot.py](test_pilot.py) | 原安全测试快照 |
| [analyze_report.py](analyze_report.py) | 原分析脚本快照，不能直接用于当前归档的只读复算 |
| [archive-audit.json](archive-audit.json) | 每个复制文件的原始与归档哈希、扫描复核、前次失败汇总及收录说明 |
| [verify_archive.js](verify_archive.js) | 新增的只读离线复算入口，仅使用 Node.js 标准模块 |
| [SHA256SUMS.txt](SHA256SUMS.txt) | 除自身外全部文件的相对路径与 SHA256，按路径排序 |
| README.md | 本说明，由原完成报告改写 |

未收录 offline-tmp/ 与原完成报告文件。原样复制 258 个文件，脱敏修改 0 个；逐文件源文件哈希与归档哈希全部一致，完整比对见 archive-audit.json 的 files 数组。

逐文件检查了 Windows、Unix 和 MSYS 机器绝对路径、本机用户名、密钥形态、Bearer、Authorization、api_key 赋值及内部材料引用。没有实际敏感项需要替换。人工复核的词法命中为 run_pilot.py 中动态组装鉴权头的代码、analyze_report.py 中关于鉴权的说明及原报告输出文件名；它们均不含凭据或私密材料内容，保留原字节。提示中的 schema:\n 形式不是机器路径。环境变量名和公开模型服务地址也保留。未读取凭据配置文件；未获得维护者内部敏感词清单，不能声称完成该清单的专项检查。

### 离线校验命令

在本目录执行：

```sh
sha256sum -c SHA256SUMS.txt
node verify_archive.js
```

第二条命令只读归档文件，校验哈希清单覆盖范围、原样复制哈希、冻结文件哈希、请求与响应哈希，并从原始响应重算逐题分数、各次均值、题级差异、用量、费用和耗时；不读取凭据配置、不联网、不写文件。评分复算只去掉响应外围空白与 Markdown 代码围栏后解析 JSON，不作语义修复，不代表效果准确性检验。

不要直接执行原 analyze_report.py 来复算本归档：它导入的 run_pilot.py 按原位置推导仓库根目录，summary() 依赖本机已安装包的路径，还会覆盖 summary.json；随后还会覆盖 arithmetic-audit.json、analysis.json 并生成原报告。原脚本按字节保留，没有为适配公开目录而修改。

### 数字对应字段

以下路径均从相应 JSON 根对象起算；长度、计数与六位小数显示为明确的派生值。

| 数字或观察 | 文件与字段／计算 |
|---|---|
| 20 条正向总体、10 条子集、样本题号与固定种子 | freeze.json：population、selection、sample_ids（长度）、seed |
| 60 个组合、120 次预计请求 | freeze.json：comparisons、expected_base_requests；summary.json：planned_combinations、attempted_combinations、finite_scores、reserved_requests、completed_http_responses |
| temperature=0、max_tokens=2048、每阶段一次、无修复或 HTTP 重试 | freeze.json：parameters.temperature、max_tokens、n、parsing_repairs、http_retries |
| 三裁判两次均值：0.670357 / 0.835714、0.914603 / 0.925714、0.781548 / 0.854762 | summary.json：model_results.{qwen72b,deepseek,glm32b}.repetitions.{1,2}.all_ten_mean，显示到六位小数 |
| 0.8 阈值下的子集 FAIL / PASS | archive-audit.json：exploratory_subset_threshold；summary.json：model_results.*.repetitions.*.exploratory_subset_gate_at_0_8；analysis.json：unweighted_ensemble_descriptive[*].subset_gate_at_0_8；阈值观察的比较式保留于 run_pilot.py 的 summary() |
| 4/10、1/10、3/10 题级变化 | summary.json：model_results.{qwen72b,deepseek,glm32b}.paired_cases 中 absolute_difference > 0 的数量／数组长度 |
| ¥0.86422315、¥0.411782、¥0.34630281 | summary.json：model_results.{qwen72b,deepseek,glm32b}.estimated_ceiling_cost_cny |
| 三裁判简单平均 0.788836 / 0.872063 | analysis.json：unweighted_ensemble_descriptive[*].mean，显示到六位小数 |
| 60 项算术一致、无不一致项 | analysis.json：arithmetic_scored、arithmetic_mismatches；arithmetic-audit.json：[*].arithmetic_matches |
| 19 个文本差异组合、1 个条数差异组合 | analysis.json：nli_text_changed_combinations、nli_count_changed_combinations 的长度 |
| 输入 529182、输出 34714、用量未知请求 0 | analysis.json：tokens.prompt_tokens、completion_tokens、unknown_usage_requests |
| p50=3.3985000000011496 秒、p95=17.26599999999962 秒 | analysis.json：latency.p50_seconds、p95_nearest_rank_seconds |
| 总估算 ¥1.62230796、未知占用 ¥0、预算 ¥5；实际账单为空 | summary.json：estimated_ceiling_cost_cny、unknown_usage_reserved_cny、budget_cny、actual_paid_bill_cny |
| 前次 60 个本地失败、0 请求、¥0 费用 | archive-audit.json：prior_unpaid_attempt.local_failure_records、summary.attempted_combinations、reserved_requests、completed_http_responses、estimated_ceiling_cost_cny |
| 原安全检查 10 项通过 | archive-audit.json：original_offline_tests.passed；原始依据为 offline-tests.txt |
| 保护哈希 True、Git 状态 False | summary.json：protected_files_unchanged、git_status_unchanged；final-audit.json：protected_changed_files、git_status_unchanged |
| 未重生成回答、未重算历史完整门禁、无正确性标签 | summary.json：answer_generation_calls、full_historical_gate_recomputed、independent_correctness_labels；analysis.json：labels、accuracy、false_positive_rate、model_quality_ranking |
| 原样 258、脱敏 0、请求与响应文件 240 | archive-audit.json：byte_identical_files、redacted_files、request_response_files；files 为逐项哈希清单 |

## 入库注意事项

console.log 命中仓库 *.log 忽略规则，建议保留为复核材料；另行授权入库时需对它使用 git add -f。其他归档文件未被忽略。本次未修改 .gitignore。

本次未修改已有 .gitattributes。建议后续追加以下目录级规则：

```gitattributes
docs/competition/judge-rerun/20261021-pilot/** -text
```

-text 关闭本目录全部文件的 Git 文本换行转换，保留源文件的原始字节，使 Windows 与 Linux 检出得到相同字节。README、新增核验脚本与 JSON、SHA256SUMS 使用 LF；复制文件不转换换行。该规则尚未追加，正式入库前须处理，避免跨平台转换破坏本包哈希。

## 与既有材料的关系

本试跑补充 [门禁可信度四问](../../gate-credibility.md) 中关于裁判重复性的证据；方法与 [独立裁判对照](../../../judge-comparison.md) 都使用冻结回答，但样本、配置和指标范围不同，不能直接替换或合并历史分数。本任务未修改这两份材料。
