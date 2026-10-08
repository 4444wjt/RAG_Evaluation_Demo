# 鉴真全域 VeriScope AI

**把 AI 应用的可靠性，交付成可检查的测试结论。**

**AI 测试交付与质量门禁平台。**

鉴真全域 VeriScope AI 是面向 AI 应用（当前以 RAG 问答为测试对象）的测试交付与质量门禁平台。将测试用例集、多模型问答、Ragas 指标、逐句溯源和 Quality Gate 汇聚到一条可复用的测试交付链路，把「回答是否可信」从一句主观判断，变成「用例 → 断言 → 门禁 → 报告 → 复核」可逐条回查的测试结论。

项目采用 React 比赛展示首页与仪表盘、FastAPI 本地接口及既有 Python 评测引擎；CLI 和 Gradio 入口继续保留。支持无密钥演示快照与配置后的真实评测。

[快速启动](#快速启动) · [比赛演示](docs/demo/README.md) · [评测可信度与限制](docs/evaluation-credibility.md) · [技术架构](docs/architecture/README.md) · [贡献指南](CONTRIBUTING.md)

[作品介绍](docs/competition/project-introduction.md) · [复现与运行证据](docs/competition/reproducibility.md) · [25题正式运行证据](docs/competition/evidence/20261005T144504Z-full-39511424/README.md) · [5题历史预跑](docs/competition/evidence/20261005T101847Z-pilot-c72cb679/README.md) · [验收记录](docs/competition/validation.md) · [25题作者确认材料](scenarios/competition_25/README.md) · [三分钟演示](docs/competition/demo-script.md)

![鉴真全域 VeriScope AI 桌面首页](docs/images/landing-desktop.png)

## 背景问题

AI 应用上线前，可靠性判断往往停在「一个平均分」或「肉眼抽查」，缺少一套可交付、可复用的测试结论：哪些用例测了什么、每条断言依据是什么、是否达到发布门槛、结果如何被复核。

本项目把「AI 应用测试」当作一条可交付的流水线来组织，围绕三个问题交付结论：

- **测试用例集是什么？** 自动出题或自定义测试集，保留检索上下文、模型回答、参考答案与逐句判定记录。
- **断言结果在哪里？** 同一测试集上呈现多模型的指标、风险统计与耗时，逐句给出依据。
- **是否通过质量门禁？** 将样本范围、正向指标、阈值与原始门禁记录分开展示，交付可复核的发布结论。

## 核心能力

一条从「用例」到「复核」的测试交付链路：

| 交付环节 | 能力 | 已有实现 |
|---|---|---|
| 测试用例集 | RAG 可靠性验证 | 文档处理、Chroma 检索、模型问答及 Ragas 评测；支持自动出题、自定义测试集和 TruthfulQA 入口 |
| 断言结果 | 逐句证据追踪 | 展示原始回答、原子陈述、有依据／事实错误／无依据编造等原判定，以及对应证据与检索原文 |
| 质量门禁 | Quality Gate | 后端按首个有效评测模型的正向忠实度与正向上下文精度判定；默认阈值各为 0.8 |
| 多模型对比 | 多模型评测 | DeepSeek、Qwen2.5-7B、GLM-5.2-Fast；统一测试集，呈现已有指标与耗时 |
| 报告导出 | 报告生成 | 后端已有 CSV、HTML、Excel、PDF 和图表能力；React 报告页支持本地 HTML 隔离预览 |
| 人工复核 | 评测验证 | 人工结论与机器原判定分开展示，核验材料绑定冻结快照版本与 SHA-256 |
| 比赛展示 | 演示与呈现 | 中文优先、Gold Design System、证据流程示意、清晰原生预览及已有演示快照 |

Ragas 指标包含忠实度、上下文精度、答案相关性、事实一致性和上下文召回。幻觉率、拒答准确率等沿用既有统计口径，不在展示层重新定义。

**解释边界：** PASS 仅表示对应样本和门槛的规则检查通过，不等于所有指标优秀或每条回答正确。逐句判定依赖原始溯源记录，不是额外的独立真实性保证。缺失指标不填为零，空溯源不代表没有幻觉。

**可信度核验：** Demo 包含 6 个问题、3 个模型、18 条回答；“评测验证”定向预选 13 条回答，已由 AI 辅助整理材料、作者人工逐条核验（一致 3、分歧 5、无法确认 5）。人工材料通过现有 JSON 单独录入，不覆盖机器结果、不生成准确率；该核验仅覆盖冻结 Demo 的 13 条定向样本，不代表总体准确率、独立认证或模型复跑验证。历史金融 FAIL 案例保留真实回答与保存指标。范围、四种核验状态和未验证事项见 [评测结果可信度与限制](docs/evaluation-credibility.md)。

**当前真实运行：** 2026-10-05 已完成一次经授权的25题单模型正式运行。输入是同一《存款保险条例》节选的固定场景，不是跨领域 Benchmark：25条回答全部完成，使用请求模型 `deepseek-chat`，实际返回模型记录为 `deepseek-flash`；Ragas 0.1.21 与 `zh-sentence-v1`，325次完整调用、558,958 token。五项指标均25/25有效，Trace非空18条、空7条。正向 Faithfulness 为 0.7375、正向 Context Precision 为 0.9833，前者低于0.8所以保留真实 FAIL。该结果不代表总体准确率或独立裁判可靠性；新分析标为“AI辅助分析、待人工确认”。[正式运行证据](docs/competition/evidence/20261005T144504Z-full-39511424/README.md)与[离线分析表](docs/competition/evidence/20261005T144504Z-full-39511424/analysis.md)保存完整回答、contexts、指标、Trace、报告和哈希。

## 技术架构

| 模块 | 技术与职责 |
|---|---|
| Frontend | React 19、TypeScript、Vite、Apache ECharts、原生 CSS；首页与六个工作台视图 |
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

前端继续通过 `npm run dev` 启动。进入工作区配置项目内文档路径、测试集、模型和阈值，再开始评测。

`/health` 仅表示服务存活，不验证模型、Embedding 或密钥。React 报告页通过本地文件选择导入 HTML；不是服务端报告下载入口。`npm run build` 生成静态产物，公网部署仍需单独配置代理和访问控制。

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

欢迎提交脱敏的问题报告、文档改进、可复现测试和有明确验证依据的改动。提交前阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，保持模型 IDs、指标口径、门禁与演示数据的一致性。

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

既有 GitHub Actions 当前执行 Python 离线验证；此处列出前端验证命令不代表已新增前端 CI。演示步骤和结果解释边界见 [比赛演示指南](docs/demo/README.md)。

## 许可与仓库

本项目自有代码按 [MIT License](LICENSE) 发布，保留现有 `Copyright (c) 2026 KK`。第三方代码与依赖仍适用各自许可证；前端已移除 React Bits 组件和 Commons Clause 代码。当前保留的第三方源码改编及运行依赖许可证见 [第三方说明](frontend/THIRD_PARTY_NOTICES.md) 和 [许可文件](frontend/licenses/)。根目录 MIT 许可不替代第三方依赖的许可证。

当前本地 Git 配置的远端：

- GitHub：[`4444wjt/RAG_Evaluation_Demo`](https://github.com/4444wjt/RAG_Evaluation_Demo)，remote 为 `github`。
- Gitee：[`i-am-a-bullshit/rag-hallucination-evaluator`](https://gitee.com/i-am-a-bullshit/rag-hallucination-evaluator)，remote 为 `origin`。

开源整理尚未暂存、提交或推送。发布步骤和检查结果见 [发布检查](docs/release-checklist.md)；目标仓库需维护者确认后执行。
