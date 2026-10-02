# 首页 Dashboard 视觉重做 + 固定表格首列（提示词 · 交 Astra 代做）

> 只改 `D:\RAG_Evaluation_Demo\main.py` 的展示层，禁止触碰评测计算逻辑。

## 现状（已精确到行号，无需通读全文件）

单文件 `main.py`，Gradio 界面入口 `launch_gradio`（约 L1893 起）。

- 结果展示区约 L2514–2545，含：
  - `gate_card = gr.HTML(elem_id="evaluation-status")`
  - `table_out = gr.Dataframe(label="多模型评测对比", interactive=False, value=empty_display_df(), elem_classes=["cmp-title"])`
- 门禁卡片生成函数 `gate_card_html(result, faith_threshold, ctx_prec_threshold)`（约 L1589）：当前输出 badge + 8 个横向小指标（`.gate-item`），视觉不突出。
- 溯源字段中文映射 `TRACE_DISPLAY_COLUMNS`（约 L736）：
  - `hallucination_statement_count`＝幻觉语句数
  - `trace_fact_error_count`＝事实错误数
  - `trace_fabrication_count`＝无依据编造数
  - `trace_statement_count`＝溯源语句总数
  - `hallucination_ratio`＝语句幻觉占比
  - `pos_trace_hallucination_ratio`＝正向语句幻觉占比
- `result` 结构：`result["summary"]` 是 `list[dict]`，每项含 `faithfulness` / `refusal_accuracy` 及上述溯源字段；`result["gate_passed"]` 是 `bool`。
- `build_display_df(summary_df)`（约 L1558）已生成含溯源列的对比表，**不要改它的列与数值**。

## 任务 1：KPI 卡片 Dashboard

新增一块 5 张醒目的统计卡片，放在结果展示区最顶部（`section-title` 之后、`gate_card` 之前）：

1. 门禁结果（通过＝绿 / 拦截＝红）
2. 幻觉语句数（`hallucination_statement_count`）
3. 事实错误数（`trace_fact_error_count`）
4. 无依据编造数（`trace_fabrication_count`）
5. 拒答准确率（`refusal_accuracy`）

要求：

- 取 `result["summary"]` 中第一个 `faithfulness is not None` 的项作为「主模型」，卡片值取自它；无有效结果时各卡片显示「−」。
- 用 `gr.HTML` 输出，新建函数（如 `kpi_cards_html(result)`）；在 `launch_gradio` 里新增一个 `gr.HTML` 组件接收，并**同时接入 `_on_run` 与 `_load_demo` 两处 click 的 `outputs` 列表**（漏一处点击后卡片就不刷新，这是最容易踩的坑）。
- 大字号数字；占比/率显示百分数（`refusal_accuracy`），计数显示整数。
- 幻觉语句数 = 事实错误数 + 编造数的汇总，卡片下加一行小字注明口径（如「事实错误 x + 编造 y」）。
- 保持现有深色主题（石墨底 + 青绿/紫环境光），不要引入突兀亮色；卡片用 CSS grid 一行（桌面）自适应换行（窄屏）。

## 任务 2：固定对比表首列

`table_out` 列多会横向滚动，首列「模型名称」滚动后移出视野。用 CSS 把表头第一列和表体第一列 sticky 固定在最左：

- 针对 Gradio `6.28` 的 Dataframe 实际 DOM（`.gradio-dataframe` 内部 table 的 `thead th:first-child` 与 `tbody td:first-child`），加 `position:sticky; left:0; z-index` 高于普通单元格。
- **必须给固定列设不透明背景色**（与主题单元格底色一致），否则滚动时底层列文字会透过露出。
- 在 `launch_gradio` 的 `CSS` 字符串里追加即可，不另改其它 CSS。
- 若 Gradio 6.28 提供更可靠的官方方式，优先用官方；否则用 CSS。

## 硬约束

- 只改展示层（CSS、HTML 生成函数、`gr.HTML` 组件接线）。
- **禁止改动** `run_pipeline`、`trace_hallucinations`、指标计算、`score`、门禁判定逻辑、`build_display_df` 的输出列与数值。
- 保持现有深色主题与中文字体。

## 验收

1. `python -m py_compile main.py` 通过（建议用 `D:\RAG_Evaluation_Demo\venv\Scripts\python.exe`）。
2. 离线 `pytest tests` 仍为 151 passed / 1 xfailed 量级，不新增失败。
3. `python main.py --web` 打开 `http://localhost:7860`，演示场景选「多模型对比」→「加载演示数据」：顶部出现 5 张 KPI 卡片，对比表横向滚动时首列固定。