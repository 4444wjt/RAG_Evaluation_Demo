# 更新日志

本文件记录本项目的重要变更，遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，版本号遵循语义化版本（SemVer）。

> 说明：仓库早期提交未打 tag，以下为按功能演进整理的版本归档；`v1.2.0` 为当前版本（`main.py` 中 `VERSION` 常量）。

## [1.2.0] - 未发布

> 定位演进说明：项目由早期的「RAG 大模型幻觉自动化评测工具」演进为「鉴真全域 VeriScope AI · AI 测试交付与质量门禁平台」，为同一项目的持续演进（非新建项目）。

### 工程与仓库维护（2026-10-01）
- 后端迁移基线完整入库，保留 main.py 兼容层；本轮未修改业务代码、测试断言或依赖版本。
- 修正根目录模型缓存忽略规则，补齐 Docker 本地资料排除，历史诊断脚本原样复制归档。
- 经确认取消跟踪16项旧备份/运行产物，恢复保留两个旧源码/配置；现存本地文件未删除。
- 原工作目录与干净 Git 检出的离线测试均为 176 passed、1 xfailed；项目源码编译通过。

### 新增
- 第三个评测模型 **GLM-5.2-Fast**（阿里云百炼），三模型横向对比（DeepSeek / Qwen2.5-7B / GLM-5.2-Fast）。
- 基准数据集接入 **TruthfulQA**（`truthfulqa/truthful_qa`），新增 `--benchmark`、`--limit` 命令行参数。
- 多格式文档加载：`.txt` / `.md` / `.pdf` / `.docx`，按扩展名分流（TextLoader / PyPDFLoader / Docx2txtLoader）。
- 早期单元测试套件（当时为 `86 passed, 1 xfailed`），覆盖格式转换、JSON 容错、测试集加载、历史记录、三模型演示、门禁边界、score 计算。
- 深蓝科技色系 UI 主题、青→蓝标题渐变、隐藏 Gradio 右上角设置按钮。

### 修复
- `answer_relevancy` 设置 `strictness=1`，规避 DeepSeek / 百炼仅支持 `n=1` 时的 `BadRequestError`。
- TruthfulQA 数据集加载使用新命名空间 `truthfulqa/truthful_qa`，避免 `HfUriError`。
- 中文字体自适应降级、代理环境变量清理、IPv4 强制、环境变量编码修复。

## [1.1.0]

### 新增
- 多模型横向对比，输出对比表、柱状图、趋势图。
- 分层质量门禁：忠实度、上下文精度双阈值判定，未通过时 `sys.exit(1)` 阻断发布；正向用例均值判定，负样本仅对抗测试。
- 报告导出：CSV、HTML、xlsx、PDF 四种格式。
- 可视化图表：多模型柱状图、历史质量趋势折线图（Y 轴动态留白）。
- 三态兜底：空状态、加载状态（`gr.Progress` 分步进度）、错误状态友好提示卡片。
- 历史持久化加固：`eval_history.json` 缺失 / 为空 / 损坏 / 元素类型异常自动修复或跳过。
- 每模型 `elapsed_seconds`、整体 `total_elapsed_seconds` 耗时统计。

## [1.0.0]

### 新增
- 核心评测闭环：文档加载 → 向量库 → 测试集生成 → RAG 问答 → Ragas 指标评测 → 质量门禁 → 报告。
- 向量库：Chroma + `BAAI/bge-small-zh-v1.5`，支持复用。
- 测试集三种来源：自动生成（LLM 中文问答对 + 内置负样本）、自定义导入（json / csv）、基准数据集。
- RAG 问答：`TOP_K=4` 检索拼接上下文后调用 LLM。
- 6 项指标评测：faithfulness、context_precision、answer_relevancy、answer_correctness、context_recall + 幻觉率（派生）。
- Gradio Web 界面（命令行 + Web 双入口）。
- Docker 容器化部署。
- MIT 开源协议。
