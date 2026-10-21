# 单独配置评测裁判

在“评测配置 → 模型 → 评测裁判”选择裁判。被测模型继续生成回答，五项 Ragas 指标与 Trace 使用所选裁判；裁判不必被勾选为被测模型。本机登记的 OpenAI 兼容模型同样可选。

不选择时沿用原有同模型核对。指定无效裁判时返回参数错误或配置失败，不自动回退、换模型或额外重跑。题集自动生成仍沿用原有规则，不会因裁判选择而更换出题模型。

API 在原请求上增加可选 `judge_model_name`，结果仍为原有八个顶层字段：

```json
{
  "doc_path": "demo.txt",
  "testset_path": "testset_example.csv",
  "model_names": ["Pro/Qwen/Qwen2.5-7B-Instruct"],
  "judge_model_name": "deepseek-chat"
}
```

新运行的汇总、逐题结果、CSV、HTML 报告和前端详情保存或显示：

- `judge_model`：裁判请求模型；
- `judge_mode`：`configured`（单独指定）或 `self`（沿用被测模型）；
- `judge_response_models`：调用实际返回的模型名；没有观察到时为空数组，界面显示未知；
- `judge_response_scope`：平台检索评测为 `model_batch`，表示本模型整个批次观察到的响应名，不冒充每题所有指标的逐一对应；端到端工具按题核对，记录为 `item`；
- 汇总内 `judge_calls`：已观察到的裁判调用状态与用量，不包含密钥或原始错误文本。

当前 SDK 会在缺少响应模型名时回填请求名，并在合并调用结果时使用请求名。裁判适配器在原始响应转换时另存真实 `model` 字段；没有该字段时明确未知。该诊断层不改变请求体、提示词、温度、重试或返回的回答文本，离线测试使用实际 SDK 与模拟 HTTP 响应核对这一点。

模型构建复用裁判对照工具使用的适配器工厂；单独配置裁判和外部应用评分时新建相同定义的指标实例，避免前一裁判残留。未配置裁判的原有路径继续使用原指标实例。Answer Correctness 权重仍为 0.75/0.25，`zh-sentence-v1`、Trace 提示词、评分与门禁规则均未改动。

当前 `main.py.run_pipeline` 委托共享的 `backend.core.pipeline.evaluate`，CLI 与 Web/API 都会经过本次修改的评分层。CLI 新增可选 `--judge-model` 参数；未传时仍按原有同模型方式执行。历史 `_legacy_run_pipeline` 未改动，Gradio 未新增裁判选择控件。外部应用独立工具复用同一评分函数。

```powershell
venv/Scripts/python.exe main.py --doc demo.txt --testset testset_example.csv --judge-model deepseek-chat
```

“单独配置”不代表独立人工认证，也不能保证模型训练数据或错误模式相互独立。若所选裁判也在被测模型中，对该模型仍属于同模型核对。历史快照没有该元数据时不推断、不回填。

裁判会收到问题、回答、检索资料及指标所需参考答案。使用远程裁判时，私有材料会发送到其供应商，请先确认数据授权与部署位置；浏览器不接收任何模型密钥。

2026-10-14 经授权真实运行一次：生成 `deepseek-chat`，裁判 `Qwen/Qwen2.5-72B-Instruct`，25 题正式题集。修复后的版本在 DeepSeek 自评时门禁 PASS（0.8607），重新生成回答并换此裁判后正向忠实度 0.7992，门禁 FAIL。见 [独立裁判复核](../competition/evidence/20261014T125415Z-full-a5002d4f/README.md)。两次部分回答不同，不能把门禁结论变化全部归因于裁判；同回答子集也只能说明本次判定输出存在差异，不说明哪个裁判更准；不宣称评测准确率提高。
