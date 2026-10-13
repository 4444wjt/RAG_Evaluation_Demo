"""鲁棒性体检：离线测试资料构造、统计口径、授权约束与被测应用接口，不调用真实模型。"""
import importlib.util
import json
from pathlib import Path
import re

import pytest

from backend.core import robustness as rb
from backend.core.attribution import is_refusal

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('robustness_check', ROOT / 'scripts' / 'robustness_check.py')
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

COMMON = '存款保险基金管理机构'
P_LIMIT = '第五条　存款保险实行限额偿付，最高偿付限额为人民币50万元。'
P_RATE = f'第九条　存款保险费率由基准费率和风险差别费率构成，由{COMMON}制定。'
P_FEE = f'第十条　投保机构应当按照{COMMON}的规定，每6个月交纳一次保费。'
P_CLAIM = f'第十九条　存款人有权要求{COMMON}偿付被保险存款。'
P_PAY = f'第二十条　{COMMON}应当在7个工作日内足额偿付存款。'
P_SCOPE = f'第四条　被保险存款不包括金融机构同业存款，由{COMMON}认定。'
P_NOTE = f'来源说明：节选自公开行政法规，{COMMON}负责解释。'
REFUSAL = '根据提供的文档，无法回答该问题。'


def items():
    return [
        {'id': 'A', 'question': '最高偿付限额是多少？', 'answer': '最高偿付限额为人民币50万元。',
         'groundTruth': '人民币50万元。', 'contexts': [P_LIMIT, P_RATE, P_CLAIM]},
        {'id': 'B', 'question': '费率由哪两部分构成？', 'answer': '存款保险费率由基准费率和风险差别费率构成。',
         'groundTruth': None, 'contexts': [P_RATE, P_FEE, P_NOTE]},
        {'id': 'C', 'question': '多久交纳一次保费？', 'answer': REFUSAL,
         'groundTruth': '投保机构每6个月交纳一次保费。', 'contexts': [P_FEE, P_LIMIT, P_PAY]},
        {'id': 'D', 'question': '多久内偿付？', 'answer': '管理机构应当在7个工作日内足额偿付存款。',
         'groundTruth': None, 'contexts': [P_PAY, P_SCOPE, P_NOTE]},
    ]


# ---------- 依据定位 ----------

def test_shared_spans_takes_longest_first_and_strips_punctuation():
    spans = rb.shared_spans('答：最高偿付限额为人民币50万元。另：费率构成', P_LIMIT + P_RATE, 5)
    assert spans[0] == '最高偿付限额为人民币50万元'
    assert all(len(s) >= 5 and s[0] not in rb.PUNCT and s[-1] not in rb.PUNCT for s in spans)
    assert rb.shared_spans('完全无关的一句话', P_LIMIT, 5) == []


def test_common_names_are_not_evidence():
    pool = rb.passage_pool(items())
    evidence = rb.locate_evidence({'answer': f'{COMMON}负责。', 'groundTruth': None, 'contexts': [P_RATE, P_FEE]}, pool)
    assert evidence == {'spans': [], 'supportIndices': []}
    evidence = rb.locate_evidence(items()[0], pool)
    assert '最高偿付限额为人民币50万元' in evidence['spans'] and evidence['supportIndices'] == [0]


def test_refusal_uses_reference_answer_to_locate_evidence():
    evidence = rb.locate_evidence(items()[2], rb.passage_pool(items()))
    assert evidence['supportIndices'] == [0] and any('6个月交纳一次保费' in s for s in evidence['spans'])


def test_distractors_exclude_related_passages_but_not_shared_names():
    pool = rb.passage_pool(items())
    item = items()[0]
    candidates = rb.distractors(item, pool, rb.locate_evidence(item, pool)['spans'])
    assert P_LIMIT not in candidates and all(p not in item['contexts'] for p in candidates)
    assert P_FEE in candidates  # 只共享到处都有的名称，仍算无关资料


# ---------- 五种条件 ----------

def test_shuffle_changes_order_only():
    built = rb.shuffled(items()[0], 1)
    assert sorted(built['contexts']) == sorted(items()[0]['contexts']) and built['contexts'] != items()[0]['contexts']
    assert rb.shuffled({'id': 'x', 'contexts': ['同一段', '同一段']}, 1) == {'skipped': '资料少于两条不同的内容，无法打乱'}


def test_noise_inserts_unrelated_passages():
    item = items()[0]
    built = rb.noised(item, [P_FEE, P_PAY, P_SCOPE], seed=1, count=2)
    assert len(built['contexts']) == 5 and [c for c in built['contexts'] if c in item['contexts']] == item['contexts']
    assert sorted(built['contexts'][i] for i in built['inserted']) == sorted(set(built['contexts']) - set(item['contexts']))
    assert 'skipped' in rb.noised(item, [P_FEE], seed=1, count=2)


def test_remove_replaces_every_support_passage_and_keeps_count():
    item = items()[0]
    built = rb.removed(item, [P_FEE, P_PAY], [0], seed=1)
    assert len(built['contexts']) == 3 and P_LIMIT not in built['contexts'] and built['removed'] == [0]
    assert rb.removed(item, [P_FEE], [], seed=1) == {'skipped': '未能定位依据资料'}
    assert 'skipped' in rb.removed(item, [], [0], seed=1)


@pytest.mark.parametrize('passage, span, expected, edit', [
    (P_LIMIT, '最高偿付限额为人民币50万元', '最高偿付限额为人民币100万元', ('50万元', '100万元')),
    ('本办法自2026年起施行', '本办法自2026年起施行', '本办法自2027年起施行', ('2026年', '2027年')),
    ('费率上浮0.5%执行', '费率上浮0.5%执行', '费率上浮1.0%执行', ('0.5%', '1.0%')),
    (P_SCOPE, '被保险存款不包括金融机构同业存款', '被保险存款包括金融机构同业存款', ('不包括', '包括')),
    (P_PAY, '应当在7个工作日内', '无需在14个工作日内', ('应当', '无需')),
])
def test_counterfactual_edits_numbers_and_modal_words_inside_evidence(passage, span, expected, edit):
    built = rb.counterfactual({'contexts': [passage, '无关资料应当保持原样，金额50万元'], 'id': 'x'}, [span])
    assert expected in built['contexts'][0]
    assert built['contexts'][1] == '无关资料应当保持原样，金额50万元'  # 依据片段之外一律不改
    assert (built['edits'][0]['before'], built['edits'][0]['after']) == edit


def test_counterfactual_skips_ordinals_and_reports_when_nothing_to_edit():
    built = rb.counterfactual({'contexts': ['依照第5条办理'], 'id': 'x'}, ['依照第5条办理'])
    assert built == {'skipped': '依据中没有可改的数字或情态词'}
    assert rb.counterfactual({'contexts': [P_LIMIT], 'id': 'x'}, []) == {'skipped': '未能定位依据资料'}


def test_plan_is_deterministic_and_skips_judging_refusals():
    first, second = rb.plan(items(), 7, refusal=is_refusal), rb.plan(items(), 7, refusal=is_refusal)
    assert first == second
    assert rb.plan(items(), 8, refusal=is_refusal)['items'] != first['items']
    sides = {(j['item'], j['side']) for j in first['jobs']}
    assert ('C', 'judge') not in sides and ('C', 'generator') in sides
    for item in first['items']:
        for condition, built in item['conditions'].items():
            planned = any(j['item'] == item['id'] and j['condition'] == condition for j in first['jobs'])
            assert planned == ('contexts' in built)
    with pytest.raises(ValueError, match='编号重复'):
        rb.plan(items() + items()[:1], 7)


def test_inputs_are_not_modified():
    original = items()
    snapshot = json.dumps(original, ensure_ascii=False)
    rb.plan(original, 7, refusal=is_refusal)
    assert json.dumps(original, ensure_ascii=False) == snapshot


# ---------- 统计口径 ----------

def test_trace_outcome_and_adoption():
    assert rb.trace_outcome(None) == 'failed'
    assert rb.trace_outcome({'trace_status': 'parse_error', 'statements': []}) == 'failed'
    assert rb.trace_outcome({'trace_status': 'ok', 'statements': [{'verdict': 'supported'}, {'verdict': 'fabrication'}]}) == 'flagged'
    assert rb.trace_outcome({'trace_status': 'ok', 'statements': [{'verdict': 'supported'}]}) == 'clean'
    assert rb.trace_outcome({'trace_status': 'filtered_all', 'statements': []}) == 'empty'
    edits = [{'kind': 'number', 'before': '50万元', 'after': '100万元'}]
    assert rb.adoption('限额为100万元', edits, False) == 'followed'
    assert rb.adoption('限额为50万元', edits, False) == 'kept'
    assert rb.adoption('原为50万元，现为100万元', edits, False) == 'both'
    assert rb.adoption('无关', edits, False) == 'neither'
    assert rb.adoption(REFUSAL, edits, True) == 'refused'
    assert rb.adoption('任何回答', [{'kind': 'modal', 'before': '应当', 'after': '无需'}], False) == 'not_applicable'


def _trace(*verdicts, status='ok'):
    return {'trace_status': status, 'statements': [{'text': 't', 'verdict': v, 'evidence': ''} for v in verdicts],
            'trace_diagnostics': {'rejected_statements': []}}


def test_summary_counts_against_same_run_baseline():
    package = rb.plan(items(), 7, refusal=is_refusal)['items']
    records = [
        {'item': 'A', 'side': 'judge', 'condition': 'original', 'status': 'ok', 'trace': _trace('supported')},
        {'item': 'A', 'side': 'judge', 'condition': 'shuffle', 'status': 'ok', 'trace': _trace('supported')},
        {'item': 'A', 'side': 'judge', 'condition': 'noise', 'status': 'ok', 'trace': _trace('fabrication')},
        {'item': 'A', 'side': 'judge', 'condition': 'remove', 'status': 'ok', 'trace': _trace('fabrication')},
        {'item': 'A', 'side': 'judge', 'condition': 'counterfactual', 'status': 'ok', 'trace': _trace('fact_error')},
        {'item': 'D', 'side': 'judge', 'condition': 'original', 'status': 'ok', 'trace': _trace('fabrication')},
        {'item': 'D', 'side': 'judge', 'condition': 'remove', 'status': 'ok', 'trace': _trace('fabrication')},
        {'item': 'B', 'side': 'judge', 'condition': 'original', 'status': 'ok', 'trace': _trace(status='call_error')},
        {'item': 'A', 'side': 'generator', 'condition': 'original', 'status': 'ok', 'answer': '限额为50万元', 'trace': _trace('supported')},
        {'item': 'A', 'side': 'generator', 'condition': 'remove', 'status': 'ok', 'answer': REFUSAL, 'trace': _trace(status='empty_answer')},
        {'item': 'A', 'side': 'generator', 'condition': 'counterfactual', 'status': 'ok', 'answer': '限额为100万元', 'trace': _trace('supported')},
        {'item': 'C', 'side': 'generator', 'condition': 'remove', 'status': 'ok', 'answer': '每6个月交纳', 'trace': _trace('fabrication')},
        {'item': 'C', 'side': 'generator', 'condition': 'noise', 'status': 'call_error'},
        {'item': 'C', 'side': 'generator', 'condition': 'counterfactual', 'status': 'ok', 'answer': '每6个月交纳', 'trace': _trace('fact_error')},
    ]
    summary = rb.summarize(package, records, is_refusal)
    judge, generator = summary['judge'], summary['generator']
    assert judge['original']['flagged']['count'] == 1 and judge['original']['flagged']['total'] == 2  # B 调用失败不计
    assert judge['shuffle']['unchanged']['rate'] == 1 and judge['noise']['newlyFlagged'] == 1
    # D 在原样资料下已被标出，不计入「移除依据后是否标出」
    assert judge['remove']['flagged'] == rb.rate(1, 1) and judge['remove']['baselineAlreadyFlagged'] == 1
    assert judge['counterfactual']['contradicted']['count'] == 1 and judge['failed'] == 1
    assert judge['counterfactual']['byEdit'] == {'number': rb.rate(1, 1), 'modal': rb.rate(0, 0)}
    assert generator['remove']['refused'] == rb.rate(1, 2) and generator['remove']['answeredFlagged'] == rb.rate(1, 1)
    assert generator['remove']['answeredNotFlagged'] == 0
    assert generator['remove']['newlyRefused'] == 1 and generator['noise']['answerFailed'] == 1
    adopted = generator['counterfactual']['adoption']
    assert adopted['followed'] == rb.rate(1, 2) and adopted['kept'] == rb.rate(1, 2)
    assert generator['counterfactual']['flaggedWhenKept'] == rb.rate(1, 1)
    assert summary['built']['original'] == 4 and set(summary['skipped']) == set(rb.CONDITIONS)


# ---------- 命令行：输入、授权与运行 ----------

class Reply:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {'input_tokens': 1000, 'output_tokens': 100}
        self.response_metadata = {'model_name': 'fake-model'}


ANSWER_PATTERNS = {'最高偿付限额是多少？': r'最高偿付限额为人民币\d+万元',
                   '费率由哪两部分构成？': r'存款保险费率由基准费率和风险差别费率构成',
                   '多久交纳一次保费？': r'每\d+个月交纳一次保费',
                   '多久内偿付？': r'(?:应当|无需)在\d+个工作日内足额偿付存款'}


class IdealLLM:
    """问答：资料里有答案就照抄（跟随改动），没有就拒答。核对：回答在资料中原样出现即支持，数字或情态词不符即矛盾，否则无依据。"""

    def __init__(self, fail=False):
        self.prompts, self.fail = [], fail

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise TimeoutError('timeout')
        if '事实核查员' not in prompt:
            question = prompt.split('问题：', 1)[1].split('\n', 1)[0].strip()
            found = re.search(ANSWER_PATTERNS[question], prompt.split('问题：', 1)[0])
            return Reply(found.group(0) + '。' if found else REFUSAL)
        contexts = json.loads(prompt.split('【上下文】\n', 1)[1].split('\n【回答】', 1)[0])
        answer = prompt.split('【回答】\n', 1)[1].split('\n【参考答案', 1)[0].strip()
        if is_refusal(answer):
            return Reply('{"statements": []}')
        claim = answer.rstrip('。').replace('管理机构', '')
        support = next((c for c in contexts if claim in c), None)
        if support:
            return Reply(json.dumps({'statements': [{'text': answer, 'verdict': 'supported', 'evidence': claim}]}, ensure_ascii=False))
        # 同一句话只在数字或情态词上不同，即判为矛盾，并引用资料中的那句原文
        pattern, last = '', 0
        for match in rb._TOKEN.finditer(claim):
            word = match.group(0)
            pattern += re.escape(claim[last:match.start()])
            pattern += r'\d+(?:\.\d+)?' if match.group('number') else f'(?:{word}|{rb.FLIPS[word]})'
            last = match.end()
        pattern += re.escape(claim[last:])
        rival = next((m.group(0) for c in contexts for m in [re.search(pattern, c)] if m), None)
        verdict = {'text': answer, 'verdict': 'fact_error', 'evidence': rival} if rival else {'text': answer, 'verdict': 'fabrication', 'evidence': ''}
        return Reply(json.dumps({'statements': [verdict]}, ensure_ascii=False))


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')
    return path


def jsonl_rows():
    return [{'id': i['id'], 'question': i['question'], 'contexts': i['contexts'], 'answer': i['answer'],
             'ground_truth': i['groundTruth']} for i in items()]


def test_load_items_from_platform_result_keeps_positive_rows(tmp_path):
    rows = [{'question': 'q1', 'answer': 'a', 'ground_truth': 'g', 'contexts': ['c'], 'case_type': 'positive'},
            {'question': 'q2', 'answer': 'a', 'ground_truth': 'g', 'contexts': ['c'], 'case_type': 'negative'},
            {'question': 'q3', 'answer': 'a', 'ground_truth': 'g', 'contexts': ['c'], 'case_type': 'positive'}]
    result = {'summary': [{'model': 'm1', 'faithfulness': None}, {'model': 'm2', 'faithfulness': 0.7}],
              'model_dfs': {'m1': [], 'm2': rows}}
    path = tmp_path / 'api-response.json'
    path.write_text(json.dumps(result), encoding='utf-8')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'cases': [{'rowIndex': 0, 'id': 'D01'}]}), encoding='utf-8')
    source, loaded = rc.load_items(path, manifest=manifest)
    assert source == {'format': 'platform', 'model': 'm2'}
    assert [(i['id'], i['rowIndex']) for i in loaded] == [('D01', 0), ('row-2', 2)]


@pytest.mark.parametrize('row, message', [
    ({'question': 'q', 'contexts': []}, 'contexts'),
    ({'question': 'q', 'contexts': ['c']}, '至少要有'),
    ({'contexts': ['c'], 'answer': 'a'}, 'question'),
])
def test_jsonl_rows_are_validated(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        rc.load_items(write_jsonl(tmp_path / 'in.jsonl', [row]))


def prepared(tmp_path, **kwargs):
    work = tmp_path / 'work'
    package = rc.prepare(write_jsonl(tmp_path / 'in.jsonl', jsonl_rows()), work, seed=7, **kwargs)
    return work, package


def test_prepare_estimates_and_authorization_binds_package_and_code(tmp_path, monkeypatch):
    work, package = prepared(tmp_path)
    assert package['estimate']['paidCalls'] == sum(1 if j['side'] == 'judge' else 2 for j in package['jobs'])
    with pytest.raises(PermissionError, match='没有授权'):
        rc.run(work, IdealLLM())
    with pytest.raises(ValueError, match='预算'):
        rc.authorize(work, 'tester', 0)
    rc.authorize(work, 'tester', 5)
    monkeypatch.setattr(rc, 'tool_hashes', lambda root=rc.ROOT: {'changed': 'x'})
    with pytest.raises(PermissionError, match='运行代码'):
        rc.run(work, IdealLLM())
    monkeypatch.undo()
    (work / 'package.json').write_text((work / 'package.json').read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(PermissionError, match='哈希'):
        rc.run(work, IdealLLM())


def test_run_analyze_and_archive_with_an_ideal_model(tmp_path):
    work, package = prepared(tmp_path)
    rc.authorize(work, 'tester', 5)
    llm = IdealLLM()
    result = rc.run(work, llm)
    assert result['completed'] == result['total'] == len(package['jobs']) and result['stopped'] is None
    assert result['paidCalls'] == len(llm.prompts) == package['estimate']['paidCalls']
    summary = rc.archive(work, tmp_path / 'archive')
    judge, generator = summary['judge'], summary['generator']
    assert judge['shuffle']['unchanged']['rate'] == 1 and judge['noise']['unchanged']['rate'] == 1
    assert judge['remove']['flagged']['rate'] == 1 and judge['counterfactual']['contradicted']['rate'] == 1
    assert generator['remove']['refused']['rate'] == 1
    assert generator['counterfactual']['adoption']['followed']['rate'] == 1
    for name in ('summary.json', 'items.json', 'package.json', 'authorization.json', 'records.jsonl'):
        assert (tmp_path / 'archive' / name).exists()
    # 归档的材料包与记录足以离线重算汇总
    recomputed = rb.summarize(rc.read_json(tmp_path / 'archive' / 'package.json')['items'],
                              rc.read_records(tmp_path / 'archive'), is_refusal)
    recomputed.pop('rows')
    assert {k: summary[k] for k in recomputed} == recomputed
    assert '移除依据后拒答' in rc.report(summary)


def test_run_stops_at_limits_resumes_and_never_retries(tmp_path):
    work, package = prepared(tmp_path)
    auth = rc.authorize(work, 'tester', 5)
    auth['maxPaidCalls'] = 3
    rc.write_json(work / 'authorization.json', auth)
    failing = IdealLLM(fail=True)
    first = rc.run(work, failing)
    assert first['stopped'] == '达到请求数上限' and first['paidCalls'] <= 3
    records = rc.read_records(work)
    assert all(len(r['calls']) == 1 for r in records)  # 失败的请求只尝试一次
    auth['maxPaidCalls'] = package['estimate']['paidCalls']
    rc.write_json(work / 'authorization.json', auth)
    second = rc.run(work, IdealLLM())
    assert second['completed'] == len(package['jobs']) and len(rc.read_records(work)) == len(package['jobs'])
    assert len({r['id'] for r in rc.read_records(work)}) == len(package['jobs'])


def test_run_stops_when_budget_cannot_cover_next_job(tmp_path):
    work, _ = prepared(tmp_path)
    auth = rc.authorize(work, 'tester', 5)
    auth['budgetCny'] = 0.001
    rc.write_json(work / 'authorization.json', auth)
    result = rc.run(work, IdealLLM())
    assert result['stopped'] == '剩余预算不足以完成下一项' and result['completed'] == 0


ENDPOINT = 'https://rag-app.example/answer'


class FakeApp:
    """替换 urllib.request.urlopen 的被测应用：在内存中按传入资料作答，不打开任何网络连接。"""

    def __init__(self, answer_key='answer'):
        self.requests, self.answer_key = [], answer_key

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode('utf-8'))
        self.requests.append({'url': request.full_url, 'method': request.get_method(), 'body': body,
                              'authorization': request.get_header('Authorization'), 'timeout': timeout})
        found = re.search(r'最高偿付限额为人民币\d+万元', ''.join(body['contexts']))
        payload = json.dumps({self.answer_key: found.group(0) + '。' if found else REFUSAL}, ensure_ascii=False).encode('utf-8')

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return payload

        return Response()


def test_generator_endpoint_receives_the_perturbed_contexts(tmp_path, monkeypatch):
    app = FakeApp()
    monkeypatch.setattr(rc.urllib.request, 'urlopen', app)
    monkeypatch.setenv('ROBUSTNESS_TEST_TOKEN', 'test-token')
    work, package = prepared(tmp_path, endpoint=ENDPOINT, token_env='ROBUSTNESS_TEST_TOKEN', limit=2)
    assert package['generator'] == {'kind': 'http', 'endpoint': ENDPOINT, 'tokenEnv': 'ROBUSTNESS_TEST_TOKEN',
                                    'timeoutSeconds': rc.HTTP_TIMEOUT}
    generator_jobs = [j for j in package['jobs'] if j['side'] == 'generator']
    assert package['estimate']['httpCalls'] == len(generator_jobs)
    rc.authorize(work, 'tester', 5)
    result = rc.run(work, IdealLLM())
    assert result['httpCalls'] == len(generator_jobs) == len(app.requests)
    items_by_id = {item['id']: item for item in package['items']}
    assert [r['body'] for r in app.requests] == [
        {'question': items_by_id[j['item']]['question'], 'contexts': items_by_id[j['item']]['conditions'][j['condition']]['contexts']}
        for j in generator_jobs]
    assert all(r['method'] == 'POST' and r['url'] == ENDPOINT and r['timeout'] == rc.HTTP_TIMEOUT for r in app.requests)
    assert all(r['authorization'] == 'Bearer test-token' for r in app.requests)
    saved = (work / 'records.jsonl').read_text(encoding='utf-8') + (work / 'package.json').read_text(encoding='utf-8')
    assert 'test-token' not in saved
    answers = {r['condition']: r['answer'] for r in rc.read_records(work) if r['side'] == 'generator' and r['item'] == 'A'}
    assert answers['remove'] == REFUSAL and '100万元' in answers['counterfactual']


def test_endpoint_errors_are_recorded_once_without_retry(tmp_path, monkeypatch):
    app = FakeApp(answer_key='text')
    monkeypatch.setattr(rc.urllib.request, 'urlopen', app)
    work, package = prepared(tmp_path, endpoint=ENDPOINT, limit=1)
    rc.authorize(work, 'tester', 5)
    rc.run(work, IdealLLM())
    generator = [r for r in rc.read_records(work) if r['side'] == 'generator']
    assert len(app.requests) == len(generator) > 0
    assert all(r['status'] == 'call_error' and r['errorType'] == 'ValueError' for r in generator)
    assert all([c['kind'] for c in r['calls']] == ['http'] for r in generator)


def test_endpoint_token_must_be_set(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.urllib.request, 'urlopen', FakeApp())
    monkeypatch.delenv('ROBUSTNESS_MISSING_TOKEN', raising=False)
    work, _ = prepared(tmp_path, endpoint=ENDPOINT, token_env='ROBUSTNESS_MISSING_TOKEN', limit=1)
    rc.authorize(work, 'tester', 5)
    rc.run(work, IdealLLM())
    generator = [r for r in rc.read_records(work) if r['side'] == 'generator']
    assert generator and all(r['errorType'] == 'RuntimeError' for r in generator)


def test_endpoint_must_be_http(tmp_path):
    with pytest.raises(ValueError, match='http'):
        prepared(tmp_path, endpoint='file:///etc/passwd')
