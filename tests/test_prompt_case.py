"""外部案例工具：离线测试材料包、提示拼装、授权约束、请求上限与分析，不调用模型。"""
import json

import pytest

from tools.competition import prompt_case as pc


class Reply:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {'input_tokens': 1000, 'output_tokens': 100}
        self.response_metadata = {'model_name': 'fake-model'}


class FakeLLM:
    """核对提示返回固定判定；被测提示一律拒答，平台提示返回固定回答。"""

    def __init__(self, fail=False):
        self.fail, self.prompts = fail, []

    def invoke(self, prompt):
        prompt = str(prompt)
        self.prompts.append(prompt)
        if self.fail:
            raise TimeoutError('timeout')
        if '事实核查员' in prompt:
            return Reply(json.dumps({'statements': [{'text': '答案是甲', 'verdict': 'supported', 'evidence': '甲'}]},
                                    ensure_ascii=False))
        if prompt.startswith('【指令】'):
            return Reply('根据已知信息无法回答该问题')
        return Reply('答案是甲。')


def authorized(tmp_path, max_requests=200, budget=5.0):
    pc.prepare(tmp_path)
    pc.authorize(tmp_path, 'test', budget, max_requests)
    return tmp_path


def test_items_come_from_the_frozen_formal_run():
    items = pc.load_items()
    assert len(items) == 25
    assert [i['id'] for i in items][:2] == ['D01', 'D02']
    counts = {key: sum(i['category'] == key for i in items) for key in pc.CATEGORY}
    assert counts == {'D': 10, 'C': 5, 'F': 5, 'N': 5}
    assert all(i['caseType'] == ('negative' if i['category'] == 'N' else 'positive') for i in items)


def test_chatchat_prompt_keeps_template_and_joins_contexts_with_blank_line():
    prompt = pc.render('langchain-chatchat', '问题？', ['资料一', '资料二'])
    assert prompt.startswith('【指令】根据已知信息，简洁和专业的来回答问题。')
    assert '“根据已知信息无法回答该问题”' in prompt
    assert '【已知信息】资料一\n\n资料二\n\n【问题】问题？\n' in prompt
    assert '{{' not in prompt


def test_prepare_builds_two_arms_and_bounds_cost(tmp_path):
    package = pc.prepare(tmp_path)
    assert len(package['jobs']) == 50
    assert {job['arm'] for job in package['jobs']} == {'platform', 'langchain-chatchat'}
    assert package['estimate']['calls'] == 100
    assert 0 < package['estimate']['costCnyUpperBound'] < 2
    assert package['source']['sha256'][pc.SOURCE_FILES[0]] == pc.sha256_file(pc.ROOT / pc.SOURCE_FILES[0])


def test_run_requires_authorization_bound_to_package(tmp_path):
    pc.prepare(tmp_path)
    with pytest.raises(PermissionError):
        pc.run(tmp_path, llm=FakeLLM())
    pc.authorize(tmp_path, 'test', 1.0, 10)
    package = json.loads((tmp_path / 'package.json').read_text(encoding='utf-8'))
    package['items'][0]['question'] += '改'
    (tmp_path / 'package.json').write_text(json.dumps(package, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(PermissionError):
        pc.run(tmp_path, llm=FakeLLM())


def test_run_stops_at_request_cap_and_resumes_without_repeating(tmp_path):
    authorized(tmp_path, max_requests=6)
    llm = FakeLLM()
    first = pc.run(tmp_path, llm=llm)
    assert first == {'requests': 6, 'spentCny': first['spentCny'], 'stopped': '达到请求数上限', 'completed': 3, 'total': 50}
    assert len(llm.prompts) == 6
    auth = json.loads((tmp_path / 'authorization.json').read_text(encoding='utf-8'))
    auth['maxRequests'] = 10
    (tmp_path / 'authorization.json').write_text(json.dumps(auth, ensure_ascii=False), encoding='utf-8')
    second = pc.run(tmp_path, llm=llm)
    assert (second['requests'], second['completed']) == (10, 5)
    ids = [json.loads(line)['id'] for line in (tmp_path / 'records.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(ids) == len(set(ids)) == 5


def test_run_stops_when_budget_cannot_cover_next_job(tmp_path):
    authorized(tmp_path, budget=0.0)
    result = pc.run(tmp_path, llm=FakeLLM())
    assert (result['stopped'], result['requests']) == ('剩余预算不足以完成下一项', 0)


def test_call_error_is_recorded_once_without_retry(tmp_path):
    authorized(tmp_path, max_requests=2)
    llm = FakeLLM(fail=True)
    pc.run(tmp_path, llm=llm)
    record = json.loads((tmp_path / 'records.jsonl').read_text(encoding='utf-8').splitlines()[0])
    assert (record['status'], record['errorType'], len(llm.prompts)) == ('call_error', 'TimeoutError', 1)
    assert record['calls'] == [{'error': 'TimeoutError', 'seconds': record['calls'][0]['seconds']}]


def test_analyze_counts_refusals_by_category_per_arm(tmp_path):
    authorized(tmp_path)
    pc.run(tmp_path, llm=FakeLLM())
    summary, items = pc.analyze(tmp_path)
    chatchat, platform = summary['arms']['langchain-chatchat'], summary['arms']['platform']
    # 假核对给出的证据「甲」不在资料原文中，逐句核对会丢弃这条陈述，所以陈述数为 0。
    assert chatchat['byCategory']['F'] == {'label': '错误前提', 'questions': 5, 'refused': 5, 'statements': 0, 'supported': 0,
                                           'refusedIds': ['F01', 'F02', 'F03', 'F04', 'F05']}
    assert platform['byCategory']['F']['refused'] == 0
    # 拒答没有可核对的陈述，不算核对失败。
    assert chatchat['traceFailed'] == platform['traceFailed'] == []
    assert sum(chatchat['traceStatus'].values()) == 25
    assert (platform['completed'], platform['errors']) == (25, 0)
    assert summary['usage']['requests'] == 100 and summary['returnedModels'] == {'fake-model': 100}
    assert len(items['rows']['platform']) == 25
