# 项目文档

仓库存放三类文档：上手接入、可信度判断、项目价值与证据。急着找东西查第一张表，想通读看下面的地图。

## 按你的问题找

| 你的问题 | 读这个 |
|---|---|
| 我怎么把它跑起来、接到自己的系统里 | [快速启动](../README.md#快速启动) · [落地与接入](DEPLOYMENT.md) |
| 这个结论我能信到什么程度 | [评测可信度与限制](evaluation-credibility.md) · [门禁可信度四问](competition/gate-credibility.md) |
| 归档里的 PASS / FAIL 怎么自己复算 | [门禁回放](gate-replay.md) · [零依赖脚本](../scripts/README.md) |
| 项目价值、证据与短板在哪 | [项目索引](competition/README.md) |

## 文档地图

**上手与接入**

- [快速启动](../README.md#快速启动)：三条命令看到演示，完整评测另配环境。
- [落地与接入](DEPLOYMENT.md)：三种落地形态、接入自有模型的步骤、私有化 / 内网部署的已核实与未实测事项。
- [技术架构](architecture/README.md)：前端、API、评测流水线、模型适配和存储边界。
- [单独配置裁判](integrations/independent-judge.md)：回答生成与指标／Trace 核对拆给不同模型，并记录请求名与实际响应名。
- [外部 RAG 端到端接入](integrations/external-rag.md)：只发题号与问题，由应用返回回答和实际生成资料。
- [引用版本检查（CLML）](integrations/citation-version.md)：本地上传、重查、导出与离线复算。
- [父上下文准备](integrations/parent-context.md)：为缺少条号标题的检索片段冻结「它属于哪一条」。

**判断结果可不可信**

- [评测可信度与限制](evaluation-credibility.md)：三层口径、人工核验范围与未验证事项。
- [门禁可信度四问](competition/gate-credibility.md)：能不能复算、换裁判会不会变、裁判是否依据证据、结论有多确定。
- [门禁回放](gate-replay.md)：对已归档结果离线重执行门禁裁决，可接入 CI 并与基线逐题对比。
- [零依赖脚本](../scripts/README.md)：不装评测库、不用密钥，能当场复算的那几个脚本。
- [独立裁判对照](judge-comparison.md)：只换裁判、不重新生成回答，量化同源裁判偏差。
- [人工核验存档格式](review-archive-format.md)：人工意见与机器判定并列保存的 JSON Schema（v1）及其校验范围。
- [中文 RAG 评测踩坑记录](zh-rag-evaluation-notes.md)：Ragas 中文陈述切分、指标波动与空 Trace 的实测观察。

**项目价值与证据**

- [项目索引](competition/README.md)：按技术创新、场景落地、开源治理、长期发展四块组织，每块给证据入口与短板。
- [演示指南](demo/README.md)：无密钥演示、推荐展示顺序、结果解释边界。
- [截图说明](screenshots/README.md)：截图尺寸、来源及静态预览说明。
- [开源发布检查](release-checklist.md)：当前整理结果和等待确认的提交、推送步骤。
- [许可变更说明](license-transition.md)：许可从 Commons Clause 转到 Apache-2.0 的范围与理由。
- [历史重写说明](competition/history-rewrite-note.md)：公开发布前那次 Git 历史重写的范围与哈希对应关系。

## 阅读顺序

| 你是谁 | 按这个顺序读 |
|---|---|
| 第一次接触 | [作品介绍](competition/project-introduction.md) → [同一次 FAIL 的三种看法](competition/value-comparison.md) → [门禁可信度四问](competition/gate-credibility.md) → [常见问题与回答口径](competition/reviewer-questions.md) |
| 开发者 | [快速启动](../README.md#快速启动) → [技术架构](architecture/README.md) → [落地与接入](DEPLOYMENT.md) → [零依赖脚本](../scripts/README.md) → [评测可信度与限制](evaluation-credibility.md) |
| 只想看一眼 | [项目索引](competition/README.md) 的四个小节，每节第一段读完就够 |

README 引用的正式图片统一放在 `images/`。临时截图、测试缓存、真实凭据和本地模型不入库。
