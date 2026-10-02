# Phase 5 · React 前端产品化记录

完成日期：2026-10-01。

## 修改范围

本轮只新增 `frontend/`，未修改 `main.py`、现有后端、Gradio、CLI、API 合同、原 Python 测试及 Python 依赖。未暂存、提交或 push。

- 工程：React、TypeScript、Vite、原生 fetch、局部状态及 reducer；固定依赖版本与 npm lockfile。
- 接口：`src/api/` 保留现有请求合同与八字段结果，Vite 同源 `/api` 代理到原 FastAPI。
- 视图：总览、Experiment Workspace、多模型对比、幻觉分析、报告。
- 展示：统一深色 CSS token，门禁优先、摘要模型明确标注、KPI 直接读取原字段、ECharts 能力/风险/耗时图、模型首列固定。
- 配置：项目相对路径、模型多选、互斥测试集来源、阈值默认值与零值透传；没有上传接口或假进度。
- 证据：直接解析已有 trace，按原 contexts 展开；缺失或解析失败不能解释为没有幻觉。
- 报告：浏览器本地导入 HTML，隔离 iframe 禁用脚本及外部请求；路径只用于复制，手动文件与当前结果不自动关联。
- 验收：新增前端离线单元/组件测试及 Playwright 浏览器用例；未修改原测试断言。

## 数据来源

离线快照使用现有同次运行 `20260930-1958`（2026-09-30 19:58:34），保留三个模型、18 条用例及原始指标/trace。来源文件和 SHA-256 见 `src/fixtures/README.md`。

原运行没有保存 `gate_message`，快照保留空字符串并显示缺失说明；没有补造门禁原因、检索来源、页码或证据。演示入口不发送评测请求。

## 实际验证

| 检查 | 结果 |
|---|---|
| `npm run typecheck` | 通过 |
| `npm test` | 59 passed，4 个测试文件 |
| `npm run build` | 通过 |
| `npm run test:browser` | 7 passed |
| 原 `python -m compileall -q main.py backend tests` | exit code 0 |
| 原 `python -m pytest -q tests` | 227 passed |
| `git diff --check` | 通过 |
| 49 个既有文件 SHA-256 对照 | 无变化、无新增既有模块文件 |

浏览器使用已有 Chrome，API 请求全部为 mock。已检查 1920×1080、1280px 和 390px 下的五个视图：无页面横向溢出；1920 首屏可见门禁、KPI 与主能力图。已验证模型名称列滚动固定且背景不透明、键盘模型选择、阈值数值/重置、运行中保留上次结果、禁止重复提交、门禁拦截正常完成。

已手动导入并查看项目原 `ragas_report.html`，确认标题、表格和内嵌图片保留；恶意脚本与外部资源用离线浏览器用例验证被隔离。截图位于已忽略的 `test-results/`。

主图使用模块化 ECharts 导入。当前生产 JS 约 836 kB（gzip 约 265 kB），构建仍提示超过 500 kB 的非阻断体积警告；本轮未扩展为复杂分包工程。

## 运行与限制

前端本地入口：`http://127.0.0.1:5173/`。后端未启动也可以加载演示；真实评测需要现有 API 单 worker 服务。

本轮没有读取或打印 `.env`/Key，没有调用真实模型 API，也没有启动或重启原 Gradio/API。真实评测接线仅通过 mock 验证，不作为模型可用性验收。

生产构建需要后续部署提供同源 API 代理；当前仅本地开发/预览。没有新增历史持久化、任务队列、Run ID、下载或上传系统。API/Gradio/CLI 共享产物，真实评测需交替执行。

回滚仅撤销本轮新增 `frontend/` 并停止其开发服务，不处理原有工作区修改。

## 联调验收补充 · 2026-10-01

修复范围仍仅限前端，没有新增依赖或 API，没有修改后端：

- `src/App.tsx`、`src/features/WorkspaceView.tsx`、`src/styles.css`：原“检查配置”仅切换视图，在工作区内没有可见效果。现在先校验本地参数，再复用现有 `checkHealth()` 调用 `GET /health`，展示检查中/成功/失败结果、自动聚焦反馈并防止重复检查；检查不会重新提交评测。顶部健康刷新同步增加检查期间禁用状态。
- `src/state.ts`：修复清空基准样本数量后切回非基准模式时发送 `null` 的问题，仅在非基准模式且缓存数量无效时回退原默认值 20；有效数量透传及真实 benchmark 校验保持不变。
- `src/features/OverviewView.tsx`：运行中的空状态演示入口同步禁用，避免看似可点但动作被执行锁忽略。
- `src/test/App.test.tsx`、`src/test/contract.test.ts`、`browser-tests/workspace.spec.ts`：补充上述交互的离线回归；保留原验收断言。
- 本 README/阶段记录补充实际检查含义与结果，不改根目录文档。

本轮实际结果：`npm test` 为 **72 passed**；`npm run test:browser` 为 **10 passed**；`npm run build`（含 TypeScript 检查）通过；`git diff --check` 通过。检查配置的浏览器截图已查看，反馈可读且可聚焦；原 1920/1280/390px、表格固定列、报告隔离及运行状态用例全部继续通过。构建仍有原有的非阻断包体积提示（当前 JS 约 837 kB）。

对照前轮 49 个既有文件摘要，后端、main.py、原 Python 测试、Python 依赖及根 README 均无变化；没有重新运行与本轮无关的 Python 测试，也没有提交或 push。

真实本地 `:8000/health` 和 `:5173/api/health` 连通检查本次未成功，不能据此宣称服务或模型可用。成功、断连、无效配置及重试路径均由离线 mock 验证；没有启动或重启后端，没有调用真实模型 API。

## Phase 5-B · 视觉精修 · 2026-10-02

沿用已批准的“工具为主，Apple 质感”方案，仅实施前端展示修改；参考产品研究及当前页面审查已在方案阶段完成，本阶段未重新设计信息架构。

### 修改文件

- `src/styles.css`：石墨黑背景层级、统一语义 Token、Typography、按钮与导航状态、KPI、Workspace、空状态和响应式。
- `src/App.tsx` / `src/features/OverviewView.tsx`：提取原来源/耗时展示标记，经可选 `resultMeta` 组合到摘要区域；请求、回调和 KPI 读取保持原样。
- `src/features/WorkspaceView.tsx`：模型标题与单一状态标识合并为一行，保留整卡多选及键盘语义。
- `src/features/ComparisonView.tsx` / `ReportView.tsx`：合并重复标题，报告保留原主导入入口，减少重复空态操作。
- `src/features/comparison.css` / `details.css`：表格行高约 58–60px，正文 14px；回答 15px、证据 14px，减少嵌套框；报告空状态 232px。`TraceView.tsx` 无净变更。
- `src/components/Charts.tsx`：标签 12px、图例和 tooltip 13px、弱化网格线；模型颜色映射、顺序、数据、格式化及 500/320px 高度保持不变。
- `browser-tests/workspace.spec.ts`：保留全部旧断言，补充唯一主标题、字号、缺失 KPI、键盘焦点和减少动态效果检查，以及空态/运行中/FAIL 截图。
- `README.md` / 本记录：补充视觉规则、验收及本阶段回滚范围。

共修改 12 个既有前端文件，没有新增业务文件或依赖。

### 实际验收

| 检查 | 结果 |
|---|---|
| `npm test` | 72 passed，4 个测试文件 |
| `npm run build`（含 TypeScript） | 通过，exit code 0 |
| `npm run test:browser` | 10 passed，exit code 0 |
| 截图滚动复位后的定向 mock 用例 | 1 passed |
| 实施前 59 个受保护文件 SHA-256 对照 | 0 项变化 |

已检查五个视图的 1920×1080、1280px、390px 截图：无页面横向溢出；1920 首屏完整显示门禁、KPI 与 500px 主图；表格自身滚动及不透明固定模型列保留。窄屏阶段名称不再断字，模型卡状态、参数与 CTA 可读。另检查空状态、运行中、FAIL 与缺值截图。

浏览器接口全部为 mock，继续覆盖零阈值透传、门禁拦截仍完成、重复提交限制、配置检查、模型键盘多选/参数重置、报告脚本与外部资源隔离。没有弱化原单元、合同或安全断言。截图保存在已忽略的 `test-results/`，不将其中服务连接/真实结果标识视为后端可用性证明。

`main.py`、backend、原 Python 测试及依赖、根 README、前端 API/types/state/config/format/fixtures 和 npm 依赖声明/锁文件均通过实施前文件摘要核对。原 Python 227 项基线本轮未重跑。没有读取 `.env` / Key，没有调用真实模型，没有暂存、提交或 push。

构建保留原有非阻断体积提示：生产 JS 约 837 kB（gzip 约 265 kB）；本阶段未扩大为分包优化。离线浏览器验收在 Windows 沙箱中退出曾挂起，随后在已授权执行环境中完整通过。

本阶段仅恢复上述 12 个文件的本轮补丁即可回滚，保留原 Phase 5 前端及用户已有修改；不删除整个前端目录。

## 摘要模型切换修复 · 2026-10-02

### 原因与修改范围

总览原来始终通过 `summaryModel(result)` 读取 `result.summary` 中第一个忠实度有效的模型；演示数据的该模型为 DeepSeek。名称没有硬编码，但其他模型的摘要 KPI 没有选择入口。

本轮仅修改以下六个前端文件：

- `src/features/OverviewView.tsx`：增加局部摘要模型选择，默认规则和返回顺序不变；四项 KPI 及摘要详情直接读取选中行。收到新结果恢复默认模型。失败模型仍可选，缺失项显示“暂无数据”，真实零值保留。
- `src/styles.css`：沿用现有控件样式，补充摘要选择器与缺失值文字的局部样式。
- `src/test/App.test.tsx`：增加三模型同步、mock 实际评测结果、失败模型、缺失/非有限数值、零值、新结果选择重置和无模型 ID 行的回归。
- `browser-tests/workspace.spec.ts`：增加桌面/窄屏三模型切换、名称与四项 KPI 同步、无新增请求及无横向溢出检查。
- `README.md` / 本记录：同步当前摘要选择与缺失值语义、实际验证结果。

门禁仍使用 `result.gate_passed`，能力图仍使用全部 `summary_df`；切换不调用 API，不过滤或重新计算图表数据。

### 实际验证

| 检查 | 结果 |
|---|---|
| `npm test` | 82 passed，4 个测试文件，exit code 0 |
| `npm run typecheck` | 通过，exit code 0 |
| `npm run build` | 通过，exit code 0 |
| `npm run test:browser` | 12 passed，exit code 0 |
| 实施前 60 个受保护文件 SHA-256 对照 | 0 项变化 |

已查看 `test-results/summary-model-1920.png` 与 `summary-model-390.png`：Qwen 名称与四项 KPI 对应，门禁保持原值，桌面首屏图表完整，窄屏无页面横向溢出。离线浏览器用例逐一验证 DeepSeek、Qwen2.5-7B、GLM-5.2-Fast，原 1280px、表格固定列、配置和报告隔离用例继续通过。

没有修改 backend、FastAPI、API/types、main.py、Gradio/CLI、全局状态、格式化规则、指标计算、依赖或演示快照；没有读取 `.env` / Key，没有调用真实模型，没有暂存、提交或 push。Python 测试本轮未重跑。构建仍有原有非阻断的大包体积提示。

回滚仅恢复上述六个文件的本轮补丁，保留 Phase 5-B 及其他已有修改。

## Phase 5-B · Gold Design System · 2026-10-02

按最终执行合同升级视觉基础，保持 Benchmark / Research Lab / Reliability Platform 定位。此前方案阶段已完成产品研究与当前页面审查；本轮不扩展页面、功能或架构。

### 八个修改文件

- `src/styles.css`：集中定义 Gold 品牌、Cyan 信息、Emerald 成功、Amber 警告、Red 错误及独立 Demo Token；建立 Page / Workspace / Surface / Elevated 深度层级，统一字体、按钮、导航、KPI 和选择状态。模型卡保持中性底色，参数值与 KPI 数字保持中性。
- `src/features/comparison.css`：移除旧色 fallback，主分析使用 Surface、辅助分析使用 Workspace；固定首列、表头和 Hover 背景不透明，能力标题只有小型 Gold 标记。
- `src/features/details.css`：Trace 选中项使用中性背景与小型 Gold 标识；证据、模型及路径信息使用 Cyan；事实错误与编造同为 Red，有依据为 Emerald。报告 iframe 白色画布、light 色系及隔离方式保持原样。
- `src/components/Charts.tsx`：局部读取七个图表环境 Token，适配背景、网格、坐标和 Tooltip；没有迁移 `MODEL_COLORS`，两处槽位分配代码保持不变。
- `src/test/charts.test.tsx`：保留原用例与断言，新增 7 项主题读取、六色顺序、Legend 映射、槽位循环及无 CSS 默认样式检查。
- `browser-tests/workspace.spec.ts`：原 12 项用例原样保留，追加 3 项颜色职责、字号/Surface、Disabled 和 Trace 状态回归；评测请求均为 mock。
- `README.md`：将初版视觉规则标记为历史，补充当前设计规则、模型颜色冻结与实际验收结果。
- 本记录：追加修改范围、保护核对、验收及回滚依据。

### 实际验收

| 检查 | 结果 |
|---|---|
| `npm test` | 89 passed，4 个测试文件，exit code 0 |
| `npm run typecheck` | 通过，exit code 0 |
| `npm run build` | 通过，exit code 0 |
| `npm run test:browser` | 15 passed，exit code 0 |
| 原 82 项单元/组件与 12 项浏览器断言 | 保留并全部通过 |

已查看五个视图在 1920×1080、1280px 与 390px 的截图，以及空状态、运行中、FAIL/缺值与配置检查反馈。中性背景占主导，Gold 集中于操作/选中/来源，Warning 单独为 Amber，模型识别色不变。1920 首屏完整显示门禁、KPI 与 500px 能力图；三个宽度均无页面横向溢出，表格仅自身滚动。

离线回归继续验证摘要模型切换、缺值与真实零值、一次请求、键盘多选、阈值重置、可见焦点、减少动态效果、禁用 Hover 和报告脚本/外部资源隔离。截图在已忽略的 `test-results/`，mock 的服务状态和结果标签不代表真实模型可用性。

实施前保存八个文件副本及 82 个相关/受保护文件的 SHA-256，回滚副本位于 `C:\Users\THUNDER\AppData\Local\Temp\rag-gold-5e0f9f7848974d4ebcac13a9d702d1c4`。最终核对只允许上述八个文件变化，其他 74 个文件保持原样；未新增源码文件。

`App.tsx`、五个视图组件、API/types/state/config/format、演示快照、backend、FastAPI、main.py、Gradio/CLI、原 Python 测试、根 README、前后端依赖声明及锁文件均未修改。没有读取 `.env` / Key，没有调用真实模型，没有暂存、提交或 push。原 Python 基线本轮未重跑。

构建仍有原有非阻断的大包提示：生产 JS 838.23 kB（gzip 265.76 kB）。本轮未扩展到分包或依赖优化。现有工作区修改及 untracked 文件保留，暂存区保持为空。

回滚仅恢复本轮八个文件的副本，不使用清理、重置或删除整个目录的操作。
