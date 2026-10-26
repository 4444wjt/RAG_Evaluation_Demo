"""独立裁判对照：只换裁判、不重新生成回答，量化「裁判与被评模型同源」带来的偏差。

回答、检索上下文和参考答案全部取自已归档的运行，被评模型不会被再次调用；
只重算依赖裁判的 Faithfulness、Context Precision（门禁输入）与逐句 Trace。

  run      需要 API 密钥并产生费用；逐行追加写盘，可断点续跑；超预算或超调用数即停止。
  analyze  离线：从 raw.jsonl 重新计算全部对照数字，写出 summary.json 与 README.md。
"""
from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stdout
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
import math
import os
from pathlib import Path
import statistics
import sys
from threading import Lock
import tokenize

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
# 离线统计 docs/competition/judge-comparison/*/llm-events.jsonl 的全部 end：
# 20261011-qwen72b/llm-events.jsonl:548 同时给出最大提示 4475、输出 711 token。
# 向上留余量；CLI 只允许调高。较长输入另按 UTF-8 字节数保守提高预留。
RESERVE_PROMPT_TOKENS = 8192
RESERVE_COMPLETION_TOKENS = 2048
GATE_THRESHOLD = 0.8
HALLUCINATION = ('fact_error', 'fabrication')
LABELS = ('supported', 'hallucination', 'empty', 'error')


# ---------- 数据读取 ----------

def load_rows(source: str | Path) -> list[dict]:
    path = ROOT / SOURCES[source] if source in SOURCES else Path(source)
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.jsonl':
        rows = []
        required = ('model', 'rowIndex', 'question', 'answer', 'contexts', 'ground_truth', 'case_type')
        for line in text.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict) or any(key not in item for key in required):
                raise ValueError('JSONL 行缺少必需字段')
            rows.append(item)
    else:
        data = json.loads(text)
        model_dfs = data['result']['model_dfs'] if source == 'demo' else data['model_dfs']
        rows = []
        for model, items in model_dfs.items():
            for index, item in enumerate(items):
                rows.append({**item, 'model': model, 'rowIndex': index, 'case_type': item.get('case_type', 'positive')})
    keys = set()
    normalized = []
    for row in rows:
        if (not isinstance(row['model'], str) or not row['model'] or type(row['rowIndex']) is not int or row['rowIndex'] < 0
                or any(not isinstance(row[key], str) for key in ('question', 'answer', 'ground_truth', 'case_type'))):
            raise ValueError('输入行字段类型无效')
        key = (row['model'], row['rowIndex'])
        if key in keys:
            raise ValueError('输入中 model / rowIndex 重复')
        keys.add(key)
        normalized.append({'source': str(source), 'model': row['model'], 'rowIndex': row['rowIndex'],
                           'question': row['question'], 'answer': row['answer'], 'contexts': parse_contexts(row['contexts']),
                           'ground_truth': row['ground_truth'], 'case_type': row['case_type'],
                           'original': {'faithfulness': number(row.get('faithfulness')), 'context_precision': number(row.get('context_precision')),
                                        'trace': parse_trace(row.get('hallucination_trace'))}})
    return normalized


def parse_contexts(value) -> list[str]:
    """演示快照把 contexts 存成 numpy 数组的打印文本（字符串之间没有逗号），逐个取出字符串字面量。"""
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        tokens = [ast.literal_eval(tok.string) for tok in tokenize.generate_tokens(io.StringIO(value).readline) if tok.type == tokenize.STRING]
        if tokens:
            return tokens
    raise ValueError('无法解析 contexts')


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

class BudgetStopped(RuntimeError):
    """请求前拒绝；由 run 转为退出码 3，不当作模型错误收费。"""


class Budget:
    def __init__(self, limit_cny: float, max_calls: int, reserve_prompt_tokens: int = RESERVE_PROMPT_TOKENS,
                 reserve_completion_tokens: int = RESERVE_COMPLETION_TOKENS):
        if not math.isfinite(limit_cny) or limit_cny < 0 or max_calls < 0:
            raise ValueError('预算与调用上限必须为有限非负数')
        if reserve_prompt_tokens < RESERVE_PROMPT_TOKENS or reserve_completion_tokens < RESERVE_COMPLETION_TOKENS:
            raise ValueError('预留 token 只允许高于或等于默认值')
        self.limit, self.max_calls = Decimal(str(limit_cny)), max_calls
        self.reserve_prompt_tokens, self.reserve_completion_tokens = reserve_prompt_tokens, reserve_completion_tokens
        self.calls, self.cost = 0, Decimal(0)
        self.unknown_usage_calls, self.error_calls = 0, 0
        self.pending: dict[str, dict] = {}
        self.finished: set[str] = set()
        self.stop_reason: str | None = None
        self.lock = Lock()

    @staticmethod
    def charge(judge: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        price_in, price_out = (Decimal(str(price)) for price in PRICES[judge])
        return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000

    def reserve(self, run_id: str, judge: str, *, prompt_tokens: int | None = None,
                completion_tokens: int | None = None, restoring: bool = False) -> dict:
        with self.lock:
            if run_id in self.pending or run_id in self.finished:
                raise ValueError('重复的调用 runId')
            prompt_tokens = self.reserve_prompt_tokens if prompt_tokens is None else prompt_tokens
            completion_tokens = self.reserve_completion_tokens if completion_tokens is None else completion_tokens
            amount = self.charge(judge, prompt_tokens, completion_tokens)
            if not restoring:
                reason = self.stop_reason
                if not reason and self.calls >= self.max_calls:
                    reason = f'调用次数 {self.calls} 已达上限 {self.max_calls}'
                if not reason and self.cost + self.reserved_cost + amount > self.limit:
                    reason = f'剩余预算不足以预留 ¥{amount:.6f}（上限 ¥{self.limit}）'
                if reason:
                    self.stop_reason = reason
                    raise BudgetStopped(reason)
            reservation = {'judge': judge, 'promptTokens': prompt_tokens, 'completionTokens': completion_tokens,
                           'reservedCny': float(amount)}
            self.pending[run_id] = reservation
            self.calls += 1
            return reservation

    @property
    def reserved_cost(self) -> Decimal:
        return sum((self.charge(r['judge'], r['promptTokens'], r['completionTokens']) for r in self.pending.values()), Decimal(0))

    def settle(self, run_id: str, usage=None, *, error: bool = False) -> None:
        with self.lock:
            reservation = self.pending.pop(run_id, None)
            if reservation is None:  # 拒绝的 start 没有发出请求；重复终止回调也不重复收费。
                return
            self.finished.add(run_id)
            usage = usage if isinstance(usage, dict) else {}
            prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
            known = all(type(value) is int and value > 0 for value in (prompt, completion))
            if error or not known:
                prompt, completion = reservation['promptTokens'], reservation['completionTokens']
            self.cost += self.charge(reservation['judge'], prompt, completion)
            self.error_calls += int(error)
            self.unknown_usage_calls += int(not error and not known)

    def raise_if_stopped(self) -> None:
        if self.stop_reason:
            raise BudgetStopped(self.stop_reason)

    def snapshot(self) -> dict:
        with self.lock:
            return {'schemaVersion': 1, 'kind': 'reservation-estimate-not-billing-record', 'budgetCny': float(self.limit),
                    'maxCalls': self.max_calls, 'calls': self.calls, 'settledCny': float(self.cost),
                    'reservedCny': float(self.reserved_cost), 'inFlightCalls': len(self.pending),
                    'unknownUsageCalls': self.unknown_usage_calls, 'errorCalls': self.error_calls,
                    'reservePromptTokens': self.reserve_prompt_tokens, 'reserveCompletionTokens': self.reserve_completion_tokens,
                    'stopReason': self.stop_reason}


def restore_budget(archive: Path, budget: Budget) -> None:
    """按 runId 配对；中断的 start 按当时预留结算，兼容旧事件。"""
    path = archive / 'llm-events.jsonl'
    if not path.exists():
        return
    for index, line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event.get('event')
        if kind not in ('start', 'end', 'error'):
            continue
        run_id = event.get('runId') or f'legacy-{index}'
        if run_id in budget.finished:
            continue
        if run_id not in budget.pending:
            reservation = event.get('reservation') or {}
            budget.reserve(run_id, event.get('judge') or 'qwen72b',
                           prompt_tokens=reservation.get('promptTokens', RESERVE_PROMPT_TOKENS),
                           completion_tokens=reservation.get('completionTokens', RESERVE_COMPLETION_TOKENS), restoring=True)
        if kind in ('end', 'error'):
            budget.settle(run_id, (event.get('llmOutput') or {}).get('token_usage'), error=kind == 'error')
    for run_id in list(budget.pending):
        budget.settle(run_id)


def install_observer(archive: Path, budget: Budget, current: dict):
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tracers.context import register_configure_hook

    lock = Lock()

    class Observer(BaseCallbackHandler):
        raise_error = True
        run_inline = True

        def record(self, event: dict) -> None:
            event.update(at=datetime.now(timezone.utc).isoformat(), group=current.get('group'), judge=current.get('judge'))
            with lock, (archive / 'llm-events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            params = kwargs.get('invocation_params') or {}
            text = json.dumps([[m.content for m in batch] for batch in messages], ensure_ascii=False)
            # 对当前两个文本裁判，UTF-8 字节数加消息封装余量是保守提示 token 上界；不联网取 tokenizer。
            prompt = max(budget.reserve_prompt_tokens, len(text.encode('utf-8')) + 256 * sum(map(len, messages)))
            completion = max(budget.reserve_completion_tokens, params.get('max_tokens') or 0) * (params.get('n') or 1)
            reservation = budget.reserve(str(run_id), current['judge'], prompt_tokens=prompt, completion_tokens=completion)
            self.record({'event': 'start', 'runId': str(run_id), 'promptSha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                         'reservation': reservation,
                         'requestParameters': {k: params[k] for k in ('model', 'model_name', 'temperature', 'n', 'max_tokens') if k in params}})

        def on_llm_end(self, response, *, run_id, **kwargs):
            output = response.llm_output if isinstance(response.llm_output, dict) else {}
            usage = output.get('token_usage') or {}
            budget.settle(str(run_id), usage)
            self.record({'event': 'end', 'runId': str(run_id), 'llmOutput': output, 'generations': [[g.text for g in batch] for batch in response.generations]})

        def on_llm_error(self, error, *, run_id, **kwargs):
            if str(run_id) in budget.pending:
                budget.settle(str(run_id), error=True)
                self.record({'event': 'error', 'runId': str(run_id), 'errorType': type(error).__name__})

    observer_var = ContextVar('judge_compare_observer', default=None)
    token = observer_var.set(Observer())
    register_configure_hook(observer_var, True)
    return lambda: observer_var.reset(token)


def build_judge(judge: str):
    from backend.adapters.models.judging import build_judge as build_configured_judge
    spec = JUDGES[judge]
    return build_configured_judge({'name': spec['name'], 'base_url': os.environ.get(spec['base_url_env'], spec['base_url']), 'api_key_env': spec['api_key_env']})


def judge_row(row: dict, llm) -> dict:
    from datasets import Dataset
    from ragas import RunConfig, evaluate
    from ragas.metrics import ContextPrecision

    from backend.adapters.ragas_compat import ChineseFaithfulness
    from backend.config import EVAL_TIMEOUT
    from backend.services.trace import trace_hallucinations

    dataset = Dataset.from_dict({'question': [row['question']], 'answer': [row['answer']], 'contexts': [row['contexts']], 'ground_truth': [row['ground_truth']]})
    frame = evaluate(dataset=dataset, metrics=[ChineseFaithfulness(), ContextPrecision()], llm=llm,
                     run_config=RunConfig(max_workers=1, max_retries=1, timeout=EVAL_TIMEOUT), raise_exceptions=True).to_pandas()
    trace = trace_hallucinations(row['question'], row['answer'], row['contexts'], row['ground_truth'], llm)
    return {'faithfulness': number(frame['faithfulness'].iloc[0]), 'context_precision': number(frame['context_precision'].iloc[0]), 'trace': trace}


def configure_call_limits(llm, completion_tokens: int) -> None:
    """限制输出，关闭不经过 start 回调的 SDK 内部重试；每次真实尝试须单独预留。"""
    llm.max_tokens = completion_tokens
    llm.max_retries = 0
    for name in ('root_client', 'root_async_client'):
        client = getattr(llm, name, None)
        if client is not None:
            client.max_retries = 0


def run(archive: Path, groups: list[str], budget_cny: float, max_calls: int, *, input_path: Path | None = None,
        judge: str | None = None, group: str | None = None, reserve_prompt_tokens: int = RESERVE_PROMPT_TOKENS,
        reserve_completion_tokens: int = RESERVE_COMPLETION_TOKENS) -> int:
    os.environ.setdefault('MPLBACKEND', 'Agg')
    budget = Budget(budget_cny, max_calls, reserve_prompt_tokens, reserve_completion_tokens)
    if input_path is not None:
        if judge not in JUDGES or not group or not group.strip():
            raise ValueError('--input 必须同时指定 --judge 和非空 --group')
        input_path = input_path.resolve()
        custom_rows = load_rows(input_path)
        input_record = {'path': str(input_path), 'sha256': sha256(input_path)}
        selected = {group: (str(input_path), judge)}
    else:
        if judge is not None or group is not None:
            raise ValueError('--judge / --group 需要 --input')
        input_record = None
        selected = {g: GROUPS[g] for g in groups}
    request_path = archive / 'request.json'
    if request_path.exists():
        saved = json.loads(request_path.read_text(encoding='utf-8'))
        if saved.get('input') != input_record or (input_record is not None and saved['groups'] != {
                group: {'source': str(input_path), 'judge': JUDGES[judge]['name']}}):
            raise ValueError('续跑输入、SHA256 或裁判/组名与 request.json 不一致；请使用新目录')
    archive.mkdir(parents=True, exist_ok=True)

    if not request_path.exists():
        request = {'kind': 'judge-comparison', 'groups': {g: {'source': SOURCES.get(source, source), 'judge': JUDGES[j]['name']}
                                                       for g, (source, j) in selected.items()},
                   'metrics': ['faithfulness', 'context_precision', 'hallucination_trace'], 'gateThreshold': GATE_THRESHOLD, 'temperature': 0,
                   'budgetCny': budget_cny, 'maxCalls': max_calls, 'answersRegenerated': False,
                   'budgetPolicy': {'version': 1, 'reservePromptTokens': reserve_prompt_tokens,
                                    'reserveCompletionTokens': reserve_completion_tokens, 'sdkRetries': 0, 'ragasAttempts': 1},
                   'sourceSha256': {key: sha256(ROOT / path) for key, path in {**SOURCES, 'reviews': REVIEWS}.items()}}
        if input_record is not None:
            request['input'] = input_record
        write_json(request_path, request)
        write_json(archive / 'runtime.json', {'dependencies': dependency_versions()['relevant'], 'sources': source_versions(ROOT), 'python': sys.version.split()[0]})

    restore_budget(archive, budget)
    current: dict = {}
    cleanup = install_observer(archive, budget, current)
    try:
        done = {(r['group'], r['model'], r['rowIndex']) for r in read_raw(archive)}
        judges = {}
        for group, (source, judge) in selected.items():
            rows = custom_rows if input_record is not None else load_rows(source)
            remaining = [r for r in rows if (group, r['model'], r['rowIndex']) not in done]
            if not remaining:
                continue
            if judge not in judges:
                current.update(group='preflight', judge=judge)
                llm = build_judge(judge)
                configure_call_limits(llm, reserve_completion_tokens)
                reply = llm.invoke('只回复两个字母：OK')
                budget.raise_if_stopped()
                preflight = {'judge': judge, 'requestedModel': JUDGES[judge]['name'], 'returnedModel': (reply.response_metadata or {}).get('veriscope_response_model'), 'reply': str(reply.content)[:20]}
                with (archive / 'preflight.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(preflight, ensure_ascii=False) + '\n')
                print(f'预检 {judge}: 请求 {preflight["requestedModel"]}，返回 {preflight["returnedModel"]}')
                judges[judge] = llm
            for row in remaining:
                current.update(group=group, judge=judge)
                result = judge_row(row, judges[judge])
                budget.raise_if_stopped()  # Trace 等下层可能把拒绝包装成普通错误结果。
                entry = {'group': group, 'judge': judge, 'source': source, 'model': row['model'], 'rowIndex': row['rowIndex'], **result, 'at': datetime.now(timezone.utc).isoformat()}
                with (archive / 'raw.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
                print(f'[{group}] {row["model"]} #{row["rowIndex"]}  F={entry["faithfulness"]}  CP={entry["context_precision"]}  Trace={trace_label(entry["trace"])}  累计≈¥{budget.cost:.2f}/{budget.calls}次')
        return 0
    except Exception:
        if not budget.stop_reason:
            raise
        print(f'停止：{budget.stop_reason}；已保存的结果保留，可调整上限后续跑。')
        return 3
    finally:
        if cleanup is not None:
            cleanup()
        status = budget.snapshot()
        write_json(archive / 'budget.json', status)
        print('预算状态：' + json.dumps(status, ensure_ascii=False))


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
    run_cmd.add_argument('--groups', nargs='+', choices=list(GROUPS))
    run_cmd.add_argument('--input', type=Path, help='api-response.json 或逐行 JSONL；配合 --judge / --group')
    run_cmd.add_argument('--judge', choices=list(JUDGES))
    run_cmd.add_argument('--group', help='自定义输入的组名')
    run_cmd.add_argument('--budget', type=float, default=10.0, help='估算费用上限（元）')
    run_cmd.add_argument('--max-calls', type=int, default=900)
    run_cmd.add_argument('--reserve-prompt-tokens', type=int, default=RESERVE_PROMPT_TOKENS, help='每次请求的提示 token 最低预留，只能调高')
    run_cmd.add_argument('--reserve-completion-tokens', type=int, default=RESERVE_COMPLETION_TOKENS, help='每次请求的输出 token 预留与上限，只能调高')
    analyze_cmd = sub.add_parser('analyze', help='离线重算对照数字')
    analyze_cmd.add_argument('archive')
    analyze_cmd.add_argument('--checksums', action='store_true', help='同时写出 SHA256SUMS.txt')
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    archive = Path(args.archive)
    if args.command == 'run':
        if args.input is not None:
            if args.groups is not None or args.judge is None or not args.group or not args.group.strip():
                parser.error('--input 需要 --judge 和非空 --group，不能与 --groups 混用')
        elif args.judge is not None or args.group is not None:
            parser.error('--judge / --group 需要 --input')
        if (not math.isfinite(args.budget) or args.budget < 0 or args.max_calls < 0
                or args.reserve_prompt_tokens < RESERVE_PROMPT_TOKENS or args.reserve_completion_tokens < RESERVE_COMPLETION_TOKENS):
            parser.error('预算与调用上限必须非负且有限；预留 token 只允许调高')
        return run(archive, args.groups if args.groups is not None else list(GROUPS), args.budget, args.max_calls,
                   input_path=args.input, judge=args.judge, group=args.group,
                   reserve_prompt_tokens=args.reserve_prompt_tokens, reserve_completion_tokens=args.reserve_completion_tokens)
    print(json.dumps(analyze(archive), ensure_ascii=False, indent=2)[:4000])
    if args.checksums:
        write_checksums(archive)
    return 0


if __name__ == '__main__':
    sys.exit(main())
