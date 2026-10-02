# Phase 4-C FastAPI 薄接口与回归验收记录

日期：2026-10-01

执行基线：`177557ef2018d23e30871afebda660ef4d7d3cfd`。

Phase 3-B-10 冻结提交 `872d959`、标签 `phase-3-b-10-freeze-20261001` 保持不变。

## 阶段状态

| 阶段 | 完成内容 | 当阶段离线 pytest |
| --- | --- | --- |
| 4-C-1 | 应用工厂、ASGI 入口、健康检查和直接依赖声明 | `182 passed in 13.36s` |
| 4-C-2 | 请求/响应合同、八字段 JSON 序列化 | `214 passed in 16.06s` |
| 4-C-3 | 同步评测接线、受控路径、互斥锁、安全错误响应 | `227 passed in 29.98s` |
| 4-C-4 | 本轮回归验收、依赖方向核对与文档更新 | `227 passed in 51.22s` |

Phase 4-C-1 至 4-C-4 均已完成，结论限于本地离线验收。

## Phase 4-C 累计文件范围

- 新增 `backend/api/__init__.py`、`app.py`、`schemas.py`、`serialization.py`、`service.py`。
- 新增并逐阶段扩展 `tests/test_api.py`，目前 48 项 API 测试；原 179 项离线测试保留。
- `requirements.txt` 补齐已有 FastAPI `0.141.1`、Uvicorn `0.53.0`、Pydantic `2.13.5` 的直接依赖声明；原版本不变。
- `tests/requirements.txt` 声明 FastAPI / httpx，沿用根 requirements 约束。
- `README.md` 和本记录说明 API 使用与实际验收结果。

## Phase 4-C-4 本轮修改范围

仅更新 `README.md` 的 API 使用章节和 `PHASE_4_C_RECORD.md`；README 原架构说明及其余章节保持原样。本轮未新增或修改代码、测试、依赖、CI、Docker 或入口。

未修改 main.py、Gradio、CLI、backend/core/pipeline.py、原指标、score、门禁、检索、模型调用流程或历史结构；不新增上传、任务轮询、数据库或队列。

## API 执行合同

请求 schema 校验 → 受控路径及所选模型校验 → 非阻塞进程内锁 → 原 `evaluate(request, resources=None, model_factory=None, progress_cb=None)` 恰好一次 → 八字段序列化及响应 schema 校验 → 释放锁并返回。

应用工厂支持注入假 evaluator；默认 evaluator 延迟导入原引擎，并使用 Agg 绘图后端。接口侧的模型校验按需延迟读取配置，真实引擎仍沿用其原有配置加载行为。启动和健康检查不加载配置或引擎，也不初始化模型。`/health` 只返回 `{"status":"ok"}`。

响应字段为 `summary`、`summary_df`、`gate_passed`、`gate_message`、`chart_path`、`html_path`、`model_dfs`、`total_elapsed_seconds`。DataFrame 转 records，NumPy 转原生类型；缺失及非有限数值转 null；保留零值、布尔值、原 trace 字符串及原对象，不重算指标。

门禁拦截仍为 200；请求错误为 422/403/404，忙碌为 409，运行/序列化/响应校验异常为固定文案的 500。请求校验不回显 raw input，异常不向客户端返回密钥、提供方原文或 traceback。

## 回归覆盖核对

现有 `tests/test_api.py` 已覆盖本轮全部必需项，无需补测试：

| 验收项 | 现有覆盖 |
| --- | --- |
| GET /health | 工厂与 ASGI 入口、启动隔离；运行中也能健康响应 |
| POST /evaluate 成功 | 八字段 JSON 响应，evaluator 调用一次 |
| 默认参数 | 延迟引擎入口 stub，默认 demo.txt、20、None 值及三个资源参数 |
| 参数透传 | 0 阈值、模型请求顺序/重复、版本字符串及实际路径 |
| gate false 返回 200 | 成功 HTTP 响应保留 false |
| evaluator 异常返回 500 | 固定安全消息，不泄漏原始异常 |
| 路径非法 403/404 | 路径越界、绝对路径、Windows 特殊路径、范围内缺失文件 |
| 并发请求 409 | 引擎执行、序列化阶段及同进程不同工厂实例 |
| 锁异常释放 | 引擎、序列化、响应校验失败后下一请求成功 |
| 数据转换与原对象 | 缺失值、0/False、字符串/数组，修改输出不影响输入 |
| benchmark 优先级 | 不检查未使用的测试集路径 |

## 依赖方向验证

只读 AST 检查覆盖 5 个 API 模块和 5 个 core 模块，包含函数内导入：

- API 无 `main.py` 导入。
- core 无 API 导入。
- `service.py` 仅在实际运行时单向依赖 `backend.config` 和 `backend.core.pipeline`。
- 未发现禁止导入；补充文本检查未发现 core 动态导入 API。

## 本轮实际验证

```powershell
.\venv\Scripts\python.exe -m py_compile main.py backend/api/__init__.py backend/api/app.py backend/api/schemas.py backend/api/serialization.py backend/api/service.py tests/test_api.py
.\venv\Scripts\python.exe -m pytest -q tests
```

- py_compile：退出码 `0`，无错误输出。
- 完整离线 pytest：退出码 `0`，`227 passed in 51.22s`，无失败、xfail 或 skip；包含原 179 项和 API 48 项。
- 验收前后 SHA-256 核对：50 个源码、测试及关键配置文件均未变；README 的 API 章节之外内容（含旧架构说明）未变。
- `git diff --check` 通过；暂存区为空，HEAD 仍为 `177557ef2018d23e30871afebda660ef4d7d3cfd`，冻结标签仍指向 `872d959fb059fb55e9a1a2514ffe688624fd837a`。

历史说明：C-1 首轮新增 TestClient 测试因 Windows asyncio 内部 socketpair 被断网保护拦截而失败；当阶段仅适配新增 API 测试，原 conftest、断网保护及原断言保持不变。

## 验收边界

HTTP 验收使用隔离子进程和进程内 ASGI TestClient，先创建标准库事件循环，再阻断网络、dotenv 访问及真实引擎/模型导入；真实引擎委托入口以模块 stub 验证。未启动实际 HTTP 监听服务、未调用真实模型、未验证全新环境安装或远程 CI。

符号链接边界测试模拟 `Path.resolve()` 的目标输出，未在 Windows 创建真实符号链接。API 默认仅本地单 worker；锁只保护同一进程，独立 Gradio / CLI 仍应交替执行真实评测。图表、报告和历史沿用共享产物路径，不是独立运行快照。

本轮不读取或打印 .env / Key、不暂存、不提交、不推送，不移动冻结标签；既有未跟踪文件保持原样。
