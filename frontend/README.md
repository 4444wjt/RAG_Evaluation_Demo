# RAG Reliability Lab · React 前端

Phase 5 独立前端：React + TypeScript + Vite + Apache ECharts。保留现有 Gradio、CLI 和 FastAPI 合同，不修改评测引擎。

## 本地启动

建议使用 Node.js 24 与项目已有 Python 虚拟环境。在两个 PowerShell 终端分别运行：

```powershell
cd D:\RAG_Evaluation_Demo
.\venv\Scripts\python.exe -m uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

```powershell
cd D:\RAG_Evaluation_Demo\frontend
npm ci
npm run dev
```

打开 http://127.0.0.1:5173/ 。后端未启动时，可以点击“加载演示”离线查看完整结果，不触发模型调用。

`/api/health` 和 `/api/evaluate` 经 Vite 代理移除 `/api` 前缀，分别调用现有 `/health` 与 `/evaluate`。生产构建的同源代理需由后续部署配置提供，本阶段不作为云部署入口。

## 页面与数据语义

- 总览：后端门禁结论、可切换摘要模型的四项风险 KPI，以及五项 Ragas 能力图；默认选择首个有效模型。
- 评测工作区：Dataset → Models → Evaluation → Run；文档与测试集使用项目相对路径，不提供文档上传。空文档路径沿用 `demo.txt`。
- 多模型对比：原始指标、风险比例及秒数分开展示，模型首列固定，返回模型顺序不变。
- 幻觉分析：逐题回答、原子陈述、原判定与检索证据。空 trace 不代表没有幻觉。
- 报告：手动导入已有 `.html` 文件，隔离预览；文件不上传。报告位置只支持复制本地路径，不是下载链接。导入文件与当前结果的对应关系需由操作者确认。

所有 KPI 直接取现有字段，不推导总数、重新计算门禁或新增排名。摘要 KPI 和摘要详情缺失显示“暂无数据”，其他指标展示继续使用 `—`；零值保留。切换摘要或详情模型不改变门禁。

未手动覆盖阈值时传 `null`，交由后端解析默认值；手动输入 `0` 原样传递。手动阈值预设为 `0.8`，重置按钮恢复该手动预设，不代表后端当前默认值。

公开模型名称映射在 `src/config.ts`。如果本地后端覆盖了模型名称，需要同步修改此处的公开 ID；前端不包含任何 API Key，也不读取项目 `.env`。静态场景选项同样以本地实际文件为准，最终路径/模型校验由现有 API 完成。

## 运行状态

每次提交只发送一次同步评测请求，没有任务轮询、阶段进度或取消接口。运行期间禁用重复提交；旧结果明确标为“上次结果”。HTTP 200 且门禁拦截仍视为评测完成。

连接中断时结果未知，服务端可能继续执行；不自动重试。再次手动提交会发起新评测。健康检查仅证明服务可连接，不验证模型、凭据或 Embedding。

失败提示中的“检查配置”会进入评测工作区、校验当前本地参数，并调用一次已有 `GET /health` 检查服务连接。Run 区显示检查中、成功或失败反馈并自动聚焦；检查本身不提交评测，不验证文件是否存在、模型或凭据是否可用。修改配置后清除旧检查反馈，避免将旧状态当作新配置的验证结果。

清空 TruthfulQA 样本数量后切换到自动生成或自定义测试集，不再把无效缓存数量发送为 `null`；非基准模式使用原默认数量 20。真正的基准模式仍要求正整数。

API 固定单 worker、不使用 reload。API、Gradio 和 CLI 共用产物与向量库，真实评测应交替运行，不跨进程并发。

## 离线演示

`src/fixtures/demo.json` 来源于现有同次运行产物 `20260930-1958`，保留三模型、18 条用例、原始指标和 trace。详细来源、SHA-256 与缺失说明见 [fixtures/README.md](src/fixtures/README.md)。

原运行未保存 `gate_message`，快照用空字符串表示，UI 提示“原始门禁说明未记录”；不编造原因。演示图表/报告路径指向共享当前文件，不能当作快照下载地址。

## 离线验收

```powershell
cd D:\RAG_Evaluation_Demo\frontend
npm run typecheck
npm test
npm run build
npm run test:browser
```

前端测试中的请求均为 mock，不调用真实模型。浏览器验收复用现有 Chrome / Edge，覆盖 1920×1080、1280px 与 390px 窄屏；报告只在本地导入预览。截图与结果写入已忽略的 `test-results/`。若浏览器安装在其他位置，可设置公开工具变量 `PHASE5_BROWSER_PATH` 为浏览器可执行文件路径。

原 Python 验收入口保持不变：

```powershell
cd D:\RAG_Evaluation_Demo
.\venv\Scripts\python.exe -m compileall -q main.py backend tests
.\venv\Scripts\python.exe -m pytest -q tests
```

## 范围与回滚

当前没有历史查询、真实趋势、文件上传、Run ID、下载或真实阶段状态接口。前端不显示未接线模块；它们留待后续接口阶段。

所有本轮内容均在 `frontend/`。停止前端服务并撤销该目录即可回退，现有后端、Gradio、CLI 和 API 无需切换。

## Phase 5-B 初版视觉精修（历史）

沿用五个视图和既有交互，以石墨黑 Surface、青蓝强调色、16px 主容器圆角和 8px 控件圆角统一展示。门禁文字为 48px，桌面辅助 KPI 数字为 38px；标题为桌面 30px / 窄屏 26px，关键说明至少 12px。主图继续保持 500px，辅助图保持 320px；只调整图表文字与网格线，不改变数据或模型颜色映射。

总览合并摘要模型与来源信息；Workspace 压缩模型卡重复状态；对比和报告合并重复标题；逐句证据使用分隔线与引用线；报告空状态缩至 232px。模型仍支持多选，缺失仍显示 `—`，HTML 仍在原隔离 iframe 中预览。

按钮、导航和选择状态采用轻量过渡，支持可见键盘焦点及 `prefers-reduced-motion`。没有新增动画库、API、指标或业务能力。

本轮实际验收：72 个单元/组件用例、10 个浏览器用例通过，TypeScript 与构建通过。浏览器截图在 `test-results/`，覆盖五个视图的 1920 / 1280 / 390px、空状态、运行中、门禁拦截和缺值；截图中的服务连接及真实结果标签使用 mock，不作为模型或后端可用性证明。

本阶段回滚只恢复本轮展示样式、标记、浏览器验收和文档补丁，保留已经完成的 Phase 5 前端，不删除整个 `frontend/`。

## 摘要模型切换

总览的“当前摘要模型”可直接选择本次结果中的 DeepSeek、Qwen 或 GLM。四项风险 KPI 和摘要详情读取该模型已有 summary 字段，切换不请求 API，能力图继续展示全部模型，质量门禁继续使用后端原判定。

默认仍选择第一个忠实度有效的模型；收到新结果时恢复该默认选择。失败模型仍可选，其缺失项显示“暂无数据”，真实 `0` 保留。本轮只使用局部展示状态，没有更改 API、指标、演示快照或全局状态管理。

实际验收：82 个单元/组件用例、12 个离线浏览器用例通过，TypeScript 检查和构建通过。已检查 1920px 与 390px 的模型切换截图；三模型名称与 KPI 同步更新，切换期间没有新增 API 请求。

## Phase 5-B · Gold Design System（当前）

定位为 Benchmark / Research Lab / Reliability Platform，五个视图和交互保持不变。颜色统一在 `src/styles.css` 中定义：

| 职责 | Token / 色值 | 使用范围 |
|---|---|---|
| 品牌 | `--brand: #F2C879`，`--accent` 为兼容别名 | 当前导航、核心按钮、少量选中标识、能力分析的小型标记 |
| 信息 | `--info: #59C3D1` | 服务连接、运行提示、模型信息、证据和路径 |
| 成功 | `--success: #34D399` | PASS、有依据和现有成功反馈 |
| 警告 | `--warning: #F59E0B` | 上次结果、未知状态及原有警示 |
| 错误 | `--danger: #F87171` | FAIL、事实错误、无依据编造和错误反馈 |
| 来源 | `--demo: #F2C879` | Demo / Snapshot；独立 Token，不与 Warning 合并 |

页面 / Workspace / Surface / Elevated 分别使用 `#080B10` / `#10161F` / `#151B24` / `#1B2430`，普通边界为 `rgba(255,255,255,0.08)`。正文与说明使用 `#F5F7FA` / `#A7B0BE`；Muted `#707887` 不用于说明、表头或证据正文。

中性 Surface 为主体，Gold 只承担小范围操作焦点，不表达风险、评分高低或模型优劣。模型卡与 Trace 选中项保持中性背景；普通标题、KPI 数字和参数值不使用 Gold。Primary 使用 Gold 与深色文字，Secondary 透明描边，Ghost 保持低强调。保留键盘焦点、Disabled、160ms 反馈及减少动态效果支持。

页面标题为桌面 32px / 窄屏 30px，区域标题 20px / 窄屏 18px，正文 15px，表格 14px，辅助说明 12–14px。门禁 48px、桌面 KPI 38px、主图 500px、辅助图 320px 保持不变；保留现有字体栈，不下载字体。

图表环境接入 `--chart-*` Token，只适配背景、坐标、网格和 Tooltip。原 `MODEL_COLORS` 及分配代码保留，六色与顺序仍是 `#59C3D1`、`#9297ED`、`#CBA575`、`#8FB8D1`、`#BD98B4`、`#B0C391`；第三模型色不引用品牌 Gold。不新增雷达图、排名或指标判断。

实际验收：`npm test` **89 passed**（原 82 项全部保留，新增 7 项主题/模型颜色检查）；`npm run test:browser` **15 passed**（原 12 项全部保留，新增 3 项颜色职责/状态检查）；TypeScript 与构建通过。已查看五个视图在 1920×1080、1280px 和 390px 的截图，以及空、运行、FAIL 和缺值状态。1920 首屏完整显示门禁、KPI 和能力图，页面无横向溢出，表格自身滚动及不透明固定首列保留。全部接口使用 mock，报告隔离用例继续通过。

仅修改本阶段列出的八个前端文件。回滚恢复本轮逐文件副本，不删除前端目录、不处理其他工作区变化。实际修改范围和验证详见 `PHASE_5_RECORD.md`。
