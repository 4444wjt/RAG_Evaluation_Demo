"""Read archived data only; standard library, no classifier or model execution."""
import hashlib
import json
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from math import ceil, comb, floor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ('U-C0', 'L-C0', 'U-C1', 'L-C1')


def read(name):
    return json.loads((HERE / name).read_bytes())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def compare(actual, expected, path='$'):
    """Compare every leaf, including types; never silently ignore extra fields."""
    require(type(actual) is type(expected), path + ': type differs')
    if isinstance(actual, dict):
        require(actual.keys() == expected.keys(), path + ': keys differ')
        for key in actual:
            compare(actual[key], expected[key], path + '.' + key)
    elif isinstance(actual, list):
        require(len(actual) == len(expected), path + ': length differs')
        for i, (a, e) in enumerate(zip(actual, expected)):
            compare(a, e, path + '[' + str(i) + ']')
    else:
        require(actual == expected, path + ': value differs')


def recompute():
    items, records, cost = read('items.json'), read('records.json'), read('cost.json')
    ids = {i['id']: i for i in items}
    cells = {(r['itemId'], r['arm']): r for r in records}
    require(len(ids) == len(items) == 49, 'items not unique/49')
    require(len(cells) == len(records) == 196, 'records not unique/196')
    require(set(cells) == {(k, a) for k in ids for a in ARMS}, 'incomplete matrix')
    require(all(i['expected'] in ('supported', 'not_supported') for i in items), 'expected')
    errors = [i for i in items if i['expected'] == 'not_supported']
    controls = [i for i in items if i['expected'] == 'supported']
    available, flagged = {}, {}
    for key, r in cells.items():
        available[key] = r['status'] == 'ok' and r['result'] is not None
        if available[key]:
            require(r['result']['verdict'] in ('supported', 'contradicted', 'insufficient'), 'verdict')
        flagged[key] = available[key] and r['result']['verdict'] != 'supported'

    def stats(selected, arm, offline=False):
        def state(i):
            if offline:
                v = i['offlineBaselines'][arm]['flagged']
                require(type(v) is bool, 'offline flagged')
                return True, v
            return available[i['id'], arm], flagged[i['id'], arm]
        return {'items': len(selected),
                'detected': sum(i['expected'] == 'not_supported' and state(i)[1] for i in selected),
                'falsePositives': sum(i['expected'] == 'supported' and state(i)[1] for i in selected),
                'unavailable': sum(not state(i)[0] for i in selected),
                'unavailableIds': [i['id'] for i in selected if not state(i)[0]]}

    def kind(i):
        if i['type'] == 'control_article':
            return 'articleOnly'
        return 'singleParagraphFirst' if i['citedArticleParagraphs'] == 1 else 'multiParagraphPinpoint'

    arms = {}
    for arm in ARMS:
        arms[arm] = {**stats(items, arm), 'detectionTotal': len(errors),
                     'falsePositiveTotal': len(controls),
                     'byType': {t: stats([i for i in items if i['type'] == t], arm)
                                for t in dict.fromkeys(i['type'] for i in items)},
                     'falsePositivesByControlKind': {
                         k: stats([i for i in controls if kind(i) == k], arm)
                         for k in ('singleParagraphFirst', 'multiParagraphPinpoint', 'articleOnly')}}

    def paired(u, l):
        b = [i['id'] for i in errors if available[i['id'], u] and available[i['id'], l]
             and flagged[i['id'], l] and not flagged[i['id'], u]]
        c = [i['id'] for i in errors if available[i['id'], u] and available[i['id'], l]
             and flagged[i['id'], u] and not flagged[i['id'], l]]
        n = len(b) + len(c)
        p = Fraction(sum(comb(n, k) for k in range(len(b), n + 1)), 2 ** n)
        return {'b': len(b), 'c': len(c), 'd': len(b) - len(c),
                'p': {'fraction': str(p), 'decimal': format(float(p), '.6f')},
                'bIds': b, 'cIds': c,
                'unavailableErrorIds': [i['id'] for i in errors
                                       if not available[i['id'], u] or not available[i['id'], l]]}

    thresholds = {'simpleSufficientMinDetected': ceil(Fraction(9, 10) * len(errors)),
                  'simpleSufficientMaxFalsePositives': floor(Fraction(1, 10) * len(controls)),
                  'labelGainMinDifference': 5, 'labelGainMaxP': '1/20',
                  'labelGainFalsePositiveTolerance': len(controls) // 9}
    primary = paired('U-C1', 'L-C1')
    sufficient = (arms['U-C1']['detected'] >= thresholds['simpleSufficientMinDetected']
                  and arms['U-C1']['falsePositives'] <= thresholds['simpleSufficientMaxFalsePositives'])
    gain = (primary['d'] >= thresholds['labelGainMinDifference']
            and Fraction(primary['p']['fraction']) <= Fraction(thresholds['labelGainMaxP'])
            and arms['L-C1']['falsePositives'] <= arms['U-C1']['falsePositives'] + thresholds['labelGainFalsePositiveTolerance'])
    no_gain = primary['d'] <= 0
    decision = ('simple_sufficient' if sufficient else 'label_gain' if gain else
                'no_gain' if no_gain else 'insufficient_evidence')
    primary.update({'decision': decision,
                    **{a: {k: arms[a][k] for k in ('detected', 'falsePositives', 'unavailable')}
                       for a in ('U-C1', 'L-C1')},
                    'containsUnavailable': any(arms[a]['unavailable'] for a in ('U-C1', 'L-C1')),
                    'note': None})
    secondary = {**paired('U-C0', 'L-C0'),
                 'falsePositives': {a: arms[a]['falsePositives'] for a in ('U-C0', 'L-C0')},
                 'note': '只报告，不判定'}
    effects = {name: {k: arms[l][k] - arms[u][k] for k in ('detected', 'falsePositives')}
               for name, u, l in [('criterionU', 'U-C0', 'U-C1'), ('criterionL', 'L-C0', 'L-C1'),
                                  ('labelC0', 'U-C0', 'L-C0'), ('labelC1', 'U-C1', 'L-C1')]}
    offline = {a: {**stats(items, a, True), 'byType': {
        t: stats([i for i in items if i['type'] == t], a, True)
        for t in dict.fromkeys(i['type'] for i in items)}} for a in ('rule', 'lexical')}
    offline['note'] = '规则与字面基线只作参照，不是真值，不作优越性论据；真值来自构造。'
    arm_cost = {}
    for a in ARMS:
        rows = [r for r in records if r['arm'] == a]
        require(all(r['usage'] is not None for r in rows), 'missing usage')
        arm_cost[a] = {'requests': len(rows),
                       'meanMessageBytes': round(sum(r['messageBytes'] for r in rows) / len(rows), 1),
                       'meanPromptTokens': round(sum(r['usage']['prompt_tokens'] for r in rows) / len(rows), 1),
                       'meanCompletionTokens': round(sum(r['usage']['completion_tokens'] for r in rows) / len(rows), 1),
                       'estimatedCostCny': format(sum((Decimal(r['charge']['costCny']) for r in rows), Decimal(0)), '.6f')}
    for a in ARMS:
        compare(arm_cost[a], cost['arms'][a], '$.cost.arms.' + a)
    estimated = format(sum((Decimal(r['charge']['costCny']) for r in records), Decimal(0)), '.6f')
    compare(estimated, cost['estimatedCostCny'], '$.cost.estimatedCostCny')
    deployment = sum(r['activeSeconds'] for r in cost['humanMinutes']['attempts'] if r['step'] in ('结构还原', '核对标签'))
    compare(deployment, cost['deploymentHumanSeconds'], '$.cost.deploymentHumanSeconds')
    from datetime import datetime
    elapsed = (datetime.fromisoformat(cost['machine']['lastRequestEndAt']) - datetime.fromisoformat(cost['machine']['firstRequestStartAt'])).total_seconds()
    compare(elapsed, cost['machine']['elapsedSeconds'], '$.cost.machine.elapsedSeconds')
    delta = {k: round(arm_cost['L-C1'][k] - arm_cost['U-C1'][k], 1)
             for k in ('meanMessageBytes', 'meanPromptTokens', 'meanCompletionTokens')}
    delta['estimatedCostCny'] = format(Decimal(arm_cost['L-C1']['estimatedCostCny']) - Decimal(arm_cost['U-C1']['estimatedCostCny']), '.6f')
    verdicts = {a: dict(sorted(Counter(r['result']['verdict'] for r in records
                                     if r['arm'] == a and available[r['itemId'], a]).items())) for a in ARMS}
    return {'totals': {'errors': len(errors), 'controls': len(controls)}, 'thresholds': thresholds,
            'arms': arms, 'verdictCounts': verdicts, 'primary': primary,
            'primaryDecisionTable': {'simpleSufficient': sufficient, 'labelGain': gain,
                                     'noGain': no_gain, 'insufficientEvidence': not (sufficient or gain or no_gain)},
            'secondaryLabelC0': secondary, 'effects': effects,
            'labeledVerdictsIdentical': sum(available[k, 'L-C0'] and available[k, 'L-C1']
                                            and cells[k, 'L-C0']['result']['verdict'] == cells[k, 'L-C1']['result']['verdict'] for k in ids),
            'labeledVerdictPairs': len(items), 'offlineBaselines': offline,
            'cost': {'arms': arm_cost, 'estimatedCostCny': estimated,
                     'actualBillCny': cost['actualBillCny'], 'attributable': cost['attributable'],
                     'deploymentHumanSeconds': deployment, 'machineSecondsRounded': round(elapsed),
                     'startedRequests': len(records), 'failedOrIncomplete': sum(not v for v in available.values()),
                     'retries': cost['retries'], 'contextBytes': cost['contextBytes'], 'labelC1Delta': delta}}


def main():
    entries = {}
    for line in (HERE / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        p = (HERE / name).resolve()
        require(name not in entries and p.is_relative_to(HERE.resolve()) and p.is_file(), 'manifest path')
        require(hashlib.sha256(p.read_bytes()).hexdigest() == digest, 'hash mismatch: ' + name)
        entries[name] = digest
    expected_files = {p.relative_to(HERE).as_posix() for p in HERE.rglob('*') if p.is_file()
                      and p.name not in ('README.md', 'SHA256SUMS.txt')}
    require(set(entries) == expected_files, 'manifest incomplete')
    compare(recompute(), read('summary.json'))
    print(json.dumps({'verification': 'passed', 'hashes': len(entries), 'items': 49,
                      'records': 196, 'summary': 'all fields matched'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
