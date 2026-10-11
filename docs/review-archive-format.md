# 人工核验存档格式（v1）

机器判定和人工意见放在同一份文件里，并列保存、互不覆盖；文件再通过版本号和 SHA-256 绑定到一份冻结评测快照。这份说明把现有存档 [`credibility-review.json`](../frontend/src/fixtures/credibility-review.json) 的结构整理成公开的 JSON Schema，便于其他评测流程按同一格式归档人工核验结果。

- Schema：[`schemas/review-archive.v1.schema.json`](../schemas/review-archive.v1.schema.json)（JSON Schema Draft 2020-12）
- 字段的填写规则与状态含义：见 [评测结果可信度与限制](evaluation-credibility.md)
- 校验测试：[`tests/test_review_archive_schema.py`](../tests/test_review_archive_schema.py)。它检查冻结存档是否符合 schema，并确认 7 类自相矛盾的写法会被拒绝

## 结构

| 字段 | 含义 |
|---|---|
| `schemaVersion` | 固定为 `1` |
| `snapshotVersion` / `snapshotSha256` | 被核验快照的版本号，以及整份快照文件的 SHA-256 |
| `gateRuleReview` | 门禁规则本身是否经过人工检查 |
| `reproduction.snapshotReplay` / `reproduction.modelRerun` | 快照重放、模型复跑是否经过人工确认 |
| `records[]` | 逐条核验记录：定位（`modelId` + `rowIndex`）、关注点 `focus`、汇总状态 `status`、核验者、日期、回答级判断 `answerReview`、逐句判断 `statementReviews[]`，以及核验前整理的材料 `reviewDraft`（含机器原判定摘录 `machineVerdict`） |

机器原判定只出现在快照和 `reviewDraft.machineVerdict` 里；人工判断写在 `answerReview` 和 `statementReviews[].humanVerdict`。两者字段分开，人工字段不会改写机器字段。

## Schema 能拦住的写法

- 尚未检查的项目（`unverified` / `pending`）预先填了核验者、日期、结论或备注；
- 已核验的记录缺少核验者、日期，或日期格式不对；
- 标记为「一致」（`agree`），却同时写了抽取遗漏、拒答存疑，或某一句「无法确认」；
- 枚举值之外的状态或判定，SHA-256 格式错误，出现 schema 未定义的字段。

## Schema 管不到、由页面校验负责的部分

下面几项需要对照快照本身才能判断，单靠 JSON Schema 无法表达，所以由 [`EvaluationVerificationView.tsx`](../frontend/src/features/EvaluationVerificationView.tsx) 中的 `validateReviewBundle` 和哈希比对完成：

- `snapshotSha256` 是否等于当前快照文件的实际哈希；
- `modelId` / `rowIndex` 是否落在预选的 13 条范围内，且不重复；
- `statementReviews` 的条数与下标是否与机器 Trace 的原子陈述一一对应；
- 「一致」记录的逐句人工判定是否与机器原判定相同。

## 校验方法

```bash
python -c "import json, jsonschema; jsonschema.validate(json.load(open('frontend/src/fixtures/credibility-review.json', encoding='utf-8')), json.load(open('schemas/review-archive.v1.schema.json', encoding='utf-8')))"
```

## 边界

- 这是本项目自用格式的公开描述，不是行业标准。今后字段如有变化，会提升 `schemaVersion`，并保留 v1 的 schema。
- 存档只记录核验过程和结论，不据此计算准确率。现有 13 条记录仅覆盖冻结演示快照的定向样本。
