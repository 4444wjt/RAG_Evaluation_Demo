"""T1: --dry-run. T2 (separately authorized): VS_ALLOW_PAID=1 plus --run.

Use the A interpreter to orchestrate; each arm is loaded in its registered venv.
No statement extraction, parser repair, HTTP retry or library-file modification.
"""
import argparse
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import urllib.error
import urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_common import (TASK, ROOT, MODEL, MAX_TOKENS, HARD_LIMIT, StopRun, BudgetLedger,
                            append_jsonl, configure, load_inputs, question_groups, read_json,
                            require_paid, reservation, scoped_path, thinking_detected,
                            validate_reference, verify_inputs, write_json)


def load_a():
    spec = importlib.util.spec_from_file_location('frozen_ragas_compat', TASK / 'inputs/ragas_compat.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from ragas.metrics._faithfulness import _faithfulness_output_parser
    return module.ChineseFaithfulness(llm=object()), _faithfulness_output_parser


def load_b():
    if version('refchecker') != '0.2.17' or version('ragchecker') != '0.1.9':
        raise StopRun('B_PACKAGE_VERSION_MISMATCH')
    from refchecker.checker import llm_checker
    return llm_checker, llm_checker.LLMChecker(model=MODEL, batch_size=1, api_base='https://api.deepseek.com')


def prompt_a(metric, group):
    return metric._create_nli_prompt({'contexts': group['contexts']}, group['statements']).to_string()


def parse_a(parser, text, count):
    native = {'return': None, 'exception': None}
    try:
        native['return'] = parser.parse(text).dicts()
    except Exception as exc:
        native['exception'] = {'type': type(exc).__name__, 'message': str(exc)}
    rows = native['return']
    if rows is None or len(rows) != count:
        return native, [{'judgment': '无结果', 'original_label': None if rows is None or i >= len(rows) else rows[i].get('verdict'),
                         'parse_failed': True} for i in range(count)]
    result = []
    for row in rows:
        verdict = row.get('verdict')
        result.append({'judgment': {1: '支持', 0: '不支持'}.get(verdict, '无结果'),
                       'original_label': verdict, 'parse_failed': verdict not in (0, 1)})
    return native, result


def call_b(module, checker, statement, reference, question, send):
    """Replace only the module's bottom-level sender; run unchanged .check and parser."""
    captured = {}

    def replacement(*, prompts, **kwargs):
        if len(prompts) != 1:
            raise StopRun('UNEXPECTED_B_BATCH_SIZE')
        captured['prompt'] = prompts[0]
        captured['upstream_max_new_tokens'] = kwargs['max_new_tokens']
        text = send(prompts[0], kwargs['max_new_tokens'])
        captured['model_text'] = text
        return [text]

    native = {'return': None, 'exception': None}
    with patch.object(module, 'get_model_batch_response', replacement):
        try:
            native['return'] = checker.check(batch_claims=[[statement]], batch_references=[reference],
                                              batch_questions=[question], is_joint=False, merge_psg=False,
                                              max_reference_segment_length=0)
        except StopRun:
            raise
        except Exception as exc:
            native['exception'] = {'type': type(exc).__name__, 'message': str(exc)}
    # A sender/import failure is not silently treated as a parser result.
    if 'model_text' not in captured:
        raise StopRun('B_FAILED_BEFORE_RESPONSE:' + str(native['exception']))
    text = captured['model_text']
    label = native['return'][0][0][0] if native['return'] is not None else None
    # M2: case-insensitive appearance of any of the three label words.
    detected = isinstance(text, str) and any(word.lower() in text.lower() for word in checker.labels)
    mapping = {'Entailment': '支持', 'Neutral': '不支持', 'Contradiction': '不支持'}
    outer = {'judgment': mapping.get(label, '无结果') if detected else '无结果',
             'original_label': label, 'parse_failed': not detected or label not in mapping}
    return captured, native, [outer]


def payload_for(prompt):
    # Official-doc candidate; offline T1 cannot confirm current deepseek-flash support.
    # https://api-docs.deepseek.com/guides/thinking_mode -- verify on T2 first response.
    return {'model': MODEL, 'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0, 'max_tokens': MAX_TOKENS, 'thinking': {'type': 'disabled'}}


def read_key():
    # No environment fallback. Read only the designated variable from this file.
    with (ROOT / '.env').open(encoding='utf-8-sig') as handle:
        for line in handle:
            line = line.strip()
            if line.startswith('export '):
                line = line[7:]
            key, separator, value = line.partition('=')
            if separator and key.strip() == 'OPENAI_API_KEY':
                value = value.strip()
                if value[:1] in ('"', "'") and value[-1:] == value[:1]:
                    value = value[1:-1]
                else:
                    value = value.split(' #', 1)[0].strip()
                if value:
                    print('已读取', flush=True)
                    return value
    raise StopRun('OPENAI_API_KEY_MISSING')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_transport(payload, key):
    request = urllib.request.Request('https://api.deepseek.com/chat/completions',
                                     data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
                                     method='POST')
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class DeepSeekClient:
    def __init__(self, ledger, raw_dir, transport=http_transport, key=None):
        self.ledger = ledger
        self.raw_dir = scoped_path(raw_dir)
        self.transport = transport
        self.key = key

    def send(self, request_id, arm, round_number, prompt, upstream_max_new_tokens=None):
        require_paid()
        reserved = self.ledger.reserve(request_id, prompt, arm)
        if self.key is None:
            self.key = read_key()
        self.raw_dir.mkdir(exist_ok=True)
        path = self.raw_dir / (request_id + '.json')
        if path.exists():
            raise StopRun('RAW_ALREADY_EXISTS')
        record = {'request_id': request_id, 'arm': arm, 'round': round_number, 'prompt': prompt,
                  'payload': payload_for(prompt), 'upstream_max_new_tokens': upstream_max_new_tokens,
                  'reserved_cny': str(reserved), 'actual_estimated_cny': None, 'response_text': None,
                  'response': None, 'model_text': None, 'interface_error': False, 'native_parser': None,
                  'outer_results': None, 'status': 'reserved'}
        write_json(path, record)
        try:
            status, body = self.transport(record['payload'], self.key)
        except Exception as exc:
            # Never log the exception message: it may contain credentials or response text.
            record.update(interface_error=True, error_type=type(exc).__name__, status='transport_error', model_text='')
            write_json(path, record)
            return path, record
        text = body.decode('utf-8', errors='replace')
        if self.key in text:
            record.update(status='STOPPED_SECRET_ECHO', response_text='[REDACTED: secret echo]', http_status=status)
            write_json(path, record)
            raise StopRun('SECRET_ECHO')
        record.update(response_text=text, http_status=status, status='response_received')
        try:
            response = json.loads(text)
            if self.key in json.dumps(response, ensure_ascii=False):
                record.update(status='STOPPED_SECRET_ECHO', response_text='[REDACTED: secret echo]')
                write_json(path, record)
                raise StopRun('SECRET_ECHO')
            record['response'] = response
            if not isinstance(response, dict):
                raise ValueError('INVALID_RESPONSE_OBJECT')
            try:
                record['actual_estimated_cny'] = str(self.ledger.settle(request_id, response.get('usage')))
            except ValueError as exc:
                record.update(interface_error=True, usage_error=str(exc))
            if thinking_detected(response):
                record['status'] = 'STOPPED_THINKING_CONTENT'
                write_json(path, record)
                raise StopRun('THINKING_CONTENT_DETECTED')
            content = response['choices'][0]['message']['content']
            if not isinstance(content, str):
                raise ValueError('INVALID_MESSAGE_CONTENT')
            record['model_text'] = content
            if not content or status != 200:
                record['interface_error'] = True
        except StopRun:
            raise
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            record.update(interface_error=True, error_type=type(exc).__name__, model_text='')
        if thinking_detected(text):
            record['status'] = 'STOPPED_THINKING_CONTENT'
            write_json(path, record)
            raise StopRun('THINKING_CONTENT_DETECTED')
        write_json(path, record)
        return path, record


def prepared_requests(arm, groups, loaded):
    """Generate the exact registered prompts; B uses a local Neutral response only to capture them."""
    requests = []
    for group in groups:
        if arm == 'A':
            requests.append({'statement_ids': group['ids'], 'prompt': prompt_a(loaded[0], group),
                             'upstream_max_new_tokens': None})
        else:
            for sid, statement in zip(group['ids'], group['statements']):
                captured, _, _ = call_b(*loaded, statement, '\n'.join(group['contexts']), group['question'],
                                        lambda prompt, maximum: 'Neutral')
                requests.append({'statement_ids': [sid], 'prompt': captured['prompt'],
                                 'upstream_max_new_tokens': captured['upstream_max_new_tokens']})
    for index, request in enumerate(requests, 1):
        request.update(index=index, arm=arm, prompt_chars=len(request['prompt']),
                       reserved_cny=str(reservation(request['prompt'])), payload=payload_for(request['prompt']))
    return requests


def execute_arm(arm, round_number, groups, loaded, output_dir=TASK, client=None):
    output_dir = scoped_path(output_dir)
    ledger = client.ledger if client is not None else BudgetLedger(output_dir / 'budget-ledger.jsonl')
    client = client if client is not None else DeepSeekClient(ledger, output_dir / 'raw')
    request_index = 0
    for group in groups:
        targets = [None] if arm == 'A' else list(range(len(group['ids'])))
        for target in targets:
            request_index += 1
            request_id = f'{arm}{round_number}-{request_index:03}'
            if arm == 'A':
                path, record = client.send(request_id, arm, round_number, prompt_a(loaded[0], group))
                native, outer = parse_a(loaded[1], record['model_text'], len(group['ids']))
                ids = group['ids']
            else:
                sent = {}

                def send(prompt, maximum):
                    sent['path'], sent['record'] = client.send(request_id, arm, round_number, prompt, maximum)
                    return sent['record']['model_text']

                _, native, outer = call_b(*loaded, group['statements'][target], '\n'.join(group['contexts']),
                                          group['question'], send)
                path, record = sent['path'], sent['record']
                ids = [group['ids'][target]]
            response = record['response'] if isinstance(record['response'], dict) else {}
            record.update(native_parser=native, outer_results=outer, status='parsed')
            write_json(path, record)
            for sid, result in zip(ids, outer):
                append_jsonl(output_dir / 'results.jsonl', dict(result, request_id=request_id, arm=arm, round=round_number,
                             statement_id=sid, interface_error=record['interface_error'],
                             model=response.get('model'), usage=response.get('usage'), raw_path=str(path.relative_to(TASK))))
            ledger.outcome(request_id, arm, record['interface_error'])


def worker(arm, dry_run, round_number):
    verify_inputs()
    inputs = load_inputs()
    validate_reference(*inputs)
    groups = question_groups(inputs[0])
    loaded = load_a() if arm == 'A' else load_b()
    if dry_run:
        requests = prepared_requests(arm, groups, loaded)
        write_json(TASK / f'dry-run-{arm}.json', {'mode': 'offline_prompt_capture', 'arm': arm, 'requests': requests})
        print(f'{arm}: {len(requests)} prompt(s); no network/model calls')
    else:
        execute_arm(arm, round_number, groups, loaded)


def child(arm, dry_run, round_number=1):
    executable = ROOT / 'venv/Scripts/python.exe' if arm == 'A' else TASK / 'venv-ragchecker/Scripts/python.exe'
    command = [str(executable), '-I', '-S', '-B', str(Path(__file__).resolve()),
               '--dry-run' if dry_run else '--run', '--worker', arm, '--round', str(round_number)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    log = f'T1-dry-run-{arm}.log' if dry_run else f'run-{arm}{round_number}.log'
    (TASK / log).write_text('exit_code=' + str(result.returncode) + '\nSTDOUT\n' + result.stdout + '\nSTDERR\n' + result.stderr, encoding='utf-8')
    if result.returncode:
        raise StopRun('WORKER_FAILED:' + log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--run', action='store_true')
    parser.add_argument('--worker', choices=('A', 'B'), help=argparse.SUPPRESS)
    parser.add_argument('--round', type=int, choices=(1, 2, 3), default=1, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.run:
        require_paid()  # Before any environment/package/input/credential side effect.
    configure(offline=args.dry_run)
    if args.worker:
        worker(args.worker, args.dry_run, args.round)
        return 0
    verification = verify_inputs()
    validate_reference(*load_inputs())
    if args.dry_run:
        child('A', True)
        child('B', True)
        from decimal import Decimal
        plan = []
        for round_number in (1, 2, 3):
            for arm in ('A', 'B'):
                for request in read_json(TASK / f'dry-run-{arm}.json')['requests']:
                    plan.append(dict(request, round=round_number, request_id=f'{arm}{round_number}-{request["index"]:03}'))
        total = sum((Decimal(request['reserved_cny']) for request in plan), Decimal('0'))
        report = {'mode': 'dry-run', 'model_calls': 0, 'network_calls': 0, 'actual_cost_cny': '0',
                  'request_count': len(plan), 'requests_per_arm': {arm: sum(r['arm'] == arm for r in plan) for arm in ('A', 'B')},
                  'order': ['A1', 'B1', 'A2', 'B2', 'A3', 'B3'], 'reserved_total_cny': str(total),
                  'budget_acceptance_le_8': total <= HARD_LIMIT, 'input_verification': verification,
                  'reference_counts': {'resolved': 46, 'supported': 40, 'unsupported': 6, 'unresolved': 6},
                  'requests': plan}
        write_json(TASK / 'dry-run-plan.json', report)
        for request in plan:
            print(request['request_id'], 'statements=' + ','.join(request['statement_ids']),
                  'chars=' + str(request['prompt_chars']), 'reserve_CNY=' + request['reserved_cny'])
        print('TOTAL', len(plan), 'RESERVED_CNY', str(total), 'LE_8', total <= HARD_LIMIT)
        return 0 if total <= HARD_LIMIT else 1
    for path in ('results.jsonl', 'budget-ledger.jsonl', 'raw'):
        if (TASK / path).exists():
            raise StopRun('EXISTING_RUN_ARTIFACT:' + path)
    for round_number in (1, 2, 3):
        for arm in ('A', 'B'):
            child(arm, False, round_number)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (StopRun, ImportError, PermissionError) as exc:
        print('STOPPED:', str(exc), file=sys.stderr)
        raise SystemExit(2)
