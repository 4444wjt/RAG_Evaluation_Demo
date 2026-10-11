"""独立裁判对照：只换裁判、不重新生成回答，量化「裁判与被评模型同源」带来的偏差。

回答、检索上下文和参考答案全部取自已归档的运行，被评模型不会被再次调用；
只重算依赖裁判的 Faithfulness、Context Precision（门禁输入）与逐句 Trace。

  run      需要 API 密钥并产生费用；逐行追加写盘，可断点续跑；超预算或超调用数即停止。
  analyze  离线：从 raw.jsonl 重新计算全部对照数字，写出 summary.json 与 README.md。
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics
import sys
from threading import Lock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.competition.run_evidence import dependency_versions, sha256, source_versions, summarize_events, write_json  # noqa: E402

FULL_RUN = 'docs/competition/evidence/20261005T144504Z-full-39511424/api-response.json'
DEMO = 'frontend/src/fixtures/demo.json'
REVIEWS = 'frontend/src/fixtures/credibility-review.json'
SOURCES = {'full': FULL_RUN, 'demo': DEMO}
JUDGES = {
    'deepseek': {'name': 'deepseek-chat', 'base_url_env': 'DEEPSEEK_BASE_URL', 'base_url': 'https://api.deepseek.com', 'api_key_env': 'OPENAI_API_KEY'},
    'qwen72b': {'name': 'Qwen/Qwen2.5-72B-Instruct', 'base_url_env': 'QWEN_BASE_URL', 'base_url': 'https://api.siliconflow.cn/v1', 'api_key_env': 'SILICONFLOW_API_KEY'},
}
# 组名 → (输入来源, 裁判)。self-rerun 是噪声基线：同一裁判再判一次会差多少。
GROUPS = {'self-rerun': ('full', 'deepseek'), 'independent': ('full', 'qwen72b'), 'demo-independent': ('demo', 'qwen72b')}
# 费用保护用的保守单价（元 / 百万 token，输入, 输出）；只是估算，不是账单。
PRICES = {'deepseek': (2.0, 8.0), 'qwen72b': (4.13, 4.13)}
PRICING_SOURCES = {'deepseek': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/', 'qwen72b': 'https://siliconflow.cn/pricing'}
GATE_THRESHOLD = 0.8
HALLUCINATION = ('fact_error', 'fabrication')
LABELS = ('supported', 'hallucination', 'empty', 'error')


# ---------- 数据读取 ----------

def load_rows(source: str) -> list[dict]:
    data = json.loads((ROOT / SOURCES[source]).read_text(encoding='utf-8'))
    model_dfs = data['result']['model_dfs'] if source == 'demo' else data['model_dfs']
    rows = []
    for model, items in model_dfs.items():
        for index, item in enumerate(items):
            rows.append({'source': source, 'model': model, 'rowIndex': index, 'question': item['question'], 'answer': item['answer'],
                         'contexts': list(item['contexts']), 'ground_truth': item['ground_truth'], 'case_type': item.get('case_type', 'positive'),
                         'original': {'faithfulness': number(item.get('faithfulness')), 'context_precision': number(item.get('context_precision')),
                                      'trace': parse_trace(item.get('hallucination_trace'))}})
    return rows


def number(value):
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else float(value)


def parse_trace(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def trace_label(trace) -> str:
    """回答级标签：含任一事实错误或编造即为 hallucination；没有陈述为 empty；调用或解析失败为 error。"""
    if not isinstance(trace, dict) or trace.get('trace_status') in ('call_error', 'parse_error', 'invalid_structure'):
        return 'error'
    verdicts = [s.get('verdict') for s in trace.get('statements') or [] if isinstance(s, dict)]
    if not verdicts:
        return 'empty'
    return 'hallucination' if any(v in HALLUCINATION for v in verdicts) else 'supported'


def read_raw(archive: Path) -> list[dict]:
    path = archive / 'raw.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


# ---------- run（付费） ----------

class Budget:
    def __init__(self, limit_cny: float, max_calls: int):
        self.limit, self.max_calls, self.calls, self.cost, self.lock = limit_cny, max_calls, 0, 0.0, Lock()

    def add(self, judge: str, prompt_tokens: int, completion_tokens: int) -> None:
        price_in, price_out = PRICES[judge]
        with self.lock:
            self.calls += 1
            self.cost += (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000

    def exceeded(self) -> str | None:
        if self.cost >= self.limit:
            return f'估算费用 ¥{self.cost:.2f} 已达上限 ¥{self.limit:.2f}'
        if self.calls >= self.max_calls:
            return f'调用次数 {self.calls} 已达上限 {self.max_calls}'
        return None


def restore_budget(archive: Path, budget: Budget) -> None:
    """续跑时把已记录的调用计入预算。"""
    path = archive / 'llm-events.jsonl'
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event.get('event') == 'end':
            usage = (event.get('llmOutput') or {}).get('token_usage') or {}
            budget.add(event.get('judge', 'qwen72b'), usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0))


def install_observer(archive: Path, budget: Budget, current: dict):
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tracers.context import register_configure_hook

    lock = Lock()

    class Observer(BaseCallbackHandler):
        def record(self, event: dict) -> None:
            event.update(at=datetime.now(timezone.utc).isoformat(), group=current.get('group'), judge=current.get('judge'))
            with lock, (archive / 'llm-events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            params = kwargs.get('invocation_params') or {}
            text = json.dumps([[m.content for m in batch] for batch in messages], ensure_ascii=False)
            self.record({'event': 'start', 'runId': str(run_id), 'promptSha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                         'requestParameters': {k: params[k] for k in ('model', 'model_name', 'temperature', 'n') if k in params}})

        def on_llm_end(self, response, *, run_id, **kwargs):
            output = response.llm_output if isinstance(response.llm_output, dict) else {}
            usage = output.get('token_usage') or {}
            budget.add(current.get('judge'), usage.get('prompt_tokens', 0) or 0, usage.get('completion_tokens', 0) or 0)
            self.record({'event': 'end', 'runId': str(run_id), 'llmOutput': output, 'generations': [[g.text for g in batch] for batch in response.generations]})

        def on_llm_error(self, error, *, run_id, **kwargs):
            self.record({'event': 'error', 'runId': str(run_id), 'errorType': type(error).__name__})

    register_configure_hook(ContextVar('judge_compare_observer', default=Observer()), True)


def build_judge(judge: str):
    from backend.adapters.models.registry import build_llm
    spec = JUDGES[judge]
    return build_llm({'name': spec['name'], 'base_url': os.environ.get(spec['base_url_env'], spec['base_url']), 'api_key_env': spec['api_key_env']})


def judge_row(row: dict, llm) -> dict:
    from datasets import Dataset
    from ragas import RunConfig, evaluate
    from ragas.metrics import ContextPrecision

    from backend.adapters.ragas_compat import ChineseFaithfulness
    from backend.config import EVAL_TIMEOUT
    from backend.services.trace import trace_hallucinations

    dataset = Dataset.from_dict({'question': [row['question']], 'answer': [row['answer']], 'contexts': [row['contexts']], 'ground_truth': [row['ground_truth']]})
    frame = evaluate(dataset=dataset, metrics=[ChineseFaithfulness(), ContextPrecision()], llm=llm,
                     run_config=RunConfig(max_workers=1, timeout=EVAL_TIMEOUT)).to_pandas()
    trace = trace_hallucinations(row['question'], row['answer'], row['contexts'], row['ground_truth'], llm)
    return {'faithfulness': number(frame['faithfulness'].iloc[0]), 'context_precision': number(frame['context_precision'].iloc[0]), 'trace': trace}


def run(archive: Path, groups: list[str], budget_cny: float, max_calls: int) -> int:
    os.environ.setdefault('MPLBACKEND', 'Agg')
    archive.mkdir(parents=True, exist_ok=True)
    import backend.config  # noqa: F401  加载 .env；密钥只在进程内使用，不写入任何输出

    if not (archive / 'request.json').exists():
        write_json(archive / 'request.json', {'kind': 'judge-comparison', 'groups': {g: {'source': SOURCES[GROUPS[g][0]], 'judge': JUDGES[GROUPS[g][1]]['name']} for g in groups},
                   'metrics': ['faithfulness', 'context_precision', 'hallucination_trace'], 'gateThreshold': GATE_THRESHOLD, 'temperature': 0,
                   'budgetCny': budget_cny, 'maxCalls': max_calls, 'answersRegenerated': False,
                   'sourceSha256': {key: sha256(ROOT / path) for key, path in {**SOURCES, 'reviews': REVIEWS}.items()}})
        write_json(archive / 'runtime.json', {'dependencies': dependency_versions()['relevant'], 'sources': source_versions(ROOT), 'python': sys.version.split()[0]})

    budget = Budget(budget_cny, max_calls)
    restore_budget(archive, budget)
    current: dict = {}
    install_observer(archive, budget, current)
    done = {(r['group'], r['model'], r['rowIndex']) for r in read_raw(archive)}

    judges = {}
    for group in groups:
        source, judge = GROUPS[group]
        if judge not in judges:
            current.update(group='preflight', judge=judge)
            llm = build_judge(judge)
            reply = llm.invoke('只回复两个字母：OK')
            preflight = {'judge': judge, 'requestedModel': JUDGES[judge]['name'], 'returnedModel': (reply.response_metadata or {}).get('model_name'), 'reply': str(reply.content)[:20]}
            with (archive / 'preflight.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(preflight, ensure_ascii=False) + '\n')
            print(f'预检 {judge}: 请求 {preflight["requestedModel"]}，返回 {preflight["returnedModel"]}')
            judges[judge] = llm
        for row in load_rows(source):
            if (group, row['model'], row['rowIndex']) in done:
                continue
            reason = budget.exceeded()
            if reason:
                print(f'停止：{reason}；已保存的结果保留，可调整上限后续跑。')
                return 3
            current.update(group=group, judge=judge)
            result = judge_row(row, judges[judge])
            entry = {'group': group, 'judge': judge, 'source': source, 'model': row['model'], 'rowIndex': row['rowIndex'], **result, 'at': datetime.now(timezone.utc).isoformat()}
            with (archive / 'raw.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
            print(f'[{group}] {row["model"]} #{row["rowIndex"]}  F={entry["faithfulness"]}  CP={entry["context_precision"]}  Trace={trace_label(entry["trace"])}  累计≈¥{budget.cost:.2f}/{budget.calls}次')
    return 0


# ---------- analyze（离线） ----------

def gate(rows: list[dict], scores: dict) -> dict:
    """用正式评测同一个 check_quality_gate 判定（只看正向样本均值）。"""
    from backend.core.gate import check_quality_gate
    from backend.core.utils import _safe_mean

    def mean(metric, case_type=None):
        return _safe_mean([scores[key][metric] for key in scores if case_type is None or rows_by_key(rows)[key]['case_type'] == case_type])

    entry = {'model': 'judge', 'faithfulness': mean('faithfulness'), 'context_precision': mean('context_precision'),
             'pos_faithfulness': mean('faithfulness', 'positive'), 'pos_context_precision': mean('context_precision', 'positive')}
    with redirect_stdout(io.StringIO()):
        result = check_quality_gate([entry], {}, GATE_THRESHOLD, GATE_THRESHOLD)
    return {'posFaithfulness': entry['pos_faithfulness'], 'posContextPrecision': entry['pos_context_precision'], 'passed': result['gate_passed']}


def rows_by_key(rows: list[dict]) -> dict:
    return {(r['model'], r['rowIndex']): r for r in rows}


def metric_diff(left: dict, right: dict, metric: str) -> dict:
    pairs = [(left[k][metric], right[k][metric]) for k in left if k in right]
    both = [(a, b) for a, b in pairs if a is not None and b is not None]
    diffs = [abs(a - b) for a, b in both]

    def crossing(threshold):
        return sum((a >= threshold) != (b >= threshold) for a, b in both)

    return {'pairs': len(pairs), 'compared': len(both), 'missingEitherSide': len(pairs) - len(both),
            'meanAbsDiff': round(statistics.fmean(diffs), 4) if diffs else None, 'medianAbsDiff': round(statistics.median(diffs), 4) if diffs else None,
            'maxAbsDiff': round(max(diffs), 4) if diffs else None, 'identical': sum(d < 1e-9 for d in diffs),
            'crossing0.8': crossing(0.8), 'crossing0.5': crossing(0.5)}


def label_agreement(left: dict, right: dict) -> dict:
    keys = [k for k in left if k in right]
    matrix = {a: {b: 0 for b in LABELS} for a in LABELS}
    for key in keys:
        matrix[left[key]['label']][right[key]['label']] += 1
    agree = sum(matrix[l][l] for l in LABELS)
    return {'compared': len(keys), 'agree': agree, 'agreementRate': round(agree / len(keys), 4) if keys else None,
            'matrix': {a: {b: n for b, n in row.items() if n} for a, row in matrix.items() if any(row.values())}}


def analyze(archive: Path) -> dict:
    raw = read_raw(archive)
    by_group: dict[str, dict] = {}
    for entry in raw:
        by_group.setdefault(entry['group'], {})[(entry['model'], entry['rowIndex'])] = {
            'faithfulness': entry['faithfulness'], 'context_precision': entry['context_precision'], 'label': trace_label(entry['trace'])}

    def original(source):
        return {(r['model'], r['rowIndex']): {'faithfulness': r['original']['faithfulness'], 'context_precision': r['original']['context_precision'],
                                               'label': trace_label(r['original']['trace'])} for r in load_rows(source)}

    summary: dict = {'schemaVersion': 1, 'rowsJudged': {g: len(v) for g, v in sorted(by_group.items())}}
    full_rows, full_original = load_rows('full'), original('full')
    full = {'rows': len(full_rows), 'gate': {'original': gate(full_rows, full_original)}, 'vsOriginal': {}}
    for group in ('self-rerun', 'independent'):
        scores = by_group.get(group)
        if not scores:
            continue
        full['gate'][group] = gate(full_rows, scores) if len(scores) == len(full_rows) else None
        full['vsOriginal'][group] = {'faithfulness': metric_diff(full_original, scores, 'faithfulness'),
                                     'context_precision': metric_diff(full_original, scores, 'context_precision'),
                                     'traceLabel': label_agreement(full_original, scores)}
    if 'self-rerun' in by_group and 'independent' in by_group:
        full['selfRerunVsIndependent'] = {'faithfulness': metric_diff(by_group['self-rerun'], by_group['independent'], 'faithfulness'),
                                          'traceLabel': label_agreement(by_group['self-rerun'], by_group['independent'])}
    summary['fullRun'] = full

    demo_scores = by_group.get('demo-independent')
    if demo_scores:
        demo_original = original('demo')
        reviews = json.loads((ROOT / REVIEWS).read_text(encoding='utf-8'))['records']
        records = []
        for record in reviews:
            key = (record['modelId'], record['rowIndex'])
            if key not in demo_scores:
                continue
            records.append({'modelId': record['modelId'], 'rowIndex': record['rowIndex'], 'focus': record['focus'], 'humanStatus': record['status'],
                            'sameFamilyAsJudge': record['modelId'].startswith('Pro/Qwen/'),
                            'originalLabel': demo_original[key]['label'], 'independentLabel': demo_scores[key]['label'],
                            'originalFaithfulness': demo_original[key]['faithfulness'], 'independentFaithfulness': demo_scores[key]['faithfulness']})
        independent_only = {k: v for k, v in demo_scores.items() if not k[0].startswith('Pro/Qwen/')}
        by_status = {}
        for record in records:
            if record['sameFamilyAsJudge']:
                continue
            bucket = by_status.setdefault(record['humanStatus'], {'records': 0, 'labelChanged': 0})
            bucket['records'] += 1
            bucket['labelChanged'] += record['originalLabel'] != record['independentLabel']
        summary['demo'] = {'rows': len(demo_scores), 'traceLabelAllRows': label_agreement(demo_original, demo_scores),
                           'traceLabelExcludingSameFamily': label_agreement(demo_original, independent_only),
                           'reviewedRecords': records, 'labelChangeByHumanStatusExcludingSameFamily': by_status,
                           'note': '演示快照的原分数早于 zh-sentence-v1 中文兼容层，Faithfulness 差异同时包含兼容层与裁判两种因素；Trace 不受兼容层影响。'}

    write_json(archive / 'summary.json', summary)
    usage = summarize_events(archive)
    write_json(archive / 'usage.json', usage)
    write_json(archive / 'cost-estimate.json', cost_estimate(archive))
    return summary


def cost_estimate(archive: Path) -> dict:
    totals: dict[str, dict] = {}
    path = archive / 'llm-events.jsonl'
    for line in (path.read_text(encoding='utf-8').splitlines() if path.exists() else []):
        event = json.loads(line)
        if event.get('event') != 'end':
            continue
        usage = (event.get('llmOutput') or {}).get('token_usage') or {}
        item = totals.setdefault(event.get('judge') or 'unknown', {'calls': 0, 'promptTokens': 0, 'completionTokens': 0})
        item['calls'] += 1
        item['promptTokens'] += usage.get('prompt_tokens', 0) or 0
        item['completionTokens'] += usage.get('completion_tokens', 0) or 0
    for judge, item in totals.items():
        price_in, price_out = PRICES.get(judge, (0, 0))
        item['estimatedCny'] = round((item['promptTokens'] * price_in + item['completionTokens'] * price_out) / 1_000_000, 4)
    return {'kind': 'estimate-not-billing-record', 'currency': 'CNY', 'actualCharge': None, 'actualChargeStatus': 'unknown',
            'ratesCnyPerMillionTokens': {j: {'input': p[0], 'output': p[1]} for j, p in PRICES.items()}, 'pricingSources': PRICING_SOURCES,
            'note': 'DeepSeek 按高峰时段未命中缓存的输入单价保守估算；实际费用以供应商账单为准。', 'byJudge': totals,
            'estimatedTotalCny': round(sum(i['estimatedCny'] for i in totals.values()), 4)}


def write_checksums(archive: Path) -> None:
    lines = [f'{sha256(p)}  {p.relative_to(archive).as_posix()}' for p in sorted(archive.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS.txt']
    (archive / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='独立裁判对照（只换裁判，不重新生成回答）')
    sub = parser.add_subparsers(dest='command', required=True)
    run_cmd = sub.add_parser('run', help='调用裁判模型（产生费用）')
    run_cmd.add_argument('archive')
    run_cmd.add_argument('--groups', nargs='+', choices=list(GROUPS), default=list(GROUPS))
    run_cmd.add_argument('--budget', type=float, default=10.0, help='估算费用上限（元）')
    run_cmd.add_argument('--max-calls', type=int, default=900)
    analyze_cmd = sub.add_parser('analyze', help='离线重算对照数字')
    analyze_cmd.add_argument('archive')
    analyze_cmd.add_argument('--checksums', action='store_true', help='同时写出 SHA256SUMS.txt')
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    archive = Path(args.archive)
    if args.command == 'run':
        return run(archive, args.groups, args.budget, args.max_calls)
    print(json.dumps(analyze(archive), ensure_ascii=False, indent=2)[:4000])
    if args.checksums:
        write_checksums(archive)
    return 0


if __name__ == '__main__':
    sys.exit(main())
