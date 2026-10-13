# 更新日志

本文件记录本项目的重要变更，遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，版本号遵循语义化版本（SemVer）。

> 说明：仓库早期提交未打 tag，以下为按功能演进整理的版本归档；`v1.2.0` 为当前版本（`main.py` 中 `VERSION` 常量）。

## [1.2.0] - 未发布

> 定位演进说明：项目由早期的「RAG 大模型幻觉自动化评测工具」演进为「鉴真全域 VeriScope AI · 逐句核对与质量门禁平台」，为同一项目的持续演进（非新建项目）。2026-10-08 至 10-10 期间曾使用「AI 测试交付与质量门禁平台」的表述，2026-10-11 起统一为现名。

### 门禁审计与可信度（2026-10-11 至 2026-10-12）
- 门禁可信度四问：从复算、换裁判、证据敏感性、结论确定性四个方面审计门禁本身，见 `docs/competition/gate-credibility.md`。
- 结论确定性分析 `tools/competition/gate_uncertainty.py`：对 25 题正式运行按题重抽求门禁指标的区间，并比较计算口径；只读冻结证据，不改门禁规则与已记录的 FAIL。
- 独立裁判对照：原裁判重判作噪声基线，另换 Qwen2.5-72B 对照，量化同源裁判偏差；结果与归档见 `docs/judge-comparison.md`。
- 门禁回放 `scripts/gate_replay.py`：离线重新执行门禁裁决，不通过时以非零退出码结束。
- 人工核验存档 JSON Schema（`schemas/review-archive.v1.schema.json`）：人工意见与机器判定并列保存、互不覆盖，绑定冻结快照的 SHA-256。
- 总览首屏「正式运行回放」：展示从逐句判定到门禁结论的计分过程，数据由 `tools/competition/build_replay_fixture.py` 从冻结证据生成并校验。

### 界面与部署（2026-10-11）
- 公网演示只读：写入类请求由 nginx 与后端两层拦截（HTTP 423）。
- 视图深链、评测数据可视化重做、侧栏当前评测卡与整体视觉升级。

### 比赛证据与文档（2026-10-05 至 2026-10-09）
- 中文陈述兼容层 `zh-sentence-v1`：修复 Ragas 0.1.21 分句只保留英文句号结尾句子、中文句子被整句丢弃的问题；英文路径、指标公式与门禁不变。
- 运行证据工具 `tools/competition/run_evidence.py` 与 25 题正式运行证据归档（2026-10-05，单模型，保留真实 FAIL）。
- 逐句溯源展示、空 Trace 原因区分、负向题拒答语义纠正。
- 作品介绍按四要素重构并导出 PDF；新增历史重写说明。

### 修复（2026-10-12）
- 新增 `.gitattributes`，按记录逐文件固定检出换行符，修复 Linux 上完整性哈希校验失败；CI 的 Python 与前端两个任务均已通过。

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
