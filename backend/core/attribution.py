"""门禁失败归因：逐题判断问题出在检索还是生成，并按类别拆分门禁差距。

门禁只回答「能不能放行」；拦截之后，开发者真正要知道的是「该修检索，还是该修生成」。
本模块只读已有的逐题指标，不调用模型、不修改输入、不影响门禁裁决：

  检索未找全   context_recall < RECALL_FLOOR：参考答案需要的原文没有被检索到，生成无从依据；
  检索排序差   召回足够，但 context_precision < PRECISION_FLOOR：相关原文排在无关片段之后；
  生成拒答     检索正常，回答是拒答句：原文已找到，模型却没有作答（拒答在门禁里按 0 计）；
  生成无依据   检索正常，faithfulness < FAITH_FLOOR：回答里有原文推不出的内容；
  正常         以上都不成立。

指标缺失的题单列为「无法判断」，不补 0。阈值是诊断用的经验值，不是门禁阈值。
"""
import math

RECALL_FLOOR = 0.5
PRECISION_FLOOR = 0.5
FAITH_FLOOR = 0.5
REFUSAL_MARKERS = ("无法回答", "无法根据", "没有提供", "未提及", "不包含相关")

RETRIEVAL_MISS, RETRIEVAL_RANK, REFUSAL, UNGROUNDED, OK, UNKNOWN = (
    "检索未找全", "检索排序差", "生成拒答", "生成无依据", "正常", "无法判断")
CATEGORIES = (RETRIEVAL_MISS, RETRIEVAL_RANK, REFUSAL, UNGROUNDED, OK, UNKNOWN)
STAGE = {RETRIEVAL_MISS: "检索", RETRIEVAL_RANK: "检索", REFUSAL: "生成", UNGROUNDED: "生成", OK: None, UNKNOWN: None}
ADVICE = {
    RETRIEVAL_MISS: "检查切分粒度、Top-K 与向量模型，确认参考答案所需条文能被检索到。",
    RETRIEVAL_RANK: "加入重排序或调整相似度阈值，让相关原文排在前面。",
    REFUSAL: "检索已找到依据，模型仍拒答：放宽提示词中的拒答条件，或在提示中强调依据已提供。",
    UNGROUNDED: "约束模型只依据给定原文作答，并对照逐句核对结果修改提示词。",
}


def _value(row, key):
    value = row.get(key)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def is_refusal(answer):
    text = str(answer or "")
    return any(marker in text for marker in REFUSAL_MARKERS)


def classify(row):
    recall, precision, faith = _value(row, "context_recall"), _value(row, "context_precision"), _value(row, "faithfulness")
    if recall is not None and recall < RECALL_FLOOR:
        return RETRIEVAL_MISS
    if precision is not None and precision < PRECISION_FLOOR:
        return RETRIEVAL_RANK
    if faith is None:
        return UNKNOWN
    if faith < FAITH_FLOOR and is_refusal(row.get("answer")):
        return REFUSAL
    if faith < FAITH_FLOOR:
        return UNGROUNDED
    if recall is None and precision is None:
        return UNKNOWN
    return OK


def attribute(result):
    """对门禁使用的主评测模型的正向题逐题归因。返回 dict，供命令行与界面展示。"""
    model = next(s for s in result["summary"] if s.get("faithfulness") is not None)["model"]
    rows = (result.get("model_dfs") or {}).get(model)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"缺少模型 {model} 的逐题结果（model_dfs），无法归因")
    questions = []
    for index, row in enumerate(rows):
        if row.get("case_type") != "positive":
            continue
        category = classify(row)
        faith = _value(row, "faithfulness")
        questions.append({"index": index, "question": str(row.get("question", "")), "category": category,
                          "stage": STAGE[category], "faithfulness": faith,
                          "context_precision": _value(row, "context_precision"),
                          "context_recall": _value(row, "context_recall")})
    scored = [q for q in questions if q["faithfulness"] is not None]
    # 门禁差距拆分：每题对「正向忠实度离满分差多少」的贡献 = (1 - faithfulness) / 题数
    loss = {c: 0.0 for c in CATEGORIES}
    for q in scored:
        loss[q["category"]] += (1 - q["faithfulness"]) / len(scored)
    counts = {c: sum(q["category"] == c for q in questions) for c in CATEGORIES}
    retrieval = loss[RETRIEVAL_MISS] + loss[RETRIEVAL_RANK]
    generation = loss[REFUSAL] + loss[UNGROUNDED]
    primary = None if retrieval == generation == 0 else ("检索" if retrieval > generation else "生成")
    return {
        "model": model, "questions": questions, "counts": counts,
        "faithfulnessLoss": {c: round(v, 4) for c, v in loss.items()},
        "retrievalLoss": round(retrieval, 4), "generationLoss": round(generation, 4),
        "primaryStage": primary,
        "advice": [{"category": c, "advice": ADVICE[c]} for c in ADVICE if counts[c]],
    }
