"""从两次 25 题正式运行与留出题的冻结证据，生成工作台「修复验证」页的逐题对照数据。

10-05 正式运行门禁 FAIL，10-14 加一句错误前提纠正指引后按相同条件重跑，门禁 PASS。
本脚本只读证据，不调用模型、不改门禁，对照数据全部由归档重新算出：

  - 先按各归档的 SHA256SUMS.txt 核对用到的文件；
  - 两次请求除版本标签外必须相同，25 题的问题、参考答案与检索资料必须逐题相同；
  - 两次运行之间改了哪些源文件，按两份运行清单记录的哈希列出，不只说「改了一句」；
  - 正向题均值按逐题分数重算，须与归档的 pos_faithfulness / pos_context_precision 一致；
  - 门禁用正式的 check_quality_gate 离线回放，须与归档的 gate_passed 一致；
  - 逐题回归对比用平台自带的 regression.compare，结论确定性用 gate_uncertainty 的按题 bootstrap；
  - 留出题用 heldout_fix.analyze 由调用记录重算，须与归档的 summary.json、items.json 一致。

任何一项对不上就报错退出，不生成文件。

  python tools/competition/build_fix_fixture.py            写出 frontend/src/fixtures/fix-verification.json
  python tools/competition/build_fix_fixture.py --check    只校验已提交的文件是否与证据重新生成的结果一致
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import regression as reg  # noqa: E402
from backend.core.attribution import is_refusal  # noqa: E402
from scripts import gate_replay  # noqa: E402
from tools.competition import gate_uncertainty as uncertainty  # noqa: E402
from tools.competition import heldout_fix  # noqa: E402
from tools.competition.run_evidence import sha256, write_json  # noqa: E402

EVIDENCE = ROOT / 'docs' / 'competition' / 'evidence'
BASELINE = EVIDENCE / '20261005T144504Z-full-39511424'
FIXED = EVIDENCE / '20261014T073859Z-full-1c6f091f'
JUDGE = EVIDENCE / '20261014T125415Z-full-a5002d4f'
HELDOUT = ROOT / 'docs' / 'competition' / 'heldout-fix' / '20261014-deepseek'
OUTPUT = ROOT / 'frontend' / 'src' / 'fixtures' / 'fix-verification.json'
MODEL = 'deepseek-chat'
FILES = {
    'baseline': (BASELINE, ('api-response.json', 'request.json', 'analysis.json', 'manifest.json', 'prompt-definitions.json')),
    'fixed': (FIXED, ('api-response.json', 'request.json', 'manifest.json', 'prompt-definitions.json')),
    'judge': (JUDGE, ('api-response.json', 'request.json')),
    'heldout': (HELDOUT, ('summary.json', 'items.json', 'package.json', 'records.jsonl')),
}
CATEGORIES = {'direct': '直接问答', 'conditional': '条件判断', 'missing': '资料无答案', 'false_premise': '错误前提'}
HELDOUT_CATEGORIES = {'false_premise': '错误前提', 'true_premise': '正确前提', 'missing': '资料无答案'}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def verify_hashes() -> dict:
    """核对用到的每个文件都与所在归档的 SHA256SUMS.txt 记录一致。"""
    hashes = {}
    for key, (folder, names) in FILES.items():
        lines = (folder / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines()
        expected = {name: digest for digest, name in (line.split(maxsplit=1) for line in lines if line.strip())}
        hashes[key] = {name: sha256(folder / name) for name in names}
        if any(expected.get(name) != digest for name, digest in hashes[key].items()):
            raise ValueError(f'{folder.name} 的证据文件与 SHA256SUMS.txt 不一致。')
    return hashes


def _trace(row: dict) -> dict:
    raw = row.get('hallucination_trace')
    trace = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    verdicts = [s.get('verdict') for s in trace.get('statements') or []]
    return {'status': trace.get('trace_status'), 'statements': len(verdicts), 'supported': verdicts.count('supported'),
            'factErrors': verdicts.count('fact_error'), 'fabrications': verdicts.count('fabrication')}


def _side(row: dict) -> dict:
    return {'answer': row['answer'], 'refusal': is_refusal(row['answer']),
            'faithfulness': _round(row['faithfulness']), 'contextPrecision': _round(row['context_precision']),
            'trace': _trace(row)}


def change(case_type: str, before: dict, after: dict, same_answer: bool) -> str:
    """逐题变化，规则固定：先看是否拒答，再看正向题的忠实度。"""
    if case_type == 'negative':
        return 'kept_refusal' if before['refusal'] and after['refusal'] else 'answered_missing'
    if before['refusal'] and not after['refusal']:
        return 'corrected'
    if before['refusal'] and after['refusal']:
        return 'still_refused'
    if not before['refusal'] and after['refusal']:
        return 'newly_refused'
    delta = after['faithfulness'] - before['faithfulness']
    if delta == 0:
        return 'same'
    if same_answer:
        return 'judge_only'
    return 'up' if delta > 0 else 'down'


def _gate(response: dict, request: dict, rows: list[dict]) -> dict:
    summary = next(s for s in response['summary'] if s['model'] == MODEL)
    positive = [r for r in rows if r['case_type'] == 'positive']
    faith = sum(r['faithfulness'] for r in positive) / len(positive)
    ctx = sum(r['context_precision'] for r in positive) / len(positive)
    if round(faith, 4) != summary['pos_faithfulness'] or round(ctx, 4) != summary['pos_context_precision']:
        raise ValueError('正向题均值不能由逐题分数重新算出。')
    with contextlib.redirect_stdout(io.StringIO()):
        replayed = gate_replay.replay(response, request['faith_threshold'], request['ctx_prec_threshold'])['gate_passed']
    if replayed is not response['gate_passed']:
        raise ValueError('门禁回放结果与归档的 gate_passed 不一致。')
    interval = uncertainty.bootstrap([r['faithfulness'] for r in positive])
    return {'passed': response['gate_passed'], 'replayed': replayed,
            'posFaithfulness': summary['pos_faithfulness'], 'posContextPrecision': summary['pos_context_precision'],
            'refusalAccuracy': summary['refusal_accuracy'], 'answerCorrectness': summary['answer_correctness'],
            'certainty': {'low': _round(interval['low']), 'high': _round(interval['high']),
                          'state': uncertainty.three_state(interval['low'], interval['high'], request['faith_threshold'])}}


def formal_runs() -> dict:
    base_response, fixed_response = _read(BASELINE / 'api-response.json'), _read(FIXED / 'api-response.json')
    base_request, fixed_request = _read(BASELINE / 'request.json'), _read(FIXED / 'request.json')
    compared = sorted((set(base_request) | set(fixed_request)) - {'version_label'})
    if any(base_request.get(key) != fixed_request.get(key) for key in compared):
        raise ValueError('两次运行的请求参数不同，不能逐题对照。')
    records = _read(BASELINE / 'analysis.json')['records']
    base_rows, fixed_rows = base_response['model_dfs'][MODEL], fixed_response['model_dfs'][MODEL]
    if not len(records) == len(base_rows) == len(fixed_rows):
        raise ValueError('两次运行的题数不同。')

    rows = []
    for index, (record, before_row, after_row) in enumerate(zip(records, base_rows, fixed_rows)):
        if record['rowIndex'] != index:
            raise ValueError(f'题号表第 {index} 行的 rowIndex 不对。')
        for field in ('question', 'ground_truth', 'contexts', 'case_type'):
            if before_row[field] != after_row[field]:
                raise ValueError(f'{record["id"]} 两次运行的 {field} 不同。')
        if record['question'] != before_row['question'] or record['caseType'] != before_row['case_type']:
            raise ValueError(f'{record["id"]} 与基线分析表对不上。')
        before, after = _side(before_row), _side(after_row)
        same_answer = before_row['answer'] == after_row['answer']
        delta = None if before_row['case_type'] != 'positive' else _round(after_row['faithfulness'] - before_row['faithfulness'])
        rows.append({'id': record['id'], 'category': record['category'], 'caseType': before_row['case_type'],
                     'question': before_row['question'], 'groundTruth': before_row['ground_truth'],
                     'sameAnswer': same_answer, 'faithfulnessDelta': delta,
                     'change': change(before_row['case_type'], before, after, same_answer),
                     'before': before, 'after': after})

    categories = []
    for key, label in CATEGORIES.items():
        group = [r for r in rows if r['category'] == key]
        positive = [r for r in group if r['caseType'] == 'positive']
        mean = (lambda side: _round(sum(r[side]['faithfulness'] for r in positive) / len(positive)) if positive else None)
        categories.append({'id': key, 'label': label, 'questions': len(group),
                           'refusedBefore': sum(r['before']['refusal'] for r in group),
                           'refusedAfter': sum(r['after']['refusal'] for r in group),
                           'faithfulnessBefore': mean('before'), 'faithfulnessAfter': mean('after')})

    comparison = reg.compare(base_response, fixed_response)
    faith = next(m for m in comparison['metrics'] if m['key'] == 'pos_faithfulness')
    by_question = {r['question']: r['id'] for r in rows}
    positive = [r for r in rows if r['caseType'] == 'positive']
    corrected = [r for r in positive if r['change'] == 'corrected']
    rest = [r for r in positive if r['change'] != 'corrected']
    contribution = {'corrected': _round(sum(r['faithfulnessDelta'] for r in corrected) / len(positive)),
                    'rest': _round(sum(r['faithfulnessDelta'] for r in rest) / len(positive)),
                    'correctedIds': [r['id'] for r in corrected], 'restCount': len(rest)}
    if abs(contribution['corrected'] + contribution['rest'] - faith['mean_delta']) > 1e-3:
        raise ValueError('忠实度变化的分解与回归对比的均值不一致。')

    return {
        'thresholds': {'faithfulness': base_request['faith_threshold'], 'contextPrecision': base_request['ctx_prec_threshold']},
        'request': {'compared': compared, 'docPath': base_request['doc_path'], 'testsetPath': base_request['testset_path']},
        'sameContexts': sum(a['contexts'] == b['contexts'] for a, b in zip(base_rows, fixed_rows)),
        'gate': {'before': _gate(base_response, base_request, base_rows), 'after': _gate(fixed_response, fixed_request, fixed_rows)},
        'regression': {'meanDelta': faith['mean_delta'], 'interval': list(faith['interval']), 'noise': faith['noise'],
                       'status': faith['status'], 'paired': faith['paired'],
                       'drops': [{'id': by_question[d['question']], 'delta': d['delta']} for d in faith['drops']],
                       'contextPrecisionStatus': next(m['status'] for m in comparison['metrics'] if m['key'] == 'pos_context_precision')},
        'contribution': contribution,
        'categories': categories,
        'rows': rows,
    }


def judge_rerun() -> dict:
    """修复后的版本只换裁判重跑：生成仍是同一模型，裁判改为请求里的 judge_model_name。"""
    fixed_response, judge_response = _read(FIXED / 'api-response.json'), _read(JUDGE / 'api-response.json')
    fixed_request, judge_request = _read(FIXED / 'request.json'), _read(JUDGE / 'request.json')
    judge = judge_request.get('judge_model_name')
    if not judge or judge == fixed_request.get('judge_model_name', MODEL):
        raise ValueError('换裁判重跑没有记录另一个裁判模型。')
    compared = (set(fixed_request) | set(judge_request)) - {'version_label', 'judge_model_name'}
    if any(fixed_request.get(key) != judge_request.get(key) for key in compared):
        raise ValueError('换裁判重跑与修复验证重跑的请求参数不同。')
    fixed_rows, judge_rows = fixed_response['model_dfs'][MODEL], judge_response['model_dfs'][MODEL]
    if len(fixed_rows) != len(judge_rows) or any(a['question'] != b['question'] for a, b in zip(fixed_rows, judge_rows)):
        raise ValueError('换裁判重跑与修复验证重跑的题目对不上。')
    same = [(a, b) for a, b in zip(fixed_rows, judge_rows) if a['answer'] == b['answer']]
    same_positive = [(a, b) for a, b in same if a['case_type'] == 'positive']
    mean = lambda rows: _round(sum(r['faithfulness'] for r in rows) / len(rows))
    return {'judgeModel': judge, 'gate': _gate(judge_response, judge_request, judge_rows),
            'sameAnswers': len(same), 'samePositive': len(same_positive),
            'samePositiveSelf': mean([a for a, _ in same_positive]), 'samePositiveJudge': mean([b for _, b in same_positive])}


def code(hashes: dict) -> dict:
    """两次运行的源码差异：按运行清单逐文件比哈希。门禁代码与裁判提示定义必须相同。"""
    manifests = [_read(folder / 'manifest.json') for folder in (BASELINE, FIXED)]
    before, after = ({f['path']: f['sha256'] for f in m['sourceFiles']} for m in manifests)
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    gate = 'backend/core/gate.py'
    if gate in changed or gate not in before:
        raise ValueError('两次运行的门禁代码不同，不能把门禁变化归于提示词。')
    if hashes['baseline']['prompt-definitions.json'] != hashes['fixed']['prompt-definitions.json']:
        raise ValueError('两次运行的 Ragas 裁判提示定义不同。')
    return {'commits': {'baseline': manifests[0]['codeCommit'][:7], 'fixed': manifests[1]['codeCommit'][:7]},
            'gateSame': True, 'judgePromptsSame': True, 'sourceFiles': len(after), 'changed': changed}


def heldout() -> dict:
    """留出题：由调用记录重算并与归档逐字段核对，只取页面需要的计数。"""
    summary, items = heldout_fix.analyze(HELDOUT)
    if json.loads(json.dumps(summary, ensure_ascii=False)) != _read(HELDOUT / 'summary.json') \
            or json.loads(json.dumps(items, ensure_ascii=False)) != _read(HELDOUT / 'items.json'):
        raise ValueError('留出题结果不能由归档的调用记录重新算出。')
    groups = []
    for key, label in HELDOUT_CATEGORIES.items():
        before, after = (summary['arms'][arm]['groups'][f'{key}/all'] for arm in ('before', 'after'))
        paired = summary['paired'][key]
        # 资料无答案题拒答即通过，「拒答数」只对需要作答的两类有意义。
        refused = (lambda group: None if key == 'missing' else group['outcomes'].get('refused', 0))
        groups.append({'id': key, 'label': label, 'questions': before['questions'],
                       'passBefore': before['pass'], 'passAfter': after['pass'],
                       'refusedBefore': refused(before), 'refusedAfter': refused(after),
                       'fullySupportedBefore': before['fullySupported'], 'fullySupportedAfter': after['fullySupported'],
                       'improved': paired['improved'], 'worsened': paired['worsened'], 'signTestP': paired['signTestP']})
    after_rows = items['rows']['after']
    exceptions = [{'id': r['id'], 'statements': [s['text'] for s in r['statements'] if s['verdict'] == 'fabrication']}
                  for r in after_rows if r['outcome'] == 'pass' and r['category'] != 'missing' and not r['fullySupported']]
    return {'questions': len(after_rows), 'documents': sorted({r['doc'] for r in after_rows}),
            'fixCommit': summary['fixCommit'], 'fixLine': summary['fixLine'],
            'model': summary['model'], 'returnedModels': summary['returnedModels'], 'usage': summary['usage'],
            'groups': groups, 'notFullySupported': exceptions}


def build() -> dict:
    hashes = verify_hashes()
    held = heldout()
    return {
        'source': {key: {'path': _rel(folder), 'sha256': hashes[key]} for key, (folder, _) in FILES.items()},
        'model': MODEL,
        'fix': {'commit': held['fixCommit'], 'line': held['fixLine']},
        'code': code(hashes),
        'formal': formal_runs(),
        'judgeRerun': judge_rerun(),
        'heldout': {key: value for key, value in held.items() if key not in ('fixCommit', 'fixLine')},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='只校验，不写文件')
    args = parser.parse_args()
    fixture = build()
    if args.check:
        if _read(OUTPUT) != fixture:
            print('fix-verification.json 与证据重新生成的结果不一致。', file=sys.stderr)
            return 1
        print('一致。')
        return 0
    write_json(OUTPUT, fixture)
    print(f'已写出 {_rel(OUTPUT)}：{len(fixture["formal"]["rows"])} 道正式运行题，{fixture["heldout"]["questions"]} 道留出题。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
