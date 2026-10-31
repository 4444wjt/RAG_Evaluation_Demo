"""PREREG-RAGT-v6: deterministic preparation, guarded calls and offline analysis.

prepare --dir DIR --source-root SOURCE; authorize --dir DIR --phase trial|formal
--by NAME; run --dir DIR --phase trial|formal; analyze --dir DIR [--output PUBLIC
--prereg PREREG]. A public archive can itself be passed to analyze --dir.
Only run reads OPENAI_API_KEY and uses the network. Importing backend.services
loads the repository-local .env via backend/config.py, as in external_validation.py.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path, PureWindowsPath
import random
import re
from statistics import median
import sys
from types import SimpleNamespace
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.trace import TRACE_PROMPT_TEMPLATE, trace_hallucinations
from tools.competition.external_validation import (
    TOOL_FILES, _rate, _trace_prompt_chars, sha256_bytes, sha256_file,
)

SOURCE_COMMIT = 'c103204b9ce28d6bbad859304bf30de72b8ed8fe'
SOURCE_HASHES = {
    'response.jsonl': 'e4c2e4ac24fff676d8984cc61c35d791612fadc58015335d97dd632375e18073',
    'source_info.jsonl': '0dffc26ea9f3c1c3d7c7e8336b56ef1646e3cec876edffcca3c9c624d12d578b',
}
SEED = 20261031
TASKS = ('QA', 'Summary', 'Data2txt')
GROUP_SIZES = {'N2': 300, 'N1': 100}
REFUSAL = 'unable to answer based on given passages'
ENDPOINT = 'https://api.deepseek.com/chat/completions'
MODELS_ENDPOINT = 'https://api.deepseek.com/models'
REQUEST_PARAMETERS = {
    'model': 'deepseek-flash', 'temperature': 0, 'max_tokens': 4096,
    'thinking': {'type': 'disabled'}, 'stream': False,
}
TIMEOUT = 180
R_MAX = 1_048_576 * 2 / 1e6 + 4096 * 8 / 1e6
HARD_CAP = 15
HASHED_TOOLS = ('tools/competition/ragtruth_validation.py', *TOOL_FILES)
FAILED_TRACE = ('call_error', 'parse_error', 'invalid_structure')
UNVERIFIED_TRACE = (*FAILED_TRACE, 'empty_answer', 'model_empty', 'filtered_all')
CONCLUSIONS = ('PASS', 'FAIL', 'UNVERIFIED', 'NOT_RUN')
INCOMPLETE_ARCHIVE_WORDING = '归档不完整，本批次不引用。'
REQUIRED_ARCHIVE_ARTIFACTS = (
    'items.json', 'summary.json', 'sample_ids.json', 'archive.json', 'prereg.md',
    *(f'{phase}/{name}' for phase in ('trial', 'formal')
      for name in ('authorization.json', 'ledger.jsonl', 'log.jsonl', 'run.json')),
)
THINK_TAG = re.compile(r'</?(think|thinking|analysis|reasoning)>', re.I)


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def read_json(path: Path):
    return json.loads(path.read_bytes())


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def append_jsonl(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    with path.open('ab') as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def has_fixed_refusal(answer: str) -> bool:
    return REFUSAL in answer.lower()


def map_response(response: dict, source: dict) -> dict:
    """Map only registered fields; labels never enter the trace input."""
    for field in ('id', 'source_id', 'model', 'response', 'split', 'quality'):
        if not isinstance(response.get(field), str):
            raise TypeError(f'response.{field} must be a string')
    if not response['response'] or not isinstance(response.get('labels'), list):
        raise ValueError('response or labels has an invalid type/value')
    if response['split'] not in ('train', 'test'):
        raise ValueError('unknown split')
    if response['quality'] not in ('good', 'incorrect_refusal', 'truncated'):
        raise ValueError('unknown quality')
    if source.get('source_id') != response['source_id']:
        raise ValueError('source_id does not join')
    task, info, prompt = source.get('task_type'), source.get('source_info'), source.get('prompt')
    if not isinstance(prompt, str):
        raise TypeError('prompt must be a string')
    if task == 'QA':
        if not isinstance(info, dict) or any(not isinstance(info.get(k), str) for k in ('question', 'passages')):
            raise TypeError('QA source_info must contain string question and passages')
        question, contexts = info['question'], [info['passages']]
    elif task == 'Summary':
        if not isinstance(info, str) or not info or prompt.count(info) != 1:
            raise ValueError('Summary source_info must be a unique nonempty prompt anchor')
        question, contexts = prompt[:prompt.index(info)].strip(), [info]
    elif task == 'Data2txt':
        if not isinstance(info, dict):
            raise TypeError('Data2txt source_info must be a dict')
        if prompt.count('Structured data:') != 1:
            raise ValueError('Data2txt requires one Structured data: anchor')
        question = prompt.split('Structured data:', 1)[0].strip()
        contexts = [json.dumps(info, ensure_ascii=False)]
    else:
        raise ValueError('unknown task_type')
    return {
        'id': response['id'], 'source_id': response['source_id'], 'task': task,
        'group': 'N1' if response['labels'] else 'N2', 'quality': response['quality'],
        'generated_model': response['model'], 'fixed_refusal': has_fixed_refusal(response['response']),
        'split': response['split'], 'qa_prompt_has_refusal': task != 'QA' or has_fixed_refusal(prompt),
        'question': question, 'contexts': contexts, 'answer': response['response'], 'ground_truth': '',
    }


def eligible(item: dict, split='test') -> bool:
    return (item['split'] == split and sum(len(c) for c in item['contexts']) >= 200
            and not item['fixed_refusal'])


def allocate_quotas(source_counts: dict, total: int) -> dict:
    """Integer remainders avoid floating point tie-breaking differences."""
    if type(total) is not int or total < 0:
        raise ValueError('sample size must be a nonnegative integer')
    denominator = sum(source_counts[t] for t in TASKS)
    if not denominator:
        if total:
            raise ValueError('no eligible sources')
        return dict.fromkeys(TASKS, 0)
    quotas = {t: total * source_counts[t] // denominator for t in TASKS}
    order = sorted(TASKS, key=lambda t: (-(total * source_counts[t] % denominator), TASKS.index(t)))
    for task in order[:total - sum(quotas.values())]:
        quotas[task] += 1
    if any(quotas[t] > source_counts[t] for t in TASKS):
        raise ValueError('task stratum has fewer eligible sources than its quota')
    return quotas


def sample_candidates(candidates: list, group_sizes=None) -> tuple[list, list, dict]:
    sizes = GROUP_SIZES if group_sizes is None else group_sizes
    rng, formal = random.Random(SEED), []
    for group in ('N2', 'N1'):
        strata = {task: {} for task in TASKS}
        for item in candidates:
            if eligible(item) and item['group'] == group:
                strata[item['task']].setdefault(item['source_id'], []).append(item)
        quotas = allocate_quotas({t: len(strata[t]) for t in TASKS}, sizes[group])
        for task in TASKS:
            chosen = rng.sample(sorted(strata[task]), quotas[task])
            for source_id in sorted(chosen):
                formal.append(rng.choice(sorted(strata[task][source_id], key=lambda i: i['id'])))
    order = [item['id'] for item in formal]
    rng.shuffle(order)
    formal_sources = {item['source_id'] for item in formal}
    train = sorted((i for i in candidates if eligible(i, 'train') and i['source_id'] not in formal_sources),
                   key=lambda i: i['id'])
    rng.shuffle(train)
    trial = []
    for task in TASKS:
        first = next((i for i in train if i['task'] == task), None)
        if first is None:
            raise ValueError('trial requires each task')
        trial.append(first)
    selected = {i['id'] for i in trial}
    trial.extend([i for i in train if i['id'] not in selected][:2])
    if len(trial) != 5:
        raise ValueError('trial requires five eligible answers')
    ids = {'formal_ids': [i['id'] for i in formal], 'run_order': order, 'trial_ids': [i['id'] for i in trial]}
    return formal, trial, ids


def describe_pool(items: list) -> dict:
    lengths = [sum(map(len, i['contexts'])) for i in items]
    return {
        'answers': len(items), 'sources': len({i['source_id'] for i in items}),
        'quality': dict(sorted(Counter(i['quality'] for i in items).items())),
        'contexts_chars': {'min': min(lengths) if lengths else None,
                           'median': median(lengths) if lengths else None,
                           'max': max(lengths) if lengths else None},
    }


def estimate(jobs: list) -> dict:
    input_tokens = sum(_trace_prompt_chars(i['question'], i['answer'], i['contexts']) for i in jobs)
    output_tokens = 800 * len(jobs)
    cost = (2 * input_tokens + 8 * output_tokens) / 1_000_000
    return {'calls': len(jobs), 'input_tokens_estimate': input_tokens, 'output_tokens_estimate': output_tokens,
            'cost_cny_estimate': cost, 'within_planning_limit': budget_allows(cost),
            'assumption': 'Prompt characters as tokens plus 800 output tokens per call; not a cost bound.'}


def prepare(directory: Path, source_root: Path, *, verify_hashes=True, group_sizes=None) -> dict:
    hashes = {name: sha256_file(source_root / name) for name in SOURCE_HASHES}
    if verify_hashes and hashes != SOURCE_HASHES:
        raise ValueError('source SHA-256 mismatch')
    sources = {}
    for source in read_jsonl(source_root / 'source_info.jsonl'):
        source_id = source['source_id']
        if not isinstance(source_id, str) or source_id in sources:
            raise ValueError('invalid or duplicate source_id')
        sources[source_id] = source
    candidates, seen = [], set()
    for response in read_jsonl(source_root / 'response.jsonl'):
        item = map_response(response, sources[response['source_id']])
        if item['id'] in seen:
            raise ValueError('duplicate response id')
        seen.add(item['id'])
        candidates.append(item)
    formal, trial, ids = sample_candidates(candidates, group_sizes)
    pool = [i for i in candidates if eligible(i)]
    stats = {}
    for group in ('N2', 'N1'):
        group_pool = [i for i in pool if i['group'] == group]
        stats[group] = {**describe_pool(group_pool), 'by_task': {
            task: {'pool': describe_pool([i for i in group_pool if i['task'] == task]),
                   'sample': describe_pool([i for i in formal if i['group'] == group and i['task'] == task])}
            for task in TASKS}}
    exclusions = {group: {task: dict.fromkeys(('not_test', 'short_context', 'fixed_refusal'), 0)
                          for task in TASKS} for group in ('N2', 'N1')}
    for item in candidates:
        reason = ('not_test' if item['split'] != 'test' else
                  'short_context' if sum(map(len, item['contexts'])) < 200 else
                  'fixed_refusal' if item['fixed_refusal'] else None)
        if reason:
            exclusions[item['group']][item['task']][reason] += 1
    jobs = [{**{k: v for k, v in i.items() if k not in ('split', 'qa_prompt_has_refusal', 'fixed_refusal')}, 'phase': phase}
            for phase, selected in (('formal', formal), ('trial', trial)) for i in selected]
    package = {
        'schema_version': 1, 'preregistration': 'PREREG-RAGT-v6', 'seed': SEED,
        'source_commit': SOURCE_COMMIT, 'source_hashes': hashes,
        'trace_prompt_sha256': sha256_bytes(TRACE_PROMPT_TEMPLATE.encode('utf-8')),
        'sample_ids_sha256': sha256_bytes(json_bytes(ids)), 'jobs': jobs, 'statistics': stats,
        'exclusions': exclusions,
        'eligible_qa_prompts_without_refusal': sum(i['task'] == 'QA' and not i['qa_prompt_has_refusal'] for i in pool),
        'estimate': estimate(jobs),
    }
    write_json(directory / 'package.json', package)
    write_json(directory / 'sample_ids.json', ids)
    for phase in ('trial', 'formal'):
        (directory / phase).mkdir(parents=True, exist_ok=True)
    return package


def tool_hashes(root: Path = ROOT) -> dict:
    return {name: sha256_bytes((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in HASHED_TOOLS}


def load_package(directory: Path) -> tuple[dict, dict]:
    package, ids = read_json(directory / 'package.json'), read_json(directory / 'sample_ids.json')
    if package['sample_ids_sha256'] != sha256_file(directory / 'sample_ids.json'):
        raise PermissionError('sample_ids hash mismatch')
    if set(ids['formal_ids']) != set(ids['run_order']) or len(set(ids['run_order'])) != len(ids['run_order']):
        raise ValueError('invalid run order')
    for phase, key in (('trial', 'trial_ids'), ('formal', 'formal_ids')):
        if {i['id'] for i in package['jobs'] if i['phase'] == phase} != set(ids[key]):
            raise ValueError('package IDs do not match sample_ids')
    return package, ids


def budget_allows(spent) -> bool:
    return Decimal(str(spent)) + Decimal(str(R_MAX)) <= Decimal(str(HARD_CAP))


def settle_usage(usage) -> dict:
    valid = isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                                           for k in ('prompt_tokens', 'completion_tokens'))
    if not valid:
        return {'basis': 'R_MAX', 'cost_cny': R_MAX, 'usage': None}
    tokens = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens')}
    cost = (Decimal(tokens['prompt_tokens']) * 2 + Decimal(tokens['completion_tokens']) * 8) / 1_000_000
    return {'basis': 'usage', 'cost_cny': float(cost), 'usage': tokens}


def ledger_total(rows: list) -> Decimal:
    return sum((Decimal(str(r['cost_cny'])) for r in rows if r['state'] == 'settled'), Decimal(0))


class Ledger:
    def __init__(self, path: Path):
        self.path, self.rows = path, read_jsonl(path)

    @property
    def total(self) -> Decimal:
        return ledger_total(self.rows)

    def append(self, row: dict) -> None:
        append_jsonl(self.path, row)
        self.rows.append(row)

    def pending(self, item_id: str, spent, started_at: str) -> None:
        self.append({'id': item_id, 'state': 'pending', 'spent_before_cny': float(spent), 'started_at': started_at})

    def settle(self, item_id: str, usage, **extra) -> dict:
        row = {'id': item_id, 'state': 'settled', **settle_usage(usage), 'ended_at': now(), **extra}
        self.append(row)
        return row

    def recover(self, records_path: Path) -> list:
        records = read_jsonl(records_path)
        done = {r['id'] for r in records}
        pending = {r['id']: r for r in self.rows if r['state'] == 'pending'}
        settled = {r['id']: r for r in self.rows if r['state'] == 'settled'}
        for item_id, entry in pending.items():
            if item_id not in settled:
                settled[item_id] = self.settle(item_id, None, interrupted=True, http_status=None,
                                               returned_model=None, s5_reason=None, raw_response_sha256=None)
            if item_id not in done:
                row = settled[item_id]
                record = {'id': item_id, 'status': 'interrupted', 'trace': None,
                          'call': {**{k: v for k, v in row.items() if k not in ('id', 'state')},
                                   'started_at': entry['started_at'], 'sent': True}}
                append_jsonl(records_path, record)
                records.append(record)
        return records


@dataclass
class HTTPResponse:
    status: int
    body: bytes


class HTTPStatusError(RuntimeError):
    def __init__(self, status):
        self.http_status = status
        super().__init__(f'HTTP {status}')


class BatchInvalid(RuntimeError):
    pass


class ProbeError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'redirect rejected', headers, fp)


def urllib_transport(request, timeout=TIMEOUT) -> HTTPResponse:
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.getcode()
            return HTTPResponse(status, response.read() if status == 200 else response.read(65_536))
    except urllib.error.HTTPError as error:
        try:
            return HTTPResponse(error.code, error.read(65_536))
        finally:
            error.close()


def request_body(prompt: str) -> dict:
    return {**json.loads(json.dumps(REQUEST_PARAMETERS)), 'messages': [{'role': 'user', 'content': prompt}]}


def _nonempty(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple)):
        return len(value) > 0
    return True


def check_response_s5(payload: dict, baseline: str | None) -> tuple[str | None, str | None]:
    model = payload.get('model')
    if not isinstance(model, str) or not model.strip():
        return 'missing_model', baseline
    if baseline is not None and model != baseline:
        return 'model_changed', baseline
    baseline = model
    choices = payload.get('choices')
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = first.get('message')
    if isinstance(message, dict):
        if any(_nonempty(message.get(k)) for k in ('reasoning_content', 'reasoning', 'thinking')):
            return 'reasoning_field', baseline
        content = message.get('content')
        if isinstance(content, str) and THINK_TAG.search(content):
            return 'reasoning_tag', baseline
    return None, baseline


class DeepSeekAdapter:
    """A single transport attempt, with durable accounting before returning to trace."""
    def __init__(self, api_key, item_id: str, phase_dir: Path, authorization: dict,
                 ledger: Ledger, spent, baseline=None, transport=None):
        self._api_key, self.item_id, self.phase_dir = api_key, item_id, phase_dir
        self.authorization, self.ledger, self.spent = authorization, ledger, spent
        self.baseline, self.transport = baseline, transport or urllib_transport
        self.call = {'sent': False}
        self.s5_reason = None

    def _finish(self, usage, raw) -> None:
        settlement = self.ledger.settle(self.item_id, usage, http_status=self.call.get('http_status'),
                                        returned_model=self.call.get('returned_model'), s5_reason=self.s5_reason,
                                        completion=self.call.get('completion'),
                                        raw_response_sha256=self.call.get('raw_response_sha256'))
        self.call.update({k: v for k, v in settlement.items() if k not in ('id', 'state')})
        if raw is not None:
            raw_path = self.phase_dir / 'raw' / f'{self.item_id}.json'
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw)

    def invoke(self, prompt):
        body = request_body(prompt)
        if {k: v for k, v in body.items() if k != 'messages'} != self.authorization['request_parameters']:
            self.s5_reason = 'request_parameters'
            raise BatchInvalid('request parameters differ from authorization')
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8'),
                                         headers={'Authorization': f'Bearer {self._api_key}', 'Content-Type': 'application/json'},
                                         method='POST')
        self.call.update(started_at=now(), prompt_sha256=sha256_bytes(prompt.encode('utf-8')))
        self.ledger.pending(self.item_id, self.spent, self.call['started_at'])
        self.call['sent'] = True
        usage, raw = None, None
        try:
            response = self.transport(request, TIMEOUT)
            self.call['http_status'] = response.status
            if response.status == 200:
                self.call.update(completion=False, returned_model=None)
            raw = response.body if response.status == 200 else response.body[:65_536]
            self.call['raw_response_sha256'] = sha256_bytes(raw)
            if response.status != 200:
                raise HTTPStatusError(response.status)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('response must be an object')
            usage = payload.get('usage')
            if not isinstance(payload.get('choices'), list) or not payload['choices']:
                raise ValueError('response must contain a nonempty choices list')
            self.call['completion'] = True
            self.s5_reason, self.baseline = check_response_s5(payload, self.baseline)
            self.call['returned_model'] = payload.get('model')
            if self.s5_reason:
                raise BatchInvalid(self.s5_reason)
            choice = payload['choices'][0]
            self.call['finish_reason'] = choice.get('finish_reason') if isinstance(choice, dict) else None
            content = choice['message']['content']
            if content is None:
                content = ''
            if not isinstance(content, str):
                raise TypeError('response content must be a string or null')
            result = SimpleNamespace(content=content, response_metadata={
                'model_name': payload.get('model'), 'finish_reason': choice.get('finish_reason')})
        except Exception as error:
            self.call['error_type'] = type(error).__name__
            if isinstance(error, HTTPStatusError):
                self.call['http_status'] = error.http_status
            self._finish(usage, raw)
            raise
        self._finish(usage, raw)
        return result


def stopped_by_failures(records: list, phase='formal') -> str | None:
    if phase != 'formal':
        return None
    consecutive = 0
    for record in reversed(records):
        if record['status'] == 'call_error' or (record.get('trace') or {}).get('trace_status') == 'call_error':
            consecutive += 1
        else:
            break
    if consecutive >= 20:
        return 'S3'
    failures = sum(r['status'] != 'ok' or (r.get('trace') or {}).get('trace_status') in FAILED_TRACE for r in records)
    if len(records) >= 50 and failures * 10 > len(records):
        return 'S2'
    return None


def _invalidated(directory: Path) -> bool:
    for phase in ('trial', 'formal'):
        state_path = directory / phase / 'run.json'
        if state_path.exists() and read_json(state_path).get('invalidated'):
            return True
        if any(r.get('call', {}).get('s5_reason') for r in read_jsonl(directory / phase / 'records.jsonl')):
            return True
        if any(r.get('s5_reason') for r in read_jsonl(directory / phase / 'ledger.jsonl')):
            return True
    return False


def trial_binding(directory: Path, ids: dict) -> dict:
    records_path, ledger_path = directory / 'trial' / 'records.jsonl', directory / 'trial' / 'ledger.jsonl'
    records, rows = read_jsonl(records_path), read_jsonl(ledger_path)
    if _invalidated(directory):
        raise PermissionError('trial/batch triggered S5')
    if len(records) != 5 or {r['id'] for r in records} != set(ids['trial_ids']):
        raise PermissionError('trial is not complete')
    settled = [r for r in rows if r['state'] == 'settled']
    if len(settled) != 5 or {r['id'] for r in settled} != set(ids['trial_ids']):
        raise PermissionError('trial ledger is incomplete')
    calls = {r['id']: r.get('call', {}) for r in records}
    baseline = next((calls[i].get('returned_model') for i in ids['trial_ids']
                     if calls[i].get('completion') is True), None)
    if not isinstance(baseline, str) or not baseline.strip():
        raise PermissionError('trial has no nonempty model baseline')
    usage_calls = [r for r in settled if r['basis'] == 'usage']
    if not usage_calls:
        raise PermissionError('trial has no usage settlements')
    total = ledger_total(rows)
    mean = ledger_total(usage_calls) / len(usage_calls)
    if total + 400 * mean > Decimal(str(HARD_CAP)) - Decimal(str(R_MAX)):
        raise PermissionError('trial extrapolation exceeds planning budget')
    return {'trial_ledger_sha256': sha256_file(ledger_path), 'trial_records_sha256': sha256_file(records_path),
            'trial_total_cny': float(total), 'model_baseline': baseline}


def _auth_values(directory: Path, phase: str) -> dict:
    if phase not in ('trial', 'formal'):
        raise ValueError('unknown phase')
    package, ids = load_package(directory)
    values = {'phase': phase, 'package_sha256': sha256_file(directory / 'package.json'),
              'sample_ids_sha256': package['sample_ids_sha256'], 'tool_sha256': tool_hashes(),
              'request_parameters': json.loads(json.dumps(REQUEST_PARAMETERS)), 'endpoint': ENDPOINT,
              'timeout_seconds': TIMEOUT, 'max_retries': 0, 'max_requests': 5 if phase == 'trial' else 400,
              'hard_cap_cny': HARD_CAP, 'r_max_cny': R_MAX,
              'price': {'input_cny_per_million': 2, 'output_cny_per_million': 8}}
    if phase == 'formal':
        values.update(trial_binding(directory, ids))
    return values


def authorize(directory: Path, phase: str, by: str) -> dict:
    auth = {**_auth_values(directory, phase), 'authorized_by': by, 'authorized_at': now()}
    write_json(directory / phase / 'authorization.json', auth)
    return auth


@contextmanager
def run_lock(directory: Path):
    path = directory / 'run.lock'
    try:
        handle = path.open('xb')
    except FileExistsError:
        raise PermissionError('run.lock already exists') from None
    handle.close()
    try:
        yield
    finally:
        path.unlink()


def probe_models(api_key, transport, log_path: Path) -> None:
    request = urllib.request.Request(MODELS_ENDPOINT, headers={'Authorization': f'Bearer {api_key}'}, method='GET')
    try:
        response = transport(request, TIMEOUT)
        available = False
        if response.status == 200:
            data = json.loads(response.body)
            available = any(isinstance(m, dict) and m.get('id') == 'deepseek-flash' for m in data.get('data', []))
        append_jsonl(log_path, {'event': 'models_probe', 'time': now(), 'http_status': response.status,
                               'deepseek_flash_available': available})
    except Exception as error:
        append_jsonl(log_path, {'event': 'models_probe', 'time': now(), 'error_type': type(error).__name__})
        raise ProbeError('models probe failed') from None
    if response.status != 200 or not available:
        raise ProbeError('models probe failed')


def run(directory: Path, phase: str, *, transport=None) -> dict:
    if os.environ.get('VS_ALLOW_PAID') != '1':
        raise PermissionError('VS_ALLOW_PAID=1 is required')
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise PermissionError('OPENAI_API_KEY is required')
    expected = _auth_values(directory, phase)
    auth = read_json(directory / phase / 'authorization.json')
    if any(auth.get(k) != value for k, value in expected.items()):
        raise PermissionError('authorization hashes or parameters no longer match')
    package, ids = load_package(directory)
    jobs = {i['id']: i for i in package['jobs'] if i['phase'] == phase}
    order = ids['trial_ids'] if phase == 'trial' else ids['run_order']
    phase_dir = directory / phase
    records_path, state_path = phase_dir / 'records.jsonl', phase_dir / 'run.json'
    transport = transport or urllib_transport
    with run_lock(directory):
        if state_path.exists():
            previous = read_json(state_path)
            if previous.get('stopped') in ('S1', 'S2', 'S3', 'S5') or previous.get('completed') == len(order):
                return previous
        if _invalidated(directory):
            raise PermissionError('batch has already triggered S5')
        probe_models(api_key, transport, phase_dir / 'log.jsonl')
        ledger = Ledger(phase_dir / 'ledger.jsonl')
        records = ledger.recover(records_path)
        done = {r['id'] for r in records}
        baseline = auth.get('model_baseline')
        if baseline is None:
            calls = {r['id']: r.get('call', {}) for r in records}
            baseline = next((calls[i].get('returned_model') for i in order
                             if i in calls and calls[i].get('completion') is True), None)
        stopped = 'S5' if _invalidated(directory) else stopped_by_failures(records, phase)
        trial_total = Decimal(str(auth.get('trial_total_cny', 0)))
        for item_id in order:
            if stopped:
                break
            if item_id in done:
                continue
            if len([r for r in ledger.rows if r['state'] == 'pending']) >= auth['max_requests']:
                raise PermissionError('authorized request count exhausted')
            spent = trial_total + ledger.total
            if not budget_allows(spent):
                stopped = 'S1'
                break
            adapter = DeepSeekAdapter(api_key, item_id, phase_dir, auth, ledger, spent, baseline, transport)
            job = jobs[item_id]
            record = {'id': item_id, 'status': 'ok'}
            try:
                record['trace'] = trace_hallucinations(job['question'], job['answer'], job['contexts'], '', adapter)
            except Exception as error:
                record.update(status='error', trace=None, error_type=type(error).__name__)
            baseline = adapter.baseline
            record['call'] = adapter.call
            if adapter.call['sent']:
                append_jsonl(records_path, record)
                records.append(record)
                done.add(item_id)
            if adapter.s5_reason:
                stopped = 'S5'
            else:
                stopped = stopped_by_failures(records, phase)
            state = {'phase': phase, 'completed': len(records), 'requests': len(done), 'stopped': stopped,
                     'invalidated': stopped == 'S5', 'model_baseline': baseline,
                     'spent_cny': float(trial_total + ledger.total)}
            write_json(state_path, state)
        state = {'phase': phase, 'completed': len(records), 'requests': len(done), 'stopped': stopped,
                 'invalidated': stopped == 'S5', 'model_baseline': baseline,
                 'spent_cny': float(trial_total + ledger.total)}
        write_json(state_path, state)
        return state


def conclusion(record: dict | None) -> str:
    if record is None:
        return 'NOT_RUN'
    if record['status'] != 'ok':
        return 'UNVERIFIED'
    trace = record['trace']
    status = trace['trace_status']
    if status in ('ok', 'partial'):
        return 'FAIL' if any(s['verdict'] in ('fact_error', 'fabrication') for s in trace['statements']) else 'PASS'
    if status in UNVERIFIED_TRACE:
        return 'UNVERIFIED'
    raise ValueError('unknown trace_status')


def public_record(job: dict, record: dict | None) -> dict:
    trace = record.get('trace') if record else None
    diagnostics = trace.get('trace_diagnostics', {}) if trace is not None else {}
    call = record.get('call', {}) if record else {}
    raw_output = diagnostics.get('raw_output')
    item = {k: job[k] for k in ('id', 'group', 'task', 'quality', 'generated_model')}
    item.update({
        'conclusion': conclusion(record), 'outer_status': record['status'] if record else None,
        'trace_status': trace['trace_status'] if trace is not None else None,
        'verdicts': [s['verdict'] for s in trace['statements']] if trace is not None else None,
        'rejected_reasons': [r['reason'] for r in diagnostics.get('rejected_statements', [])] if trace is not None else None,
        'usage': call.get('usage') if trace is not None else None,
        'returned_model': call.get('returned_model') if trace is not None else None,
        'finish_reason': call.get('finish_reason') if trace is not None else None,
        'statement_hashes': [{'text_sha256': sha256_bytes(s['text'].encode('utf-8')),
                              'evidence_sha256': sha256_bytes(s['evidence'].encode('utf-8'))}
                             for s in trace['statements']] if trace is not None else None,
        'raw_output_sha256': sha256_bytes(raw_output.encode('utf-8')) if isinstance(raw_output, str) else None,
        'raw_response_sha256': call.get('raw_response_sha256'),
        'error_type': call.get('error_type') or (record.get('error_type') if record else None),
        'http_status': call.get('http_status'), 'started_at': call.get('started_at'),
        'ended_at': call.get('ended_at'), 'settlement_basis': call.get('basis'),
        'cost_cny': call.get('cost_cny'), 's5_reason': call.get('s5_reason'),
    })
    return item


def public_items(directory: Path) -> dict:
    package, ids = load_package(directory)
    result = {'package_sha256': sha256_file(directory / 'package.json'),
              'sample_ids_sha256': package['sample_ids_sha256'], 'seed': package['seed'],
              'source_commit': package['source_commit'], 'source_hashes': package['source_hashes'],
              'invalidated': _invalidated(directory)}
    for phase, key in (('formal', 'formal_ids'), ('trial', 'trial_ids')):
        jobs = {i['id']: i for i in package['jobs'] if i['phase'] == phase}
        records = {r['id']: r for r in read_jsonl(directory / phase / 'records.jsonl')}
        result[phase] = [public_record(jobs[item_id], records.get(item_id)) for item_id in ids[key]]
    return result


def _metrics(items: list) -> dict:
    n2 = [i for i in items if i['group'] == 'N2']
    n1 = [i for i in items if i['group'] == 'N1']

    def valid_rate(rows):
        valid = [i for i in rows if i['conclusion'] in ('PASS', 'FAIL')]
        return _rate(sum(i['conclusion'] == 'FAIL' for i in valid), len(valid))

    return {'M2': valid_rate(n2), 'M2_conservative': _rate(sum(i['conclusion'] != 'PASS' for i in n2), len(n2)),
            'M1': valid_rate(n1), 'M2_ok': valid_rate([i for i in n2 if i['trace_status'] == 'ok'])}


def _counts(items: list) -> dict:
    return {
        'sampled': len(items), 'conclusions': {c: sum(i['conclusion'] == c for i in items) for c in CONCLUSIONS},
        'trace_status': dict(Counter(i['trace_status'] for i in items if i['trace_status'] is not None)),
        'partial': sum(i['trace_status'] == 'partial' for i in items),
        'unverified_status': dict(Counter(i['outer_status'] if i['outer_status'] != 'ok' else i['trace_status']
                                          for i in items if i['conclusion'] == 'UNVERIFIED')),
        'not_run': sum(i['conclusion'] == 'NOT_RUN' for i in items),
        'finish_reason_length': sum(i['finish_reason'] == 'length' for i in items),
        'rejected_reasons': dict(Counter(r for i in items for r in (i['rejected_reasons'] or []))),
        'quality': dict(Counter(i['quality'] for i in items)),
        'generated_model': dict(Counter(i['generated_model'] for i in items)),
    }


def analyze_items(items: list, *, invalidated=False) -> dict:
    if any(i['conclusion'] not in CONCLUSIONS or i['group'] not in GROUP_SIZES or i['task'] not in TASKS for i in items):
        raise ValueError('unknown public record classification')
    if len({i['id'] for i in items}) != len(items):
        raise ValueError('duplicate public record ID')
    metrics = _metrics(items)
    n1_valid, n1_fail = metrics['M1']['total'], metrics['M1']['count']
    s4 = 'N1_NO_VALID' if not n1_valid else 'M1_BELOW_5_PERCENT' if n1_fail * 20 < n1_valid else None
    invalidated = invalidated or any(i.get('s5_reason') for i in items)
    reportable = metrics['M2']['total'] >= 250 and s4 is None and not invalidated
    if invalidated:
        wording = '本批次作废，不引用任何数字。'
    elif s4 == 'N1_NO_VALID':
        wording = 'N1 没有有效结果，本次 M2 不作解读。'
    elif s4:
        wording = '核对在该数据上几乎不触发，本次 M2 不作解读。'
    elif not reportable:
        wording = '本次未取得可报告的结论。'
    else:
        m2, conservative = metrics['M2'], metrics['M2_conservative']
        n2 = [i for i in items if i['group'] == 'N2']
        k = sum(i['conclusion'] == 'UNVERIFIED' for i in n2)
        m = sum(i['conclusion'] == 'NOT_RUN' for i in n2)
        low, high = m2['wilson95']
        low_c, high_c = conservative['wilson95']
        wording = (f'RAGTruth 测试集按登记抽取 {len(n2)} 条无标注、非固定拒答的回答，逐句核对判 FAIL 的比例为 '
                   f'{m2["rate"] * 100:.2f}%（95% CI {low * 100:.2f}–{high * 100:.2f}%，有效 {m2["total"]}）；'
                   f'未核验 {k} 条、未执行 {m} 条，全部计为 FAIL 时为 {conservative["rate"] * 100:.2f}%'
                   f'（95% CI {low_c * 100:.2f}–{high_c * 100:.2f}%）。单位为整条回答。')
    return {
        'metrics': metrics, 'counts': _counts(items),
        'by_group': {g: _counts([i for i in items if i['group'] == g]) for g in ('N2', 'N1')},
        'by_task': {task: {'metrics': _metrics([i for i in items if i['task'] == task]),
                           'counts': _counts([i for i in items if i['task'] == task])} for task in TASKS},
        'S4': s4, 'invalidated': bool(invalidated), 'reportable': reportable, 'result_wording': wording,
    }


def load_public_archive(directory: Path) -> tuple[dict, dict]:
    names = set()
    try:
        for line in (directory / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
            digest, name = line.split('  ', 1)
            relative = PureWindowsPath(name)
            if relative.anchor or Path(name).anchor or '..' in relative.parts:
                raise ValueError('unsafe archive checksum path')
            if sha256_file(directory / name) != digest:
                raise ValueError('archive checksum mismatch')
            names.add(name)
        if not set(REQUIRED_ARCHIVE_ARTIFACTS[:4]) <= names:
            raise ValueError('archive checksum list is incomplete')
        if names - set(REQUIRED_ARCHIVE_ARTIFACTS):
            raise ValueError('unexpected archive checksum entry')
        public, ids = read_json(directory / 'items.json'), read_json(directory / 'sample_ids.json')
        if sha256_file(directory / 'sample_ids.json') != public['sample_ids_sha256']:
            raise ValueError('archive sample_ids hash mismatch')
        for phase, key in (('formal', 'formal_ids'), ('trial', 'trial_ids')):
            if [item['id'] for item in public[phase]] != ids[key]:
                raise ValueError('archive IDs do not match sample_ids')
        completeness = read_json(directory / 'archive.json')
        missing = sorted(set(completeness['missing_artifacts']) | (set(REQUIRED_ARCHIVE_ARTIFACTS) - names))
        completeness.update(complete=completeness['complete'] and not missing, missing_artifacts=missing)
        return public, completeness
    except OSError:
        raise ValueError('archive artifact is missing or unreadable') from None


def analyze(directory: Path) -> dict:
    if (directory / 'items.json').exists():
        public, completeness = load_public_archive(directory)
    else:
        public, completeness = public_items(directory), None
    summary = {'schema_version': 1, 'package_sha256': public['package_sha256'],
               'sample_ids_sha256': public['sample_ids_sha256'],
               **analyze_items(public['formal'], invalidated=public['invalidated'])}
    if completeness is not None and not completeness['complete']:
        summary.update(reportable=False, result_wording=INCOMPLETE_ARCHIVE_WORDING)
    return summary


def archive(directory: Path, output: Path, prereg: Path | None = None) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError('archive output must be empty')
    public, summary = public_items(directory), analyze(directory)
    write_json(output / 'items.json', public)
    (output / 'sample_ids.json').write_bytes((directory / 'sample_ids.json').read_bytes())
    names, missing = list(REQUIRED_ARCHIVE_ARTIFACTS[:3]), []
    if prereg is None:
        missing.append('prereg.md')
    else:
        (output / 'prereg.md').write_bytes(prereg.read_bytes())
        names.append('prereg.md')
    for relative in REQUIRED_ARCHIVE_ARTIFACTS[5:]:
        source = directory / relative
        if source.exists():
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            names.append(relative)
        else:
            missing.append(relative)
    completeness = {'complete': not missing, 'missing_artifacts': missing,
                    'package_sha256': public['package_sha256']}
    if missing:
        summary.update(reportable=False, result_wording=INCOMPLETE_ARCHIVE_WORDING)
    write_json(output / 'summary.json', summary)
    write_json(output / 'archive.json', completeness)
    names.append('archive.json')
    (output / 'SHA256SUMS.txt').write_bytes(''.join(f'{sha256_file(output / n)}  {n}\n' for n in sorted(names)).encode('utf-8'))
    return {**summary, 'archive': completeness}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'authorize', 'run', 'analyze'):
        command = commands.add_parser(name)
        command.add_argument('--dir', type=Path, required=True)
        if name == 'prepare':
            command.add_argument('--source-root', type=Path, required=True)
        if name in ('authorize', 'run'):
            command.add_argument('--phase', choices=('trial', 'formal'), required=True)
        if name == 'authorize':
            command.add_argument('--by', required=True)
        if name == 'analyze':
            command.add_argument('--output', type=Path)
            command.add_argument('--prereg', type=Path)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        if args.command == 'prepare':
            result = prepare(args.dir, args.source_root)['estimate']
        elif args.command == 'authorize':
            result = authorize(args.dir, args.phase, args.by)
        elif args.command == 'run':
            result = run(args.dir, args.phase)
        else:
            result = archive(args.dir, args.output, args.prereg) if args.output else analyze(args.dir)
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'http_status': getattr(error, 'http_status', None)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
