"""鲁棒性体检页的数据必须能由归档重新生成，且拒绝被改动或对不上的归档。"""
from pathlib import Path
import hashlib
import json
import shutil

import pytest

from tools.competition import build_robustness_fixture as fixture

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / 'frontend' / 'src' / 'fixtures' / 'robustness.json'


@pytest.fixture(scope='module')
def built():
    return fixture.build()


def test_committed_fixture_matches_archive(built):
    assert json.loads(OUTPUT.read_text(encoding='utf-8')) == built


def test_formal_run_results(built):
    assert built['items'] == 20 and built['jobs'] == {'planned': 167, 'completed': 167}
    assert built['calls'] == {'paid': 260, 'http': 0, 'errors': 0}
    assert built['built'] == {'original': 20, 'shuffle': 20, 'noise': 20, 'remove': 20, 'counterfactual': 13}
    judge, generator = built['judge'], built['generator']
    assert (judge['shuffle']['unchanged']['count'], judge['noise']['unchanged']['count']) == (16, 16)
    assert judge['remove']['flagged']['count'] == judge['remove']['flagged']['total'] == 16
    assert (judge['counterfactual']['flagged']['count'], judge['counterfactual']['flagged']['total']) == (9, 10)
    assert (generator['remove']['refused']['count'], generator['remove']['refused']['total']) == (19, 20)
    assert generator['counterfactual']['adoption']['kept']['count'] == 0
    assert {note['id'] for note in built['notes']} == set(fixture.REMARKS)
    assert built['usage']['estimatedCostCny'] <= built['source']['budgetCny']


def copy_archive(tmp_path):
    archive = tmp_path / 'archive'
    shutil.copytree(fixture.ARCHIVE, archive)
    return archive


def rewrite_checksums(archive):
    lines = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}' for p in sorted(archive.iterdir())
             if p.is_file() and p.name != 'SHA256SUMS.txt']
    (archive / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_tampered_file_is_rejected(tmp_path):
    archive = copy_archive(tmp_path)
    (archive / 'summary.json').write_text((archive / 'summary.json').read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match='哈希'):
        fixture.build(archive)


def test_summary_must_be_recomputable_from_records(tmp_path):
    archive = copy_archive(tmp_path)
    lines = (archive / 'records.jsonl').read_text(encoding='utf-8').splitlines()
    records = [json.loads(line) for line in lines]
    target = next(r for r in records if r['id'] == 'D06:judge:remove')
    for statement in target['trace']['statements']:
        statement['verdict'], statement['evidence'] = 'supported', 'x'
    (archive / 'records.jsonl').write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n', encoding='utf-8')
    rewrite_checksums(archive)  # 即使校验清单同步改写，汇总也对不上
    with pytest.raises(ValueError, match='重新算出'):
        fixture.build(archive)


def test_remarks_must_match_found_results(monkeypatch):
    monkeypatch.setitem(fixture.REMARKS, 'D02:judge:remove', '不存在的结果')
    with pytest.raises(ValueError, match='对应不到'):
        fixture.build()
