"""Standard-library recount. Schema flags are archived observations, not revalidation.

Raw stdout/stderr are not shipped. This cannot independently prove schema validity,
nonempty output, absence of uncaught exceptions, or historical run completion.
"""
from collections import Counter
import json
from pathlib import Path
import sys


def main():
    base = Path(__file__).resolve().parent
    read = lambda name: json.loads((base / name).read_bytes())
    records, data, analysis = read('records.json'), read('items.json'), read('analysis.json')
    comparisons = []

    def compare(name, actual, expected):
        if isinstance(expected, dict):
            comparisons.append({'field': name + '.keys', 'passed': isinstance(actual, dict) and set(actual) == set(expected)})
            for key, value in expected.items():
                compare(name + '.' + key, actual.get(key) if isinstance(actual, dict) else None, value)
        elif isinstance(expected, list):
            comparisons.append({'field': name, 'passed': actual == expected and isinstance(actual, list)})
        else:
            comparisons.append({'field': name, 'passed': type(actual) is type(expected) and actual == expected})

    expected_jobs = data['jobs'] + [dict(j, id='extended--' + j['itemId'], group='extended')
                                  for j in data['jobs'] if j['group'] == 'base']
    jobs = {j['id']: j for j in expected_jobs}
    compare('recordCount', len(records), len(expected_jobs))
    compare('recordIds', sorted(r['id'] for r in records), sorted(jobs))
    for row in records:
        job = jobs[row['id']]
        for key in ('itemId', 'group', 'expectedStatus'):
            compare('record.' + row['id'] + '.' + key, row[key], job[key])
        compare('record.' + row['id'] + '.engine', row['engine'], 'extended' if row['group'] == 'extended' else 'base')

    statuses = {group: {r['itemId']: r['status'] for r in records if r['group'] == group}
                for group in ('base', 'extended')}

    def tally(items, group):
        states = statuses[group]
        errors = [i for i in items if i['expected'] == 'not_supported']
        controls = [i for i in items if i['expected'] == 'supported']
        return {'E': len(errors), 'K': len(controls),
                'detected': sum(states.get(i['id']) == 'findings' for i in errors),
                'falsePositives': sum(states.get(i['id']) == 'findings' for i in controls),
                'unavailableIds': [i['id'] for i in items if states.get(i['id']) not in ('findings', 'no_error_found')],
                'statuses': dict(sorted(Counter(states.get(i['id']) or 'missing' for i in items).items()))}

    items = data['items']
    counted = tally(items, 'base')
    bounds = {'minDetected': (9 * counted['E'] + 9) // 10, 'maxFalsePositives': counted['K'] // 10}
    h1 = {**counted, 'passed': counted['detected'] >= bounds['minDetected'] and counted['falsePositives'] <= bounds['maxFalsePositives'],
          'missedErrorIds': [i['id'] for i in items if i['expected'] == 'not_supported' and statuses['base'].get(i['id']) != 'findings'],
          'falsePositiveIds': [i['id'] for i in items if i['expected'] == 'supported' and statuses['base'].get(i['id']) == 'findings']}
    for key in ('E', 'K'):
        compare(key, counted[key], analysis[key])
    compare('thresholds', bounds, analysis['thresholds'])
    compare('excludedIds', [i['id'] for i in data['excluded']], analysis['excludedIds'])
    compare('H1', h1, analysis['H1'])
    variants, failures = {}, []
    for n in range(1, 11):
        group = 'F' + str(n)
        rows = []
        for row in records:
            if row['group'] != group:
                continue
            passed = row['status'] == row['expectedStatus'] and row['status'] in ('review', 'invalid_input') and row['returncode'] == 2
            rows.append({'id': row['id'], 'status': row['status'], 'passed': passed})
            if not passed:
                failures.append(row['id'])
        variants[group] = {'denominator': len(items), 'applicable': len(rows),
                           'matched': sum(r['passed'] for r in rows), 'rows': rows,
                           'notApplicableIds': data['notApplicable'].get(group, [])}
    # Preserve original run ordering for failureIds.
    failures = [r['id'] for r in records if r['id'] in set(failures)]
    compare('H2', {'passed': not failures, 'failureIds': failures, 'variants': variants}, analysis['H2'])
    codes = {'no_error_found': 0, 'findings': 1, 'review': 2, 'invalid_input': 2}
    contract = []
    for row in records:
        problems = []
        if not row['schemaValid']:
            problems.append('schema_flag_false')
        if not row['stderrEmpty']:
            problems.append('stderr_not_empty')
        if row['status'] not in codes or row['returncode'] != codes[row['status']]:
            problems.append('exit_status_mismatch')
        if problems:
            contract.append({'id': row['id'], 'problems': problems})
    compare('H3.recordDerived', {'checked': len(records), 'passed': not contract, 'failures': contract}, analysis['H3'])
    report = analysis['reportOnly']
    compare('reportOnly.extended', tally(items, 'extended'), report['extended'])
    for group, key in [('base', 'baseByType'), ('extended', 'extendedByType')]:
        compare('reportOnly.' + key, {kind: tally([i for i in items if i['type'] == kind], group)
                                     for kind in sorted({i['type'] for i in items})}, report[key])
    review = Counter(code for r in records if r['status'] == 'review' for code in r['diagnosticCodes'])
    compare('reportOnly.reviewDiagnosticCodes', dict(sorted(review.items())), report['reviewDiagnosticCodes'])
    result = {'checks': len(comparisons), 'passed': sum(c['passed'] for c in comparisons),
              'comparisons': comparisons,
              'notRecomputed': ['raw output schema validation', 'empty stdout', 'uncaught exceptions', 'historical completion',
                                'author timing/corrections', 'limitations', 'conclusion']}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if all(c['passed'] for c in comparisons) else 1


if __name__ == '__main__':
    sys.exit(main())
