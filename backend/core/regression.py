"""回归对比：把本次评测与基线评测逐题配对，判断门禁指标是否真的退步。

门禁（gate.py）只看本次分数是否达到阈值；这里回答另一个问题——「这次比上次差了吗」。
两层判定：
1. 抽样：按题配对重抽样，求正向均值变化的 95% 区间；区间整体低于 0 才说明下降不是题目抽样造成的。
2. 裁判波动：同一裁判对同一批回答重判一次，逐题分数也会变动（NOISE_FLOOR，来自归档实测）。
   均值下降不超过这个幅度时，无法与裁判自身波动区分。
两层都成立才判「确定退步」，否则只提示「可能退步」。

纯函数：不调用模型、不写文件，不修改输入；只依赖 numpy / pandas / 标准库。
"""
import math

import numpy as np

METRICS = (
    ("pos_faithfulness", "faithfulness", "正向忠实度"),
    ("pos_context_precision", "context_precision", "正向上下文精度"),
)

# 同一裁判（DeepSeek）对 25 题正式运行重判一次，逐题分数的平均绝对变化。
# 出处：docs/competition/judge-comparison/20261011-qwen72b/summary.json → fullRun.vsOriginal.self-rerun.*.meanAbsDiff
NOISE_FLOOR = {"pos_faithfulness": 0.0167, "pos_context_precision": 0.04}

BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 20261013

CONFIRMED, POSSIBLE, NONE, UNPAIRED = "确定退步", "可能退步", "未退步", "无法配对"


def _missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


def judged_rows(result):
    """与门禁相同：取第一个 faithfulness 非空的模型，返回 (模型名, 逐题正向行)。"""
    model = next(s for s in result["summary"] if s.get("faithfulness") is not None)["model"]
    rows = (result.get("model_dfs") or {}).get(model)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"缺少模型 {model} 的逐题结果（model_dfs），无法与基线逐题对比")
    if any("case_type" not in row or "question" not in row for row in rows):
        raise ValueError("逐题结果缺少 question 或 case_type 字段，无法与基线逐题对比")
    return model, [row for row in rows if row["case_type"] == "positive"]


def _keyed(rows):
    """按题目文本配对；同一题目出现多次时按出现顺序编号。"""
    keyed, seen = {}, {}
    for row in rows:
        question = str(row["question"])
        seen[question] = seen.get(question, 0) + 1
        keyed[(question, seen[question])] = row
    return keyed


def bootstrap_interval(deltas, resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    """按题有放回重抽样，返回均值变化的 95% 区间（固定种子，结果可复现）。"""
    values = np.asarray(deltas, dtype=float)
    if values.size == 0:
        return None
    rng = np.random.default_rng(seed)
    means = values[rng.integers(0, values.size, size=(resamples, values.size))].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return float(low), float(high)


def classify(mean_delta, interval, noise):
    if mean_delta is None:
        return UNPAIRED
    if mean_delta >= 0:
        return NONE
    if interval is not None and interval[1] < 0 and -mean_delta > noise:
        return CONFIRMED
    return POSSIBLE


def compare(baseline, candidate, noise_floor=None):
    """逐题对比两次评测的门禁指标。返回 dict，供命令行、JUnit 与作业摘要展示。"""
    noise_floor = {**NOISE_FLOOR, **(noise_floor or {})}
    base_model, base_rows = judged_rows(baseline)
    cand_model, cand_rows = judged_rows(candidate)
    base, cand = _keyed(base_rows), _keyed(cand_rows)
    shared = [key for key in cand if key in base]

    metrics = []
    for summary_key, row_key, label in METRICS:
        pairs, one_sided = [], []
        for key in shared:
            before, after = base[key].get(row_key), cand[key].get(row_key)
            if _missing(before) and _missing(after):
                continue
            if _missing(before) or _missing(after):
                one_sided.append({"question": key[0], "baseline": None if _missing(before) else float(before),
                                  "candidate": None if _missing(after) else float(after)})
                continue
            pairs.append({"question": key[0], "baseline": float(before), "candidate": float(after),
                          "delta": round(float(after) - float(before), 4)})
        deltas = [pair["delta"] for pair in pairs]
        mean_delta = round(sum(deltas) / len(deltas), 4) if deltas else None
        interval = bootstrap_interval(deltas)
        noise = noise_floor[summary_key]
        metrics.append({
            "key": summary_key,
            "label": label,
            "baseline_mean": _summary_value(baseline, summary_key),
            "candidate_mean": _summary_value(candidate, summary_key),
            "paired": len(pairs),
            "mean_delta": mean_delta,
            "interval": None if interval is None else (round(interval[0], 4), round(interval[1], 4)),
            "noise": noise,
            "status": classify(mean_delta, interval, noise),
            "drops": sorted((p for p in pairs if p["delta"] < 0), key=lambda p: p["delta"]),
            "one_sided": one_sided,
        })
    return {
        "baseline_model": base_model,
        "candidate_model": cand_model,
        "shared": len(shared),
        "only_baseline": len([key for key in base if key not in cand]),
        "only_candidate": len([key for key in cand if key not in base]),
        "metrics": metrics,
        "confirmed": any(m["status"] == CONFIRMED for m in metrics),
    }


def _summary_value(result, key):
    value = next(s for s in result["summary"] if s.get("faithfulness") is not None).get(key)
    return None if _missing(value) else float(value)
