# RAG 大模型幻觉自动化评测工具

> 面向大模型 RAG 应用“幻觉”痛点的轻量化自动化评测工具，基于 LangChain、Ragas 与 Chroma，支持三模型横向对比与质量门禁。

## 📖 项目背景
当前基于 RAG（检索增强生成）的大模型应用广泛落地，但“幻觉”（模型一本正经地胡说八道）严重影响可靠性。传统人工抽检效率低且不可复现。本项目通过自动化手段，从“检索 + 生成”两个环节量化评测 RAG 系统，并给出可落地的发布门禁。

## ✨ 核心功能
1. **多格式文档加载切分**：支持 `.txt` / `.pdf` / `.docx` / `.md`，自动分段存入 Chroma 向量库。
2. **测试集三来源**：大模型自动出题、自定义测试集（`.json` / `.csv`）、TruthfulQA 基准。
3. **RAG 链路评测**：执行检索与问答，收集上下文、回答与参考答案。
4. **量化指标评测**：基于 Ragas 计算 5 项核心指标（详见下文指标说明）。
5. **质量门禁**：只看正向用例的忠实度与上下文精度，双阈值判定“通过 / 不通过”。
6. **三模型横向对比**：DeepSeek / Qwen / GLM-5.2-Fast 一键对比，输出可视化图表。
7. **报告导出**：CSV / HTML / Excel / PDF 多格式，附质量趋势图与评测历史。

## 🛠️ 技术架构
- 语言：Python 3.11.9
- 编排框架：LangChain
- 评测框架：Ragas
- 向量库：Chroma
- Embedding：HuggingFace（`BAAI/bge-small-zh-v1.5`）
- 被测模型：DeepSeek（`deepseek-chat`）、Qwen（`Qwen2.5-7B-Instruct`）、GLM（`glm-5.2-fast-preview`）
- 界面：Gradio Web

## 🚀 快速开始
### 1. 环境准备
```bash
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### 2. 配置密钥
复制 `.env.example` 为 `.env`，填入三个模型的密钥（均为环境变量名）：
- `OPENAI_API_KEY`：DeepSeek
- `SILICONFLOW_API_KEY`：Qwen
- `DASHSCOPE_API_KEY`：GLM-5.2-Fast（阿里云百炼）

> 密钥只可作为环境变量注入，严禁写入仓库。

Embedding 模型需提前下载至本地（`models/bge-small-zh-v1.5` 或 HuggingFace 缓存），运行时不会联网下载。

### 3. 运行
```bash
# 命令行评测（demo.txt + 自动生成测试集 + 三模型）
python main.py --doc demo.txt

# 指定自定义测试集 / 基准测试
python main.py --doc demo.txt --testset testset_example.json
python main.py --benchmark "TruthfulQA" --limit 20

# 启动 Web 界面
python main.py --web           # 默认端口 7860
python main.py --web --port 8080
```

命令行参数：
| 参数 | 说明 |
| --- | --- |
| `--doc` | 评测文档路径 |
| `--testset` | 自定义测试集（`.json` / `.csv`） |
| `--benchmark` | 基准测试（如 `TruthfulQA`） |
| `--limit` | 基准测试条数（默认 20） |
| `--web` | 启动 Gradio Web 界面 |
| `--port` | Web 端口（默认 7860） |

## 📊 评测指标
| 指标 | 英文 | 含义 |
| --- | --- | --- |
| 忠实度 | Faithfulness | 回答是否都能在检索到的上下文中找到依据（幻觉检测核心） |
| 上下文精度 | Context Precision | 检索到的上下文与问题的相关程度 |
| 答案相关性 | Answer Relevancy | 回答与问题的相关程度 |
| 事实一致性 | Answer Correctness | 回答与标准答案的事实一致性 |
| 回答完整性 | Context Recall | 标准答案中应被检索到的信息是否被覆盖 |

派生指标：
- **幻觉率** = `1 − 正向忠实度`
- **综合得分** = `(正向忠实度 + 正向上下文精度) / 2`
- **拒答准确率** = `负样本中正确拒答（命中“无法回答/未提及”等措辞）的数量 / 负样本总数`（负样本的 ground_truth 为“文档未提及”，期望模型拒答而非编造）

## 🚦 质量门禁
门禁只依据**正向用例**（有答案可依的题目）计算，避免“拒绝回答”的负向用例拉高分数：

- 正向忠实度 ≥ 阈值（默认 `0.8`）
- 正向上下文精度 ≥ 阈值（默认 `0.8`）

两项同时满足才判为“通过，允许发布”，否则“不通过”。主评测模型取**第一个成功完成评测的模型**。

## 对抗性负样本生成

自动出题模式默认生成 3 条文档相关负样本，替代固定的常识问题。优先覆盖三类：真实实体的缺失细节、虚构实体/关系嫁接、篡改数值条件后追问不存在的实验结果。简单是非题或能用原文直接纠错作答的问题不应标为“文档未提及”。自定义测试集、TruthfulQA 和演示数据不受影响。

- **先生成，再审查**：复用正向出题的 LLM，一次批量请求 6 个候选，再用另一条审查提示批量复核；默认最多增加 2 次应用层调用，不逐条调用、不在生成器内重试。底层客户端原有重试设置保持不变。生成/审查的输出上限分别为 2200/1600 token。
- **控制上下文成本**：生成阶段均匀选择最多 12 个片段，总文本不超过 6000 字符；审查阶段最多 12000 字符，长文优先保留候选来源和相关段落。文档作为数据单独传入，不拼入系统指令。
- **过滤伪负样本**：验证锚点与逐字原文证据，扫描全文排除已出现的“虚构细节”，与正向题及其他候选做标点/全半角归一化和近似去重；只接收明确通过审查的题目，优先保留不同攻击类型。
- **保守兜底**：生成或审查失败、候选不足时，用文档短句和全文未出现的 `NX-…` 虚构记录编号构造缺失细节问题，补足 3 条。日志分别给出“已审查生成”和“本地兜底”数量，不输出原始提供方异常。兜底问题更容易识别，不能将它们视为与 LLM 对抗候选等难度。
- **兼容返回结构**：每题仍为 `question`、`ground_truth="文档未提及"`、`contexts=[]`、`case_type="negative"`。证据和攻击类型仅供内部过滤；不改 Ragas 指标、score、门禁或下游组件。

**审查边界**：生成与审查复用同一个模型，不能保证完全消除标签错误。长文未全量送入审查时，只有能提供原文矛盾证据的候选才会被保留；证据真实性可做本地验证，其语义是否足以排除问题前提仍依赖模型判断，建议人工抽检。跨段落复杂推断容易误标，本版没有单独增加此类策略。

离线验证（假模型响应，不读 `.env`、不访问 API）：

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_negative_generation.py
.\venv\Scripts\python.exe -m pytest -q tests
.\venv\Scripts\python.exe -m py_compile main.py
```

真实效果验证建议：在已配置好的环境里对同一份文档运行自动出题，检查日志中的生成/兜底数量，并查看 `ragas_report_<model>.csv` 内 `case_type=negative` 的问题及回答。抽查问题是否确实无文档答案、是否与主题相关、回答是否拒绝虚构细节；三模型仍共用同一套测试题。“幻觉率”来自正向忠实度，不能替代负样本的“拒答准确率”；后者为新增指标（负样本正确拒答比例，命中“无法回答/未提及”等措辞计为拒答），见下方“真实场景案例”的实测结果。

## 🧪 真实评测案例
以 `demo.txt`（星云科技产品介绍）为评测文档，自动生成 3 正向 + 3 负向测试集，门禁阈值 0.8，三模型对比结果如下：

| 模型 | 忠实度 | 上下文精度 | 答案相关性 | 事实一致性 | 回答完整性 | 幻觉率 | 综合得分 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| deepseek-chat | 1.0000 | 0.4583 | 0.4877 | 0.5354 | 1.0000 | 0.0000 | **0.9584** | 71s |
| Qwen2.5-7B | 0.5833 | 0.5000 | 0.2914 | 0.1543 | 0.4667 | 0.3333 | **0.8334** | 265s |
| GLM-5.2-Fast | 1.0000 | 0.8333 | 0.4788 | 0.9158 | 0.6667 | 0.0000 | **0.9584** | 1267s |

门禁结论：主评测模型 `deepseek-chat`，正向忠实度 1.0000（≥0.8）、正向上下文精度 0.9167（≥0.8）→ **✅ 门禁通过**。

- DeepSeek 与 GLM 忠实度满分、幻觉率为 0；GLM 事实一致性（0.9158）三模型最高，但耗时最长（深度思考所致，见已知限制）。
- Qwen 忠实度偏低、幻觉率 0.3333，体现工具对不同模型的区分能力。

图表与产物：`result_chart.png`（对比柱状图）、`trend_chart.png`（质量趋势）、`ragas_report.html`、`multi_model_summary.csv`、各模型 `ragas_report_*.csv` 及 `eval_history.json`。

## ⚠️ 已知限制
1. **GLM-5.2-Fast 深度思考已通过 `extra_body` 关闭**：阿里云百炼上的 `glm-5.2-fast-preview` 默认启用 reasoning 模式，未关闭时每次调用会额外产生大量 `reasoning_tokens`，拖慢评测并增加成本。已在 `get_llm` 中针对百炼/GLM 模型传入 `extra_body={"enable_thinking": False}` 关闭思考（仅作用于此模型，不影响 DeepSeek / Qwen）。实测「只回复OK」单次调用：开启思考 `completion_tokens=95`（其中 `reasoning_tokens=92`）/ 耗时 2.07s；关闭后 `completion_tokens=1`（`reasoning_tokens=0`）/ 耗时 1.06s。
2. **Embedding 依赖本地模型**：初始化时使用 `local_files_only=True`，不联网下载，需提前将 `bge-small-zh-v1.5` 放入本地缓存或 `models/` 目录。
3. **自定义 CSV 空值处理已在 Phase 4-B 修复**：CSV/TXT 加载后先将 pandas 识别的缺失值填为 `""`，不再将缺失值转成 `"nan"`；原缺陷测试已转为普通回归测试，历史评测产物不重算。

## 📁 输出产物
- 汇总对比：`multi_model_summary.csv`
- 单模型明细：`ragas_report_<model>.csv`
- HTML 报告：`ragas_report.html`
- 图表：`result_chart.png`、`trend_chart.png`
- 评测历史：`eval_history.json`

## 📂 项目结构
```
main.py                # CLI / Gradio / 兼容入口，保留旧管线
backend/
  config.py            # 配置
  core/                # 类型、总编排、指标聚合与门禁
  adapters/models/     # 模型适配器
  services/            # 文档、检索、问答、测试集、负样本、溯源
  storage/             # 实验历史与本地产物存储
  reporting/           # 图表、HTML / Excel / PDF 报告
archive/diagnostics/phase_3_b_10/  # 历史诊断快照，不加入 pytest
tests/                 # 现有离线测试与测试依赖
scenarios/             # 公开场景文档；运行归档保留在本地
demo.txt               # 演示文档
requirements.txt       # 当前依赖版本，工程冻结不升级
Dockerfile             # 现有 Gradio 容器入口
.env.example           # 环境变量占位模板
CHANGELOG.md           # 变更记录
CONTRIBUTING.md        # 贡献指南
PHASE_3_B_10_FREEZE_REPORT.md  # 本轮整理与验收状态
LICENSE                # 开源协议
```

## 后端架构与兼容入口

后端沿用 config / core / adapters / services / storage / reporting 六层。`main.py` 的 `run_pipeline` 是兼容入口，委托 `backend.core.pipeline.evaluate`，仍保留 `_legacy_run_pipeline` 供兼容与回滚参考；本轮不删除旧管线、不改变参数或返回结构。

当前后端完整清单为 29 个 Python 文件、2410 行（含 `__init__.py`）。React、FastAPI 与 Docker Compose 属于后续阶段，本轮不引入。模型执行顺序、Ragas 五项指标、trace、score、门禁、失败语义和历史结构保持原有行为。

## 如何运行测试

在项目根目录使用现有 Python 3.11 虚拟环境，无需安装或升级依赖：

```powershell
.\venv\Scripts\python.exe -m compileall -q main.py backend tests
.\venv\Scripts\python.exe -m pytest -q tests
```

离线测试沿用 `tests/pytest.ini`、原测试用例和断言，不需要 `.env`、API Key、本地 Embedding 或网络。本地与 CI 使用相同的源码编译范围（`main.py`、`backend/`、`tests/`），不遍历虚拟环境或本地运行产物。Phase 3-B-10 的完整仓库语法检查结果保留在冻结报告。

冻结及 Phase 4-A 验收基线为 **176 passed、1 xfailed**，见 [PHASE_4_A_RECORD.md](PHASE_4_A_RECORD.md)。Phase 4-B 修复 CSV 空值并解除该缺陷测试的 xfail，实际验证结果见 [PHASE_4_B_RECORD.md](PHASE_4_B_RECORD.md)；下方 2026-09-28 的场景验证数字属于历史记录。

自定义 CSV/TXT 测试集沿用 pandas 默认的缺失值识别，读取后统一填为 `""`：空 `question` 经原有规则过滤，空 `ground_truth` 保留为空字符串，空 `case_type` 默认 `positive`。CSV 的 `contexts` 仍为 `[]`；全部问题无效时仍抛出原异常。JSON 和 benchmark 加载不变。不额外清洗普通文本；正常输入的指标与门禁规则不变，原含缺失值的输入重跑后可能因有效样本或类型修正而得到不同结果，已保存的历史结果不会修改。

仓库托管在 Gitee。现有 `.github/workflows/ci.yml` 是 GitHub Actions 配置，不会在 Gitee 自动执行；本轮不新增 Gitee 流水线，以本地离线验收为准。

## Phase 3-B-10 工程整理

- Git 的 `/models/` 仅排除根目录模型缓存，`backend/adapters/models/` 四个源码文件必须入库。
- Docker 构建排除本地 work、backup、模型缓存、历史和临时报告，Dockerfile 与部署入口保持不变。
- 五个诊断脚本原样复制归档并校验 SHA-256，根目录原件保留，不改写为测试。
- 经确认的 16 项历史备份/运行产物仅取消 Git 跟踪，现存本地文件保留；`regenerate_report.py`、`requirements_new.txt` 恢复保留，它们不是当前主程序的入口或依赖安装清单。
- `.env`、模型缓存、向量库、实时历史、浏览器资料及临时文件不入库。README 中的场景报告链接指向本地运行归档，不能当作仓库已提交的产物。
- 回滚快照存放在仓库外；冻结提交与验收结果见 [Phase 3-B-10 Freeze Report](PHASE_3_B_10_FREEZE_REPORT.md)。

## 真实场景案例

本节使用真实公开行政法规节选，核验日期为 2026-09-28；不是仿真案例。仅整理条文空白、换行和列项括号，保留条号与适用条件。结论只针对本次文档节选与随机生成的测试题，不代表完整行业能力。

| 场景 | 文档 | 篇幅 / 片段 | 可核验来源与节选范围 |
| --- | --- | --- | --- |
| 金融：存款保险 | [deposit_insurance.txt](scenarios/deposit_insurance.txt) | 1552 字符 / 12 块 | [上海市发展改革委公开法规 PDF](https://fgw.sh.gov.cn/cmsres/12/12ffae28e34c4ddca96be15eb536cfcc/9d219522a984fcf99715211700a297b2.pdf)，第一至五、八至十、十九条 |
| 政务：政府信息公开申请 | [information_disclosure.txt](scenarios/information_disclosure.txt) | 1621 字符 / 13 块 | [国家市场监督管理总局](https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/bgt/art/2025/art_dd2bea320df84cfc91245630c8fb2ee7.html)，第二十九至三十四、三十七、三十八、四十二条 |

**运行方式与预算**：自动出题仍由 DeepSeek 主用、Qwen 兜底，目标为共用的 3 正 + 3 负测试题。总预算 5 元；存款保险场景安排 DeepSeek / Qwen / GLM 三模型，政务场景安排 DeepSeek / Qwen 双模型。GLM 第二场不调用，不记作模型失败或 0 分。只新增文档与案例记录，不改 main.py、Ragas、门禁或计分规则。

金融场景使用原 CLI：

```powershell
.\venv\Scripts\python.exe main.py --doc scenarios/deposit_insurance.txt
```

CLI 没有模型筛选参数；第二场通过原有 `run_pipeline(model_names=...)` 参数选择前两个模型，正常导入 main 时仍由程序自动加载 `.env`：

```powershell
.\venv\Scripts\python.exe -c "import main; r = main.run_pipeline(doc_path='scenarios/information_disclosure.txt', model_names=[c['name'] for c in main.MODELS_CONFIG[:2]]); raise SystemExit(0 if r['gate_passed'] else 1)"
```

每场结束后先调用现有 `generate_trend_chart()`，再立即归档本轮新生成的汇总 CSV、各模型明细 CSV、HTML、柱状图、趋势图及历史快照至 `scenarios/<场景名>/`。CLI 本身不会重绘趋势图；失败模型残留的旧 CSV 不能充当本轮结果。趋势图包含既有历史，跨文档的得分变化不等同于固定测试集上的回归趋势。进程退出码 1 也可能只是质量门禁拦截，需结合日志及产物判断。

### 实测结果（2026-09-28）

下表五项 Ragas 指标是全部用例的有效值均值；幻觉率与综合得分沿用原程序的正向口径。拒答准确率为新增指标：负样本（ground_truth=“文档未提及”）回答命中“无法回答/未提及”等拒答措辞的比例，其值由存档 CSV 离线回填得到。空值不填 0。原始数据见每场归档的 CSV 与 run_record.json。

| 场景 | 模型 | 忠实度 | 上下文精度 | 答案相关性 | 事实一致性 | 回答完整性 | 幻觉率 | 拒答准确率 | 综合得分 | 模型耗时（秒） |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 金融 | deepseek-chat | 0.7500 | 0.3889 | 0.4602 | 0.7353 | 0.5000 | 0.0000 | 1.0000 | 0.8889 | 57.72 |
| 金融 | Pro/Qwen/Qwen2.5-7B-Instruct | 0.4667 | 0.4722 | 0.3951 | 0.3565 | 0.5000 | 0.2222 | 1.0000 | 0.8611 | 362.55 |
| 金融 | glm-5.2-fast-preview | 1.0000 | 0.3889 | 0.4573 | 0.8199 | 0.8333 | 0.0000 | 1.0000 | 0.8889 | 85.01 |
| 政务 | deepseek-chat | 1.0000 | 0.5000 | 0.4664 | 0.7442 | 0.6667 | 0.0000 | 1.0000 | 1.0000 | 62.23 |
| 政务 | Pro/Qwen/Qwen2.5-7B-Instruct | 0.7000 | 0.5000 | 0.1757 | 0.3972 | 0.8333 | 0.0000 | 1.0000 | 1.0000 | 432.37 |

政务场景 GLM **未运行（预算控制）**，不填分数。单场总耗时：金融 527.87 秒，政务 511.89 秒（包括初始化、自动出题及报告生成）。

### 门禁结论

门禁仍以第一个成功评测模型 DeepSeek 的正向指标判定，两项阈值均为 0.8。

| 场景 | 主模型 | 正向忠实度 | 正向上下文精度 | 原程序门禁 |
| --- | --- | --- | --- | --- |
| 金融 | deepseek-chat | 1.0000 | 0.7778 | 拦截 |
| 政务 | deepseek-chat | 1.0000 | 1.0000 | 通过 |

金融场景的 DeepSeek 正向忠实度为 1，但正向上下文精度 0.7778 未达标，所以即使 score 为 0.8889 仍应拦截；没有为了案例好看放宽门槛。政务场景两项均为 1，门禁通过。

### 负样本原文与观察

**两场日志均为“已审查生成 0 条，本地兜底 3 条”**。以下三类只是对兜底模板的人工描述，不是 LLM 攻击策略标签；本次没有证实 missing_detail / entity_mix / numeric_condition 三类动态对抗策略的覆盖。没有重新生成题目来筛选成功案例。

| 场景 | 兜底模板描述 | 实际问题 |
| --- | --- | --- |
| 金融 | 虚构复现实验数据 | 围绕文档中的“存款保险条例（节选） 第一条 为了建立和规范存款保险制度，依法保护存款人”，编号“NX-4a371ca742-0”的独立复现实验各组原始测量值是多少？ |
| 金融 | 虚构后续评审条件 | 文档涉及“第二条 在中华人民共和国境内设立的商业银行、农村合作银行、农村信用合作社”，请列出编号“NX-4a371ca742-1”的后续评审记录中新增加的例外条件及依据。 |
| 金融 | 虚构补充审计参数 | 关于文档中的“第三条 本条例所称存款保险，是指投保机构向存款保险基金管理机构交纳保费，”，补充审计记录“NX-4a371ca742-2”采用了哪些误差校正步骤及参数？ |
| 政务 | 虚构复现实验数据 | 围绕文档中的“中华人民共和国政府信息公开条例（2019年修订，节选）”，编号“NX-268e3e7125-0”的独立复现实验各组原始测量值是多少？ |
| 政务 | 虚构后续评审条件 | 文档涉及“第二十九条 公民、法人或者其他组织申请获取政府信息的，应当向行政机关的政”，请列出编号“NX-268e3e7125-1”的后续评审记录中新增加的例外条件及依据。 |
| 政务 | 虚构补充审计参数 | 关于文档中的“（一）申请人的姓名或者名称、身份证明、联系方式； （二）申请公开的政府信”，补充审计记录“NX-268e3e7125-2”采用了哪些误差校正步骤及参数？ |

这些负样本的 ground_truth 均为“文档未提及”。金融三模型、政务双模型共 5 组、15 条负样本回答均明确包含“根据提供的文档，无法回答该问题”，部分 Qwen 回答补充了不存在相应编号记录的说明。现已新增“拒答准确率”指标（负样本正确拒答比例），对存档 CSV 离线回填后五组均为 1.0000；该指标与“幻觉率”（1 − 正向忠实度）含义不同、不能互相替代。带显著虚构编号的兜底题较容易，不能据此宣称对强对抗幻觉免疫。

### 缺失值与适用边界

| 场景 | 模型 | 正向忠实度缺失数 / 3 | 全部忠实度缺失数 / 6 |
| --- | --- | --- | --- |
| 金融 | deepseek-chat | 0 | 2 |
| 金融 | Pro/Qwen/Qwen2.5-7B-Instruct | 0 | 1 |
| 金融 | glm-5.2-fast-preview | 1 | 4 |
| 政务 | deepseek-chat | 0 | 3 |
| 政务 | Pro/Qwen/Qwen2.5-7B-Instruct | 0 | 1 |

五组结果的其他四项 Ragas 明细均无空值。金融 GLM 有一条正向忠实度缺失，其正向均值 1.0000 只来自剩余有效值，不能称为三题全满分。日志包含拒答时无法抽取陈述、评测输出解析失败等信息，不能只凭空值反推模型幻觉。统计仍由原程序执行，没有改写缺失值处理。

每场只有 3 道自动正向题，当前生成器按前几个有效片段出题，不能代表全文覆盖；不同模型还使用各自 LLM 执行 Ragas 裁判，分数差异不只是被测回答差异。进一步提升对抗题通过率和测试覆盖是后续工作，本任务没有修改生成器或裁判。

### 归档、图表与费用

- 金融：[归档目录](scenarios/deposit_insurance/)、[指标 CSV](scenarios/deposit_insurance/multi_model_summary.csv)、[柱状图](scenarios/deposit_insurance/result_chart.png)、[趋势图](scenarios/deposit_insurance/trend_chart.png)、[HTML](scenarios/deposit_insurance/ragas_report.html)、[本次记录](scenarios/deposit_insurance/run_record.json)。
- 政务：[归档目录](scenarios/information_disclosure/)、[指标 CSV](scenarios/information_disclosure/multi_model_summary.csv)、[柱状图](scenarios/information_disclosure/result_chart.png)、[趋势图](scenarios/information_disclosure/trend_chart.png)、[HTML](scenarios/information_disclosure/ragas_report.html)、[本次记录](scenarios/information_disclosure/run_record.json)。

归档还包含各模型 CSV、eval_history.json 快照、run.log 与 archive_manifest.json（文档、main.py 和产物哈希，及空值数量）。政务归档仅含 DeepSeek/Qwen 明细，没有混入首场 GLM 的旧 CSV。完整历史快照和趋势图包含更早的演示/评测记录，只有 run_record.json 代表当前这一场。既有 .gitignore 忽略 PNG、评测 CSV/HTML/历史及日志；归档目前可在本地访问，若需展示在 Gitee，需另行选择要纳入版本管理的产物，本次没有暂存、提交或 push。

**费用状态**：本次仅运行 2 场、5 组模型评测，未重跑，GLM 仅参与首场。调用前估算为金融 2–4 元、政务预留 0.5 元，合计约 2–4.5 元，另留 0.5 元缓冲。GLM 北京区公开原价为输入 16 元/百万 Token、输出 56 元/百万 Token（[官方价格](https://help.aliyun.com/zh/model-studio/glm-5-2-fast)）。估算按每模型约 60–90 次内部生成、GLM 约 8–15 万输入及 1–2 万输出 Token 的短文量级，另为 DeepSeek 出题/评测及 Qwen 留量；这些 Token 数是规划假设，不是采集值。原 CLI 未记录请求级 Token 或账单，旧模型别名的实时单价也未完整核实，因此 **实际扣费未知，不能凭本次产物证明实际总额一定不超过 5 元**。archive_manifest.json 的 actual_api_cost_cny 保持 null，不填写虚构金额。以三家平台账单为准。

**验证**：`python -m py_compile main.py` 通过；`pytest -q tests` 为 **135 passed, 1 xfailed（5.05 秒）**，xfail 为原有 CSV 空值问题。main.py 执行前后的 SHA-256 均为 `5234a36c0c78a5c46a5b83bf77ad8cbb30ac91b62af699affcca5d94341bc567`。本次没有新增依赖、修改代码或保留临时脚本。
