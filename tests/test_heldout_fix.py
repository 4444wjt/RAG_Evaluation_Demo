"""修复验证留出题工具：离线测试题目校验、修复前后提示的差异、授权约束、请求上限与判定，不调用模型。"""
import json
import shutil

import pytest

from tools.competition import heldout_fix as hf
from tools.competition.prompt_case import FixedRetriever

# 提交 b90a14c 之前 backend/services/answering.py 拼出的提示（逐字）。
PRE_FIX_TEMPLATE = (
    '\n    请根据以下参考资料，用一段完整的、详细的中文句子回答问题。\n'
    '    如果参考资料中没有相关信息，请直接回答“根据提供的文档，无法回答该问题。”，不要编造。\n\n'
    '    参考资料：\n    {contexts}\n\n    问题：{question}\n    答案：\n    '
)


class Reply:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {'input_tokens': 1000, 'output_tokens': 100}
        self.response_metadata = {'model_name': 'fake-model'}


class FakeLLM:
    """带纠正指引的提示回答「不对」，不带的一律拒答；核对提示返回一条有依据的陈述。"""

    def __init__(self):
        self.prompts = []

    def invoke(self, prompt):
        prompt = str(prompt)
        self.prompts.append(prompt)
        if '事实核查员' in prompt:
            return Reply(json.dumps({'statements': [{'text': '答案是甲', 'verdict': 'supported', 'evidence': '甲'}]},
                                    ensure_ascii=False))
        if '请依据参考资料指出并纠正' in prompt:
            return Reply('不对。答案是甲。')
        return Reply('根据提供的文档，无法回答该问题。')


def fake_contexts(data, _root):
    """依据题给出含原文依据的资料，资料无答案的题给无关资料。"""
    return {c['id']: ([e['quote'] for e in c['evidence']] or ['无关资料']) for c in data['cases']}


@pytest.fixture
def root(tmp_path):
    """题目已确认的副本仓库：只含题目、两份资料和运行代码。"""
    base = tmp_path / 'repo'
    data = json.loads((hf.ROOT / hf.CASES).read_text(encoding='utf-8'))
    data['authorValidation'] = {'status': 'confirmed', 'reviewer': 'tester', 'reviewedAt': '2026-10-14', 'note': ''}
    paths = [doc['path'] for doc in data['documents'].values()] + list(hf.TOOL_FILES)
    for relative in paths:
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(hf.ROOT / relative, base / relative)
    (base / hf.CASES).parent.mkdir(parents=True, exist_ok=True)
    (base / hf.CASES).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return base


def prepared(root, work, max_requests=200, budget=5.0):
    hf.prepare(work, root=root, contexts_fn=fake_contexts)
    hf.authorize(work, 'test', budget, max_requests, root=root)
    return work


def test_case_file_is_valid_and_balanced():
    data = hf.load_cases()
    counts = {key: sum(c['category'] == key for c in data['cases']) for key in hf.CATEGORIES}
    assert counts == {'false_premise': 14, 'true_premise': 5, 'missing': 5}
    assert {c['doc'] for c in data['cases']} == {'deposit', 'disclosure'}
    # 不复用 25 题正式运行的题目。
    formal = {row['question'] for row in json.loads((hf.ROOT / 'scenarios/competition_25/testset.json').read_text(encoding='utf-8'))}
    assert not formal & {c['question'] for c in data['cases']}


def test_authorization_requires_author_confirmation(root, tmp_path):
    data = json.loads((root / hf.CASES).read_text(encoding='utf-8'))
    data['authorValidation']['status'] = 'pending'
    (root / hf.CASES).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    hf.prepare(tmp_path, root=root, contexts_fn=fake_contexts)
    with pytest.raises(PermissionError):
        hf.authorize(tmp_path, 'test', 1.0, 10, root=root)


def test_misquoted_evidence_is_rejected(root):
    data = json.loads((root / hf.CASES).read_text(encoding='utf-8'))
    data['cases'][0]['evidence'][0]['quote'] += '改'
    (root / hf.CASES).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError):
        hf.load_cases(root)


def test_arms_differ_only_by_the_fix_line_and_before_matches_pre_fix_prompt():
    item = {'id': 'X', 'question': '问题？', 'contexts': ['资料一', '资料二'], 'groundTruth': '答'}
    prompts = {}
    for arm in hf.ARMS:
        llm = FakeLLM()
        hf.run_job({'id': f'{arm}:X', 'arm': arm, 'item': 'X'}, item, llm)
        prompts[arm] = llm.prompts[0]
    assert hf.FIX_LINE in prompts['after']
    assert prompts['after'].replace(hf.FIX_LINE, '', 1) == prompts['before']
    assert prompts['before'] == PRE_FIX_TEMPLATE.format(contexts='资料一\n资料二', question='问题？')


def test_prepare_records_whether_evidence_was_retrieved(root, tmp_path):
    def partial(data, _root):
        contexts = fake_contexts(data, _root)
        contexts['AF04'] = contexts['AF04'][:1]
        contexts['BF01'] = ['无关资料']
        return contexts
    package = hf.prepare(tmp_path, root=root, contexts_fn=partial)
    coverage = {item['id']: item['evidenceRetrieved'] for item in package['items']}
    assert coverage['AF04'] == 'partial' and coverage['BF01'] == 'none'
    assert coverage['AF01'] == 'all' and coverage['AN01'] == 'n/a'
    assert package['estimate']['calls'] == 96
    assert 0 < package['estimate']['costCnyUpperBound'] < 2


def test_run_requires_authorization_and_unchanged_code(root, tmp_path):
    hf.prepare(tmp_path, root=root, contexts_fn=fake_contexts)
    with pytest.raises(PermissionError):
        hf.run(tmp_path, llm=FakeLLM(), root=root)
    hf.authorize(tmp_path, 'test', 1.0, 10, root=root)
    tool = root / 'backend/services/answering.py'
    tool.write_text(tool.read_text(encoding='utf-8') + '\n# 改动\n', encoding='utf-8')
    with pytest.raises(PermissionError):
        hf.run(tmp_path, llm=FakeLLM(), root=root)


def test_run_stops_at_request_cap_and_resumes(root, tmp_path):
    prepared(root, tmp_path, max_requests=6)
    first = hf.run(tmp_path, llm=FakeLLM(), root=root)
    assert first['completed'] == 3 and first['stopped'] == '达到请求数上限'
    auth = json.loads((tmp_path / 'authorization.json').read_text(encoding='utf-8'))
    auth['maxRequests'] = 1000
    (tmp_path / 'authorization.json').write_text(json.dumps(auth, ensure_ascii=False), encoding='utf-8')
    second = hf.run(tmp_path, llm=FakeLLM(), root=root)
    assert second['completed'] == second['total'] == 48 and second['stopped'] is None


def test_analysis_applies_fixed_rules_and_pairs_arms(root, tmp_path):
    prepared(root, tmp_path)
    hf.run(tmp_path, llm=FakeLLM(), root=root)
    summary, items = hf.analyze(tmp_path)
    before, after = summary['arms']['before']['groups'], summary['arms']['after']['groups']
    # 假模型：修复前一律拒答，修复后一律回答「不对」。
    assert before['false_premise/all']['pass'] == 0 and after['false_premise/all']['pass'] == 14
    assert before['missing/all']['pass'] == 5 and after['missing/all']['pass'] == 0
    assert after['missing/all']['outcomes'] == {'answered': 5}
    paired = summary['paired']
    assert len(paired['false_premise']['improved']) == 14 and paired['false_premise']['worsened'] == []
    assert paired['false_premise']['signTestP'] == round(2 / 2 ** 14, 4)
    assert len(paired['missing']['worsened']) == 5
    assert summary['usage']['requests'] == 96
    assert {row['outcome'] for row in items['rows']['before'] if row['category'] != 'missing'} == {'refused'}


def test_outcome_rules():
    base = {'status': 'ok', 'category': 'false_premise', 'refused': False, 'traceStatus': 'ok', 'factErrors': 0}
    assert hf.outcome(base) == 'pass'
    assert hf.outcome({**base, 'factErrors': 1}) == 'contradicts_source'
    assert hf.outcome({**base, 'refused': True}) == 'refused'
    assert hf.outcome({**base, 'traceStatus': 'parse_error'}) == 'trace_failed'
    assert hf.outcome({**base, 'status': 'call_error'}) == 'error'
    assert hf.outcome({**base, 'category': 'missing', 'refused': True}) == 'pass'
    assert hf.sign_test(0, 0) is None and hf.sign_test(3, 3) == 1.0


def test_without_fix_refuses_to_guess_when_line_is_missing():
    with pytest.raises(RuntimeError):
        hf.WithoutFix(FakeLLM()).invoke('没有那一行的提示')
    assert FixedRetriever(['甲']).invoke('?')[0].page_content == '甲'
