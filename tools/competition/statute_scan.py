"""用法条结构核对扫描已归档运行的全部回答。只读归档，不调用模型、不写文件。"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.statute_check import check_answer  # noqa: E402

SOURCE = 'scenarios/deposit_insurance.txt'
CASES = 'scenarios/competition_25/case-manifest.json'
RUNS = {
    '20261005-deepseek': 'docs/competition/evidence/20261005T144504Z-full-39511424/api-response.json',
    '20261014-qwen72b': 'docs/competition/evidence/20261014T125415Z-full-a5002d4f/api-response.json',
    '20261014-example-app': 'docs/competition/external-rag/20261014-example-app-qwen72b/result.json',
    '20261021-anythingllm': 'docs/competition/external-rag/20261021-anythingllm-qwen72b/result.json',
}


def rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding='utf-8'))
    return data['rows'] if 'rows' in data else next(iter(data['model_dfs'].values()))


def scan(root: Path = ROOT) -> dict:
    source = (root / SOURCE).read_text(encoding='utf-8')
    cases = json.loads((root / CASES).read_text(encoding='utf-8'))['cases']
    runs = {}
    for name, path in RUNS.items():
        answers = []
        for i, row in enumerate(rows(root / path)):
            case = next((c for c in cases if c['question'] == row['question']), cases[i])
            result = check_answer(row.get('answer') or '', row.get('contexts') or [], source, row['question'])
            answers.append({'caseId': case['id'], 'flagged': result['flagged'],
                            'findings': [f for f in result['findings'] if f['severity'] != 'info'],
                            'info': dict(Counter(f['code'] for f in result['findings'] if f['severity'] == 'info'))})
        runs[name] = {'path': path, 'answers': len(answers), 'flagged': [a['caseId'] for a in answers if a['flagged']],
                      'details': [a for a in answers if a['findings'] or a['info']]}
    return {'answers': sum(r['answers'] for r in runs.values()), 'flagged': sum(len(r['flagged']) for r in runs.values()), 'runs': runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--json', action='store_true', help='输出逐题明细')
    args = parser.parse_args()
    result = scan()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"共 {result['answers']} 个回答，检出 {result['flagged']} 个。")
    for name, run in result['runs'].items():
        print(f"  {name}：{run['answers']} 个回答，检出 {'、'.join(run['flagged']) or '无'}")
        for answer in run['details']:
            for f in answer['findings']:
                print(f"    {answer['caseId']} [{f['severity']}] {f['code']}：{f['text'][:60]}")
            if answer['info']:
                print(f"    {answer['caseId']} [info] " + '，'.join(f'{k}×{v}' for k, v in answer['info'].items()))


if __name__ == '__main__':
    main()
