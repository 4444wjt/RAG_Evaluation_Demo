"""Offline checks of independent experiment preparation, never calls a model."""
import copy
from datetime import date, timedelta
import json
from pathlib import Path
import shutil

import pytest

from tools.competition.run_evidence import prepare, sha256, summarize_events, validate_cases, write_json

SOURCE = Path(__file__).resolve().parents[1]


@pytest.fixture
def case_root(tmp_path):
    root = tmp_path / 'source'
    (root / 'scenarios').mkdir(parents=True)
    shutil.copytree(SOURCE / 'scenarios/competition_25', root / 'scenarios/competition_25')
    shutil.copy2(SOURCE / 'scenarios/deposit_insurance.txt', root / 'scenarios/deposit_insurance.txt')
    bundle = root / 'scenarios/competition_25/case-manifest.json'
    data = json.loads(bundle.read_text(encoding='utf-8'))
    data['authorValidation'] = {'status': 'pending', 'reviewer': None, 'reviewedAt': None, 'note': None}
    write_json(bundle, data)
    return root


def change_manifest(root, mutate):
    path = root / 'scenarios/competition_25/case-manifest.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    mutate(data)
    write_json(path, data)


def test_source_scope_and_case_counts(case_root):
    material = validate_cases(case_root)
    assert len(material['cases']) == 25
    assert len(material['pilotIds']) == 5
    assert material['document']['sha256'] == 'fee557dc493eb2b92f52834cf9b164720cff6b13f276aaa2054babccbc4666eb'
    assert material['authorValidation']['status'] == 'pending'


def test_ai_draft_does_not_authorize_paid_run(case_root):
    with pytest.raises(ValueError, match='作者实际确认'):
        validate_cases(case_root, require_author=True)


@pytest.mark.parametrize('field,value', [('reviewer', None), ('reviewedAt', None), ('note', ''), ('reviewedAt', 'not-a-date'), ('reviewedAt', (date.today() + timedelta(days=1)).isoformat())])
def test_incomplete_attestation_rejected(case_root, field, value):
    def edit(data):
        data['authorValidation'] = {'status': 'confirmed', 'reviewer': '测试材料', 'reviewedAt': '2026-10-05', 'note': '仅测试，不是真实作者确认。'}
        data['authorValidation'][field] = value
    change_manifest(case_root, edit)
    with pytest.raises(ValueError):
        validate_cases(case_root, require_author=True)


def test_complete_test_attestation_is_accepted(case_root):
    change_manifest(case_root, lambda data: data.update(authorValidation={'status': 'confirmed', 'reviewer': '测试材料', 'reviewedAt': '2026-10-05', 'note': '仅测试，不写入正式材料。'}))
    assert validate_cases(case_root, require_author=True)['authorValidation']['status'] == 'confirmed'


@pytest.mark.parametrize('filename', ['testset.json', 'pilot.json'])
def test_input_byte_change_is_rejected(case_root, filename):
    path = case_root / 'scenarios/competition_25' / filename
    path.write_text(path.read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match='哈希'):
        validate_cases(case_root)


def test_source_change_rejected(case_root):
    (case_root / 'scenarios/deposit_insurance.txt').write_text('不同原文', encoding='utf-8')
    with pytest.raises(ValueError, match='法规节选'):
        validate_cases(case_root)


def test_false_premise_is_answerable_and_not_refusal_negative(case_root):
    rows = json.loads((case_root / 'scenarios/competition_25/testset.json').read_text(encoding='utf-8'))
    material = validate_cases(case_root)
    assert all(rows[r['rowIndex']]['case_type'] == 'positive' for r in material['cases'] if r['category'] == 'false_premise')
    assert sum(r['case_type'] == 'negative' for r in rows) == 5


def test_invented_quote_is_rejected(case_root):
    change_manifest(case_root, lambda data: data['cases'][0]['evidence'][0].update(quote='伪造引文'))
    with pytest.raises(ValueError, match='引用无法'):
        validate_cases(case_root)


def test_pilot_cannot_switch_to_other_question(case_root):
    change_manifest(case_root, lambda data: data.update(pilotIds=['D01', 'C02', 'C05', 'N01', 'F03']))
    with pytest.raises(ValueError, match='预跑题'):
        validate_cases(case_root)


def test_prepare_copies_only_source_into_new_output(case_root, monkeypatch):
    (case_root / 'backend').mkdir()
    (case_root / 'backend/__init__.py').write_text('# source', encoding='utf-8')
    for name in ('requirements.txt', 'requirements_new.txt'):
        (case_root / name).write_text('# existing dependencies', encoding='utf-8')
    (case_root / 'ragas_report.html').write_text('old result', encoding='utf-8')
    monkeypatch.setattr('tools.competition.run_evidence.subprocess.check_output', lambda *args, **kwargs: 'test-commit\n')
    first = prepare(case_root, 'pilot', 'deepseek-chat')
    second = prepare(case_root, 'pilot', 'deepseek-chat')
    assert first != second
    assert first.is_relative_to(case_root / 'work/competition/runs')
    assert not (first / 'project/.env').exists()
    assert not (first / 'project/ragas_report.html').exists()
    assert (case_root / 'ragas_report.html').read_text() == 'old result'
    assert sha256(first / 'project/backend/__init__.py') == sha256(case_root / 'backend/__init__.py')
    meta = json.loads((first / 'manifest.json').read_text(encoding='utf-8'))
    assert meta['status'] == 'prepared'
    assert meta['authorValidation']['status'] == 'pending'
    assert json.loads((first / 'request.json').read_text())['testset_path'].endswith('pilot.json')


def test_usage_is_observation_not_actual_cost(tmp_path):
    (tmp_path / 'llm-events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
        {'event': 'start', 'runId': 'one'},
        {'event': 'end', 'runId': 'one', 'llmOutput': {'token_usage': {'prompt_tokens': 9, 'completion_tokens': 2}}},
        {'event': 'error', 'runId': 'two'},
    ]), encoding='utf-8')
    result = summarize_events(tmp_path)
    assert result['promptTokens'] == 9
    assert result['completedCalls'] == 1
    assert result['errorEvents'] == 1
    assert result['actualCharge'] is None
