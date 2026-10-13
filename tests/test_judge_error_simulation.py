"""裁判判错校准的模拟：归档结果可按同一设定重算，文档引用的结论在归档中成立。"""
import json
import sys

import pytest

from tools.competition import judge_error_simulation as sim

ARCHIVE = json.loads(sim.ARCHIVE.read_text(encoding='utf-8'))


def _cells(scenario):
    return [cell for cell in ARCHIVE['cells'] if cell['scenario'] == scenario]


@pytest.mark.skipif(ARCHIVE['pythonVersion'] != '.'.join(map(str, sys.version_info[:2])),
                    reason='随机数序列只在同一 Python 版本下保证一致')
def test_archive_recomputes():
    assert sim.same(ARCHIVE, sim.simulate(ARCHIVE['seed'], ARCHIVE['replicates']))


def test_archive_uses_the_formal_run_structure_and_measured_judge_rates():
    structure, rates = sim.formal_structure(), sim.judge_rates()
    assert ARCHIVE['structure'] == structure and ARCHIVE['judgeRates'] == rates
    assert structure['statements'] == 52 and len(structure['sizes']) == 20
    assert structure['concentration'] == pytest.approx(0.4, abs=0.01)
    assert rates['q1Counts'] == [72, 99] and rates['q0Counts'] == [178, 200]
    assert len(ARCHIVE['cells']) == 24


def test_documented_conclusions_hold():
    coverage = lambda cells, name: [c['estimators'][name]['coverage'] for c in cells]
    invariant, shifted = _cells('invariant'), _cells('shifted')
    # 判定不变：只计题目抽样严重失准，把陈述当独立样本略偏低，按题聚集的校正接近 95%
    assert max(coverage(invariant, 'naive')) < 0.6
    assert 0.9 <= min(coverage(invariant, 'leeIndependent')) and max(coverage(invariant, 'leeIndependent')) < 0.94
    assert min(coverage(invariant, 'leeClustered')) >= 0.94
    # 判定漂移：校准校正失准，同次运行人工标注的 PPI 不受影响
    assert max(coverage(shifted, 'leeClustered')) < 0.9
    assert min(coverage(shifted, 'ppi')) >= 0.92
    # 真实值 0.9（达标）时，只计题目抽样约一半的模拟判为「确定不通过」
    naive_fail = [c['estimators']['naive']['decisions']['确定不通过'] for c in invariant if c['theta'] == 0.9]
    assert min(naive_fail) > 0.45
