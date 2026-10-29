# 复现与当前 Web API 运行材料

## 三种证据分别保存

1. **冻结 Demo 展示重现**：不调用模型；验证首页、六个视图、作者核验和历史 FAIL 能否按说明打开。
2. **新批次当前 Web API 真实运行**：通过既有 `POST /evaluate` 重新评测新案例，保存输入、配置和产物。
3. **旧冻结 Demo 模型复跑**：需要旧运行条件和新一轮模型调用，本轮新案例实验不能替代它。

本轮不更改 `credibility-review.json` 中的复现状态，也不把自动浏览器验收写成人工复核。实测结果见 [本轮验收记录](validation.md)。

## 无密钥展示重现

推荐 Node.js 24（24.15.0 及以上）。在独立源码目录运行：

```powershell
cd frontend
npm ci
npm run build
npm run preview
```

访问 `http://127.0.0.1:5173/#/lab/demo`，检查六个导航入口、三个模型、13条作者核验及历史 FAIL。没有后端时“服务未连接”提示正常；不能点击开始评测。检查过程中不应出现 `/api/evaluate` 请求。

本轮独立副本不复制 `.env`、`node_modules`、Python环境、本地模型、向量库或旧运行结果，从锁文件重新安装前端依赖，并使用全新浏览器上下文。它在**同一台 Windows 机器**完成，不能描述为另一台机器或全新操作系统验收。源码包含尚未提交的文件，因此同时记录文件哈希，不能仅用 Git HEAD 表示这次源码版本。

## 冷启动计时实测

2026-10-21（UTC 2026-10-22 02:07–02:12），在作者的 Windows 11 机器上按 README 的无密钥路径，从公开仓库重新走了一遍：新的空目录，空的 npm 缓存，不复用本地依赖。克隆的是 Gitee `master`，提交 `9b23382`；Node 24.21.0、npm 11.19.0、Git 2.45.1。

| 步骤 | 命令 | 用时 | 结果 |
|---|---|---:|---|
| 克隆 | `git clone https://gitee.com/kkbushishou/rag-hallucination-evaluator.git RAG-Reliability-Lab` | 13 秒 | 成功 |
| 安装前端依赖 | `cd frontend`，`npm ci`（空缓存） | 2 分 31 秒 | 退出码 0 |
| 启动 | `npm run dev` | 约 1 秒 | 首页返回 200 |
| 查看演示 | 打开 `http://127.0.0.1:5173/`，点「查看演示」 | — | 显示 25 题正式运行回放与门禁 FAIL；只有 `/api/health` 因没起后端返回 502，没有评测请求 |
| 前端单元测试 | `npm test` | 25 秒 | 16 个文件、280 项通过 |
| 类型检查 | `npm run typecheck` | 2 秒 | 通过 |

命令本身合计约 3 分 12 秒。加上操作浏览器的时间，从开始克隆到看到演示约 4 分钟。

边界：同一台机器、作者自己的网络；Node 与 Git 已预装，不计安装工具的时间；清空的只是 npm 缓存，系统层网络缓存没有清。浏览器由 Claude 的内置浏览器操作，不是人工计时。Python 测试、Docker 部署和真实评测不在这次计时内。

**Windows 长路径。** 第一次计时把仓库克隆在约 150 个字符深的临时目录下，检出失败，报 `Filename too long`。仓库里最长的相对路径有 140 个字符，Windows 默认路径上限是 260 个字符，所以克隆目录本身超过约 118 个字符就会失败。两种办法：克隆到较短的路径（例如 `C:\src`），或者克隆时加 `-c core.longpaths=true`（只对这个仓库生效，不改全局配置）。后一种在 154 个字符深的目录下实测检出完整。

```powershell
git clone -c core.longpaths=true https://gitee.com/kkbushishou/rag-hallucination-evaluator.git RAG-Reliability-Lab
```

## 已完成与未执行的批次

| 材料 | 状态与边界 |
|---|---|
| `20261005T101847Z-pilot-c72cb679` | 历史旧版本5题草稿预跑已完成：HTTP 200，5条回答，106,955 token、64次逻辑调用；未使用中文兼容层 |
| `20261005T131353Z-pilot-35f623cb` | 修复后5题技术验证已完成；用于确认中文句子输入和归档链路，不替代正式案例结果 |
| `20261005T144504Z-full-39511424` | 25题单模型正式运行已完成：25/25回答、325次完整调用、558,958 token；正向 Faithfulness 0.7375 低于0.8，原始 Gate FAIL；[公开证据](evidence/20261005T144504Z-full-39511424/README.md) |

本次正式运行使用已锁定的题集、原文和 case-manifest，原始 run 独立保存且未覆盖旧run。结果是单一金融材料固定场景，不是跨领域 Benchmark 或总体准确率证明。25题新分析仍为 AI 辅助、待人工确认；旧13条作者核验不扩展到本批。未来任何付费运行都必须重新授权，不能复用历史授权单自动执行。

## 中文兼容版本与入口范围

项目内 `backend/adapters/ragas_compat.py` 提供 `zh-sentence-v1`，仅覆盖 Answer Correctness / Faithfulness 的中文句子输入准备，不改Ragas 0.1.21、指标公式、权重、裁判定义、Trace或门禁阈值。纯英文沿用原实现，旧归档不重算。离线通过仅说明适配和回归检查成功，不能保证AI Judge的最终判定正确。

当前CLI和Gradio通过 `main.py.run_pipeline` 委托 `backend.core.pipeline.evaluate`，与Web API共享编排，因此新运行都会使用兼容层。`_legacy_run_pipeline` 保留旧实现但不是正常入口；仅 `main.py` 未改动不能证明CLI与适配隔离。不同入口的模型工厂、参数和校验仍需分别记录。

后续每次运行使用新的归档目录，记录sentence adapter、Ragas及依赖、提示和Trace源码哈希、请求及实际模型、Embedding来源与参数、时间和工作区状态。缺失版本如实标记，不事后猜测。新旧adapter分数不能当作完全相同条件直接比较；本次修复后5题分析见[公开证据](evidence/20261005T131353Z-pilot-35f623cb/README.md)。

## 固定25题与作者确认

题集及完整确认卡见 [金融法规节选25题](../../scenarios/competition_25/README.md)。首版材料曾是 AI 整理草稿；当前版本已由作者确认，历史说明不代表本次状态。

准备范围：10题直接可答、5题多条件／例外、5题材料缺失、5题错误前提。后者可以依据原文纠正，属于 `positive`；材料缺失为 `negative`。它们是同一节选中的25个不同问题，部分共享法条，不是25个独立领域或统计独立样本。

作者Kk已于2026-10-05实际检查全部题目、参考答案和来源，`case-manifest.json` 的既有 `authorValidation` 已填入 `status: confirmed`、真实显示姓名、实际日期和检查范围。不得复用旧13条回答的人审完成说明。输入与原文改动后必须重新确认对应哈希。

## 独立运行工具

使用既有 Python 3.11 环境，安装依赖以 `requirements.txt` 为准，提前准备 Embedding 缓存和模型凭据。

默认只准备材料，**没有模型调用**：

```powershell
.\venv\Scripts\python.exe tools/competition/run_evidence.py --batch pilot
```

以下命令仅供作者确认、版本固定后执行；会调用提供方并产生费用：

```powershell
.\venv\Scripts\python.exe tools/competition/run_evidence.py --batch pilot --execute
```

检查预跑调用数、token、耗时和失败材料，再决定25题完整运行：

```powershell
.\venv\Scripts\python.exe tools/competition/run_evidence.py --batch full --execute
```

默认单模型 `deepseek-chat`，不自动扩展另外两个模型。运行工具不是新评测算法：复制当前 `backend/` 原始字节及冻结输入，使用同一 Python 环境，在独立本地 API 单进程通过 HTTP 请求调用现有流水线。默认端口8002只服务此次归档，结束时退出；绝不附着到原工作台API或同时运行CLI。25题执行前必须完成作者确认和单独付费授权，见[最终启动清单](formal-run-checklist.md)。

产物在 `work/competition/runs/<运行ID>/`，每次使用新目录，原报告、向量库和历史不被覆盖。这个目录默认不入库；对外分发必须先检查凭据、日志、机器路径及内容权限，不自动上传。

## 归档包含什么

| 文件 | 意义 |
|---|---|
| `manifest.json` | 代码提交与脏工作区状态、实际源码哈希、运行状态、题ID、参数、配置、耗时和产物哈希 |
| `request.json` | 实际发往当前API的请求；仅一个模型，门槛沿用0.8 |
| `project/backend/` | 本次真正使用的原始后端代码副本；不改算法或接口 |
| `project/scenarios/` | 原文、固定题集和确认材料；不复制旧评测结果 |
| `api-response.json` | HTTP返回的原回答、上下文、Trace、指标及原始门禁说明；失败响应也保留 |
| `project/` 下的CSV／HTML／PNG／历史 | 本次真实流水线生成的产物，不使用根目录残留报告 |
| `packages.json` | 实际Python依赖版本 |
| `runner/run_evidence.py` | 新运行准备时冻结的归档工具源码；隔离API使用此副本启动，不回读后续修改的工具 |
| `project/main.py` | 入口关系审查所用源码副本；新归档仍只调用Web API，不执行CLI |
| `runtime.json`、`prompt-definitions.json` | 隔离API实际配置、五项指标提示词定义及哈希；配置观测失败如实标未知 |
| `llm-events.jsonl` | 本地只读回调观察的实际提示词、生成文本和提供方token；不保存HTTP凭据头 |
| `usage.json` | token及返回模型名的日志汇总；未报告用量不能解释成零消耗，费用仍未知 |
| `answers.json`、`metrics.json`、`traces.json` | 按原API响应分别组织的阅读副本；不重算原指标、Trace或门禁 |
| `result-coverage.json` | 分别统计指标有效／缺失和Trace非空／空／缺失／不可解析，不推断空Trace原因 |
| `server.log` | 启动和运行过程日志；输出时遮盖已知环境密钥值 |
| `checksums.json` | 新归档所有证据文件的外部SHA-256清单，包括manifest，不包括校验清单自身；Chroma数据库和缓存不纳入 |

回调只观察，不修改模型参数、提示词、答案或指标。生成模型、Ragas Judge及Trace Judge依旧是同一当前模型；配置方式有自评偏差风险，不构成严格公平的模型排名。提示词版本以源码哈希与实际消息为依据，不填虚构的人工版本号。

新归档使用schema 2，只能执行新建的prepared目录；旧schema或已经开始的run会被拒绝。准备阶段保存Git状态与实际源码哈希，执行前核对外部清单，运行结束后重新生成最终清单。manifest不包含自己的摘要。生成提示词哈希采用包含占位表达式的f-string AST；Trace采用模板原文UTF-8；Ragas采用实际Prompt定义的规范JSON，各自的hashKind均明确记录。

实际返回模型名来自逐次日志，角色配置里的返回名只表示整个运行的汇总，不推测每个裁判调用的权重版本。Embedding记录配置标识、实现、依赖及只读本地模式；现有流水线不暴露实际权重路径或不可变revision时，仍标未知。这些未知信息是复现边界，不是已完成的版本证明。

`completed` 只表示当前API返回预期数量的回答，不保证全部指标存在、Trace完整或门禁PASS。保留失败、缺失值和原始门禁，不为通过而调整结果。

旧预跑的原始manifest保留当时实际字段，不补写新版本信息；公开证据副本与新归档规范分别说明。对外材料保留可核对的输入、响应、日志、报告与校验入口，不包含凭据或机器私有路径。

观察到的token及调用数不一定包含HTTP内部重试，也不是扣费账单；`actualCharge` 保持为空，除非作者实际取得账单。模型温度0不保证服务响应逐字可复现。文件哈希说明材料对应，不是独立认证。

## 公开检出校验边界

原始运行目录保留完整产物；公开仓库只包含实际被 Git 跟踪的脱敏副本。下列 17 个历史产物在本机存在、列在旧 `SHA256SUMS.txt` 中，但被忽略规则排除，公开克隆不会取得它们。旧清单与历史证据保持原样，不据此宣称公开副本包含全部原始产物。

| 公开归档目录（均位于 `docs/competition/evidence/`） | 公开检出缺项 |
|---|---|
| `20261005T101847Z-pilot-c72cb679` | 6 项 |
| `20261005T131353Z-pilot-35f623cb` | 6 项 |
| `20261005T144504Z-full-39511424` | 5 项 |

前两个预跑各缺少相对于其归档目录的以下文件：

```text
project/eval_history.json
project/multi_model_summary.csv
project/ragas_report_deepseek-chat.csv
project/ragas_report.html
project/result_chart.png
server.log
```

正式运行公开副本缺少：

```text
report/multi_model_summary.csv
report/ragas_report_deepseek-chat.csv
report/ragas_report.html
report/result_chart.png
server.log
```

完整原始归档使用 `sha256sum --check SHA256SUMS.txt`，缺文件或哈希不一致都应报告。上述公开副本可以使用 `--ignore-missing` 仅核对检出中实际存在的文件；通过只说明这些现有文件与清单一致，不代表完整归档验证通过。本机包含被忽略文件的工作区与公开克隆须分别说明。

部分历史校验清单采用 CRLF。GNU 校验工具不能直接正确读取时，可在各自目录下临时去掉清单中的回车后校验，不改写清单或被校验文件：

```bash
tr -d '\r' < SHA256SUMS.txt | sha256sum --check --ignore-missing
```

`judge-sensitivity/run-20261012/` 与 `analysis-20261012/` 的清单属于这种情况；这两个公开目录没有上表中的缺项，验证完整性时应去掉 `--ignore-missing`。外部基准、鲁棒性和裁判校准三个新归档须对公开文件执行完整校验，不以忽略缺项掩盖失败。

Git 的 `autocrlf`／`eol` 设置验证的是检出字节是否一致。Windows 上模拟 Linux 换行设置不能替代真实 Linux 测试运行，后者以 Linux 环境或 GitHub Actions 的实际结果为准。

## 离线回归

```powershell
.\venv\Scripts\python.exe -m pytest -q tests
cd frontend
npm test
npm run typecheck
npm run build
npm run test:browser
```

归档工具有独立离线测试，拒绝哈希不匹配、伪造引用、错配预跑题以及不完整作者确认。真实评测不作为自动测试运行，不调用第三方模型。
