"""款项准备材料的离线契约，不调用模型、不改冻结配方。"""
import json
import socket
from collections import Counter
from copy import deepcopy
from decimal import Decimal

import pytest

from backend.core.statute_check import parse_provisions
from tools.competition import statute_injection as si
from tools.competition import statute_pinpoint as sp


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('准备材料不能访问网络或构造付费传输')
    monkeypatch.setattr(si, 'make_transport', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


@pytest.fixture
def material(tmp_path):
    output = tmp_path / 'prep'
    return output, sp.prepare(output)


def test_prepare_is_byte_reproducible_and_accepts_empty_directory(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    second.mkdir()
    sp.prepare(first)
    sp.prepare(second)
    left = {p.relative_to(first).as_posix(): p.read_bytes() for p in first.rglob('*') if p.is_file()}
    right = {p.relative_to(second).as_posix(): p.read_bytes() for p in second.rglob('*') if p.is_file()}
    assert left == right
    si.verify_checksums(first)
    si.verify_checksums(second)
    with pytest.raises(ValueError, match='空目录'):
        sp.prepare(first)
    assert left == {p.name: p.read_bytes() for p in first.iterdir()}


def test_every_changed_statement_has_one_local_replacement(material):
    for item in material[1]['package']['items']:
        original, edit = item['originalStatement'], item['edit']
        if item['type'] == 'control':
            assert edit is None and item['statement'] == original
            continue
        start, before, after = (edit[k] for k in ('start', 'before', 'after'))
        assert original.count(before) == 1 and original[start:start + len(before)] == before
        assert before != after
        assert item['statement'] == original[:start] + after + original[start + len(before):]
        if 'pinpointEdit' in item:
            anchor = item['anchorStatement']
            edit = item['pinpointEdit']
            start = edit['start']
            assert anchor.count(edit['before']) == 1
            assert item['statement'] == anchor[:start] + edit['after'] + anchor[start + len(edit['before']):]
            # 相对正确对照仅改变定位；去掉引用后正文完全相同。
            assert item['statement'].replace(edit['after'], '', 1) == anchor.replace(edit['before'], '', 1)


def test_expected_labels_follow_source_structure_independently_of_rules(material):
    provisions = parse_provisions((sp.ROOT / si.SOURCE).read_text(encoding='utf-8'))
    for item in material[1]['package']['items']:
        assert item['expected'] == ('not_supported' if item['type'] in sp.ERROR_TYPES else 'supported')
        if 'sourceLocation' not in item:
            continue
        source = item['sourceLocation']
        assert any((p.article, p.paragraph, p.item) == tuple(source[k] for k in ('article', 'paragraph', 'item')) for p in provisions)
        assert item['evidence'][0]['quote'] in (sp.ROOT / si.SOURCE).read_text(encoding='utf-8')
        target = item.get('targetLocation')
        if target is None:
            continue
        paragraphs = {p.paragraph for p in provisions if p.article == target['article']}
        if item['type'] == 'paragraph_absent':
            assert target['paragraph'] not in paragraphs
        elif item['type'] == 'paragraph_existing':
            assert target['paragraph'] in paragraphs and target['paragraph'] != source['paragraph']
            assert target['article'] == source['article'] and target['item'] is None
        elif item['type'].startswith('item_'):
            numbers = {p.item for p in provisions if p.article == target['article'] and p.paragraph == target['paragraph']}
            assert target['paragraph'] == source['paragraph'] and target['item'] != source['item']
            assert (target['item'] in numbers) == (item['type'] == 'item_existing')
        else:
            assert item['type'] == 'paragraph_omitted' and source['paragraph'] > 1 and target['paragraph'] is None


def test_prompts_differ_only_by_added_instruction_and_payload_never_leaks_labels(material):
    assert sp.PROMPTS['original'] == si.SYSTEM_PROMPT
    assert sp.PROMPTS['pinpoint'] == si.SYSTEM_PROMPT + sp.ADDITION
    output = material[0]
    assert (output / 'prompt.original.txt').read_bytes() == (sp.ROOT / sp.FORMAL / 'prompt.txt').read_bytes()
    assert (output / 'prompt.pinpoint.txt').read_bytes() == (output / 'prompt.original.txt').read_bytes() + sp.ADDITION.encode()
    for item in material[1]['package']['items']:
        original, pinpoint = [sp.prompt_messages(item, name) for name in sp.PROMPTS]
        assert original == si.prompt_messages(item, item)
        assert original[1] == pinpoint[1]
        payload = json.loads(original[1]['content'])
        assert set(payload) == {'question', 'statement', 'contexts'}
        assert payload['statement'] == item['statement']
        assert payload['contexts'] == [{'contextIndex': n, 'text': c} for n, c in enumerate(item['contexts'])]


def test_pending_review_draft_and_full_factorial_request_count(material):
    output, result = material
    package, auth = result['package'], result['authorization']
    assert package['review'] == si.pending()
    assert all(i['review'] == si.pending() for i in package['items'])
    assert auth['status'] == 'draft' and not auth['allowPaidCalls'] and not auth['acceptNoHardBillingCap']
    assert auth['authorizedBy'] is None and auth['authorizedAt'] is None and auth['budgetCny'] is None
    assert auth['maxRequests'] == len(package['items']) * len(package['judges']) * len(sp.PROMPTS) == 264
    assert auth['maxRetries'] == 0 and auth['packageSha256'] == si.sha256(output / 'package.json')
    assert auth['promptSetSha256'] == si.digest(sp.PROMPTS)
    with pytest.raises(ValueError, match='付费授权'):
        si.validate_authorization(package, output / 'package.json', auth)
    # 旧工具必须拒绝新配方，不能误启动旧的单提示运行协议。
    with pytest.raises(ValueError, match='实验版本'):
        si.validate_package(package)


def test_historical_disagreement_and_omission_are_not_misrepresented(material):
    items = material[1]['package']['items']
    assert {i['base'] for i in items} == {r[1] for r in si.RECIPES if r[2] == 'control'} | {'S01', 'S19', 'S26'}
    for item in items:
        if item['base'] == 'S26':
            assert item['baseHumanLabels'] == {'V': '支持', 'A': '支持', 'B': '无法判断'}
        else:
            assert set(item['baseHumanLabels'].values()) == {'支持'}
        if item['type'] == 'paragraph_omitted':
            assert item['expected'] == 'supported'
    assert len({i['statement'] for i in items}) == len(items)
    assert all(i['type'] == 'control' for i in items if i['base'] == 'S32')


def test_cost_uses_historical_actual_usage_and_both_prompts(material):
    cost = material[1]['authorization']['estimates']
    for judge, totals in {'deepseek': (23766, 4016), 'qwen72b': (25872, 3704)}.items():
        row = cost['judges'][judge]
        assert (row['historicalInputTokens'], row['historicalOutputTokens']) == totals
        assert row['historicalRequests'] == 42
        assert set(row['byPrompt']) == set(sp.PROMPTS)
        assert row['byPrompt']['pinpoint']['inputTokensEstimate'] > row['byPrompt']['original']['inputTokensEstimate']
        assert row['byPrompt']['pinpoint']['outputTokensEstimate'] == row['byPrompt']['original']['outputTokensEstimate']
    assert cost['totalCostCnyEstimate'] == round(sum(r['costCnyEstimate'] for r in cost['judges'].values()), 6)


def test_absent_provisions_are_reported_as_errors_not_only_warnings(material):
    package, rules = material[1]['package'], material[1]['rules']
    assert set(rules['items']) == {i['id'] for i in package['items']}
    for item in package['items']:
        if item['type'] not in ('paragraph_absent', 'item_absent'):
            continue
        result = rules['items'][item['id']]
        assert result['flagged'] is True
        assert 'PROVISION_NOT_IN_SOURCE' in result['codes']
        assert any(f['code'] == 'PROVISION_NOT_IN_SOURCE' and f['severity'] == 'error' for f in result['findings'])


@pytest.fixture
def confirmation_note(tmp_path):
    path = tmp_path / 'note.txt'
    path.write_bytes('  确认原文：全部同意。\r\nS26 保留历史分歧。\n末行空白  \n'.encode('utf-8'))
    return path


def test_confirm_preserves_material_note_and_unpaid_draft(material, confirmation_note, tmp_path):
    prep, original = material
    before = {p.name: p.read_bytes() for p in prep.iterdir()}
    output = tmp_path / 'confirmed'
    result = sp.confirm(prep / 'package.json', 'Ll', '2026-10-21', confirmation_note, output)
    confirmed = deepcopy(result['package'])
    review = {'status': 'confirmed', 'reviewer': 'Ll', 'reviewedAt': '2026-10-21',
              'note': confirmation_note.read_bytes().decode('utf-8').rstrip('\r\n')}
    assert confirmed['review'] == review
    assert confirmed['scope'] == sp.SCOPES['confirmed'] and original['package']['scope'] == sp.SCOPES['pending']
    confirmed['review'] = original['package']['review']
    confirmed['scope'] = original['package']['scope']
    for item, old in zip(confirmed['items'], original['package']['items']):
        assert item['review'] == review
        item['review'] = old['review']
    assert confirmed == original['package']
    assert si.load(output / 'package.json') == result['package']
    auth = si.load(output / 'authorization.draft.json')
    assert auth['status'] == 'draft' and auth['allowPaidCalls'] is False
    assert auth['acceptNoHardBillingCap'] is False
    assert auth['authorizedBy'] is None and auth['authorizedAt'] is None and auth['budgetCny'] is None
    assert auth['packageSha256'] == si.sha256(output / 'package.json') != si.sha256(prep / 'package.json')
    assert auth['preparationToolFiles'] == {name: si.sha256(sp.ROOT / name) for name in
                                             ('tools/competition/statute_pinpoint.py', 'tests/test_statute_pinpoint.py')}
    for name in ('rules.json', 'cost-estimate.json', 'prompt.original.txt', 'prompt.pinpoint.txt', 'prompts.diff'):
        assert (output / name).read_bytes() == before[name]
    sheet = (output / 'RUN_CONFIRMATION.md').read_bytes().decode('utf-8')
    assert '已确认、未授权' in sheet and '确认人：Ll；日期：2026-10-21' in sheet
    assert review['note'] in sheet
    assert 'V/A=支持、B=无法判断' in sheet
    assert before == {p.name: p.read_bytes() for p in prep.iterdir()}
    si.verify_checksums(output)
    with pytest.raises(ValueError, match='付费授权'):
        si.validate_authorization(result['package'], output / 'package.json', auth)


@pytest.mark.parametrize('change', ['package_review', 'item_review', 'count', 'group', 'duplicate',
                                   'item_existing-S26-I1', 'item_existing-S26-I2', 'item_existing-S26-I4',
                                   'item_absent-S26', 'control-S26', 'history'])
def test_confirm_rejects_invalid_input_before_writing(material, confirmation_note, tmp_path, change):
    prep, result = material
    package = deepcopy(result['package'])
    by_id = {i['id']: i for i in package['items']}
    if change == 'package_review':
        package['review']['status'] = 'confirmed'
    elif change == 'item_review':
        package['items'][0]['review']['status'] = 'confirmed'
    elif change == 'count':
        package['items'].pop()
    elif change == 'group':
        package['items'][0]['type'] = 'item_existing'
    elif change == 'duplicate':
        package['items'][0]['id'] = package['items'][1]['id']
    elif change == 'history':
        by_id['control-S26']['baseHumanLabels']['B'] = '支持'
    else:
        by_id[change]['expected'] = 'supported' if change != 'control-S26' else 'not_supported'
    si.write_json(prep / 'package.json', package)
    output = tmp_path / 'rejected'
    output.mkdir()
    with pytest.raises(ValueError):
        sp.confirm(prep / 'package.json', 'Ll', '2026-10-21', confirmation_note, output)
    assert list(output.iterdir()) == []


def test_confirm_reproducible_idempotent_and_rejects_different_reconfirmation(material, confirmation_note, tmp_path):
    prep, _ = material
    first, second = tmp_path / 'first', tmp_path / 'second'
    second.mkdir()
    for output in (first, second):
        sp.confirm(prep / 'package.json', 'Ll', '2026-10-21', confirmation_note, output)
    snapshot = {p.name: p.read_bytes() for p in first.iterdir()}
    assert snapshot == {p.name: p.read_bytes() for p in second.iterdir()}
    si.verify_checksums(first)
    si.verify_checksums(second)
    with pytest.raises(ValueError, match='空目录'):
        sp.confirm(prep / 'package.json', 'Ll', '2026-10-21', confirmation_note, first)
    assert snapshot == {p.name: p.read_bytes() for p in first.iterdir()}
    # 对已确认材料用同一份确认重跑：产物逐字节相同。
    again = tmp_path / 'again'
    sp.confirm(first / 'package.json', 'Ll', '2026-10-21', confirmation_note, again)
    assert snapshot == {p.name: p.read_bytes() for p in again.iterdir()}
    other_note = tmp_path / 'other.txt'
    other_note.write_text('另一份说明', encoding='utf-8')
    for args in (('Kk', '2026-10-21', confirmation_note), ('Ll', '2026-10-22', confirmation_note), ('Ll', '2026-10-21', other_note)):
        rejected = tmp_path / 'rejected'
        with pytest.raises(ValueError, match='同一份确认'):
            sp.confirm(first / 'package.json', *args, rejected)
        assert not rejected.exists()


@pytest.mark.parametrize('reviewer,reviewed_at', [('', '2026-10-21'), ('Ll', '20261021'), ('Ll', '2026-02-30')])
def test_confirm_rejects_invalid_reviewer_or_date(material, confirmation_note, tmp_path, reviewer, reviewed_at):
    output = tmp_path / 'rejected'
    with pytest.raises(ValueError):
        sp.confirm(material[0] / 'package.json', reviewer, reviewed_at, confirmation_note, output)
    assert not output.exists()


@pytest.fixture
def authorized(material, confirmation_note, tmp_path):
    """仅临时目录中的离线测试副本；不是真实授权。"""
    confirmed = tmp_path / 'test-confirmed'
    result = sp.confirm(material[0] / 'package.json', 'offline-test-only', '2026-10-21', confirmation_note, confirmed)
    output = tmp_path / 'run'
    auth = deepcopy(result['authorization'])
    auth.update(status='authorized', authorizedBy='offline-test-only', authorizedAt='2026-10-21',
                allowPaidCalls=True, acceptNoHardBillingCap=True, budgetCny=100,
                outputDirectory=str(output.resolve()))
    for price in auth['prices'].values():
        price.update(inputCnyPerMillion=1, outputCnyPerMillion=2, source='offline synthetic price', checkedAt='2026-10-21')
    auth_path = tmp_path / 'test-authorization.json'
    si.write_json(auth_path, auth)
    return confirmed / 'package.json', auth_path, output


def fake_response(verdict='supported', usage=True):
    response = {'model': 'offline-test-model', 'choices': [{'message': {'content': json.dumps({
        'verdict': verdict, 'reason': '离线模拟结果', 'citations': []}, ensure_ascii=False)}, 'finish_reason': 'stop'}]}
    if usage:
        response['usage'] = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
    return response


def events_in(directory):
    return [json.loads(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]


def reserved_costs(package_path, auth_path):
    package, auth = si.load(package_path), si.load(auth_path)
    items = {i['id']: i for i in package['items']}
    return [Decimal(sp.reservation(package, auth, job, sp.payload_for(package, items[job['itemId']], job))['costCny'])
            for job in sp.schedule(package)]


def set_budget(auth_path, budget):
    auth = si.load(auth_path)
    auth['budgetCny'] = float(budget)
    si.write_json(auth_path, auth)
    return Decimal(str(auth['budgetCny']))


@pytest.mark.parametrize('change,match', [
    ('package_pending', '包级 review'), ('item_pending', '逐条 review'),
    ('package_note', '包级 review'), ('item_reviewer', '逐条 review'),
    ('package_date', '包级 review'), ('item_date', '逐条 review'),
    ('statement', '逐字段'), ('context', '逐字段'), ('label', '逐字段'), ('item_extra', '逐字段'),
    ('count', '66'), ('order', '逐字段'), ('plan', '分析口径'), ('prompt', '提示词'),
    ('source', '来源'), ('model', '模型'), ('scope', 'scope'),
])
def test_run_rejects_unconfirmed_or_changed_material_without_output(authorized, change, match):
    package_path, auth_path, output = authorized
    package = si.load(package_path)
    if change == 'package_pending':
        package['review'] = si.pending()
    elif change == 'item_pending':
        package['items'][10]['review'] = si.pending()
    elif change == 'package_note':
        package['review']['note'] = ' '
    elif change == 'item_reviewer':
        package['items'][0]['review']['reviewer'] = ''
    elif change == 'package_date':
        package['review']['reviewedAt'] = '20261021'
    elif change == 'item_date':
        package['items'][0]['review']['reviewedAt'] = '2026-02-30'
    elif change == 'statement':
        package['items'][0]['statement'] += '篡改'
    elif change == 'context':
        package['items'][0]['contexts'][0] += '篡改'
    elif change == 'label':
        package['items'][0]['expected'] = 'not_supported'
    elif change == 'item_extra':
        package['items'][0]['extra'] = '篡改'
    elif change == 'count':
        package['items'].pop()
    elif change == 'order':
        package['items'].reverse()
    elif change == 'plan':
        package['analysisPlan'] = []
    elif change == 'prompt':
        package['prompts']['pinpoint'] += '篡改'
    elif change == 'source':
        package['sourceFiles'] = {}
    elif change == 'model':
        package['judges']['deepseek']['model'] = 'other'
    else:
        package['scope'] = sp.SCOPES['pending']
    si.write_json(package_path, package)
    with pytest.raises(ValueError, match=match):
        sp.run(package_path, auth_path, output)
    assert not output.exists()


def test_run_rejects_actual_pending_prep(material, tmp_path):
    output = tmp_path / 'rejected-run'
    with pytest.raises(ValueError, match='包级 review'):
        sp.run(material[0] / 'package.json', material[0] / 'authorization.draft.json', output)
    assert not output.exists()


@pytest.mark.parametrize('field', [
    'status', 'allowPaidCalls', 'acceptNoHardBillingCap', 'authorizedBy', 'authorizedAt',
    'packageSha256', 'configurationSha256', 'promptSha256', 'promptSetSha256', 'promptHashes',
    'preparationToolFiles', 'toolFiles', 'maxRequests', 'maxRetries', 'budgetCny', 'outputDirectory', 'prices', 'estimates',
])
def test_run_rejects_missing_authorization_fields(authorized, field):
    package_path, auth_path, output = authorized
    auth = si.load(auth_path)
    del auth[field]
    si.write_json(auth_path, auth)
    with pytest.raises(ValueError):
        sp.run(package_path, auth_path, output)
    assert not output.exists()


@pytest.mark.parametrize('field,value', [
    ('status', 'draft'), ('allowPaidCalls', False), ('authorizedBy', ' '), ('authorizedAt', '2026-02-30'),
    ('packageSha256', '0' * 64), ('configurationSha256', '0' * 64), ('promptSetSha256', '0' * 64),
    ('promptHashes', {'original': '0' * 64}), ('preparationToolFiles', {}), ('toolFiles', {}),
    ('maxRequests', 263), ('maxRetries', 1), ('budgetCny', 0), ('budgetCny', -1), ('budgetCny', True),
    ('outputDirectory', 'wrong-output'), ('prices', {}), ('estimates', {}),
])
def test_run_rejects_invalid_authorization(authorized, field, value):
    package_path, auth_path, output = authorized
    auth = si.load(auth_path)
    auth[field] = value
    si.write_json(auth_path, auth)
    with pytest.raises(ValueError):
        sp.run(package_path, auth_path, output)
    assert not output.exists()


@pytest.mark.parametrize('field', ['toolFiles', 'preparationToolFiles'])
def test_run_recomputes_each_tool_hash(authorized, field):
    package_path, auth_path, output = authorized
    auth = si.load(auth_path)
    for name in auth[field]:
        bad = deepcopy(auth)
        bad[field][name] = '0' * 64
        si.write_json(auth_path, bad)
        with pytest.raises(ValueError, match=field):
            sp.run(package_path, auth_path, output)
        assert not output.exists()


def test_request_cap_is_enforced_even_if_scheduler_has_extra_job(authorized, monkeypatch):
    package_path, auth_path, output = authorized
    fixed = sp.schedule(si.load(package_path))
    monkeypatch.setattr(sp, 'schedule', lambda package: fixed + [{**fixed[-1], 'requestId': 'must-not-send'}])
    calls = []
    manifest = sp.run(package_path, auth_path, output,
                      transport=lambda judge, payload: calls.append((judge, payload)) or fake_response())
    assert manifest['attempts'] == len(calls) == 264
    assert manifest['stopReason'] == 'request_limit'
    events = events_in(output)
    assert events[-1]['event'] == 'stop' and events[-1]['reason'] == 'request_limit'
    assert not any(e.get('requestId') == 'must-not-send' for e in events)
    for index in range(0, 264, 2):
        assert calls[index][0] == calls[index + 1][0]
        original, pinpoint = calls[index][1]['messages'], calls[index + 1][1]['messages']
        assert original[1] == pinpoint[1]
        assert [original[0]['content'], pinpoint[0]['content']] == list(sp.PROMPTS.values())
        assert set(json.loads(original[1]['content'])) == {'question', 'statement', 'contexts'}
    assert [j['itemId'] for j in fixed] == [i['id'] for i in si.load(package_path)['items'] for _ in range(4)]
    assert [j['judge'] for j in fixed[:4]] == ['deepseek', 'deepseek', 'qwen72b', 'qwen72b']
    si.verify_checksums(output)


@pytest.mark.parametrize('first_paid', [False, True])
def test_budget_reserves_before_each_request_and_cannot_resume(authorized, first_paid):
    package_path, auth_path, output = authorized
    reserved = reserved_costs(package_path, auth_path)
    actual = Decimal('0.00002')  # 每请求 (10*1 + 5*2)/1e6
    # 边界含等号：预算恰好等于「已花 + 本次预留」时仍发送。
    budget = set_budget(auth_path, actual + reserved[1] if first_paid else reserved[0])
    expected, spent = 0, Decimal(0)
    while spent + reserved[expected] <= budget:
        spent, expected = spent + actual, expected + 1
    if first_paid:
        assert expected >= 2  # 第二次请求恰在边界上，仍须发送
    else:
        assert expected == 1
    calls = []
    manifest = sp.run(package_path, auth_path, output,
                      transport=lambda *args: calls.append(args) or fake_response())
    assert manifest['stopReason'] == 'estimated_budget_guard' and manifest['attempts'] == len(calls) == expected
    assert Decimal(manifest['estimatedCostCny']) == spent <= budget
    events = events_in(output)
    assert events[-1]['reason'] == 'estimated_budget_guard'
    starts = [e for e in events if e['event'] == 'start']
    assert [Decimal(e['reservation']['costCny']) for e in starts] == reserved[:expected]
    assert starts[0]['reservation']['tokens']['completion_tokens'] == 768
    runtime = si.load(output / 'runtime.json')
    assert set(runtime['git']) >= {'head', 'status'} and 'packages' in runtime['dependencies']
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='空目录'):
        sp.run(package_path, auth_path, output)
    assert before == {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    summary = sp.analyze(output, output.parent / 'partial-analysis')
    assert summary['records'] == {'total': expected, 'ok': expected, 'failed': 0, 'notAttempted': 264 - expected}
    assert summary['paired']['qwen72b']['unavailable']['count'] == 66


def test_budget_below_first_reservation_sends_nothing(authorized):
    package_path, auth_path, output = authorized
    set_budget(auth_path, reserved_costs(package_path, auth_path)[0] - Decimal('0.000001'))
    calls = []
    manifest = sp.run(package_path, auth_path, output, transport=lambda *args: calls.append(args) or fake_response())
    assert manifest['stopReason'] == 'estimated_budget_guard' and manifest['attempts'] == len(calls) == 0
    assert [e['event'] for e in events_in(output)] == ['stop']
    assert sp.analyze(output, output.parent / 'empty-analysis')['records']['notAttempted'] == 264


def test_missing_usage_charges_reservation_and_stops(authorized):
    package_path, auth_path, output = authorized
    calls = []
    manifest = sp.run(package_path, auth_path, output,
                      transport=lambda *args: calls.append(args) or fake_response(usage=False))
    assert manifest['attempts'] == len(calls) == 1 and manifest['stopReason'] == 'usage_unknown'
    record, = si.load(output / 'records.json')
    start = events_in(output)[0]
    assert record['charge'] == {'basis': 'pre_request_reservation', **start['reservation']}
    assert manifest['estimatedCostCny'] == start['reservation']['costCny']
    assert '供应商侧没有费用自动封顶' in manifest['costNote']
    assert sp.analyze(output, output.parent / 'usage-analysis')['records']['ok'] == 1


@pytest.mark.parametrize('change,reason', [('model', 'actual_model_unknown'), ('usage', 'reservation_exceeded')])
def test_unknown_model_or_usage_beyond_reservation_stops(authorized, change, reason):
    package_path, auth_path, output = authorized
    response = fake_response()
    if change == 'model':
        del response['model']
    else:
        response['usage'] = {'prompt_tokens': 10 ** 6, 'completion_tokens': 5, 'total_tokens': 10 ** 6 + 5}
    manifest = sp.run(package_path, auth_path, output, transport=lambda *args: deepcopy(response))
    assert manifest['attempts'] == 1 and manifest['stopReason'] == reason
    record, = si.load(output / 'records.json')
    assert record['charge']['basis'] == 'response_usage'
    assert events_in(output)[-1]['reason'] == reason
    assert sp.analyze(output, output.parent / 'halt-analysis')['records']['total'] == 1


def test_failures_are_archived_separately_and_not_retried(authorized):
    package_path, auth_path, output = authorized
    calls = []
    def transport(judge, payload):
        calls.append((judge, payload))
        if len(calls) == 1:
            response = fake_response()
            response['choices'][0]['message']['content'] = 'invalid json'
            return response
        raise RuntimeError('do-not-log-this-secret')
    manifest = sp.run(package_path, auth_path, output, transport=transport)
    # 解析失败有用量，照常继续；调用异常没有用量，按预留记账后停止，不重试。
    assert manifest['attempts'] == len(calls) == 2 and manifest['stopReason'] == 'usage_unknown'
    records = si.load(output / 'records.json')
    assert [r['status'] for r in records] == ['parse_error', 'call_error']
    assert [r['charge']['basis'] for r in records] == ['response_usage', 'pre_request_reservation']
    assert records[1]['error']['type'] == 'RuntimeError' and records[1]['error']['httpStatus'] is None
    assert [e['event'] for e in events_in(output)] == ['start', 'failure', 'start', 'failure', 'stop']
    assert 'do-not-log-this-secret' not in (output / 'events.jsonl').read_text(encoding='utf-8')
    summary = sp.analyze(output, output.parent / 'failure-analysis')
    assert summary['records'] == {'total': 2, 'ok': 0, 'failed': 2, 'notAttempted': 262}
    assert [r['status'] for r in summary['failures']] == ['parse_error', 'call_error']


def test_http_error_keeps_redacted_body_and_stops(authorized):
    package_path, auth_path, output = authorized
    failure = {'_transport': {'httpStatus': 429, 'error': 'http_error', 'body': 'rate limited'}}
    manifest = sp.run(package_path, auth_path, output, transport=lambda *args: deepcopy(failure))
    assert manifest['attempts'] == 1 and manifest['stopReason'] == 'usage_unknown'
    record, = si.load(output / 'records.json')
    assert record['status'] == 'call_error'
    assert record['error']['type'] == 'http_error' and record['error']['httpStatus'] == 429
    assert events_in(output)[1]['response'] == failure
    assert sp.analyze(output, output.parent / 'http-analysis')['records']['failed'] == 1


def test_interrupted_attempt_is_not_silently_removed(authorized):
    package_path, auth_path, output = authorized
    def interrupt(*args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        sp.run(package_path, auth_path, output, transport=interrupt)
    assert si.load(output / 'manifest.json')['stopReason'] == 'interrupted'
    summary = sp.analyze(output, output.parent / 'interrupted-analysis')
    assert summary['records'] == {'total': 1, 'ok': 0, 'failed': 1, 'notAttempted': 263}
    assert summary['failures'][0]['status'] == 'incomplete'


def test_analysis_group_counts_pairs_union_s26_and_byte_reproducibility(authorized):
    package_path, auth_path, output = authorized
    package = si.load(package_path)
    jobs = iter(sp.schedule(package))
    new, lost, s26 = 'paragraph_existing-S03-P2', 'paragraph_absent-S03', 'item_existing-S26-I1'
    fp_new, fp_reduced = 'control-S03', 'control-S07'
    detected = {(new, 'deepseek', 'pinpoint'), (lost, 'deepseek', 'original'),
                (s26, 'deepseek', 'pinpoint'), (fp_new, 'deepseek', 'pinpoint'),
                (fp_reduced, 'deepseek', 'original'), (new, 'qwen72b', 'original')}
    def transport(judge, payload):
        job = next(jobs)
        assert judge == job['judge']
        key = (job['itemId'], judge, job['prompt'])
        return fake_response('insufficient' if key in detected else 'supported')
    assert sp.run(package_path, auth_path, output, transport=transport)['stopReason'] == 'completed'
    first, second = output.parent / 'analysis-first', output.parent / 'analysis-second'
    second.mkdir()
    summary = sp.analyze(output, first)
    assert summary == sp.analyze(output, second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {p.name: p.read_bytes() for p in second.iterdir()}
    si.verify_checksums(first)
    assert summary['paired']['deepseek'] == {
        'newDetections': {'count': 2, 'ids': [new, s26]}, 'lostDetections': {'count': 1, 'ids': [lost]},
        'newFalsePositives': {'count': 1, 'ids': [fp_new]}, 'reducedFalsePositives': {'count': 1, 'ids': [fp_reduced]},
        'unavailable': {'count': 0, 'ids': []}}
    counts = Counter(i['type'] for i in package['items'])
    rules = si.load(first / 'rules.json')['items']
    rule_ids = {key for key, value in rules.items() if any(f['severity'] == 'error' for f in value['findings'])}
    for kind in sp.TYPES:
        row = summary['byType'][kind]
        members = [i for i in package['items'] if i['type'] == kind]
        member_ids = {i['id'] for i in members}
        assert row['items'] == counts[kind]
        assert row['rulesFlagged'] == len(member_ids & rule_ids)
        assert row['union']['flagged'] == len(member_ids & (rule_ids | {x[0] for x in detected}))
        for judge in package['judges']:
            for prompt in sp.PROMPTS:
                result = row['judges'][judge][prompt]
                expected = sum((i['id'], judge, prompt) in detected for i in members)
                assert result['valid'] == len(members) and result['unavailable'] == 0
                assert result['notSupported'] == expected
                assert result['detectedErrors'] == (expected if kind in sp.ERROR_TYPES else 0)
                assert result['falsePositives'] == (0 if kind in sp.ERROR_TYPES else expected)
    assert summary['byType']['paragraph_errors']['items'] == sum(counts[t] for t in sp.ERROR_TYPES[:2])
    assert summary['byType']['item_errors']['items'] == sum(counts[t] for t in sp.ERROR_TYPES[2:])
    assert summary['withoutS26']['paired']['deepseek']['newDetections'] == {'count': 1, 'ids': [new]}
    without = [i for i in package['items'] if i['base'] != 'S26']
    assert summary['withoutS26']['records']['total'] == len(without) * 4
    assert sum(summary['withoutS26']['byType'][t]['items'] for t in sp.TYPES) == len(without)
    for row in summary['withoutS26']['byType'].values():
        assert all('-S26' not in identity for identity in row['union']['ids'])
    with pytest.raises(ValueError, match='空目录'):
        sp.analyze(output, first)


def test_rules_warning_is_not_a_detection():
    package = {'items': [{'id': 'test', 'type': 'control', 'expected': 'supported', 'base': 'test'}], 'judges': {'test': {}}}
    rules = {'items': {'test': {'flagged': True, 'findings': [{'severity': 'warning'}]}}}
    summary = sp.summarize(package, rules, [])
    assert summary['byType']['control']['rulesFlagged'] == 0
    assert summary['byType']['control']['union']['flagged'] == 0


@pytest.mark.parametrize('tamper', ['records', 'schedule', 'event_result', 'request', 'manifest', 'stop', 'source'])
def test_analysis_rejects_inconsistent_archive_before_writing(authorized, tamper):
    package_path, auth_path, output = authorized
    set_budget(auth_path, reserved_costs(package_path, auth_path)[0])  # 恰好一次请求
    assert sp.run(package_path, auth_path, output, transport=lambda *args: fake_response())['attempts'] == 1
    if tamper in ('records', 'schedule'):
        si.write_json(output / f'{tamper}.json', [])
    elif tamper == 'manifest':
        manifest = si.load(output / 'manifest.json')
        manifest['attempts'] = 0
        si.write_json(output / 'manifest.json', manifest)
    elif tamper == 'source':
        with (output / 'sources/tools/competition/statute_pinpoint.py').open('ab') as stream:
            stream.write(b'\n# changed\n')
    else:
        events = events_in(output)
        if tamper == 'event_result':
            events[1]['result']['verdict'] = 'contradicted'
        elif tamper == 'request':
            events[0]['request']['messages'][1]['content'] = '{}'
        else:
            events[-1]['reason'] = 'completed'
        si.write_text(output / 'events.jsonl', ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events))
    si.checksums(output)  # 即使重写清单，事件/原响应一致性检查仍应拒绝。
    analysis = output.parent / 'invalid-analysis'
    with pytest.raises(ValueError):
        sp.analyze(output, analysis)
    assert not analysis.exists()


def test_prepare_confirm_match_public_archived_bytes(tmp_path):
    """公开包保存确认后材料；准备阶段共享五份原字节，确认阶段再核对正文与确认单。"""
    archived = sp.ROOT / 'docs/competition/statute-check/20261021-pinpoint-prep'
    si.verify_checksums(archived)
    before = {p.name: p.read_bytes() for p in archived.iterdir() if p.is_file()}
    prepared, confirmed = tmp_path / 'public-prep', tmp_path / 'public-confirmed'
    sp.prepare(prepared)
    review = si.load(archived / 'package.json')['review']
    note = tmp_path / 'public-archived-note.txt'
    note.write_bytes(review['note'].encode('utf-8'))
    sp.confirm(prepared / 'package.json', review['reviewer'], review['reviewedAt'], note, confirmed)
    shared = ('cost-estimate.json', 'prompt.original.txt', 'prompt.pinpoint.txt', 'prompts.diff', 'rules.json')
    for generated, names in ((prepared, shared), (confirmed, (*shared, 'package.json', 'RUN_CONFIRMATION.md'))):
        for name in names:
            assert (generated / name).read_bytes() == before[name], name
        si.verify_checksums(generated)
    assert before == {p.name: p.read_bytes() for p in archived.iterdir() if p.is_file()}


def test_prepare_confirm_still_match_archived_bytes(tmp_path):
    """工具哈希只影响授权草稿及清单中该草稿的一行；其余字节不变。"""
    archived = sp.ROOT / 'work/statute-injection/pinpoint-20261021'
    if not archived.exists():
        pytest.skip('missing-local-material: work/statute-injection/pinpoint-20261021')
    before = {p.relative_to(archived): p.read_bytes() for name in ('prep', 'confirmed')
              for p in (archived / name).iterdir() if p.is_file()}
    prepared, confirmed = tmp_path / 'compatible-prep', tmp_path / 'compatible-confirmed'
    sp.prepare(prepared)
    review = si.load(archived / 'confirmed/package.json')['review']
    note = tmp_path / 'archived-note.txt'
    note.write_bytes(review['note'].encode('utf-8'))
    sp.confirm(prepared / 'package.json', review['reviewer'], review['reviewedAt'], note, confirmed)
    for name, generated in (('prep', prepared), ('confirmed', confirmed)):
        baseline = archived / name
        assert {p.name for p in generated.iterdir()} == {p.name for p in baseline.iterdir()}
        for path in generated.iterdir():
            if path.name == 'authorization.draft.json':
                actual, expected = si.load(path), si.load(baseline / path.name)
                actual.pop('preparationToolFiles')
                expected.pop('preparationToolFiles')
                assert actual == expected
            elif path.name == 'SHA256SUMS.txt':
                def material_lines(p):
                    return [line for line in p.read_bytes().splitlines(keepends=True)
                            if not line.endswith(b'  authorization.draft.json\n')]
                assert material_lines(path) == material_lines(baseline / path.name)
            else:
                assert path.read_bytes() == (baseline / path.name).read_bytes(), path.name
        si.verify_checksums(generated)
    assert before == {p.relative_to(archived): p.read_bytes() for name in ('prep', 'confirmed')
                      for p in (archived / name).iterdir() if p.is_file()}
