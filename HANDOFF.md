# HANDOFF.md — RAG 评测项目交接文档

> 2026-10-01 工程整理说明：下文为早期交接记录，旧行号、依赖说明和待办不代表当前冻结状态。后端现已拆分为六层；当前架构、测试方法和冻结状态以 README 与 PHASE_3_B_10_FREEZE_REPORT.md 为准。

> 本文件用于交接给 ChatGPT / 其他 AI 继续协作。所有行号、版本号、函数名均取自当前 `main.py`（共约 1660 行）、`requirements.txt`、`Dockerfile`、`README.md` 的实际内容。

## 一、项目概况

- **名称**：RAG 大模型幻觉自动化评测工具，版本 `v1.2.0`（见 `main.py` L107 `VERSION`）。
- **用途**：把「文档加载 → 向量化 → 自动生成测试集 → RAG 问答 → Ragas 指标评测 → 多模型横向对比 → 分层质量门禁 → HTML 报告」串成一条可复现流水线，核心量化 RAG 系统的**忠实度（faithfulness，幻觉检测）**与**检索精度（context_precision）**。
- **位置**：`D:\RAG_Evaluation_Demo\`。
- **目录**：`main.py`（主程序，命令行 + Gradio 双入口）、`README.md`、`requirements.txt`、`Dockerfile`、`demo.txt`（示例知识库）、`testset_example.json` / `testset_example.csv`（测试集示例）、`.env.example`（密钥模板）、`LICENSE`；另有历史/产物文件 `eval_history.json`、`result_chart.png`、`trend_chart.png`、`ragas_report.html` 等。
- **交付物**：可直接运行的评测脚本 + Gradio Web 界面 + 自动生成的图表 / HTML 报告 / CSV 汇总。
- **比赛与截止时间**：项目文档中未标注，需你补充后再决定任务优先级。

## 二、技术栈（以 requirements.txt 及 freeze_full.txt 为准）

- 语言：Python 3.11（`Dockerfile` L2 `FROM python:3.11-slim`）。
- 编排/评测：`langchain==0.2.17`、`langchain-community==0.2.19`、`langchain-core==0.2.43`、`langchain-openai==0.1.25`、`langchain-text-splitters==0.2.4`、`ragas==0.1.21`、`datasets==5.0.1`。
- 向量库/Embedding：`chromadb==1.5.9`、`sentence-transformers==6.1.0`、`transformers==5.17.0`、`torch==2.14.0`、`huggingface_hub==1.32.0`。
- 界面/联网：`gradio==6.28.0`、`httpx==0.28.1`、`openai==1.109.1`。
- 数据/绘图：`pandas==3.0.6`、`numpy==1.26.4`、`matplotlib==3.11.2`、`openpyxl==3.1.5`。
- 其他锁定：`python-dotenv==1.2.3`、`tiktoken==0.14.0`、`tokenizers==0.23.2`、`pysbd==0.3.4`、`safetensors==0.8.0`、`scikit-learn==1.9.1`。
- 完整清单见 `requirements.txt`（已锁版本，防漂移）；`freeze_full.txt` 是 `pip freeze` 全量快照。

## 三、已完成功能（每条给证据）

1. **多格式文档加载** `load_and_split_doc`（`main.py` L207）：`.txt/.md` 走 `TextLoader`、`.pdf` 走 `PyPDFLoader`、`.docx` 走 `Docx2txtLoader`，用 `RecursiveCharacterTextSplitter` 分块（`CHUNK_SIZE=200` L110）。
2. **向量库构建** `build_vector_store`（L233）：`Chroma` + `HuggingFaceEmbeddings`，Embedding 名 `BAAI/bge-small-zh-v1.5`（L98）且强制离线（`local_files_only=True`），`REUSE_VECTOR_DB` 控制复用/重建。
3. **自动生成测试集** `generate_testset`（L251）：LLM 按文档块生成中文问答对，`safe_json_loads`（L149）多层压缩容错，并内置 3 道动态负样本（对抗测试）。
4. **自定义测试集导入** `load_custom_testset`（L320）：支持 `.json` / `.csv`。
5. **RAG 问答** `rag_answer`（L367）：`TOP_K=4`（L112）检索拼接上下文后调用 LLM。
6. **6 项指标评测**：`RAGAS_METRICS`（L659）= faithfulness / context_precision / answer_relevancy / answer_correctness / context_recall；`answer_relevancy.strictness = 1`（L661，规避 DeepSeek/百炼等仅支持 `n=1` 的接口报错）；幻觉率由 `pos_faithfulness` 派生。
7. **主流程** `run_pipeline`（L675）：6 步进度 + 每模型耗时统计（`elapsed_seconds`）+ `_safe_mean`（L665）空值过滤 + 门禁判定。
8. **多模型抽象**：`MODELS_CONFIG`（L125）现含 3 模型——DeepSeek（`api_key_env=OPENAI_API_KEY`）、硅基流动 Qwen（`SILICONFLOW_API_KEY`）、GLM-5.2-Fast（`DASHSCOPE_API_KEY`，base_url 为阿里云百炼 `dashscope.aliyuncs.com/compatible-mode/v1`）；`get_llm`（L174）按 `api_key_env`/`base_url` 通用初始化。
9. **门禁与对比表**：`gate_card_html`（L1034）分层门禁结论卡片；`build_display_df`（L1010）6 指标 + 耗时转百分数展示。
10. **可视化**：`draw_summary_chart`（L1203）多模型柱状图、`generate_trend_chart`（L574）历史质量趋势折线图（Y 轴动态留白）、`_resolve_cjk_font`（L504）中文字体自适应降级。
11. **报告导出**：`generate_html_report`（L386）、`export_report_xlsx`（L1069）、`export_report_pdf`（L1079，经 `matplotlib.backends.backend_pdf`，零新依赖）。
12. **Gradio Web** `launch_gradio`（L1319）：模型选择卡片、阈值滑块（`FAITH_THRESHOLD`/`CTX_PREC_THRESHOLD`=0.8，L115-116）、内置演示数据 `build_demo_result`（L1153）、友好报错 `friendly_error_message`（L1284）。
13. **历史记录**：`load_history`/`save_eval_record`/`history_to_dataframe`（L518/534/545），`eval_history.json` 持久化。

## 四、未完成 / 待办（按优先级）

- **【高】** GLM 第三模型**真实 API** 端到端验证：配置与演示数据已就绪，但尚未跑通百炼真实调用（约束：报错即停，不反复试）。可复用 `_verify_bailian.py`。
- **【高】** `.env.example` 缺 `DASHSCOPE_API_KEY`（GLM 需要）：缺这行时第三模型真实评测会抛「API 密钥未配置」。
- **【高】** PDF/DOCX **真实上传评测未验证**：`freeze_full.txt` 中未出现 `pypdf`/`docx2txt`，`load_and_split_doc` 的 PDF/DOCX 分支可能缺依赖而失败。
- **【中】** `requirements.txt` 未显式锁定 `pypdf`/`docx2txt`（多格式解析依赖），需决定是否补。
- **【中】** 回归对比/退化告警、RAG 参数对比实验台（规划中的 P1-4 / P1-5）。
- **【低】** 幻觉归因可视化、单元测试 + CI（规划中的 P2）。

## 五、当前卡点

- **端口**：`7860 / 7861 / 7862` 当前均有 python 进程监听（多实例并存）；代码与 `Dockerfile` 默认 `7860`（`EXPOSE 7860`，L26），历史会话曾用 `7862`。
- **上一会话遗留**：「重启 Web 服务（端口 7862）」后台任务失败，日志为 `httpx.ReadError [WinError 10054]`（模型 API 连接被远程关闭，疑似网络/接口抖动）。
- **Docker**：本机 `docker` CLI 不可用，容器化部署路径未验证。
- **环境变量**：`.env` 已存在并由 `python-dotenv` 加载（内容不外泄）；DeepSeek 用 `OPENAI_API_KEY`、Qwen 用 `SILICONFLOW_API_KEY`、GLM 用 `DASHSCOPE_API_KEY`。
- **Embedding 离线依赖**：启动需 `models/bge-small-zh-v1.5` 本地目录或 HF 缓存，否则在 Embedding 初始化阶段即失败。

## 六、关键约束（务必遵守）

- 不改核心评测逻辑：门禁判定、`score` 口径（`(pos_faithfulness + pos_context_precision) / 2`）保持不动。
- 不读、不输出 `.env`、API Key、Token。
- 不新增第三方依赖（多格式解析除外，需说明理由）。
- 只改 theme/CSS 时不动功能逻辑与组件结构；门禁卡片红（不通过）/绿（通过）语义色必须保留。
- 每阶段改完用 `python -m py_compile main.py` 做语法校验。
- 百炼模型 ID 或 API 异常时停下报告，不要反复重试。

## 七、如何继续

- **启动 Web**：`venv\Scripts\activate` 后 `python main.py --web`（默认 7860）；命令行完整评测：`python main.py`（默认 `demo.txt` + 三模型）或 `python main.py --doc 文档.txt --testset 测试集.json`。
- **验证**：`python -m py_compile main.py` 语法校验；演示数据（不调 API）：进 main 模块调 `build_demo_result("多模型对比") → draw_summary_chart() → build_display_df()`。
- **建议下一步**：① 补 `.env.example` 的 `DASHSCOPE_API_KEY` → ② 用 `_verify_bailian.py` 验证 GLM 可用性 → ③ 真实跑一次 `--doc demo.txt` 三模型评测 → ④ 之后推进 PDF/DOCX 端到端验证与回归告警功能。