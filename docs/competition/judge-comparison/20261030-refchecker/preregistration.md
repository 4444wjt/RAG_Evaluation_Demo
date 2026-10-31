# 预登记：VeriScope 核查器与 RAGChecker 核查器同条件对照

登记时间：2026-10-30（北京时间晚间，任何模型调用之前）。登记人：主 Agent（Claude）。
授权：用户允许付费运行，总上限 ¥10；本实验硬停线 ¥8。

## 1. 要回答的问题

在**同一批陈述、同一检索原文、同一模型**下，VeriScope 门禁所用的陈述核查步骤，与 RAGChecker 所用的核查器相比，谁的判定与非作者人工标注更一致？

只比较「给定陈述 → 是否被原文支持」这一步。**不比较陈述抽取**：两臂都直接使用同一批固定陈述。

## 2. 固定输入（以 `05099e7` 的 git blob 为准）

| 用途 | 路径 | blob |
|---|---|---|
| 陈述、题面、检索原文 | `frontend/src/fixtures/judge-annotation.json` | `eea6f758b96b0099fa32232a1697fdadc401b440` |
| 人工标签 A | `docs/competition/judge-calibration/20261021-annotation-AB/annotation-A.json` | `39b17df9ab8fbb8265e23d3ba5da49b926fcdca6` |
| 人工标签 B | `docs/competition/judge-calibration/20261021-annotation-AB/annotation-B.json` | `860c6051f9cc0b86621f5c27cd12c8f52c5ea0dc` |
| 原分析（只用于核对确定集） | `docs/competition/judge-calibration/20261021-annotation-AB/analysis.json` | `55435ed260f413934bd27903ae968af514d2a0c0` |
| A 臂实现 | `backend/adapters/ragas_compat.py`（Ragas 0.1.21） | `e476246487e0be72ae7f5284d61a6c5efbfc112c` |

标注包共 52 条陈述、20 道题，每题 4 段检索原文。

## 3. 两臂

| | A 臂：VeriScope | B 臂：RAGChecker 核查器 |
|---|---|---|
| 实现 | `ChineseFaithfulness`（继承 Ragas 0.1.21 `Faithfulness`）的 NLI 提示词、解析与 0/1 判定，冻结不改；跳过陈述抽取，直接输入固定陈述 | RefChecker 0.2.17 `LLMChecker`（RAGChecker 0.1.9 所用）的提示词与解析，冻结不改；只把底层发送函数换成带预算台账的客户端 |
| 每次请求的单位 | 每题一次请求，包含该题全部陈述，与原实现的分组方式一致 | 按 RefChecker 默认的联合核查分组 |
| 检索原文 | 该题 4 段 contexts，按原实现的方式拼接 | 同样的 4 段 contexts，作为一份 reference |
| 判定映射 | verdict=1 → 支持；0 → 不支持 | Entailment → 支持；Neutral／Contradiction → 不支持 |

两臂共同设置：
- 模型 `deepseek-flash`，**非思考模式**，两臂用同一种关闭方式；
- temperature 0，max_tokens 2048；
- 不做 HTTP 重试，不做解析修复；解析失败记为「无结果」，不归入任何一类。

每臂运行 3 次，顺序交错：A1、B1、A2、B2、A3、B3。

## 4. 人工参照

- 人工标签映射：「支持」→ 支持；「矛盾」「无依据」→ 不支持；「无法判断」→ 不参与。
- **确定集**：A、B 两人在「支持／不支持」上一致，且都没有选「无法判断」的陈述。预期 46 条（人工支持 40 条、不支持 6 条）；必须与原分析一致，且未决集合恰为 S12、S14、S16、S18、S26、S35，否则停下。
- 模型永远看不到人工标签、标注人信息、10-05 原裁判判定和原始题号。

## 5. 指标（只报条数）

主指标：确定集上，每次运行与人工一致的条数（满分 46）。

次指标：
- 人工支持类命中 x/40、不支持类命中 x/6；
- 同一臂三次运行之间判定翻转的条数；
- 6 条未决陈述的逐条原始判定；
- 解析失败条数；
- 实际返回的模型名与 usage。

## 6. 判定规则（事先写定，结果出来后不改）

只有同时满足以下两条，才说「X 臂有可证优势」：
1. X 臂三次运行的一致条数均值大于 Y 臂；
2. X 臂三次中最少的一次，多于 Y 臂三次中最少的一次。

其他情况一律写「无可证优势」。

不支持类只有 6 条，在这一类上的任何差异都只能描述，不能作为优势依据。

## 7. 局限（提前声明）

- 只比较核查步骤；RAGChecker 的抽取器和其余诊断指标都未参与。
- RefChecker 的提示词为英文，输入为中文。
- 单材料（《存款保险条例》节选）、单模型、单一时段；模型版本为 DeepSeek-V4.1-Flash-1017，与 10-05 原运行不同，所以 A 臂必须重跑，不沿用旧判定。
- 人工标注人的独立性由本人勾选，未经第三方核实（见原 README）。
- 本实验不满足 K 级三件套：没有未参与开发者的复现。证据等级最多为 E。

## 8. 停止条件

任一情况发生即停，报告证据，不擅自豁免：
- 累计预留费用加实际费用达到 ¥8；
- 任一臂接口错误率超过 10%；
- 响应中出现思考内容（说明思考模式未关闭）；
- 确定集核对不一致；
- 出现密钥回显；
- 需要写入下文允许范围以外的路径。

## 9. 写入范围

只写本目录 `work/competition/ragchecker-checker-compare-20261030/`（含 `venv-ragchecker/`）。不改仓库其他文件，不改 `docs/competition/evidence/**`，不 git add、不 commit、不 push。
