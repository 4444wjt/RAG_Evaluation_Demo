"""从 25 题正式运行的冻结证据中，抽取总览「正式运行回放」所需的逐句数据。

门禁的正向忠实度由 Ragas Faithfulness 计分：回答先被拆成陈述，裁判再逐条判断能否由检索原文推出，
每题得分 = 判为支持的陈述数 / 陈述总数。这些逐条判定保存在 llm-events.jsonl 里，本脚本把它们
与题目一一对应，并逐题核对比例与 api-response.json 中归档的 faithfulness 完全一致；
任何一条对不上就报错退出，不生成文件。证据目录只读，不改动其中任何文件。

注意：这里的「逐句」是 Ragas 的内部陈述判定，不是界面其他位置展示的自研 Trace。

  python tools/competition/build_replay_fixture.py            写出 frontend/src/fixtures/formal-run-replay.json
  python tools/competition/build_replay_fixture.py --check    只校验已提交的文件是否与证据重新生成的结果一致
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.competition.run_evidence import sha256, write_json  # noqa: E402

RUN_ID = '20261005T144504Z-full-39511424'
EVIDENCE = ROOT / 'docs' / 'competition' / 'evidence' / RUN_ID
OUTPUT = ROOT / 'frontend' / 'src' / 'fixtures' / 'formal-run-replay.json'
MODEL = 'deepseek-chat'
DETAIL_ROW = 0
REFUSAL = '根据提供的文档，无法回答该问题。'
SOURCE_FILES = ('api-response.json', 'llm-events.jsonl', 'request.json')


def _prompt(event: dict) -> str:
    return event['messages'][0][0]['content']


def _field(text: str, name: str) -> str | None:
    """取提示中最后一个 `name: "<JSON 字符串>"`，即 few-shot 示例之后的实际任务。"""
    found = re.findall(rf'\n{name}: ("(?:[^"\\]|\\.)*")', text)
    return json.loads(found[-1]) if found else None


def _output(event: dict):
    raw = event['generations'][0][0]
    return json.loads(re.search(r'[\[{].*[\]}]', raw, re.S).group(0))


def _expected_hashes(evidence: Path) -> dict[str, str]:
    lines = (evidence / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines()
    return {name: digest for digest, name in (line.split(maxsplit=1) for line in lines if line.strip())}


def map_statement_verdicts(evidence: Path, rows: list[dict]) -> dict[int, list[dict]]:
    """把每次 Faithfulness 逐条判定调用对应到唯一一道题。"""
    events = [json.loads(line) for line in (evidence / 'llm-events.jsonl').open(encoding='utf-8')]
    starts = {e['runId']: e for e in events if e['event'] == 'start'}
    ends = {e['runId']: e for e in events if e['event'] == 'end' and e['runId'] in starts}
    nli, generated = [], {}
    for run_id, end in ends.items():
        text, raw = _prompt(starts[run_id]), end['generations'][0][0]
        if '"verdict"' in raw and '"reason"' in raw and '\nstatements: ' in text:
            nli.append(run_id)
        elif '"simpler_statements"' in raw and '\nsentences: ' in text:
            statements = [s for item in _output(end) for s in item.get('simpler_statements', [])]
            generated.setdefault(json.dumps(statements, ensure_ascii=False), []).append(_prompt(starts[run_id]))

    verdicts: dict[int, list[dict]] = {}
    for run_id in nli:
        text = _prompt(starts[run_id])
        statements = json.loads(_field(text, 'statements'))
        context = _field(text, 'context') or ''
        prompts = generated.get(json.dumps(statements, ensure_ascii=False), [])
        answers = {_field(p, 'answer') for p in prompts}
        questions = {_field(p, 'question') for p in prompts}
        matched = [i for i, row in enumerate(rows)
                   if row['answer'] in answers and row['question'] in questions
                   and all(chunk in context for chunk in row['contexts'])]
        if len(matched) != 1 or matched[0] in verdicts:
            raise ValueError(f'逐条判定调用 {run_id} 无法唯一对应到一道题：{matched}')
        verdicts[matched[0]] = [{'statement': v['statement'], 'verdict': int(v['verdict'])} for v in _output(ends[run_id])]
    return verdicts


def build(evidence: Path = EVIDENCE) -> dict:
    expected = _expected_hashes(evidence)
    hashes = {name: sha256(evidence / name) for name in SOURCE_FILES}
    if any(expected.get(name) != digest for name, digest in hashes.items()):
        raise ValueError('证据文件哈希与 SHA256SUMS.txt 不一致。')

    response = json.loads((evidence / 'api-response.json').read_text(encoding='utf-8'))
    request = json.loads((evidence / 'request.json').read_text(encoding='utf-8'))
    rows = response['model_dfs'][MODEL]
    summary = next(s for s in response['summary'] if s['model'] == MODEL)
    verdicts = map_statement_verdicts(evidence, rows)

    positive = []
    for index, row in enumerate(rows):
        if row['case_type'] != 'positive':
            continue
        judged = verdicts.get(index)
        if not judged:
            raise ValueError(f'第 {index} 题缺少逐条判定。')
        supported = sum(item['verdict'] for item in judged)
        if supported / len(judged) != row['faithfulness']:
            raise ValueError(f'第 {index} 题逐条判定比例与归档 faithfulness 不一致。')
        positive.append({'rowIndex': index, 'supported': supported, 'total': len(judged),
                         'faithfulness': row['faithfulness'], 'refusal': row['answer'].strip() == REFUSAL})

    mean = sum(item['faithfulness'] for item in positive) / len(positive)
    if abs(mean - summary['pos_faithfulness']) > 1e-9:
        raise ValueError('正向题均值与归档 pos_faithfulness 不一致。')
    detail = rows[DETAIL_ROW]
    return {
        'source': {'runId': RUN_ID, 'model': MODEL, 'evidence': f'docs/competition/evidence/{RUN_ID}', 'sha256': hashes},
        'thresholds': {'faithfulness': request['faith_threshold'], 'context_precision': request['ctx_prec_threshold']},
        'gate': {'passed': response['gate_passed'], 'posFaithfulness': summary['pos_faithfulness'],
                 'posContextPrecision': summary['pos_context_precision']},
        'detail': {'rowIndex': DETAIL_ROW, 'question': detail['question'], 'answer': detail['answer'],
                   'context': detail['contexts'][0], 'statements': verdicts[DETAIL_ROW]},
        'rows': positive,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='只校验，不写文件')
    args = parser.parse_args()
    fixture = build()
    if args.check:
        saved = json.loads(OUTPUT.read_text(encoding='utf-8'))
        if saved != fixture:
            print('formal-run-replay.json 与证据重新生成的结果不一致。', file=sys.stderr)
            return 1
        print('一致。')
        return 0
    write_json(OUTPUT, fixture)
    print(f'已写出 {OUTPUT.relative_to(ROOT)}：{len(fixture["rows"])} 道正向题。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
