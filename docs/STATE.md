# 当前状态（开工前先读）

> 本文件只记「现在做到哪」，不记规则——规则见 [AGENTS.md](../AGENTS.md)。
> 每次有实质进展时同步更新，并改掉下方日期。
> **最后更新：2026-10-22**

---

## 分支

| 分支 | 说明 |
|---|---|
| `next` | 开发分支，日常工作都在这里 |
| `master` | **冻结中**，评委可见的就是它；进 master 须单独授权 |

`next` **尚未合并**到 `master`。HEAD、领先提交数与推送状态不写死在这里，开工时现查：

```bash
git log -1 --oneline next
git log -1 --oneline master
git rev-list --count master..next
git status -sb
```

## 规模

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

- **10-22 三份归档与文档更新**：已在工作区，**未提交**（`statute-check/20261022-pinpoint-reasons`、`statute-check/20261022-posthoc-locate`、`external-rag/20261022-bank-qa-anythingllm`，以及 statute-check README、作品介绍、README、`.gitattributes`）
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
