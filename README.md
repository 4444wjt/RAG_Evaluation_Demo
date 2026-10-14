# 鉴真全域 VeriScope AI

**AI 的每个回答，都能逐句查到出处。**

**逐句核对与质量门禁平台。**

鉴真全域 VeriScope AI 面向 RAG 问答应用：把模型回答拆成逐句，每一句对照检索原文核对是否有依据，再由质量门禁（Quality Gate）按阈值决定这批回答能否放行。题集、检索上下文、多模型回答、Ragas 指标、逐句判定、门禁记录和人工核验保存在同一条链路上，「回答是否可信」可以按「题集 → 逐句核对 → 门禁 → 报告 → 人工核验」逐条回查。

**典型用法：RAG 应用上线前，把它接进 CI 当回归门禁——门禁不通过，构建失败。** 已归档的评测结果可离线重放门禁裁决，不调用模型、不需要密钥，不通过时以非零退出码结束。门禁已打包为 GitHub Action（[action.yml](action.yml)），其他仓库加一步 `uses: 4444wjt/RAG_Evaluation_Demo@master` 即可接入，CI 页面会显示哪个指标没过、差多少（作业摘要与 JUnit 报告）。指定基线（`--baseline` / `baseline`）后还会逐题对比上一次运行，区分「确定退步」和「在题目抽样与裁判波动范围内的下降」，只有前者拦截；接入示例见 [quality-gate-example.yml](.github/workflows/quality-gate-example.yml)，用法见 [门禁回放](docs/gate-replay.md)。

项目采用 React 比赛展示首页与仪表盘、FastAPI 本地接口及既有 Python 评测引擎；CLI 和 Gradio 入口继续保留。支持无密钥演示快照与配置后的真实评测。

[English](README.en.md) · [快速启动](#快速启动) · [比赛演示](docs/demo/README.md) · [评测可信度与限制](docs/evaluation-credibility.md) · [技术架构](docs/architecture/README.md) · [贡献指南](CONTRIBUTING.md)

[作品介绍](docs/competition/project-introduction.md) · [门禁可信度四问](docs/competition/gate-credibility.md) · [外部基准验证](docs/competition/external-validation/README.md) · [鲁棒性体检](docs/competition/robustness/README.md) · [裁判判错校准](docs/competition/judge-calibration/README.md) · [复现与运行证据](docs/competition/reproducibility.md) · [25题正式运行证据](docs/competition/evidence/20261005T144504Z-full-39511424/README.md) · [5题历史预跑](docs/competition/evidence/20261005T101847Z-pilot-c72cb679/README.md) · [验收记录](docs/competition/validation.md) · [25题作者确认材料](scenarios/competition_25/README.md) · [三分钟演示](docs/competition/demo-script.md)

![鉴真全域 VeriScope AI 桌面首页](docs/images/landing-desktop.png)

## 背景问题

AI 应用上线前，可靠性判断往往停在「一个平均分」或「肉眼抽查」：说不清错在哪一句、依据是哪段原文、是否达到放行门槛，人工复核时也难以和机器判定对照。

本项目围绕三个问题组织评测结果：

- **测试用例集是什么？** 自动出题或自定义测试集，保留检索上下文、模型回答、参考答案与逐句判定记录。
- **断言结果在哪里？** 同一测试集上呈现多模型的指标、风险统计与耗时，逐句给出依据。
- **是否通过质量门禁？** 将样本范围、正向指标、阈值与原始门禁记录分开展示，给出可复核的放行结论。

## 核心能力

一条从「题集」到「人工核验」的逐句核对链路：

| 环节 | 能力 | 已有实现 |
|---|---|---|
| 测试用例集 | RAG 可靠性验证 | 文档处理、Chroma 检索、模型问答及 Ragas 评测；支持自动出题、自定义测试集和 TruthfulQA 入口 |
| 断言结果 | 逐句证据追踪 | 展示原始回答、原子陈述、有依据／事实错误／无依据编造等原判定，以及对应证据与检索原文 |
| 质量门禁 | Quality Gate | 后端按首个有效评测模型的正向忠实度与正向上下文精度判定；默认阈值各为 0.8 |
| 多模型对比 | 多模型评测 | DeepSeek、Qwen2.5-7B、GLM-5.2-Fast；统一测试集，呈现已有指标与耗时 |
| 报告导出 | 报告生成 | 后端已有 CSV、HTML、Excel、PDF 和图表能力；React 报告页支持本地 HTML 隔离预览 |
| 人工复核 | 评测验证 | 人工结论与机器原判定分开展示，核验材料绑定冻结快照版本与 SHA-256 |
| 门禁自身 | 门禁审计 | 集中展示：门禁可信度四问的结论、失败归因（问题出在检索还是生成）、逐句核对的外部基准（ANAH / RGB）、门禁代码自检（性质测试、变异测试）与回归对比样例；数据由 `tools/competition/build_audit_fixture.py` 从冻结证据生成并核对哈希 |
| 鲁棒性 | 鲁棒性体检 | 对任意 RAG 应用改动检索资料（打乱、加噪、移除依据、反事实），检验应用会不会被资料带偏、逐句核对是否跟着证据走；可接入被测应用的 HTTP 接口 |
| 裁判误差 | 人工标注 | 请非作者盲标门禁所用的逐条判定，估计裁判判错的程度，给出同时计入题目抽样与裁判判错的区间 |
| 比赛展示 | 演示与呈现 | 中文优先、Gold Design System、证据流程示意、清晰原生预览及已有演示快照 |

Ragas 指标包含忠实度、上下文精度、答案相关性、事实一致性和上下文召回。幻觉率、拒答准确率等沿用既有统计口径，不在展示层重新定义。

**解释边界：** PASS 仅表示对应样本和门槛的规则检查通过，不等于所有指标优秀或每条回答正确。逐句判定依赖原始溯源记录，不是额外的独立真实性保证。缺失指标不填为零，空溯源不代表没有幻觉。

**可信度核验：** Demo 包含 6 个问题、3 个模型、18 条回答；“评测验证”定向预选 13 条回答，已由 AI 辅助整理材料、作者人工逐条核验（一致 3、分歧 5、无法确认 5）。人工材料通过现有 JSON 单独录入，不覆盖机器结果、不生成准确率；该核验仅覆盖冻结 Demo 的 13 条定向样本，不代表总体准确率、独立认证或模型复跑验证。历史金融 FAIL 案例保留真实回答与保存指标。范围、四种核验状态和未验证事项见 [评测结果可信度与限制](docs/evaluation-credibility.md)。

**当前真实运行：** 2026-10-05 已完成一次经授权的25题单模型正式运行。输入是同一《存款保险条例》节选的固定场景，不是跨领域 Benchmark：25条回答全部完成，使用请求模型 `deepseek-chat`，实际返回模型记录为 `deepseek-flash`，按 DeepSeek 官方[模型页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)即 DeepSeek-V4.1-Flash；Ragas 0.1.21 与 `zh-sentence-v1`，325次完整调用、558,958 token。五项指标均25/25有效，Trace非空18条、空7条。正向 Faithfulness 为 0.7375、正向 Context Precision 为 0.9833，前者低于0.8所以保留真实 FAIL。该结果不代表总体准确率或独立裁判可靠性；新分析标为“AI辅助分析、待人工确认”。[正式运行证据](docs/competition/evidence/20261005T144504Z-full-39511424/README.md)与[离线分析表](docs/competition/evidence/20261005T144504Z-full-39511424/analysis.md)保存完整回答、contexts、指标、Trace、报告和哈希。

**外部基准：** 2026-10-13 用两个公开、带人工标注的中文数据集检验逐句核对本身，直接调用生产代码，500 次请求（经授权，按用量估算约 ¥2.68）。
- **ANAH**（ACL 2024）逐句幻觉标注 300 句，各类等量抽取：有幻觉句子的召回率 0.89（95% 区间 0.84–0.93）；精确率 0.87，按实际比例折算约 0.72。人工判为无幻觉的句子有 27% 被判无依据，即核对比人工更严。
- **RGB**（AAAI 2024）新闻领域 100 题：含噪声资料时 40 个正确回答全部判为有依据；资料无关时拒答 87.5%。资料本身含错误时，照抄错误的回答多半仍被判为有依据（11 题中只标出 3 题）。这是忠实度核对的固有边界：它检查回答与证据是否一致，不检查证据本身对不对。

这是单次运行、单一裁判的抽样检验，不代表总体准确率，详见 [外部基准验证](docs/competition/external-validation/README.md)。

**鲁棒性体检：** 2026-10-13 在 25 题正式运行的 20 道正向题上运行一次（260 次请求，经授权，按用量估算约 ¥0.47）。
- 核对侧 58 项检验中 57 项符合预期：移除依据后 16 / 16 标出；反事实 9 / 10 判为矛盾，唯一漏判的改动落在修饰语上。
- 生成侧移除依据后拒答 19 / 20；反事实条件下没有一题沿用原值顶替资料。
- 打乱资料顺序后，两道错误前提题由拒答改为正确纠正，与失败归因的结论一致。

详见 [鲁棒性体检](docs/competition/robustness/README.md)。

**裁判判错校准：** 门禁区间原先把每条逐句判定都当作正确。现阶段：**已实现裁判误差分析工具和模拟验证，真实人工校准待完成。** 区间方法为 Lee et al. 的校准校正（推广到按题聚集）与 PPI，并在本次题目结构下做了模拟。
- 只计题目抽样的区间，实际覆盖率只有 3%–53%；按题聚集的校正为 94%–96%。
- 作答题中判为不支持的陈述只有 5 条，全部改判才到 80%。
- 门禁裁判的真实误差**待非作者人工标注**（52 条，约 30 分钟，工作台「人工标注」页），完成前不报告校正后的结论。分析口径已事先写定：分母与门禁一致，未决陈述给上下界，部分标注只用从第一题起连续、至少 10 题的部分。

详见 [裁判判错校准](docs/competition/judge-calibration/README.md)。

**用了本平台好在哪：** 同一次 FAIL，只看 Ragas 总分是 0.59（把 5 道正确拒答的负向题算作 0 分）。本平台给出正向题 0.7375，并说明：作答题 40 条陈述都有原文依据；拦截的 76% 来自 4 道错误前提题的拒答，该修提示词而不是检索；区间跨门槛，属于保守拦截。详见 [同一次 FAIL 的三种看法](docs/competition/value-comparison.md)。

**外部案例：** 2026-10-14 检验了 [Langchain-Chatchat](https://github.com/chatchat-space/Langchain-Chatchat) 的默认知识库提示词（经授权，100 次请求，约 ¥0.15）。同一模型、同一批资料，只换提示词：5 道错误前提题中，它拒答 2 道，本项目自己的提示拒答 3 道；其中 F03、F04 两道资料里有纠正所需的原文，两种提示都回答「无法回答」。资料无答案的 5 道题两者都正确拒答，作答的陈述逐句核对全部有原文依据。只测了默认提示词，不代表该项目的实际部署效果。详见 [外部案例](docs/competition/cases/langchain-chatchat/README.md)。

完整性哈希基于文件原始字节；仓库通过 [`.gitattributes`](.gitattributes) 逐文件固定检出换行符，以保证各平台校验一致。

## 复用与自研

指标计算不重复造轮子；本项目的工作在指标层之上。

| 层 | 内容 |
|---|---|
| 自研 | 逐句判定与证据定位（`backend/services/trace.py`：回答拆成原子陈述，逐句对应检索原文）；质量门禁裁决（`backend/core/gate.py`：正向样本双阈值，保留真实 FAIL）；人工核验存档（人工意见与机器判定并列保存、互不覆盖，绑定冻结快照 SHA-256，格式见 [人工核验存档格式](docs/review-archive-format.md)）；中文陈述兼容层 `zh-sentence-v1`（`backend/adapters/ragas_compat.py`） |
| 复用 | 指标计算：Ragas 0.1.21；检索与模型调用：LangChain、Chroma、OpenAI-compatible 接口；界面：React、ECharts；部署：Docker Compose（[deploy/](deploy/README.md)） |

阈值卡口本身并非本项目独有（例如 DeepEval 支持为指标设阈值并在 CI 中判定通过与否）。本项目的差异在于：门禁结论旁并列给出逐句核对证据，可回查到检索原文，并与人工核验的分歧放在同一份冻结快照里对照。该逐句证据与门禁并列呈现，不参与门禁计算；门禁由正向忠实度与正向上下文精度决定。以上比较基于各项目公开资料，不排除其内部已有类似实现。

**同源裁判偏差的实测：** 用 Qwen2.5-72B 作为独立裁判，重判 25 题正式运行（不重新生成回答）。门禁仍为 FAIL（正向 Faithfulness 0.7233），均值层面变化很小；但逐题差异明显：Faithfulness 平均绝对差 0.095，而原裁判自己重判一次只差 0.017；Trace 回答级标签一致率 72%，而原裁判重判为 92%。详见 [独立裁判对照](docs/judge-comparison.md)。

## 技术架构

| 模块 | 技术与职责 |
|---|---|
| Frontend | React 19、TypeScript、Vite、Apache ECharts、原生 CSS；首页与七个工作台视图 |
| Backend | Python、FastAPI；参数／项目路径校验、同步评测调用、结果序列化 |
| Evaluation Pipeline | LangChain、Chroma、本地 Embedding、Ragas；文档 → 测试集 → 检索与回答 → 指标与溯源 → 门禁与报告 |
| Model Adapter | 既有 OpenAI-compatible 模型适配；模型配置和凭据读取集中管理 |
| Compatibility | `main.py` 保留 CLI 和 Gradio；报告、存储、服务与核心评测分层在 `backend/` |

前端开发服务将 `/api` 代理到本地后端。评测为同步请求，后端按单进程执行保护运行；当前没有任务队列、取消或真实阶段进度接口。详细边界见 [架构说明](docs/architecture/README.md)。

### 目录结构

```text
RAG-Reliability-Lab/
├── frontend/                 # 比赛首页与研究工作台
├── backend/                  # API、core、services、adapters、reporting、storage
├── docs/
│   ├── images/               # README 引用的正式 PNG
│   ├── screenshots/          # 截图来源说明
│   ├── architecture/         # 技术架构
│   └── demo/                 # 演示步骤与历史评测说明
├── tests/                    # Python 离线测试
├── scenarios/                # 既有场景文档与记录
├── main.py                   # 兼容 CLI / Gradio 入口
├── requirements.txt          # 原有 Python 依赖约束
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── .gitignore
└── .env.example
```

项目品牌为鉴真全域 VeriScope AI；现有远端仓库和本地目录名称未强制重命名。仓库保留架构、用户、演示和贡献文档；内部交接、提示词及阶段记录不作为公开项目文档。

## 快速启动

建议 Python 3.11、Node.js 24。依赖版本以 `requirements.txt` 和 `frontend/package-lock.json` 为准。

只想在服务器上部署只读演示站时，可以跳过下面的步骤，直接用 Docker 一键部署：在 `deploy/` 目录执行 `./deploy.sh`，详见 [部署说明](deploy/README.md)。无需服务器的备用只读站发布在 GitHub Pages：https://4444wjt.github.io/RAG_Evaluation_Demo/（纯静态，由 `pages.yml` 自动构建）。

### 1. 获取项目

使用 GitHub 或 Gitee 任一仓库。本地目录可以命名为 `RAG-Reliability-Lab`：

```powershell
git clone https://github.com/4444wjt/RAG_Evaluation_Demo.git RAG-Reliability-Lab
cd RAG-Reliability-Lab
```

Gitee 当前配置地址见文末；发布前需维护者确认目标仓库。

### 2. 仅看比赛演示：不需要模型密钥

```powershell
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173/`，点击 **查看演示**。前端直接加载已有快照，不启动真实模型评测；后端未启动时的健康检查提示不影响演示内容。

### 3. 配置真实评测环境

另开终端，在项目根目录执行：

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

如果 `.env` 已存在，请保留已有文件并按模板补充配置，不覆盖现有密钥。

在本地 `.env` 填入凭据：

| 变量 | 用途 |
|---|---|
| `OPENAI_API_KEY` | 现有 DeepSeek 适配器使用的变量，填写 DeepSeek 凭据 |
| `SILICONFLOW_API_KEY` | Qwen / SiliconFlow 凭据 |
| `DASHSCOPE_API_KEY` | GLM-5.2-Fast / 百炼凭据 |
| `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL` | 已有 DeepSeek 配置覆盖项 |
| `QWEN_MODEL` / `QWEN_BASE_URL` | 已有 Qwen 配置覆盖项 |
| `RAG_ENABLE_TRACE` | 既有溯源开关，默认 1；设为 0 关闭额外溯源调用 |
| `EMBEDDING_MODEL` | 可选，指定已准备好的本地 Embedding 路径／缓存模型 |

预先准备 `BAAI/bge-small-zh-v1.5`，放在 `models/bge-small-zh-v1.5/` 或可用的 Hugging Face 本地缓存。引擎以离线方式加载，不会在评测时自动下载模型。

密钥只存放在本地 `.env` 或后端环境变量中；不要写进源码、截图、Issue 或 `VITE_*` 前端变量。真实评测会调用提供方服务并产生费用。

### 4. 启动后端

在项目根目录执行：

```powershell
.\venv\Scripts\python.exe -m uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

前端继续通过 `npm run dev` 启动。进入工作区，默认可直接选择项目自带示例，也可切换“电脑文件”选择或拖入资料，再配置测试集、模型和阈值。文档支持 TXT / PDF / DOCX / MD，自定义测试集支持 JSON / CSV / TXT，单个文件最多 10 MiB；导入会保存到当前评测服务的 `work/uploads/`，不会调用模型。确认配置后点击“开始评测”。已有项目文件的手动路径入口位于高级选项中。

`/health` 仅表示服务存活，不验证模型、Embedding 或密钥。React 报告页通过本地文件选择导入 HTML；不是服务端报告下载入口。`npm run build` 生成静态产物；公网部署的代理与只读拦截配置见 [deploy/](deploy/README.md)，可评测部署还需自行加上访问控制。

### 5. 保留的 CLI / Gradio 入口

```powershell
.\venv\Scripts\python.exe main.py --doc demo.txt --testset testset_example.json
.\venv\Scripts\python.exe main.py --web
```

Gradio 默认端口为 7860。当前 CLI 和 Gradio 经 `main.py.run_pipeline` 委托 `backend.core.pipeline.evaluate`，与 Web API 共享后端评测编排；入口参数、模型工厂和请求校验仍有区别，不能据此认定不同入口的运行条件完全相同。`_legacy_run_pipeline` 保留旧实现供历史回查和离线回归，并非当前正常入口。中文句子兼容层随共享编排生效，不改指标公式、Trace 或门禁阈值。运行产物仍按各自配置保存，历史产物不重算。

## Demo 截图

下列图片来自已有产品页面与快照 `20260930-1958`，不是本轮重新执行的模型评测。

### 笔记本展示

![1280×800 比赛首页](docs/images/landing-laptop.png)

### 实验结论与质量门禁

![质量门禁章节](docs/images/evidence-desktop.png)

### 逐句判定与原始证据

![3× 无损逐句证据预览](docs/images/evidence-trace-3x.png)

原生预览的 3× PNG 为 3198×846；保留原文与“有依据”判定，没有放大旧图片。出处见 [截图说明](docs/screenshots/README.md)，推荐展示步骤见 [演示指南](docs/demo/README.md)。

## Roadmap

已完成：

- [x] 评测引擎分层、CLI / Gradio 兼容入口和 FastAPI 本地接口。
- [x] 多模型指标、逐句溯源、正向 Quality Gate 与报告能力。
- [x] React 总览、工作区、模型对比、幻觉分析与报告预览。
- [x] 中文比赛首页、Gold Design System 与清晰证据展示。
- [x] 开源文档、环境模板、发布排除规则和第三方许可记录。

后续方向（尚未承诺完成）：

- [ ] 扩大测试覆盖与更强对抗样本，明确生成／裁判模型带来的偏差。
- [ ] 完善固定测试集回归、结果出处与可复现记录。
- [ ] 加强生产部署的鉴权、资源隔离及长任务管理。
- [ ] 补充持续前端验收和更完整的开发者文档。

## 开源贡献

欢迎提交脱敏的问题报告、文档改进、可复现测试和有明确验证依据的改动。提交前阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [行为准则](CODE_OF_CONDUCT.md)，安全问题按 [SECURITY.md](SECURITY.md) 私下报告；保持模型 IDs、指标口径、门禁与演示数据的一致性。

前端验证：

```powershell
cd frontend
npm test
npm run typecheck
npm run build
npm run test:browser -- browser-tests/landing.spec.ts
```

Python 离线验证（项目根目录）：

```powershell
.\venv\Scripts\python.exe -m pip install -r tests/requirements.txt
.\venv\Scripts\python.exe -m pytest -q tests
```

GitHub Actions 的 CI 在推送或向 master／main 发起 Pull Request 时执行 Python 离线测试与前端单元测试（含类型检查），两项均自提交 `c66de7b` 起通过；另有 `gate-action` 任务自检根目录的门禁 Action（演示快照应放行、25 题正式运行应拦截）；门禁代码变动时，`mutation.yml` 在 Linux 上对其做变异测试（本地结果：42 个变异抓住 39 个，见 [门禁可信度](docs/competition/gate-credibility.md) 附录）；浏览器端用例需本地运行，未纳入 CI。演示步骤和结果解释边界见 [比赛演示指南](docs/demo/README.md)。

## 许可与仓库

引用信息见 [CITATION.cff](CITATION.cff)（GitHub 仓库页的「Cite this repository」）。本项目自有代码按 [MIT License](LICENSE) 发布，保留现有 `Copyright (c) 2026 KK`。第三方代码与依赖仍适用各自许可证；前端已移除 React Bits 组件和 Commons Clause 代码。当前保留的第三方源码改编及运行依赖许可证见 [第三方说明](frontend/THIRD_PARTY_NOTICES.md) 和 [许可文件](frontend/licenses/)。根目录 MIT 许可不替代第三方依赖的许可证。

当前本地 Git 配置的远端：

- GitHub：[`4444wjt/RAG_Evaluation_Demo`](https://github.com/4444wjt/RAG_Evaluation_Demo)，remote 为 `github`。
- Gitee：[`kkbushishou/rag-hallucination-evaluator`](https://gitee.com/kkbushishou/rag-hallucination-evaluator)，remote 为 `origin`。

发布步骤和检查结果见 [发布检查](docs/release-checklist.md)；实际发布版本以仓库提交与 CI 记录为准。历史归档的公开缺项及校验边界见 [复现说明](docs/competition/reproducibility.md#公开检出校验边界)。
