# 第三方 RAG 端到端实测：AnythingLLM 源码构建实例（2026-10-21）

**结论：第一次在第三方真实 RAG 系统上跑完整 25 题。** 25/25 题评测完成，门禁 **FAIL**：正向忠实度 **0.765 < 0.8**，正向上下文精确率 **1.0**。门禁依据 20 个正样本，两项各 20/20 有效，没有缺失。25 题的上下文对应关系均为「已证明（所列前提下）」。

这次验证了接入与证据链，不把门禁 FAIL 全部归因于被测系统，也不把上下文对应关系的证明当作回答事实正确的证明。

## 被测对象与接入方式

被测对象为 AnythingLLM v1.18.0：**从官方 tag 源码构建、在本机运行的实例，不是桌面安装包或官方 Docker 镜像**。提交为 `3e4f9411b67f2dd7fa9fc6f2b95dfacbda0633ba`。只运行服务端，没有构建前端；工作区经开发者 API 创建。

- 工作区为 query 模式；topN=4、相似度阈值=0.25，均使用默认值；拒答句为源码默认句。
- 生成提供方为 DeepSeek，请求名为 `deepseek-flash`。**实际响应模型名未知**：该版本的 `metrics.model` 取请求名，不能作为供应商实际返回模型名的证明。
- 向量模型为硅基流动 `BAAI/bge-m3`，向量库为 lancedb；条例切为 2 段。工作区只有这一份文档，没有固定文档，记忆关闭。
- [shim](anythingllm/shim/shim.py) 把 `veriscope.e2e.v1` 请求转换为 AnythingLLM 开发者 API 调用，每题使用新会话，不带附件。shim SHA-256 为 `53057b9dc1081310337a0d73929ba253bd9519aa5fe954ab8efeab5b46e2b20c`。
- DeepSeek 适配器把推理内容拼成 `<think>…</think>` 放在回答前。shim 按运行前锁定的规则处理：以 `<think>` 开头且恰好只有一个 `</think>` 时才剥离，不 trim；原文保存在 `meta.rawAnswer`，并记录字节数及 SHA-256。本次 25 题全部为 `stripped`。

裁判为硅基流动 `Qwen/Qwen2.5-72B-Instruct`，与 10-14 自写示例应用的配置逐字段相同。

[清单对照](anythingllm/checks/manifest-compare-formal.json)显示评测口径字段与基线相同。`SOURCE_PATHS` 中只有 `backend/core/pipeline.py`（入库检查）与 `backend/core/utils.py`（显示名）不同，两处改动均不在打分路径上。

| 运行 | 正向忠实度 | 门禁 |
|---|---:|---|
| [10-14 自写示例应用](../20261014-example-app-qwen72b/README.md) | 0.6686 | FAIL |
| [10-14 平台内部、修复版](../../evidence/20261014T125415Z-full-a5002d4f/README.md) | 0.7992 | FAIL |
| 本次 AnythingLLM | 0.765 | FAIL |

同题集、同裁判，只作系统对照。检索、切分、生成请求名和推理块处理等多个变量同时不同，不能归因到单一变量或据此比较系统优劣。

## 发现

### 上下文对应关系

运行前后对照了三类证据：只读快照、受审 14 个服务端文件与 tag 的逐字节比对、locale 扫描；并逐题计算提示词字节上界。25 题全部满足检查前提，本地上下文压缩未触发，API 返回的 sources 对应送入生成请求的资料，文档信息头保留在内。

这是所列前提下对本地请求构造的证明，**不是供应商侧抓包**。逐题依据见[上下文检查](anythingllm/checks/context-bound-formal.json)。

### 拒答准确率为 0.0

每题都检索到资料，AnythingLLM 从未输出默认拒答句。5 道负样本均用中文说明节选中没有相关内容，但平台按固定短语未识别为拒答。这与 10-14 示例应用的发现相同；本次没有改识别规则。拒答准确率不参与门禁。

### 逐句核对的覆盖有限

10 题 Trace 为空：**D08、D09、D10、C02、C03、N01、N02、N04、N05、F02**。状态分布为 `ok=11、partial=4、call_error=10`。裁判调用共 492 次，逐题调用记录中有限流错误 `RateLimitError`。

不能仅凭空列表确定具体原因，也不能把空 Trace 解释为没有错误。逐句核对不参与门禁。

### 待复核观察

F04 的回答引用了条文「7个工作日内」，faithfulness 却为 0.0，原因未核实。保留原判定，不用人工推测替换分数。

## 不能说明什么

- 只运行一次，只用一个裁判，未验证重复运行或换裁判后的稳定性。
- 结论只适用于本次源码构建实例，不代表桌面安装包、官方 Docker 镜像或其他部署。
- 上下文对应证明不是供应商侧抓包，也不证明回答事实正确。
- 被测侧多个变量同时不同，不能把结果归因于单一变量。
- 裁判总用量字段为未知；未取得账单，不估算实际费用。

## 运行与用量

| 项目 | 值 |
|---|---|
| 时间 | 2026-10-21 04:02:18–04:53:55 UTC，约 52 分钟 |
| 应用请求 | 25 次 |
| 裁判调用 | 492 次 |
| 裁判总 Token 与实付费用 | 未知 |
| 运行次数 | 只运行一次，没有重跑或补跑 |

应用逐题自报的生成用量保留在响应的 `meta.upstreamMetrics` 中；它不等于裁判用量或整次运行的账单。

正式运行前做过 3 次一题冒烟，均不计入正式 25 题，原始结果见 `anythingllm/smoke/`：

1. 第一次请求 `deepseek-chat` 未成功，保留失败记录。
2. 第二次发现回答含推理块。
3. 第三次验证按锁定规则剥离生效。

## 文件

顶层为评测工具写出的运行目录完整副本，共 18 个文件，字节未改，由顶层 [SHA256SUMS.txt](SHA256SUMS.txt) 校验其列出的 17 个文件。**本 README 不在该清单内。**

| 文件 | 内容 |
|---|---|
| `manifest.json` | 运行清单、配置与依赖、输入及源码哈希、起止时间 |
| `testset.json` | 25 题题集及平台使用的参考答案 |
| `records.jsonl` | 逐题请求、响应、评分、逐句核对及裁判调用记录 |
| `result.json`、`report.md`、`report.html` | 原始汇总结果与报告 |
| `sources/` | 11 个运行时源码副本 |
| `SHA256SUMS.txt` | 工具生成的原始校验清单，不覆盖自身或本 README |

补充证据共 22 个文件，由 [anythingllm/SHA256SUMS.txt](anythingllm/SHA256SUMS.txt) 校验其列出的 21 个文件。**本 README 不在该清单内。**

| 路径 | 内容 |
|---|---|
| `anythingllm/shim/shim.py` | 运行时接入转换脚本的字节副本 |
| `anythingllm/snapshots/` | 前后快照、源码比对和部署 Git 状态，共 7 个文件 |
| `anythingllm/checks/` | 上下文检查、结果汇总及配置对照，共 5 个文件 |
| `anythingllm/smoke/` | 三次一题冒烟与第三次剥离复核，共 4 个文件；保留第一次失败 |
| `anythingllm/tools/` | 检查器、源码比对工具和固定版本参考清单，共 4 个文件；保留目录层级以供复核 |
| `anythingllm/SHA256SUMS.txt` | 补充证据清单，LF 换行，不覆盖自身或本 README |

复核顶层归档（仓库根目录执行，只校验，不运行评测）：

```bash
python -c "from pathlib import Path; from tools.competition.evaluate_external import verify; verify(Path('docs/competition/external-rag/20261021-anythingllm-qwen72b')); print('ok')"
```

复核逐题上下文，输出必须写到尚不存在的临时路径：

```bash
python docs/competition/external-rag/20261021-anythingllm-qwen72b/anythingllm/tools/round3/check_context_bound_v2.py --input docs/competition/external-rag/20261021-anythingllm-qwen72b/records.jsonl --before docs/competition/external-rag/20261021-anythingllm-qwen72b/anythingllm/snapshots/before-formal.json --after docs/competition/external-rag/20261021-anythingllm-qwen72b/anythingllm/snapshots/after-formal.json --source-before docs/competition/external-rag/20261021-anythingllm-qwen72b/anythingllm/snapshots/source-before-formal.json --source-after docs/competition/external-rag/20261021-anythingllm-qwen72b/anythingllm/snapshots/source-after-formal.json --output <临时目录>/context-bound.json
```
