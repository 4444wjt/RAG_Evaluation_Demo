"""Shared offline data checks and budget arithmetic for the registered experiment."""
from collections import Counter
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

TASK = Path(__file__).resolve().parent
ROOT = TASK.parents[2]
MODEL = 'deepseek-flash'
MAX_TOKENS = 2048
HARD_LIMIT = Decimal('8')
UNRESOLVED = {'S12', 'S14', 'S16', 'S18', 'S26', 'S35'}
BLOBS = {
    'judge-annotation.json': 'eea6f758b96b0099fa32232a1697fdadc401b440',
    'annotation-A.json': '39b17df9ab8fbb8265e23d3ba5da49b926fcdca6',
    'annotation-B.json': '860c6051f9cc0b86621f5c27cd12c8f52c5ea0dc',
    'analysis.json': '55435ed260f413934bd27903ae968af514d2a0c0',
    'ragas_compat.py': 'e476246487e0be72ae7f5284d61a6c5efbfc112c',
}


class StopRun(RuntimeError):
    pass


def configure(offline=True):
    """Process-local settings only; never change packages, network adapters or .env."""
    sys.dont_write_bytecode = True
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    (TASK / 'T1-tmp').mkdir(exist_ok=True)
    os.environ['TMP'] = os.environ['TEMP'] = str(TASK / 'T1-tmp')
    site = str(Path(sys.executable).resolve().parents[1] / 'Lib/site-packages')
    if site not in sys.path:
        sys.path.insert(0, site)

    def audit(event, args):
        if offline and event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
            raise StopRun('OFFLINE_NETWORK_ATTEMPT:' + event)
        if event == 'open':
            path, mode, flags = args
            if isinstance(path, (str, bytes)):
                name = os.fsdecode(path)
                if offline and Path(name).name == '.env':
                    raise StopRun('T1_ENV_READ_FORBIDDEN')
                if name.lower() == os.devnull.lower():
                    return
                if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                    scoped_path(name)
        elif event in ('os.mkdir', 'os.remove', 'os.rmdir'):
            scoped_path(args[0])
        elif event in ('os.rename', 'os.link'):
            scoped_path(args[0])
            scoped_path(args[1])

    sys.addaudithook(audit)


def scoped_path(path):
    result = Path(path).resolve()
    if not result.is_relative_to(TASK):
        raise StopRun('OUT_OF_SCOPE_WRITE:' + str(result))
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    scoped_path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []


def append_jsonl(path, value):
    with scoped_path(path).open('a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + '\n')
        handle.flush()


def verify_inputs():
    manifest = read_json(TASK / 'inputs/manifest.json')
    if len(manifest['files']) != 5:
        raise StopRun('INPUT_MANIFEST_COUNT')
    checks = []
    for row in manifest['files']:
        path = TASK / row['local']
        expected = BLOBS.get(path.name)
        data = path.read_bytes()
        actual = subprocess.check_output(['git', 'hash-object', '--stdin'], input=data, cwd=ROOT).decode().strip()
        source = subprocess.check_output(['git', 'show', '05099e7:' + row['source']], cwd=ROOT)
        if actual != expected or data != source or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise StopRun('INPUT_BLOB_MISMATCH:' + path.name)
        checks.append({'file': row['local'], 'blob': actual, 'verified': True})
    return checks


def load_inputs():
    return tuple(read_json(TASK / 'inputs' / name) for name in
                 ('judge-annotation.json', 'annotation-A.json', 'annotation-B.json', 'analysis.json'))


def validate_reference(fixture, annotation_a, annotation_b, analysis):
    ids = {f'S{i:02}' for i in range(1, 53)}
    if len(fixture['items']) != 52 or {r['id'] for r in fixture['items']} != ids:
        raise StopRun('STATEMENT_SET_MISMATCH')
    if len(fixture['questions']) != 20 or any(len(q['contexts']) != 4 for q in fixture['questions']):
        raise StopRun('QUESTION_CONTEXT_COUNT_MISMATCH')
    package = fixture['packageId']
    if any(obj['packageId'] != package for obj in (annotation_a, annotation_b, analysis)):
        raise StopRun('PACKAGE_MISMATCH')
    mapping = {'支持': 1, '矛盾': 0, '无依据': 0, '无法判断': None}
    resolved = {}
    for annotation in (annotation_a, annotation_b):
        if set(annotation['labels']) != ids:
            raise StopRun('ANNOTATION_SET_MISMATCH')
    for sid in sorted(ids):
        try:
            a = mapping[annotation_a['labels'][sid]['label']]
            b = mapping[annotation_b['labels'][sid]['label']]
        except KeyError as exc:
            raise StopRun('INVALID_HUMAN_LABEL') from exc
        if a is not None and a == b:
            resolved[sid] = a
    if ids - resolved.keys() != UNRESOLVED or len(resolved) != 46 or Counter(resolved.values()) != {1: 40, 0: 6}:
        raise StopRun('REFERENCE_SET_MISMATCH')
    rows = analysis['rows']
    if len(rows) != 52 or {row['id'] for row in rows} != ids:
        raise StopRun('ARCHIVED_ANALYSIS_SET_MISMATCH')
    archived = {row['id']: row['human'] for row in rows if row['state'] == 'resolved'}
    if archived != resolved or {row['id'] for row in rows if row['state'] != 'resolved'} != UNRESOLVED:
        raise StopRun('ARCHIVED_ANALYSIS_DISAGREES')
    if analysis['counts'] != {'resolved': 46, 'unresolved': 6, 'unlabeled': 0}:
        raise StopRun('ARCHIVED_COUNTS_DISAGREE')
    if analysis['calibration']['m1'] != 40 or analysis['calibration']['m0'] != 6:
        raise StopRun('ARCHIVED_CLASS_COUNTS_DISAGREE')
    return resolved


def question_groups(fixture):
    """Only question text, contexts and statement text enter either prompt builder."""
    groups = []
    for question in fixture['questions']:
        items = [item for item in fixture['items'] if item['question'] == question['id']]
        if not items:
            raise StopRun('EMPTY_QUESTION_GROUP')
        groups.append({'question': question['question'], 'contexts': list(question['contexts']),
                       'statements': [item['statement'] for item in items], 'ids': [item['id'] for item in items]})
    if sum(len(group['ids']) for group in groups) != 52:
        raise StopRun('QUESTION_STATEMENT_BINDING_MISMATCH')
    return groups


def reservation(prompt):
    return Decimal(len(prompt) * 2) * Decimal('0.000002') + Decimal(MAX_TOKENS) * Decimal('0.000008')


def usage_cost(usage):
    """Missing usage remains unknown; it never settles as zero."""
    if not isinstance(usage, dict):
        raise ValueError('USAGE_MISSING')
    fields = ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens')
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in fields):
        raise ValueError('USAGE_INCOMPLETE')
    prompt, output, hit = (usage[key] for key in fields)
    miss = prompt - hit
    if miss < 0 or ('prompt_cache_miss_tokens' in usage and usage['prompt_cache_miss_tokens'] != miss):
        raise ValueError('USAGE_INCONSISTENT')
    return Decimal(miss) * Decimal('0.000002') + Decimal(hit) * Decimal('0.00000004') + Decimal(output) * Decimal('0.000008')


class BudgetLedger:
    def __init__(self, path):
        self.path = scoped_path(path)

    def totals(self):
        actual, pending = Decimal('0'), {}
        for row in read_jsonl(self.path):
            if row['event'] == 'reserve':
                if row['request_id'] in pending:
                    raise StopRun('DUPLICATE_RESERVATION')
                pending[row['request_id']] = Decimal(row['reserved_cny'])
            elif row['event'] == 'settle':
                if row['request_id'] not in pending:
                    raise StopRun('SETTLEMENT_WITHOUT_RESERVATION')
                del pending[row['request_id']]
                actual += Decimal(row['actual_estimated_cny'])
        return actual, sum(pending.values(), Decimal('0'))

    def reserve(self, request_id, prompt, arm):
        amount = reservation(prompt)
        actual, pending = self.totals()
        if actual + pending + amount >= HARD_LIMIT:
            raise StopRun('BUDGET_HARD_STOP')
        if any(row.get('request_id') == request_id for row in read_jsonl(self.path)):
            raise StopRun('REQUEST_ALREADY_RECORDED')
        append_jsonl(self.path, {'event': 'reserve', 'request_id': request_id, 'arm': arm,
                                'prompt_chars': len(prompt), 'reserved_cny': str(amount),
                                'settled_before_cny': str(actual), 'unsettled_before_cny': str(pending)})
        return amount

    def settle(self, request_id, usage):
        amount = usage_cost(usage)
        append_jsonl(self.path, {'event': 'settle', 'request_id': request_id,
                                'actual_estimated_cny': str(amount), 'usage': usage})
        return amount

    def outcome(self, request_id, arm, interface_error):
        append_jsonl(self.path, {'event': 'outcome', 'request_id': request_id,
                                'arm': arm, 'interface_error': bool(interface_error)})
        outcomes = [r for r in read_jsonl(self.path) if r['event'] == 'outcome' and r['arm'] == arm]
        errors = sum(r['interface_error'] for r in outcomes)
        if errors * 10 > len(outcomes):
            raise StopRun('INTERFACE_ERROR_RATE_EXCEEDS_10_PERCENT')
        if sum(self.totals()) >= HARD_LIMIT:
            raise StopRun('BUDGET_HARD_STOP')


def thinking_detected(response):
    if isinstance(response, dict):
        for key, value in response.items():
            if key.lower() in {'reasoning_content', 'reasoning', 'thinking'} and value:
                return True
            if thinking_detected(value):
                return True
    elif isinstance(response, list):
        return any(thinking_detected(item) for item in response)
    elif isinstance(response, str):
        return bool(re.search(r'<\s*/?\s*(think|thinking|analysis)\s*>|\[analysis\]', response, re.I))
    return False


def require_paid():
    if os.environ.get('VS_ALLOW_PAID') != '1':
        raise StopRun('PAID_DISABLED: --run requires VS_ALLOW_PAID=1')
