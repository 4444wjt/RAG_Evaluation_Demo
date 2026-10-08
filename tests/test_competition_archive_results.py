"""Archive bookkeeping tests use synthetic responses, never a provider model."""
import copy
import json
from pathlib import Path
import shutil

import pytest

from tools.competition.run_evidence import (
    METRICS, finish_archive, runtime_metadata, sha256, summarize_events,
    summarize_quality, verify_checksums, write_json,
)


def row(trace='{"statements": []}', **metrics):
    return {'question': '测试问题', 'answer': '原始回答。', 'contexts': ['原文'],
            'ground_truth': '参考答案。', 'hallucination_trace': trace,
            **{name: 0.0 for name in METRICS}, **metrics}


def test_missing_metrics_and_empty_trace_do_not_mean_failed_execution(tmp_path):
    response = {'model_dfs': {'test-model': [row(faithfulness=None), row({'statements': [{'text': '原陈述'}]})]}, 'gate_passed': True}
    original = copy.deepcopy(response)
    write_json(tmp_path / 'api-response.json', response)
    (tmp_path / 'project').mkdir()
    (tmp_path / 'project/report.html').write_text('真实报告', encoding='utf-8')
    meta = finish_archive(tmp_path, {'schemaVersion': 2, 'status': 'completed', 'gatePassed': True})
    coverage = meta['resultCoverage']['models']['test-model']
    assert meta['status'] == 'completed' and meta['gatePassed'] is True
    assert coverage['metrics']['faithfulness'] == {'valid': 1, 'missing': 1}
    assert coverage['metrics']['answer_correctness'] == {'valid': 2, 'missing': 0}
    assert coverage['trace'] == {'nonempty': 1, 'empty': 1, 'missing': 0, 'invalid': 0}
    assert json.loads((tmp_path / 'api-response.json').read_text(encoding='utf-8')) == original
    assert json.loads((tmp_path / 'answers.json').read_text(encoding='utf-8'))['test-model'][0]['answer'] == '原始回答。'
    assert json.loads((tmp_path / 'metrics.json').read_text())['test-model'][0]['faithfulness'] is None
    assert json.loads((tmp_path / 'traces.json').read_text())['test-model'][0]['hallucination_trace'] == original['model_dfs']['test-model'][0]['hallucination_trace']
    assert meta['tokenObservation']['actualChargeStatus'] == 'unknown'
    index = json.loads((tmp_path / 'checksums.json').read_text())
    hashes = {item['path']: item['sha256'] for item in index['files']}
    assert hashes['manifest.json'] == sha256(tmp_path / 'manifest.json')
    assert 'checksums.json' not in hashes
    assert 'manifest.json' not in {item['path'] for item in meta['artifacts']}
    verify_checksums(tmp_path)


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), True, '0.9'])
def test_only_finite_numeric_metrics_count_as_observed(value):
    quality = summarize_quality({'model_dfs': {'test-model': [row(faithfulness=value)]}})
    assert quality['models']['test-model']['metrics']['faithfulness'] == {'valid': 0, 'missing': 1}


@pytest.mark.parametrize('trace,category', [(None, 'missing'), ('bad json', 'invalid'), ({'statements': None}, 'invalid'), ({'statements': []}, 'empty')])
def test_trace_absence_and_invalid_structure_are_separate(trace, category):
    stats = summarize_quality({'model_dfs': {'test-model': [row(trace)]}})['models']['test-model']['trace']
    assert stats[category] == 1 and sum(stats.values()) == 1


def test_usage_tracks_provider_model_cache_and_unknown_fields(tmp_path):
    events = [
        {'event': 'start', 'runId': '1', 'requestParameters': {'model_name': 'requested-model'}},
        {'event': 'end', 'runId': '1', 'llmOutput': {'model_name': 'actual-model', 'token_usage': {'prompt_tokens': 100, 'completion_tokens': 7, 'total_tokens': 107, 'prompt_cache_hit_tokens': 20}}},
        {'event': 'end', 'runId': '2', 'llmOutput': None},
    ]
    (tmp_path / 'llm-events.jsonl').write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
    observed = summarize_events(tmp_path)
    assert observed['actualReturnedModels'] == {'actual-model': 1}
    assert observed['callsWithoutReturnedModel'] == 1
    assert observed['callsWithTokenUsage'] == 1
    assert observed['cacheHitTokens'] == 20
    assert observed['totalTokens'] == 107
    assert observed['cacheCoverage'] == 'partial-or-unknown'
    assert observed['actualCharge'] is None


def test_unknown_observation_is_not_zero_cost_proof(tmp_path):
    result = summarize_events(tmp_path)
    assert result['observationStatus'] == 'unknown'
    assert result['actualReturnedModels']['status'] == 'unknown'
    assert result['actualChargeStatus'] == 'unknown'
    assert '缺失用量不按零消耗解释' in result['note']


def test_runtime_metadata_records_prompt_definitions_without_model_calls(tmp_path, monkeypatch):
    from backend import config
    from backend.core.pipeline import RAGAS_METRICS
    from backend.adapters.models import registry
    root = Path(__file__).resolve().parents[1]
    (tmp_path / 'project').mkdir()
    shutil.copytree(root / 'backend', tmp_path / 'project/backend', ignore=shutil.ignore_patterns('__pycache__'))
    write_json(tmp_path / 'request.json', {'model_names': [config.MODELS_CONFIG[0]['name']], 'faith_threshold': 0.8, 'ctx_prec_threshold': 0.8})
    monkeypatch.setattr(registry, 'build_llm', lambda *args: pytest.fail('metadata must not build a model'))
    before = [(metric.name, copy.deepcopy(getattr(metric, 'weights', None))) for metric in RAGAS_METRICS]
    captured = runtime_metadata(tmp_path)
    assert captured['sentenceAdapterVersion'] == 'zh-sentence-v1'
    assert captured['versions']['dependencies']['ragas'] == '0.1.21'
    assert captured['configuration']['metricParameters']['answer_correctness']['weights'] == [0.75, 0.25]
    assert captured['configuration']['metricParameters']['answer_relevancy']['strictness'] == 1
    assert captured['configuration']['ragasRunConfig'] == {
        'max_workers': 1, 'timeout': config.EVAL_TIMEOUT, 'max_retries': 10,
        'max_wait': 60, 'log_tenacity': False, 'seed': 42, 'exception_types': ['builtins.Exception'],
    }
    assert captured['configuration']['embedding']['weightsRevision']['status'] == 'unknown'
    assert captured['configuration']['generator']['maxTokens']['status'] == 'unknown'
    assert captured['configuration']['generator']['requestedModel'] == captured['configuration']['ragasJudge']['requestedModel']
    assert 'long_form_answer_prompt' in captured['versions']['promptVersions']['ragas']['answer_correctness']
    assert (tmp_path / 'prompt-definitions.json').exists()
    assert before == [(metric.name, getattr(metric, 'weights', None)) for metric in RAGAS_METRICS]


def test_failed_start_has_external_integrity_without_fabricated_results(tmp_path):
    meta = finish_archive(tmp_path, {'schemaVersion': 2, 'status': 'failed', 'failureType': 'OSError'})
    assert meta['status'] == 'failed'
    assert meta['resultCoverage']['status'] == 'unknown'
    assert not (tmp_path / 'answers.json').exists()
    verify_checksums(tmp_path)


def test_interrupted_logs_preserve_failure_and_finish_integrity(tmp_path):
    (tmp_path / 'llm-events.jsonl').write_text('{"event":"start","runId":"1"}\n\n{"event":', encoding='utf-8')
    (tmp_path / 'runtime.json').write_text('{"configuration":', encoding='utf-8')
    (tmp_path / 'api-response.json').write_text('upstream failed', encoding='utf-8')
    before = {name: sha256(tmp_path / name) for name in ('llm-events.jsonl', 'runtime.json', 'api-response.json')}
    meta = finish_archive(tmp_path, {'schemaVersion': 2, 'status': 'failed', 'failureType': 'TimeoutError'})
    assert meta['status'] == 'failed' and meta['failureType'] == 'TimeoutError'
    assert meta['tokenObservation']['observationStatus'] == 'partial'
    assert meta['tokenObservation']['unreadableEventLines'] == 1
    assert meta['runtimeObservation']['status'] == 'unknown'
    assert meta['resultCoverage']['status'] == 'unknown'
    assert not (tmp_path / 'metrics.json').exists()
    assert before == {name: sha256(tmp_path / name) for name in before}
    verify_checksums(tmp_path)


def test_old_archive_cannot_be_finalized(tmp_path):
    write_json(tmp_path / 'manifest.json', {'schemaVersion': 1, 'status': 'completed'})
    old = sha256(tmp_path / 'manifest.json')
    with pytest.raises(ValueError, match='禁止向旧run'):
        finish_archive(tmp_path, {'schemaVersion': 1, 'status': 'completed'})
    assert sha256(tmp_path / 'manifest.json') == old
    assert not (tmp_path / 'usage.json').exists()
