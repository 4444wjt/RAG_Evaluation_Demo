"""Build a historical product fixture from the public blindspot archive, offline.

This script never imports an evaluator or transport, reads credentials, or sends
requests. It checks archived bytes and recomputes counts; it does not certify the
semantic correctness of the archived labels or diagnose a new user's document.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_PATH = 'docs/competition/statute-check/20261022-blindspot'
OUTPUT_PATH = 'frontend/src/fixtures/blindspot-check.json'
REGISTRATION_SHA256 = '3703256a6e24f2a1c848a772638d7f71286896843ddfd4682239fc72a47f4d06'
DETECTED = {'contradicted', 'insufficient'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_bytes().decode('utf-8'))


def json_lines(path):
    return [json.loads(line) for line in path.read_bytes().decode('utf-8').splitlines() if line]


def verify_manifests(archive):
    """Count manifest entries, including repeated coverage in nested manifests."""
    verified = 0
    manifests = sorted(archive.rglob('SHA256SUMS.txt'))
    require(len(manifests) == 5, '公开归档应包含五份 SHA256SUMS 清单')
    for manifest in manifests:
        seen = set()
        for line in manifest.read_bytes().decode('utf-8').splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
            require(match is not None, f'哈希清单格式错误：{manifest.name}')
            digest, relative = match.groups()
            target = (manifest.parent / relative).resolve()
            require(target.is_relative_to(manifest.parent.resolve()), '哈希清单路径越界')
            require(relative not in seen, '哈希清单路径重复')
            seen.add(relative)
            require(target.is_file(), f'归档文件缺失：{relative}')
            require(sha256(target.read_bytes()) == digest, f'归档哈希不符：{relative}')
            verified += 1
    return verified


def validate_contexts(package, directory):
    values = {}
    for arm in ('U', 'L'):
        entry = package['contexts'][arm]
        target = (directory / entry['file']).resolve()
        require(target.is_relative_to(directory.resolve()), '上下文路径越界')
        data = target.read_bytes()
        require(sha256(data) == entry['sha256'], f'{arm} 上下文哈希不符')
        values[arm] = data
    labeled = values['L'].decode('utf-8')
    insertions = package['contexts']['L']['insertions']
    require(len(insertions) == 13, '预期十三处历史插入')
    previous = len(labeled) + 1
    for insertion in reversed(insertions):
        offset, text = insertion['offset'], insertion['text']
        require(type(offset) is int and 0 <= offset < previous, '插入位置无效或乱序')
        require(isinstance(text, str) and text, '插入文本无效')
        require(labeled[offset:offset + len(text)] == text, '标签插入记录与原始字节不符')
        labeled = labeled[:offset] + labeled[offset + len(text):]
        previous = offset
    require(labeled.encode('utf-8') == values['U'], '删除标签后不能逐字节恢复原节选')
    require(package['source']['sha256'] == sha256(values['U']), '题包来源与原节选不一致')
    return values


def validate_requests(package, requests, contexts):
    items = {item['id']: item for item in package['items']}
    require(len(items) == 25, '历史题包应有二十五条陈述且编号唯一')
    expected = {(item['id'], arm) for item in items.values() for arm in ('U', 'L')
                if not (arm == 'L' and item['layer'] == '条级')}
    require(len(requests) == len(expected) == 40, '历史请求数不符')
    indexed = {}
    system_messages = []
    config = package['judges']['deepseek']
    for request in requests:
        key = request['itemId'], request['arm']
        require(key in expected and key not in indexed, '请求调度缺失、重复或越界')
        require(request['requestId'] == f'{key[0]}--{key[1]}--deepseek', '请求编号不一致')
        item = items[key[0]]
        payload = request['payload']
        require(payload['model'] == config['model'] and payload['temperature'] == config['temperature']
                and payload['max_tokens'] == config['maxTokens'] and payload['thinking'] == config['thinking']
                and payload['stream'] is False, '请求配置与题包裁判设置不同')
        messages = payload['messages']
        require(len(messages) == 2 and messages[0]['role'] == 'system'
                and messages[1]['role'] == 'user', '历史消息结构不符')
        body = json.loads(messages[1]['content'])
        require(body == {'question': item['question'], 'statement': item['statement'], 'contexts': [
            {'contextIndex': 0, 'text': contexts[key[1]].decode('utf-8')}]}, '请求题面或资料与题包不符')
        system_messages.append(messages[0])
        normalized = deepcopy(payload)
        body['contexts'] = '<仅上下文不同>'
        normalized['messages'][1]['content'] = json.dumps(body, ensure_ascii=False, sort_keys=True)
        indexed[key] = normalized
    require(all(message == system_messages[0] for message in system_messages), '历史系统提示发生变化')
    for item in items.values():
        if item['layer'] != '条级':
            require(indexed[item['id'], 'U'] == indexed[item['id'], 'L'], '配对请求除上下文外存在变化')
    return expected


def validate_records(records, events, requests, expected, manifest):
    require(len(records) == len(expected), '结果记录数不符')
    indexed = {}
    by_request = {request['requestId']: request for request in requests}
    starts, ends = {}, {}
    for event in events:
        if event['event'] in ('start', 'end'):
            target = starts if event['event'] == 'start' else ends
            request_id = event['requestId']
            require(request_id in by_request and request_id not in target, '事件编号缺失或重复')
            target[request_id] = event
            if event['event'] == 'start':
                require(event['request'] == by_request[request_id]['payload'], '发送事件与题包请求不同')
    require(starts.keys() == ends.keys() == by_request.keys(), '开始、结束事件不完整')
    for record in records:
        key = record['itemId'], record['arm']
        require(key in expected and key not in indexed, '结果记录重复或越界')
        require(record['requestId'] == f'{key[0]}--{key[1]}--deepseek', '结果编号不一致')
        end = ends[record['requestId']]
        require(all(end[field] == value for field, value in record.items()), '结果与结束事件不一致')
        require(record['status'] == 'ok' and record['actualModel'] == 'deepseek-flash', '历史结果不可用或模型名不同')
        require(record['result']['verdict'] in {'supported', *DETECTED}, '历史判定类型未知')
        indexed[key] = record
    require(manifest['stopReason'] == 'completed' and manifest['attempts'] == manifest['completedRecords']
            == manifest['plannedRequests'] == len(expected), '历史运行未完整结束')
    return indexed


def build_fixture(archive=ROOT / ARCHIVE_PATH):
    archive = Path(archive)
    verified = verify_manifests(archive)
    package_dir = archive / 'run/package'
    package = load(package_dir / 'package.json')
    authorization = load(archive / 'run/authorization.json')
    require(package['registration']['sha256'] == authorization['registrationSha256'] == REGISTRATION_SHA256,
            '登记绑定不一致')
    for filename, field in [('package.json', 'packageSha256'), ('requests.jsonl', 'requestsSha256')]:
        require(sha256((package_dir / filename).read_bytes()) == authorization[field], '授权输入哈希不一致')
    contexts = validate_contexts(package, package_dir)
    require(all(sha256(contexts[arm]) == authorization[field] for arm, field in
                [('U', 'plainSha256'), ('L', 'labeledSha256')]), '授权上下文哈希不同')
    requests = json_lines(package_dir / 'requests.jsonl')
    expected = validate_requests(package, requests, contexts)
    events = json_lines(archive / 'run/events.jsonl')
    manifest = load(archive / 'run/manifest.json')
    records = validate_records(load(archive / 'run/records.json'), events, requests, expected, manifest)
    analysis = load(archive / 'analysis/analysis.json')
    require((archive / 'analysis/analysis.json').read_bytes() ==
            (archive / 'analysis-replay/analysis.json').read_bytes(), '两次历史离线分析字节不同')

    cells = {}
    for arm, layer in [('U', '款级'), ('L', '款级'), ('U', '对照'), ('L', '对照'), ('U', '条级')]:
        selected = [item for item in package['items'] if item['layer'] == layer]
        detected = sum(records[item['id'], arm]['result']['verdict'] in DETECTED for item in selected)
        cell = {'items': len(selected), 'detected': detected if layer != '对照' else 0,
                'falsePositives': detected if layer == '对照' else 0, 'unavailable': 0}
        require(all(analysis['cells'][f'{arm}--{layer}'][key] == value for key, value in cell.items()),
                '逐条重算统计与历史分析不同')
        cells[arm, layer] = cell

    def arm_stats(arm):
        wrong, control = cells[arm, '款级'], cells[arm, '对照']
        return {'detected': wrong['detected'], 'total': wrong['items'],
                'falsePositives': control['falsePositives'], 'controls': control['items']}

    before, after = arm_stats('U'), arm_stats('L')
    thresholds = [
        {'id': 'blindSpot', 'label': '原节选款级盲区重现', 'passed': before['detected'] <= 3,
         'observed': before['detected'], 'total': before['total'], 'operator': 'lte', 'threshold': 3},
        {'id': 'intervention', 'label': '补标签后款级检出', 'passed': after['detected'] >= 7,
         'observed': after['detected'], 'total': after['total'], 'operator': 'gte', 'threshold': 7},
        {'id': 'usable', 'label': '两组正确对照误报',
         'passed': before['falsePositives'] <= 1 and after['falsePositives'] <= 1,
         'observed': {'before': before['falsePositives'], 'after': after['falsePositives']},
         'total': 5, 'operator': 'lte', 'threshold': 1},
        {'id': 'layered', 'label': '原节选条级检出', 'passed': cells['U', '条级']['detected'] >= 7,
         'observed': cells['U', '条级']['detected'], 'total': cells['U', '条级']['items'],
         'operator': 'gte', 'threshold': 7},
    ]
    require(all(item['passed'] for item in thresholds) == analysis['established'], '登记结论重算不同')

    def result_for(item_id, arm):
        record = records.get((item_id, arm))
        return None if record is None else {
            'status': record['status'], 'verdict': record['result']['verdict'], 'reason': record['result']['reason']}

    rows = [{**{key: item[key] for key in ['id', 'type', 'layer', 'statement', 'question', 'cited', 'contentFrom']},
             'before': result_for(item['id'], 'U'), 'after': result_for(item['id'], 'L')}
            for item in package['items']]
    first_start = next(event for event in events if event['event'] == 'start')['at']
    return {
        'schema': 'blindspot-product.v1',
        'source': {'archivePath': ARCHIVE_PATH, 'runId': archive.name,
                   'registrationPath': package['registration']['path'],
                   'registrationSha256': package['registration']['sha256'],
                   'packageSha256': sha256((package_dir / 'package.json').read_bytes()),
                   'recordsSha256': sha256((archive / 'run/records.json').read_bytes()),
                   'modelRequested': package['judges']['deepseek']['model'],
                   'modelReturned': 'deepseek-flash', 'date': first_start[:10], 'hashesVerified': verified},
        'stats': {'before': before, 'after': after, 'calls': len(records),
                  'contentUnits': len({item['contentFrom'] for item in package['items']})},
        'contexts': {'plain': contexts['U'].decode('utf-8'), 'labeled': contexts['L'].decode('utf-8'),
                     'plainSha256': sha256(contexts['U']), 'labeledSha256': sha256(contexts['L']),
                     'plainBytes': len(contexts['U']), 'labeledBytes': len(contexts['L']),
                     'insertions': package['contexts']['L']['insertions'],
                     'bodyBytesUnchanged': True, 'pairedRequestsMatch': True},
        'thresholds': thresholds, 'rows': rows,
        'metadata': {'mode': 'historical_replay', 'singleRun': True, 'modelCallsThisBuild': 0,
                     'archiveDate': '2026-10-22', 'runStartedAt': first_start, 'independentItems': False,
                     'hashesVerifiedBasis': '五份清单的校验条目合计，包含重复覆盖，不是五十个独立文件',
                     'registrationOriginalIncluded': False,
                     'estimatedHistoricalCostCny': manifest['estimatedCostCny'],
                     'actualHistoricalBillCny': manifest['actualBillCny'],
                     'limitations': ['历史单次归档展示，不是本轮新模型实测，也不体检用户上传的材料。',
                                     '二十五条陈述来自十个内容单元，不是二十五个独立样本。',
                                     '单部法规节选、单个裁判；补标签组未跑条级错误。',
                                     '仅改变引用、不改写内容；不外推真实业务或其他模型。',
                                     '登记原文为内部材料；公开归档仅提供其哈希和授权绑定。',
                                     '字节校验只证明输入和归档一致，不证明语义标签正确或唯一成因。']},
    }


def encode_fixture(fixture):
    return (json.dumps(fixture, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=ROOT / ARCHIVE_PATH)
    parser.add_argument('--output', type=Path, default=ROOT / OUTPUT_PATH)
    parser.add_argument('--check', action='store_true', help='只核对当前 fixture，不写文件')
    args = parser.parse_args(argv)
    try:
        expected = encode_fixture(build_fixture(args.archive))
        if args.check:
            require(args.output.is_file() and args.output.read_bytes() == expected, '产品 fixture 与公开归档不一致')
            print('盲区体检 fixture 核对通过；仅离线归档检查，模型调用 0。')
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(expected)
            print('盲区体检 fixture 已生成；仅离线归档检查，模型调用 0。')
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        print(f'盲区体检 fixture 检查失败：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
