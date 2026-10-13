"""外部数据验证工具：离线测试解析、抽样、授权约束与指标计算，不调用模型、不依赖已下载的数据。"""
from pathlib import Path
import json

import pytest

from tools.competition import external_validation as ev


class Reply:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {'input_tokens': 1000, 'output_tokens': 100}
        self.response_metadata = {'model_name': 'fake-model'}


class FakeLLM:
    """核对提示返回固定判定；问答提示返回固定回答。记录每次调用。"""

    def __init__(self, verdict='supported', answer='答案是甲。', fail=False):
        self.verdict, self.answer, self.fail, self.prompts = verdict, answer, fail, []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise TimeoutError('timeout')
        if '事实核查员' in prompt:
            evidence = '' if self.verdict == 'fabrication' else '甲'
            return Reply(json.dumps({'statements': [{'text': '答案是甲', 'verdict': self.verdict, 'evidence': evidence}]},
                                    ensure_ascii=False))
        return Reply(self.answer)


@pytest.mark.parametrize('text, expected', [
    ('<要点>井冈山根据地成立于1928年。\n<幻觉>矛盾\n<参考>参考0：1927年\n<改正>改为1927年', ('井冈山根据地成立于1928年。', 'contradictory')),
    ('<幻觉>无\n<参考>参考0：……', (None, 'none')),
    ('<幻觉>无法验证\n<参考>参考0：\n<改正>无', (None, 'unverifiable')),
    ('<幻觉>矛盾。\n<参考>', (None, 'contradictory')),
    ('<无事实>对不起，我不了解您所说的事件。', None),
    ('<要点>某句。\n<幻觉>无法验证参考文档中没有提到……\n<改正>', None),
])
def test_parse_annotation_is_strict(text, expected):
    assert ev.parse_annotation(text) == expected


def test_split_sentences_drops_list_numbers():
    assert ev.split_sentences('贡献如下：\n\n1. 推动特区建设。\n\n2、制定规划！最后一句') == \
        ['贡献如下：\n\n1. 推动特区建设。', '制定规划！', '最后一句']


def test_aligned_sentence_used_only_when_counts_match(tmp_path):
    ann = ['<幻觉>无\n<参考>', '<要点>第二句原文。\n<幻觉>矛盾\n<参考>', '<幻觉>无法验证\n<参考>']
    row = {'name': 't', 'language': 'zh', 'documents': ['甲'], 'selected_questions': ['问？'],
           'GPT3.5_answers_D': ['第一句。第二句原文。3. 第三句。'], 'human_GPT3.5_answers_D_ann': [ann],
           'InternLM_answers': ['只有一句。'], 'human_InternLM_answers_ann': [ann]}
    root = tmp_path / 'src'
    for name in ev.SOURCES['anah']['files']:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(json.dumps(row, ensure_ascii=False) + '\n' if name.endswith('event.jsonl') else '', encoding='utf-8')
    items = list(ev.anah_candidates(root))
    # InternLM 回答只有一句，与三条标注对不上：只保留带要点的那条，不猜其余两句
    assert [(i['sentence'], i['gold'], i['sentenceSource']) for i in items] == [
        ('第一句。', 'none', 'aligned'), ('第二句原文。', 'contradictory', 'annotation'),
        ('第三句。', 'unverifiable', 'aligned'), ('第二句原文。', 'contradictory', 'annotation')]


def candidates(n_topics=40):
    return [{'id': f'{t}:{k}:{label}', 'topic': f'topic{t}', 'gold': label}
            for t in range(n_topics) for k in range(3) for label in ev.ANAH_LABELS.values()]


def test_sample_is_balanced_deterministic_and_caps_topics():
    first = ev.sample_anah(candidates(), per_label=10, max_per_topic=2)
    again = ev.sample_anah(candidates(), per_label=10, max_per_topic=2)
    assert [i['id'] for i in first] == [i['id'] for i in again]
    assert {label: sum(i['gold'] == label for i in first) for label in ev.ANAH_LABELS.values()} == \
        {'none': 10, 'contradictory': 10, 'unverifiable': 10}
    topics = [i['topic'] for i in first]
    assert max(topics.count(t) for t in set(topics)) <= 2
    with pytest.raises(ValueError, match='少于'):
        ev.sample_anah(candidates(n_topics=3), per_label=10, max_per_topic=2)


@pytest.mark.parametrize('prediction, answer, expected', [
    ('导演是陈凯歌。', '陈凯歌', True),
    ('导演是张艺谋。', '陈凯歌', False),
    ('冠军是A队，亚军是B队', [['A队', 'A'], ['B队']], True),
    ('冠军是A队', [['A队'], ['B队']], False),
])
def test_rgb_correct_follows_official_rule(prediction, answer, expected):
    assert ev.rgb_correct(prediction, answer) is expected


@pytest.mark.parametrize('verdicts, expected', [
    ([], 'no_statement'), (['supported'], 'none'), (['supported', 'fabrication'], 'unverifiable'),
    (['fabrication', 'fact_error'], 'contradictory'),
])
def test_predicted_label_mapping(verdicts, expected):
    assert ev.predicted_label({'statements': [{'verdict': v} for v in verdicts]}) == expected


def test_wilson_interval():
    assert ev.wilson(0, 0) is None
    low, high = ev.wilson(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-4) and high == pytest.approx(0.5962, abs=1e-4)


def make_package(directory: Path):
    jobs = [
        {'kind': 'anah', 'id': 'a1', 'gold': 'none', 'sentenceSource': 'aligned', 'topic': 't1',
         'question': '问？', 'sentence': '答案是甲。', 'documents': ['甲是答案。']},
        {'kind': 'anah', 'id': 'a2', 'gold': 'unverifiable', 'sentenceSource': 'annotation', 'topic': 't2',
         'question': '问？', 'sentence': '答案是甲。', 'documents': ['甲是答案。']},
        {'kind': 'rgb', 'id': 'rgb-noise:1', 'rgbId': '1', 'condition': 'noise', 'question': '谁？', 'answer': '甲',
         'contexts': ['甲是答案。', '无关']},
        {'kind': 'rgb', 'id': 'rgb-fact:2', 'rgbId': '2', 'condition': 'counterfactual', 'question': '谁？', 'answer': '乙',
         'fakeAnswer': '甲', 'contexts': ['甲是答案。']},
    ]
    ev.write_json(directory / 'package.json', {'seed': ev.SEED, 'jobs': jobs, 'estimate': ev.estimate(jobs)})
    return jobs


def test_run_requires_authorization_bound_to_package_and_code(tmp_path):
    make_package(tmp_path)
    with pytest.raises(PermissionError, match='没有授权'):
        ev.run(tmp_path, llm=FakeLLM())
    with pytest.raises(ValueError, match='预算'):
        ev.authorize(tmp_path, 'tester', budget=0)
    auth = ev.authorize(tmp_path, 'tester', budget=1)
    assert auth['maxRetries'] == 0 and auth['maxRequests'] == 6 and set(auth['toolSha256']) == set(ev.TOOL_FILES)
    package = ev.read_json(tmp_path / 'package.json')
    package['jobs'][0]['sentence'] = '改过的句子。'
    ev.write_json(tmp_path / 'package.json', package)
    llm = FakeLLM()
    with pytest.raises(PermissionError, match='哈希'):
        ev.run(tmp_path, llm=llm)
    assert llm.prompts == []


def test_run_uses_production_prompts_once_each_and_resumes(tmp_path):
    make_package(tmp_path)
    ev.authorize(tmp_path, 'tester', budget=1)
    llm = FakeLLM()
    result = ev.run(tmp_path, llm=llm)
    assert result['completed'] == 4 and result['requests'] == 6 and result['stopped'] is None
    assert sum('事实核查员' in p for p in llm.prompts) == 4
    assert sum('请根据以下参考资料' in p for p in llm.prompts) == 2
    # 再次运行只续跑未完成的条目，不重复付费
    assert ev.run(tmp_path, llm=llm)['completed'] == 4 and len(llm.prompts) == 6


def test_run_stops_at_request_limit_without_retry(tmp_path):
    make_package(tmp_path)
    auth = ev.authorize(tmp_path, 'tester', budget=1)
    auth['maxRequests'] = 3
    ev.write_json(tmp_path / 'authorization.json', auth)
    llm = FakeLLM(fail=True)
    result = ev.run(tmp_path, llm=llm)
    # 前两条各 1 次核对（失败也不重试），第三条需要 2 次，超过上限即停
    assert result['stopped'] == '达到请求数上限' and len(llm.prompts) == 2 and result['requests'] == 2
    records = [json.loads(line) for line in (tmp_path / 'records.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [r['trace']['trace_status'] for r in records] == ['call_error', 'call_error']
    assert all(call == {'error': 'TimeoutError', 'seconds': call['seconds']} for r in records for call in r['calls'])


def test_analyze_counts_failures_separately_and_archive_has_no_rgb_text(tmp_path):
    make_package(tmp_path)
    ev.authorize(tmp_path, 'tester', budget=1)
    ev.run(tmp_path, llm=FakeLLM(verdict='supported', answer='答案是甲。'))
    out = tmp_path / 'archive'
    summary = ev.archive(tmp_path, out)
    anah = summary['anah']
    assert anah['confusion'] == {'none': {'none': 1}, 'contradictory': {}, 'unverifiable': {'none': 1}}
    assert anah['binary']['fn'] == 1 and anah['binary']['tn'] == 1 and anah['traceFailed'] == 0
    rgb = summary['rgb']
    assert rgb['noise']['accuracy']['count'] == 1 and rgb['noise']['flaggedWhenCorrect']['count'] == 0
    assert rgb['counterfactual']['adoptedFakeAnswer']['count'] == 1
    assert summary['requests'] == 6 and summary['usage']['inputTokens'] == 6000
    items = (out / 'items.json').read_text(encoding='utf-8')
    assert '甲是答案' not in items and '谁？' not in items
    assert json.loads((out / 'authorization.json').read_text(encoding='utf-8'))['authorizedBy'] == 'tester'
