"""Offline analysis under preregistration-2 sections 4 and 5; no A/B advantage claim."""
import argparse
from decimal import Decimal
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as runner
common = runner.common


def analyze_rows(rows, inputs):
    reference = common.validate_reference(*inputs)
    ids = [sid for group in common.question_groups(inputs[0]) for sid in group['ids']]
    expected = {}
    for round_number in (1, 2, 3):
        for index, sid in enumerate(ids, 1):
            expected[('Bq0', round_number, sid)] = f'Bq0{round_number}-{index:03}'
        for sid in runner.SENTINELS:
            expected[('S', round_number, sid)] = f'S{round_number}-{sid}'
    indexed = {}
    mapping = {'Entailment': '支持', 'Neutral': '不支持', 'Contradiction': '不支持'}
    for row in rows:
        key = (row['arm'], row['round'], row['statement_id'])
        if key not in expected or key in indexed or row['request_id'] != expected[key]:
            raise common.StopRun('DUPLICATE_OR_UNREGISTERED_RESULT')
        if row['judgment'] not in ('支持', '不支持', '无结果'):
            raise common.StopRun('INVALID_RESULT_LABEL')
        if row['parse_failed'] != (row['judgment'] == '无结果'):
            raise common.StopRun('PARSE_FAILURE_MUST_BE_NO_RESULT')
        if row['judgment'] != '无结果' and mapping.get(row['original_label']) != row['judgment']:
            raise common.StopRun('ORIGINAL_OUTER_LABEL_MISMATCH')
        indexed[key] = row
    if set(indexed) != set(expected):
        raise common.StopRun('INCOMPLETE_RESULTS_NO_HYPOTHESIS_CONCLUSION')

    # This gate must precede inspection of Bq0 target outcomes.
    sentinels = [indexed[('S', r, sid)] for r in (1, 2, 3) for sid in runner.SENTINELS]
    drift = [row['request_id'] for row in sentinels
             if row['original_label'] != 'Entailment' or row['judgment'] != '支持' or row['parse_failed']]
    targets = [indexed[('Bq0', r, sid)] for r in (1, 2, 3) for sid in runner.SENTINELS]
    if drift:
        decision, hypothesis_evaluated = '存在时间漂移，不确定', False
    elif all(row['judgment'] == '不支持' and row['original_label'] in ('Neutral', 'Contradiction') for row in targets):
        decision, hypothesis_evaluated = '支持假设', True
    elif all(row['judgment'] == '支持' and row['original_label'] == 'Entailment' for row in targets):
        decision, hypothesis_evaluated = '否定假设', True
    else:
        decision, hypothesis_evaluated = '不确定', True

    runs = []
    for r in (1, 2, 3):
        run = [indexed[('Bq0', r, sid)] for sid in ids]
        hits = {value: sum(indexed[('Bq0', r, sid)]['judgment'] == ('支持' if value else '不支持')
                           for sid, label in reference.items() if label == value) for value in (0, 1)}
        runs.append({'round': r, 'agree': hits[1] + hits[0], 'denominator': 46,
                     'supported_hits': hits[1], 'supported_denominator': 40,
                     'unsupported_hits': hits[0], 'unsupported_denominator': 6,
                     'parse_failures': sum(row['parse_failed'] for row in run),
                     'interface_errors': sum(row['interface_error'] for row in run),
                     'no_result': sum(row['judgment'] == '无结果' for row in run),
                     'no_result_on_resolved': sum(indexed[('Bq0', r, sid)]['judgment'] == '无结果' for sid in reference)})
    states = {sid: {indexed[('Bq0', r, sid)]['judgment'] for r in (1, 2, 3)} for sid in ids}
    flips = [sid for sid in ids if {'支持', '不支持'}.issubset(states[sid])]
    missing = [sid for sid in ids if '无结果' in states[sid]]
    changes = [sid for sid in ids if len(states[sid]) > 1]
    request_usage = []
    for key in expected:
        row = indexed[key]
        try:
            cost = str(common.usage_cost(row['usage']))
        except ValueError:
            cost = None
        request_usage.append({'request_id': row['request_id'], 'arm': row['arm'], 'round': row['round'],
                              'model': row['model'], 'usage': row['usage'], 'estimated_cny': cost,
                              'interface_error': row['interface_error']})
    known_cost = sum((Decimal(row['estimated_cny']) for row in request_usage if row['estimated_cny'] is not None), Decimal('0'))
    unknown_cost_count = sum(row['estimated_cny'] is None for row in request_usage)
    usage_keys = ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens')
    usage_total = (None if unknown_cost_count else
                   {key: sum(row['usage'][key] for row in request_usage) for key in usage_keys})
    return {
        'decision': decision, 'hypothesis_evaluated': hypothesis_evaluated,
        'sentinel_gate': {'checked_first': True, 'entailment_required': 9,
                          'passed': not drift, 'non_entailment_request_ids': drift, 'rows': sentinels},
        'hypothesis_targets': targets,
        'reference': {'resolved': 46, 'supported': 40, 'unsupported': 6, 'unresolved_ids': sorted(common.UNRESOLVED)},
        'request_count': len(rows), 'Bq0_request_count': 156, 'sentinel_request_count': 9,
        'Bq0_runs': runs, 'binary_flip_count': len(flips), 'binary_flip_ids': flips,
        'any_no_result_count': len(missing), 'any_no_result_ids': missing,
        'all_state_change_count': len(changes), 'all_state_change_ids': changes,
        'flip_definition': '52条中三轮同时出现支持和不支持的陈述数；无结果不归入任一类，另列状态变化与缺失。',
        'unresolved': {sid: [{'round': r, 'judgment': indexed[('Bq0', r, sid)]['judgment'],
                              'original_label': indexed[('Bq0', r, sid)]['original_label'],
                              'parse_failed': indexed[('Bq0', r, sid)]['parse_failed']} for r in (1, 2, 3)]
                       for sid in sorted(common.UNRESOLVED)},
        'parse_failures': {'Bq0': sum(row['parse_failed'] for row in rows if row['arm'] == 'Bq0'),
                           'sentinel': sum(row['parse_failed'] for row in sentinels)},
        'interface_errors': {'Bq0': sum(row['interface_error'] for row in rows if row['arm'] == 'Bq0'),
                             'sentinel': sum(row['interface_error'] for row in sentinels)},
        'returned_models': sorted({row['model'] for row in rows if row['model'] is not None}),
        'missing_model_request_count': sum(row['model'] is None for row in rows),
        'usage_total': usage_total, 'estimated_total_cny': None if unknown_cost_count else str(known_cost),
        'known_usage_estimated_cny': str(known_cost), 'unknown_cost_request_count': unknown_cost_count,
        'requests': request_usage,
        'scope': '仅适用于RefChecker整句核查器的去题面对照；三个目标陈述来自同一道题，非独立样本，最多E级。',
        'interpretation': '支持假设也只说明这3条去题面后改判；其他陈述的变化仅描述，不比较Bq0与A，不改写主实验无可证优势结论。',
    }


def render_report(summary):
    lines = ['# 去题面对照', '', summary['decision'], '',
             f'哨兵先行核对：{summary["sentinel_gate"]["passed"]}；进入假设判定：{summary["hypothesis_evaluated"]}。', '',
             '| 轮次 | 确定集一致 | 人工支持类 | 人工不支持类 | 解析失败 | 接口错误 | 无结果 |',
             '|---|---|---|---|---|---|---|']
    for row in summary['Bq0_runs']:
        lines.append(f'| {row["round"]} | {row["agree"]}/46 | {row["supported_hits"]}/40 | {row["unsupported_hits"]}/6 | '
                     f'{row["parse_failures"]} | {row["interface_errors"]} | {row["no_result"]} |')
    lines += ['', f'二元翻转 {summary["binary_flip_count"]}/52；至少一次无结果 {summary["any_no_result_count"]}/52。',
              summary['flip_definition'], '', '| 未决陈述 | 第1轮 | 第2轮 | 第3轮 |', '|---|---|---|---|']
    for sid, rounds in summary['unresolved'].items():
        labels = [str(row['original_label']) + ' / ' + row['judgment'] for row in rounds]
        lines.append('| ' + sid + ' | ' + ' | '.join(labels) + ' |')
    lines += ['', f'解析失败：{summary["parse_failures"]}；接口错误：{summary["interface_errors"]}。',
              f'实际返回模型名：{summary["returned_models"]}；模型名缺失请求 {summary["missing_model_request_count"]} 条。',
              f'usage合计：{summary["usage_total"]}；逐请求usage见summary.json。',
              f'usage估算总费用：{summary["estimated_total_cny"]} 元；费用未知请求 {summary["unknown_cost_request_count"]} 条。',
              f'已结算估算：{summary["settled_estimated_cny"]} 元；未结算预留：{summary["unsettled_reserved_cny"]} 元。',
              '费用为登记价格与usage的估算，不是供应商账单。', '', summary['scope'], summary['interpretation']]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=runner.OUTPUT / 'results.jsonl')
    parser.add_argument('--output-dir', type=Path, default=runner.OUTPUT)
    args = parser.parse_args(argv)
    runner.configure(offline=True)
    inputs, checks = runner.preflight()
    output_dir = runner.scoped_path(args.output_dir)
    results_path = runner.scoped_path(args.results)
    if not results_path.is_file():
        raise common.StopRun('NO_RESULTS_NO_HYPOTHESIS_CONCLUSION')
    ledger_path = results_path.parent / 'budget-ledger.jsonl'
    if not ledger_path.is_file():
        raise common.StopRun('BUDGET_LEDGER_MISSING')
    summary = analyze_rows(common.read_jsonl(results_path), inputs)
    actual, pending = runner.AblationLedger(ledger_path).totals()
    summary.update(checks, settled_estimated_cny=str(actual), unsettled_reserved_cny=str(pending))
    runner.verify_parent()
    common.write_json(output_dir / 'summary.json', summary)
    (output_dir / 'report.md').write_text(render_report(summary), encoding='utf-8')
    print(summary['decision'])
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (common.StopRun, ImportError, OSError, ValueError) as exc:
        print('STOPPED:', str(exc), file=sys.stderr)
        raise SystemExit(2)
