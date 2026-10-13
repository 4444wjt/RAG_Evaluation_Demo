import math
import random

import pytest

from backend.core import judge_error as je

# 25 题正式运行的 20 道正向题：Ragas 忠实度的逐条判定（后 5 题依次为 F01–F05，F02 以外均为拒答）
FORMAL = [[0, 1, 1, 1], [1] * 4, [1] * 4, [1] * 2, [1] * 3, [1], [1] * 2, [1] * 2, [0, 1], [1] * 5,
          [1] * 3, [1, 0, 1, 1, 0, 0], [1] * 2, [1] * 2, [1] * 2, [0], [1] * 4, [0], [0], [0]]
REFUSALS = [15, 17, 18, 19]


def test_formal_run_values_and_effective_sample_size():
    values = je.question_values(FORMAL)
    assert sum(values) / len(values) == 0.7375
    assert sum(len(row) for row in FORMAL) == 52
    # Ĝ(1−Ĝ) / (s²/Q)：s² ≈ 0.1676，按题聚集后 52 句陈述只相当于约 23 条独立陈述
    assert je.effective_n(values) == pytest.approx(23.102, abs=1e-3)
    assert je.effective_n(values, cap=52) == je.effective_n(values)
    assert je.effective_n(values, cap=20) == 20.0


def test_effective_sample_size_falls_back_when_design_effect_is_unknown():
    assert je.effective_n([0.5, 0.5, 0.5], cap=12) == 12.0  # 题间没有波动
    assert je.effective_n([1.0, 1.0], cap=7) == 7.0
    assert je.effective_n([0.0, 0.0, 0.0]) == 3.0  # 未给上限时退回题数
    # 题目占比只取 0 或 1 时，有效样本量约等于题数
    assert je.effective_n([0, 1, 1, 0, 1, 1, 1, 0, 1, 1]) == pytest.approx(9.0)


def test_question_values_rejects_invalid_rows():
    with pytest.raises(ValueError):
        je.question_values([[1, 0], []])
    with pytest.raises(ValueError):
        je.question_values([[1, 2]])
    with pytest.raises(ValueError):
        je.question_values([])


def test_wilson_matches_the_pooled_interval_in_the_gate_credibility_report():
    low, high = je.wilson(43, 52)
    assert (round(low, 3), round(high, 3)) == (0.703, 0.906)
    assert je.wilson(0, 0) is None
    assert je.wilson(0, 10)[0] == pytest.approx(0.0) and je.wilson(10, 10)[1] == pytest.approx(1.0)


def test_rogan_gladen_inverts_the_expected_judge_value():
    for theta in (0.0, 0.3, 0.75, 1.0):
        for q0, q1 in ((0.89, 0.7273), (0.95, 0.95), (0.6, 0.99)):
            observed = (q0 + q1 - 1) * theta + (1 - q0)
            assert je.rogan_gladen(observed, q0, q1) == pytest.approx(theta)
    assert je.rogan_gladen(0.7375, 0.89, 0.7273) == 1.0  # 越界截断
    assert je.rogan_gladen(0.5, 0.5, 0.5) is None  # 裁判不比随机好


def test_corrected_interval_matches_a_hand_computation():
    # p̃ = 0.68573，q̃0 = 0.88462，q̃1 = 0.83654，θ̃ = 0.79088，dθ̃ = 0.01283，半宽 0.20579
    result = je.corrected_interval(0.7, 50, 0.9, 50, 0.85, 50)
    assert result['point'] == pytest.approx(0.8)
    assert result['interval'][0] == pytest.approx(0.597916, abs=1e-6)
    assert result['interval'][1] == 1.0


def test_corrected_interval_reduces_to_agresti_coull_for_a_perfect_judge():
    z = je.Z95
    for p, n in ((0.7, 50), (0.95, 20), (0.2, 13.5)):
        n_t = n + z * z
        p_t = (n * p + z * z / 2) / n_t
        half = z * math.sqrt(p_t * (1 - p_t) / n_t)
        result = je.corrected_interval(p, n, 1.0, 10 ** 9, 1.0, 10 ** 9)
        assert result['interval'] == pytest.approx([max(0.0, p_t - half), min(1.0, p_t + half)], abs=1e-7)


def test_corrected_interval_widens_with_fewer_calibration_labels_and_rejects_bad_input():
    def width(m0, m1):
        low, high = je.corrected_interval(0.75, 23.1, 0.9, m0, 0.9, m1)['interval']
        return high - low
    assert width(10, 40) > width(50, 50) > width(500, 500)
    assert je.corrected_interval(0.5, 50, 0.3, 50, 0.4, 50) is None
    with pytest.raises(ValueError):
        je.corrected_interval(0.5, 0, 0.9, 50, 0.9, 50)


def test_clustered_interval_uses_the_effective_sample_size():
    values = je.question_values(FORMAL)
    calibration = {'q0': 0.9, 'm0': 30, 'q1': 0.95, 'm1': 60}
    result = je.clustered_interval(values, calibration, statements=52)
    assert result['nEff'] == pytest.approx(23.102, abs=1e-3)
    direct = je.corrected_interval(0.7375, result['nEff'], 0.9, 30, 0.95, 60)
    assert result['interval'] == direct['interval'] and result['point'] == direct['point']
    # 把 52 句当独立样本时区间更窄：忽略聚集会低估不确定性
    iid = je.corrected_interval(0.7375, 52, 0.9, 30, 0.95, 60)['interval']
    assert iid[1] - iid[0] < result['interval'][1] - result['interval'][0]


def test_ppi_corrects_with_labeled_questions_only():
    judge_u, judge_l = [0.5, 1.0, 0.75, 1.0], [1.0, 0.5, 1.0]
    same = je.ppi_interval(judge_u, judge_l, judge_l)
    assert same['point'] == pytest.approx(0.8125)
    # 人工与裁判一致时，只剩未标注题的抽样误差
    assert same['interval'] == pytest.approx(je.question_interval(judge_u))
    shifted = je.ppi_interval(judge_u, judge_l, [1.0, 1.0, 1.0])
    assert shifted['point'] == pytest.approx(0.8125 + 0.5 / 3)
    with pytest.raises(ValueError):
        je.ppi_interval([0.5], judge_l, judge_l)
    with pytest.raises(ValueError):
        je.ppi_interval(judge_u, judge_l, [1.0])


def test_ppi_is_unbiased_when_the_judge_is_biased():
    # 裁判漏判 40% 的支持、误判 10% 的不支持，裁判均值约 0.35；PPI 仍以真实值 0.5 为中心（取 0.5 使截断几乎不发生）
    rng = random.Random(7)
    estimates, judged = [], []
    for _ in range(3000):
        truth = [1.0 if rng.random() < 0.5 else 0.0 for _ in range(20)]
        judge = [float(rng.random() < (0.6 if t else 0.1)) for t in truth]
        judged.append(sum(judge) / 20)
        estimates.append(je.ppi_interval(judge[10:], judge[:10], truth[:10])['point'])
    assert sum(judged) / len(judged) == pytest.approx(0.35, abs=0.01)
    assert sum(estimates) / len(estimates) == pytest.approx(0.5, abs=0.01)


def test_min_flips_on_the_formal_run():
    overall = je.min_flips(FORMAL, 0.8)
    assert overall['passed'] is False and overall['flips'] == 2
    assert all(q in REFUSALS for q, _ in overall['statements'])
    answered = je.min_flips(FORMAL, 0.8, exclude=REFUSALS)
    # 不计拒答题：5 条判为不支持的陈述要全部改判，均值恰好到 0.80
    assert answered['flips'] == 5 and answered['after'] == pytest.approx(0.8)
    assert sorted(answered['statements']) == [[0, 0], [8, 0], [11, 1], [11, 4], [11, 5]]
    assert je.min_flips(FORMAL, 0.8, exclude=REFUSALS + [11])['flips'] is None


def test_min_flips_from_pass_to_fail_and_rounding():
    rows = [[1, 1], [1], [1, 1, 1, 1]]
    result = je.min_flips(rows, 0.8)
    assert result['passed'] is True and result['flips'] == 1 and result['statements'] == [[1, 0]]
    # 与流水线一致按 4 位小数比较：0.79996 记为 0.8，视为达到门槛
    assert je.min_flips([[1] * 9 + [0]] * 10, 0.89996)['passed'] is True


def test_tipping_sensitivity_is_where_the_corrected_point_meets_the_threshold():
    assert je.tipping_sensitivity(0.7375, 0.8, 1.0) == pytest.approx(0.921875)
    assert je.tipping_sensitivity(0.7375, 0.8, 0.9) == pytest.approx(0.896875)
    for q0 in (0.7, 0.85, 1.0):
        q1 = je.tipping_sensitivity(0.7375, 0.8, q0)
        assert je.rogan_gladen(0.7375, q0, q1) == pytest.approx(0.8)
        assert je.rogan_gladen(0.7375, q0, q1 - 0.01) > 0.8


def test_calibration_counts_and_three_state():
    result = je.calibration_counts([(1, 1)] * 40 + [(1, 0)] * 2 + [(0, 0)] * 7 + [(0, 1)] * 3)
    assert (result['m1'], result['m0']) == (42, 10)
    assert result['q1'] == pytest.approx(40 / 42) and result['q0'] == pytest.approx(0.7)
    assert result['confusion'] == {'humanSupportedJudgeSupported': 40, 'humanSupportedJudgeNot': 2,
                                   'humanNotJudgeSupported': 3, 'humanNotJudgeNot': 7}
    assert je.calibration_counts([(1, 1)])['q0'] is None
    with pytest.raises(ValueError):
        je.calibration_counts([(1, 2)])
    assert je.three_state(0.81, 0.9, 0.8) == je.PASS
    assert je.three_state(0.6, 0.79, 0.8) == je.FAIL
    assert je.three_state(0.7, 0.85, 0.8) == je.UNSURE


def test_cohen_kappa():
    a = ['支持'] * 8 + ['无依据'] * 2
    assert je.cohen_kappa(a, a) == 1.0
    b = ['支持'] * 7 + ['无依据'] * 3
    # 观测一致 0.9，期望一致 0.8×0.7 + 0.2×0.3 = 0.62
    assert je.cohen_kappa(a, b) == pytest.approx((0.9 - 0.62) / 0.38)
    assert je.cohen_kappa(['支持'] * 4, ['支持'] * 4) is None
    with pytest.raises(ValueError):
        je.cohen_kappa(a, b[:5])
