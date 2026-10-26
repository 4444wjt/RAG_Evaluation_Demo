# CLAUDE.md

本仓库的**约束**与**当前状态**分在两个文件里。开工前先读，不要凭任务文件的描述推断项目现状。

@AGENTS.md

@docs/STATE.md

---

## 三句话摘要（细节以 imports 的内容为准）

1. **不能越过什么** → `AGENTS.md`：冻结文件只读、不改数字、禁止 `.gitattributes` 通配规则、
   禁止 `git add -A`、禁止 push、所有写入动作等授权。
2. **现在做到哪** → `docs/STATE.md`：当前分支与 HEAD、测试规模、已完成实验、本轮待办。
3. **两者冲突时**，以仓库实际字节（`git status` / `git log` / 文件内容）为准，并报告冲突。

## 最小验收

改完任何东西，至少跑：

```bash
venv/Scripts/python.exe -m pytest tests -q
```

1140 个用例全绿才算通过（2026-10-22；新增测试后以 `docs/STATE.md` 为准）。**不要为了让测试变绿去改测试或放宽门禁阈值。**

## 需要知道「最近改了什么」时

`docs/STATE.md` 只记摘要。要了解上一次具体做了什么：

```bash
ls -t work/competition/codex-tasks/ | head -3
```

**读最近 3 个任务文件**——它们就是执行记录（任务名带日期，内含当时的命令与结论）。
更早的历史用 `git log --oneline -15`。

## 长期记忆（basic-memory MCP）

已配置 MCP 服务器 **`basic-memory`**，记忆存在仓库根目录的 `.memory/`（已 gitignore，不进仓库）。

三个用法：

| 时机 | 调用 |
|---|---|
| **开工前** | `search-notes "关键词"` —— 先搜"否决""口径""坑" |
| **定了决策/口径** | `write-note --title ... --folder decisions` |
| **踩了坑** | `write-note --title ... --folder pitfalls` |

**必读**：`decisions/已否决的方向与理由` —— **在提议任何新研究方向之前先读它**，
里面记着已经被否决的方案和理由（裁判×管线、绝对vs相对门禁、裁判×重复大实验等）。

> 注意：语义检索已关闭（`BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=false`），只走全文检索。
> 不要重新开启——首次会下载 embedding 模型，会卡住。
