# Phase 3-B-10 Freeze Report

日期：2026-10-01。项目源码与完整离线测试均通过验收；最终冻结提交以本节冻结标签为准。

## 1. 本轮修改文件

- `.gitignore`
- `.dockerignore`
- `.env.example`
- `README.md`
- `CHANGELOG.md`
- `HANDOFF.md`

修改仅涉及忽略规则、文档和密钥模板占位项。`.env.example` 仅新增空的 DASHSCOPE_API_KEY；未读取或修改 `.env`，未打印 Key 值。

## 2. 新增文件与迁移基线入库

- `PHASE_3_B_10_FREEZE_REPORT.md`
- `archive/diagnostics/phase_3_b_10/_diag_env.py`
- `archive/diagnostics/phase_3_b_10/_diag_refusal.py`
- `archive/diagnostics/phase_3_b_10/_diag_refusal2.py`
- `archive/diagnostics/phase_3_b_10/_verify_refusal.py`
- `archive/diagnostics/phase_3_b_10/_verify_trend.py`
- `archive/diagnostics/phase_3_b_10/manifest.json`
- `archive/diagnostics/phase_3_b_10/README.md`

上一阶段已有的 backend 全部29个 Python 文件、tests 全部12个文件（含9个测试文件、conftest和配置）、必要配置与公开场景文档已纳入 Git。main.py、backend、tests、requirements.txt、Dockerfile、demo.txt 共45个受保护文件与整理前 SHA-256 一致；本轮没有重新实现迁移代码或升级依赖。

## 3. 取消跟踪与归档

以下16项已有删除经明确批准，仅取消 Git 跟踪；当前存在的6个本地产物仍保留，其余实体在本轮开始前已缺失：

- `backup/demo.txt`
- `backup/main.py`
- `backup/requirements.txt`
- `chroma_db/cc6465d2-207f-40cd-8f63-fc501c39b1c5/data_level0.bin`
- `chroma_db/cc6465d2-207f-40cd-8f63-fc501c39b1c5/header.bin`
- `chroma_db/cc6465d2-207f-40cd-8f63-fc501c39b1c5/length.bin`
- `chroma_db/cc6465d2-207f-40cd-8f63-fc501c39b1c5/link_lists.bin`
- `chroma_db/chroma.sqlite3`
- `multi_model_summary.csv`
- `ragas_report.csv`
- `ragas_report.html`
- `ragas_report_Pro_Qwen_Qwen2.5-7B-Instruct.csv`
- `ragas_report_deepseek-chat.csv`
- `ragas_report_glm-5.3-flash.csv`
- `ragas_report_qwen3.8-flash.csv`
- `result_chart.png`

- regenerate_report.py、requirements_new.txt 已从 HEAD 恢复保留，内容与原 Git 基线一致。
- 五个历史诊断脚本复制至 archive/diagnostics/phase_3_b_10/，SHA-256一致，根目录原件保留并忽略，不加入pytest。
- 本轮没有执行任何新的实体文件删除；没有清理未知文件。

## 4. Git 与回滚

- 整理前 HEAD：`d5e54b0081014cf043cec351fa9dfa0ae7ccc869`。
- 迁移基线提交：`85b19491221c3ed0fb4ebf06b5bec033af04ec1e`。
- 冻结标签：`phase-3-b-10-freeze-20261001`，指向最终工程整理提交。
- 不执行 git push；不使用 git add .、reset --hard 或 git clean。
- 冻结提交不包含 .env、venv、根目录模型缓存、向量库、浏览器资料或运行报告。
- 无关的 .trae、onetake、提示词文档和其余场景归档保持未跟踪，未覆盖或清理。
- 回滚材料位于仓库外：`C:\Users\THUNDER\Documents\Codex\RAG_Evaluation_Demo_freeze_backups\phase_3_b_10_20261001_5f53f7a7`，包括已验证Git bundle、工作区/索引补丁、原索引、源码快照与取消跟踪清单。
- 回滚先在独立目录重建并核对清单，不在现有工作目录覆盖本地产物或强制重置。

## 5. 验证结果

| 检查 | 结果 |
| --- | --- |
| 原工作目录 `compileall -q .` | 退出码1；仅 venv/Lib/site-packages/torch/testing/_internal/py312_intrinsics.py 含Python3.12专用语法，环境为Python3.11 |
| 全部项目源码编译（含保留和归档诊断脚本） | 通过 |
| 原工作目录完整离线pytest | 176 passed, 1 xfailed in 14.69s |
| Git候选树独立检出的 `compileall -q .` | 通过，退出码0 |
| Git候选树独立检出的完整离线pytest | 176 passed, 1 xfailed in 13.69s |
| 受保护代码/测试/依赖文件哈希 | 45个文件均未改变 |
| Git中的模型适配器 | 四个文件完整 |

全目录编译的第三方例外没有通过删除文件、修改依赖或升级Python处理。独立检出包含全部冻结源码，不含未跟踪venv；未减少pytest测试范围或修改断言，原CSV空值xfail保持不变。没有真实模型/API调用。

## 6. 当前架构状态

沿用Phase 3-B-9验收后的config / core / adapters / services / storage / reporting六层，29个Python文件、2410行。main.py中的_legacy_run_pipeline与run_pipeline兼容入口保留。模型顺序、Ragas、trace、score、门禁、接口与历史结构未修改。

## 7. Phase 4 准入

满足后端源码完整入库、可回滚、干净检出编译及完整离线测试通过的工程冻结条件，可以进入Phase 4规划。Phase 4功能与部署开发未执行。
