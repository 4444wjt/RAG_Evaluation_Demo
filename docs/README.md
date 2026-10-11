# 项目文档

- [技术架构](architecture/README.md)：前端、API、评测流水线、模型适配和存储边界。
- [比赛演示](demo/README.md)：无密钥演示、推荐展示顺序、结果解释边界。
- [人工核验存档格式](review-archive-format.md)：人工意见与机器判定并列保存的 JSON Schema（v1）及其校验范围。
- [门禁回放](gate-replay.md)：对已归档的运行离线重新执行门禁裁决，可接入 CI。
- [中文 RAG 评测踩坑记录](zh-rag-evaluation-notes.md)：Ragas 中文陈述切分、指标波动与空 Trace 的实测观察。
- [截图说明](screenshots/README.md)：截图尺寸、来源及静态预览说明。
- [开源发布检查](release-checklist.md)：当前整理结果和等待确认的提交、推送步骤。
- [比赛作品介绍](competition/project-introduction.md)、[三分钟演示脚本](competition/demo-script.md)、[评委常见问题](competition/reviewer-questions.md)。
- [复现与独立API归档](competition/reproducibility.md)、[本轮验收](competition/validation.md)：区分无密钥展示、实际模型运行与待确认事项。
- [5题真实预跑证据](competition/evidence/20261005T101847Z-pilot-c72cb679/README.md)：旧版本草稿预跑的来源、消耗、结果和限制；不代表中文兼容版本已真实运行。
- [25题正式运行证据](competition/evidence/20261005T144504Z-full-39511424/README.md)：单一金融材料固定场景的真实单模型运行、AI辅助离线分析、报告与脱敏哈希；不代表总体准确率或独立裁判可靠性。

README 引用的正式图片统一放在 `images/`。临时截图、测试缓存、真实凭据和本地模型不入库。
