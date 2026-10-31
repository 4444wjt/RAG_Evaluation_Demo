"""Registered no-question ablation. T3 uses --dry-run and synthetic transports only."""
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
OUTPUT = Path(__file__).resolve().parent
PARENT = OUTPUT.parent
sys.path.insert(0, str(PARENT))
import compare_common as common
from run_compare import DeepSeekClient, call_b, load_b, payload_for

HARD_LIMIT = Decimal('1')
REMAINING_AUTHORIZATION = Decimal('9.7')
SENTINELS = ('S02', 'S05', 'S06')
_configured = False


def scoped_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(OUTPUT):
        raise common.StopRun('OUT_OF_SCOPE_WRITE:' + str(path))
    return path


def configure(offline=True):
    """Use local packages without calling common.configure (its temp dir is outside scope)."""
    global _configured
    if _configured:
        return
    expected = PARENT / 'venv-ragchecker/Scripts/python.exe'
    if Path(sys.executable).resolve() != expected.resolve():
        raise common.StopRun('WRONG_INTERPRETER')
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    settings = {
        'LITELLM_LOCAL_MODEL_COST_MAP': 'True', 'PYTHON_DOTENV_DISABLED': '1',
        'RAGAS_DO_NOT_TRACK': 'true', 'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1', 'TQDM_DISABLE': '1',
        'TMP': str(OUTPUT / 'tmp'), 'TEMP': str(OUTPUT / 'tmp'),
    }
    os.environ.update(settings)
    site = str(expected.parents[1] / 'Lib/site-packages')
    if site not in sys.path:
        sys.path.insert(0, site)

    def audit(event, args):
        if offline and event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
            raise common.StopRun('OFFLINE_NETWORK_ATTEMPT:' + event)
        if event == 'open':
            path, _, flags = args
            if isinstance(path, (str, bytes)):
                name = os.fsdecode(path)
                if Path(name).name.lower() == '.env':
                    raise common.StopRun('ENV_READ_FORBIDDEN')
                if name.lower() == os.devnull.lower():
                    return
                if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                    scoped_path(name)
        elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod'):
            scoped_path(args[0])
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            scoped_path(args[0])
            scoped_path(args[1])

    sys.addaudithook(audit)
    (OUTPUT / 'tmp').mkdir(exist_ok=True)
    _configured = True


def verify_parent():
    manifest = PARENT / 'SHA256SUMS.txt'
    initial = common.read_json(OUTPUT / 'T3-initial-integrity.json')
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if manifest_sha256 != initial['manifest_sha256']:
        raise common.StopRun('PARENT_MANIFEST_CHANGED')
    checks = []
    for line in manifest.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.lstrip('*')
        path = (PARENT / name).resolve()
        if not path.is_relative_to(PARENT) or path.is_relative_to(OUTPUT) or path.name.lower() == '.env':
            raise common.StopRun('INVALID_PARENT_MANIFEST_PATH')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise common.StopRun('PARENT_HASH_CHANGED:' + name)
        checks.append(name)
    if len(checks) != 237 or len(set(checks)) != 237:
        raise common.StopRun('PARENT_HASH_COUNT_MISMATCH')
    return {'checked': 237, 'passed': 237, 'manifest_sha256': manifest_sha256}


def preflight():
    integrity = verify_parent()
    blobs = common.verify_inputs()
    inputs = common.load_inputs()
    common.validate_reference(*inputs)
    return inputs, {'parent_integrity': integrity, 'input_verification': blobs,
                    'reference_counts': {'resolved': 46, 'supported': 40, 'unsupported': 6, 'unresolved': 6}}


class AblationLedger(common.BudgetLedger):
    """Keep the upstream journal/settlement rules; tighten only the registered limit."""
    def __init__(self, path):
        super().__init__(scoped_path(path))

    def reserve(self, request_id, prompt, arm):
        actual, pending = self.totals()
        if actual + pending + common.reservation(prompt) >= HARD_LIMIT:
            raise common.StopRun('BUDGET_HARD_STOP')
        return super().reserve(request_id, prompt, arm)

    def outcome(self, request_id, arm, interface_error):
        super().outcome(request_id, arm, interface_error)
        if sum(self.totals()) >= HARD_LIMIT:
            raise common.StopRun('BUDGET_HARD_STOP')


def make_client(output_dir=OUTPUT, *, key=None, transport=None):
    output_dir = scoped_path(output_dir)
    # Never let the upstream client's key=None branch read .env, including in T4.
    key = key if key is not None else os.environ.get('OPENAI_API_KEY')
    if not isinstance(key, str) or not key:
        raise common.StopRun('PROCESS_OPENAI_API_KEY_REQUIRED_NO_ENV_FILE')
    kwargs = {'key': key}
    if transport is not None:
        kwargs['transport'] = transport
    return DeepSeekClient(AblationLedger(output_dir / 'budget-ledger.jsonl'), output_dir / 'raw', **kwargs)


def statement_inputs(groups):
    return {sid: (statement, '\n'.join(group['contexts']), group['question'])
            for group in groups for sid, statement in zip(group['ids'], group['statements'])}


def prepared_requests(groups, loaded):
    originals = common.read_json(PARENT / 'dry-run-B.json')['requests']
    by_sid = {request['statement_ids'][0]: request for request in originals
              if len(request['statement_ids']) == 1}
    statements = statement_inputs(groups)
    if len(originals) != 52 or set(by_sid) != set(statements) or len(by_sid) != 52:
        raise common.StopRun('ORIGINAL_DRY_RUN_STATEMENT_SET_MISMATCH')
    templates = []
    for index, (sid, (statement, reference, question)) in enumerate(statements.items(), 1):
        captured, _, _ = call_b(*loaded, statement, reference, None, lambda prompt, maximum: 'Neutral')
        original = by_sid[sid]
        full_reference = question + ' ' + reference
        if original['prompt'].count(full_reference) != 1:
            raise common.StopRun('QUESTION_PREFIX_NOT_UNIQUE:' + sid)
        expected = original['prompt'].replace(full_reference, reference, 1)
        if captured['prompt'] != expected or captured['upstream_max_new_tokens'] != original['upstream_max_new_tokens']:
            raise common.StopRun('NO_QUESTION_PROMPT_MISMATCH:' + sid)
        templates.append({'arm': 'Bq0', 'index': index, 'statement_ids': [sid],
                          'prompt': captured['prompt'], 'upstream_max_new_tokens': captured['upstream_max_new_tokens'],
                          'prompt_comparison_passed': True})
    for sid in SENTINELS:
        captured, _, _ = call_b(*loaded, *statements[sid], lambda prompt, maximum: 'Neutral')
        if captured['prompt'] != by_sid[sid]['prompt'] or captured['upstream_max_new_tokens'] != by_sid[sid]['upstream_max_new_tokens']:
            raise common.StopRun('SENTINEL_PROMPT_MISMATCH:' + sid)
        templates.append({'arm': 'S', 'statement_ids': [sid], 'prompt': captured['prompt'],
                          'upstream_max_new_tokens': captured['upstream_max_new_tokens'], 'prompt_comparison_passed': True})
    requests = []
    for round_number in (1, 2, 3):
        for template in templates:
            sid = template['statement_ids'][0]
            request_id = (f'Bq0{round_number}-{template["index"]:03}' if template['arm'] == 'Bq0'
                          else f'S{round_number}-{sid}')
            requests.append(dict(template, round=round_number, request_id=request_id,
                                 prompt_chars=len(template['prompt']),
                                 reserved_cny=str(common.reservation(template['prompt'])),
                                 payload=payload_for(template['prompt'])))
    return requests


def estimate_actual_cost(requests):
    """Match every planned request to the same statement and round in historical B usage."""
    rows = [row for row in common.read_jsonl(PARENT / 'results.jsonl') if row['arm'] == 'B']
    indexed = {(row['round'], row['statement_id']): row for row in rows}
    if len(rows) != 156 or len(indexed) != 156:
        raise common.StopRun('HISTORICAL_B_USAGE_COUNT_MISMATCH')
    total = Decimal('0')
    selected_usage = []
    for request in requests:
        source = indexed[(request['round'], request['statement_ids'][0])]
        cost = common.usage_cost(source['usage'])
        total += cost
        selected_usage.append({'request_id': request['request_id'], 'source_request_id': source['request_id'],
                               'usage': source['usage'], 'estimated_cny': str(cost)})
    historical = sum((common.usage_cost(row['usage']) for row in rows), Decimal('0'))
    return {'estimated_total_cny': str(total), 'historical_B_request_count': 156,
            'historical_B_estimated_cny': str(historical),
            'method': '逐一匹配主实验B同轮同陈述usage：156条Bq0加9条哨兵；沿用prompt/cache-hit/completion登记单价，不扣减题面tokens。',
            'limitation': '历史usage代理估算，未来输出长度和缓存命中可能变化；不是实际费用或费用上界。',
            'matched_requests': selected_usage}


def execute_requests(requests, groups, loaded, client, output_dir=OUTPUT):
    common.require_paid()
    output_dir = scoped_path(output_dir)
    statements = statement_inputs(groups)
    previous_round = None
    for request in requests:
        if request['round'] != previous_round:
            verify_parent()
            previous_round = request['round']
        sid = request['statement_ids'][0]
        statement, reference, question = statements[sid]
        sent = {}

        def send(prompt, maximum):
            if prompt != request['prompt'] or maximum != request['upstream_max_new_tokens']:
                raise common.StopRun('EXECUTION_PROMPT_MISMATCH:' + request['request_id'])
            sent['path'], sent['record'] = client.send(request['request_id'], request['arm'], request['round'], prompt, maximum)
            return sent['record']['model_text']

        _, native, outer = call_b(*loaded, statement, reference, None if request['arm'] == 'Bq0' else question, send)
        path, record = sent['path'], sent['record']
        response = record['response'] if isinstance(record['response'], dict) else {}
        record.update(native_parser=native, outer_results=outer, status='parsed')
        common.write_json(scoped_path(path), record)
        common.append_jsonl(output_dir / 'results.jsonl', dict(outer[0], request_id=request['request_id'],
                            arm=request['arm'], round=request['round'], statement_id=sid,
                            interface_error=record['interface_error'], model=response.get('model'),
                            usage=response.get('usage'), raw_path=path.relative_to(output_dir).as_posix()))
        client.ledger.outcome(request['request_id'], request['arm'], record['interface_error'])
    verify_parent()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    if args.run:
        common.require_paid()  # Before packages, files or credentials are accessed.
    configure(offline=args.dry_run)
    inputs, checks = preflight()
    groups = common.question_groups(inputs[0])
    if args.run:
        for name in ('results.jsonl', 'budget-ledger.jsonl', 'raw'):
            if (OUTPUT / name).exists():
                raise common.StopRun('EXISTING_RUN_ARTIFACT:' + name)
    loaded = load_b()
    requests = prepared_requests(groups, loaded)
    if args.dry_run:
        total = sum((Decimal(request['reserved_cny']) for request in requests), Decimal('0'))
        maximum = max(Decimal(request['reserved_cny']) for request in requests)
        report = dict(checks, mode='offline_dry_run', network_calls=0, model_calls=0, actual_cost_cny='0',
                      request_count=len(requests), requests_per_round=55, Bq0_requests=156, sentinel_requests=9,
                      order=['Bq01', 'S1', 'Bq02', 'S2', 'Bq03', 'S3'],
                      prompt_comparison_passed=all(request['prompt_comparison_passed'] for request in requests),
                      reserved_total_cny=str(total), max_single_reservation_cny=str(maximum),
                      remaining_authorization_cny=str(REMAINING_AUTHORIZATION),
                      reservation_acceptance_le_remaining=total <= REMAINING_AUTHORIZATION,
                      rolling_hard_limit_cny=str(HARD_LIMIT),
                      rolling_budget_rule='已结算 + 未结算预留 + 本次预留 >= 1 时停止；结算后释放该次预留。预留总和不与1元比较。',
                      expected_actual_cost=estimate_actual_cost(requests), requests=requests)
        if len(requests) != 165 or maximum >= HARD_LIMIT or total > REMAINING_AUTHORIZATION:
            raise common.StopRun('DRY_RUN_ACCEPTANCE_FAILED')
        verify_parent()
        common.write_json(OUTPUT / 'dry-run-plan.json', report)
        for request in requests:
            print(request['request_id'], ','.join(request['statement_ids']), 'reserve_CNY=' + request['reserved_cny'])
        print('TOTAL', len(requests), 'RESERVED_CNY', total, 'MAX_SINGLE_CNY', maximum,
              'EXPECTED_ACTUAL_CNY', report['expected_actual_cost']['estimated_total_cny'],
              'PROMPT_COMPARISON_PASS', report['prompt_comparison_passed'])
    else:
        execute_requests(requests, groups, loaded, make_client(), OUTPUT)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (common.StopRun, ImportError, OSError, ValueError) as exc:
        print('STOPPED:', str(exc), file=sys.stderr)
        raise SystemExit(2)
