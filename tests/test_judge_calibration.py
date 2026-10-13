"""裁判判错校准工具：盲标注材料、未标注时的敏感性分析、标注结果分析。测试用的标签都是合成的，只检验计算流程。"""
import json
from pathlib import Path

import jsonschema
import pytest

from backend.core import judge_error as je
from tools.competition import build_replay_fixture as replay
from tools.competition import judge_calibration as jc

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas' / 'judge-annotation.v1.schema.json').read_text(encoding='utf-8'))
QUESTIONS = 20


@pytest.fixture(scope='module')
def built():
    return jc.build_package()


def _export(package, labels, annotator='A', from_question=()):
    return {'schema': jc.ANNOTATION_SCHEMA, 'packageId': package['packageId'], 'annotator': annotator,
            'notAuthor': True, 'savedAt': '2026-10-14T08:00:00Z',
            'labels': {sid: {'label': label, **({'fromQuestion': True} if sid in from_question else {})}
                       for sid, label in labels.items()}}


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


def _as_judge(key, override=None):
    override = override or {}
    return {k['id']: override.get(k['id'], '支持' if k['judge'] == 1 else '无依据') for k in key}


def _only(key, labels, questions):
    """只保留指定题（Q 编号）的标签，模拟部分标注。"""
    return {k['id']: labels[k['id']] for k in key if k['question'] in questions}


def _qids(*numbers):
    return {f'Q{n:02d}' for n in numbers}


def _analyze(tmp_path, package, *label_sets):
    paths = [_write(tmp_path, f'{name}.json', _export(package, labels, annotator=name))
             for name, labels in zip('AB', label_sets)]
    return jc.analyze(paths)


# ---------- 标注材料与敏感性分析 ----------

def test_committed_package_matches_the_evidence(built):
    package, _ = built
    assert json.loads(jc.PACKAGE.read_text(encoding='utf-8')) == package


def test_package_is_blind_and_complete(built):
    package, key = built
    assert len(package['questions']) == QUESTIONS and len(package['items']) == 52
    assert [item['id'] for item in package['items']] == [f'S{i:02d}' for i in range(1, 53)]
    assert [q['id'] for q in package['questions']] == [f'Q{i:02d}' for i in range(1, QUESTIONS + 1)]
    # 只给题目、资料与陈述：不含裁判判定、原题号、回答与参考答案
    assert all(set(q) == {'id', 'question', 'contexts'} for q in package['questions'])
    assert all(set(item) <= {'id', 'question', 'statement', 'note'} for item in package['items'])
    assert set(package) == {'schema', 'packageId', 'source', 'guide', 'labels', 'rules', 'questions', 'items'}
    # 题目顺序已打乱，不按原题号排列
    assert [k['caseId'] for k in key][:3] != ['D01', 'D01', 'D01']
    assert sum(k['judge'] for k in key) == 43
    noted = [item for item in package['items'] if 'note' in item]
    assert len(noted) == 1 and jc.REDACTED in noted[0]['statement']
    # packageId 绑定题目与陈述，任何一处改动都会改变它
    body = {'questions': package['questions'], 'items': package['items']}
    assert package['packageId'] == jc.hashlib.sha256(jc._canonical(body)).hexdigest()


def test_status_without_annotations():
    report = jc.status()
    assert report['judgeValue'] == 0.7375 and report['statements'] == 52 and report['judgedSupported'] == 43
    assert report['flips']['overall']['flips'] == 2
    answered = report['flips']['answeredOnly']
    assert answered['flips'] == 5 and answered['after'] == pytest.approx(0.8)
    assert sorted((s['caseId'], s['statement']) for s in answered['statements']) == [
        ('C02', 2), ('C02', 5), ('C02', 6), ('D01', 1), ('D09', 1)]
    assert len(report['unsupported']) == 9 and sum(s['refusal'] for s in report['unsupported']) == 4
    assert report['tipping'][0] == {'specificity': 1.0, 'sensitivity': pytest.approx(0.921875)}
    assert report['sampling']['state'] == je.UNSURE and report['sampling']['nEff'] == pytest.approx(23.102, abs=1e-3)


# ---------- 标注结果的格式：schema 与工具一致 ----------

def test_exported_annotation_conforms_to_schema(built, tmp_path):
    package, key = built
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    export = _export(package, _as_judge(key), from_question={key[0]['id']})
    export['labels'][key[1]['id']]['note'] = '见第五条'
    jsonschema.validate(export, SCHEMA)
    loaded = jc.load_annotation(_write(tmp_path, 'a.json', export), package)
    assert loaded['fromQuestion'] == {key[0]['id']} and loaded['notes'] == {key[1]['id']: '见第五条'}
    # 前端导出的样例（与 frontend/src/test/annotate.test.tsx 的导出结果同形）也符合 schema
    jsonschema.validate({'schema': 'judge-annotation.v1', 'packageId': package['packageId'], 'annotator': 'A',
                         'notAuthor': True, 'savedAt': '2026-10-14T08:00:00.000Z',
                         'labels': {'S01': {'label': '支持', 'fromQuestion': True, 'note': '见第五条'}}}, SCHEMA)


INVALID = {
    '格式名不对': lambda d: {**d, 'schema': 'judge-annotation.v0'},
    '多出字段': lambda d: {**d, 'verdicts': {}},
    'packageId 不是哈希': lambda d: {**d, 'packageId': 'abc'},
    '缺少代号': lambda d: {k: v for k, v in d.items() if k != 'annotator'},
    '代号为空白': lambda d: {**d, 'annotator': '  '},
    '代号过长': lambda d: {**d, 'annotator': 'x' * 41},
    '未确认非作者': lambda d: {**d, 'notAuthor': False},
    '缺少非作者确认': lambda d: {k: v for k, v in d.items() if k != 'notAuthor'},
    '缺少 labels': lambda d: {k: v for k, v in d.items() if k != 'labels'},
    '标签不在四类之内': lambda d: {**d, 'labels': {'S01': {'label': '对'}}},
    '缺少标签': lambda d: {**d, 'labels': {'S01': {'note': '没选'}}},
    '陈述编号格式不对': lambda d: {**d, 'labels': {'S1': {'label': '支持'}}},
    'fromQuestion 不是布尔值': lambda d: {**d, 'labels': {'S01': {'label': '支持', 'fromQuestion': 'yes'}}},
    '备注过长': lambda d: {**d, 'labels': {'S01': {'label': '支持', 'note': '字' * 501}}},
    '标签含未知字段': lambda d: {**d, 'labels': {'S01': {'label': '支持', 'judge': 1}}},
}


@pytest.mark.parametrize('case', INVALID)
def test_files_the_schema_rejects_are_rejected_by_the_tool(built, tmp_path, case):
    package, key = built
    bad = INVALID[case](_export(package, _as_judge(key)))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)
    with pytest.raises(ValueError):
        jc.load_annotation(_write(tmp_path, 'bad.json', bad), package)


def test_tool_also_rejects_other_packages_and_unknown_statements(built, tmp_path):
    package, key = built
    good = _export(package, _as_judge(key))
    with pytest.raises(ValueError, match='packageId'):
        jc.analyze([_write(tmp_path, 'a.json', {**good, 'packageId': '0' * 64})])
    with pytest.raises(ValueError, match='未知'):
        jc.analyze([_write(tmp_path, 'a.json', {**good, 'labels': {'S99': {'label': '支持'}}})])
    with pytest.raises(ValueError, match='代号相同'):
        jc.analyze([_write(tmp_path, 'a.json', good), _write(tmp_path, 'b.json', good)])


# ---------- 分母一致性 ----------

def test_annotator_who_agrees_with_the_judge_reproduces_the_gate(built, tmp_path):
    package, key = built
    result = _analyze(tmp_path, package, _as_judge(key))
    assert result['counts'] == {'resolved': 52, 'unresolved': 0, 'unlabeled': 0}
    assert result['calibration']['q1'] == 1.0 and result['calibration']['q0'] == 1.0
    estimate = result['estimate']
    assert estimate['method'] == 'human' and estimate['unresolved'] == 0
    assert estimate['point'] == [pytest.approx(0.7375)] * 2 and estimate['lower'] == estimate['upper']
    assert estimate['state'] == je.UNSURE and estimate['pointPasses'] is False
    # 与第四问「只计题目抽样」的区间相同：人工完全同意裁判时，区间不变
    assert estimate['interval'] == pytest.approx(jc.status()['sampling']['interval'])
    assert not any(row['disagreesWithJudge'] for row in result['rows'])


def test_per_question_denominators_match_the_gate(built, tmp_path):
    package, key = built
    result = _analyze(tmp_path, package, _as_judge(key))
    gate = {row['rowIndex']: row for row in replay.build()['rows']}
    by_question = {q['id']: q for q in result['questions']}
    assert [q['id'] for q in result['questions']] == [q['id'] for q in package['questions']]
    for qid, question in by_question.items():
        row = gate[next(k['rowIndex'] for k in key if k['question'] == qid)]
        assert question['statements'] == row['total']
        assert question['judge'] == pytest.approx(row['faithfulness'])
        assert question['humanLower'] == question['humanUpper'] == pytest.approx(row['supported'] / row['total'])
    assert sum(q['statements'] for q in result['questions']) == 52


def test_unresolved_statements_keep_their_denominator(built, tmp_path):
    """「无法判断」不从分母中剔除：该题的人工值在 (支持数−1)/n 与 支持数/n 之间，而不是 (支持数−1)/(n−1)。"""
    package, key = built
    target = next(k for k in key if k['judge'] == 1 and sum(o['question'] == k['question'] for o in key) >= 3)
    n = sum(k['question'] == target['question'] for k in key)
    supported = sum(k['judge'] for k in key if k['question'] == target['question'])
    result = _analyze(tmp_path, package, _as_judge(key, {target['id']: '无法判断'}))
    question = next(q for q in result['questions'] if q['id'] == target['question'])
    assert question['statements'] == n
    assert question['humanLower'] == pytest.approx((supported - 1) / n)
    assert question['humanUpper'] == pytest.approx(supported / n)
    estimate = result['estimate']
    assert result['counts']['unresolved'] == estimate['unresolved'] == 1
    assert estimate['point'][0] == pytest.approx(0.7375 - 1 / (QUESTIONS * n))
    assert estimate['point'][1] == pytest.approx(0.7375)
    assert estimate['interval'] is None and estimate['state'] is None
    assert all(estimate[name]['interval'] is None and estimate[name]['state'] is None
               for name in ('lower', 'upper'))
    # 校准计数只用已定的陈述
    assert result['calibration']['m1'] + result['calibration']['m0'] == 51


# ---------- 未决标签 ----------

def test_two_annotators_disagreement_is_bounded_not_dropped(built, tmp_path):
    package, key = built
    disputed = [k['id'] for k in key if k['caseId'] == 'C02' and k['judge'] == 0]
    second = _as_judge(key, {sid: '支持' for sid in disputed})
    second[key[0]['id']] = '无法判断'
    result = _analyze(tmp_path, package, _as_judge(key), second)
    unresolved = [row for row in result['rows'] if row['state'] == 'unresolved']
    assert len(unresolved) == result['counts']['unresolved'] == 4
    agreement = result['agreement']
    assert agreement['statements'] == 52 and agreement['agreement'] == pytest.approx(48 / 52)
    assert agreement['binaryStatements'] == 51 and agreement['binaryKappa'] is not None

    estimate = result['estimate']
    sizes = {q['id']: q['statements'] for q in result['questions']}
    # 上下界之差恰为每条未决陈述在门禁口径中的权重之和 Σ 1/(题数 × 该题陈述数)
    width = sum(1 / (QUESTIONS * sizes[row['question']]) for row in unresolved)
    assert estimate['point'][1] - estimate['point'][0] == pytest.approx(width)
    assert estimate['lower']['point'] == estimate['point'][0] and estimate['upper']['point'] == estimate['point'][1]
    # 点估计边界保留；端点区间不能包住混合赋值，故未决时暂停置信结论。
    assert estimate['interval'] is None and estimate['state'] is None
    assert all(estimate[name]['interval'] is None and estimate[name]['state'] is None
               for name in ('lower', 'upper'))
    assert '混合赋值' in estimate['reason']


def test_point_verdict_is_undecided_when_the_bounds_straddle_the_threshold(built, tmp_path):
    package, key = built
    targets = {('D01', 1), ('D09', 1), ('C02', 2), ('C02', 5)}
    flipped = {k['id']: '支持' for k in key if (k['caseId'], k['statementIndex'] + 1) in targets}
    last = next(k['id'] for k in key if (k['caseId'], k['statementIndex'] + 1) == ('C02', 6))
    result = _analyze(tmp_path, package, _as_judge(key, {**flipped, last: '无法判断'}))
    estimate = result['estimate']
    assert estimate['lower']['pointPasses'] is False and estimate['upper']['pointPasses'] is True
    assert estimate['pointPasses'] is None
    assert estimate['upper']['point'] == pytest.approx(0.8)
    assert estimate['interval'] is None and estimate['state'] is None


def test_endpoint_intervals_do_not_cover_mixed_unresolved_assignments():
    """端点方差为零、混合赋值方差非零：不能把两个端点区间当作稳健包络。"""
    groups = [[('resolved', 1)] * 2 + [('resolved', 0)] * 7 + [('unresolved', None)]
              for _ in range(10)]
    groups += [[('unlabeled', None)] * 10 for _ in range(10)]
    judge = [0.2] * 20
    lower = je.ppi_interval(judge[10:], judge[:10], [0.2] * 10)
    upper = je.ppi_interval(judge[10:], judge[:10], [0.3] * 10)
    mixed = je.ppi_interval(judge[10:], judge[:10], [0.3] + [0.2] * 9)
    endpoint_low = min(lower['interval'][0], upper['interval'][0])
    assert mixed['interval'][0] == pytest.approx(0.19040036015459946)
    assert mixed['interval'][0] < endpoint_low

    result = jc._estimates(groups, judge, 0.8)
    assert result['method'] == 'ppi' and result['labeledQuestions'] == 10
    assert result['unresolved'] == 10
    assert result['point'] == pytest.approx([0.2, 0.3])
    assert result['pointPasses'] is False
    assert result['interval'] is None and result['state'] is None
    assert result['lower']['interval'] is None and result['upper']['interval'] is None


def test_unresolved_report_keeps_bounds_without_claiming_a_confidence_interval(built, tmp_path):
    package, key = built
    result = _analyze(tmp_path, package, _as_judge(key, {key[0]['id']: '无法判断'}))
    text = jc.render_analysis(result)
    assert '点估计边界' in text and '不给95%区间' in text
    assert '边界不是置信区间' in text and '混合赋值' in text
    assert '95% 区间' not in text and '确定通过' not in text and '确定不通过' not in text


def test_flipping_the_five_answered_statements_reaches_the_threshold(built, tmp_path):
    package, key = built
    targets = {('D01', 1), ('D09', 1), ('C02', 2), ('C02', 5), ('C02', 6)}
    flipped = {k['id']: '支持' for k in key if (k['caseId'], k['statementIndex'] + 1) in targets}
    result = _analyze(tmp_path, package, _as_judge(key, flipped))
    estimate = result['estimate']
    assert estimate['point'] == [pytest.approx(0.8)] * 2 and estimate['pointPasses'] is True
    assert result['calibration']['m0'] == 4 and result['calibration']['q1'] == pytest.approx(43 / 48)
    assert sum(row['disagreesWithJudge'] for row in result['rows']) == 5


# ---------- 部分标注的使用限制 ----------

def test_partial_annotation_uses_ppi_on_a_prefix_of_at_least_ten(built, tmp_path):
    package, key = built
    result = _analyze(tmp_path, package, _only(key, _as_judge(key), _qids(*range(1, 11))))
    estimate = result['estimate']
    assert estimate['method'] == 'ppi' and estimate['labeledQuestions'] == jc.MIN_PPI_QUESTIONS == 10
    assert estimate['outsidePrefix'] == 0 and estimate['reason'] is None
    # 人工与裁判一致时，PPI 点估计等于未标注题的裁判均值
    rest = [q['judge'] for q in result['questions'][10:]]
    assert estimate['point'] == [pytest.approx(sum(rest) / len(rest))] * 2


def test_questions_labeled_outside_the_prefix_are_not_used(built, tmp_path):
    package, key = built
    labels = _as_judge(key)
    base = _analyze(tmp_path, package, _only(key, labels, _qids(*range(1, 11))))
    # 额外标了 Q15，且与裁判相反：不在连续前缀内，不进入 PPI
    extra = {k['id']: ('无依据' if k['judge'] else '支持') for k in key if k['question'] == 'Q15'}
    result = _analyze(tmp_path, package, {**_only(key, labels, _qids(*range(1, 11))), **extra})
    estimate = result['estimate']
    assert estimate['method'] == 'ppi' and estimate['labeledQuestions'] == 10 and estimate['outsidePrefix'] == 1
    assert estimate['point'] == base['estimate']['point'] and estimate['interval'] == base['estimate']['interval']
    assert '未计入 PPI' in jc.render_analysis(result)


@pytest.mark.parametrize('labeled, reason', [
    (range(11, 21), '从 Q01 起连续标完的只有 0 题'),
    (range(1, 6), '只有 5 题，少于 10 题'),
    ((*range(1, 10), *range(11, 20)), '只有 9 题'),
    (range(1, 20), '未标完的题少于 2 题'),
])
def test_partial_annotation_without_a_valid_prefix_gives_no_interval(built, tmp_path, labeled, reason):
    package, key = built
    result = _analyze(tmp_path, package, _only(key, _as_judge(key), _qids(*labeled)))
    estimate = result['estimate']
    assert estimate['method'] is None and reason in estimate['reason']
    assert 'interval' not in estimate and 'point' not in estimate
    assert f'不给区间：{estimate["reason"]}' in jc.render_analysis(result)


def test_a_partly_labeled_question_ends_the_prefix(built, tmp_path):
    package, key = built
    labels = _only(key, _as_judge(key), _qids(*range(1, 13)))
    dropped = next(k['id'] for k in key if k['question'] == 'Q03')
    del labels[dropped]
    estimate = _analyze(tmp_path, package, labels)['estimate']
    assert estimate['method'] is None and estimate['labeledQuestions'] == 2 and estimate['outsidePrefix'] == 9


def test_two_annotators_count_a_question_only_when_both_finished_it(built, tmp_path):
    package, key = built
    labels = _as_judge(key)
    result = _analyze(tmp_path, package, labels, _only(key, labels, _qids(*range(1, 11))))
    estimate = result['estimate']
    assert estimate['method'] == 'ppi' and estimate['labeledQuestions'] == 10
    assert result['counts']['unlabeled'] == sum(k['question'] not in _qids(*range(1, 11)) for k in key)


# ---------- 输出 ----------

def test_single_annotator_report_says_so(built, tmp_path):
    package, key = built
    text = jc.render_analysis(_analyze(tmp_path, package, _as_judge(key)))
    assert '单人标注' in text and '全部人工标注' in text and '仅为描述' in text


def test_calibration_file_states_its_assumption(built, tmp_path):
    package, key = built
    result = _analyze(tmp_path, package, _as_judge(key))
    calibration = jc.calibration_file(result)
    assert (calibration['m0'], calibration['m1']) == (9, 43)
    assert '同一裁判模型' in calibration['assumption']
