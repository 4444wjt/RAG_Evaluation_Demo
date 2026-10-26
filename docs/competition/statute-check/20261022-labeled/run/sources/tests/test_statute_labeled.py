"""仅离线假 transport；不读取 .env，不调用提供方。"""
from copy import deepcopy
from decimal import Decimal
import builtins
import json
from pathlib import Path
import socket

import pytest

from backend.core import statute_check as sc
from tools.competition import statute_labeled as sl

si = sl.si


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
    def guarded_open(file, *args, **kwargs):
        check(file)
        return original_open(file, *args, **kwargs)
    def guarded_path_open(path, *args, **kwargs):
        check(path)
        return path_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(Path, 'open', guarded_path_open)


@pytest.fixture
def package():
    return sl.build_package()


@pytest.fixture
def authorized(tmp_path):
    prep, output = tmp_path / 'prep', tmp_path / 'run'
    result = sl.prepare(prep)
    auth = result['authorization']
    auth.update(status='authorized', allowPaidCalls=True, authorizedBy='offline-test-only',
                authorizedAt='2026-10-22', note='仅临时假运行，非真实付费授权',
                budgetCny=100, outputDirectory=str(output))
    auth_path = tmp_path / 'test-authorization.json'
    si.write_json(auth_path, auth)
    return prep / 'package.json', auth_path, output


def fake_response(verdict='supported', *, model='offline-fake', usage=True):
    response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
        {'verdict': verdict, 'reason': '离线假响应', 'citations': []}, ensure_ascii=False)}}]}
    if model is not None:
        response['model'] = model
    if usage:
        response['usage'] = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
    return response


def execute(authorized, transport, sleep=lambda seconds: None):
    return sl.run(*authorized, transport=transport, sleep=sleep)


def events_in(directory):
    return [json.loads(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]


def rewrite_events(directory, events):
    si.write_text(directory / 'events.jsonl', ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events))


@pytest.mark.parametrize('newline', [None, '\n', '\r\n', '\r'])
def test_label_byte_roundtrip_count_and_line_numbering(newline):
    text = sl.source_text()
    if newline is not None:
        text = newline.join(text.splitlines()) + newline
    labeled, edits = sl.label_source(text)
    restored = labeled
    for edit in reversed(edits):
        offset, label = edit['offset'], edit['text']
        assert restored[offset:offset + len(label)] == label
        restored = restored[:offset] + restored[offset + len(label):]
    assert restored.encode('utf-8') == text.encode('utf-8')
    paragraphs = [p for p in sc.parse_provisions(text) if p.item is None]
    assert len(edits) == len(paragraphs)
    assert [(e['article'], e['paragraph']) for e in edits] == [(p.article, p.paragraph) for p in paragraphs]
    source_lines, labeled_lines = text.splitlines(keepends=True), labeled.splitlines(keepends=True)
    rows = sl.provision_lines(text)
    assert len(source_lines) == len(labeled_lines)
    for row in rows:
        loc, n = row['location'], row['line']
        if loc is None or loc[2] is not None:
            assert source_lines[n] == labeled_lines[n]
        else:
            a, p, _ = loc
            if row['header']:
                position = source_lines[n].index('条') + 1
                assert labeled_lines[n] == source_lines[n][:position] + '第一款' + source_lines[n][position:]
            else:
                assert labeled_lines[n] == f'第{sc.cn_label(a)}条第{sc.cn_label(p)}款　' + source_lines[n]


def test_normalize_only_for_numbering_and_preserve_raw_bytes():
    text = '标题\r\n\r\n 第５条　首款。\r\n第二款５０万元。\r\n（一）项目。\r\n\r\n尾注'
    labeled, _ = sl.label_source(text)
    assert labeled == '标题\r\n\r\n 第５条第一款　首款。\r\n第五条第二款　第二款５０万元。\r\n（一）项目。\r\n\r\n尾注'


def test_slices_exact_three_examples_and_all_locations(package):
    text = sl.source_text()
    slices = sl.build_slices(package['items'], text)
    lines = text.splitlines()
    fifth = lines.index(next(x for x in lines if x.startswith('第五条')))
    nineteenth = lines.index(next(x for x in lines if x.startswith('第十九条')))
    assert len(slices) == 31
    assert slices['control_pinpoint-S03'] == lines[fifth]
    assert slices['paragraph_existing-S03-P2'] == lines[fifth + 1]
    assert slices['control_pinpoint-S26'] == '\n'.join([lines[nineteenth], lines[nineteenth + 3]])
    assert slices['paragraph_existing-S27-P1'] == '\n'.join(lines[nineteenth:nineteenth + 5])
    provisions = {(p.article, p.paragraph, p.item) for p in sc.parse_provisions(text)}
    for item in package['items']:
        if item['type'] in sl.SLICE_TYPES:
            assert sc._ref_value(sc.REF.search(sc.normalize(item['statement']))) in provisions
    for statement in ('没有引用', '第五条', '第五条第九款', '第十九条第一款第九项'):
        changed = deepcopy(package['items'])
        next(i for i in changed if i['type'] in sl.SLICE_TYPES)['statement'] = statement
        with pytest.raises(ValueError):
            sl.build_slices(changed, text)


def test_package_fixed_material_judges_schedule_and_verbatim_preregistration(package):
    archived = si.load(sl.ROOT / sl.REFERENCE / 'package.json')
    assert package['judges'] == archived['judges']
    assert package['items'] == si.load(sl.ROOT / sl.BASE)['items']
    assert package['sourceFiles'][sl.BASE] == sl.BASE_SHA256
    assert package['sourceFiles'][si.SOURCE] == sl.SOURCE_SHA256
    task = (sl.ROOT / 'work/competition/codex-tasks/labeled-runner-20261022.md').read_text(encoding='utf-8')
    expected = task.split('### 9. 预注册口径', 1)[1].split('\n', 1)[1].split('\n\n### 10.', 1)[0]
    assert package['preregistration'] == expected
    jobs = sl.schedule(package)
    expected = [(i['id'], 'L', j) for i in package['items'] for j in ('deepseek', 'qwen72b')]
    expected += [(i['id'], 'S', 'qwen72b') for i in package['items'] if i['type'] in sl.SLICE_TYPES]
    assert len(jobs) == len({j['requestId'] for j in jobs}) == 163
    assert [(j['itemId'], j['condition'], j['judge']) for j in jobs] == expected
    assert all(j['requestId'] == '--'.join([j['itemId'], j['condition'], j['judge']]) for j in jobs)


def test_messages_match_every_archived_j2_request_except_context(package):
    starts = {(e['itemId'], e['judge']): e['request'] for e in events_in(sl.ROOT / sl.REFERENCE)
              if e['event'] == 'start' and e.get('condition') == 'J2'}
    items = {i['id']: i for i in package['items']}
    for job in sl.schedule(package):
        new = sl.payload_for(package, items[job['itemId']], job)
        old = deepcopy(starts[job['itemId'], job['judge']])
        new_data, old_data = [json.loads(p['messages'][1]['content']) for p in (new, old)]
        old_data['contexts'] = new_data['contexts']
        old['messages'][1]['content'] = json.dumps(old_data, ensure_ascii=False, separators=(',', ':'))
        assert new == old


def test_prepare_draft_and_expected_cost_formula(authorized):
    package_path, _, _ = authorized
    prep = package_path.parent
    package, auth, estimates = [si.load(prep / name) for name in
                               ('package.json', 'authorization.template.json', 'estimates.json')]
    assert auth['status'] == 'draft' and auth['allowPaidCalls'] is False
    assert auth['budgetCny'] is auth['note'] is None
    assert 'acceptNoHardBillingCap' not in auth
    assert auth['prices'] == si.load(sl.ROOT / sl.REFERENCE / 'authorization.json')['prices']
    assert auth['labeledSha256'] == si.sha256(prep / 'labeled.txt')
    assert auth['slicesSha256'] == si.digest(si.load(prep / 'slices.json'))
    assert estimates['requests'] == 163
    assert estimates['byCondition']['L']['requests'] == 132
    assert estimates['byCondition']['S']['requests'] == 31
    starts, totals = {}, {('L', 'deepseek'): Decimal(0), ('L', 'qwen72b'): Decimal(0), ('S', 'qwen72b'): Decimal(0)}
    items = {i['id']: i for i in package['items']}
    for event in events_in(sl.ROOT / sl.REFERENCE):
        if event.get('condition') not in ('J1', 'J2'):
            continue
        if event['event'] == 'start':
            starts[event['requestId']] = event
        elif event['event'] in ('end', 'failure'):
            condition = 'L' if event['condition'] == 'J2' else 'S'
            if condition == 'S' and (event['judge'] != 'qwen72b' or items[event['itemId']]['type'] not in sl.SLICE_TYPES):
                continue
            old = starts[event['requestId']]['request']['messages']
            new = sl.prompt_messages(items[event['itemId']], condition)
            totals[condition, event['judge']] += (Decimal(event['charge']['costCny']) *
                Decimal(sum(len(m['content'].encode('utf-8')) for m in new)) /
                Decimal(sum(len(m['content'].encode('utf-8')) for m in old)))
    for (condition, judge), value in totals.items():
        assert Decimal(estimates['byCondition'][condition]['judges'][judge]['expectedCostCny']) == value
    with pytest.raises(ValueError, match='空目录'):
        sl.prepare(prep)


def test_budget_guard_refuses_before_sending(authorized):
    _, auth_path, output = authorized
    auth = si.load(auth_path)
    auth['budgetCny'] = 0.000001
    si.write_json(auth_path, auth)
    def unexpected(*args):
        pytest.fail('预算不足不能发送')
    manifest = execute(authorized, unexpected)
    assert manifest['attempts'] == 0 and manifest['stopReason'] == 'estimated_budget_guard'
    assert sl.analyze(output, output.parent / 'analysis')['records']['notAttempted'] == 163


@pytest.mark.parametrize('failure', ['http', 'exception', 'none', 'nondict', 'parse', 'malformed_transport'])
def test_call_failures_continue_and_reserve_without_usage(authorized, failure):
    calls, sleeps = [], []
    def send(*args):
        calls.append(args)
        if len(calls) > 1:
            return fake_response()
        if failure == 'http':
            return {'_transport': {'error': 'http_error', 'httpStatus': 429, 'body': 'rate limited'}}
        if failure == 'exception':
            raise RuntimeError('do not archive this exception message')
        if failure == 'none':
            return None
        if failure == 'nondict':
            return []
        if failure == 'malformed_transport':
            return {'_transport': None}
        response = fake_response(usage=False)
        response['choices'][0]['message']['content'] = 'invalid json'
        return response
    manifest = execute(authorized, send, sleeps.append)
    assert manifest['attempts'] == 163 and manifest['stopReason'] == 'request_limit'
    assert sleeps == [1.0] * 162
    output = authorized[2]
    events = events_in(output)
    assert events[1]['event'] == 'failure'
    assert events[1]['charge'] == {'basis': 'pre_request_reservation', **events[0]['reservation']}
    analysis = sl.analyze(output, output.parent / 'analysis')
    assert analysis['records'] == {'total': 163, 'ok': 162, 'failed': 1, 'notAttempted': 0}
    assert analysis['conditions']['L']['deepseek']['unavailableIds'] == ['control-S03']
    assert analysis['conditions']['L']['deepseek']['note'] == '含不可用，结论可能变化'


@pytest.mark.parametrize('kind', ['missing_model', 'normal_empty_dict', 'over_reservation', 'no_usage'])
def test_model_and_reservation_stops_and_missing_usage_continues(authorized, kind):
    def send(*args):
        if kind == 'normal_empty_dict':
            return {}
        response = fake_response(model=None if kind == 'missing_model' else 'offline-fake', usage=kind != 'no_usage')
        if kind == 'over_reservation':
            response['usage']['completion_tokens'] = 1_000_000
            response['usage']['total_tokens'] = 1_000_010
        return response
    manifest = execute(authorized, send)
    expected = {'missing_model': 'actual_model_unknown', 'normal_empty_dict': 'actual_model_unknown',
                'over_reservation': 'reservation_exceeded', 'no_usage': 'request_limit'}[kind]
    assert manifest['stopReason'] == expected
    assert manifest['attempts'] == (163 if kind == 'no_usage' else 1)
    sl.analyze(authorized[2], authorized[2].parent / 'analysis')


def test_interrupt_preserves_incomplete_event(authorized):
    def send(*args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execute(authorized, send)
    analysis = sl.analyze(authorized[2], authorized[2].parent / 'analysis')
    assert analysis['records'] == {'total': 1, 'ok': 0, 'failed': 1, 'notAttempted': 162}
    assert analysis['failures'][0]['status'] == 'incomplete'


@pytest.mark.parametrize('tamper', ['request', 'result', 'reservation', 'charge', 'stop', 'records',
                                  'schedule', 'manifest', 'package', 'source', 'labeled', 'slices', 'checksum'])
def test_analyze_rejects_tampering_even_with_rehashed_manifest(authorized, tamper):
    execute(authorized, lambda *args: fake_response(model=None))
    output = authorized[2]
    if tamper in ('request', 'result', 'reservation', 'charge', 'stop'):
        events = events_in(output)
        if tamper == 'request':
            events[0]['request']['messages'][1]['content'] = '{}'
        elif tamper == 'result':
            events[1]['result']['verdict'] = 'contradicted'
        elif tamper == 'reservation':
            events[0]['reservation']['costCny'] = '0'
        elif tamper == 'charge':
            events[1]['charge']['costCny'] = '0'
        else:
            events[-1]['reason'] = 'request_limit'
        rewrite_events(output, events)
    elif tamper in ('records', 'schedule'):
        si.write_json(output / f'{tamper}.json', [])
    elif tamper == 'manifest':
        manifest = si.load(output / 'manifest.json')
        manifest['attempts'] = 0
        si.write_json(output / 'manifest.json', manifest)
    elif tamper == 'package':
        package = si.load(output / 'package.json')
        package['preregistration'] = 'changed'
        si.write_json(output / 'package.json', package)
    elif tamper == 'source':
        path = output / 'sources/tools/competition/statute_labeled.py'
        path.write_bytes(path.read_bytes() + b'\n# changed\n')
    elif tamper in ('labeled', 'slices'):
        path = output / ('inputs/labeled.txt' if tamper == 'labeled' else 'inputs/slices.json')
        path.write_bytes(b'{}')
    else:
        (output / 'prompt.txt').write_bytes(b'changed')
    if tamper != 'checksum':
        si.checksums(output)
    target = output.parent / 'analysis'
    with pytest.raises(ValueError):
        sl.analyze(output, target)
    assert not target.exists()


@pytest.mark.parametrize('field,value', [
    ('status', 'draft'), ('allowPaidCalls', False), ('authorizedBy', ''), ('authorizedAt', '20261022'),
    ('note', ''), ('packageSha256', 'bad'), ('configurationSha256', 'bad'), ('promptSha256', 'bad'),
    ('labeledSha256', 'bad'), ('slicesSha256', 'bad'), ('toolFiles', {}), ('maxRequests', 162),
    ('maxRetries', 1), ('budgetCny', 0), ('budgetCny', True), ('budgetCny', float('inf')),
    ('prices', {}), ('outputDirectory', 'different-directory')])
def test_authorization_rejects_invalid_fields(authorized, field, value):
    path, auth_path, output = authorized
    auth = si.load(auth_path)
    auth[field] = value
    with pytest.raises(ValueError):
        sl.validate_authorization(si.load(path), path, auth, output)


def test_authorization_analyze_skips_only_output_binding(authorized):
    path, auth_path, _ = authorized
    auth = si.load(auth_path)
    auth.pop('outputDirectory')
    sl.validate_authorization(si.load(path), path, auth, None)


@pytest.mark.parametrize('d,a,fp,result', [
    (15, 10, 4, '成立'), (14, 10, 4, '部分成立'), (15, 9, 4, '部分成立'),
    (5, 13, 0, '推翻'), (6, 10, 4, '部分成立'), (15, 10, 5, '部分成立'),
    (15, 10, 7, '部分成立'), (15, 10, 8, '推翻')])
def test_claim1_boundaries(d, a, fp, result):
    assert sl.decide_claim1(d, a, fp) == result


@pytest.mark.parametrize('count,result', [(5, '与主张一致'), (6, '部分'), (9, '部分'), (10, '推翻')])
def test_claim3_label_boundaries(count, result):
    assert sl.decide_claim3_l(count) == result


@pytest.mark.parametrize('count,fp,result', [(15, 3, '成立'), (14, 3, '部分成立'),
    (15, 4, '部分成立'), (9, 0, '推翻'), (10, 0, '部分成立')])
def test_claim3_slice_boundaries(count, fp, result):
    assert sl.decide_claim3_s(count, fp) == result


def test_full_replay_pairs_types_s26_and_repeatable_analysis(authorized):
    package = si.load(authorized[0])
    jobs = iter(sl.schedule(package))
    reference = si.load(sl.ROOT / sl.REFERENCE / 'records.json')
    def send(*args):
        job = next(jobs)
        return fake_response('insufficient' if job['itemId'].startswith(('paragraph_', 'item_')) else 'supported')
    execute(authorized, send)
    output = authorized[2]
    result = sl.analyze(output, output.parent / 'analysis')
    assert result == sl.analyze(output, output.parent / 'again')
    assert result['records'] == {'total': 163, 'ok': 163, 'failed': 0, 'notAttempted': 0}
    assert result['typeCounts']['item_existing'] + result['typeCounts']['item_absent'] == 4
    assert set(result['paired']) == {'L--J2--deepseek', 'L--J2--qwen72b', 'S--J1--qwen72b', 'S--J2--qwen72b'}
    lookup = {(r['itemId'], r['condition'], r['judge']): r for r in reference}
    for key, comparison in result['paired'].items():
        condition, baseline, judge = key.split('--')
        for name, row in comparison.items():
            assert row['count'] == len(row['ids'])
            for identity in row['ids']:
                original = lookup[identity, baseline, judge]
                if name == 'unavailable':
                    assert original['status'] != 'ok'
    assert result['conditions']['L']['deepseek']['claim1']['result'] == '成立'
    assert result['conditions']['L']['qwen72b']['claim3L']['result'] == '推翻'
    assert result['conditions']['S']['qwen72b']['claim3S']['result'] == '成立'
    assert result['withoutS26']['typeCounts'].get('item_existing', 0) == 0
    for row in result['withoutS26']['paired'].values():
        assert all('S26' not in identity for values in row.values() for identity in values['ids'])
    assert result['referenceHashes'] == {p: si.sha256(output / 'inputs' / p) for p in sl.INPUT_FILES if p.startswith(sl.REFERENCE)}
    si.verify_checksums(output)
    si.verify_checksums(output.parent / 'analysis')
