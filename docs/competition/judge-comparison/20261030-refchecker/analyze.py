"""Offline analysis of T2 results. T1 tests call analyze_rows with synthetic data only."""
import argparse
from collections import Counter
from decimal import Decimal
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_common import (TASK, UNRESOLVED, BudgetLedger, StopRun, configure, load_inputs,
                            read_jsonl, validate_reference, verify_inputs, write_json)


def analyze_rows(rows, inputs):
    reference = validate_reference(*inputs)  # Must precede any metric calculation.
    ids = {item['id'] for item in inputs[0]['items']}
    expected = {(arm, r, sid) for arm in ('A', 'B') for r in (1, 2, 3) for sid in ids}
    indexed = {}
    requests = {}
    for row in rows:
        key = (row['arm'], row['round'], row['statement_id'])
        if key in indexed or key not in expected:
            raise StopRun('DUPLICATE_OR_UNREGISTERED_RESULT')
        if row['judgment'] not in ('支持', '不支持', '无结果'):
            raise StopRun('INVALID_RESULT_LABEL')
        if row['parse_failed'] and row['judgment'] != '无结果':
            raise StopRun('PARSE_FAILURE_MUST_BE_NO_RESULT')
        indexed[key] = row
        request = {'request_id': row['request_id'], 'arm': row['arm'], 'round': row['round'],
                   'model': row['model'], 'usage': row['usage'], 'interface_error': row['interface_error']}
        old = requests.setdefault(row['request_id'], request)
        if old != request:
            raise StopRun('INCONSISTENT_REQUEST_METADATA')
    if set(indexed) != expected:
        raise StopRun('INCOMPLETE_RESULTS_NO_COMPARATIVE_CONCLUSION')
    counts = Counter((request['arm'], request['round']) for request in requests.values())
    if any(counts[(arm, r)] != (20 if arm == 'A' else 52) for arm in ('A', 'B') for r in (1, 2, 3)):
        raise StopRun('REQUEST_COUNT_MISMATCH')
    arms = {}
    for arm in ('A', 'B'):
        runs = []
        for r in (1, 2, 3):
            run = [indexed[(arm, r, sid)] for sid in sorted(ids)]
            hits = {label: sum(indexed[(arm, r, sid)]['judgment'] == ('支持' if label else '不支持')
                               for sid, value in reference.items() if value == label) for label in (1, 0)}
            runs.append({'round': r, 'agree': hits[1] + hits[0], 'denominator': 46,
                         'supported_hits': hits[1], 'supported_denominator': 40,
                         'unsupported_hits': hits[0], 'unsupported_denominator': 6,
                         'parse_failures': sum(row['parse_failed'] for row in run),
                         'no_result': sum(row['judgment'] == '无结果' for row in run),
                         'no_result_on_resolved': sum(indexed[(arm, r, sid)]['judgment'] == '无结果' for sid in reference)})
        flips = []
        missing = []
        state_changes = []
        for sid in sorted(ids):
            values = {indexed[(arm, r, sid)]['judgment'] for r in (1, 2, 3)}
            if {'支持', '不支持'}.issubset(values):
                flips.append(sid)
            if '无结果' in values:
                missing.append(sid)
            if len(values) > 1:
                state_changes.append(sid)
        arms[arm] = {'runs': runs, 'agree_sum': sum(row['agree'] for row in runs),
                     'mean_agree': str(sum(row['agree'] for row in runs)) + '/3',
                     'worst_agree': min(row['agree'] for row in runs),
                     'binary_flip_count': len(flips), 'binary_flip_ids': flips,
                     'any_no_result_count': len(missing), 'any_no_result_ids': missing,
                     'all_state_change_count': len(state_changes), 'all_state_change_ids': state_changes,
                     'unresolved': {sid: [{'round': r, 'judgment': indexed[(arm, r, sid)]['judgment'],
                                          'original_label': indexed[(arm, r, sid)]['original_label'],
                                          'parse_failed': indexed[(arm, r, sid)]['parse_failed']} for r in (1, 2, 3)]
                                    for sid in sorted(UNRESOLVED)}}
    winner = None
    for a, b in (('A', 'B'), ('B', 'A')):
        if arms[a]['agree_sum'] > arms[b]['agree_sum'] and arms[a]['worst_agree'] > arms[b]['worst_agree']:
            winner = a
    totals = None
    all_usage = [request['usage'] for request in requests.values()]
    keys = ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens')
    if all(isinstance(usage, dict) and all(type(usage.get(key)) is int for key in keys) for usage in all_usage):
        totals = {key: sum(usage[key] for usage in all_usage) for key in keys}
    return {'reference': {'resolved': 46, 'supported': 40, 'unsupported': 6, 'unresolved_ids': sorted(UNRESOLVED)},
            'result_rows': len(rows), 'request_count': len(requests), 'arms': arms, 'winner': winner,
            'decision': f'{winner} 臂有可证优势' if winner else '无可证优势',
            'returned_models': sorted({r['model'] for r in requests.values() if r['model'] is not None}),
            'missing_model_request_count': sum(r['model'] is None for r in requests.values()),
            'usage_total': totals, 'requests': list(requests.values()),
            'flip_definition': '52条中三轮同时出现支持和不支持的陈述数；无结果不归入任一类，另列状态变化与缺失条数。',
            'scope': 'B为RefChecker 0.2.17整句核查器，不是RAGChecker默认三元组联合配置；只比较核查步骤。'}


def render_report(summary):
    lines = ['# 核查器同条件对照', '', summary['decision'], '', summary['scope'], '',
             '| 臂 | 轮次 | 人工一致 | 支持命中 | 不支持命中 | 解析失败 | 无结果 |',
             '|---|---|---|---|---|---|---|']
    for arm in ('A', 'B'):
        for row in summary['arms'][arm]['runs']:
            lines.append(f'| {arm} | {row["round"]} | {row["agree"]}/46 | {row["supported_hits"]}/40 | {row["unsupported_hits"]}/6 | {row["parse_failures"]} | {row["no_result"]} |')
        result = summary['arms'][arm]
        lines += ['', f'{arm}：一致条数均值 {result["mean_agree"]}，最少 {result["worst_agree"]}/46；'
                  f'二元翻转 {result["binary_flip_count"]}/52，至少一次无结果 {result["any_no_result_count"]}/52。']
    lines += ['', '6条未决陈述逐轮原判定、实际返回模型名及逐请求usage见 summary.json。', '',
              '不支持类只有6条，其差异仅作描述。单法规节选、单模型、单时段；英文提示用于中文输入。'
              '人工独立性仅本人勾选，未经第三方核实；无非开发者复现，证据等级最多E。', '',
              '费用为usage与登记价格的估算，不是供应商账单。', '',
              f'已结算估算费用：{summary["settled_estimated_cny"]} 元；未结算预留：{summary["unsettled_reserved_cny"]} 元。']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=TASK / 'results.jsonl')
    args = parser.parse_args()
    configure(offline=True)
    verify_inputs()
    inputs = load_inputs()
    validate_reference(*inputs)
    summary = analyze_rows(read_jsonl(args.results), inputs)
    if not (TASK / 'budget-ledger.jsonl').is_file():
        raise StopRun('BUDGET_LEDGER_MISSING')
    actual, pending = BudgetLedger(TASK / 'budget-ledger.jsonl').totals()
    summary.update(settled_estimated_cny=str(actual), unsettled_reserved_cny=str(pending))
    write_json(TASK / 'summary.json', summary)
    (TASK / 'report.md').write_text(render_report(summary), encoding='utf-8')
    print(summary['decision'])


if __name__ == '__main__':
    try:
        main()
    except (StopRun, FileNotFoundError) as exc:
        print('STOPPED:', str(exc), file=sys.stderr)
        raise SystemExit(2)
