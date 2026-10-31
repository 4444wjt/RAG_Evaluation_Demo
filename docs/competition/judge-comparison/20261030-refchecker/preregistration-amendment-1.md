# 预登记修订 1（任何模型调用之前；状态：作者已批准，2026-10-30，原话「批准」）

登记时间：2026-10-30，T1 前置核查停止之后、任何模型调用之前。登记人：主 Agent（Claude）。
原 `preregistration.md` 不改；本修订只替换下列条目，其余全部沿用。

## 触发原因（证据：`T1-report.md` 第 2–3 节、`T1-blocker-source.txt`）

1. RefChecker 0.2.17 的联合核查（`is_joint=True`，RAGChecker 0.1.9 默认）只接受 `(subject, predicate, object)` 三元组（`llm_checker.py:104-105`、`checker_prompts.py:2`）。固定陈述是整句，直接送入会被截成前三个字。原第 3 节「按 RefChecker 默认的联合核查分组」无法执行。
2. 联合路径把缺失标签补为 Neutral（`llm_checker.py:144-150`），与「解析失败记为无结果」冲突。

## 修订内容

**M1：B 臂改用 RefChecker 原生整句核查路径。**
- 调用 `LLMChecker.check(..., is_joint=False, merge_psg=False, max_reference_segment_length=0)`。整句陈述以 `str` 传入，进入 `llm_checker.py:192` 分支，使用原提示词 `SUBSENTENCE_CLAIM_CHECKING_PROMPT` 与原标签判定（`llm_checker.py:220-228`），均不改动。
- 4 段 contexts 按原第 3 节拼成一份 reference 字符串，所以每条陈述只发一次请求：每轮 52 次，三轮 156 次。题面按原实现前置到 reference（`llm_checker.py:193-194`）。
- 理由：这是 RefChecker 为整句 claim 提供的原生路径（对应其 extractor 的 `claim_format='subsentence'`），不需要抽取三元组，也不需要自造输入适配。
- 代价：B 臂不再是 RAGChecker 默认的「三元组＋联合」配置。报告必须写明这一点，结论只适用于「RefChecker 整句核查器」。

**M2：两臂的「无结果」判定在外围统一做，原解析函数不改。**
- 每条原始响应同时记录两项：原解析函数的返回值；外围检测的结果。
- 外围检测：
  - B 臂：响应中 Entailment、Neutral、Contradiction 三个词一个都没出现（不区分大小写），记为「无结果」。
  - A 臂：Ragas 原解析抛出异常、返回的条数与陈述数不一致，或某条陈述缺少 verdict，相应陈述记为「无结果」。
- 指标只使用外围结果。原解析值只记录，不参与计分。
- 空响应：原实现会 `raise`，计为一次接口错误，计入第 8 节「错误率」。

**M3：max_tokens。** 原包的非联合路径传 `max_new_tokens=10`。两臂统一由替换后的发送客户端使用 2048（与原第 3 节一致），报告注明这一覆盖。

**M4：预算台账口径澄清。** 每次请求前检查「已结算实际费用 + 未结算预留 + 本次预留」。达到 ¥8 就不发这次请求，并停止。结算完成后，该请求的预留释放，改计实际费用。

## 不变

固定输入、确定集与未决集合、指标、判定规则、交错顺序、停止条件、写入范围、¥8 硬停、¥10 总上限，全部不变。
