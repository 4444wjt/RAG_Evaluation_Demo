"""Historical product fixture integrity tests; no model, evaluator or network."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import socket

import pytest

from tools.competition import build_blindspot_fixture as builder


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('离线 fixture 检查禁止网络')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def test_public_fixture_is_deterministic_and_matches_committed_bytes():
    first = builder.encode_fixture(builder.build_fixture())
    second = builder.encode_fixture(builder.build_fixture())
    assert first == second == (builder.ROOT / builder.OUTPUT_PATH).read_bytes()
    assert b'\r\n' not in first


def test_counts_and_historical_scope():
    fixture = builder.build_fixture()
    assert fixture['stats'] == {'before': {'detected': 1, 'total': 10, 'falsePositives': 0, 'controls': 5},
                                'after': {'detected': 10, 'total': 10, 'falsePositives': 0, 'controls': 5},
                                'calls': 40, 'contentUnits': 10}
    assert len(fixture['rows']) == 25
    assert all(row['after'] is None for row in fixture['rows'] if row['layer'] == '条级')
    assert all(row['before']['verdict'] == row['after']['verdict'] == 'supported'
               for row in fixture['rows'] if row['layer'] == '对照')
    assert len(fixture['thresholds']) == 4 and all(item['passed'] for item in fixture['thresholds'])
    assert fixture['source']['hashesVerified'] == 50
    directory = builder.ROOT / builder.ARCHIVE_PATH
    assert fixture['source']['packageSha256'] == builder.sha256((directory / 'run/package/package.json').read_bytes())
    assert fixture['source']['recordsSha256'] == builder.sha256((directory / 'run/records.json').read_bytes())
    assert fixture['source']['date'] == '2026-10-23'
    assert fixture['metadata']['mode'] == 'historical_replay'
    assert fixture['metadata']['modelCallsThisBuild'] == 0
    assert fixture['metadata']['actualHistoricalBillCny'] is None


def test_context_restoration_is_byte_exact():
    fixture = builder.build_fixture()
    contexts = fixture['contexts']
    restored = contexts['labeled']
    for insertion in reversed(contexts['insertions']):
        start = insertion['offset']
        assert restored[start:start + len(insertion['text'])] == insertion['text']
        restored = restored[:start] + restored[start + len(insertion['text']):]
    assert restored.encode('utf-8') == contexts['plain'].encode('utf-8')
    assert contexts['plainBytes'] == 4559 and contexts['labeledBytes'] == 4748
    assert contexts['bodyBytesUnchanged'] and contexts['pairedRequestsMatch']


def test_hash_mismatch_fails_instead_of_reusing_success(tmp_path):
    archive = tmp_path / 'archive'
    shutil.copytree(builder.ROOT / builder.ARCHIVE_PATH, archive)
    records = archive / 'run/records.json'
    records.write_bytes(records.read_bytes() + b' ')
    with pytest.raises(ValueError, match='归档哈希不符'):
        builder.build_fixture(archive)


def test_wrong_insertion_metadata_is_rejected():
    directory = builder.ROOT / builder.ARCHIVE_PATH / 'run/package'
    package = deepcopy(builder.load(directory / 'package.json'))
    package['contexts']['L']['insertions'][0]['offset'] += 1
    with pytest.raises(ValueError, match='插入记录'):
        builder.validate_contexts(package, directory)


@pytest.mark.parametrize('change', ['question', 'temperature', 'missing', 'duplicate'])
def test_changed_pair_or_schedule_is_rejected(change):
    directory = builder.ROOT / builder.ARCHIVE_PATH / 'run/package'
    package = builder.load(directory / 'package.json')
    requests = builder.json_lines(directory / 'requests.jsonl')
    contexts = builder.validate_contexts(package, directory)
    if change == 'question':
        body = json.loads(requests[-1]['payload']['messages'][1]['content'])
        body['question'] = '另一个问题'
        requests[-1]['payload']['messages'][1]['content'] = json.dumps(body)
    elif change == 'temperature':
        requests[-1]['payload']['temperature'] = 0.2
    elif change == 'missing':
        requests.pop()
    else:
        requests[-1] = deepcopy(requests[0])
    with pytest.raises(ValueError):
        builder.validate_requests(package, requests, contexts)


def test_check_does_not_write_and_reports_stale_fixture(tmp_path):
    output = tmp_path / 'fixture.json'
    output.write_bytes(b'{}\n')
    before = output.read_bytes()
    assert builder.main(['--output', str(output), '--check']) == 1
    assert output.read_bytes() == before


def test_build_and_check_cli(tmp_path):
    output = tmp_path / 'fixture.json'
    assert builder.main(['--output', str(output)]) == 0
    assert builder.main(['--output', str(output), '--check']) == 0
    assert output.read_bytes() == (builder.ROOT / builder.OUTPUT_PATH).read_bytes()
