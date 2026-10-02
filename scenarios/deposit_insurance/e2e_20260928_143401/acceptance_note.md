# 存款保险场景端到端评测 — 验收结论

- 运行版本：20260928-1434（2026-09-28 14:34 ~ 14:41）
- 文档：deposit_insurance.txt（12 块，chunk_size=200）

## 本次改动核心

负样本审查环节由布尔 `keep` 改为三态 `answerable`（`true`=可答拒收 / `false`=确实答不出该虚构细节保留 / `null`=无法确认拒收），并把「仅纠正错误前提即可充分回答则拒收」提为两分支公共规则。代码判定保持严格 `review.get("answerable") is not False`，只放行准确语义的 `false`。

## 关键结论：动态负样本修复成功

- **动态负样本 3 条生成、本地兜底 0 条**（首次在真实监管文档上稳定产出动态负样本）。
- 三条题型：虚构债权登记批次（missing_detail）、虚构白皮书背书（entity_mix）、80万限额篡改（numeric_condition）。

## 三模型结果

| 模型 | 忠实度 | 上下文精度 | 答案相关性 | 事实一致性 | 幻觉率 | score |
|---|---|---|---|---|---|---|
| deepseek-chat | 0.75 | 0.3472 | 0.4733 | 0.6182 | 0.0 | 0.8472 |
| Pro/Qwen/Qwen2.5-7B-Instruct | 0.5333 | 0.4722 | 0.3725 | 0.3484 | 0.3333 | 0.8055 |
| glm-5.2-fast-preview | 0.75 | 0.5139 | 0.4435 | 0.5697 | 0.25 | 0.7222 |

负样本拒答表现：DeepSeek 三题全拒（neg_faithfulness 0.0）；Qwen 出现幻觉（neg_faithfulness 0.3333）；GLM 一条负样本评测缺失（neg_faithfulness 为 null、neg_context_precision 0.3333）。

## 门禁

- **未通过**：正向上下文精度 0.6944 < 阈值 0.8（正向忠实度 1.0 达标）。门禁在发布前正常拦截。

## 残留问题（记入待办）

1. 第 2 条负样本仍为「是否参考『2025年度华东地区存款结构白皮书』」的是非题，与生成提示词「不生成是非题」的约束不一致。属生成侧约束未完全生效，非本轮审查修复范围。

## 费用

- 调用 219 次，input 375389 token，output 19463 token；
- 估算费用 **3.41 元**（依据预留单价：DeepSeek 9/27、Qwen 1/1、GLM 16/56，元/M token）；平台实扣未提供（actual_billed_cost_cny = null）；无漏记调用（missing_usage_calls = 0）。