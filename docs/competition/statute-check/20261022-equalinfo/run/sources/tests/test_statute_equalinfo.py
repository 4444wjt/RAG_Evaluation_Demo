"""等信息运行器离线契约；所有授权仅是pytest临时副本，禁止网络和真实transport。"""
import asyncio
from copy import deepcopy
from datetime import date
import json
from decimal import Decimal
from pathlib import Path
import shutil
import socket

import pytest

from tools.competition import statute_equalinfo as se
from tools.competition import statute_injection as si


@pytest.fixture(autouse=True, scope='module')
def offline_only():
    # Windows的asyncio自唤醒socketpair使用本机回环；先建循环，再全面封锁socket。
    # 与test_api相同，应用及全部Ragas调用都在封锁生效期间运行。
    loop = asyncio.new_event_loop()
    def forbidden(*args, **kwargs):
        raise AssertionError('离线测试禁止网络和真实transport')
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(si, 'make_transport', forbidden)
        monkeypatch.setattr(socket, 'create_connection', forbidden)
        monkeypatch.setattr(socket.socket, 'connect', forbidden)
        monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
        monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
        monkeypatch.setattr(asyncio, 'run', loop.run_until_complete)
        try:
            yield
        finally:
            loop.close()


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    output = tmp_path_factory.mktemp('equalinfo') / 'prep'
    return output, se.prepare(output)


def snapshot(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob('*') if p.is_file()}


def response(content, *, usage=True, model=True, tokens=10):
    result = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
    if usage:
        result['usage'] = {'prompt_tokens': tokens, 'completion_tokens': 5, 'total_tokens': tokens + 5}
    if model:
        result['model'] = 'offline-test-model'
    return result


def statements(text='第五条第一款规定限额为50万元。'):
    return json.dumps([{'sentence_index': 0, 'simpler_statements': [text]}], ensure_ascii=False)


def nli(text='第五条第一款规定限额为50万元。', verdict=1):
    return json.dumps([{'statement': text, 'reason': '离线假响应', 'verdict': verdict}], ensure_ascii=False)


def judge_response(verdict='supported'):
    return response(json.dumps({'verdict': verdict, 'reason': '离线假响应', 'citations': []}, ensure_ascii=False))


class FakeTransport:
    def __init__(self, package):
        metric = se.ChineseFaithfulness()
        self.statements_by_prompt = {metric._create_statements_prompt(se.ragas_row(i, 'R1')).to_string(): i['statement']
                                     for i in package['items']}
        self.calls = []

    def __call__(self, judge, payload):
        self.calls.append((judge, deepcopy(payload)))
        if len(payload['messages']) == 2:
            return judge_response()
        text = payload['messages'][0]['content']
        step = se.step_for(text)
        if step == 'statements':
            return response(statements(self.statements_by_prompt[text]))
        if step == 'nli':
            return response(nli())
        raise AssertionError('本假transport未计划格式修复')


def authorized(prepared, tmp_path, **changes):
    """仅临时目录中的离线测试副本；不得用于真实运行。"""
    prep, result = prepared
    auth = deepcopy(result['authorization'])
    output = tmp_path / 'run'
    day = date.today().isoformat()
    auth.update(status='authorized', authorizedBy='OFFLINE-TEST-ONLY', authorizedAt=day,
                allowPaidCalls=True, acceptNoHardBillingCap=True, budgetCny=1000,
                outputDirectory=str(output), phase='full', maxRequests=1320, haltOn=[])
    for price in auth['prices'].values():
        price.update(checkedAt=day, source='OFFLINE-TEST-ONLY')
    auth.update(changes)
    path = tmp_path / 'authorization.test.json'
    si.write_json(path, auth)
    return prep / 'package.json', path, output, auth


def run_fake(prepared, tmp_path, *, transport=None, **changes):
    package_path, auth_path, output, auth = authorized(prepared, tmp_path, **changes)
    fake = transport if transport is not None else FakeTransport(prepared[1]['package'])
    sleeps = []
    manifest = se.run(package_path, auth_path, output, transport=fake, sleep=sleeps.append)
    return output, manifest, fake, sleeps, auth


def small_package(package):
    # 仅核心调度/守卫单测缩小items，不绕过run/prepare/analyze的66条校验。
    return {**package, 'items': package['items'][:1]}


def small_auth(prepared, **changes):
    auth = deepcopy(prepared[1]['authorization'])
    auth.update(budgetCny=1000, phase='full', maxRequests=1320, haltOn=[])
    auth.update(changes)
    return auth


def test_prepare_reproducible_and_only_draft(prepared, tmp_path, capsys):
    second = tmp_path / 'empty'
    second.mkdir()
    se.prepare(second)
    assert snapshot(prepared[0]) == snapshot(second)
    si.verify_checksums(second)
    auth = si.load(second / 'authorization.draft.json')
    assert auth['status'] == 'draft'
    assert not auth['allowPaidCalls'] and not auth['acceptNoHardBillingCap']
    assert all(auth[k] is None for k in ('phase', 'maxRequests', 'budgetCny', 'outputDirectory', 'authorizedBy', 'authorizedAt'))
    assert all(p['checkedAt'] is None for p in auth['prices'].values())
    assert auth['haltOn'] == [] and auth['maxRetries'] == 0
    assert set(auth['promptHashes']) == {'original'}
    assert auth['promptSetSha256'] == si.digest(se.PROMPTS)
    assert not (second / 'prompt.pinpoint.txt').exists()
    assert set(auth['toolFiles']) == set(se.TOOL_FILES)
    assert set(auth['preparationToolFiles']) == set(se.PREPARATION_TOOLS)
    before = snapshot(second)
    with pytest.raises(ValueError, match='空目录'):
        se.prepare(second)
    assert snapshot(second) == before
    cost = prepared[1]['estimates']['phases']['full']
    assert 0 < cost['totalCostCnyEstimate'] < float(cost['reservationHardUpperCny'])
    assert cost['historicalRagas']['steps']['statements']['requests'] > 0
    assert cost['historicalRagas']['steps']['nli']['requests'] > 0
    assert cost['judges']['qwen72b']['byCondition']['R2']['qwenTokenRatioAssumption']
    assert cost['judges']['deepseek']['byCondition']['J1']['sameDayActualUsageReference']['requests'] == 66


def test_base_and_items_unchanged_and_all_metadata_locked(prepared, tmp_path):
    package = prepared[1]['package']
    base = si.load(se.ROOT / se.BASE)
    assert package['items'] == base['items']
    assert si.sha256(se.ROOT / se.BASE) == se.BASE_SHA256
    assert package['ragas']['version'] == '0.1.21'
    assert package['ragas']['max_retries'] == 1 and package['ragas']['_reproducibility'] == 1
    assert package['ragas']['RunConfig'] == {'max_retries': 1, 'max_workers': 1, 'timeout': 90}
    assert package['ragas']['temperature'] == 0 and package['ragas']['maxTokens'] == 2048
    for field in ('statement_prompt', 'nli_statements_message', 'FIX_OUTPUT_FORMAT'):
        text = (prepared[0] / f'prompt.ragas.{field}.txt').read_text(encoding='utf-8')
        assert si.digest(text) == package['ragas']['promptHashes'][field]
    for field in ('analysisPlan', 'conditions', 'labelPattern', 'throttleSeconds', 'ragas', 'fullContext', 'sourceFiles'):
        changed = deepcopy(package)
        changed[field] = None
        with pytest.raises(ValueError):
            se.validate_package(changed)
    changed = deepcopy(package)
    changed['items'][0]['review']['note'] += '改'
    with pytest.raises(ValueError):
        se.validate_package(changed)
    copy = tmp_path / 'root' / se.BASE
    copy.parent.mkdir(parents=True)
    copy.write_bytes((se.ROOT / se.BASE).read_bytes().replace('Ll'.encode(), 'Lx'.encode(), 1))
    with pytest.raises(ValueError, match='基础材料'):
        se.prepare(tmp_path / 'rejected', tmp_path / 'root')
    assert not (tmp_path / 'rejected').exists()


def test_J1_model_change_and_full_context_is_one_untouched_piece(prepared):
    package = prepared[1]['package']
    old_events = se.ROOT / Path(se.OLD_RECORDS).with_name('events.jsonl')
    starts = [json.loads(line) for line in old_events.read_text(encoding='utf-8').splitlines() if json.loads(line)['event'] == 'start']
    items = {i['id']: i for i in package['items']}
    for event in starts:
        if event['prompt'] != 'original':
            continue
        condition = 'J1'
        job = {'judge': event['judge'], 'condition': condition}
        actual = se.payload_for(package, job, se.prompt_messages(items[event['itemId']], condition))
        if event['judge'] == 'deepseek':
            # 与10-21只差请求名与显式关闭思考两处。
            assert actual['model'] == 'deepseek-flash' and actual['thinking'] == {'type': 'disabled'}
            assert 'thinking' not in event['request']
            changed = ('model', 'thinking')
            assert {k: v for k, v in actual.items() if k not in changed} == {k: v for k, v in event['request'].items() if k not in changed}
        else:
            assert actual == event['request']
    full = (se.ROOT / si.SOURCE).read_text(encoding='utf-8')
    for item in package['items']:
        for condition in ('J2',):
            payload = json.loads(se.prompt_messages(item, condition)[1]['content'])
            assert set(payload) == {'question', 'statement', 'contexts'}
            assert payload['contexts'] == [{'contextIndex': 0, 'text': full}]
        assert se.ragas_row(item, 'R1') == {'question': item['question'], 'answer': item['statement'], 'contexts': item['contexts']}
        assert se.ragas_row(item, 'R2')['contexts'] == [full]


@pytest.mark.parametrize('field,value', [
    ('status', 'draft'), ('allowPaidCalls', False), ('acceptNoHardBillingCap', False),
    ('authorizedBy', ''), ('authorizedAt', None), ('packageSha256', 'bad'),
    ('configurationSha256', 'bad'), ('promptSha256', 'bad'), ('promptSetSha256', 'bad'),
    ('promptHashes', {}), ('ragasPromptHashes', {}), ('toolFiles', {}), ('preparationToolFiles', {}),
    ('maxRequests', 1319), ('maxRequests', True), ('maxRetries', 1), ('phase', None),
    ('phase', 'invalid'), ('haltOn', None), ('haltOn', ['unknown']), ('haltOn', ['actual_model_unknown'] * 2),
    ('haltOn', [1]), ('costPolicySha256', 'bad'), ('budgetCny', 0), ('budgetCny', float('inf')),
    ('outputDirectory', 'elsewhere'), ('prices', {}), ('estimates', {}),
])
def test_invalid_authorization_rejected_before_output(prepared, tmp_path, field, value):
    package_path, auth_path, output, auth = authorized(prepared, tmp_path)
    auth[field] = value
    # allow_nan=False的写入器会先拒绝inf；直接测试授权校验。
    with pytest.raises(ValueError):
        se.validate_authorization(prepared[1]['package'], package_path, auth, output)
    assert not output.exists()
    if not (isinstance(value, float) and not se.math.isfinite(value)):
        si.write_json(auth_path, auth)
        with pytest.raises(ValueError):
            se.run(package_path, auth_path, output, transport=lambda *_: pytest.fail('不应发送'))
        assert not output.exists()


def test_missing_authorization_fields_and_invalid_prices(prepared, tmp_path):
    path, _, output, original = authorized(prepared, tmp_path)
    for field in original:
        if field in ('note',):
            continue
        auth = deepcopy(original)
        del auth[field]
        with pytest.raises(ValueError):
            se.validate_authorization(prepared[1]['package'], path, auth, output)
    for field, value in [('source', ''), ('checkedAt', '2026-02-30'), ('checkedAt', '2026-10-21'),
                         ('inputCnyPerMillion', -1), ('outputCnyPerMillion', True)]:
        auth = deepcopy(original)
        auth['prices']['deepseek'][field] = value
        with pytest.raises(ValueError):
            se.validate_authorization(prepared[1]['package'], path, auth, output)


def test_schedule_is_item_condition_judge_blocked(prepared):
    package = prepared[1]['package']
    jobs = se.schedule(package)
    assert len(jobs) == 66 * 4 * 2
    assert [(j['condition'], j['judge']) for j in jobs[:8]] == [(c, j) for c in se.CONDITIONS for j in se.JUDGES]
    assert len({j['itemId'] for j in jobs[:8]}) == 1
    assert jobs[8]['itemId'] == package['items'][1]['id']


def test_pilot_selection_and_phase_authorization(prepared, tmp_path):
    package = prepared[1]['package']
    expected = {next(i['id'] for i in package['items'] if i['type'] == kind) for kind in se.sp.TYPES}
    expected.add([i['id'] for i in package['items'] if i['type'] == 'paragraph_existing'][1])
    assert len(expected) == 8
    assert package['pilot']['ids'] == [i['id'] for i in package['items'] if i['id'] in expected]
    assert se.MAX_REQUESTS == {'pilot': 160, 'full': 1320}
    for phase, count in [('pilot', 8), ('full', 66)]:
        jobs = se.schedule(package, phase)
        assert len(jobs) == count * 8
        assert [(j['condition'], j['judge']) for j in jobs[:8]] == [(c, j) for c in se.CONDITIONS for j in se.JUDGES]
        path, _, output, auth = authorized(prepared, tmp_path, phase=phase, maxRequests=se.MAX_REQUESTS[phase])
        se.validate_authorization(package, path, auth, output)
        auth['maxRequests'] = se.MAX_REQUESTS['full' if phase == 'pilot' else 'pilot']
        with pytest.raises(ValueError, match='160/1320'):
            se.validate_authorization(package, path, auth, output)


def test_confirmed_work_copy_is_optional_but_must_match_and_reviews_are_confirmed(prepared, tmp_path):
    package = prepared[1]['package']
    base = si.load(se.ROOT / se.BASE)
    assert len([base['review'], *(i['review'] for i in base['items'])]) == 67
    assert all(r['status'] == 'confirmed' for r in [base['review'], *(i['review'] for i in base['items'])])
    cfg = deepcopy(si.judge_config())
    cfg['deepseek']['model'] = 'deepseek-flash'
    cfg['deepseek']['thinking'] = {'type': 'disabled'}
    assert package['judges'] == cfg == se.JUDGES
    # 公开克隆仅复制sourceFiles；不依赖work路径。
    for path in package['sourceFiles']:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(se.ROOT / path, target)
    assert se.build_package(tmp_path) == package
    work = tmp_path / se.CONFIRMED_BASE
    work.parent.mkdir(parents=True)
    shutil.copyfile(se.ROOT / se.BASE, work)
    assert se.build_package(tmp_path) == package
    work.write_bytes(work.read_bytes() + b' ')
    with pytest.raises(ValueError, match='字节不同'):
        se.build_package(tmp_path)


def test_version_checked_for_run_and_analyze(prepared, completed_run, tmp_path, monkeypatch):
    original = se.version
    monkeypatch.setattr(se, 'version', lambda name: '0.1.22' if name == 'ragas' else original(name))
    path, auth_path, output, _ = authorized(prepared, tmp_path)
    with pytest.raises(ValueError, match='0.1.21'):
        se.run(path, auth_path, output, transport=lambda *_: pytest.fail('不应发送'))
    with pytest.raises(ValueError, match='0.1.21'):
        se.analyze(completed_run[0], tmp_path / 'analysis')
    assert not output.exists()


def test_cost_estimates_cover_both_phases(prepared, capsys):
    for phase, items in [('pilot', 8), ('full', 66)]:
        cost = prepared[1]['estimates']['phases'][phase]
        assert cost['items'] == items and cost['maxRequests'] == se.MAX_REQUESTS[phase]
        assert all(set(c['byCondition']) == set(se.CONDITIONS) for c in cost['judges'].values())
        assert cost['judges']['deepseek']['byCondition']['J1']['requests'] == items
        assert cost['judges']['deepseek']['byCondition']['R1']['requestsUpper'] == items * 4
        assert cost['pricesAsOf'] == '2026-10-21'


def test_imputation_hierarchy_decimal_means_and_history_excludes_imputed(prepared):
    package = prepared[1]['package']
    auth = small_auth(prepared)
    events = []
    sequence = [response('ok', usage=False), response('ok', tokens=1), response('ok', usage=False),
                response('ok', tokens=2), response('ok', usage=False), response('ok', usage=False)]
    guard = se.Guard(package, auth, lambda *_: sequence.pop(0), events.append, lambda _: None)
    job = se.schedule(package)[0]
    for index, step in enumerate([None, None, 'statements', None, None, 'nli'], 1):
        guard.request(job, se.payload_for(package, job, [{'role': 'user', 'content': 'ok'}]), step, index)
    a, b, c, d, e, f = [r['charge'] for r in guard.calls]
    assert a['basis'] == 'pre_request_reservation' and a['meanCallCount'] == 0
    assert b['basis'] == 'response_usage'
    assert c['basis'] == 'imputed_mean_same_judge' and c['costCny'] == b['costCny']
    assert c['meanCallCount'] == 1
    mean = (Decimal(b['costCny']) + Decimal(d['costCny'])) / 2
    assert e['basis'] == 'imputed_mean_same_kind' and Decimal(e['costCny']) == mean
    assert e['meanCallCount'] == 2 and Decimal(e['meanCostSumCny']) == mean * 2
    assert f['basis'] == 'imputed_mean_same_judge' and Decimal(f['costCny']) == mean
    assert f['meanCallCount'] == 2  # 三次无usage调用不参与均值。
    other = {**job, 'judge': 'qwen72b'}
    sequence.append(response('ok', usage=False))
    guard.request(other, se.payload_for(package, other, [{'role': 'user', 'content': 'ok'}]))
    assert guard.calls[-1]['charge']['basis'] == 'pre_request_reservation'


def test_imputed_charges_are_recomputed_and_tampering_rejected(prepared, tmp_path):
    fake = FakeTransport(prepared[1]['package'])
    def send(judge, payload):
        result = fake(judge, payload)
        if len(fake.calls) in (3, 5, 7):
            del result['usage']
        return result
    output, _, _, _, auth = run_fake(prepared, tmp_path, transport=send, phase='pilot', maxRequests=160)
    se.read_records(output, prepared[1]['package'], auth)
    events = [json.loads(l) for l in (output / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert {e['charge']['basis'] for e in events if 'charge' in e} >= {'imputed_mean_same_kind', 'imputed_mean_same_judge'}
    terminal = next(e for e in events if e.get('charge', {}).get('basis') == 'imputed_mean_same_kind')
    terminal['charge']['meanCallCount'] += 1
    si.write_text(output / 'events.jsonl', '\n'.join(json.dumps(e, ensure_ascii=False) for e in events) + '\n')
    si.checksums(output)
    with pytest.raises(ValueError, match='重放'):
        se.read_records(output, prepared[1]['package'], auth)


def test_pilot_report_extrapolates_per_judge_condition_and_is_reproducible(prepared, tmp_path):
    output, manifest, _, _, auth = run_fake(prepared, tmp_path, phase='pilot', maxRequests=160)
    assert manifest['plannedRequests'] == 160 and manifest['attempts'] == 96
    assert manifest['plannedUnits'] == 64
    first, second = tmp_path / 'analysis1', tmp_path / 'analysis2'
    summary = se.analyze(output, first)
    se.analyze(output, second)
    assert snapshot(first) == snapshot(second)
    assert summary['conclusion'] is None and 'byType' not in summary and 'unions' not in summary
    report = si.load(first / 'pilot-report.json')
    assert report['conclusion'] is None and not report['unavailable'] and not report['failedCalls']
    for judge in se.JUDGES:
        for kind in se.COST_POLICY['kinds']:
            value = report['byJudgeAndKind'][judge][kind]
            count = 16 if kind in ('judge', 'ragas_statements', 'ragas_nli') else 0
            assert value['requests'] == count
            assert value['inputTokens'] == 10 * count and value['outputTokens'] == 5 * count
            assert value['meanInputTokensPerUsageRequest'] == ('10' if count else None)
            assert Decimal(value['usageCostCny']) == se.sp.token_cost({'prompt_tokens': count * 10, 'completion_tokens': count * 5}, auth['prices'][judge])
        for condition in se.CONDITIONS:
            value = report['extrapolation'][judge][condition]
            assert value['scale'] == '8.25'
            assert Decimal(value['fullCostCnyEstimate']) == Decimal(value['pilotCostCny']) * Decimal('8.25')
            if condition.startswith('R'):
                assert value['measuredRequestsPerRow'] == '2' and value['fullRequestsFromMeasuredCallsPerRow'] == '132'
    assert Decimal(report['fullCostCnyEstimate']) == Decimal(report['pilotCostCny']) * Decimal('8.25')


def test_request_size_limit_marks_missing_without_charge_and_continues(prepared):
    package = small_package(deepcopy(prepared[1]['package']))
    package['ragas']['maxMessageBytes'] = se.message_bytes(se.prompt_messages(package['items'][0], 'J1'))
    fake, events = FakeTransport(package), []
    records, manifest = se.execute(package, small_auth(prepared), se.ROOT, fake, events.append, lambda _: None)
    assert manifest['stopReason'] == 'completed'
    assert len(records) == 8 and records[2]['error']['reason'] == 'request_size_limit'
    assert records[2]['calls'] == [] and records[3]['calls'] == []
    assert records[0]['status'] == records[1]['status'] == 'ok'
    assert manifest['attempts'] == len(fake.calls)


@pytest.mark.parametrize('mode', ['usage', 'actual_model', 'reservation'])
def test_default_haltOn_allows_missing_usage_model_and_over_reservation(prepared, mode):
    package = small_package(prepared[1]['package'])
    fake = FakeTransport(package)
    def send(judge, payload):
        value = fake(judge, payload)
        if mode == 'usage':
            value.pop('usage')
        elif mode == 'actual_model':
            value.pop('model')
        else:
            value['usage'] = {'prompt_tokens': 999999, 'completion_tokens': 1, 'total_tokens': 1000000}
        return value
    records, manifest = se.execute(package, small_auth(prepared), se.ROOT, send, lambda _: None, lambda _: None)
    assert manifest['stopReason'] == 'completed' and all(r['status'] == 'ok' for r in records)


def test_runner_program_error_is_archived_then_reraised(prepared, tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError('internal-test-only')
    monkeypatch.setattr(se, 'prompt_messages', broken)
    path, auth_path, output, _ = authorized(prepared, tmp_path)
    with pytest.raises(RuntimeError, match='internal-test-only'):
        se.run(path, auth_path, output, transport=lambda *_: pytest.fail('不应发送'))
    assert si.load(output / 'manifest.json')['stopReason'] == 'runner_error'
    assert si.load(output / 'records.json') == []
    si.verify_checksums(output)


@pytest.fixture(scope='module')
def completed_run(prepared, tmp_path_factory):
    # 显式假transport，并由module级offline_only禁止socket。
    return run_fake(prepared, tmp_path_factory.mktemp('completed'))


def test_full_fake_run_records_ragas_intermediates_and_throttles(prepared, completed_run):
    output, manifest, fake, sleeps, auth = completed_run
    assert manifest['stopReason'] == 'completed'
    assert manifest['attempts'] == 792 and len(fake.calls) == 792
    assert sleeps == [1.0] * 791
    assert manifest['requestsByCondition'] == {'J1': 132, 'J2': 132, 'R1': 264, 'R2': 264}
    records = se.read_records(output, prepared[1]['package'], auth)
    assert len(records) == 528
    for record in records:
        if record['condition'].startswith('R'):
            data = record['ragas']
            assert data['score'] == 1 and not data['unavailable']
            assert len(data['statements']) == 1 and data['extractionValid']
            assert data['verdicts'][0]['verdict'] == 1 and data['verdicts'][0]['reason'] == '离线假响应'
            assert data['statementsRaw'] and data['nliRaw']
    for judge, payload in fake.calls:
        assert payload['temperature'] == 0
        base_keys = {'model', 'messages', 'temperature', 'max_tokens', 'stream'}
        # J与Ragas内部的DeepSeek请求都显式关闭思考；Qwen不发送该字段。
        if judge == 'deepseek':
            assert set(payload) == base_keys | {'thinking'} and payload['thinking'] == {'type': 'disabled'}
        else:
            assert set(payload) == base_keys
        if len(payload['messages']) == 1:
            assert payload['messages'][0]['role'] == 'user' and payload['max_tokens'] == 2048
    assert (output / 'inputs' / se.BASE).read_bytes() == (se.ROOT / se.BASE).read_bytes()
    assert (output / 'inputs' / se.OLD_RECORDS).is_file()
    assert (output / 'sources' / 'backend/adapters/ragas_compat.py').is_file()
    assert si.load(output / 'runtime.json')['dependencies']['ragas'] == '0.1.21'
    si.verify_checksums(output)


@pytest.mark.parametrize('limit,reason', [('requests', 'request_limit'), ('budget', 'estimated_budget_guard')])
def test_guard_stops_before_send_and_remains_closed(prepared, limit, reason):
    package = prepared[1]['package']
    auth = small_auth(prepared)
    auth['maxRequests' if limit == 'requests' else 'budgetCny'] = 0
    events, sends = [], []
    job = se.schedule(package)[0]
    guard = se.Guard(package, auth, lambda *a: sends.append(a), events.append, lambda _: None)
    for _ in range(2):
        with pytest.raises(se.GuardStopped, match=reason):
            guard.request(job, se.payload_for(package, job, [{'role': 'user', 'content': 'x'}]))
    assert not sends and not events


def test_request_limit_is_manifest_stop(prepared):
    package = small_package(prepared[1]['package'])
    events = []
    records, manifest = se.execute(package, small_auth(prepared, maxRequests=1), se.ROOT,
                                   FakeTransport(package), events.append, lambda _: None)
    assert manifest['stopReason'] == 'request_limit' and manifest['attempts'] == 1
    assert events[-1]['reason'] == 'request_limit' and len(records) == 1


@pytest.mark.parametrize('reason', se.HALT_ON)
def test_explicit_halt_options_stop_and_permanently_block_transport(prepared, reason):
    package = prepared[1]['package']
    job = se.schedule(package)[0]
    calls, events = [], []
    def send(*args):
        calls.append(args)
        return response('ok', model=reason != 'actual_model_unknown',
                        tokens=9999999 if reason == 'reservation_exceeded' else 10)
    guard = se.Guard(package, small_auth(prepared, haltOn=[reason]), send, events.append, lambda _: None)
    for _ in range(2):
        with pytest.raises(se.GuardStopped, match=reason):
            guard.request(job, se.payload_for(package, job, [{'role': 'user', 'content': 'x'}]))
    assert len(calls) == 1 and events[-1]['charge']['basis'] == 'response_usage'


def ragas_guard(prepared, transport, **changes):
    package = prepared[1]['package']
    job = next(j for j in se.schedule(package) if j['condition'] == 'R1')
    events, sleeps = [], []
    guard = se.Guard(package, small_auth(prepared, **changes), transport, events.append, sleeps.append)
    metric = se.ChineseFaithfulness(llm=se.GuardedRagasLLM(guard, job))
    metric.init(se.RunConfig(**package['ragas']['RunConfig']))
    return guard, job, metric, events, sleeps


@pytest.mark.parametrize('repair', ['none', 'statements', 'nli', 'both'])
def test_ragas_score_and_format_repairs_are_guarded_and_recomputed(prepared, repair):
    responses = []
    if repair in ('statements', 'both'):
        truncated = response('not-json')
        truncated['choices'][0]['finish_reason'] = 'length'
        responses.append(truncated)
    responses.append(response(statements()))
    if repair in ('nli', 'both'):
        responses.append(response('not-json'))
    responses.append(response(nli(verdict=0)))
    sent = []
    def transport(judge, payload):
        sent.append(payload)
        return responses[len(sent) - 1]
    guard, job, metric, events, sleeps = ragas_guard(prepared, transport)
    row = se.ragas_row(prepared[1]['package']['items'][0], 'R1')
    score = asyncio.run(metric.ascore(row))
    assert score == 0
    assert len(sent) == 2 + (repair in ('statements', 'both')) + (repair in ('nli', 'both'))
    assert sleeps == [1.0] * (len(sent) - 1)
    assert len([e for e in events if e['event'] == 'start']) == len(sent)
    assert all(p['temperature'] == 0 and p['max_tokens'] == 2048 for p in sent)
    data = se.replay_ragas_calls(prepared[1]['package'], job, row, guard.calls)
    assert data['score'] == 0 and data['verdicts'][0]['verdict'] == 0
    assert data['formatFixUsed'] == (repair != 'none')
    assert data['statements'] == ['第五条第一款规定限额为50万元。']
    assert data['statementsRaw'] and data['nliRaw']
    assert all(e['ragasStep'] in ('statements', 'nli', 'fix') for e in events)
    assert [c['ragasCall'] for c in guard.calls] == list(range(1, len(sent) + 1))


@pytest.mark.parametrize('http_status', [400, 429, 500, 503, 599])
def test_HTTP_failure_never_retries_and_blocks_current_ragas_line(prepared, http_status):
    calls = []
    def send(*args):
        calls.append(args)
        return {'_transport': {'httpStatus': http_status, 'error': 'http_error', 'body': 'offline'}}
    guard, job, metric, _, _ = ragas_guard(prepared, send)
    row = se.ragas_row(prepared[1]['package']['items'][0], 'R1')
    with pytest.raises(se.UnitUnavailable, match='http_error'):
        asyncio.run(metric.ascore(row))
    assert len(calls) == 1 and guard.stop is None
    assert guard.calls[0]['charge']['basis'] == 'pre_request_reservation'
    with pytest.raises(se.UnitUnavailable):
        asyncio.run(metric.llm.agenerate_text(metric._create_statements_prompt(row)))
    assert len(calls) == 1


def test_guard_stops_between_ragas_steps_without_second_HTTP(prepared):
    sent = []
    def send(*args):
        sent.append(args)
        return response(statements())
    guard, job, metric, _, _ = ragas_guard(prepared, send, maxRequests=1)
    row = se.ragas_row(prepared[1]['package']['items'][0], 'R1')
    with pytest.raises(se.GuardStopped, match='request_limit'):
        asyncio.run(metric.ascore(row))
    assert len(sent) == 1
    with pytest.raises(se.GuardStopped):
        asyncio.run(metric.llm.agenerate_text(metric._create_statements_prompt(row)))
    assert len(sent) == 1


def test_adapter_rejects_n_and_sync(prepared):
    guard, job, metric, _, _ = ragas_guard(prepared, lambda *_: pytest.fail('不应发送'))
    prompt = metric._create_statements_prompt(se.ragas_row(prepared[1]['package']['items'][0], 'R1'))
    with pytest.raises(ValueError, match='n=1'):
        asyncio.run(metric.llm.agenerate_text(prompt, n=2))
    with pytest.raises(NotImplementedError):
        metric.llm.generate_text(prompt)
    assert not guard.calls


@pytest.mark.parametrize('failure', ['judge_parse', 'judge_http', 'judge_exception', 'extract', 'nli', 'extract_parse', 'nli_parse'])
def test_failures_are_missing_continue_and_replay(prepared, tmp_path, failure):
    fake = FakeTransport(prepared[1]['package'])
    sent = []
    target = 1 if failure.startswith('judge') else (6 if failure == 'nli' else 5)
    failed_unit = 0 if failure.startswith('judge') else 4
    def send(judge, payload):
        sent.append((judge, deepcopy(payload)))
        if failure.endswith('_parse') and not failure.startswith('judge'):
            # 两次格式解析失败后，该行不可用；下一单元继续。
            if judge == 'deepseek' and len(sent) in ((5, 6) if failure == 'extract_parse' else (6, 7)):
                return response('invalid-json')
        elif len(sent) == target:
            if failure == 'judge_parse':
                return response('invalid-json')
            if failure == 'judge_exception':
                raise TimeoutError('不得归档的异常消息')
            return {'_transport': {'httpStatus': 500, 'error': 'http_error', 'body': 'offline'}}
        return fake(judge, payload)
    output, manifest, _, _, auth = run_fake(prepared, tmp_path, transport=send, phase='pilot', maxRequests=160)
    assert manifest['stopReason'] == 'completed'
    records = se.read_records(output, prepared[1]['package'], auth)
    assert len(records) == 64
    assert records[failed_unit]['status'] == 'unavailable'
    assert records[failed_unit + 1]['status'] == 'ok'
    if failure in ('extract', 'nli'):
        assert len(records[4]['calls']) == (1 if failure == 'extract' else 2)
        next_index = target
        assert sent[next_index][0] == 'qwen72b'
        assert se.step_for(sent[next_index][1]['messages'][0]['content']) == 'statements'
    assert '不得归档的异常消息' not in (output / 'events.jsonl').read_text(encoding='utf-8')
    summary = se.analyze(output, tmp_path / 'analysis')
    assert summary['counts']['unavailable'] == 1
    report = si.load(tmp_path / 'analysis' / 'pilot-report.json')
    assert len(report['unavailable']) == 1


@pytest.mark.parametrize('extract,nli_text', [(statements(), '[]'), ('bad', 'bad')])
def test_nan_and_exhausted_fix_are_null_and_unavailable(prepared, extract, nli_text):
    package = small_package(prepared[1]['package'])
    fake = FakeTransport(package)
    def send(judge, payload):
        if len(payload['messages']) == 2:
            return fake(judge, payload)
        step = se.step_for(payload['messages'][0]['content'])
        return response(extract if step == 'statements' else nli_text)
    events = []
    records, manifest = se.execute(package, small_auth(prepared), se.ROOT, send, events.append, lambda _: None)
    last = records[-1]
    assert manifest['stopReason'] == 'completed'
    assert last['status'] == 'unavailable' and last['ragas']['score'] is None and last['ragas']['unavailable']
    assert 'NaN' not in json.dumps(records, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize('reason', se.HALT_ON)
def test_halt_options_stop_entire_run_and_replay(prepared, tmp_path, reason):
    def send(*_):
        value = judge_response()
        if reason == 'actual_model_unknown':
            value.pop('model')
        else:
            value['usage'] = {'prompt_tokens': 9999999, 'completion_tokens': 1, 'total_tokens': 10000000}
        return value
    output, manifest, _, _, auth = run_fake(prepared, tmp_path, transport=send,
                                            phase='pilot', maxRequests=160, haltOn=[reason])
    assert manifest['stopReason'] == reason and manifest['attempts'] == 1
    assert len(se.read_records(output, prepared[1]['package'], auth)) == 1
    se.analyze(output, tmp_path / 'analysis')
    report = si.load(tmp_path / 'analysis' / 'pilot-report.json')
    assert report['fullCostCnyEstimate'] is None
    for judge in se.JUDGES:
        for condition in ('J2', 'R1', 'R2'):
            row = report['extrapolation'][judge][condition]
            assert row['executedPilotUnits'] == 0
            assert row['fullCostCnyEstimate'] is row['fullRequestsEstimate'] is None
            assert row['fullInputTokensEstimate'] is row['fullOutputTokensEstimate'] is None


def test_budget_stops_between_ragas_steps_and_replays(prepared, tmp_path):
    package = prepared[1]['package']
    fake = FakeTransport(package)
    def send(judge, request):
        value = fake(judge, request)
        if len(fake.calls) == 5:
            # 初始预算足以通过前四个J请求；抽取后剩余预算不足以预留NLI。
            # 默认haltOn=[]允许本次usage超预留，下一次发送仍受预算守卫约束。
            value['usage'] = {'prompt_tokens': 499000, 'completion_tokens': 0, 'total_tokens': 499000}
        return value
    output, manifest, _, _, auth = run_fake(prepared, tmp_path, transport=send, phase='pilot', maxRequests=160,
                                            budgetCny=1)
    assert manifest['stopReason'] == 'estimated_budget_guard' and len(fake.calls) == 5
    records = se.read_records(output, package, auth)
    assert records[-1]['status'] == 'unavailable' and records[-1]['ragas']['extractionValid']
    assert records[-1]['ragas']['nliRaw'] is None


def test_oversized_nli_is_unsent_uncharged_and_next_unit_runs_with_replay(prepared, tmp_path):
    package = prepared[1]['package']
    fake = FakeTransport(package)
    calls = []
    def send(judge, payload):
        calls.append((judge, payload))
        if len(calls) == 5:
            return response(statements('x' * 65536))
        return fake(judge, payload)
    output, manifest, _, _, auth = run_fake(prepared, tmp_path, transport=send, phase='pilot', maxRequests=160)
    assert manifest['stopReason'] == 'completed' and manifest['attempts'] == 95
    records = se.read_records(output, package, auth)
    assert records[4]['status'] == 'unavailable' and records[4]['error']['reason'] == 'request_size_limit'
    assert len(records[4]['calls']) == 1 and records[4]['ragas']['extractionValid']
    assert records[5]['status'] == 'ok' and calls[5][0] == 'qwen72b'
    assert len(calls) == 95


def test_analyze_reproducible_and_tamper_rejected(prepared, completed_run, tmp_path):
    directory = completed_run[0]
    original = snapshot(directory)
    first, second = tmp_path / 'analysis1', tmp_path / 'analysis2'
    summary = se.analyze(directory, first)
    se.analyze(directory, second)
    assert snapshot(first) == snapshot(second) and snapshot(directory) == original
    assert summary['counts'] == {'executed': 528, 'valid': 528, 'unavailable': 0, 'notExecuted': 0}
    assert set(summary['unions']) == set(se.CONDITIONS)
    assert set(summary['withoutS26']['unions']) == set(se.CONDITIONS)
    assert summary['sameDayComparisonBackground']['interpretation'] == se.ANALYSIS_PLAN['historicalComparison']
    assert si.load(first / 'rules.json')['experimentVersion'] == se.VERSION
    assert summary['withoutS26']['byType']['all_errors']['items'] < summary['byType']['all_errors']['items']
    tampered = tmp_path / 'tampered'
    shutil.copytree(directory, tampered)
    events = [json.loads(l) for l in (tampered / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    # 重算外部校验也不能掩盖原响应与records的冲突。
    terminal = next(e for e in events if e['event'] == 'end')
    terminal['response']['choices'][0]['message']['content'] = judge_response('contradicted')['choices'][0]['message']['content']
    si.write_text(tampered / 'events.jsonl', '\n'.join(json.dumps(e, ensure_ascii=False) for e in events) + '\n')
    si.checksums(tampered)
    rejected = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='重放'):
        se.analyze(tampered, rejected)
    assert not rejected.exists()


@pytest.mark.parametrize('statement,substatements,expected', [
    ('第五条第一款规定。', ['第五条第一款规定。'], 'full'),
    ('第五条第一款规定。', ['第五条规定。'], 'truncated'),
    ('第十九条第一款第三项规定。', ['第十九条第一款规定。'], 'truncated'),
    ('第十九条第一款第三项规定。', ['第十九条规定。'], 'truncated'),
    ('第五条第一款规定。', ['Article five says so.'], 'absent'),
    ('第五条和第八条第二款。', ['第五条。', '第八条第二款。'], 'full'),
    ('第五条和第八条第二款。', ['第五条。'], 'absent'),
    ('无标签。', ['无标签。'], None),
])
def test_label_retention(statement, substatements, expected):
    assert se.label_retention(statement, substatements) == expected


def test_group_pair_rule_union_background_and_mechanism_counts(prepared):
    package = deepcopy(prepared[1]['package'])
    ids = ['paragraph_existing-S03-P2', 'paragraph_absent-S03', 'control_pinpoint-S03', 'item_absent-S26']
    package['items'] = [next(i for i in package['items'] if i['id'] == identity) for identity in ids]
    rules = {'items': {i: {'findings': [{'severity': 'error'}] if i in ids[1:3] else [{'severity': 'warning'}]} for i in ids}}
    records = []
    for job in se.schedule(package):
        item = next(i for i in package['items'] if i['id'] == job['itemId'])
        detected = ((job['condition'] in ('J2', 'R2') and item['id'] in ids[:3])
                    or (job['condition'] == 'J1' and item['id'] == ids[3]))
        record = {**job, 'status': 'ok', 'error': None,
                  'result': {'verdict': 'contradicted' if detected else 'supported'}, 'ragas': None}
        if job['condition'].startswith('R'):
            sub = (item['statement'] if job['condition'] == 'R2' else ('第五条的规定。' if item['id'] in ids[:3] else 'A statement.'))
            record['ragas'] = {'score': 0 if detected else 1, 'extractionValid': True, 'statements': [sub]}
        records.append(record)
    summary = se.summarize(package, rules, records)
    assert summary['byType']['all_errors']['items'] == 3
    assert summary['byType']['all_errors']['rules']['detectedErrors']['ids'] == [ids[1]]
    assert summary['byType']['support_controls']['conditions']['J2']['deepseek']['falsePositives']['ids'] == [ids[2]]
    pair = summary['paired']['deepseek']['J1->J2']
    assert pair['newDetections']['ids'] == ids[:2]
    assert pair['lostDetections']['ids'] == [ids[3]]
    assert pair['newFalsePositives']['ids'] == [ids[2]]
    assert summary['rulesComparison']['deepseek']['J1']['judgeOnly']['ids'] == [ids[3]]
    assert summary['rulesComparison']['deepseek']['J1']['rulesOnly']['ids'] == [ids[1]]
    assert set(summary['unions']) == set(se.CONDITIONS)
    assert summary['unions']['R2']['detectedErrors']['ids'] == ids[:2]
    assert summary['unions']['R2']['falsePositives']['ids'] == [ids[2]]
    mechanism = summary['labelRetention']['R1']['deepseek']
    assert mechanism['byType']['all_errors']['truncated']['ids'] == ids[:2]
    assert mechanism['byType']['all_errors']['absent']['ids'] == [ids[3]]
    assert mechanism['byType']['all_errors']['noCjk']['ids'] == [ids[3]]
    assert mechanism['undetectedErrorsWithLabelLoss']['ids'] == [ids[0], ids[1], ids[3]]
    background = se.same_day_background(package, records, se.ROOT)
    old = {(r['itemId'], r['judge'], r['prompt']): r['result']['verdict'] for r in si.load(se.ROOT / se.OLD_RECORDS)}
    expected = [r['itemId'] for r in records if r['judge'] == 'deepseek' and r['condition'] == 'J1'
                and r['result']['verdict'] != old[(r['itemId'], 'deepseek', 'original')]]
    assert background['judges']['deepseek']['J1']['verdictDifferences']['ids'] == expected
    incomplete = se.summarize(package, rules, records[:1])
    assert incomplete['counts']['notExecuted'] == len(records) - 1
    assert incomplete['paired']['deepseek']['J1->J2']['unavailable']['count'] == 4
    partial = incomplete['byType']['all_errors']['conditions']['J1']['deepseek']
    assert partial['valid'] == 1 and partial['unavailable'] == 0 and partial['notExecuted'] == 2


def test_analysis_and_run_refuse_nonempty_output(prepared, completed_run, tmp_path):
    path, auth_path, output, _ = authorized(prepared, tmp_path)
    output.mkdir()
    (output / 'keep.txt').write_text('keep', encoding='utf-8')
    before = snapshot(output)
    with pytest.raises(ValueError, match='空目录'):
        se.run(path, auth_path, output, transport=lambda *_: pytest.fail('不应发送'))
    with pytest.raises(ValueError, match='空目录'):
        se.analyze(completed_run[0], output)
    assert snapshot(output) == before


def test_thinking_request_and_response_facts_recorded_and_reported(prepared, tmp_path):
    package = prepared[1]['package']
    fake = FakeTransport(package)
    count = 0

    def send(judge, payload):
        nonlocal count
        count += 1
        value = fake(judge, payload)
        if count == 3:
            # 若供应商仍返回思维链，必须照实记录、计数，不过滤。
            value['choices'][0]['message']['reasoning_content'] = '思考'
            value['usage']['completion_tokens_details'] = {'reasoning_tokens': 7}
        return value
    output, manifest, _, _, auth = run_fake(prepared, tmp_path, transport=send, phase='pilot', maxRequests=160)
    assert manifest['stopReason'] == 'completed'
    runtime = si.load(output / 'runtime.json')
    assert runtime['thinking'] == {'deepseek': {'type': 'disabled'}, 'qwen72b': None}
    calls = [json.loads(l) for l in (output / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    starts = [e for e in calls if e['event'] == 'start']
    ends = [e for e in calls if e['event'] in ('end', 'failure')]
    assert all(e['thinkingRequested'] == ({'type': 'disabled'} if e['judge'] == 'deepseek' else None) for e in starts + ends)
    flagged = [e for e in ends if e['reasoning']['reasoningContentReturned']]
    assert len(flagged) == 1 and flagged[0]['reasoning'] == {'reasoningContentReturned': True,
                                                             'reasoningContentChars': 2, 'reasoningTokens': 7}
    se.analyze(output, tmp_path / 'analysis')
    report = si.load(tmp_path / 'analysis' / 'pilot-report.json')
    evidence = report['thinkingEvidence'][flagged[0]['judge']]['all']
    assert evidence['reasoningContentReturned'] == {'count': 1, 'ids': [flagged[0]['requestId']]}
    assert evidence['reasoningTokensReported'] == 1 and evidence['reasoningTokensSum'] == 7
    assert evidence['outputTokens']['max'] == 5
    other = 'qwen72b' if flagged[0]['judge'] == 'deepseek' else 'deepseek'
    assert report['thinkingEvidence'][other]['all']['reasoningContentReturned']['count'] == 0


@pytest.mark.parametrize('decisions', [None, [{'decidedBy': 'x', 'decidedAt': 'bad', 'text': 't'}],
                                       [{'decidedBy': '', 'decidedAt': '2026-10-22', 'text': 't'}],
                                       [{'decidedBy': 'x', 'decidedAt': '2026-10-22', 'text': 't', 'extra': 1}]])
def test_supplementary_decisions_must_be_well_formed(prepared, tmp_path, decisions):
    path, auth_path, output, _ = authorized(prepared, tmp_path, supplementaryDecisions=decisions)
    with pytest.raises(ValueError):
        se.run(path, auth_path, output, transport=lambda *_: pytest.fail('不应发送'))
    assert not output.exists()
