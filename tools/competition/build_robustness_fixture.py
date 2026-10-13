"""从鲁棒性体检归档生成工作台「鲁棒性体检」页的数据，并核对归档。

只读 docs/competition/robustness/<运行>/：核对 SHA256SUMS.txt、授权与汇总所绑定的材料包哈希，
再用归档的材料包与逐条调用记录重新计算汇总，与 summary.json 逐项比对；任何一项对不上就报错，不生成文件。
值得注意的结果按固定规则从记录中找出（打乱、加噪后结论改变，移除依据后仍作答，核对未达预期等），
REMARKS 只为其中个别结果附上作者解读；解读对不上找出的结果时同样报错。不调用模型。

  python tools/competition/build_robustness_fixture.py          写出 frontend/src/fixtures/robustness.json
  python tools/competition/build_robustness_fixture.py --check  只校验已提交的文件是否与归档重新生成的结果一致
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import robustness as rb  # noqa: E402
from backend.core.attribution import is_refusal  # noqa: E402

ARCHIVE = ROOT / 'docs' / 'competition' / 'robustness' / '20261013-formal-run'
OUTPUT = ROOT / 'frontend' / 'src' / 'fixtures' / 'robustness.json'
FILES = ('summary.json', 'items.json', 'package.json', 'authorization.json', 'records.jsonl')
VERDICT_LABELS = {'supported': '支持', 'fact_error': '矛盾', 'fabrication': '无依据'}
REMARKS = {
    'D07:generator:shuffle': '资料片段没有写这段条文的条号，回答却注明「第四条」；核对把这一句标为无依据，标得对。',
    'D07:generator:noise': '加噪后回答把出处写成「第二条」；核对标为矛盾，标得对。打乱与加噪都会诱发编造出处。',
    'F01:generator:shuffle': '原样资料下拒答的错误前提题，只打乱资料顺序就改为依据原文纠正前提：这类题的拒答对资料顺序敏感，与失败归因「提示词只给了拒答出口」一致。',
    'F05:generator:shuffle': '同上：打乱顺序后改为正确纠正前提。',
    'F02:generator:noise': '回答本身正确；核对把「……这一说法不对」判为矛盾，是否定叠加句上的误判。',
    'D01:generator:remove': '依据被移除后，模型把第三条对存款保险的定义当成「目的」作答；核对按字面判为有依据。逐句核对只查有无依据，不查是否答对了题。',
    'D09:judge:counterfactual': '改动落在修饰语上（应当交纳的保费 → 无需交纳的保费），核对引用了改动后的原文仍判为支持。这一改动本身语义别扭，如实计为未标出。',
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def verify_archive(archive: Path) -> dict:
    expected = {}
    for line in (archive / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        expected[name] = digest
    actual = {p.name for p in archive.iterdir() if p.is_file() and p.name != 'SHA256SUMS.txt'}
    if set(expected) != actual or set(FILES) - actual:
        raise ValueError('鲁棒性体检归档的文件集合与 SHA256SUMS.txt 不符。')
    for name, digest in expected.items():
        if sha256(archive / name) != digest:
            raise ValueError(f'鲁棒性体检归档文件 {name} 的哈希与 SHA256SUMS.txt 不一致。')
    return expected


def _statements(trace) -> list:
    return [{'text': s['text'], 'verdict': VERDICT_LABELS[s['verdict']], 'evidence': s['evidence']}
            for s in (trace or {}).get('statements') or []]


def _code(outcome: dict | None, side: str) -> str | None:
    """矩阵单元格：生成侧 answered / refused / flagged / failed，核对侧 clean / flagged / empty / failed。"""
    if outcome is None:
        return None
    if side == 'generator':
        if not outcome['answered']:
            return 'failed'
        if outcome['refused']:
            return 'refused'
        return 'flagged' if outcome['trace'] == 'flagged' else 'failed' if outcome['trace'] == 'failed' else 'answered'
    return outcome['trace']


def notable(rows: list) -> list:
    """按固定规则找出值得注意的结果，返回 (记录编号, 类别)。"""
    found = []
    for row in rows:
        generator, judge = row['generator'], row['judge']
        base = _code(generator.get('original'), 'generator')
        for condition in ('shuffle', 'noise'):
            code = _code(generator.get(condition), 'generator')
            if base and code and code != base and 'failed' not in (base, code):
                kind = {'answered': '改为作答', 'refused': '改为拒答', 'flagged': '核对新标出'}[code]
                found.append((f"{row['id']}:generator:{condition}", kind))
        if _code(generator.get('remove'), 'generator') in ('answered', 'flagged'):
            found.append((f"{row['id']}:generator:remove", '移除依据后仍作答'))
        if (generator.get('counterfactual') or {}).get('adoption') in ('kept', 'both'):
            found.append((f"{row['id']}:generator:counterfactual", '沿用原值'))
        original = judge.get('original')
        if original and original['trace'] != 'failed':
            flagged = original['trace'] == 'flagged'
            for condition in ('shuffle', 'noise'):
                outcome = judge.get(condition)
                if outcome and outcome['trace'] != 'failed' and (outcome['trace'] == 'flagged') != flagged:
                    found.append((f"{row['id']}:judge:{condition}", '核对结论改变'))
            for condition in ('remove', 'counterfactual'):
                outcome = judge.get(condition)
                if not flagged and outcome and outcome['trace'] not in ('failed', 'flagged'):
                    found.append((f"{row['id']}:judge:{condition}", '核对未标出'))
    return found


def build(archive: Path = ARCHIVE) -> dict:
    hashes = verify_archive(archive)
    summary, items = read_json(archive / 'summary.json'), read_json(archive / 'items.json')
    package, auth = read_json(archive / 'package.json'), read_json(archive / 'authorization.json')
    records = [json.loads(line) for line in (archive / 'records.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    package_sha = hashes['package.json']
    if not (auth['packageSha256'] == summary['packageSha256'] == items['packageSha256'] == package_sha):
        raise ValueError('授权、汇总与逐题结果绑定的材料包哈希不一致。')
    recomputed = rb.summarize(package['items'], records, is_refusal)
    rows = recomputed.pop('rows')
    if any(summary.get(key) != value for key, value in recomputed.items()) or items['rows'] != rows:
        raise ValueError('鲁棒性体检汇总不能由材料包与调用记录重新算出。')
    if summary['calls']['paid'] > auth['maxPaidCalls'] or summary['usage']['estimatedCostCny'] > auth['budgetCny']:
        raise ValueError('实际调用超出授权上限。')

    by_id = {record['id']: record for record in records}
    package_items = {item['id']: item for item in package['items']}
    found = notable(rows)
    unknown = set(REMARKS) - {key for key, _ in found}
    if unknown:
        raise ValueError(f'作者解读对应不到值得注意的结果：{sorted(unknown)}')
    notes = []
    for key, kind in found:
        item_id, side, condition = key.split(':')
        record, item = by_id[key], package_items[item_id]
        note = {'id': key, 'item': item_id, 'side': side, 'condition': condition, 'kind': kind,
                'question': item['question'],
                'answer': record.get('answer') if side == 'generator' else item['answer'],
                'statements': _statements(record.get('trace')), 'remark': REMARKS.get(key)}
        if condition == 'counterfactual':
            note['edits'] = item['conditions']['counterfactual']['edits']
        notes.append(note)

    matrix = []
    for row in rows:
        item = package_items[row['id']]
        conditions = item['conditions']
        matrix.append({
            'id': row['id'], 'question': row['question'], 'answerIsRefusal': item['answerIsRefusal'],
            'generator': {c: _code(row['generator'].get(c), 'generator') for c in rb.CONDITIONS},
            'judge': {c: _code(row['judge'].get(c), 'judge') for c in rb.CONDITIONS},
            'adoption': (row['generator'].get('counterfactual') or {}).get('adoption'),
            'skipped': {c: conditions[c]['skipped'] for c in rb.CONDITIONS if 'skipped' in conditions[c]},
            'edits': [f"{e['before']}→{e['after']}" for e in conditions['counterfactual'].get('edits', [])],
        })
    return {
        'source': {'archive': _rel(archive), 'sha256': hashes, 'input': summary['input'],
                   'authorizedAt': auth['authorizedAt'], 'budgetCny': auth['budgetCny'],
                   'judgeModel': summary['judgeModel'], 'returnedModels': summary['returnedModels'],
                   'generator': summary['generator']},
        'parameters': summary['parameters'], 'jobs': summary['jobs'], 'calls': summary['calls'], 'usage': summary['usage'],
        'items': summary['items'], 'built': summary['built'], 'skipped': summary['skipped'], 'edits': summary['edits'],
        'judge': summary['judge'], 'generator': summary['generator'],
        'matrix': matrix, 'notes': notes,
    }


def write(fixture: dict, output: Path = OUTPUT) -> None:
    output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='只校验，不写文件')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    fixture = build()
    if args.check:
        if read_json(OUTPUT) != fixture:
            print('robustness.json 与归档重新生成的结果不一致。', file=sys.stderr)
            return 1
        print('一致。')
        return 0
    write(fixture)
    print(f'已写出 {_rel(OUTPUT)}：{fixture["items"]} 题，{len(fixture["notes"])} 条值得注意的结果。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
