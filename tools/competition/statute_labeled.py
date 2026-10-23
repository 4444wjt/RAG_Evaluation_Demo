"""补标签 / 切片裁判实验。prepare/analyze 离线；run 仅接受独立绑定授权。"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import statute_check as sc  # noqa: E402
from tools.competition import statute_injection as si  # noqa: E402

VERSION = 'statute-labeled-v1'
BASE = 'docs/competition/statute-check/20261021-pinpoint-run/run/package.json'
BASE_SHA256 = 'a70506f4502780194fce6c3f611cc494e1bb371f9c5b2d36749fbc32e9fc958f'
SOURCE_SHA256 = 'fee557dc493eb2b92f52834cf9b164720cff6b13f276aaa2054babccbc4666eb'
REFERENCE = 'docs/competition/statute-check/20261022-equalinfo/run'
INPUT_FILES = (BASE, si.SOURCE, f'{REFERENCE}/package.json', f'{REFERENCE}/records.json')
TOOL_FILES = (*si.TOOL_FILES, 'tools/competition/statute_labeled.py', 'tests/test_statute_labeled.py')
JUDGES = {
    'deepseek': {'model': 'deepseek-flash', 'baseUrl': 'https://api.deepseek.com',
                 'baseUrlEnv': 'DEEPSEEK_BASE_URL', 'apiKeyEnv': 'OPENAI_API_KEY',
                 'temperature': 0, 'maxTokens': 768, 'timeoutSeconds': 90,
                 'maxRetries': 0, 'thinking': {'type': 'disabled'}},
    'qwen72b': {'model': 'Qwen/Qwen2.5-72B-Instruct', 'baseUrl': 'https://api.siliconflow.cn/v1',
                'baseUrlEnv': 'QWEN_BASE_URL', 'apiKeyEnv': 'SILICONFLOW_API_KEY',
                'temperature': 0, 'maxTokens': 768, 'timeoutSeconds': 90, 'maxRetries': 0},
}
SLICE_TYPES = ('paragraph_existing', 'control_pinpoint')
PARAGRAPH_TYPES = ('paragraph_existing', 'paragraph_absent')
SUPPORT_TYPES = ('control', 'control_pinpoint', 'paragraph_omitted')
STOP_REASONS = ('request_limit', 'estimated_budget_guard', 'actual_model_unknown',
                'reservation_exceeded', 'interrupted')
LABEL_RULE = ('逐行 normalize 后按 parse_provisions 识别，输出原始行：条标题第一个第X条之后插入第一款；'
              '其他非空非项款行前插入第X条第N款及全角空格；数字用 cn_label；项、标题、空行不变，保留换行符。')
SLICE_RULE = ('paragraph_existing 和 control_pinpoint 按材料顺序；normalize 后第一个 REF 经 _ref_value 解析；'
              '款=原始款行及其全部项行，项=原始总领款行及被引项行；以\\n连接，不补标签；仅引条或单元不存在时报错。')
PREREGISTRATION = '''- 检出：裁判 verdict 为 `contradicted` 或 `insufficient`。`supported` 为未检出。失败或解析失败为不可用，单列，不算未检出。只报条数。
- 款级错误 = `paragraph_existing` + `paragraph_absent`，共 31；支持对照 = `control` + `control_pinpoint` + `paragraph_omitted`，共 31。
- 主张 1（DeepSeek，条件 L）：
  - 成立：款级检出 ≥ 15/31，且 `paragraph_absent` 检出 ≥ 10/13，且支持对照误报 ≤ 4/31；
  - 推翻：款级检出 ≤ 5/31，或支持对照误报 > 7/31；
  - 其余：部分成立。
- 主张 3，补标签臂（Qwen，条件 L）：
  - 与主张一致：款级检出 ≤ 5/31；
  - 推翻：≥ 10/31；
  - 其余（6–9）：部分。
- 主张 3，切片臂（Qwen，条件 S）：
  - 成立：`paragraph_existing` 检出 ≥ 15/18，且 `control_pinpoint` 误报 ≤ 3/13；
  - 推翻：`paragraph_existing` 检出 ≤ 9/18；
  - 其余：部分成立。
- 有不可用时：判定照上面的条数算，同时在输出里列出不可用 ID，并标注「含不可用，结论可能变化」。
- 另外报告（描述，不作判定）：
  - L 条件下与 equalinfo J2 同裁判逐条配对：新检出、丢失检出、新误报、减少误报，列出 ID；
  - S 条件下 Qwen 与 equalinfo J1、J2 同条配对；
  - 项级 4 条与其他类型的条数；
  - 剔除 S26 全部变体的敏感性复算。'''
COST_NOTE = '费用为授权单价估算，不是账单。无 usage（含失败）按本次预留计；不重试。'


def empty_output(output):
    si.require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
               '输出必须是新的空目录；禁止覆盖或续跑')


def source_text(root=ROOT):
    # read_text 会把 CRLF 转为 LF；这里必须逐字节保留。
    return (root / si.SOURCE).read_bytes().decode('utf-8')


def provision_lines(text):
    """与 parse_provisions 同一状态机，同时保留原始行和换行符。"""
    article, paragraph = None, 0
    rows = []
    for number, raw in enumerate(text.splitlines(keepends=True)):
        line = sc.normalize(raw).strip()
        location, head = None, sc.HEADER.match(line)
        if not line:
            article = None
        elif head:
            article, paragraph = sc.cn_number(head.group(1)), 1
            location = (article, paragraph, None)
        elif article is not None:
            item = sc.ITEM.match(line)
            if item:
                location = (article, paragraph, sc.cn_number(item.group(1)))
            else:
                paragraph += 1
                location = (article, paragraph, None)
        rows.append({'line': number, 'raw': raw, 'location': location, 'header': bool(head)})
    si.require([r['location'] for r in rows if r['location']] ==
               [(p.article, p.paragraph, p.item) for p in sc.parse_provisions(text)], '行编号与 parse_provisions 不一致')
    return rows


def label_source(text):
    output, insertions, offset = [], [], 0
    for row in provision_lines(text):
        raw, loc = row['raw'], row['location']
        if loc and loc[2] is None:
            article, paragraph, _ = loc
            if row['header']:
                # normalize 不改变字符数量，只用归一化文本定位，原文仍用 raw。
                position = sc.normalize(raw).index('条') + 1
                label = f'第{sc.cn_label(1)}款'
            else:
                position = 0
                label = f'第{sc.cn_label(article)}条第{sc.cn_label(paragraph)}款　'
            insertions.append({'line': row['line'], 'article': article, 'paragraph': paragraph,
                               'offset': offset + position, 'text': label})
            raw = raw[:position] + label + raw[position:]
        output.append(raw)
        offset += len(raw)
    return ''.join(output), insertions


def build_slices(items, text):
    rows = provision_lines(text)
    locations = {r['location']: r['raw'].rstrip('\r\n') for r in rows if r['location']}
    slices = {}
    for item in items:
        if item['type'] not in SLICE_TYPES:
            continue
        match = sc.REF.search(sc.normalize(item['statement']))
        si.require(match is not None, f"{item['id']} 无可解析引用")
        article, paragraph, index = sc._ref_value(match)
        si.require(paragraph is not None and (article, paragraph, index) in locations,
                   f"{item['id']} 仅引条或所引单元不存在")
        parent = (article, paragraph, None)
        si.require(parent in locations, f"{item['id']} 缺少总领款行")
        parts = [locations[parent]]
        parts += ([locations[(article, paragraph, index)]] if index is not None else
                  [r['raw'].rstrip('\r\n') for r in rows if r['location'] and
                   r['location'][:2] == parent[:2] and r['location'][2] is not None])
        slices[item['id']] = '\n'.join(parts)
    si.require(len(slices) == 31, '切片必须为31条')
    return slices


def build_package(root=ROOT):
    si.require(si.sha256(root / BASE) == BASE_SHA256, '材料 SHA-256 不符')
    si.require(si.sha256(root / si.SOURCE) == SOURCE_SHA256, '节选 SHA-256 不符')
    items = deepcopy(si.load(root / BASE)['items'])
    si.require(len(items) == 66 and len({i['id'] for i in items}) == 66, '材料须为66条唯一陈述')
    labeled, _ = label_source(source_text(root))
    slices = build_slices(items, source_text(root))
    return {'schemaVersion': 1, 'experimentVersion': VERSION, 'items': items,
            'sourceFiles': {p: si.sha256(root / p) for p in INPUT_FILES},
            'conditions': {'L': {'judges': ['deepseek', 'qwen72b'], 'context': '整份补标签后的节选'},
                           'S': {'judges': ['qwen72b'], 'types': list(SLICE_TYPES), 'context': '所引款或项的原文切片'}},
            'judges': deepcopy(JUDGES), 'promptSha256': si.digest(si.SYSTEM_PROMPT),
            'labeledSha256': hashlib.sha256(labeled.encode('utf-8')).hexdigest(),
            'slicesSha256': si.digest(slices), 'labelRule': LABEL_RULE, 'sliceRule': SLICE_RULE,
            'preregistration': PREREGISTRATION, 'maxRequests': 163, 'maxRetries': 0,
            'scheduleRule': '先L：材料顺序每条deepseek、qwen72b；再S：材料顺序31条qwen72b。',
            'throttleSeconds': 1.0, 'scope': '单一法规、共享底稿、单次配对；不参与 Quality Gate；只报条数。'}


def validate_package(package, root=ROOT):
    si.require(package == build_package(root), 'package 逐字段重建不一致')


def schedule(package):
    return [{'requestId': f"{i['id']}--{condition}--{judge}", 'itemId': i['id'],
             'condition': condition, 'judge': judge}
            for condition in ('L', 'S') for i in package['items']
            if condition == 'L' or i['type'] in SLICE_TYPES
            for judge in (('deepseek', 'qwen72b') if condition == 'L' else ('qwen72b',))]


def contexts_for(item, condition, root=ROOT):
    text = source_text(root)
    si.require(condition in ('L', 'S'), '未知条件')
    return [label_source(text)[0] if condition == 'L' else build_slices(si.load(root / BASE)['items'], text)[item['id']]]


def prompt_messages(item, condition, root=ROOT):
    messages = si.prompt_messages(item, {'contexts': contexts_for(item, condition, root)})
    messages[0]['content'] = si.SYSTEM_PROMPT
    return messages


def payload_for(package, item, job, root=ROOT):
    cfg = package['judges'][job['judge']]
    payload = {'model': cfg['model'], 'messages': prompt_messages(item, job['condition'], root),
               'temperature': cfg['temperature'], 'max_tokens': cfg['maxTokens'], 'stream': False}
    if 'thinking' in cfg:
        payload['thinking'] = deepcopy(cfg['thinking'])
    return payload


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


def tool_hashes(root=ROOT):
    return {name: si.sha256(root / name) for name in TOOL_FILES}


def authorization_template(package, package_path, prices, root=ROOT):
    return {'status': 'draft', 'allowPaidCalls': False, 'authorizedBy': None, 'authorizedAt': None,
            'note': None, 'packageSha256': si.sha256(package_path),
            'configurationSha256': si.digest(package['judges']), 'promptSha256': si.digest(si.SYSTEM_PROMPT),
            'labeledSha256': package['labeledSha256'], 'slicesSha256': package['slicesSha256'],
            'toolFiles': tool_hashes(root), 'maxRequests': 163, 'maxRetries': 0,
            'budgetCny': None, 'prices': deepcopy(prices), 'outputDirectory': None}


def validate_authorization(package, package_path, auth, output, root=ROOT):
    si.require(auth.get('status') == 'authorized' and auth.get('allowPaidCalls') is True, '未授权付费')
    for name in ('authorizedBy', 'note'):
        si.require(isinstance(auth.get(name), str) and bool(auth[name].strip()), f'授权缺少 {name}')
    iso_date(auth.get('authorizedAt'))
    fields = {'packageSha256': si.sha256(package_path), 'configurationSha256': si.digest(package['judges']),
              'promptSha256': si.digest(si.SYSTEM_PROMPT), 'labeledSha256': package['labeledSha256'],
              'slicesSha256': package['slicesSha256'], 'toolFiles': tool_hashes(root)}
    si.require(all(auth.get(k) == v for k, v in fields.items()), '授权哈希不符')
    si.require(type(auth.get('maxRequests')) is int and auth['maxRequests'] == 163 and
               type(auth.get('maxRetries')) is int and auth['maxRetries'] == 0, '请求上限必须为163且不重试')
    si.require(type(auth.get('budgetCny')) in (int, float) and math.isfinite(auth['budgetCny']) and
               auth['budgetCny'] > 0, '预算必须为正有限数')
    if output is not None:
        si.require(isinstance(auth.get('outputDirectory'), str) and bool(auth['outputDirectory'].strip()) and
                   (root / auth['outputDirectory']).resolve() == output.resolve(), '授权未绑定本次输出目录')
    si.require(isinstance(auth.get('prices'), dict), '缺少单价')
    for judge in JUDGES:
        price = auth['prices'].get(judge)
        si.require(isinstance(price, dict) and all(type(price.get(k)) in (int, float) and
                   math.isfinite(price[k]) and price[k] >= 0 for k in ('inputCnyPerMillion', 'outputCnyPerMillion')),
                   '单价无效')
        si.require(isinstance(price.get('source'), str) and bool(price['source'].strip()), '单价缺少来源')
        iso_date(price.get('checkedAt'))


def cost_estimate(package, prices, root=ROOT):
    starts, history = {}, {}
    for line in (root / REFERENCE / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event.get('condition') not in ('J1', 'J2'):
            continue
        if event['event'] == 'start':
            starts[event['requestId']] = event
        elif event['event'] in ('end', 'failure'):
            start = starts.pop(event['requestId'])
            key = (event['itemId'], event['condition'], event['judge'])
            si.require(key not in history, '参照调用重复')
            history[key] = (message_bytes(start['request']['messages']), Decimal(event['charge']['costCny']))
    items = {i['id']: i for i in package['items']}
    rows = {}
    for job in schedule(package):
        key = (job['condition'], job['judge'])
        row = rows.setdefault(key, {'requests': 0, 'reservationUpperCny': Decimal(0), 'expectedCostCny': Decimal(0)})
        payload = payload_for(package, items[job['itemId']], job, root)
        reserved = reservation(package, {'prices': prices}, job, payload)
        old_bytes, old_cost = history[(job['itemId'], 'J2' if job['condition'] == 'L' else 'J1', job['judge'])]
        row['requests'] += 1
        row['reservationUpperCny'] += Decimal(reserved['costCny'])
        row['expectedCostCny'] += old_cost * Decimal(message_bytes(payload['messages'])) / Decimal(old_bytes)
    def total(values):
        values = list(values)
        return {'requests': sum(r['requests'] for r in values),
                **{k: str(sum((r[k] for r in values), Decimal(0))) for k in ('reservationUpperCny', 'expectedCostCny')}}
    result = {'byCondition': {}, 'byJudge': {}, **total(rows.values()),
              'method': 'L用同条同裁判J2 charge.costCny×新/旧消息UTF-8字节数；S用同条Qwen J1同式；预留输入字节+256、输出maxTokens。',
              'referenceEventsSha256': si.sha256(root / REFERENCE / 'events.jsonl')}
    for condition in ('L', 'S'):
        result['byCondition'][condition] = {**total(r for (c, j), r in rows.items() if c == condition),
            'judges': {j: {k: str(v) if isinstance(v, Decimal) else v for k, v in r.items()}
                       for (c, j), r in rows.items() if c == condition}}
    for judge in JUDGES:
        result['byJudge'][judge] = total(r for (c, j), r in rows.items() if j == judge)
    return result


def prepare(output, root=ROOT):
    empty_output(output)
    package = build_package(root)
    prices = si.load(root / REFERENCE / 'authorization.json')['prices']
    estimates = cost_estimate(package, prices, root)
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / 'package.json', package)
    (output / 'labeled.txt').write_bytes(label_source(source_text(root))[0].encode('utf-8'))
    si.write_json(output / 'slices.json', build_slices(package['items'], source_text(root)))
    si.write_json(output / 'estimates.json', estimates)
    auth = authorization_template(package, output / 'package.json', prices, root)
    si.write_json(output / 'authorization.template.json', auth)
    return {'package': package, 'authorization': auth, 'estimates': estimates}


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


def run(package_path, authorization_path, output, *, root=ROOT, transport=None, sleep=time.sleep):
    package, auth = si.load(package_path), si.load(authorization_path)
    validate_package(package, root)
    validate_authorization(package, package_path, auth, output, root)
    empty_output(output)
    labeled = label_source(source_text(root))[0].encode('utf-8')
    slices = build_slices(package['items'], source_text(root))
    si.require(hashlib.sha256(labeled).hexdigest() == auth['labeledSha256'] and
               si.digest(slices) == auth['slicesSha256'], '上下文哈希不符')
    send = transport if transport is not None else si.make_transport(package)
    jobs, items = schedule(package), {i['id']: i for i in package['items']}
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package_path, output / 'package.json')
    shutil.copyfile(authorization_path, output / 'authorization.json')
    si.write_json(output / 'schedule.json', jobs)
    si.write_text(output / 'prompt.txt', si.SYSTEM_PROMPT)
    for folder, names in (('sources', TOOL_FILES), ('inputs', INPUT_FILES)):
        for name in names:
            target = output / folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
    (output / 'inputs/labeled.txt').write_bytes(labeled)
    si.write_json(output / 'inputs/slices.json', slices)
    records, calls, cost, stop = [], 0, Decimal(0), 'interrupted'
    events, budget = output / 'events.jsonl', Decimal(str(auth['budgetCny']))
    try:
        for job in jobs:
            payload = payload_for(package, items[job['itemId']], job, root)
            reserved = reservation(package, auth, job, payload)
            if cost + Decimal(reserved['costCny']) > budget:
                stop = 'estimated_budget_guard'
                break
            if calls:
                sleep(1.0)
            si.append_event(events, {'event': 'start', **job, 'at': si.utc(), 'request': payload, 'reservation': reserved})
            calls += 1
            response, exception = None, None
            try:
                response = send(job['judge'], payload)
            except Exception as error:
                exception = error
            record = response_record(job, response, contexts_for(items[job['itemId']], job['condition'], root), exception)
            record['charge'] = charge(record, reserved, auth)
            cost += Decimal(record['charge']['costCny'])
            si.append_event(events, {'event': 'end' if record['status'] == 'ok' else 'failure', **record,
                                     'response': response, 'at': si.utc()})
            records.append(record)
            stop = halt(record, reserved, response)
            if stop:
                break
            if calls >= auth['maxRequests']:
                stop = 'request_limit'
                break
    except BaseException:
        stop = 'interrupted'
        raise
    finally:
        si.append_event(events, {'event': 'stop', 'reason': stop, 'attempts': calls,
                                 'estimatedCostCny': str(cost), 'at': si.utc()})
        manifest = {'schemaVersion': 1, 'experimentVersion': VERSION, 'stopReason': stop,
                    'attempts': calls, 'completedRecords': len(records), 'plannedRequests': len(jobs),
                    'estimatedCostCny': str(cost), 'actualBillCny': None, 'costNote': COST_NOTE}
        si.write_json(output / 'manifest.json', manifest)
        si.write_json(output / 'records.json', records)
        si.checksums(output)
    return manifest


def read_records(directory, package, auth):
    jobs, items = schedule(package), {i['id']: i for i in package['items']}
    starts, records, stopped = [], [], None
    current = reserved = halted = None
    cost, budget = Decimal(0), Decimal(str(auth['budgetCny']))
    root = directory / 'inputs'
    for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        si.require(stopped is None, '停止后出现事件')
        kind = event.get('event')
        if kind == 'stop':
            si.require(event.get('attempts') == len(starts) and event.get('estimatedCostCny') == str(cost), '停止计数/费用不符')
            stopped = event
            continue
        si.require(kind in ('start', 'end', 'failure'), '未知事件')
        if kind == 'start':
            si.require(current is None and halted is None and len(starts) < min(len(jobs), auth['maxRequests']), '请求越过停止条件')
            job = jobs[len(starts)]
            si.require(all(event.get(k) == v for k, v in job.items()), '调度顺序不符')
            payload = payload_for(package, items[job['itemId']], job, root)
            si.require(event.get('request') == payload, '请求体不符')
            reserved = reservation(package, auth, job, payload)
            si.require(event.get('reservation') == reserved and cost + Decimal(reserved['costCny']) <= budget, '预留/预算不符')
            starts.append(job)
            current = job
            continue
        si.require(current is not None and all(event.get(k) == v for k, v in current.items()), '结束事件无对应开始')
        record = {k: v for k, v in event.items() if k not in ('event', 'at', 'response')}
        response = event.get('response')
        fields = response_record(current, response, contexts_for(items[current['itemId']], current['condition'], root))
        si.require(all(record.get(k) == fields[k] for k in ('status', 'result', 'actualModel', 'usage')), '结果/用量与原始响应不符')
        si.require((kind == 'end') == (record['status'] == 'ok'), '结束事件类型不符')
        si.require(record.get('charge') == charge(record, reserved, auth), '计费不符')
        cost += Decimal(record['charge']['costCny'])
        halted = halt(record, reserved, response)
        records.append(record)
        current = None
    si.require(stopped is not None and stopped.get('reason') in STOP_REASONS, '缺少或未知停止事件')
    reason = stopped['reason']
    terminal = list(records)
    if current is not None:
        si.require(reason == 'interrupted', '请求未结束但未标记中断')
        records.append({**current, 'status': 'incomplete', 'result': None, 'actualModel': None, 'usage': None,
                        'error': {'type': 'interrupted'}})
    if reason != 'interrupted':
        expected = halted or ('request_limit' if len(starts) >= auth['maxRequests'] else None)
        if expected is None and len(starts) < len(jobs):
            job = jobs[len(starts)]
            upcoming = reservation(package, auth, job, payload_for(package, items[job['itemId']], job, root))
            if cost + Decimal(upcoming['costCny']) > budget:
                expected = 'estimated_budget_guard'
        si.require(reason == expected, '停止原因与事件不符')
    manifest = si.load(directory / 'manifest.json')
    si.require(manifest.get('stopReason') == reason and manifest.get('attempts') == len(starts) and
               manifest.get('completedRecords') == len(terminal) and manifest.get('plannedRequests') == len(jobs) and
               manifest.get('estimatedCostCny') == str(cost), 'manifest 与事件不符')
    si.require(si.load(directory / 'records.json') == terminal, 'records 与事件不符')
    return records


def decide_claim1(detected, absent, false_positives):
    if detected <= 5 or false_positives > 7:
        return '推翻'
    return '成立' if detected >= 15 and absent >= 10 and false_positives <= 4 else '部分成立'


def decide_claim3_l(detected):
    return '与主张一致' if detected <= 5 else '推翻' if detected >= 10 else '部分'


def decide_claim3_s(detected, false_positives):
    return '推翻' if detected <= 9 else '成立' if detected >= 15 and false_positives <= 3 else '部分成立'


def summarize(package, records, reference):
    verdicts = {(r['itemId'], r['condition'], r['judge']): r['result']['verdict']
                for r in records if r['status'] == 'ok'}
    old = {(r['itemId'], r['condition'], r['judge']): r['result']['verdict']
           for r in reference if r['status'] == 'ok' and r.get('result') is not None and r['condition'] in ('J1', 'J2')}
    types = list(dict.fromkeys(i['type'] for i in package['items']))
    result = {'conditions': {}, 'paired': {}, 'preregistration': package['preregistration']}
    for condition, judges in (('L', ('deepseek', 'qwen72b')), ('S', ('qwen72b',))):
        members = [i for i in package['items'] if condition == 'L' or i['type'] in SLICE_TYPES]
        groups = {t: [i for i in members if i['type'] == t] for t in types}
        groups.update(paragraph_errors=[i for i in members if i['type'] in PARAGRAPH_TYPES],
                      support_controls=[i for i in members if i['type'] in SUPPORT_TYPES],
                      item_errors=[i for i in members if i['type'] in ('item_existing', 'item_absent')])
        result['conditions'][condition] = {}
        for judge in judges:
            unavailable = [i['id'] for i in members if (i['id'], condition, judge) not in verdicts]
            by_type = {}
            for kind, group in groups.items():
                detected = [i['id'] for i in group if verdicts.get((i['id'], condition, judge)) in si.NOT_SUPPORTED]
                missing = [i['id'] for i in group if (i['id'], condition, judge) not in verdicts]
                by_type[kind] = {'items': len(group), 'notSupported': len(detected), 'ids': detected,
                                 'unavailable': len(missing), 'unavailableIds': missing,
                                 'detectedErrors': sum(i['id'] in detected and i['expected'] == 'not_supported' for i in group),
                                 'falsePositives': sum(i['id'] in detected and i['expected'] == 'supported' for i in group)}
            row = {'byType': by_type, 'unavailableIds': unavailable,
                   'note': '含不可用，结论可能变化' if unavailable else None}
            if condition == 'L' and judge == 'deepseek':
                counts = {'paragraphDetections': by_type['paragraph_errors']['notSupported'],
                          'absentDetections': by_type['paragraph_absent']['notSupported'],
                          'supportFalsePositives': by_type['support_controls']['notSupported']}
                row['claim1'] = {**counts, 'result': decide_claim1(*counts.values())}
            elif condition == 'L':
                count = by_type['paragraph_errors']['notSupported']
                row['claim3L'] = {'paragraphDetections': count, 'result': decide_claim3_l(count)}
            else:
                detected, fp = by_type['paragraph_existing']['notSupported'], by_type['control_pinpoint']['notSupported']
                row['claim3S'] = {'paragraphExistingDetections': detected, 'controlPinpointFalsePositives': fp,
                                 'result': decide_claim3_s(detected, fp)}
            result['conditions'][condition][judge] = row
            for baseline in (('J2',) if condition == 'L' else ('J1', 'J2')):
                paired = {k: [] for k in ('newDetections', 'lostDetections', 'newFalsePositives', 'reducedFalsePositives', 'unavailable')}
                for item in members:
                    before, after = old.get((item['id'], baseline, judge)), verdicts.get((item['id'], condition, judge))
                    if before is None or after is None:
                        paired['unavailable'].append(item['id'])
                    elif (before in si.NOT_SUPPORTED) != (after in si.NOT_SUPPORTED):
                        key = (('newDetections' if after in si.NOT_SUPPORTED else 'lostDetections')
                               if item['expected'] == 'not_supported' else
                               ('newFalsePositives' if after in si.NOT_SUPPORTED else 'reducedFalsePositives'))
                        paired[key].append(item['id'])
                result['paired'][f'{condition}--{baseline}--{judge}'] = {k: {'count': len(ids), 'ids': ids} for k, ids in paired.items()}
    present = {r['requestId'] for r in records}
    missing = [j for j in schedule(package) if j['requestId'] not in present]
    result.update(failures=[r for r in records if r['status'] != 'ok'], notAttempted=missing,
                  records={'total': len(records), 'ok': sum(r['status'] == 'ok' for r in records),
                           'failed': sum(r['status'] != 'ok' for r in records), 'notAttempted': len(missing)},
                  typeCounts=dict(Counter(i['type'] for i in package['items'])))
    return result


def analyze(directory, output):
    empty_output(output)
    si.verify_checksums(directory)
    package, auth = si.load(directory / 'package.json'), si.load(directory / 'authorization.json')
    # 归档源码逐文件必须等于当前源码，才用同一实现重建；不执行任意归档 Python。
    si.require(tool_hashes(directory / 'sources') == tool_hashes(ROOT), '当前实现与归档 sources 不一致')
    validate_package(package, directory / 'inputs')
    validate_authorization(package, directory / 'package.json', auth, None, directory / 'sources')
    si.require(si.sha256(directory / 'inputs/labeled.txt') == auth['labeledSha256'] and
               si.digest(si.load(directory / 'inputs/slices.json')) == auth['slicesSha256'], '归档上下文哈希不符')
    si.require(si.load(directory / 'schedule.json') == schedule(package), '归档调度不符')
    si.require((directory / 'prompt.txt').read_bytes() == si.SYSTEM_PROMPT.encode('utf-8'), '归档提示不符')
    records = read_records(directory, package, auth)
    reference = si.load(directory / 'inputs' / REFERENCE / 'records.json')
    result = {'schemaVersion': 1, 'experimentVersion': VERSION,
              'referenceHashes': {p: package['sourceFiles'][p] for p in INPUT_FILES if p.startswith(REFERENCE)},
              **summarize(package, records, reference)}
    subset = deepcopy(package)
    subset['items'] = [i for i in package['items'] if i['base'] != 'S26']
    kept = {i['id'] for i in subset['items']}
    result['withoutS26'] = summarize(subset, [r for r in records if r['itemId'] in kept], reference)
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / 'analysis.json', result)
    si.checksums(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--output', type=Path, required=True)
    execute = sub.add_parser('run')
    execute.add_argument('--package', type=Path, required=True)
    execute.add_argument('--authorization', type=Path, required=True)
    execute.add_argument('--output', type=Path, required=True)
    analysis = sub.add_parser('analyze')
    analysis.add_argument('--run', type=Path, required=True)
    analysis.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(json.dumps(prepare(args.output)['estimates'], ensure_ascii=False))
    elif args.command == 'run':
        print(json.dumps(run(args.package, args.authorization, args.output), ensure_ascii=False))
    else:
        print(json.dumps(analyze(args.run, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
