"""门禁可信度第 4 问：25 题正式运行的门禁结论有多确定。

门禁按正向题均值与 0.8 门槛比较，得出 PASS / FAIL。20 道正向题是小样本，均值本身有抽样波动，
本脚本从冻结证据离线计算这份不确定性，不调用模型、不改门禁、不改任何已记录的结论：

  - 按题 bootstrap 区间：以「题」为单位有放回重抽（同一题的陈述彼此相关，不能当独立样本）；
  - 按陈述合并的 Wilson 区间：把 52 句陈述合在一起看，作为口径对照；
  - 口径敏感性：按题平均与按陈述合并、拒答按 0 计与拒答不计，各自的点估计；
  - 三态解读：区间下限也达到门槛为「确定通过」，区间上限仍低于门槛为「确定不通过」，其余为「证据不足」。

这是对已有结论的事后分析，只说明结论的确定程度，不能用来改判；门禁仍按原规则拦截。

  python tools/competition/gate_uncertainty.py           打印中文报告
  python tools/competition/gate_uncertainty.py --json    打印 JSON
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.competition import build_replay_fixture as replay  # noqa: E402

SEED = 20261012
RESAMPLES = 20000
Z95 = 1.959963984540054


def bootstrap(values: list[float], seed: int = SEED, resamples: int = RESAMPLES) -> dict:
    """按题有放回重抽，返回均值的 95% 百分位区间，以及排序后的全部重抽均值。"""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(resamples))
    return {'low': means[int(0.025 * resamples)], 'high': means[int(0.975 * resamples)], 'means': means}


def wilson(successes: int, total: int) -> tuple[float, float]:
    p = successes / total
    centre = (p + Z95 ** 2 / (2 * total)) / (1 + Z95 ** 2 / total)
    half = Z95 * math.sqrt(p * (1 - p) / total + Z95 ** 2 / (4 * total ** 2)) / (1 + Z95 ** 2 / total)
    return centre - half, centre + half


def three_state(low: float, high: float, threshold: float) -> str:
    if low >= threshold:
        return '确定通过'
    if high < threshold:
        return '确定不通过'
    return '证据不足'


def analyze(evidence: Path = replay.EVIDENCE) -> dict:
    fixture = replay.build(evidence)  # 已核对证据哈希，并逐题核对逐句判定与归档 faithfulness 一致
    response = json.loads((evidence / 'api-response.json').read_text(encoding='utf-8'))
    rows = response['model_dfs'][replay.MODEL]
    positive = fixture['rows']
    thresholds, gate = fixture['thresholds'], fixture['gate']

    faith = [row['faithfulness'] for row in positive]
    ctx = [rows[row['rowIndex']]['context_precision'] for row in positive]
    if round(sum(ctx) / len(ctx), 4) != gate['posContextPrecision']:  # 归档值保留 4 位小数
        raise ValueError('正向题上下文精度均值与归档 pos_context_precision 不一致。')

    report = {'source': fixture['source'], 'seed': SEED, 'resamples': RESAMPLES,
              'thresholds': thresholds, 'recordedGatePassed': gate['passed'], 'metrics': {}}
    for name, values, threshold in (('faithfulness', faith, thresholds['faithfulness']),
                                    ('context_precision', ctx, thresholds['context_precision'])):
        boot = bootstrap(values)
        report['metrics'][name] = {
            'questions': len(values), 'mean': sum(values) / len(values),
            'bootstrap95': [boot['low'], boot['high']],
            'shareOfResamplesAtOrAboveThreshold': sum(m >= threshold for m in boot['means']) / RESAMPLES,
            'state': three_state(boot['low'], boot['high'], threshold),
        }

    supported = sum(row['supported'] for row in positive)
    statements = sum(row['total'] for row in positive)
    answered = [row['faithfulness'] for row in positive if not row['refusal']]
    report['faithfulnessViews'] = {
        'perQuestionRefusalAsZero': {'value': sum(faith) / len(faith), 'note': '门禁实际使用的口径'},
        'pooledStatements': {'value': supported / statements, 'supported': supported, 'statements': statements,
                             'wilson95': list(wilson(supported, statements))},
        'perQuestionRefusalExcluded': {'value': sum(answered) / len(answered), 'questions': len(answered),
                                       'note': '仅作敏感性对照：正向题本应可答，拒答按 0 计是设计规则'},
    }
    point_passed = all(report['metrics'][m]['mean'] >= thresholds[m] for m in ('faithfulness', 'context_precision'))
    if point_passed != gate['passed']:
        raise ValueError('按点估计重算的门禁结论与归档结论不一致。')
    return report


def render(report: dict) -> str:
    pct = lambda value: f'{value * 100:.1f}%'
    lines = [f"来源：{report['source']['evidence']}（冻结证据，只读）",
             f"重抽：按题有放回 {report['resamples']} 次，随机种子 {report['seed']}", '']
    for name, label in (('faithfulness', '正向忠实度'), ('context_precision', '正向上下文精度')):
        m = report['metrics'][name]
        low, high = m['bootstrap95']
        lines.append(f"{label}：{pct(m['mean'])}（{m['questions']} 题），95% 区间 {pct(low)}–{pct(high)}，"
                     f"门槛 {pct(report['thresholds'][name])}，重抽均值达到门槛的比例 {pct(m['shareOfResamplesAtOrAboveThreshold'])}，"
                     f"三态解读：{m['state']}")
    views = report['faithfulnessViews']
    pooled = views['pooledStatements']
    lines += ['', '正向忠实度的口径敏感性：',
              f"  按题平均、拒答按 0 计（门禁口径）：{pct(views['perQuestionRefusalAsZero']['value'])}",
              f"  按陈述合并（{pooled['supported']}/{pooled['statements']}）：{pct(pooled['value'])}，"
              f"Wilson 95% 区间 {pct(pooled['wilson95'][0])}–{pct(pooled['wilson95'][1])}",
              f"  按题平均、拒答不计（{views['perQuestionRefusalExcluded']['questions']} 题，仅作对照）：{pct(views['perQuestionRefusalExcluded']['value'])}",
              '', f"归档门禁结论：{'PASS' if report['recordedGatePassed'] else 'FAIL'}（不因本分析改变）"]
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()
    report = analyze()
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
