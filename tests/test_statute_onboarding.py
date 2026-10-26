"""新法规接入的离线验证：两部旧法规节选建包并用假传输跑通；不发请求、不联网。

两份旧节选都不满足登记的节选规则（条号不连续、末尾带来源说明），所以除专门检查规则的用例外，
测试一律关掉 ENFORCE_EXCERPT_RULE；命令行入口始终强制执行。
"""
from decimal import Decimal
import importlib.util
import json
import shutil
import subprocess
import sys

import pytest

from backend.core import statute_check as sc
from tools.competition import statute_injection as si
from tools.competition import statute_onboarding as so

ROOT = so.ROOT
# 第二章第一条的条号：测试用名义值，只为让旧节选走完流程，不代表官方文本的分章。
OLD = {'deposit_insurance': 1, 'information_disclosure': 29}
TODAY = '2026-10-24'


@pytest.fixture(scope='module')
def packages(tmp_path_factory):
    base = tmp_path_factory.mktemp('packages')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(so, 'ENFORCE_EXCERPT_RULE', False)
        for name, first in OLD.items():
            so.write_package(ROOT / f'scenarios/{name}.txt', base / name, first)
    return {name: base / name for name in OLD}


@pytest.fixture(autouse=True)
def relaxed(monkeypatch):
    monkeypatch.setattr(so, 'ENFORCE_EXCERPT_RULE', False)


@pytest.fixture
def deposit(packages):
    return packages['deposit_insurance']


def authorize(tmp_path, output, package, **changes):
    auth = so.authorization_template(package)
    auth.update(status='authorized', allowPaidCalls=True, authorizedBy='offline-test',
                authorizedAt=TODAY, note='离线假传输，无真实授权', budgetCny=5.0,
                outputDirectory=str(output), prices={'deepseek': {
                    'inputCnyPerMillion': 2, 'outputCnyPerMillion': 8,
                    'source': 'offline-test', 'checkedAt': TODAY}})
    auth.update(changes)
    path = tmp_path / 'auth.json'
    si.write_json(path, auth)
    return path


def response(verdict='supported', model='deepseek-flash', usage=True):
    result = {'model': model, 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
        {'verdict': verdict, 'reason': '离线理由', 'citations': []}, ensure_ascii=False)}}]}
    if usage:
        result['usage'] = {'prompt_tokens': 1000, 'completion_tokens': 60, 'total_tokens': 1060}
    return result


def execute(tmp_path, package, send=None, **changes):
    output = tmp_path / 'run'
    auth = authorize(tmp_path, output, package, **changes)
    sent = []

    def transport(judge, payload):
        sent.append(payload)
        return send(judge, payload) if send else response()

    manifest = so.run(package, auth, output, transport=transport, sleep=lambda _: None, today=TODAY)
    return manifest, output, sent


def events(output):
    return so.load_requests(output / 'events.jsonl')


def excerpt(articles, title='测试条例（2026年，节选）', tail=''):
    """articles：{条号: [款, ...]}；款为字符串，或 (款, [项, ...])。"""
    blocks = []
    for number, paragraphs in articles.items():
        lines = []
        for j, paragraph in enumerate(paragraphs):
            body, items = (paragraph, []) if isinstance(paragraph, str) else paragraph
            lines.append((f'第{sc.cn_label(number)}条　' if j == 0 else '') + body)
            lines.extend(items)
        blocks.append('\n'.join(lines))
    return title + '\n\n' + '\n\n'.join(blocks) + '\n' + tail


def paragraphs(article, n):
    return [f'甲{sc.cn_label(article)}乙{sc.cn_label(j)}须依法办理。补充{sc.cn_label(article)}说明{sc.cn_label(j)}。'
            for j in range(1, n + 1)]


def multi(numbers, n=3):
    return {a: paragraphs(a, n) for a in numbers}


# ---- 出题规则 ----

def test_thresholds_match_replication_at_31():
    assert so.thresholds_of({'errors': 31, 'controls': 31}) == {
        'simpleSufficientMinDetected': 28, 'simpleSufficientMaxFalsePositives': 3, 'labelGainMinDifference': 5,
        'labelGainMaxP': '1/20', 'labelGainFalsePositiveTolerance': 3}
    assert so.thresholds_of({'errors': 24, 'controls': 27})['simpleSufficientMinDetected'] == 22  # ⌈21.6⌉


def test_statement_construction_rules():
    text = excerpt({
        2: ['单款条内容须遵守。第二句不取。'],
        3: [('有下列情形之一的，应当报告：', ['（一）甲情形；', '（二）乙情形。']), '报告应当及时。其余不取。'],
        4: ['登记机构应当登记。具体办法另定。', '前款所称登记机构应当登记。另行规定。'],
        5: ['本款没有句号'] + paragraphs(5, 2)[1:],
    })
    rows, excluded = so.build_items(text)
    by_id = {r['id']: r for r in rows}
    question = '《测试条例》节选对此有什么规定？'
    assert all(r['question'] == question for r in rows)
    assert by_id['control_pinpoint-A2P1']['statement'] == '按照第二条第一款规定，单款条内容须遵守。'
    assert by_id['paragraph_absent-A2toP2']['statement'] == '按照第二条第二款规定，单款条内容须遵守。'
    assert by_id['control_article-A2']['statement'] == '按照第二条规定，单款条内容须遵守。'
    assert by_id['control_pinpoint-A3P1']['statement'] == '按照第三条第一款规定，有下列情形之一的，应当报告：（一）甲情形；（二）乙情形。'
    assert by_id['paragraph_existing-A3P1toP2']['statement'] == '按照第三条第二款规定，有下列情形之一的，应当报告：（一）甲情形；（二）乙情形。'
    assert by_id['paragraph_existing-A3P2toP1']['statement'] == '按照第三条第一款规定，报告应当及时。'
    assert by_id['control_article-A3']['statement'] == '按照第三条规定，报告应当及时。'  # 多款条只引条号取最后一款
    assert by_id['paragraph_absent-A3toP3']['cited'] == '第三条第三款'
    assert by_id['control_pinpoint-A5P1']['statement'] == '按照第五条第一款规定，本款没有句号'
    # 剔除规则只有一条：第四条第一款的第一句在第二款里逐字出现。
    assert [e['id'] for e in excluded] == ['paragraph_existing-A4P1toP2']
    assert 'paragraph_existing-A4P2toP1' in by_id
    assert [r['type'] for r in rows if r['citedArticle'] == 3] == [
        'paragraph_existing', 'paragraph_existing', 'paragraph_absent', 'control_pinpoint', 'control_pinpoint',
        'control_article']
    totals = so.totals_of(rows)
    # E = 多款条款数之和 + 条数 − 剔除；K = 全部款数 + 条数。
    assert totals == {'errors': (2 + 2 + 2) + 4 - 1, 'controls': (1 + 2 + 2 + 2) + 4}
    assert all(r['expected'] == ('not_supported' if r['type'] in so.ERROR_TYPES else 'supported') for r in rows)


def test_requests_rotate_arms_and_only_change_context_and_prompt():
    text = excerpt(multi(range(3, 9)))
    derived = so.derive(text, 3)
    requests, rows = derived['requests'], derived['rows']
    assert len(requests) == 4 * len(rows)
    assert [r['arm'] for r in requests[:8]] == ['U-C0', 'L-C0', 'U-C1', 'L-C1', 'L-C0', 'U-C1', 'L-C1', 'U-C0']
    for request in requests:
        payload = request['payload']
        assert {k: v for k, v in payload.items() if k != 'messages'} == {
            'model': 'deepseek-flash', 'temperature': 0, 'max_tokens': 768, 'stream': False,
            'thinking': {'type': 'disabled'}}
        system, user = payload['messages']
        assert system['content'] == (si.SYSTEM_PROMPT if request['prompt'] == 'C0'
                                     else si.SYSTEM_PROMPT + '\n' + so.CRITERION)
        body = json.loads(user['content'])
        assert body['contexts'] == [{'contextIndex': 0, 'text': text if request['context'] == 'U' else derived['labeled']}]
    assert so.strip_labels(derived['labeled'], derived['insertions']) == text
    assert '第三条第一款　甲三乙一' in derived['labeled'] and '\n第三条第二款　甲三乙二' in derived['labeled']


@pytest.mark.parametrize('articles,first,failed,last', [
    (multi(range(3, 9)), 3, [], 8),
    (multi(range(1, 7)), 1, [], 6),                                             # 不分章，从第一条起
    ({1: paragraphs(1, 3), **multi(range(3, 8))}, 3, [], 1),                    # 到附则前不够，从第一条起补取
    # 第一章整章补取后与第二章条号相连：最后取的是第二条，不是第八条。
    ({1: paragraphs(1, 1), 2: paragraphs(2, 3), **multi(range(3, 8)), 8: paragraphs(8, 1)}, 3, [], 2),
    (multi(range(3, 10)), 3, ['minimal'], 9),                                   # 多取了一条
    ({1: paragraphs(1, 1), **multi(range(3, 9))}, 3, ['minimal'], 1),           # 补取不必要
    ({2: paragraphs(2, 1), **multi(range(3, 9))}, 3, ['backfillFromFirst', 'minimal'], 2),
    ({**multi(range(3, 6)), **multi(range(8, 11))}, 3, ['mainFromChapterTwo'], 10),
    (multi(range(3, 9)), 2, ['mainFromChapterTwo'], 8),                         # 没从第二章第一条起
    (multi(range(3, 8)), 3, ['minMultiParagraphArticles', 'minErrorStatements'], 7),
    ({a: paragraphs(a, 2) for a in range(3, 11)}, 3, [], 10),                   # 8 个两款条：多款 8，E = 24
    ({3: [('有下列情形：', ['（一）甲；', '（三）乙。'])], **multi(range(4, 10))}, 3, ['itemsContiguous'], 9),
])
def test_excerpt_rule_checks(articles, first, failed, last):
    report = so.excerpt_report(excerpt(articles), first)
    assert [k for k, v in report['checks'].items() if not v] == failed
    assert report['passed'] is (not failed) and report['lastTaken'] == last


@pytest.mark.parametrize('first', [0, '3', 3.0, None, True])
def test_chapter_two_first_must_be_positive_int(first):
    with pytest.raises(ValueError, match='正整数'):
        so.excerpt_report(excerpt(multi(range(3, 9))), first)


def test_excerpt_rule_bytes_tail_and_format():
    long = multi(range(3, 9))
    long[3] = ['长' * 3000 + '。'] + long[3][1:]
    assert not so.excerpt_report(excerpt(long), 3)['checks']['maxBytes']
    tail = excerpt(multi(range(3, 9)), tail='\n来源说明：不属于条文。\n')
    assert [k for k, v in so.excerpt_report(tail, 3)['checks'].items() if not v] == ['allLinesParsed']
    with pytest.raises(ValueError, match='CR'):
        so.derive(excerpt(multi(range(3, 9))).replace('\n', '\r\n'), 3)
    with pytest.raises(ValueError, match='首行'):
        so.derive(excerpt(multi(range(3, 9))).replace('节选）\n\n', '节选）\n', 1), 3)


@pytest.mark.parametrize('name,errors,controls,checks', [
    ('deposit_insurance', 24, 27, ['allLinesParsed', 'mainFromChapterTwo']),
    ('information_disclosure', 16, 22, ['allLinesParsed', 'mainFromChapterTwo', 'minMultiParagraphArticles',
                                        'minErrorStatements']),
])
def test_old_statutes_build_offline(packages, name, errors, controls, checks):
    package, requests, contexts = so.validate_package(packages[name])
    assert package['totals'] == {'errors': errors, 'controls': controls}
    assert package['maxRequests'] == len(requests) == 4 * (errors + controls)
    assert package['excluded'] == []
    assert package['thresholds'] == so.thresholds_of(package['totals'])
    assert [k for k, v in package['excerptRule']['checks'].items() if not v] == checks
    assert contexts['U'] == [(ROOT / f'scenarios/{name}.txt').read_text(encoding='utf-8')]
    estimate = si.load(packages[name] / 'estimates.json')
    assert Decimal(estimate['expectedCostCny']) < Decimal(estimate['reservationUpperCny']) <= Decimal(so.BUDGET_CAP_CNY)
    rules = si.load(packages[name] / 'rules.json')['items']
    assert set(rules) == {i['id'] for i in package['items']}
    assert all(type(r['rule']['flagged']) is bool and type(r['lexical']['flagged']) is bool for r in rules.values())


def test_enforced_rule_refuses_old_excerpts(packages, monkeypatch, tmp_path):
    monkeypatch.setattr(so, 'ENFORCE_EXCERPT_RULE', True)
    with pytest.raises(ValueError, match='节选不满足登记的节选规则'):
        so.validate_package(packages['deposit_insurance'])
    with pytest.raises(ValueError, match='节选不满足登记的节选规则'):
        so.write_package(ROOT / 'scenarios/deposit_insurance.txt', tmp_path / 'out', 1)
    assert not (tmp_path / 'out' / 'package.json').exists()


def test_isolated_cli_always_enforces(packages, tmp_path):
    maker = [sys.executable, '-X', 'utf8', '-I', str(ROOT / so.MAKER),
             '--source', str(ROOT / 'scenarios/deposit_insurance.txt'), '--out', str(tmp_path / 'out'),
             '--chapter-two-first-article', '1']
    result = subprocess.run(maker, capture_output=True)
    assert result.returncode != 0 and '节选不满足登记的节选规则' in result.stderr.decode('utf-8')
    assert not (tmp_path / 'out' / 'package.json').exists()
    runner = [sys.executable, '-X', 'utf8', '-I', str(ROOT / 'tools/competition/statute_onboarding.py'),
              'template', '--package', str(packages['deposit_insurance'])]
    result = subprocess.run(runner, capture_output=True)
    assert result.returncode != 0 and '节选不满足登记的节选规则' in result.stderr.decode('utf-8')


def test_make_package_main_in_process(tmp_path, monkeypatch, capsys, packages):
    spec = importlib.util.spec_from_file_location('onboarding_make_package', ROOT / so.MAKER)
    maker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(maker)
    assert maker.so is so
    monkeypatch.setattr(sys, 'argv', ['make_package.py', '--source', str(ROOT / 'scenarios/deposit_insurance.txt'),
                                      '--out', str(tmp_path / 'out'), '--chapter-two-first-article', '1'])
    maker.main()
    assert '待发请求 204 个' in capsys.readouterr().out
    for name in (*so.PACKAGE_FILES, 'SHA256SUMS.txt'):
        assert (tmp_path / 'out' / name).read_bytes() == (packages['deposit_insurance'] / name).read_bytes()


def test_template(deposit):
    template = so.authorization_template(deposit)
    assert template['status'] == 'draft' and template['allowPaidCalls'] is False
    assert template['maxRequests'] == 204 and template['maxRetries'] == 0 and template['budgetCny'] is None
    assert template['toolFiles'] == so.tool_hashes() and set(template['toolFiles']) == set(so.TOOL_FILES)
    assert so.MAKER in template['toolFiles'] and so.LEXICAL in template['toolFiles']


def test_source_outside_repository_refused(tmp_path):
    path = tmp_path / 'excerpt.txt'
    path.write_text(excerpt(multi(range(3, 9))), encoding='utf-8', newline='\n')
    with pytest.raises(ValueError, match='仓库内'):
        so.write_package(path, tmp_path / 'out', 3)


# ---- 题包与登记的绑定 ----

@pytest.mark.parametrize('name,value,match', [
    ('REGISTRATION_SHA256', '0' * 64, '哈希不符'),
    ('CANDIDATES_SHA256', '0' * 64, '哈希不符'),
    ('CRITERION_REGISTRATION_SHA256', '0' * 64, '哈希不符'),
    ('CRITERION', '另一句判据。', '判据句'),
    ('LEXICAL_SHA256', '0' * 64, '字面基线'),
])
def test_registrations_bound(deposit, monkeypatch, name, value, match):
    monkeypatch.setattr(so, name, value)
    with pytest.raises(ValueError, match=match):
        so.validate_package(deposit)


@pytest.mark.parametrize('filename', [*so.PACKAGE_FILES, 'SHA256SUMS.txt'])
def test_any_package_file_tampered(tmp_path, deposit, filename):
    package = tmp_path / 'package'
    shutil.copytree(deposit, package)
    path = package / filename
    path.write_bytes(path.read_bytes() + b'\nX')
    with pytest.raises(ValueError):
        so.validate_package(package)


@pytest.mark.parametrize('change', ['counts', 'expected', 'duplicate', 'criterion', 'thresholds', 'excluded',
                                    'passed', 'chapter', 'chapterType', 'source', 'code', 'extra'])
def test_rehashed_package_semantics_rejected(tmp_path, deposit, change):
    package = tmp_path / 'package'
    shutil.copytree(deposit, package)
    meta = si.load(package / 'package.json')
    if change == 'counts':
        meta['counts']['control_article'] += 1
    elif change == 'expected':
        meta['items'][0]['expected'] = 'supported'
    elif change == 'duplicate':
        meta['items'][1] = dict(meta['items'][0])
    elif change == 'criterion':
        meta['prompts']['C1']['criterion'] = 'unregistered criterion'
    elif change == 'thresholds':
        meta['thresholds']['simpleSufficientMinDetected'] -= 1
    elif change == 'excluded':
        meta['excluded'] = [{'id': meta['items'][0]['id'], 'reason': '事后剔除'}]
    elif change == 'passed':
        meta['excerptRule']['passed'] = True
    elif change == 'chapter':
        meta['excerptRule']['chapterTwoFirstArticle'] = 2
    elif change == 'chapterType':
        meta['excerptRule']['chapterTwoFirstArticle'] = '1'
    elif change == 'source':
        meta['source']['path'] = 'scenarios/information_disclosure.txt'
    elif change == 'code':
        meta['code']['statuteCheck'] = '0' * 64
    else:
        (package / 'notes.txt').write_text('extra', encoding='utf-8')
    si.write_json(package / 'package.json', meta)
    si.checksums(package)
    with pytest.raises(ValueError):
        so.validate_package(package)


@pytest.mark.parametrize('field,value', [
    ('model', 'wrong'), ('temperature', 1), ('max_tokens', 769),
    ('thinking', {'type': 'enabled'}), ('system', 'wrong'), ('user', 'wrong'),
])
def test_rehashed_payload_still_validated(tmp_path, deposit, field, value):
    package = tmp_path / 'package'
    shutil.copytree(deposit, package)
    requests = so.load_requests(package / 'requests.jsonl')
    if field in ('system', 'user'):
        requests[0]['payload']['messages'][0 if field == 'system' else 1]['content'] = value
    else:
        requests[0]['payload'][field] = value
    (package / 'requests.jsonl').write_text(so.request_lines(requests), encoding='utf-8', newline='\n')
    meta = si.load(package / 'package.json')
    meta['requestsSha256'] = si.sha256(package / 'requests.jsonl')
    si.write_json(package / 'package.json', meta)
    si.checksums(package)
    with pytest.raises(ValueError, match='请求与重建不符'):
        so.validate_package(package)


# ---- 运行器：授权、预留、停止、记录与回放 ----

@pytest.mark.parametrize('field', so.BOUND)
def test_every_authorization_hash_bound(tmp_path, deposit, field):
    with pytest.raises(ValueError, match='授权哈希不符'):
        execute(tmp_path, deposit, lambda *_: pytest.fail('不得发送'), **{field: '0' * 64})
    assert not (tmp_path / 'run').exists()


def test_nonempty_output_refused(tmp_path, deposit):
    output = tmp_path / 'run'
    output.mkdir()
    (output / 'keep.txt').write_bytes(b'keep')
    with pytest.raises(ValueError, match='新的空目录'):
        execute(tmp_path, deposit, lambda *_: pytest.fail('不得发送'))
    assert (output / 'keep.txt').read_bytes() == b'keep'


@pytest.mark.parametrize('name', OLD)
def test_complete_run_and_byte_identical_replay(tmp_path, packages, name):
    package, requests, _ = so.validate_package(packages[name])
    expected = {i['statement']: i['expected'] for i in package['items']}
    totals = package['totals']

    def send(judge, payload):
        statement = json.loads(payload['messages'][1]['content'])['statement']
        return response('contradicted' if expected[statement] == 'not_supported' else 'supported')

    manifest, output, sent = execute(tmp_path, packages[name], send)
    planned = len(requests)
    assert sent == [r['payload'] for r in requests]
    assert manifest['stopReason'] == 'completed'
    assert manifest['attempts'] == manifest['completedRecords'] == manifest['plannedRequests'] == planned
    assert [e['event'] for e in events(output)] == ['start', 'end'] * planned + ['stop']
    assert all((output / 'sources' / n).read_bytes() == (ROOT / n).read_bytes() for n in so.TOOL_FILES)
    si.verify_checksums(output)
    result = so.analyze(output, tmp_path / 'a.json')
    so.analyze(output, tmp_path / 'b.json')
    assert (tmp_path / 'a.json').read_bytes() == (tmp_path / 'b.json').read_bytes()
    assert len(result['details']) == planned
    assert result['primary']['decision'] == 'simple_sufficient'
    assert result['primaryAfterConstructionRemoval'] is None
    for arm in so.ARMS:
        row = result['arms'][arm]
        assert row['detected'] == row['detectionTotal'] == totals['errors']
        assert row['falsePositiveTotal'] == totals['controls']
        assert row['falsePositives'] == row['unavailable'] == 0
        assert {t: c['items'] for t, c in row['byType'].items()} == package['counts']
    rules = si.load(packages[name] / 'rules.json')['items']
    for engine in ('rule', 'lexical'):
        reference = result['offlineBaselines'][engine]
        assert reference['detected'] == sum(rules[i['id']][engine]['flagged'] for i in package['items']
                                            if i['expected'] == 'not_supported')
        assert reference['falsePositives'] == sum(rules[i['id']][engine]['flagged'] for i in package['items']
                                                  if i['expected'] == 'supported')
    assert all(v == {'detected': 0, 'falsePositives': 0} for v in result['effects'].values())


@pytest.mark.parametrize('model', ['wrong', '', None])
def test_wrong_model_stops_at_k(tmp_path, deposit, model):
    calls = []

    def send(*_):
        calls.append(1)
        return response(model=model if len(calls) == 3 else 'deepseek-flash')

    manifest, output, sent = execute(tmp_path, deposit, send)
    assert manifest['stopReason'] == 'actual_model_unknown' and len(sent) == 3
    result = so.analyze(output, tmp_path / 'a.json')
    assert result['primary']['containsUnavailable'] is True  # 后续未发出的格均为不可用


def test_budget_just_below_next_reservation(tmp_path, deposit):
    package, requests, _ = so.validate_package(deposit)
    template = so.authorization_template(deposit)
    template['prices'] = {'deepseek': {'inputCnyPerMillion': 2, 'outputCnyPerMillion': 8}}
    first = so.reservation(package, template, so.job_of(requests[0]), requests[0]['payload'])
    second = so.reservation(package, template, so.job_of(requests[1]), requests[1]['payload'])
    budget = float(Decimal(first['costCny']) + Decimal(second['costCny']) - Decimal('0.000000001'))
    manifest, output, sent = execute(tmp_path, deposit, lambda *_: response(usage=False), budgetCny=budget)
    assert manifest['stopReason'] == 'estimated_budget_guard' and len(sent) == 1
    assert manifest['estimatedCostCny'] == first['costCny']
    so.analyze(output, tmp_path / 'a.json')


def test_transport_exception_incomplete_charged_and_stopped(tmp_path, deposit):
    def send(*_):
        raise RuntimeError('do not archive exception text')

    manifest, output, sent = execute(tmp_path, deposit, send)
    assert len(sent) == 1 and manifest['stopReason'] == 'interrupted'
    assert manifest['completedRecords'] == 0
    rows = si.load(output / 'records.json')
    assert rows[0]['status'] == 'incomplete'
    assert rows[0]['charge']['costCny'] == manifest['estimatedCostCny']
    assert events(output)[0]['reservation']['costCny'] == manifest['estimatedCostCny']
    assert [e['event'] for e in events(output)] == ['start', 'stop']
    assert 'do not archive' not in (output / 'events.jsonl').read_text(encoding='utf-8')
    result = so.analyze(output, tmp_path / 'a.json')
    assert result['primary']['containsUnavailable'] is True


@pytest.mark.parametrize('during_sleep', [False, True])
def test_keyboard_interrupt_archived(tmp_path, deposit, during_sleep):
    output = tmp_path / 'run'
    auth = authorize(tmp_path, output, deposit)

    def interrupt(*_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        so.run(deposit, auth, output, transport=(lambda *_: response()) if during_sleep else interrupt,
               sleep=interrupt if during_sleep else (lambda _: None), today=TODAY)
    manifest = si.load(output / 'manifest.json')
    assert manifest['stopReason'] == 'interrupted'
    assert manifest['completedRecords'] == int(during_sleep)
    so.analyze(output, tmp_path / 'a.json')


@pytest.mark.parametrize('changes', [
    {'budgetCny': 0}, {'budgetCny': float('inf')}, {'budgetCny': -1}, {'budgetCny': 5.01},
    {'maxRequests': 203}, {'maxRetries': 1}, {'allowPaidCalls': False},
    {'outputDirectory': 'wrong'}, {'authorizedAt': '2026-10-23'},
])
def test_invalid_authorization_rejected(tmp_path, deposit, changes):
    # NaN/Infinity 不经过 JSON 编码，直接验证授权对象。
    output = tmp_path / 'run'
    auth = si.load(authorize(tmp_path, output, deposit))
    auth.update(changes)
    with pytest.raises(ValueError):
        so.validate_authorization(deposit, auth, output, today=TODAY)


def test_date_not_checked_on_replay(tmp_path, deposit):
    output = tmp_path / 'run'
    auth = si.load(authorize(tmp_path, output, deposit))
    with pytest.raises(ValueError, match='运行当天须重新核价'):
        so.validate_authorization(deposit, auth, output, today='2026-10-25')
    so.validate_authorization(deposit, auth, None, today='2026-10-25')


def test_replay_tamper_rejected_after_rehash(tmp_path, deposit):
    _, output, _ = execute(tmp_path, deposit)
    rows = events(output)
    rows[1]['result']['verdict'] = 'contradicted'
    (output / 'events.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    si.checksums(output)
    with pytest.raises(ValueError, match='结果/用量与原始响应不符'):
        so.analyze(output, tmp_path / 'a.json')


def test_analysis_output_rules(tmp_path, deposit):
    _, output, _ = execute(tmp_path, deposit)
    with pytest.raises(ValueError, match='不能写入运行归档'):
        so.analyze(output, output / 'a.json')
    (tmp_path / 'a.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='禁止覆盖'):
        so.analyze(output, tmp_path / 'a.json')


@pytest.mark.parametrize('bad', [None, [], {'choices': [], 'model': ''}])
def test_malformed_response_stops_and_replays(tmp_path, deposit, bad):
    manifest, output, sent = execute(tmp_path, deposit, lambda *_: bad)
    assert manifest['stopReason'] == 'actual_model_unknown' and len(sent) == 1
    result = so.analyze(output, tmp_path / 'a.json')
    assert result['primary']['containsUnavailable'] is True


def test_usage_exceeds_reservation_stops(tmp_path, deposit):
    def send(*_):
        value = response()
        value['usage'] = {'prompt_tokens': 1000000, 'completion_tokens': 1000000, 'total_tokens': 2000000}
        return value

    manifest, output, sent = execute(tmp_path, deposit, send)
    assert manifest['stopReason'] == 'reservation_exceeded' and len(sent) == 1
    so.analyze(output, tmp_path / 'a.json')


def test_missing_usage_full_reservation(tmp_path, deposit):
    manifest, output, sent = execute(tmp_path, deposit, lambda *_: response(usage=False))
    assert manifest['stopReason'] == 'completed' and len(sent) == 204
    starts = [e for e in events(output) if e['event'] == 'start']
    assert Decimal(manifest['estimatedCostCny']) == sum(Decimal(e['reservation']['costCny']) for e in starts)
    assert (Decimal(manifest['estimatedCostCny']).quantize(Decimal('0.0001')) ==
            Decimal(si.load(deposit / 'estimates.json')['reservationUpperCny']))
    so.analyze(output, tmp_path / 'a.json')


def test_transport_error_envelope_stops(tmp_path, deposit):
    manifest, output, sent = execute(tmp_path, deposit, lambda *_: {'_transport': {'error': 'timeout'}})
    assert manifest['stopReason'] == 'interrupted' and len(sent) == 1
    assert si.load(output / 'records.json')[0]['status'] == 'incomplete'
    so.analyze(output, tmp_path / 'a.json')


# ---- 判定 ----

def synthetic_results(package_dir, b=0, c=0, common=0, uf=0, lf=0):
    package = si.load(package_dir / 'package.json')
    errors = [i['id'] for i in package['items'] if i['expected'] == 'not_supported']
    controls = [i['id'] for i in package['items'] if i['expected'] == 'supported']
    u = set(errors[:common] + errors[common + b:common + b + c])
    l = set(errors[:common + b])
    rows = []
    for item in package['items']:
        for arm in so.ARMS:
            flagged = (item['id'] in (u if arm[0] == 'U' else l) if item['expected'] == 'not_supported'
                       else item['id'] in controls[:uf if arm[0] == 'U' else lf])
            rows.append({'itemId': item['id'], 'arm': arm, 'status': 'ok',
                         'result': {'verdict': 'contradicted' if flagged else 'supported', 'reason': 'fake'}})
    return package, rows, si.load(package_dir / 'rules.json')


# 存款保险旧节选：E = 24，K = 27，门槛 22、2、+3。
@pytest.mark.parametrize('b,c,common,uf,lf,decision,fraction,decimal', [
    (5, 0, 0, 0, 0, 'label_gain', '1/32', '0.031250'),
    (6, 1, 0, 0, 0, 'insufficient_evidence', '1/16', '0.062500'),
    (5, 0, 0, 0, 4, 'insufficient_evidence', '1/32', '0.031250'),
    (5, 0, 0, 0, 3, 'label_gain', '1/32', '0.031250'),
    (0, 0, 22, 2, 20, 'simple_sufficient', '1/1', '1.000000'),
    (0, 0, 22, 3, 3, 'no_gain', '1/1', '1.000000'),
    (0, 0, 21, 0, 0, 'no_gain', '1/1', '1.000000'),
    (3, 3, 0, 0, 0, 'no_gain', '21/32', '0.656250'),
    (0, 1, 0, 0, 0, 'no_gain', '1/1', '1.000000'),
    (0, 0, 0, 0, 0, 'no_gain', '1/1', '1.000000'),
    (4, 0, 0, 0, 0, 'insufficient_evidence', '1/16', '0.062500'),
])
def test_decision_boundaries(deposit, b, c, common, uf, lf, decision, fraction, decimal):
    package, rows, rules = synthetic_results(deposit, b, c, common, uf, lf)
    result = so.summarize(package, rows, rules)
    primary = result['primary']
    assert result['thresholds'] == {'simpleSufficientMinDetected': 22, 'simpleSufficientMaxFalsePositives': 2,
                                    'labelGainMinDifference': 5, 'labelGainMaxP': '1/20',
                                    'labelGainFalsePositiveTolerance': 3}
    assert primary['decision'] == decision
    assert (primary['b'], primary['c'], primary['d']) == (b, c, b - c)
    assert primary['p'] == {'fraction': fraction, 'decimal': decimal}


def test_secondary_label_effect_under_c0(deposit):
    package, rows, rules = synthetic_results(deposit, b=5, common=2, lf=1)
    secondary = so.summarize(package, rows, rules)['secondaryLabelC0']
    assert (secondary['b'], secondary['c']) == (5, 0)
    assert secondary['p'] == {'fraction': '1/32', 'decimal': '0.031250'}
    assert secondary['falsePositives'] == {'U-C0': 0, 'L-C0': 1}


def test_construction_errors_reported_before_and_after_with_fixed_thresholds(deposit, tmp_path):
    package, rows, rules = synthetic_results(deposit, b=5)
    errors = [i['id'] for i in package['items'] if i['expected'] == 'not_supported']
    removed = [{'id': errors[0], 'reason': '构造错误：内容不是逐字原文'}]
    result = so.summarize(package, rows, rules, removed)
    assert result['primary']['decision'] == 'label_gain'
    after = result['primaryAfterConstructionRemoval']
    assert after['decision'] == 'insufficient_evidence' and after['b'] == 4 and after['removedIds'] == [errors[0]]
    package, rows, rules = synthetic_results(deposit, common=22)
    result = so.summarize(package, rows, rules, [{'id': errors[0], 'reason': 'x'}])
    assert result['primary']['decision'] == 'simple_sufficient'
    # 门槛不按剔除后的条数重算：剩 21/23 检出，仍按 22 判。
    assert result['primaryAfterConstructionRemoval']['U-C1']['detected'] == 21
    assert result['primaryAfterConstructionRemoval']['decision'] == 'no_gain'
    assert result['thresholds']['simpleSufficientMinDetected'] == 22


@pytest.mark.parametrize('value', [
    {'items': [{'id': 'nope', 'reason': 'x'}]}, {'items': []}, {'items': [{'id': None, 'reason': 'x'}]},
    {'items': [{'id': 'A', 'reason': ' '}]}, {'other': []},
])
def test_construction_error_file_validated(tmp_path, deposit, value):
    package = si.load(deposit / 'package.json')
    for entry in value.get('items', []):
        if entry['id'] == 'A':
            entry['id'] = package['items'][0]['id']
    path = tmp_path / 'errors.json'
    si.write_json(path, value)
    with pytest.raises(ValueError, match='构造错误'):
        so.load_construction_errors(path, package)


def test_construction_errors_through_analyze(tmp_path, deposit):
    _, output, _ = execute(tmp_path, deposit)
    package = si.load(deposit / 'package.json')
    path = tmp_path / 'errors.json'
    first, second = package['items'][0]['id'], package['items'][1]['id']
    si.write_json(path, {'items': [{'id': first, 'reason': 'x'}, {'id': first, 'reason': 'y'}]})
    with pytest.raises(ValueError, match='重复'):
        so.analyze(output, tmp_path / 'a.json', path)
    si.write_json(path, {'items': [{'id': second, 'reason': '离线测试'}]})
    result = so.analyze(output, tmp_path / 'b.json', path)
    assert result['primaryAfterConstructionRemoval']['removedIds'] == [second]


def test_unavailable_and_insufficient_counts_and_pair_changes(deposit):
    package, rows, rules = synthetic_results(deposit, b=5, common=1)
    error = next(i for i in package['items'] if i['expected'] == 'not_supported')
    control = next(i for i in package['items'] if i['expected'] == 'supported')
    indexed = {(r['itemId'], r['arm']): r for r in rows}
    indexed[error['id'], 'U-C1'].update(status='parse_error', result=None)
    indexed[error['id'], 'L-C1']['result']['verdict'] = 'insufficient'
    indexed[control['id'], 'U-C1']['result']['verdict'] = 'insufficient'
    result = so.summarize(package, rows, rules)
    assert result['arms']['U-C1']['detected'] == 0
    assert result['arms']['U-C1']['unavailable'] == 1
    assert result['arms']['L-C1']['detected'] == 6
    assert result['primary']['b'] == 6 and result['primary']['c'] == 0
    assert result['primary']['decision'] == 'label_gain'
    assert result['primary']['containsUnavailable'] is True
    assert result['primary']['unavailableErrorIds'] == [error['id']]
    assert result['pairedC0toC1']['U']['unavailable'] == [error['id']]
    assert result['pairedC0toC1']['U']['newFalsePositives'] == [control['id']]
    indexed[error['id'], 'U-C1'].update(status='ok', result={'verdict': 'supported', 'reason': 'lost'})
    indexed[control['id'], 'L-C0']['result']['verdict'] = 'contradicted'
    another = [i for i in package['items'] if i['expected'] == 'not_supported'][6]
    indexed[another['id'], 'U-C1']['result']['verdict'] = 'contradicted'
    result = so.summarize(package, rows, rules)
    assert result['pairedC0toC1']['U']['lostDetections'] == [error['id']]
    assert result['pairedC0toC1']['U']['newDetections'] == [another['id']]
    assert result['pairedC0toC1']['L']['reducedFalsePositives'] == [control['id']]
    assert result['effects']['criterionU'] == {'detected': 0, 'falsePositives': 1}
    assert result['effects']['criterionL'] == {'detected': 0, 'falsePositives': -1}
    assert result['effects']['interaction'] == {'detected': 0, 'falsePositives': -2}


def test_false_positives_split_by_control_kind(deposit):
    package, rows, rules = synthetic_results(deposit, uf=27, lf=27)
    result = so.summarize(package, rows, rules)
    controls = [i for i in package['items'] if i['expected'] == 'supported']
    expected = {
        'singleParagraphFirst': sum(i['type'] == 'control_pinpoint' and i['citedArticleParagraphs'] == 1 for i in controls),
        'multiParagraphPinpoint': sum(i['type'] == 'control_pinpoint' and i['citedArticleParagraphs'] > 1 for i in controls),
        'articleOnly': sum(i['type'] == 'control_article' for i in controls),
    }
    assert sum(expected.values()) == 27 and all(expected.values())
    for arm in so.ARMS:
        split = result['arms'][arm]['falsePositivesByControlKind']
        assert {k: v['falsePositives'] for k, v in split.items()} == expected
