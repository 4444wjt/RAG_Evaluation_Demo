# 历史重写说明（公开前清理）

本仓库在公开发布前，对 Git 历史做过一次重写，用于**移除不面向公众的非公开材料**。本文说明重写的范围、影响与哈希对应关系。

## 一、背景与范围

- **目的**：把不宜公开的材料从全部历史中清除，使仓库可安全转为公开。
- **内容范围**：仅移除上述非公开材料；**未改动任何保留提交的文件树（tree）**，即源码内容保持不变。
- **结果**：提交数由 18 变为 **15**；**3 个仅涉及非公开材料的提交已移除**。
- 本次重写同时统一了历史中的提交作者标识（现统一为当前维护者身份）。

## 二、证据归档中的 `codeCommit`

`docs/competition/evidence/` 下各归档的 `manifest.json` 里，`codeCommit` 字段记录的是**重写前**提交的哈希，因此与当前仓库的哈希**不再对应**：

| 归档中的 `codeCommit`（重写前） | 当前仓库对应提交（重写后） | 文件树 tree |
|---|---|---|
| `ce97b4a` | `a75057e` | `dceb4de9f5514eb851654ddbf643d3481a412ad8` |

两者 **tree 完全相同**，说明该次运行所对应的源码内容未发生任何变化，仅是提交哈希随历史重写而改变。

> 结论：证据归档的**内容**与当前仓库**一致**；归档中的 `codeCommit` 为重写前哈希，属预期现象，不影响归档内容与证据有效性。

## 三、保留提交的哈希映射（重写前 → 重写后）

| 重写前 | 重写后 | 提交说明 |
|---|---|---|
| 8e6c185 | 1b1f1de | docs: 同步页面 title/description 为 AI 测试交付平台 |
| 73dd6a3 | fe7d64a | feat: TraceView 方向徽章与负向拒答语义纠正 |
| 9fbf628 | 970b489 | docs: 叙事定位升级为 AI 测试交付平台 |
| 78baa69 | cd96679 | docs: 作品介绍文档导出正式 PDF（内容与 markdown 原文一致） |
| ca6a6a2 | e3defe9 | chore: 比赛收口——逐句溯源展示、空 Trace 原因区分与正式 25 题证据归档 |
| ce97b4a | a75057e | Phase 5-C completed frontend baseline |
| 177557e | a6c3a0e | fix: normalize csv empty values in testset loading |
| a9c2a49 | ca24809 | ci: strengthen phase 4-a acceptance baseline |
| 872d959 | 72d4eeb | chore: archive diagnostics and freeze phase 3-b-10 |
| 85b1949 | 12a464c | refactor: freeze extracted evaluation backend baseline |
| d5e54b0 | 648524d | 规范化：去除运行时产物和 IDE 配置 |
| 1f9c065 | 8e4c408 | 本地双模型跑通，同步最新代码 |
| d8cf40a | ff7d100 | add README.md. |
| 9f3ccce | 1f7de90 | 上传核心代码 |
| 6950304 | 0236d37 | Initial commit |

> 说明：`872d959`（重写后 `72d4eeb`）为标签 `phase-3-b-10-freeze-20261001` 指向的提交；该标签已随重写指向新的哈希。

## 四、其他

- 除上表哈希变化外，其余保留提交的提交说明与文件内容保持不变。
- 被移除的提交仅涉及非公开材料，其内容不对外公开。
