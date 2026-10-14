# 外部 RAG 应用端到端接入

此入口让应用自己检索并生成回答，VeriScope 核对应用实际返回的资料。与鲁棒性体检的 `--generator-endpoint` 分开：后者由平台提供资料，只检查生成侧对受控资料的反应。

实现入口：[客户端与校验](../../backend/adapters/rag_application.py)、[独立运行工具](../../tools/competition/evaluate_external.py)。没有新增服务端转发接口；通过本机命令行显式指定应用地址，公开只读站不会调用该地址。

## 请求与响应契约

平台串行发送 `POST {endpoint}`，`Content-Type: application/json`：

```json
{"schemaVersion":"veriscope.e2e.v1","id":"Q01","question":"存款保险的最高偿付限额是多少？"}
```

不发送参考答案、平台检索资料、题目分类或预期标签。应用自行检索，返回：

```json
{
  "schemaVersion": "veriscope.e2e.v1",
  "id": "Q01",
  "status": "answered",
  "answer": "最高偿付限额为人民币50万元。",
  "contexts": [{
    "text": "存款保险实行限额偿付，最高偿付限额为人民币50万元。",
    "sourceId": "deposit_insurance_regulation",
    "sourceTitle": "存款保险条例",
    "location": "第五条",
    "rank": 1,
    "score": 0.83
  }],
  "error": null,
  "meta": {"model": "qwen2.5-7b", "latencyMs": 1840}
}
```

- `schemaVersion` 与 `id` 必须准确回传，不一致记该题失败，不尝试对齐到其他题。
- `status` 仅接受 `answered`、`refused`、`error`。
- `answered/refused` 必须提供非空原文回答和 `contexts` 数组；`error` 必须提供 `{"code":"应用自定代码","message":"错误说明"}`。
- `contexts` 必须是**实际交给生成模型、重排与截断之后**的片段，并保持传入顺序。来源与位置可选，检索 `rank/score` 不参与指标计算。
- 最多 20 段，每段 8,000 字；回答最多 32,000 字，HTTP 响应最多 2 MiB。超限记为失败，不截断后继续评测。
- 超时 180 秒，每题一次应用请求，不自动重试、不跟随重定向。失败保留记录后继续下一题。
- 空 `contexts` 不改写成平台资料：标注“应用未提供检索资料”，仍保留实际裁判结果或异常；不强制制造“无依据”判定。
- 应用声明的 `refused` 与现有 `_is_refusal` 分别保留，识别不一致只提示，不改判，也不改变门禁规则。
- 应用 `meta` 和资料来源字段作为自报信息存档，不代表平台已验证其内部链路。凭据字段或已知密钥会脱敏，标注后的副本不称为未修改的原始字节。
- 完整读取的响应记录原始字节 SHA-256；脱敏记录带 `redactionApplied`。原响应哈希与脱敏文本不作同字节比较，归档副本另由文件校验清单校验。无效 UTF-8、重复 JSON 字段直接记录失败，不替换字符后继续计算。
- 不要求相同题号重复运行必然相同。每次使用独立归档；同一批次不重复调用，不自动整批重跑。

需要鉴权时用 `--token-env` 指定环境变量名，客户端只在请求头发送 `Authorization: Bearer ...`。不要把令牌填入参数值、URL、输入文件或应用返回的 `meta`。

**数据流向：** 应用返回的回答与检索资料会进入所选裁判的提示。使用远程裁判时，这些材料会发送到其供应商；私有资料请先确认使用授权与部署位置。本入口不会修改代理、VPN 或路由设置。

## 准备材料：不调用模型

完整五项评测需要本地 JSON 题集，每题有唯一 `id`、`question`、非空 `ground_truth`；`case_type` 为 `positive/negative`，省略时沿用原有 `positive` 默认值。参考答案只在平台内用于评测，不传给应用。

```json
[
  {"id":"Q01","question":"存款保险的最高偿付限额是多少？","ground_truth":"人民币50万元。","case_type":"positive"}
]
```

先在“模型配置”登记裁判，密钥仍从该配置指定的环境变量读取。以下地址仅为本机应用示例：

```powershell
venv/Scripts/python.exe tools/competition/evaluate_external.py prepare --input scenarios/my-rag-cases.json --dir work/external-rag/run-001 --endpoint http://127.0.0.1:9000/rag --judge-model deepseek-chat --application-name 我的知识库
```

准备阶段锁定输入哈希、裁判配置、源码副本及哈希、依赖版本、Git 状态、五项指标、`zh-sentence-v1`、Embedding、Trace、阈值与重试参数。不会请求应用、构建模型或下载 Embedding。

## 显式运行与离线分析

**运行会调用应用及裁判，可能产生费用。** 先确认材料、调用配置、供应商单价和预算；没有供应商费用自动封顶能力。应用每题一次请求，裁判执行五项指标和 Trace，一题会有多次模型调用，仍沿用现有裁判与 Ragas 的重试规则。

```powershell
venv/Scripts/python.exe tools/competition/evaluate_external.py run --dir work/external-rag/run-001 --allow-model-calls
```

运行前再次核对输入、源码与配置，不匹配则停止；运行中按题落盘。已尝试或中断的批次不会自动重新开始。Embedding 仅从已有本地缓存读取，不下载模型。

重新分析不调用应用或模型：

```powershell
venv/Scripts/python.exe tools/competition/evaluate_external.py analyze --dir work/external-rag/run-001
```

## 结果与解释边界

- 独立目录保存 `manifest.json`、原题集、源码副本、`records.jsonl`、`result.json`、中文 Markdown/HTML 报告和 `SHA256SUMS.txt`。manifest 由外部清单校验，不自引用。
- 每题保留应用请求、响应、HTTP/协议/应用错误、实际 contexts、来源元数据、五项指标、Trace 与裁判调用用量。实际响应模型未返回时记录未知，不以请求名代填。
- 调用失败不计入指标均值，不填零；报告同时列总数、有效数量、缺失指标与未尝试题。
- 复用原有聚合与 Quality Gate。没有有效指标时显示“门禁未计算”；部分题失败时已有有效结果的门禁与运行完整性分开说明。
- 检索方标注为“被测应用”，不与平台自己检索的运行混合成排名。原始冻结 Demo、历史运行、阈值和人审结果均不回填。
- 2026-10-14 经授权用本仓库自写的示例应用（[`examples/external_rag_app/app.py`](../../examples/external_rag_app/app.py)）完整实测一次：25 题全部评测完成，门禁 FAIL。见 [示例应用实测](../competition/external-rag/20261014-example-app-qwen72b/README.md)。尚未接入第三方真实系统。
- 实测发现：应用对 5 道无答案题都自报 `refused`，但平台按固定短语识别拒答，没有认出应用的拒答句，拒答准确率记为 0。按上文约定两者分别保留、不改判；识别规则的改动需单独提出。

配套测试：[端到端协议与归档测试](../../tests/test_external_rag.py)、[独立裁判测试](../../tests/test_independent_judge.py)。
