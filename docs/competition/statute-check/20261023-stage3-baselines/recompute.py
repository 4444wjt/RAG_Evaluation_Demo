"""Recount archived outputs only. Standard library; no classifier or model execution."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

def read(name):
    return json.loads((HERE / name).read_bytes())

def recompute():
    items = read('items.json')
    records = read('records.json')
    by_id = {row['id']: row for row in items}
    cells = {(row['itemId'], row['arm']): row for row in records}
    assert len(by_id) == len(items)
    assert len(cells) == len(records) == 2 * len(items)
    assert set(cells) == {(key, arm) for key in by_id for arm in ('R', 'X')}
    details = {}
    for (key, arm), row in cells.items():
        available = row['status'] == 'ok'
        flagged = None
        if available:
            flagged = (any(f['severity'] == 'error' for f in row['raw']['findings'])
                       if arm == 'R' else row['raw']['flagged'])
            assert type(flagged) is bool
            assert row['result'] == {'verdict': 'contradicted' if flagged else 'supported'}
        else:
            assert row['status'] == 'unavailable' and row['result'] is None
        details[key, arm] = (available, flagged)
    for key in by_id:
        r, x = details[key, 'R'], details[key, 'X']
        available = r[0] and x[0]
        details[key, 'RX'] = (available, (r[1] or x[1]) if available else None)
    layers = {}
    for layer in ('T1', 'T2'):
        selected = [row for row in items if row['layer'] == layer]
        layers[layer] = {}
        for arm in ('R', 'X', 'RX'):
            errors = [i for i in selected if i['expected'] == 'not_supported']
            controls = [i for i in selected if i['expected'] == 'supported']
            layers[layer][arm] = {
                'items': len(selected), 'detectionTotal': len(errors),
                'falsePositiveTotal': len(controls),
                'detected': sum(details[i['id'], arm] == (True, True) for i in errors),
                'falsePositives': sum(details[i['id'], arm] == (True, True) for i in controls),
                'unavailable': sum(not details[i['id'], arm][0] for i in selected)}
    gaps = [i['id'] for i in items if i['layer'] == 'T2' and i['expected'] == 'not_supported'
            and details[i['id'], 'R'] == (True, False) and details[i['id'], 'X'] == (True, True)]
    wrong = [i['id'] for i in items if i['layer'] == 'T2' and (
        not details[i['id'], 'RX'][0] or
        details[i['id'], 'RX'][1] != (i['expected'] == 'not_supported'))]
    method = read('method.json')
    return {
        'byLayer': layers, 'gapIds': sorted(gaps),
        'gapTypes': {kind: sum(by_id[key]['type'] == kind for key in gaps)
                     for kind in sorted({by_id[key]['type'] for key in gaps})},
        'gapBestDoesNotMatchSource': sorted(key for key in gaps if cells[key, 'X']['raw']['best'] != by_id[key]['sourceLabel']),
        'RXequalsX': all(details[key, 'RX'] == details[key, 'X'] for key in by_id),
        'wrongT2Ids': sorted(wrong), 'w': len(wrong),
        'stopPaid': len(wrong) <= method['stopThreshold'],
        'excludedItemCount': len(read('excluded-items.json')),
        'modelCallsInRecordedBaselineRun': 0}

def main():
    for line in (HERE / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        expected, name = line.split('  ', 1)
        target = (HERE / name).resolve()
        assert target.is_relative_to(HERE.resolve()) and target.is_file(), name
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected, name
    actual = recompute()
    assert actual == read('summary.json'), 'Recount differs from archived summary'
    print(json.dumps({'verification': 'passed', **actual}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
