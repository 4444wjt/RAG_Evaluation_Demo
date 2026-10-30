# 工程约束 · 在本仓库工作时不能越过的边界

本文件规定在**本仓库内执行改动的维护者与自动化工具**不得越过的边界。

- **适用范围**：本仓库及其全部 worktree。更深目录下的约定只能**补充**本文件，不能放宽。
- **读者**：在本仓库执行写入的维护者与自动化工具。
- **与其它文档的分工**：[CONTRIBUTING.md](CONTRIBUTING.md) 回答「怎么贡献」（流程与验证命令）；[SECURITY.md](SECURITY.md) 回答「安全问题怎么报」；[docs/release-checklist.md](docs/release-checklist.md) 回答「发布前查什么」；[docs/STATE.md](docs/STATE.md) 回答「现在做到哪」；**本文件回答「不能越过什么」**。

---

## 0. 开工前先读当前状态

动手之前先读 **[docs/STATE.md](docs/STATE.md)**：当前分支与 HEAD、测试规模、已完成的实验、
本轮待办、以及容易混的命名。

- **本文件只回答「不能越过什么」，不回答「现在做到哪」。**
- STATE.md 是**摘要**，不是依据。与仓库实际字节冲突时，以第 1 节的事实优先级为准。
- 完成实质进展后，同步更新 STATE.md 的表格与「最后更新」日期——**它是给下一位接手的人看的，不是存档。**

---

## 1. 事实优先级：实时状态高于文档与记忆

| 优先级 | 来源 | 定位 |
|---|---|---|
| 1 | 仓库实际字节：`git status`、`git log`、文件内容 | 依据 |
| 2 | 可复算的证据：各 `SHA256SUMS.txt` | 依据 |
| 3 | 已归档的发布说明、交接文档、历史日志、记忆 | **仅作线索** |

- 引用一个文件之前先读它；引用一条结论之前先找到它的证据。
- **证据冲突时报告冲突并暂停**，不要挑一个"看起来合理"的继续推进。
- 不得把推测写成事实，不得把历史记录当作当前状态。

> 依据：`docs/competition/evidence/*/SHA256SUMS.txt`（可按 `sha256sum -c` 复算）；`frontend/src/fixtures/demo.json` 的 `provenance` 参与 `credibilitySnapshotSha256`。

## 2. 冻结文件：只读，任何理由都不得就地修改

以下内容一旦改动，会使已归档证据的完整性校验失败。**「顺手格式化」和「统一换行符」同样属于改动**：

- `frontend/src/fixtures/demo.json`
- `docs/competition/evidence/**` 及其哈希记录
- 人工核验包与历史运行结果

需要新增证据时**新建目录**，不要修改旧归档。

## 3. 换行符：不得引入全局规则

- `.gitattributes` 采用**逐文件显式路径规则**，由各 `SHA256SUMS.txt` 的记录反推得出。
- **禁止**添加 `* text=auto eol=...` 之类的通配规则。本仓库的哈希基准是**混合的**（部分文件按 CRLF 记录、部分按 LF 记录），任何全局规则都会一次性破坏其中一半。
- 新增参与完整性校验的文件时，必须在 `.gitattributes` 中补一条显式规则。

> 依据：仓库根目录 `.gitattributes`。当前不含任何**全局**通配规则（如 `* text=auto`）；仅有 5 条**目录级** `**` 规则（`judge-sensitivity/run-20261012/**`、`judge-sensitivity/analysis-20261012/**`、`judge-rerun/20261021-pilot/**`、`deploy/**`、`robustness/20261013-formal-run/**`），各自只约束本目录。Windows 检出与 Linux 检出必须得到相同字节。

## 4. 缺失值不填零，预览不冒充实时

- 缺失值**不填成零**，静态快照**不标注为实时结果**。
- 不得为了让页面"看起来完整"而补数据。

> 依据：`CONTRIBUTING.md`「设计与实现约定」。

## 5. 门禁口径不得为「变绿」而放宽

- 门禁阈值、指标公式、正负样本口径的改动必须**单独提出**，附设计、兼容性与验证材料，不与其他改动混在一起。
- **保留真实 FAIL。** 不得调阈值、改断言或跳过测试使 CI 通过。
- 已知真实结果：25 题正式运行正向忠实度 `0.7375`，低于 `0.8`，门禁结论为 `FAIL`。

> 依据：`backend/core/gate.py`（`check_quality_gate` 只读取 `pos_faithfulness` 与 `pos_context_precision`）；`docs/competition/evidence/20261005T144504Z-full-39511424/`；门禁性质测试见 `tests/test_gate_properties.py`。

## 6. CI 依赖约束

- `tests/requirements.txt` **必须保留** `-c ../requirements.txt`（这是**约束**，不是安装）。**绝不能改为 `-r`** —— 那会把 torch / transformers / chromadb / gradio 等约 180 个包拉进 CI。
- `pyarrow<26` 必须显式钉住：26.x 运行时要求 `numpy>=2`，与本项目钉住的 `numpy==1.26.4` 冲突，且 `pip check` 检测不到。

> 依据：`tests/requirements.txt`（两条均有行内注释说明原因）。

## 7. 凭据与只读部署

- 密钥只从本地 `.env` 或进程环境读取，**不得**出现在仓库、日志、报告、归档产物或前端。
- 误把凭据提交到公开位置后，**必须立即在服务商处吊销**——仅删除提交记录不能让已泄露的凭据失效。
- 公开演示站为只读部署：写入类请求由 nginx 与后端两层拦截（HTTP 423）。

> 依据：`SECURITY.md`；`deploy/nginx/default.conf.template`（`location = /api/evaluate` 返回 423）；`backend/api/app.py`（读取 `VERISCOPE_READ_ONLY`）。

## 8. 授权边界

**可直接执行，无需确认**

- 只读操作：读取文件、`git status` / `git log` / `git diff`、运行测试与 `compileall`。

**必须先报告精确命令与影响，等待明确授权**

- `commit`、`push`、打 tag、创建 Release
- 修改远端可见性、删除远端仓库或分支
- 任何会改动**正在对外服务**的实例的操作（重启、重建、改端口、改反向代理）
- 引入新依赖，或调整既有依赖版本

报告须包含：**精确命令、影响范围、恢复点与回滚方式**。

> 依据：`CONTRIBUTING.md`「提交改动」；`ROADMAP.md`。

## 9. 公开范围

- 内部工作文档、提示词、交接材料与阶段报告一律写入 `work/`（该目录已在 `.gitignore` 中忽略），不进入公开仓库。
- 图片仅从仓库既有的白名单目录进入版本控制。
- 提交前须完成一次敏感词与凭据扫描。扫描口径为维护者的内部清单，**不列于本文件**。

> 依据：`.gitignore`。

## 10. 改动前自查

- [ ] 有没有碰到第 2 节的冻结文件？
- [ ] 新增文件是否需要在 `.gitattributes` 补一条显式规则？
- [ ] 是否改动了门禁阈值、指标公式或测试断言？
- [ ] 是否引入新依赖或调整了依赖版本？
- [ ] 是否夹带了凭据、机器绝对路径或内部材料？
- [ ] 验证命令与结果是否如实记录，**含未覆盖的部分**？
