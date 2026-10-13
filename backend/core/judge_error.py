"""考虑裁判误差的门禁区间：同时计入「题目抽样」与「裁判判错」两类不确定性。

门禁的正向忠实度 G 是逐题「判为支持的陈述占比」的平均。裁判会判错：真支持的陈述以概率 q1 判为支持
（灵敏度），真不支持的陈述以概率 q0 判为不支持（特异度）。G 对逐条判定是线性的，所以
    E[Ĝ] = (q0 + q1 − 1)·θ + (1 − q0)，
θ 为人工口径下的门禁指标。本模块提供三种做法：

  - 校准校正（Lee et al., arXiv 2511.21140）：用一批人工标注估计 q0、q1，按 Rogan–Gladen 校正点估计，
    再用调整 Wald 区间（原文式 8–12）同时计入测试样本与校准样本的抽样误差。原方法假设测试样本相互独立；
    门禁的陈述按题聚集，这里把测试样本量换成按题方差折算的有效样本量 n_eff = Ĝ(1−Ĝ)/Var_q(Ĝ)
    （Kish 设计效应），这是本项目的推广，覆盖率由模拟检验；
  - 预测驱动推断（PPI，Angelopoulos et al., Science 2023）：只人工标注部分题目时，未标注题用裁判值，
    已标注题用「人工 − 裁判」的差校正，不需要裁判误差的任何假设；
  - 人工标注全部陈述时，直接按题计算人工口径的指标与区间，只剩题目抽样误差。

另给两种不需要人工标注的敏感性分析：最少要改判几条陈述，门禁结论才会翻转；裁判的灵敏度、特异度落在
什么范围时，校正后的点估计会越过门槛。

只做计算，不调用模型、不改门禁规则；结果只说明门禁结论的可信程度，不用于改判。
"""
from __future__ import annotations

from fractions import Fraction
import math

Z95 = 1.959963984540054
PASS, FAIL, UNSURE = "确定通过", "确定不通过", "证据不足"


def _mean(values):
    return sum(values) / len(values)


def _variance(values):
    """样本方差（除以 n − 1）；少于两个值时为 0。"""
    if len(values) < 2:
        return 0.0
    centre = _mean(values)
    return sum((v - centre) ** 2 for v in values) / (len(values) - 1)


def _clip(value):
    return min(1.0, max(0.0, value))


def wilson(successes, total, z=Z95):
    """比例的 Wilson 区间；total 为 0 时返回 None。"""
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [_clip(centre - half), _clip(centre + half)]


def three_state(low, high, threshold):
    """区间下限也达到门槛为确定通过，上限仍低于门槛为确定不通过，其余为证据不足（与门禁可信度第四问一致）。"""
    if low >= threshold:
        return PASS
    if high < threshold:
        return FAIL
    return UNSURE


def question_values(rows):
    """逐题「判为支持的陈述占比」。rows 为每题的逐条判定列表（1 支持、0 不支持）。"""
    if not rows or any(not row for row in rows):
        raise ValueError("每道题至少要有一条陈述")
    if any(v not in (0, 1) for row in rows for v in row):
        raise ValueError("逐条判定只能是 0 或 1")
    return [sum(row) / len(row) for row in rows]


def question_interval(values, z=Z95):
    """只计题目抽样：按题均值 ± z·SE，SE 由题间方差估计。"""
    centre = _mean(values)
    half = z * math.sqrt(_variance(values) / len(values))
    return [_clip(centre - half), _clip(centre + half)]


def effective_n(values, cap=None):
    """按题聚集时的有效样本量 n_eff = Ĝ(1−Ĝ) / Var_q(Ĝ)，Var_q 为按题估计的均值方差。

    题间没有波动或 Ĝ 为 0、1 时无法估计设计效应，退回陈述总数 cap（未给出时用题数）。
    """
    count = len(values)
    centre = _mean(values)
    variance = _variance(values) / count
    if variance <= 0 or centre <= 0 or centre >= 1:
        return float(cap if cap is not None else count)
    n = centre * (1 - centre) / variance
    return min(n, float(cap)) if cap is not None else n


def sampling_interval(values, statements=None, z=Z95):
    """只计题目抽样的区间：有效样本量代入 Agresti–Coull 区间（同 Korn & Graubard 1998 处理聚集抽样的思路）。

    等于裁判完全正确、校准样本无穷大时的 corrected_interval，便于与校准校正区间直接比较。
    """
    n = effective_n(values, cap=statements)
    p = _mean(values)
    zz = z * z
    n_t = n + zz
    p_t = (n * p + zz / 2) / n_t
    half = z * math.sqrt(p_t * (1 - p_t) / n_t)
    return {"point": p, "interval": [_clip(p_t - half), _clip(p_t + half)], "nEff": n}


def rogan_gladen(p, q0, q1):
    """Rogan–Gladen 校正（Lee et al. 式 7），截断到 [0, 1]；裁判不比随机好（q0 + q1 ≤ 1）时返回 None。"""
    youden = q0 + q1 - 1
    if youden <= 0:
        return None
    return _clip((p + q0 - 1) / youden)


def corrected_interval(p_hat, n, q0_hat, m0, q1_hat, m1, z=Z95):
    """Lee et al. 式 8–12 的调整 Wald 区间，同时计入测试样本 n 与校准样本 m0、m1 的抽样误差。

    p_hat 为裁判口径的指标，n 为测试样本量（可为有效样本量，不必是整数）；
    q0_hat、q1_hat 为在 m0 条真不支持、m1 条真支持的人工标注陈述上估计的特异度与灵敏度。
    调整后的裁判不比随机好时返回 None。
    """
    if n <= 0 or m0 <= 0 or m1 <= 0:
        raise ValueError("测试与校准样本量都必须为正")
    zz = z * z
    n_t, m0_t, m1_t = n + zz, m0 + 2, m1 + 2
    p_t = (n * p_hat + zz / 2) / n_t
    q0_t = (m0 * q0_hat + 1) / m0_t
    q1_t = (m1 * q1_hat + 1) / m1_t
    youden = q0_t + q1_t - 1
    if youden <= 0:
        return None
    theta_t = (p_t + q0_t - 1) / youden
    shift = 2 * zz * (-(1 - theta_t) * q0_t * (1 - q0_t) / m0_t + theta_t * q1_t * (1 - q1_t) / m1_t)
    variance = (p_t * (1 - p_t) / n_t + (1 - theta_t) ** 2 * q0_t * (1 - q0_t) / m0_t
                + theta_t ** 2 * q1_t * (1 - q1_t) / m1_t) / youden ** 2
    half = z * math.sqrt(variance)
    centre = theta_t + shift
    return {"point": rogan_gladen(p_hat, q0_hat, q1_hat), "interval": [_clip(centre - half), _clip(centre + half)]}


def clustered_interval(values, calibration, statements=None, z=Z95):
    """门禁指标的校准校正区间：Lee et al. 的区间，测试样本量取按题聚集的有效样本量。

    values 为裁判口径的逐题占比；calibration 含 q0、m0、q1、m1；statements 为陈述总数（有效样本量的上限）。
    """
    n = effective_n(values, cap=statements)
    result = corrected_interval(_mean(values), n, calibration["q0"], calibration["m0"],
                                calibration["q1"], calibration["m1"], z)
    return None if result is None else {**result, "nEff": n}


def ppi_interval(judge_unlabeled, judge_labeled, human_labeled, z=Z95):
    """按题的预测驱动推断（PPI）：两组题互不重叠，各自至少两题。

    点估计 = 未标注题的裁判均值 + 已标注题「人工 − 裁判」的均值；
    方差 = 未标注题裁判值的方差 / U + 差值的方差 / L。
    """
    if len(judge_labeled) != len(human_labeled):
        raise ValueError("已标注题的裁判值与人工值数量不一致")
    if len(judge_unlabeled) < 2 or len(judge_labeled) < 2:
        raise ValueError("未标注题与已标注题都至少要有两题")
    rectifier = [h - j for h, j in zip(human_labeled, judge_labeled)]
    point = _mean(judge_unlabeled) + _mean(rectifier)
    half = z * math.sqrt(_variance(judge_unlabeled) / len(judge_unlabeled) + _variance(rectifier) / len(rectifier))
    return {"point": _clip(point), "interval": [_clip(point - half), _clip(point + half)]}


def _gate_passes(total, count, threshold, digits):
    # 与评测流水线一致：正向均值保留 4 位小数后与门槛比较
    return round(float(total / count), digits) >= threshold


def min_flips(rows, threshold, exclude=(), digits=4):
    """最少改判几条陈述，门禁结论才会翻转。

    当前拦截时，把判为不支持的陈述改为支持；当前放行时反之。每改判一条，指标变化 1 /（题数 × 该题陈述数），
    所以按变化量从大到小依次改判即为最少条数。exclude 为不参与改判的题（下标）。无法翻转时 flips 为 None。
    """
    question_values(rows)
    count, excluded = len(rows), set(exclude)
    total = sum(Fraction(sum(row), len(row)) for row in rows)
    passed = _gate_passes(total, count, threshold, digits)
    target = 0 if passed else 1
    candidates = sorted(((Fraction(1, len(row)), q, j) for q, row in enumerate(rows) if q not in excluded
                         for j, verdict in enumerate(row) if verdict == 1 - target),
                        key=lambda item: (-item[0], item[1], item[2]))
    chosen = []
    for weight, q, j in candidates:
        if _gate_passes(total, count, threshold, digits) != passed:
            break
        total += weight if target == 1 else -weight
        chosen.append([q, j])
    flipped = _gate_passes(total, count, threshold, digits) != passed
    return {"passed": passed, "flips": len(chosen) if flipped else None,
            "statements": chosen if flipped else [], "after": float(total / count) if flipped else None}


def tipping_sensitivity(p, threshold, q0):
    """给定特异度 q0，校正后的点估计恰好等于门槛时的灵敏度 q1；灵敏度低于它，校正后即达到门槛。

    由 (p + q0 − 1) / (q0 + q1 − 1) = t 解出 q1 = (p − 1 + t + (1 − t)·q0) / t。
    """
    return (p - 1 + threshold + (1 - threshold) * q0) / threshold


def calibration_counts(pairs):
    """由（人工, 裁判）二值标签对估计灵敏度 q1 与特异度 q0，并给出 Wilson 区间。"""
    pairs = list(pairs)
    if any(h not in (0, 1) or j not in (0, 1) for h, j in pairs):
        raise ValueError("标签只能是 0 或 1")
    m1 = sum(1 for h, _ in pairs if h == 1)
    m0 = len(pairs) - m1
    hit1 = sum(1 for h, j in pairs if h == 1 and j == 1)
    hit0 = sum(1 for h, j in pairs if h == 0 and j == 0)
    return {"m1": m1, "q1": hit1 / m1 if m1 else None, "q1Wilson95": wilson(hit1, m1),
            "m0": m0, "q0": hit0 / m0 if m0 else None, "q0Wilson95": wilson(hit0, m0),
            "confusion": {"humanSupportedJudgeSupported": hit1, "humanSupportedJudgeNot": m1 - hit1,
                          "humanNotJudgeSupported": m0 - hit0, "humanNotJudgeNot": hit0}}


def cohen_kappa(first, second):
    """两位标注人的 Cohen's κ；两人的标签分布都只有同一个值时一致性无法区分于随机，返回 None。"""
    if len(first) != len(second) or not first:
        raise ValueError("两份标签数量须相同且不为空")
    total = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / total
    labels = set(first) | set(second)
    expected = sum((first.count(label) / total) * (second.count(label) / total) for label in labels)
    if expected >= 1:
        return None
    return (observed - expected) / (1 - expected)
