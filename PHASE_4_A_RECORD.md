# Phase 4-A 持续验收记录

日期：2026-10-01

冻结基线：`872d959fb059fb55e9a1a2514ffe688624fd837a`（`872d959`）

冻结标签：`phase-3-b-10-freeze-20261001`

## 本轮修改

- `.github/workflows/ci.yml`：将编译检查扩展为 `python -m compileall -q main.py backend tests`。
- `README.md`：同步本地编译范围，保留 Gitee 不自动触发 GitHub Actions 的说明。
- `PHASE_4_A_RECORD.md`：记录本轮范围、实际验证结果与回滚边界。

依赖安装步骤及唯一测试门槛 `python -m pytest -q tests` 保持原样；未新增脚本、测试或依赖。未修改 main.py、backend、测试断言、xfail、skip、requirements、指标或模型流程。

## 本地验证

使用项目现有 Python 3.11 虚拟环境，在项目根目录执行：

```powershell
.\venv\Scripts\python.exe -m compileall -q main.py backend tests
.\venv\Scripts\python.exe -m pytest -q tests
```

- 编译：退出码 `0`，无错误输出；覆盖 main.py、backend/、tests/，不遍历 venv/ 或本地运行产物。
- pytest：退出码 `0`，`176 passed, 1 xfailed in 27.68s`，与冻结基线一致。
- 原 CSV 空值问题继续保留 xfail，本轮未修复。

上述结果仅代表本地验收。未触发或验证远端 GitHub Actions；仓库托管于 Gitee，本轮未新增 Gitee 流水线。未执行真实模型评测。

## Git 与回滚

本轮不暂存、不提交、不推送，不移动冻结标签。执行前已有的未跟踪文件保持原样。

回滚仅撤销 CI 和 README 的本轮差异，并在确认后移除本轮新增记录；不处理其他文件，不使用 `git reset --hard` 或 `git clean`。
