# 鉴真全域 VeriScope AI · React 前端

React + TypeScript + Vite + Apache ECharts 前端，包含展示首页与评测工作台。保留现有 Gradio、CLI 和 FastAPI 合同，不修改评测引擎。

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

后端不在默认的 `127.0.0.1:8000` 时（例如已在 8000 跑了别的检出版本，又要在旁边预览当前分支），用 `VITE_API_TARGET` 把 dev 代理指过去，无需改代码：`VITE_API_TARGET=http://127.0.0.1:8010 npm run dev`。生产构建的同源代理不受此变量影响。

`/api/health`、`/api/inputs`、`/api/evaluate` 和 `/api/models` 经 Vite 代理移除 `/api` 前缀，分别调用后端 `/health`、`/inputs`、`/evaluate` 与 `/models`。「模型配置」页（`#/lab/models`）登记自有模型，只显示密钥是否已设置；只读站和纯静态站不请求该接口。生产构建的同源代理和只读拦截见 [deploy/](../deploy/README.md)。

## 页面与数据语义

- 总览：后端门禁结论、可切换摘要模型的四项风险 KPI，以及五项 Ragas 能力图；默认选择首个有效模型。
- 评测工作区：数据 → 模型 → 参数 → 运行。默认提供三份可直接选择的示例资料，也支持从电脑选择或拖入 TXT / PDF / DOCX / MD 文件；自定义测试集支持 JSON / CSV / TXT。单个文件最多 10 MiB。导入成功后显示文件名，并自动绑定后端路径，无需手填；已有项目文件的路径输入保留在高级选项中。
- 导入调用 `POST /api/inputs`，文件按原始字节保存到后端 `work/uploads/`，使用独立文件名避免覆盖。导入本身不调用模型，点击“开始评测”才会发起评测。公开只读站和纯静态站禁止导入；不会把选择文件的浏览器路径当作后端文件路径。
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

## 当前接口边界

当前没有历史查询、真实趋势、Run ID、服务端报告下载或真实阶段状态接口。前端不显示未接线模块；它们留待后续接口阶段。

## 摘要模型切换

总览的“当前摘要模型”可直接选择本次结果中的 DeepSeek、Qwen 或 GLM。四项风险 KPI 和摘要详情读取该模型已有 summary 字段，切换不请求 API，能力图继续展示全部模型，质量门禁继续使用后端原判定。

默认仍选择第一个忠实度有效的模型；收到新结果时恢复该默认选择。失败模型仍可选，其缺失项显示“暂无数据”，真实 `0` 保留。本轮只使用局部展示状态，没有更改 API、指标、演示快照或全局状态管理。
