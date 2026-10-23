"""裁判盲区体检：运行与分析离线题包。run 仅接受独立绑定授权；analyze 离线。

题包由 work/statute-injection/blindspot-20261022/make_exam.py 生成，本工具按 requests.jsonl 的请求内容发送（HTTP 层由 httpx 重新序列化，字节不保证相同），不重新拼装请求。
阈值按跑前登记（REGISTRATION_SHA256）判定，不改门禁、不改历史结论。
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import math
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.competition import statute_injection as si  # noqa: E402

VERSION = 'statute-blindspot-v1'
EXAM_VERSION = 'judge-blindspot-exam-v1'
REGISTRATION_SHA256 = '3703256a6e24f2a1c848a772638d7f71286896843ddfd4682239fc72a47f4d06'
TOOL_FILES = (*si.TOOL_FILES, 'tools/competition/statute_blindspot.py', 'tests/test_statute_blindspot.py')
PACKAGE_FILES = ('package.json', 'requests.jsonl', 'contexts/plain.txt', 'contexts/labeled.txt',
                 'rules.json', 'estimates.json')
COUNTS = {'paragraph_existing': 7, 'paragraph_absent': 3, 'article_swap': 5,
          'article_absent': 5, 'control_pinpoint': 5}
LAYERS = {'paragraph_existing': '款级', 'paragraph_absent': '款级',
          'article_swap': '条级', 'article_absent': '条级', 'control_pinpoint': '对照'}
ARMS = ('U', 'L')
MAX_REQUESTS = 40
# 随运行脚本的哈希一起被授权绑定。
BUDGET_CAP_CNY = 0.5
THROTTLE_SECONDS = 1.0
STOP_REASONS = ('completed', 'request_limit', 'estimated_budget_guard', 'actual_model_unknown',
                'reservation_exceeded', 'interrupted')
# 跑前登记的四条阈值；四条同时满足才算成立，任何一条在界外（含 4–6/10）记为不成立。
THRESHOLDS = {
    'blindSpot': {'arm': 'U', 'layer': '款级', 'rule': 'detected <= 3', 'of': 10},
    'intervention': {'arm': 'L', 'layer': '款级', 'rule': 'detected >= 7', 'of': 10},
    'usableU': {'arm': 'U', 'layer': '对照', 'rule': 'falsePositives <= 1', 'of': 5},
    'usableL': {'arm': 'L', 'layer': '对照', 'rule': 'falsePositives <= 1', 'of': 5},
    'layered': {'arm': 'U', 'layer': '条级', 'rule': 'detected >= 7', 'of': 10},
}
COST_NOTE = '费用为授权单价估算，不是账单。无 usage（含失败）按本次预留计；不重试。'


# ---- 以下八个函数复制自 tools/competition/statute_labeled.py（未跟踪、仍在变动），避免授权绑定它的哈希 ----

def empty_output(output):
    si.require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
               '输出必须是新的空目录；禁止覆盖或续跑')


def message_bytes(messages):
    return sum(len(m['content'].encode('utf-8')) for m in messages)


def token_cost(tokens, price):
    return (Decimal(tokens['prompt_tokens']) * Decimal(str(price['inputCnyPerMillion'])) +
            Decimal(tokens['completion_tokens']) * Decimal(str(price['outputCnyPerMillion']))) / Decimal(1_000_000)


def reservation(package, auth, job, payload):
    tokens = {'prompt_tokens': message_bytes(payload['messages']) + 256,
              'completion_tokens': package['judges'][job['judge']]['maxTokens']}
    return {'tokens': tokens, 'costCny': str(token_cost(tokens, auth['prices'][job['judge']]))}


def charge(record, reserved, auth):
    if record['usage'] is None:
        return {'basis': 'pre_request_reservation', **deepcopy(reserved)}
    return {'basis': 'response_usage', 'tokens': record['usage'],
            'costCny': str(token_cost(record['usage'], auth['prices'][record['judge']]))}


def iso_date(value):
    si.require(isinstance(value, str) and bool(value.strip()), '缺少 ISO 日期')
    try:
        valid = date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    si.require(valid, 'ISO 日期无效')


def halt(record, reserved, response):
    if isinstance(response, dict) and '_transport' not in response and record['actualModel'] is None:
        return 'actual_model_unknown'
    if record['usage'] is not None and Decimal(record['charge']['costCny']) > Decimal(reserved['costCny']):
        return 'reservation_exceeded'
    return None


def response_record(job, response, contexts, exception=None):
    try:
        fields = si.response_fields(response, contexts)
    except Exception:
        # 畸形响应造成解析器异常，也应记缺失后继续；回放走同一分支。
        model = response.get('model') if isinstance(response, dict) else None
        fields = {'status': 'parse_error', 'result': None, 'usage': None,
                  'actualModel': model if isinstance(model, str) else None}
    record = {**job, **fields, 'error': None}
    if isinstance(response, dict) and '_transport' in response:
        problem = response['_transport']
        record['error'] = ({'type': problem.get('error'), 'httpStatus': problem.get('httpStatus')}
                           if isinstance(problem, dict) else {'type': record['status']})
    elif record['status'] != 'ok':
        record['error'] = {'type': record['status']}
    if exception is not None:
        record['error'] = {'type': type(exception).__name__, 'note': '不归档异常消息；不重试。'}
    return record

# ---- 复制结束 ----


def model_halt(record, response, package):
    if isinstance(response, dict) and '_transport' not in response and record['actualModel'] != package['judges'][record['judge']]['model']:
        return 'actual_model_unknown'
    return None


def incomplete_record(job, reserved):
    return {**job, 'status': 'incomplete', 'result': None, 'actualModel': None, 'usage': None,
            'error': {'type': 'interrupted'}, 'charge': {'basis': 'pre_request_reservation', **deepcopy(reserved)}}


def build_request(item, arm, context, judge):
    """与 make_exam.py 同一构造；validate_package 用它逐条重建 requests.jsonl。"""
    messages = si.prompt_messages(item, {'contexts': [context]})
    payload = {'model': judge['model'], 'messages': messages, 'temperature': judge['temperature'],
               'max_tokens': judge['maxTokens'], 'stream': False, 'thinking': dict(judge['thinking'])}
    return {'requestId': f"{item['id']}--{arm}--deepseek", 'itemId': item['id'], 'arm': arm,
            'judge': 'deepseek', 'payload': payload}


def schedule(package):
    return [(item, arm) for arm in ARMS for item in package['items']
            if not (arm == 'L' and item['layer'] == '条级')]


def tool_hashes(root=ROOT):
    return {name: si.sha256(root / name) for name in TOOL_FILES}


def load_requests(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def validate_package(directory):
    """逐文件核对题包，并用 build_request 重建全部请求；返回 (package, requests, contexts)。"""
    si.verify_checksums(directory)
    si.require(sorted(p.relative_to(directory).as_posix() for p in directory.rglob('*')
                      if p.is_file() and p.name != 'SHA256SUMS.txt') == sorted(PACKAGE_FILES), '题包文件集合不符')
    package = si.load(directory / 'package.json')
    si.require(package.get('experimentVersion') == EXAM_VERSION, '题包版本不符')
    si.require(package['registration']['sha256'] == REGISTRATION_SHA256, '题包未绑定跑前登记')
    si.require(package['requestsSha256'] == si.sha256(directory / 'requests.jsonl'), 'requests.jsonl 哈希不符')
    contexts = {}
    for arm in ARMS:
        meta = package['contexts'][arm]
        si.require(si.sha256(directory / meta['file']) == meta['sha256'], f'{arm} 节选哈希不符')
        contexts[arm] = (directory / meta['file']).read_bytes().decode('utf-8')
    si.require(package['promptSha256'] == si.digest(si.SYSTEM_PROMPT), '提示不是原提示')
    items = package['items']
    si.require(len({i['id'] for i in items}) == len(items), '陈述 id 重复')
    si.require({t: sum(i['type'] == t for i in items) for t in COUNTS} == COUNTS and
               all(i['layer'] == LAYERS[i['type']] for i in items), '各类型条数或层级与登记不符')
    si.require(all(i['expected'] == ('supported' if i['type'] == 'control_pinpoint' else 'not_supported')
                   for i in items), '预期标签不符')
    judge = package['judges']['deepseek']
    expected = [build_request(item, arm, contexts[arm], judge) for item, arm in schedule(package)]
    si.require(len(expected) == MAX_REQUESTS == package['maxRequests'] and package['maxRetries'] == 0, '请求数须为40且不重试')
    si.require(load_requests(directory / 'requests.jsonl') == expected, 'requests.jsonl 与逐条重建不一致')
    return package, expected, contexts


def authorization_template(package_dir, root=ROOT):
    package, _, _ = validate_package(package_dir)
    return {'status': 'draft', 'allowPaidCalls': False, 'authorizedBy': None, 'authorizedAt': None, 'note': None,
            'packageSha256': si.sha256(package_dir / 'package.json'),
            'checksumsSha256': si.sha256(package_dir / 'SHA256SUMS.txt'),
            'requestsSha256': package['requestsSha256'],
            'plainSha256': package['contexts']['U']['sha256'], 'labeledSha256': package['contexts']['L']['sha256'],
            'registrationSha256': REGISTRATION_SHA256, 'configurationSha256': si.digest(package['judges']),
            'promptSha256': si.digest(si.SYSTEM_PROMPT), 'toolFiles': tool_hashes(root),
            'maxRequests': MAX_REQUESTS, 'maxRetries': 0, 'budgetCny': None,
            'prices': {'deepseek': {'inputCnyPerMillion': None, 'outputCnyPerMillion': None,
                                    'source': None, 'checkedAt': None}},
            'outputDirectory': None}


def validate_authorization(package_dir, auth, output, root=ROOT, *, today=None):
    """output 为 None 时是回放核对：不查输出目录和当天日期。"""
    si.require(auth.get('status') == 'authorized' and auth.get('allowPaidCalls') is True, '未授权付费')
    for name in ('authorizedBy', 'note'):
        si.require(isinstance(auth.get(name), str) and bool(auth[name].strip()), f'授权缺少 {name}')
    iso_date(auth.get('authorizedAt'))
    template = authorization_template(package_dir, root)
    bound = ('packageSha256', 'checksumsSha256', 'requestsSha256', 'plainSha256', 'labeledSha256',
             'registrationSha256', 'configurationSha256', 'promptSha256', 'toolFiles')
    si.require(all(auth.get(k) == template[k] for k in bound), '授权哈希不符')
    si.require(type(auth.get('maxRequests')) is int and auth['maxRequests'] == MAX_REQUESTS and
               type(auth.get('maxRetries')) is int and auth['maxRetries'] == 0, '请求上限必须为40且不重试')
    si.require(type(auth.get('budgetCny')) in (int, float) and math.isfinite(auth['budgetCny']) and
               auth['budgetCny'] > 0, '预算必须为正有限数')
    si.require(auth['budgetCny'] <= BUDGET_CAP_CNY, '预算超过脚本上限 ¥0.5')
    price = auth.get('prices', {}).get('deepseek') if isinstance(auth.get('prices'), dict) else None
    si.require(isinstance(price, dict) and all(type(price.get(k)) in (int, float) and math.isfinite(price[k]) and
               price[k] >= 0 for k in ('inputCnyPerMillion', 'outputCnyPerMillion')), '单价无效')
    si.require(isinstance(price.get('source'), str) and bool(price['source'].strip()), '单价缺少来源')
    iso_date(price.get('checkedAt'))
    si.require(price['checkedAt'] == auth['authorizedAt'], '单价须在授权当天核实')
    if output is not None:
        si.require(price['checkedAt'] == (today or date.today().isoformat()), '运行当天须重新核价和授权')
        si.require(isinstance(auth.get('outputDirectory'), str) and bool(auth['outputDirectory'].strip()) and
                   (root / auth['outputDirectory']).resolve() == output.resolve(), '授权未绑定本次输出目录')


def job_of(request):
    return {k: request[k] for k in ('requestId', 'itemId', 'arm', 'judge')}


def run(package_dir, authorization_path, output, *, root=ROOT, transport=None, sleep=time.sleep, today=None):
    auth = si.load(authorization_path)
    package, requests, contexts = validate_package(package_dir)
    validate_authorization(package_dir, auth, output, root, today=today)
    empty_output(output)
    send = transport if transport is not None else si.make_transport(package)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package_dir, output / 'package')
    shutil.copyfile(authorization_path, output / 'authorization.json')
    for name in TOOL_FILES:
        target = output / 'sources' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    records, calls, cost, stop = [], 0, Decimal(0), 'interrupted'
    pending = None
    events, budget = output / 'events.jsonl', Decimal(str(auth['budgetCny']))
    try:
        for request in requests:
            if calls >= auth['maxRequests']:
                stop = 'request_limit'
                break
            job, payload = job_of(request), request['payload']
            reserved = reservation(package, auth, job, payload)
            if cost + Decimal(reserved['costCny']) > budget:
                stop = 'estimated_budget_guard'
                break
            if calls:
                sleep(THROTTLE_SECONDS)
            si.append_event(events, {'event': 'start', **job, 'at': si.utc(), 'request': payload, 'reservation': reserved})
            pending = (job, reserved)
            calls += 1
            response, exception = None, None
            try:
                response = send(job['judge'], payload)
            except Exception as error:
                exception = error
            record = response_record(job, response, [contexts[job['arm']]], exception)
            record['charge'] = charge(record, reserved, auth)
            cost += Decimal(record['charge']['costCny'])
            si.append_event(events, {'event': 'end' if record['status'] == 'ok' else 'failure', **record,
                                     'response': response, 'at': si.utc()})
            pending = None
            records.append(record)
            stop = model_halt(record, response, package) or halt(record, reserved, response)
            if stop:
                break
        else:
            stop = 'completed'
    except BaseException:
        if pending is not None:
            job, reserved = pending
            records.append(incomplete_record(job, reserved))
            cost += Decimal(reserved['costCny'])
        stop = 'interrupted'
        raise
    finally:
        si.append_event(events, {'event': 'stop', 'reason': stop, 'attempts': calls,
                                 'estimatedCostCny': str(cost), 'at': si.utc()})
        si.write_json(output / 'manifest.json', {
            'schemaVersion': 1, 'experimentVersion': VERSION, 'stopReason': stop, 'attempts': calls,
            'completedRecords': sum(r['status'] != 'incomplete' for r in records), 'plannedRequests': len(requests),
            'estimatedCostCny': str(cost), 'actualBillCny': None, 'costNote': COST_NOTE})
        si.write_json(output / 'records.json', records)
        si.checksums(output)
    return si.load(output / 'manifest.json')


def read_records(directory, package, requests, contexts, auth):
    """从 events.jsonl 重建每条结果，并核对请求体、计费、停止原因、manifest 与 records。"""
    starts, records, stopped, current, reserved, halted = [], [], None, None, None, None
    cost, budget = Decimal(0), Decimal(str(auth['budgetCny']))
    for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        si.require(stopped is None, '停止后出现事件')
        kind = event.get('event')
        if kind == 'stop':
            si.require(current is None or event.get('reason') == 'interrupted', '请求未结束但未标记中断')
            if current is not None:
                records.append(incomplete_record(current, reserved))
                cost += Decimal(reserved['costCny'])
            si.require(event.get('attempts') == len(starts) and event.get('estimatedCostCny') == str(cost), '停止计数/费用不符')
            stopped = event
            continue
        si.require(kind in ('start', 'end', 'failure'), '未知事件')
        if kind == 'start':
            si.require(current is None and halted is None and len(starts) < min(len(requests), auth['maxRequests']),
                       '请求越过停止条件')
            request = requests[len(starts)]
            job = job_of(request)
            si.require(all(event.get(k) == v for k, v in job.items()) and event.get('request') == request['payload'],
                       '调度或请求体不符')
            reserved = reservation(package, auth, job, request['payload'])
            si.require(event.get('reservation') == reserved and cost + Decimal(reserved['costCny']) <= budget, '预留/预算不符')
            starts.append(job)
            current = job
            continue
        si.require(current is not None and all(event.get(k) == v for k, v in current.items()), '结束事件无对应开始')
        record = {k: v for k, v in event.items() if k not in ('event', 'at', 'response')}
        si.require(set(record) == set(current) | {'status', 'result', 'actualModel', 'usage', 'error', 'charge'}, '记录字段不符')
        response = event.get('response')
        fields = response_record(current, response, [contexts[current['arm']]])
        error = record['error']
        valid_error = (isinstance(error, dict) and set(error) == {'type', 'note'} and
                       isinstance(error['type'], str) and bool(error['type'].strip()) and
                       error['note'] == '不归档异常消息；不重试。') if response is None else error == fields['error']
        si.require(valid_error, '错误字段与原始响应不符')
        si.require(all(record.get(k) == fields[k] for k in ('status', 'result', 'actualModel', 'usage')), '结果/用量与原始响应不符')
        si.require((kind == 'end') == (record['status'] == 'ok'), '结束事件类型不符')
        si.require(record.get('charge') == charge(record, reserved, auth), '计费不符')
        cost += Decimal(record['charge']['costCny'])
        halted = model_halt(record, response, package) or halt(record, reserved, response)
        records.append(record)
        current = None
    si.require(stopped is not None and stopped.get('reason') in STOP_REASONS, '缺少或未知停止事件')
    reason = stopped['reason']
    if reason != 'interrupted':
        if halted:
            expected = halted
        elif len(starts) == len(requests):
            expected = 'completed'
        elif len(starts) >= auth['maxRequests']:
            expected = 'request_limit'
        else:
            upcoming = reservation(package, auth, job_of(requests[len(starts)]), requests[len(starts)]['payload'])
            expected = 'estimated_budget_guard' if cost + Decimal(upcoming['costCny']) > budget else None
        si.require(reason == expected, '停止原因与事件不符')
    manifest = si.load(directory / 'manifest.json')
    si.require(manifest.get('stopReason') == reason and manifest.get('attempts') == len(starts) and
               manifest.get('completedRecords') == sum(r['status'] != 'incomplete' for r in records) and
               manifest.get('plannedRequests') == len(requests) and
               manifest.get('estimatedCostCny') == str(cost), 'manifest 与事件不符')
    si.require(si.load(directory / 'records.json') == records, 'records 与事件不符')
    return records


def decide(name, detected, false_positives):
    return detected <= 3 if name == 'blindSpot' else detected >= 7 if name in ('intervention', 'layered') \
        else false_positives <= 1


def summarize(package, records):
    verdicts = {(r['itemId'], r['arm']): r['result']['verdict'] for r in records if r['status'] == 'ok'}
    planned = {(item['id'], arm) for item, arm in schedule(package)}
    cells = {}
    for arm in ARMS:
        for layer in ('款级', '条级', '对照'):
            members = [i for i in package['items'] if i['layer'] == layer and (i['id'], arm) in planned]
            if not members:
                continue
            hit = [i['id'] for i in members if verdicts.get((i['id'], arm)) in si.NOT_SUPPORTED]
            missing = [i['id'] for i in members if (i['id'], arm) not in verdicts]
            cells[f'{arm}--{layer}'] = {
                'items': len(members), 'notSupported': len(hit), 'ids': hit,
                'detected': sum(i['expected'] == 'not_supported' for i in members if i['id'] in hit),
                'falsePositives': sum(i['expected'] == 'supported' for i in members if i['id'] in hit),
                'unavailable': len(missing), 'unavailableIds': missing}
    thresholds = {}
    for name, spec in THRESHOLDS.items():
        cell = cells[f"{spec['arm']}--{spec['layer']}"]
        detected, fp, missing = cell['detected'], cell['falsePositives'], cell['unavailable']
        # 不可用两头都算一遍：都满足或都不满足才算结论稳定。
        low = decide(name, detected, fp)
        high = decide(name, detected + (missing if spec['layer'] != '对照' else 0),
                      fp + (missing if spec['layer'] == '对照' else 0))
        thresholds[name] = {**spec, 'detected': detected, 'falsePositives': fp, 'unavailable': missing,
                            'met': low and high, 'robust': low == high}
    paired = {k: [] for k in ('newDetections', 'lostDetections', 'newFalsePositives', 'reducedFalsePositives', 'unavailable')}
    for item in package['items']:
        if item['layer'] == '条级':
            continue
        before, after = verdicts.get((item['id'], 'U')), verdicts.get((item['id'], 'L'))
        if before is None or after is None:
            paired['unavailable'].append(item['id'])
        elif (before in si.NOT_SUPPORTED) != (after in si.NOT_SUPPORTED):
            key = (('newDetections' if after in si.NOT_SUPPORTED else 'lostDetections')
                   if item['expected'] == 'not_supported' else
                   ('newFalsePositives' if after in si.NOT_SUPPORTED else 'reducedFalsePositives'))
            paired[key].append(item['id'])
    return {'cells': cells, 'thresholds': thresholds,
            'established': all(t['met'] for t in thresholds.values()),
            'robust': all(t['robust'] for t in thresholds.values()),
            'pairedUtoL': {k: {'count': len(v), 'ids': v} for k, v in paired.items()},
            'records': {'total': len(records), 'ok': sum(r['status'] == 'ok' for r in records),
                        'failed': [r['requestId'] for r in records if r['status'] != 'ok'],
                        'notAttempted': len(planned) - len(records)},
            'detection': '检出 = verdict 为 contradicted 或 insufficient；失败或解析失败单列为不可用，不算未检出。'}


def analyze(directory, output):
    empty_output(output)
    si.verify_checksums(directory)
    # 归档源码逐文件必须等于当前源码，才用同一实现回放；不执行归档里的 Python。
    si.require(tool_hashes(directory / 'sources') == tool_hashes(ROOT), '当前实现与归档 sources 不一致')
    package, requests, contexts = validate_package(directory / 'package')
    auth = si.load(directory / 'authorization.json')
    validate_authorization(directory / 'package', auth, None, directory / 'sources')
    records = read_records(directory, package, requests, contexts, auth)
    result = {'schemaVersion': 1, 'experimentVersion': VERSION, 'registrationSha256': REGISTRATION_SHA256,
              **summarize(package, records)}
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / 'analysis.json', result)
    si.checksums(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    template = sub.add_parser('template', help='校验题包并输出授权骨架（不调用模型）')
    template.add_argument('--package', type=Path, required=True)
    execute = sub.add_parser('run')
    execute.add_argument('--package', type=Path, required=True)
    execute.add_argument('--authorization', type=Path, required=True)
    execute.add_argument('--output', type=Path, required=True)
    analysis = sub.add_parser('analyze')
    analysis.add_argument('--run', type=Path, required=True)
    analysis.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'template':
        result = authorization_template(args.package)
    elif args.command == 'run':
        result = run(args.package, args.authorization, args.output)
    else:
        result = analyze(args.run, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
