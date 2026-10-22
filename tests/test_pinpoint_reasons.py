"""裁判理由表层分类与冻结运行的离线复算契约。"""
import socket

import pytest

from tools.competition import pinpoint_reasons as pr


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('离线分类不能调用模型或访问网络')
    monkeypatch.setattr(pr.si, 'make_transport', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


@pytest.mark.parametrize('reason,category,correct,contains,bound', [
    ('存款有依据', 'no_paragraph_in_reason', False, False, False),
    ('第二款有依据', 'repeats_stated_paragraph', False, False, False),
    ('第五条第一款与第二款', 'repeats_stated_paragraph', False, True, True),
    ('第五条第一款', 'names_other_paragraph', True, True, True),
    ('第一款及第一款', 'names_other_paragraph', True, True, False),
    ('第八条第一款', 'names_other_paragraph', True, True, False),
    ('第五条第三款', 'names_other_paragraph', False, False, False),
    ('第一款和第三款', 'names_other_paragraph', False, True, False),
])
def test_classify(reason, category, correct, contains, bound):
    result = pr.classify('根据第五条第二款，存款有保障。', reason, {'article': 5, 'paragraph': 1})
    assert result['category'] == category
    assert result['namesCorrectParagraph'] is correct
    assert result['saidContainsCorrectParagraph'] is contains
    assert result['hasArticleBoundParagraph'] is bound
    assert result['stated'] == ['第二款']


def test_multiple_stated_paragraphs_use_set_intersection():
    result = pr.classify('第二款及第三款', '第三款与第一款', {'article': 5, 'paragraph': 1})
    assert result['category'] == 'repeats_stated_paragraph'
    assert result['saidContainsCorrectParagraph'] is True


def test_archive_replay_is_deterministic_and_matches_original(tmp_path):
    run = pr.ROOT / 'docs/competition/statute-check/20261021-pinpoint-run/run'
    first, second = tmp_path / 'a', tmp_path / 'b'
    second.mkdir()
    result = pr.analyze(run, first)
    pr.analyze(run, second)
    assert result['byPromptCategory']['original'] == dict(zip(pr.CATEGORIES, (32, 20, 8)))
    for judge, expected in [('deepseek', (22, 6, 2)), ('qwen72b', (10, 14, 6))]:
        assert result['byPromptJudgeCategory']['original'][judge] == dict(zip(pr.CATEGORIES, expected))
    other = [r for r in result['misses'] if r['prompt'] == 'original' and r['category'] == 'names_other_paragraph']
    assert len(other) == 8
    assert all(r['namesCorrectParagraph'] for r in other)
    left = {p.name: p.read_bytes() for p in first.iterdir()}
    assert left == {p.name: p.read_bytes() for p in second.iterdir()}
    pr.si.verify_checksums(first)
    with pytest.raises(ValueError, match='空目录'):
        pr.analyze(run, first)
    assert left == {p.name: p.read_bytes() for p in first.iterdir()}


def test_corrupt_input_is_rejected_before_output(tmp_path):
    run, output = tmp_path / 'run', tmp_path / 'output'
    run.mkdir()
    pr.si.write_json(run / 'package.json', {})
    pr.si.checksums(run)
    pr.si.write_json(run / 'package.json', {'changed': True})
    with pytest.raises(ValueError, match='归档校验失败'):
        pr.analyze(run, output)
    assert not output.exists()
