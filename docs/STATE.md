# 当前状态（开工前先读）

> 本文件只记「现在做到哪」，不记规则——规则见 [AGENTS.md](../AGENTS.md)。
> 每次有实质进展时同步更新，并改掉下方日期。
> **最后更新：2026-10-26**

---

## 分支

| 分支 | 说明 |
|---|---|
| `next` | 开发分支，日常工作都在这里 |
| `master` | **冻结中**，评委可见的就是它；进 master 须单独授权 |

**2026-10-26 发布核验**：GitHub `master` 已包含下文所列 CI 修复及未见法规包。下方历史分叉说明不作为当前 refs 的依据；按现查命令核对。后续批次仍需作者明确授权。

`next` **尚未合并**到 `master`。HEAD、领先提交数与推送状态不写死在这里，开工时现查：

```bash
git log -1 --oneline next
git log -1 --oneline master
git rev-list --count master..next
git status -sb
```

**两条分支历史已分叉**：`master` 上有 3 个提交不在 `next` 的历史里（10-15/16 的 README 横幅、README 首屏与截图、首页标题遮罩）。`next` 里各有一个内容完全相同的对应提交，内容没丢，但合并到 `master` **不能快进**，要产生一个合并提交。合并前用下面两条命令现查：前者输出 `-` 表示该提交在 `next` 里有同内容提交；后者只在内存里试合并，不动分支和工作区，合并结果的树应与 `next^{tree}` 相同。

```bash
git cherry -v next master
git merge-tree --write-tree master next
```

## 规模

- GitHub 已发布代码 `5cdfea1` 的真实 CI：1750 收集，1649 通过、101 本地材料跳过、0 失败/错误；frontend 324 项通过。与下方历史工作区规模分开。
- Python 测试：**52 个 `test_*.py` 文件 / 1140 个用例**（`venv\Scripts\python.exe -m pytest tests -q`）
- 前端测试：**280 个**
- 门禁阈值：`pos_faithfulness ≥ 0.8` **且** `pos_context_precision ≥ 0.8`

## 已完成的实验（一句话结论）

| 实验 | 结论 |
|---|---|
| 10-05 正式运行（25 题，DeepSeek 自评） | FAIL，0.7375 |
| 10-11 换独立裁判重判（固定回答） | 结论不变（FAIL） |
| 10-12 裁判证据敏感性（固定陈述、只换证据） | 两个裁判无一处标签不同 |
| 10-14 修复验证 | PASS，0.8607 |
| 10-14 留出题验证（24 道新题） | 错误前提题 9/14 → 14/14 |
| 10-14 换裁判重跑修复版（Qwen） | FAIL，0.7992。**回答也重新生成，25 题里 7 题不同——不能把翻转全部归因于裁判** |
| 10-14 外部示例应用实测 | 链路跑通，FAIL |
| 10-14 人工标注（V，52 条） | 7 条锚点 0 判反；S12 永久未决 |
| 10-21 第二轮双人标注（A / B） | 四分 κ = **0.639**（距 0.6 门槛仅一条余量） |
| 10-21 AnythingLLM v1.18.0 源码构建实例 | FAIL，**0.765**；25 题中 10 题 Trace 为空（裁判限流） |
| 10-21 法条注入三方对比 | 条号类 16 条：规则 14、DeepSeek 14、Qwen 4；规则 **0 误报** |
| 10-21 款项定位双提示运行 | 款级 31 条：规则 30、两个裁判常规提示各 **1**；DeepSeek 加核对提示 1 → 22 但误报 2 → 7；**项级 3 条规则全漏** |
| 10-21 双管线矛盾调查 | 大部分差距是定义差异；真矛盾 3 对（Ragas 错 2、逐句核对错 1） |
| 10-21/22 银行问答 11 题（AnythingLLM，`deepseek-flash`） | 门禁 PASS（只看正向 5 题）；**负向 6 题全部给出节选外内容**（4 题带声明）；51/158 次裁判限流；估算 ¥3.33 |
| 10-22 款项定位漏检理由分类（离线） | 常规提示漏检 60 次：无款号 32、复述错款 20、**写出正确款号仍判支持 8** |
| 10-22 项级事后补查与回指（离线，同材料） | 项级仍 **1/4**（S26 长句覆盖 16/45）；回指 28 对、0 错、3 未定；formal 材料无新增标记 |

## 进行中

- **未提交的新工具**：`tools/competition/statute_equalinfo.py`（信息对等对比）、`tools/competition/statute_holdout.py`（第二部法规留出集）及两份测试。加上它们后，`pytest` 收集 1265 个用例；已提交的仍为 1140 个。
  - 信息对等对比：试跑已完成；全量运行要等作者确认授权细节。
  - 留出集：已暂停；规则还没在新材料上运行过，要等作者先完成盲确认。
- **两个来源不明的前端改动**（`frontend/src/features/FileImportControl.tsx`、`workspace-inputs.css`）：去留由作者决定，不要随其他改动一起提交。
- 10-22 的三份归档（`statute-check/20261022-pinpoint-reasons`、`statute-check/20261022-posthoc-locate`、`external-rag/20261022-bank-qa-anythingllm`）**已提交**到 `next`。
- **银行问答负向题口径**（作者 10-22 确认）：以「是否给出资料外内容」二元判定，不用「部分拒答」；6/6 为「是」。平台短语识别的拒答准确率 0.5 偏高（实际 0/6），改识别口径须单独提出

## 待办（按时间）

1. **重新生成作品介绍 PDF** —— 现有 PDF 落后于 `.md`；**不得覆盖已提交的那份**
2. `GOVERNANCE.md` 定稿

## 不能动的文件

`frontend/src/fixtures/demo.json`、`docs/competition/evidence/**`、
`docs/competition/judge-calibration/20261014-annotation-V/**`、
`docs/competition/project-introduction.pdf`

（完整清单与理由见 [AGENTS.md](../AGENTS.md) 第 2 节。）

## 模型命名（容易混，写在这里）

| 名字 | 用途 |
|---|---|
| `deepseek-chat` | 旧别名，config 默认值；**冒烟时失败过** |
| `deepseek-flash` | **实际请求名**，AnythingLLM 工作区里配的就是它 |
| `DeepSeek-V4.1-Flash` | 官方版本号，**只写进材料，不能填进 API** |


### CI 修复、未见法规包与治理发布（2026-10-26）

| 项目 | 已核实记录与边界 |
|---|---|
| CI 修复 | `396304036dddd18916c0f52bd7c500fec5fb7514`，9 个精确 CI/测试路径；来源登记修正已在前置 `07bf7bdfb8993d9eee71ccdbf7d46495061647fa`。GitHub CI [40278113103](https://github.com/4444wjt/RAG_Evaluation_Demo/actions/runs/40278113103) 的 test、gate-action、frontend 全部成功，精确 headSha 已核对。 |
| 未见法规包 | `5cdfea149a086bfc995981607a95b1f4b7c26d65` 在上述 CI 成功后发布；仅13份包文件及13条显式LF规则。CI [40278985598](https://github.com/4444wjt/RAG_Evaluation_Demo/actions/runs/40278985598) 三 job 全部成功，真实 pytest 为1649通过/101跳过。公开包与本地13/13逐字节一致，SHA清单11/11、标准库派生重计2906/2906通过。 |
| 跳过与门禁 | 101项本地材料依赖未被本次CI验证：onboarding97、labeled1、pinpoint1、blindspot既有2；执行数、跳过数、原因在CI日志公开。现存组部分缺失或绑定损坏继续失败；正式25题的业务门禁真实FAIL（0.7375<0.8）保留。 |
| 研究边界 | 一部新法规、作者构造与操作；原始schema检查、登记抽签、官方DOCX与作者计时未由公开包审计独立重验。公开派生复计不升级为独立确认性验证，不重复计分或宣称算法优势。 |
| 治理 | 新增GOVERNANCE.md：确认维护者增补、决策留痕、逐批授权与精确提交CI、无自动移交/归档、生效与修订五项流程。助手在作者本轮明确授权下起草，不声称作者逐字审阅或既往已履约；该独立文档提交的CI须另按精确SHA核对，不预写成功。 |

本批没有纳入 .lnk、scoring-rubric-v1.md、旧冻结证据或其它并行功能；未升级依赖。CI/文档维护不自动增加研究分，本轮不重复计算既有评分条目。
