# 更新日志

本文件记录本项目的重要变更，遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，版本号遵循语义化版本（SemVer）。

> 说明：仓库早期提交未打 tag，以下为按功能演进整理的版本归档；`v1.2.0` 为当前版本（`main.py` 中 `VERSION` 常量）。

## [未发布]

### 落地文档与模型配置（2026-10-14）
- 新增 `docs/DEPLOYMENT.md`：三种落地形态（独立质检平台、嵌入现有系统、CI 门禁）及接入方式；接入自有模型的四步说明；私有化 / 内网部署的数据流向，按代码核实的事项与尚未实测的事项分开列出（未在完全断网的环境里跑过完整评测）。
- README、英文版与作品介绍新增「落地场景」表；作品介绍 PDF 重新导出。
- 工作台新增「模型配置」页（`#/lab/models`）：登记任何 OpenAI 兼容接口（vLLM、Ollama、内部网关）的模型，写入项目根目录 `models.local.json`（git 忽略）。页面只记录密钥所在的环境变量名，只显示「已设置 / 未设置」，不接收、不返回密钥。评测工作区合并显示登记的模型，默认不选中。
- 后端新增 `backend/core/model_registry.py` 与接口 `GET /models`、`PUT /models/local`。自有模型的密钥变量必须形如 `VERISCOPE_<名称>_API_KEY`，不能借用内置模型的密钥变量；接口地址不能带用户名、密码或查询参数；出现密钥字段或未知字段一律拒绝；登记只接受本机发起的请求，只读部署返回 423。内置模型的行为不变；登记的模型只在按名称点选时参评。

### 价值对照与外部案例（2026-10-14）
- 新增 `docs/competition/value-comparison.md`：同一次 25 题正式运行，对照只看 Ragas 总分（0.59，正确拒答的负向题被计 0 分）、看逐题表格与用本平台各能回答什么；数据全部来自冻结证据，不调用模型。
- 新增 `tools/competition/prompt_case.py`：同一模型、同一批固定资料，只换问答提示词，比较外部 RAG 项目默认提示词与本项目提示的作答行为；授权绑定材料包与代码哈希，按预算与请求数上限停止，不重试。
- 外部案例 `docs/competition/cases/langchain-chatchat/`：Langchain-Chatchat 默认知识库提示词在 5 道错误前提题中拒答 2 道（本项目提示拒答 3 道），F03、F04 资料有依据仍被两者拒答；经授权运行一次，100 次请求，约 ¥0.15。

### 部署与治理（2026-10-13，v1.2.0 之后）
- 新增 `deploy/` 一键部署：docker-compose（api + web 两个容器）、nginx 反向代理与只读两层拦截、`deploy.sh`；默认只读演示且只监听本机，可切换为可评测部署。已在 Linux 云服务器上全新克隆实测（构建、只读拦截、状态码均符合预期），并修复测试中发现的 nginx `default_type` 位置错误。
- 新增 CODE_OF_CONDUCT.md（参考 Contributor Covenant 2.1）。

### 门禁接入 CI（2026-10-13）
- `scripts/gate_replay.py` 新增 `--junit` 与 `--summary`：输出 JUnit XML 与 Markdown 结果表，CI 页面直接显示哪个门禁指标未达标、差多少；裁决与退出码不变。
- 新增根目录 `action.yml`：门禁回放打包为 GitHub 复合 Action，其他仓库一步接入；日常 CI 新增 `gate-action` 任务自检（演示快照放行、25 题正式运行拦截）。`quality-gate-example.yml` 改用该 Action，并发布 JUnit 检查结果。
- 回归对比 `backend/core/regression.py`：门禁回放与 Action 新增 `--baseline` / `baseline`，与基线逐题配对，按题重抽样求均值变化区间，并以同一裁判重判的实测波动为噪声底线；「确定退步」才拦截，「可能退步」只提示。以两次同题 5 题预跑为真实样例。
- 门禁代码自检：新增 Hypothesis 性质测试（阈值边界、分数单调、题目顺序、负向样本等 10 条性质）与 mutmut 变异测试（42 个变异抓住 39 个，存活 3 个均不影响裁决），CI 新增 `mutation.yml`；门禁代码未改。见门禁可信度文档附录。
- 新增 CITATION.cff（GitHub「Cite this repository」，可供 Zenodo 读取）、OpenSSF Scorecard 工作流 `scorecard.yml`（每周及推送时做供应链安全评分）与英文说明 README.en.md。
- 工作台新增「门禁审计」视图（`#/lab/audit`）：集中展示门禁可信度四问、门禁代码自检（10 条性质、42 个变异抓住 39 个）与回归对比样例；数据由 `tools/competition/build_audit_fixture.py` 从冻结证据生成并核对哈希，`gate.py` 改动后拒绝沿用旧的变异结果。变异测试原始报告归档于 `docs/competition/gate-self-check/`。首页未改动。
- GitHub Pages 备用演示站：新增 `pages.yml`，用同一份只读快照构建纯静态前端（`VITE_STATIC_SITE=1` 时不探测 API，顶栏标明「静态演示站 · 无后端」），服务器或隧道中断时仍可打开。
- 门禁失败归因 `backend/core/attribution.py`：拦截时逐题判断问题出在检索（未找全、排序差）还是生成（拒答、无依据），按类别拆分正向忠实度差距并给出修改建议，写入命令行输出与作业摘要；不影响裁决。25 题正式运行的 FAIL 全部来自 4 道检索已召回却拒答的题，这 4 道都是错误前提题（参考答案为「不对 / 不支持」），问答提示词只给了拒答出口。「门禁审计」页新增失败归因面板，列出这 4 道题的问题、回答与参考答案。
- 外部基准验证 `tools/competition/external_validation.py`：用 ANAH（中文逐句人工幻觉标注，Apache-2.0）与 RGB（中文新闻检索增强问答，CC BY-NC-SA 4.0）检验逐句核对本身，直接调用生产代码 `trace_hallucinations` 与 `rag_answer`。样本由固定种子从固定版本的源文件重建，源文件哈希逐一核对；付费调用须有绑定样本包与运行代码哈希的授权，不重试，按预算与请求数上限停止。2026-10-13 运行一次：500 次请求，按用量估算约 ¥2.68。结果如下：
  - ANAH 300 句，幻觉召回率 0.89、精确率 0.87（按实际比例折算约 0.72）、特异度 0.73；
  - RGB 噪声条件 40 个正确回答 0 误判，应拒答条件拒答 87.5%；
  - 资料本身含错误时，11 题照抄错误的回答只标出 3 题，作为已知边界写入文档。

  归档见 `docs/competition/external-validation/`，只含编号与判定，不转载原文；「门禁审计」页新增外部基准面板，数据生成时用逐条判定重算汇总并核对哈希。
- 鲁棒性体检 `scripts/robustness_check.py` 与 `backend/core/robustness.py`：把 RGB 的加噪、应拒答、反事实三种情形和门禁可信度第三问的证据敏感性实验，合成一个可用于任何 RAG 应用的功能。
  - 依据资料用逐字重合定位，不调用模型；构造打乱、加噪、移除依据、反事实（只改依据内的数字和情态词）四种条件，构造不了的写明原因。
  - 同时检验核对侧（同一回答换资料重新逐句核对）与生成侧（换资料重新作答再核对）；生成侧可接入被测应用的 HTTP 接口，令牌只从环境变量读取。
  - 付费调用须有绑定材料包与运行代码哈希的授权，不重试。2026-10-13 在 20 道正向题上运行一次：260 次请求，按用量估算约 ¥0.47。
  - 结果：核对侧 58 项中 57 项符合预期；生成侧移除依据后拒答 19 / 20，反事实条件下没有一题沿用原值。
  - 归档见 `docs/competition/robustness/`；工作台新增「鲁棒性体检」视图（`#/lab/robustness`）。
- 裁判判错校准 `backend/core/judge_error.py`：同时计入题目抽样与裁判判错的门禁区间。
  - 方法：Lee et al.（ICML 2026）的校准校正，推广到按题聚集（有效样本量）；另实现 PPI，以及不需要标注的敏感性分析（最少改判条数、临界灵敏度）。
  - 模拟检验（`tools/competition/judge_error_simulation.py`，24 种设定、每种 2000 次）：只计题目抽样的区间实际覆盖率只有 3%–53%，按题聚集的校正为 94%–96%；裁判判定漂移时校准校正失准，同次运行人工标注不受影响。
  - 盲标注材料与分析工具 `tools/competition/judge_calibration.py`、标注结果格式 `schemas/judge-annotation.v1.schema.json`；工作台新增「人工标注」视图（`#/lab/annotate`）。
  - 分析口径：分母与门禁一致；分歧与「无法判断」不剔除，只报告点估计上下界；存在未决时不把端点区间合并冒充稳健的 95% 区间，暂停区间结论并保留原因，新增混合赋值反例测试。只标部分题时，须事先确定本批题数，并只用从 Q01 起连续标完、至少 10 题的部分做 PPI，否则不给区间并写明原因。工具与 schema 的校验一致，均有测试。
  - 「门禁审计」页新增裁判判错校准面板，分开显示三个状态：敏感性分析（已完成）、模拟验证（已完成）、真实人工校准（待标注）。
  - 现阶段：已实现裁判误差分析工具和模拟验证，真实人工校准待完成；之前不报告校正后的结论。
- `.gitattributes`：外部基准验证归档中三个 JSON 实为 CRLF，改为按原样存储，避免在 Linux 上检出后哈希不符。
- `backend/core/regression.py` 改用可移植的 Mulberry32 伪随机数与整数拒绝采样，不再依赖 numpy，已发布的区间数值不变；新增与 JavaScript 参考实现逐位一致的测试。
- 供应链安全（Scorecard 首次评分 2.9 后补齐）：所有工作流默认只读权限；新增 Dependabot（Actions、pip、npm 每周分组更新）与 CodeQL 静态扫描（Python、TypeScript）。
- ROADMAP 补里程碑、目标日期与 Issue / PR 响应时限；作品介绍补「上线前接入 CI 作回归门禁」用法。

## [1.2.0] - 2026-10-13

> 定位演进说明：项目由早期的「RAG 大模型幻觉自动化评测工具」演进为「鉴真全域 VeriScope AI · 逐句核对与质量门禁平台」，为同一项目的持续演进（非新建项目）。2026-10-08 至 10-10 期间曾使用「AI 测试交付与质量门禁平台」的表述，2026-10-11 起统一为现名。

### 门禁审计与可信度（2026-10-11 至 2026-10-12）
- 门禁可信度四问：从复算、换裁判、证据敏感性、结论确定性四个方面审计门禁本身，见 `docs/competition/gate-credibility.md`。
- 裁判证据敏感性实验 `tools/competition/judge_sensitivity.py`：固定 6 条既有陈述，只改变提供给裁判的证据（原样重复、打乱顺序、移除依据、改成矛盾），两个裁判共 60 次请求；材料经作者逐条确认、付费单独授权，2026-10-12 运行一次，档案与离线分析见 `docs/competition/judge-sensitivity/`。
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

### 治理与文档（2026-10-13）
- 新增 SECURITY.md（私密漏洞报告与备用邮箱）、Issue 模板与 PR 模板；CONTRIBUTING 的安全报告方式指向 SECURITY.md。
- README 补充典型用法「上线前接入 CI 作回归门禁」，更正 CI 覆盖范围的旧表述；演示脚本新增门禁接入 CI 的镜头。
- 新增 v1.2.0 发布说明 `docs/releases/v1.2.0.md`。

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
