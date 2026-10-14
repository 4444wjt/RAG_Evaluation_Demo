"""修复验证留出题：在没参与调试的新题上，对照问答提示词修复前后的作答行为。

  python tools/competition/heldout_fix.py prepare   --dir <工作目录>
  python tools/competition/heldout_fix.py authorize --dir <工作目录> --by <授权人> --budget <元> --max-requests <次>
  python tools/competition/heldout_fix.py run       --dir <工作目录>
  python tools/competition/heldout_fix.py analyze   --dir <工作目录> [--output <归档目录>]

题目来自 scenarios/heldout_fp/cases.json，须作者确认后才能授权。prepare 在本机用平台同样的切分、Embedding 与 Top-K
检索每题资料并固定下来（不调用模型），两组提示词看到的资料完全相同。
两组都走生产代码 rag_answer：after 组即当前提示；before 组只在发出请求前去掉 2026-10-14 新加的那一句纠正指引，
其余逐字不变。每题每组两次调用：作答一次、逐句核对一次。授权、哈希绑定、预算与请求数上限沿用外部案例工具。
判定标准在运行前固定（见 OUTCOME_RULES），运行后不再修改。不计算 Ragas 指标，不给门禁结论。
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from math import comb
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.attribution import is_refusal  # noqa: E402
from backend.services.answering import rag_answer  # noqa: E402
from backend.services.trace import TRACE_PROMPT_TEMPLATE, trace_hallucinations  # noqa: E402
from tools.competition.prompt_case import (  # noqa: E402
    MODEL, OUTPUT_ALLOWANCE, PRICE, TRACE_FAILED, FixedRetriever, Recorder, build_llm, call_cost,
    read_json, sha256_file, write_json,
)

CASES = 'scenarios/heldout_fp/cases.json'
FIX_COMMIT = 'b90a14c'
FIX_LINE = '    如果问题中的说法或前提与参考资料不一致，请依据参考资料指出并纠正，不要拒答。\n'
ARMS = {
    'before': {'label': '修复前提示词', 'source': f'backend/services/answering.py 去掉提交 {FIX_COMMIT} 新增的一行'},
    'after': {'label': '修复后提示词', 'source': 'backend/services/answering.py（当前）'},
}
TOOL_FILES = ('tools/competition/heldout_fix.py', 'tools/competition/prompt_case.py', 'backend/services/trace.py',
              'backend/services/answering.py', 'backend/core/attribution.py')
CATEGORIES = ('false_premise', 'true_premise', 'missing')
OUTCOME_RULES = {
    'false_premise': '通过 = 没有拒答，且逐句核对没有与资料矛盾（fact_error）的陈述',
    'true_premise': '通过 = 没有拒答，且逐句核对没有与资料矛盾（fact_error）的陈述',
    'missing': '通过 = 拒答',
    'secondary': '作答题另报「全部陈述有资料依据」（无 fact_error、无 fabrication）的题数',
    'comparison': '同一题两组配对比较：只看结果不同的题，用精确符号检验（双侧）',
}
ANSWER_PROMPT_CHARS = 250


def tool_hashes(root: Path = ROOT) -> dict:
    return {name: sha256_file(root / name) for name in TOOL_FILES}


# ---------- 题目与资料 ----------

def load_cases(root: Path = ROOT, *, require_author: bool = False) -> dict:
    data = read_json(root / CASES)
    cases = data['cases']
    if len({c['id'] for c in cases}) != len(cases) or len({c['question'] for c in cases}) != len(cases):
        raise ValueError('题号与问题必须唯一。')
    for key, doc in data['documents'].items():
        if sha256_file(root / doc['path']) != doc['sha256']:
            raise ValueError(f'资料 {key} 与登记的哈希不一致。')
    for case in cases:
        if case['category'] not in CATEGORIES or case['doc'] not in data['documents']:
            raise ValueError(f'{case["id"]}：类别或资料未登记。')
        if not case['groundTruth'].strip():
            raise ValueError(f'{case["id"]}：缺少参考答案。')
        if (case['category'] == 'missing') != (not case['evidence']):
            raise ValueError(f'{case["id"]}：资料无答案的题不列原文依据，其余题必须列。')
        lines = (root / data['documents'][case['doc']]['path']).read_text(encoding='utf-8-sig').splitlines()
        for evidence in case['evidence']:
            line = evidence['line']
            if not 1 <= line <= len(lines) or evidence['quote'] not in lines[line - 1]:
                raise ValueError(f'{case["id"]}：原文依据与资料第 {line} 行不符。')
    review = data['authorValidation']
    if require_author and not (review.get('status') == 'confirmed' and review.get('reviewer') and review.get('reviewedAt')):
        raise PermissionError('题目尚未经作者确认，不能授权付费运行。')
    return data


def retrieve(data: dict, root: Path = ROOT) -> dict:
    """与平台 prepare_resources 相同的切分、Embedding 与 Top-K；向量库只建在内存里，不动项目的 chroma_db。"""
    from langchain_community.embeddings import HuggingFaceEmbeddings
    from langchain_community.vectorstores import Chroma
    from backend.config import EMBEDDING_MODEL, TOP_K
    from backend.services.documents import load_and_split_doc

    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL, model_kwargs={'local_files_only': True})
    contexts = {}
    for key, doc in data['documents'].items():
        chunks = load_and_split_doc(str(root / doc['path']))
        store = Chroma.from_documents(documents=chunks, embedding=embeddings, collection_name=f'heldout-{key}')
        retriever = store.as_retriever(search_kwargs={'k': TOP_K})
        for case in data['cases']:
            if case['doc'] == key:
                contexts[case['id']] = [d.page_content for d in retriever.invoke(case['question'])]
    return contexts


def evidence_coverage(case: dict, contexts: list) -> str:
    if not case['evidence']:
        return 'n/a'
    joined = '\n'.join(contexts)
    found = sum(e['quote'] in joined for e in case['evidence'])
    return 'all' if found == len(case['evidence']) else 'partial' if found else 'none'


def job_estimate(item: dict) -> dict:
    context_chars = sum(len(c) for c in item['contexts'])
    trace_chars = (len(TRACE_PROMPT_TEMPLATE) + len(item['question']) + OUTPUT_ALLOWANCE['answer'] + len(item['groundTruth'])
                   + len(json.dumps(item['contexts'], ensure_ascii=False)))
    return {'calls': 2, 'input': ANSWER_PROMPT_CHARS + len(item['question']) + context_chars + trace_chars,
            'output': OUTPUT_ALLOWANCE['answer'] + OUTPUT_ALLOWANCE['trace']}


def estimate(package: dict, jobs=None) -> dict:
    items = {item['id']: item for item in package['items']}
    parts = [job_estimate(items[job['item']]) for job in (package['jobs'] if jobs is None else jobs)]
    input_tokens, output_tokens = sum(p['input'] for p in parts), sum(p['output'] for p in parts)
    cost = (input_tokens * PRICE['inputCnyPerMillion'] + output_tokens * PRICE['outputCnyPerMillion']) / 1_000_000
    return {'jobs': len(parts), 'calls': sum(p['calls'] for p in parts), 'inputTokensUpperBound': input_tokens,
            'outputTokensUpperBound': output_tokens, 'costCnyUpperBound': round(cost, 2),
            'assumption': '中文按每字 1 token、输出按上限计，偏保守'}


def prepare(directory: Path, root: Path = ROOT, contexts_fn=retrieve) -> dict:
    data = load_cases(root)
    contexts = contexts_fn(data, root)
    items = []
    for case in data['cases']:
        found = contexts[case['id']]
        items.append({'id': case['id'], 'doc': case['doc'], 'category': case['category'], 'question': case['question'],
                      'groundTruth': case['groundTruth'], 'contexts': found,
                      'evidenceRetrieved': evidence_coverage(case, found)})
    jobs = [{'id': f'{arm}:{item["id"]}', 'arm': arm, 'item': item['id']} for arm in ARMS for item in items]
    package = {'schemaVersion': 1, 'cases': {'path': CASES, 'sha256': sha256_file(root / CASES)},
               'fixCommit': FIX_COMMIT, 'fixLine': FIX_LINE.strip(), 'model': MODEL, 'price': PRICE,
               'arms': ARMS, 'outcomeRules': OUTCOME_RULES, 'items': items, 'jobs': jobs}
    package['estimate'] = estimate(package)
    write_json(directory / 'package.json', package)
    return package


def authorize(directory: Path, by: str, budget: float, max_requests: int, note: str = '', root: Path = ROOT) -> dict:
    load_cases(root, require_author=True)
    package = read_json(directory / 'package.json')
    if package['cases']['sha256'] != sha256_file(root / CASES):
        raise PermissionError('题目在 prepare 之后有改动，需重新 prepare。')
    auth = {'packageSha256': sha256_file(directory / 'package.json'), 'casesSha256': package['cases']['sha256'],
            'authorizedBy': by, 'authorizedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'budgetCny': budget, 'maxRequests': max_requests, 'maxRetries': 0, 'price': PRICE,
            'toolSha256': tool_hashes(root), 'note': note}
    write_json(directory / 'authorization.json', auth)
    return auth


# ---------- 运行 ----------

class WithoutFix:
    """before 组：把生产代码拼好的提示去掉新增的那一行再发出，其余逐字不变。"""

    def __init__(self, llm):
        self.llm = llm

    def invoke(self, prompt):
        if FIX_LINE not in prompt:
            raise RuntimeError('当前问答提示中找不到修复新增的那一行，before 组无法构造。')
        return self.llm.invoke(prompt.replace(FIX_LINE, '', 1))


def run_job(job: dict, item: dict, llm) -> dict:
    recorder = Recorder(llm)
    record = {'id': job['id'], 'arm': job['arm'], 'item': job['item']}
    try:
        model = WithoutFix(recorder) if job['arm'] == 'before' else recorder
        record['answer'], _ = rag_answer(item['question'], FixedRetriever(item['contexts']), model)
        record['trace'] = trace_hallucinations(item['question'], record['answer'], item['contexts'], item['groundTruth'], recorder)
        record['status'] = 'ok'
    except Exception as error:  # 只记类型，避免把可能含密钥或请求头的异常文本写入档案
        record['status'], record['errorType'] = 'call_error', type(error).__name__
    record['calls'] = recorder.calls
    return record


def run(directory: Path, llm=None, root: Path = ROOT) -> dict:
    package_path, auth_path = directory / 'package.json', directory / 'authorization.json'
    if not auth_path.exists():
        raise PermissionError('没有授权文件，不能发起付费调用。')
    auth = read_json(auth_path)
    if auth['packageSha256'] != sha256_file(package_path):
        raise PermissionError('授权绑定的材料包哈希与当前 package.json 不一致。')
    if auth.get('toolSha256') != tool_hashes(root):
        raise PermissionError('授权后运行代码（工具、逐句核对、问答提示、拒答判定）有改动，需重新授权。')
    package = read_json(package_path)
    items = {item['id']: item for item in package['items']}
    records_path = directory / 'records.jsonl'
    done, spent, requests = set(), 0.0, 0
    if records_path.exists():
        for line in records_path.read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            done.add(record['id'])
            requests += len(record['calls'])
            spent += sum(call_cost(c) for c in record['calls'])
    llm = llm or build_llm()
    stopped, completed = None, len(done)
    with records_path.open('a', encoding='utf-8') as handle:
        for job in package['jobs']:
            if job['id'] in done:
                continue
            if requests + 2 > auth['maxRequests']:
                stopped = '达到请求数上限'
                break
            if spent + estimate(package, [job])['costCnyUpperBound'] > auth['budgetCny']:
                stopped = '剩余预算不足以完成下一项'
                break
            record = run_job(job, items[job['item']], llm)
            requests += len(record['calls'])
            spent += sum(call_cost(c) for c in record['calls'])
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            handle.flush()
            completed += 1
    return {'requests': requests, 'spentCny': round(spent, 4), 'stopped': stopped,
            'completed': completed, 'total': len(package['jobs'])}


# ---------- 分析 ----------

def outcome(row: dict) -> str:
    """按 OUTCOME_RULES 判定；调用或核对失败单列，不算通过也不算失败。"""
    if row['status'] != 'ok':
        return 'error'
    if row['category'] == 'missing':
        return 'pass' if row['refused'] else 'answered'
    if row['refused']:
        return 'refused'
    if row['traceStatus'] in TRACE_FAILED:
        return 'trace_failed'
    return 'contradicts_source' if row['factErrors'] else 'pass'


def _row(record: dict, item: dict) -> dict:
    trace = record.get('trace') or {}
    statements = trace.get('statements') or []
    verdicts = Counter(s.get('verdict') for s in statements)
    row = {'id': item['id'], 'doc': item['doc'], 'category': item['category'], 'question': item['question'],
           'groundTruth': item['groundTruth'], 'evidenceRetrieved': item['evidenceRetrieved'],
           'status': record['status'], 'answer': record.get('answer'),
           'refused': is_refusal(record.get('answer')) if record['status'] == 'ok' else None,
           'traceStatus': trace.get('trace_status'),
           'statements': [{'text': s.get('text'), 'verdict': s.get('verdict'), 'evidence': s.get('evidence')} for s in statements],
           'supported': verdicts.get('supported', 0), 'factErrors': verdicts.get('fact_error', 0),
           'fabrications': verdicts.get('fabrication', 0), 'total': len(statements)}
    row['outcome'] = outcome(row)
    row['fullySupported'] = row['outcome'] == 'pass' and row['category'] != 'missing' and row['fabrications'] == 0 and row['total'] > 0
    return row


def sign_test(better: int, worse: int) -> float | None:
    """配对结果不同的题上做精确符号检验（双侧）；没有不同的题时返回 None。"""
    n = better + worse
    if not n:
        return None
    tail = sum(comb(n, k) for k in range(0, min(better, worse) + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def analyze(directory: Path) -> tuple[dict, dict]:
    package = read_json(directory / 'package.json')
    items = {item['id']: item for item in package['items']}
    records = [json.loads(line) for line in (directory / 'records.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = {arm: {} for arm in package['arms']}
    for record in records:
        rows[record['arm']][record['item']] = _row(record, items[record['item']])
    arms = {}
    for arm, arm_rows in rows.items():
        groups = {}
        for category in CATEGORIES:
            for doc in sorted({item['doc'] for item in items.values()}) + ['all']:
                group = [r for r in arm_rows.values() if r['category'] == category and doc in (r['doc'], 'all')]
                groups[f'{category}/{doc}'] = {'questions': len(group), 'outcomes': dict(Counter(r['outcome'] for r in group)),
                                               'pass': sum(r['outcome'] == 'pass' for r in group),
                                               'fullySupported': sum(r['fullySupported'] for r in group)}
        arms[arm] = {'label': package['arms'][arm]['label'], 'groups': groups,
                     'errors': [r['id'] for r in arm_rows.values() if r['outcome'] in ('error', 'trace_failed')]}
    paired = {}
    for category in CATEGORIES:
        ids = [i for i, item in items.items() if item['category'] == category and i in rows['before'] and i in rows['after']]
        better = [i for i in ids if rows['after'][i]['outcome'] == 'pass' and rows['before'][i]['outcome'] != 'pass']
        worse = [i for i in ids if rows['before'][i]['outcome'] == 'pass' and rows['after'][i]['outcome'] != 'pass']
        paired[category] = {'questions': len(ids), 'improved': better, 'worsened': worse,
                            'signTestP': sign_test(len(better), len(worse))}
    calls = [c for record in records for c in record['calls']]
    summary = {
        'schemaVersion': 1, 'cases': package['cases'], 'fixCommit': package['fixCommit'], 'fixLine': package['fixLine'],
        'model': {k: package['model'][k] for k in ('name', 'temperature')},
        'returnedModels': dict(Counter(c.get('model') for c in calls if 'error' not in c)),
        'outcomeRules': package['outcomeRules'],
        'evidenceRetrieved': dict(Counter(item['evidenceRetrieved'] for item in items.values())),
        'evidenceNotFullyRetrieved': [i for i, item in items.items() if item['evidenceRetrieved'] in ('partial', 'none')],
        'arms': arms, 'paired': paired,
        'usage': {'requests': len(calls), 'errors': sum('error' in c for c in calls),
                  'inputTokens': sum(c.get('inputTokens') or 0 for c in calls),
                  'outputTokens': sum(c.get('outputTokens') or 0 for c in calls),
                  'costCny': round(sum(call_cost(c) for c in calls), 4)},
        'scope': '一次运行、两份法规节选、24 题、一个模型；只差问答提示中的一句。不计算 Ragas 指标，不给门禁结论。',
    }
    return summary, {'arms': package['arms'], 'rows': {arm: list(r.values()) for arm, r in rows.items()}}


def archive(directory: Path, output: Path) -> dict:
    summary, items = analyze(directory)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'summary.json', summary)
    write_json(output / 'items.json', items)
    for name in ('authorization.json', 'package.json'):
        (output / name).write_bytes((directory / name).read_bytes())
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'authorize', 'run', 'analyze'):
        command = sub.add_parser(name)
        command.add_argument('--dir', type=Path, required=True, help='工作目录（材料包、授权与调用记录）')
        if name == 'authorize':
            command.add_argument('--by', required=True)
            command.add_argument('--budget', type=float, required=True)
            command.add_argument('--max-requests', type=int, required=True)
            command.add_argument('--note', default='')
        if name == 'analyze':
            command.add_argument('--output', type=Path, help='归档目录；不给则只打印')
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        package = prepare(args.dir)
        coverage = Counter(item['evidenceRetrieved'] for item in package['items'])
        print(json.dumps({'estimate': package['estimate'], 'evidenceRetrieved': coverage}, ensure_ascii=False, indent=2))
    elif args.command == 'authorize':
        print(json.dumps(authorize(args.dir, args.by, args.budget, args.max_requests, args.note), ensure_ascii=False, indent=2))
    elif args.command == 'run':
        print(json.dumps(run(args.dir), ensure_ascii=False, indent=2))
    else:
        summary = archive(args.dir, args.output) if args.output else analyze(args.dir)[0]
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
