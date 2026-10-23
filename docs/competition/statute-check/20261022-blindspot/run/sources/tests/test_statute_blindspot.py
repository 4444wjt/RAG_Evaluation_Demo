"""仅离线假 transport；不读取 .env，不调用提供方。题包用临时合成的；work/ 下的真实题包存在时另外核对。"""
from copy import deepcopy
from decimal import Decimal
import builtins
import json
from pathlib import Path
import socket

import pytest

from tools.competition import statute_blindspot as sb

si = sb.si
REAL_PACKAGE = sb.ROOT / 'work/statute-injection/blindspot-20261022/package'
TODAY = '2026-10-22'
JUDGE = {'model': 'deepseek-flash', 'baseUrl': 'https://api.deepseek.com', 'baseUrlEnv': 'DEEPSEEK_BASE_URL',
         'apiKeyEnv': 'OPENAI_API_KEY', 'temperature': 0, 'maxTokens': 768, 'timeoutSeconds': 90,
         'maxRetries': 0, 'thinking': {'type': 'disabled'}}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('离线测试禁止网络或真实 transport')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(si, 'make_transport', forbidden)
    original_open, path_open = builtins.open, Path.open
    def check(file):
        if isinstance(file, (str, Path)):
            assert Path(file).name != '.env', '禁止读取 .env'
    monkeypatch.setattr(builtins, 'open', lambda file, *a, **k: (check(file), original_open(file, *a, **k))[1])
    monkeypatch.setattr(Path, 'open', lambda path, *a, **k: (check(path), path_open(path, *a, **k))[1])


def make_package(directory):
    """按 make_exam.py 的结构合成一个最小题包：条数、层级、请求顺序与登记一致。"""
    items = [{'id': f'{kind}-{n}', 'type': kind, 'layer': sb.LAYERS[kind],
              'expected': 'supported' if kind == 'control_pinpoint' else 'not_supported',
              'question': '合成问题？', 'statement': f'按照第{n + 1}条规定，合成内容{kind}{n}。'}
             for kind, count in sb.COUNTS.items() for n in range(count)]
    (directory / 'contexts').mkdir(parents=True)
    texts = {'U': '第一条　合成原文。\n', 'L': '第一条第一款　合成原文。\n'}
    files = {'U': 'contexts/plain.txt', 'L': 'contexts/labeled.txt'}
    for arm, text in texts.items():
        (directory / files[arm]).write_bytes(text.encode('utf-8'))
    package = {'schemaVersion': 1, 'experimentVersion': sb.EXAM_VERSION,
               'registration': {'path': 'registration.md', 'sha256': sb.REGISTRATION_SHA256},
               'contexts': {arm: {'file': files[arm], 'sha256': si.sha256(directory / files[arm])} for arm in sb.ARMS},
               'judges': {'deepseek': deepcopy(JUDGE)}, 'promptSha256': si.digest(si.SYSTEM_PROMPT),
               'items': items, 'maxRequests': 40, 'maxRetries': 0}
    requests = [sb.build_request(item, arm, texts[arm], JUDGE) for item, arm in sb.schedule(package)]
    with open(directory / 'requests.jsonl', 'w', encoding='utf-8', newline='\n') as f:
        for request in requests:
            f.write(json.dumps(request, ensure_ascii=False, separators=(',', ':')) + '\n')
    package['requestsSha256'] = si.sha256(directory / 'requests.jsonl')
    si.write_json(directory / 'package.json', package)
    si.write_json(directory / 'rules.json', {})
    si.write_json(directory / 'estimates.json', {})
    si.checksums(directory)
    return directory


def authorize(package_dir, path, output, **changes):
    auth = sb.authorization_template(package_dir)
    auth.update(status='authorized', allowPaidCalls=True, authorizedBy='offline-test-only', authorizedAt=TODAY,
                note='仅临时假运行，非真实付费授权', budgetCny=0.5, outputDirectory=str(output),
                prices={'deepseek': {'inputCnyPerMillion': 2, 'outputCnyPerMillion': 8,
                                     'source': 'offline-test', 'checkedAt': TODAY}})
    auth.update(changes)
    si.write_json(path, auth)
    return path


def fake_response(verdict='supported', *, model='deepseek-flash', usage=True):
    response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
        {'verdict': verdict, 'reason': '离线假响应', 'citations': []}, ensure_ascii=False)}}]}
    if model is not None:
        response['model'] = model
    if usage:
        response['usage'] = {'prompt_tokens': 1000, 'completion_tokens': 60, 'total_tokens': 1060}
    return response


def by_expectation(package):
    """错误陈述判 contradicted，对照判 supported：让每格计数可预知。按陈述原文查预期标签。"""
    expected = {i['statement']: i['expected'] for i in package['items']}
    def send(judge, payload):
        statement = json.loads(payload['messages'][1]['content'])['statement']
        return fake_response('supported' if expected[statement] == 'supported' else 'contradicted')
    return send


def events_in(directory):
    return [json.loads(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]


@pytest.fixture(params=['synthetic', 'real'])
def package_dir(request, tmp_path):
    if request.param == 'real':
        if not REAL_PACKAGE.is_dir():
            pytest.skip('work/ 下没有真实题包')
        return REAL_PACKAGE
    return make_package(tmp_path / 'synthetic')


def execute(package_dir, tmp_path, transport, **changes):
    output = tmp_path / 'run'
    auth = authorize(package_dir, tmp_path / 'auth.json', output, **changes)
    sent = []
    def send(judge, payload):
        sent.append(payload)
        return transport(judge, payload)
    manifest = sb.run(package_dir, auth, output, transport=send, sleep=lambda s: None, today=TODAY)
    return manifest, output, sent


def test_full_fake_run_sequence_cost_stop_and_archive(package_dir, tmp_path):
    package, requests, _ = sb.validate_package(package_dir)
    manifest, output, sent = execute(package_dir, tmp_path, by_expectation(package))
    # 请求序列：先 U 臂 25 条，再 L 臂 15 条（不含条级），逐项等于 requests.jsonl
    assert [r['arm'] for r in requests] == ['U'] * 25 + ['L'] * 15
    assert [r['itemId'] for r in requests[:25]] == [i['id'] for i in package['items']]
    assert [r['itemId'] for r in requests[25:]] == [i['id'] for i in package['items'] if i['layer'] != '条级']
    assert sent == [r['payload'] for r in requests]
    events = events_in(output)
    assert [e['event'] for e in events] == ['start', 'end'] * 40 + ['stop']
    assert [e['requestId'] for e in events if e['event'] == 'start'] == [r['requestId'] for r in requests]
    # 计费：每次发送前预留，累计加预留不超预算；按 usage 计费且不超过预留；累计单调递增
    auth = si.load(output / 'authorization.json')
    total, budget = Decimal(0), Decimal(str(auth['budgetCny']))
    for start, end in zip(events[0:80:2], events[1:80:2]):
        reserved = Decimal(start['reservation']['costCny'])
        assert total + reserved <= budget
        assert end['charge']['basis'] == 'response_usage'
        assert Decimal(0) < Decimal(end['charge']['costCny']) <= reserved
        total += Decimal(end['charge']['costCny'])
    assert manifest['estimatedCostCny'] == str(total) == events[-1]['estimatedCostCny']
    # 停止条件：一个都不触发，正常完成
    assert manifest['stopReason'] == 'completed' and manifest['attempts'] == 40 == manifest['completedRecords']
    assert events[-1] == {**events[-1], 'event': 'stop', 'reason': 'completed', 'attempts': 40}
    # 归档完整：events / records / manifest / 题包 / 授权 / 源码都在，哈希清单覆盖且校验通过
    for name in ('events.jsonl', 'records.json', 'manifest.json', 'authorization.json', 'SHA256SUMS.txt',
                 'package/package.json', 'package/requests.jsonl', 'package/SHA256SUMS.txt'):
        assert (output / name).is_file(), name
    assert all((output / 'sources' / name).is_file() for name in sb.TOOL_FILES)
    si.verify_checksums(output)
    sb.validate_package(output / 'package')
    assert len(si.load(output / 'records.json')) == 40
    result = sb.analyze(output, tmp_path / 'analysis')
    assert result['cells']['U--款级']['detected'] == 10 and result['cells']['L--款级']['detected'] == 10
    assert result['cells']['U--条级']['detected'] == 10 and result['cells']['U--对照']['falsePositives'] == 0
    assert result['thresholds']['blindSpot']['met'] is False and result['established'] is False


def test_missing_usage_is_charged_at_reservation(tmp_path):
    package_dir = make_package(tmp_path / 'p')
    manifest, output, _ = execute(package_dir, tmp_path, lambda j, p: fake_response(usage=False))
    events = events_in(output)
    starts, ends = events[0:80:2], events[1:80:2]
    assert all(e['charge'] == {'basis': 'pre_request_reservation', **s['reservation']} for s, e in zip(starts, ends))
    assert Decimal(manifest['estimatedCostCny']) == sum(Decimal(s['reservation']['costCny']) for s in starts)
    sb.analyze(output, tmp_path / 'analysis')


def test_budget_guard_stops_before_sending(tmp_path):
    package_dir = make_package(tmp_path / 'p')
    manifest, output, sent = execute(package_dir, tmp_path, lambda j, p: fake_response(usage=False), budgetCny=0.03)
    assert manifest['stopReason'] == 'estimated_budget_guard' and 0 < len(sent) < 40
    assert manifest['attempts'] == len(sent)
    sb.analyze(output, tmp_path / 'analysis')


def test_unknown_model_halts_without_retry(tmp_path):
    package_dir = make_package(tmp_path / 'p')
    manifest, output, sent = execute(package_dir, tmp_path, lambda j, p: fake_response(model=None))
    assert manifest['stopReason'] == 'actual_model_unknown' and len(sent) == 1
    sb.analyze(output, tmp_path / 'analysis')


def test_transport_exception_is_recorded_once_and_run_continues(tmp_path):
    package_dir = make_package(tmp_path / 'p')
    calls = []
    def flaky(judge, payload):
        calls.append(payload)
        if len(calls) == 2:
            raise RuntimeError('secret-looking message must not be archived')
        return fake_response()
    manifest, output, sent = execute(package_dir, tmp_path, flaky)
    assert manifest['stopReason'] == 'completed' and len(sent) == 40
    failure = [e for e in events_in(output) if e['event'] == 'failure']
    assert len(failure) == 1 and failure[0]['error']['type'] == 'RuntimeError'
    assert 'secret-looking' not in (output / 'events.jsonl').read_text(encoding='utf-8')
    result = sb.analyze(output, tmp_path / 'analysis')
    assert result['records']['failed'] == [failure[0]['requestId']]


def test_interrupt_is_archived_and_replayable(tmp_path):
    package_dir = make_package(tmp_path / 'p')
    def interrupt(judge, payload):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        execute(package_dir, tmp_path, interrupt)
    output = tmp_path / 'run'
    assert si.load(output / 'manifest.json')['stopReason'] == 'interrupted'
    si.verify_checksums(output)
    sb.analyze(output, tmp_path / 'analysis')


@pytest.mark.parametrize('field, value, message', [
    ('requestsSha256', '0' * 64, '授权哈希不符'),
    ('maxRequests', 41, '请求上限必须为40'),
    ('budgetCny', 0, '预算必须为正'),
    ('authorizedAt', '2026-10-21', '单价须在授权当天核实'),
    ('allowPaidCalls', False, '未授权付费'),
])
def test_authorization_mismatch_refuses_before_any_call(tmp_path, field, value, message):
    package_dir = make_package(tmp_path / 'p')
    with pytest.raises(Exception, match=message):
        execute(package_dir, tmp_path, lambda j, p: pytest.fail('不应发送'), **{field: value})
    assert not (tmp_path / 'run').exists()


def test_run_day_must_match_price_check(tmp_path):
    package_dir = make_package(tmp_path / 'p')
    auth = authorize(package_dir, tmp_path / 'auth.json', tmp_path / 'run')
    with pytest.raises(Exception, match='运行当天须重新核价'):
        sb.run(package_dir, auth, tmp_path / 'run', transport=lambda j, p: pytest.fail('不应发送'), today='2026-10-23')


def test_tampered_package_is_refused(tmp_path):
    package_dir = make_package(tmp_path / 'p')
    path = package_dir / 'requests.jsonl'
    path.write_text(path.read_text(encoding='utf-8').replace('合成内容', '改过内容', 1), encoding='utf-8', newline='\n')
    with pytest.raises(Exception, match='归档校验失败'):
        sb.validate_package(package_dir)


def test_tampered_events_fail_replay(tmp_path):
    package_dir = make_package(tmp_path / 'p')
    _, output, _ = execute(package_dir, tmp_path, by_expectation(si.load(package_dir / "package.json")))
    events = events_in(output)
    events[1]['result']['verdict'] = 'supported'
    (output / 'events.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events),
                                         encoding='utf-8', newline='\n')
    si.checksums(output)
    with pytest.raises(Exception, match='结果/用量与原始响应不符'):
        sb.analyze(output, tmp_path / 'analysis')


def records_for(package, verdicts):
    return [{'requestId': f"{item['id']}--{arm}--deepseek", 'itemId': item['id'], 'arm': arm, 'judge': 'deepseek',
             'status': 'ok' if verdicts(item, arm) else 'call_error',
             'result': {'verdict': verdicts(item, arm)} if verdicts(item, arm) else None}
            for item, arm in sb.schedule(package)]


@pytest.mark.parametrize('u_hits, l_hits, t_hits, fp, established', [
    (3, 7, 7, 1, True),    # 四条恰好在界内
    (4, 7, 7, 1, False),   # 盲区重现 4/10 不算
    (3, 6, 7, 1, False),   # 干预 6/10 不算
    (3, 7, 6, 1, False),   # 条级 6/10 不算
    (3, 7, 7, 2, False),   # 对照误报 2/5 不算
])
def test_threshold_boundaries(tmp_path, u_hits, l_hits, t_hits, fp, established):
    package = si.load(make_package(tmp_path / 'p') / 'package.json')
    rank = {}
    for item in package['items']:
        rank[item['id']] = sum(1 for other in package['items'] if other['layer'] == item['layer']
                               and package['items'].index(other) < package['items'].index(item))
    def verdict(item, arm):
        limit = {'款级': u_hits if arm == 'U' else l_hits, '条级': t_hits, '对照': fp}[item['layer']]
        return 'contradicted' if rank[item['id']] < limit else 'supported'
    result = sb.summarize(package, records_for(package, verdict))
    assert result['established'] is established and result['robust'] is True


def test_unavailable_marks_conclusion_unstable(tmp_path):
    package = si.load(make_package(tmp_path / 'p') / 'package.json')
    first = next(i['id'] for i in package['items'] if i['layer'] == '款级')
    def verdict(item, arm):
        if item['id'] == first and arm == 'U':
            return None
        return 'contradicted' if item['layer'] != '对照' and (arm == 'L' or item['layer'] == '条级') else 'supported'
    result = sb.summarize(package, records_for(package, verdict))
    spot = result['thresholds']['blindSpot']
    assert spot['detected'] == 0 and spot['unavailable'] == 1 and spot['met'] is True and spot['robust'] is True
    assert result['pairedUtoL']['unavailable']['ids'] == [first]


def test_real_package_matches_rebuild_if_present():
    if not REAL_PACKAGE.is_dir():
        pytest.skip('work/ 下没有真实题包')
    package, requests, contexts = sb.validate_package(REAL_PACKAGE)
    assert len(requests) == 40 and sum(r['arm'] == 'L' for r in requests) == 15
    assert contexts['U'].encode('utf-8') == (sb.ROOT / package['source']['path']).read_bytes()


@pytest.mark.parametrize('during_sleep', [False, True])
def test_interrupt_reservation_and_between_requests(tmp_path, during_sleep):
    package_dir = make_package(tmp_path / 'p')
    output = tmp_path / 'run'
    auth = authorize(package_dir, tmp_path / 'auth.json', output)
    calls, sleeps = [], []
    def send(judge, payload):
        calls.append(payload)
        if not during_sleep and len(calls) == 3:
            raise KeyboardInterrupt
        return fake_response()
    def sleep(seconds):
        sleeps.append(seconds)
        if during_sleep and len(sleeps) == 2:
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        sb.run(package_dir, auth, output, transport=send, sleep=sleep, today=TODAY)
    manifest, records, events = si.load(output / 'manifest.json'), si.load(output / 'records.json'), events_in(output)
    assert manifest['stopReason'] == 'interrupted'
    assert manifest['completedRecords'] == 2
    assert manifest['attempts'] == len(calls) == (2 if during_sleep else 3)
    ends = [e for e in events if e['event'] in ('end', 'failure')]
    assert len(ends) == 2
    cost = sum(Decimal(e['charge']['costCny']) for e in ends)
    if during_sleep:
        assert manifest['attempts'] == manifest['completedRecords']
        assert len(records) == 2 and all(r['status'] != 'incomplete' for r in records)
    else:
        reserved = [e for e in events if e['event'] == 'start'][2]['reservation']
        assert len(records) == 3 and records[-1]['status'] == 'incomplete'
        assert records[-1]['charge'] == {'basis': 'pre_request_reservation', **reserved}
        cost += Decimal(reserved['costCny'])
    assert manifest['estimatedCostCny'] == events[-1]['estimatedCostCny'] == str(cost)
    result = sb.analyze(output, tmp_path / 'analysis')
    assert result['records']['failed'] == ([] if during_sleep else [records[-1]['requestId']])
    assert result['records']['notAttempted'] == (38 if during_sleep else 37)


@pytest.mark.parametrize('model', ['', 'deepseek-chat'])
def test_mismatched_actual_model_stops_and_replays(tmp_path, model):
    manifest, output, sent = execute(make_package(tmp_path / 'p'), tmp_path, lambda j, p: fake_response(model=model))
    assert manifest['stopReason'] == 'actual_model_unknown' and len(sent) == 1
    sb.analyze(output, tmp_path / 'analysis')


def test_budget_above_script_cap_refuses_before_send(tmp_path):
    with pytest.raises(Exception, match='预算超过脚本上限'):
        execute(make_package(tmp_path / 'p'), tmp_path, lambda j, p: pytest.fail('不应发送'), budgetCny=0.51)
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('tamper, message', [
    ('failure_error', '错误字段与原始响应不符'), ('end_error', '错误字段与原始响应不符'),
    ('extra_key', '记录字段不符'),
])
def test_replay_rejects_error_or_record_keys_after_rehash(tmp_path, tamper, message):
    calls = []
    def send(judge, payload):
        calls.append(payload)
        if tamper == 'failure_error' and len(calls) == 2:
            raise RuntimeError('offline failure')
        return fake_response()
    _, output, _ = execute(make_package(tmp_path / 'p'), tmp_path, send)
    events = events_in(output)
    target = next(e for e in events if e['event'] == ('failure' if tamper == 'failure_error' else 'end'))
    if tamper == 'failure_error':
        target['error'] = {'type': 'RuntimeError'}
    elif tamper == 'end_error':
        assert target['error'] is None
        target['error'] = {'type': 'x'}
    else:
        target['extra'] = True
    (output / 'events.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events),
                                         encoding='utf-8', newline='\n')
    si.checksums(output)
    with pytest.raises(Exception, match=message):
        sb.analyze(output, tmp_path / 'analysis')


def threshold_result(tmp_path, *, limits=None, unavailable=None, insufficient=None):
    package = si.load(make_package(tmp_path / 'p') / 'package.json')
    limits = {**{'blindSpot': 3, 'intervention': 7, 'layered': 7, 'usableU': 0, 'usableL': 0}, **(limits or {})}
    ranks = {item['id']: n for layer in ('款级', '条级', '对照')
             for n, item in enumerate(i for i in package['items'] if i['layer'] == layer)}
    def verdict(item, arm):
        name = next((k for k, spec in sb.THRESHOLDS.items() if spec['arm'] == arm and spec['layer'] == item['layer']), None)
        rank = ranks[item['id']]
        if unavailable == (name, rank):
            return None
        if insufficient == (name, rank):
            return 'insufficient'
        return 'contradicted' if name is not None and rank < limits[name] else 'supported'
    return sb.summarize(package, records_for(package, verdict))


@pytest.mark.parametrize('arm', ['U', 'L'])
def test_control_thresholds_are_independent(tmp_path, arm):
    result = threshold_result(tmp_path, limits={f'usable{arm}': 2})
    for name, threshold in result['thresholds'].items():
        assert threshold['met'] is (name != f'usable{arm}')
    assert result['established'] is False


@pytest.mark.parametrize('name', ['blindSpot', 'intervention', 'layered'])
@pytest.mark.parametrize('hits', [4, 5, 6])
def test_middle_detection_counts_do_not_establish(tmp_path, name, hits):
    result = threshold_result(tmp_path, limits={name: hits})
    assert result['thresholds'][name]['met'] is False
    assert result['thresholds'][name]['robust'] is True
    assert result['established'] is False


@pytest.mark.parametrize('name, hits, met, robust', [
    ('blindSpot', 3, False, False), ('blindSpot', 2, True, True),
    ('intervention', 6, False, False), ('usableU', 1, False, False),
])
def test_unavailable_crosses_threshold(tmp_path, name, hits, met, robust):
    result = threshold_result(tmp_path, limits={name: hits}, unavailable=(name, hits))
    threshold = result['thresholds'][name]
    assert threshold['unavailable'] == 1
    assert threshold['met'] is met and threshold['robust'] is robust


@pytest.mark.parametrize('name, hits, field, expected', [
    ('blindSpot', 2, 'detected', 3), ('usableU', 0, 'falsePositives', 1),
])
def test_insufficient_counts_as_detection_or_false_positive(tmp_path, name, hits, field, expected):
    result = threshold_result(tmp_path, limits={name: hits}, insufficient=(name, hits))
    assert result['thresholds'][name][field] == expected
