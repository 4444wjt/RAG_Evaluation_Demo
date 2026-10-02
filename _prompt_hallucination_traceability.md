# 幻觉溯源与归因：实现需求 + 代做提示词

> 用途：把第 4 节「精准提示词」直接复制给另一个 AI（建议 GPT-6 Sol + high）代做。
> 一句话目标：让工具从「仅输出幻觉率」升级为「定位每条幻觉来自哪句陈述、属于哪类错误、对应哪个上下文块」。

## 1. 现状（精简）

单文件 Python 项目 `main.py`（约 109KB），RAG 幻觉评测工具。

技术栈：langchain 0.2.17 / ragas 0.1.21 / chromadb / sentence-transformers(bge-small-zh-v1.5) / LLM=DeepSeek + Qwen + GLM。

评测流程 `run_pipeline`（main.py L1082）：
1. 文档切分 → chunks
2. 向量库 → retriever（Top-K）
3. 生成测试集：question / ground_truth / case_type
4. 每模型：`rag_answer()` 得 (answer, contexts) → 构建 eval_data → Ragas `evaluate()` → df
5. 分层统计 → summary

关键数据结构（已存在，实现时直接复用）：
- `rag_answer(question, retriever, llm)`（L658）返回 `(answer, contexts)`；`contexts = [d.page_content for d in docs]`，即 Top-K 块文本 `list[str]`
- `eval_data = {"question":[], "answer":[], "contexts":[], "ground_truth":[]}`（L1164）
- `RAGAS_METRICS = {faithfulness, context_precision, answer_relevancy, answer_correctness, context_recall}`
- `df = result.to_pandas()`（L1183），列含 question/answer/contexts/ground_truth + 各指标分数

现有指标：faithfulness / context_precision / answer_relevancy / answer_correctness / context_recall + 派生 hallucination_rate + refusal_accuracy。

缺口：Ragas 的 faithfulness 只输出聚合分数，不标明「哪句话是幻觉、为什么」。

## 2. 目标功能规格

输入（复用，不重新检索）：
- `question`: str
- `answer`: str
- `contexts`: list[str]
- `ground_truth`: str

输出（每题）：
```json
{"statements": [{"text": "...", "verdict": "supported|fact_error|fabrication", "evidence": "命中 context 原文或空"}]}
```

判定三态：
- `supported`：陈述可被 contexts 支撑
- `fact_error`：与 contexts 明确矛盾（事实/数字错误）
- `fabrication`：contexts 完全无依据（无中生有）

汇总：
- df 新增列 `hallucination_trace`（上述 JSON 字符串）
- summary 新增统计：fabrication/fact_error 语句总数、占比

## 3. 硬约束
1. 不修改现有 6 个指标的计算与含义；不动测试集生成、门禁逻辑
2. 复用 `get_llm()`，不新增模型、不重复检索（省 token、不重复计费）
3. 溯源判定用「单次 LLM 调用批量输出 JSON」，不逐句单独调用
4. JSON 解析失败降级为空 statements，不抛异常中断评测
5. 改完 `python -m py_compile main.py` 通过；现有 pytest 尽量不破

## 4. 精准实现提示词（复制给外部 AI）

```text
你是资深 Python / LLM 应用工程师。请在单文件项目 main.py 中实现「幻觉溯源与归因」功能。

【代码现状（已确认）】
- get_llm(model_cfg) 返回 langchain ChatOpenAI 兼容实例，调用方式 llm.invoke(prompt).content
- rag_answer(question, retriever, llm)：先 retriever.invoke(question) 得 docs，contexts = [d.page_content for d in docs]，再 llm.invoke 得 answer，返回 (answer, contexts)
- run_pipeline 内循环每模型构建 eval_data = {"question":[], "answer":[], "contexts":[], "ground_truth":[]}，随后 dataset = Dataset.from_dict(eval_data); result = evaluate(dataset, RAGAS_METRICS, llm, embeddings, run_config); df = result.to_pandas()
- 已有 safe_json_loads(text) 用于容错解析

【要新增】
写函数 trace_hallucinations(question, answer, contexts, ground_truth, llm) -> dict：
- 单次 llm.invoke，把 answer 与 contexts 一起喂给 LLM，中文 prompt 要求：
  把 answer 拆成原子陈述，逐条判定 verdict ∈ {supported, fact_error, fabrication}，
  并给出 evidence（命中 context 的原文切片，无则空字符串）。
  只返回 JSON，禁止 Markdown 代码块、禁止任何解释文字。
- 返回 {"statements": [{"text":str, "verdict":str, "evidence":str}]}
- 用 safe_json_loads 解析，失败返回 {"statements": []}

【集成点】
在 result = evaluate(...) 与 df = result.to_pandas() 之后：
1. 对该模型 eval_data 每一行调用 trace_hallucinations，返回 dict 序列化为 JSON 字符串
2. df["hallucination_trace"] = 该列（后续 df.to_csv 会自动写入，无需改）
3. 统计该模型所有 fabrication/fact_error 语句总数与占比，追加到该模型 summary 字典（新增键如 hallucination_statement_count / hallucination_ratio），其余 summary 结构保持不变

【硬约束】
- 不改现有 6 个指标含义，不改测试集生成与门禁逻辑
- 不新增模型、不重复检索；temperature 沿用现有（0）
- llm prompt 必须强制「只输出 JSON，禁止 Markdown 代码块/解释」，避免解析失败
- 改完后 python -m py_compile main.py 通过；不破坏 tests/ 目录现有用例

【交付】
1. 列出新增/修改的函数名与行号
2. 一句话说明运行验证方式
3. 说明 token 增量来源（每道题仅 1 次额外调用）
```