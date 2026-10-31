"""Offline contract tests for PREREG-RAGT-v6; all data and responses are synthetic."""
import hashlib
import io
import json
from pathlib import Path
import urllib.error
import urllib.request

import pytest

from tools.competition import ragtruth_validation as rv


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('real urllib transport attempted')

    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', forbidden)
    # Replace the mapping, rather than reading/saving real credential values.
    monkeypatch.setattr(rv.os, 'environ', {})


@pytest.fixture
def paid_environment():
    rv.os.environ.update(VS_ALLOW_PAID='1', OPENAI_API_KEY='offline-test-placeholder')


def source_row(source_id, task):
    excerpt = 'Evidence excerpt for synthetic offline tests. ' * 8
    info = {'question': 'Which claim?', 'passages': excerpt} if task == 'QA' else \
        excerpt if task == 'Summary' else {'name': '例', 'description': excerpt}
    prompt = ('Use passages. Unable to answer based on given passages.' if task == 'QA' else
              'Summarize within 30 words:\n' + excerpt + '\noutput:' if task == 'Summary' else
              'Describe within 30 words.\nStructured data:' + json.dumps(info, ensure_ascii=False))
    return {'source_id': source_id, 'task_type': task, 'source_info': info, 'source': 'synthetic', 'prompt': prompt}


def response_row(item_id, source_id, group='N2', split='test', **extra):
    return {'id': item_id, 'source_id': source_id, 'model': 'synthetic-generator', 'temperature': 0,
            'labels': [] if group == 'N2' else [{'start': 0, 'end': 1}], 'split': split,
            'quality': 'good', 'response': 'A private synthetic answer.', **extra}


def synthetic_source(root, counts=None):
    counts = counts or dict.fromkeys(rv.TASKS, 4)
    sources, responses = [], []
    for task in rv.TASKS:
        for index in range(counts[task]):
            sid = f'test-{task}-{index}'
            sources.append(source_row(sid, task))
            for group, variants in (('N2', 2), ('N1', 1)):
                for variant in range(variants):
                    quality = ('good', 'incorrect_refusal', 'truncated')[index % 3]
                    responses.append(response_row(f'{sid}-{group}-{variant}', sid, group, quality=quality))
        for index in range(2):
            sid = f'train-{task}-{index}'
            sources.append(source_row(sid, task))
            responses.append(response_row(sid, sid, split='train'))
    root.mkdir(parents=True, exist_ok=True)
    for name, rows in (('source_info.jsonl', sources), ('response.jsonl', responses)):
        (root / name).write_bytes(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows).encode('utf-8'))
    return sources, responses


def make_package(root, group_sizes=None, counts=None):
    synthetic_source(root / 'source', counts)
    directory = root / 'run'
    package = rv.prepare(directory, root / 'source', verify_hashes=False,
                         group_sizes=group_sizes or {'N2': 6, 'N1': 3})
    return directory, package


def reply_payload(*, model='server-model', content=None, usage='default', finish_reason='stop', **message_extra):
    if content is None:
        content = json.dumps({'statements': [{'text': 'private synthetic statement',
                                            'verdict': 'supported', 'evidence': 'Evidence'}]})
    return {'model': model, 'choices': [{'message': {'content': content, **message_extra},
                                        'finish_reason': finish_reason}],
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 20} if usage == 'default' else usage}


def http_reply(payload=None, status=200):
    return rv.HTTPResponse(status, json.dumps(payload if payload is not None else reply_payload(),
                                              ensure_ascii=False).encode('utf-8'))


class FakeTransport:
    def __init__(self, replies=(), probe=None, before_post=None):
        self.replies = list(replies)
        self.probe = probe or rv.HTTPResponse(200, b'{"data":[{"id":"deepseek-flash"}]}')
        self.requests, self.bodies = [], []
        self.before_post = before_post

    def __call__(self, request, timeout):
        assert timeout == 180
        self.requests.append((request.get_method(), request.full_url))
        if request.get_method() == 'GET':
            result = self.probe
        else:
            self.bodies.append(json.loads(request.data))
            if self.before_post:
                self.before_post()
            result = self.replies.pop(0) if self.replies else http_reply()
        if isinstance(result, BaseException):
            raise result
        return result


def adapter_for(root, transport, *, baseline=None, parameters=None):
    ledger = rv.Ledger(root / 'ledger.jsonl')
    adapter = rv.DeepSeekAdapter('offline-test-placeholder', 'item', root,
                                 {'request_parameters': parameters or rv.REQUEST_PARAMETERS},
                                 ledger, 0, baseline, transport)
    return adapter, ledger


@pytest.mark.parametrize('task', rv.TASKS)
def test_mapping_keeps_exact_registered_input(task):
    source = source_row('s', task)
    row = rv.map_response(response_row('r', 's', 'N1'), source)
    assert row['answer'] == 'A private synthetic answer.' and row['ground_truth'] == ''
    assert row['group'] == 'N1' and 'labels' not in row
    if task == 'QA':
        assert row['question'] == source['source_info']['question']
        assert row['contexts'] == [source['source_info']['passages']]
    elif task == 'Summary':
        assert row['question'] == 'Summarize within 30 words:'
        assert row['contexts'] == [source['source_info']]
    else:
        assert row['question'] == 'Describe within 30 words.'
        assert row['contexts'] == [json.dumps(source['source_info'], ensure_ascii=False)]


@pytest.mark.parametrize('task', ('Summary', 'Data2txt'))
@pytest.mark.parametrize('duplicate', (False, True))
def test_missing_or_duplicate_anchor_fails(task, duplicate):
    source = source_row('s', task)
    source['prompt'] = source['prompt'] * 2 if duplicate else 'No registered anchor here.'
    with pytest.raises(ValueError):
        rv.map_response(response_row('r', 's'), source)


@pytest.mark.parametrize('task,info', [('QA', {'question': [], 'passages': 'x'}), ('QA', []),
                                    ('Summary', {}), ('Data2txt', 'text')])
def test_wrong_mapping_types_fail(task, info):
    source = source_row('s', task)
    source['source_info'] = info
    with pytest.raises((TypeError, ValueError)):
        rv.map_response(response_row('r', 's'), source)


def test_eligibility_boundary_and_quality_is_not_excluded():
    row = rv.map_response(response_row('r', 's', quality='truncated'), source_row('s', 'QA'))
    row['contexts'] = ['x' * 99, 'y' * 100]
    assert not rv.eligible(row)
    row['contexts'][1] += 'z'
    assert rv.eligible(row)
    row['split'] = 'train'
    assert not rv.eligible(row) and rv.eligible(row, 'train')


def test_largest_remainders_and_tie_order_and_insufficient_stratum():
    assert rv.allocate_quotas({'QA': 4, 'Summary': 3, 'Data2txt': 2}, 5) == {'QA': 2, 'Summary': 2, 'Data2txt': 1}
    assert rv.allocate_quotas(dict.fromkeys(rv.TASKS, 3), 2) == {'QA': 1, 'Summary': 1, 'Data2txt': 0}
    assert rv.allocate_quotas(dict.fromkeys(rv.TASKS, 3), 1) == {'QA': 1, 'Summary': 0, 'Data2txt': 0}
    with pytest.raises(ValueError, match='quota'):
        rv.allocate_quotas(dict.fromkeys(rv.TASKS, 1), 4)


def test_prepare_verifies_both_hashes_before_reading_or_writing(tmp_path, monkeypatch):
    synthetic_source(tmp_path / 'source')
    monkeypatch.setattr(rv, 'read_jsonl', lambda path: pytest.fail('parsed data before hash verification'))
    with pytest.raises(ValueError, match='SHA-256'):
        rv.prepare(tmp_path / 'run', tmp_path / 'source')
    assert not (tmp_path / 'run').exists()


def test_prepare_is_byte_identical_and_contains_pool_statistics(tmp_path):
    directory, package = make_package(tmp_path)
    first = {name: (directory / name).read_bytes() for name in ('package.json', 'sample_ids.json')}
    rv.prepare(directory, tmp_path / 'source', verify_hashes=False, group_sizes={'N2': 6, 'N1': 3})
    assert first == {name: (directory / name).read_bytes() for name in first}
    assert all(b'\r\n' not in b and b.endswith(b'\n') for b in first.values())
    assert len(package['jobs']) == 14 and package['estimate']['calls'] == 14
    assert package['source_commit'] == rv.SOURCE_COMMIT
    assert package['preregistration'] == 'PREREG-RAGT-v6'
    assert all('fixed_refusal' not in i for i in package['jobs'])
    assert 'created_at' not in package
    assert package['statistics']['N2']['answers'] == 24
    assert package['statistics']['N2']['sources'] == 12
    assert package['statistics']['N1']['answers'] == 12
    assert package['statistics']['N2']['by_task']['QA']['pool']['quality'] == {
        'good': 4, 'incorrect_refusal': 2, 'truncated': 2}
    assert package['eligible_qa_prompts_without_refusal'] == 0
    assert (directory / 'trial').is_dir() and (directory / 'formal').is_dir()


def test_sample_source_uniqueness_and_trial_order(tmp_path):
    _, package = make_package(tmp_path)
    formal = [i for i in package['jobs'] if i['phase'] == 'formal']
    trial = [i for i in package['jobs'] if i['phase'] == 'trial']
    for group, n in (('N2', 6), ('N1', 3)):
        rows = [i for i in formal if i['group'] == group]
        assert len(rows) == n == len({i['source_id'] for i in rows})
    assert [i['task'] for i in trial[:3]] == list(rv.TASKS)
    assert len({i['id'] for i in trial}) == 5
    assert not {i['source_id'] for i in formal} & {i['source_id'] for i in trial}
    assert all(i['id'].startswith('train-') for i in trial)


@pytest.mark.parametrize('answer,expected', [('Unable to answer based on given passages', True),
                                          ('UNABLE TO ANSWER BASED ON GIVEN PASSAGES.', True),
                                          ('prefix unable to answer based on given passages suffix', True),
                                          ('Unable to answer from other material', False)])
def test_fixed_refusal_is_case_insensitive(answer, expected):
    assert rv.has_fixed_refusal(answer) is expected


def test_prepare_reports_missing_qa_instruction_and_context_exclusions(tmp_path):
    sources, responses = synthetic_source(tmp_path / 'source')
    sources[0]['prompt'] = 'Use passages.'
    responses[0]['response'] = 'UNABLE TO ANSWER BASED ON GIVEN PASSAGES'
    sources.append(source_row('short', 'QA'))
    sources[-1]['source_info']['passages'] = 'x' * 199
    responses.append(response_row('short', 'short'))
    for name, rows in (('source_info.jsonl', sources), ('response.jsonl', responses)):
        (tmp_path / 'source' / name).write_bytes(''.join(json.dumps(r) + '\n' for r in rows).encode())
    package = rv.prepare(tmp_path / 'run', tmp_path / 'source', verify_hashes=False,
                         group_sizes={'N2': 6, 'N1': 3})
    assert package['eligible_qa_prompts_without_refusal'] == 2
    pool = package['statistics']['N2']['by_task']['QA']['pool']
    assert pool['answers'] == 7
    assert set(pool) == {'answers', 'sources', 'quality', 'contexts_chars'}
    expected = {g: {t: {'not_test': 2 if g == 'N2' else 0, 'short_context': 0, 'fixed_refusal': 0}
                    for t in rv.TASKS} for g in ('N2', 'N1')}
    expected['N2']['QA'].update(short_context=1, fixed_refusal=1)
    assert package['exclusions'] == expected


@pytest.mark.parametrize('group', ('N2', 'N1'))
@pytest.mark.parametrize('task', rv.TASKS)
def test_refusals_excluded_from_both_phases_and_exclusions_use_first_failure(tmp_path, group, task):
    sources, responses = synthetic_source(tmp_path / 'source')
    short, long = source_row('short', task), source_row('long', task)
    if task == 'QA':
        short['source_info']['passages'] = 'x' * 199
    elif task == 'Summary':
        short['source_info'] = 'x' * 199
        short['prompt'] = 'Summarize:\n' + short['source_info']
    else:
        short['source_info'] = {'value': 'short'}
    sources.extend((short, long))
    refusal = 'Prefix UnAbLe To AnSwEr BaSeD oN gIvEn PaSsAgEs suffix.'
    rejected = [response_row('all-fail', 'short', group, 'train', response=refusal),
                response_row('short-refusal', 'short', group, response=refusal),
                response_row('test-refusal', 'long', group, response=refusal),
                response_row('train-refusal', 'long', group, 'train', response=refusal)]
    for row in rejected:
        mapped = rv.map_response(row, short if row['source_id'] == 'short' else long)
        assert mapped['fixed_refusal'] is True
        assert not rv.eligible(mapped) and not rv.eligible(mapped, 'train')
    responses.extend(rejected)
    for name, rows in (('source_info.jsonl', sources), ('response.jsonl', responses)):
        (tmp_path / 'source' / name).write_bytes(''.join(json.dumps(r) + '\n' for r in rows).encode())
    package = rv.prepare(tmp_path / 'run', tmp_path / 'source', verify_hashes=False,
                         group_sizes={'N2': 6, 'N1': 3})
    assert package['statistics']['N2']['answers'] == 24
    assert package['statistics']['N1']['answers'] == 12
    assert not {i['id'] for i in rejected} & {i['id'] for i in package['jobs']}
    expected = {g: {t: {'not_test': 2 if g == 'N2' else 0, 'short_context': 0, 'fixed_refusal': 0}
                    for t in rv.TASKS} for g in ('N2', 'N1')}
    expected[group][task]['not_test'] += 2
    expected[group][task].update(short_context=1, fixed_refusal=1)
    assert package['exclusions'] == expected
    assert list(package['exclusions']) == ['N2', 'N1']
    assert all(list(tasks) == list(rv.TASKS) for tasks in package['exclusions'].values())


def test_adapter_uses_exact_body_and_fsyncs_settlement_before_saving_original_bytes(tmp_path, monkeypatch):
    raw = b' {"model":"server-model", "choices":[{"message":{"content":null},"finish_reason":"length"}],\n' \
          b'"usage":{"prompt_tokens":10,"completion_tokens":2}}\n'
    synced = []
    original_fsync = rv.os.fsync
    original_write_bytes = Path.write_bytes

    def fsync(fd):
        synced.append(True)
        original_fsync(fd)

    monkeypatch.setattr(rv.os, 'fsync', fsync)

    def write_bytes(path, data):
        if path == tmp_path / 'raw/item.json':
            rows = rv.read_jsonl(tmp_path / 'ledger.jsonl')
            assert [r['state'] for r in rows] == ['pending', 'settled']
            assert len(synced) == 2
            assert rows[-1]['raw_response_sha256'] == hashlib.sha256(data).hexdigest()
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, 'write_bytes', write_bytes)

    def pending_is_durable():
        rows = rv.read_jsonl(tmp_path / 'ledger.jsonl')
        assert rows[-1]['state'] == 'pending' and len(synced) == 1

    transport = FakeTransport([rv.HTTPResponse(200, raw)], before_post=pending_is_durable)
    adapter, ledger = adapter_for(tmp_path, transport)
    result = adapter.invoke('unchanged prompt')
    assert result.content == ''
    assert result.response_metadata == {'model_name': 'server-model', 'finish_reason': 'length'}
    assert transport.requests == [('POST', 'https://api.deepseek.com/chat/completions')]
    assert transport.bodies == [{'model': 'deepseek-flash', 'temperature': 0, 'max_tokens': 4096,
                                 'thinking': {'type': 'disabled'}, 'stream': False,
                                 'messages': [{'role': 'user', 'content': 'unchanged prompt'}]}]
    assert (tmp_path / 'raw/item.json').read_bytes() == raw
    assert ledger.rows[-1]['basis'] == 'usage'
    assert ledger.rows[-1]['cost_cny'] == pytest.approx(0.000036)
    assert adapter.call['completion'] is True and ledger.rows[-1]['completion'] is True
    assert ledger.rows[-1]['raw_response_sha256'] == hashlib.sha256(raw).hexdigest()
    assert b'\r\n' not in (tmp_path / 'ledger.jsonl').read_bytes()


@pytest.mark.parametrize('raw,basis', [
    (b'\n\r\n \n', 'R_MAX'), (b'not JSON', 'R_MAX'), (b'\xff', 'R_MAX'),
    (b'[]', 'R_MAX'), (b'null', 'R_MAX'), (b'"text"', 'R_MAX'),
    (b'{}', 'R_MAX'), (b'{"choices":[]}', 'R_MAX'),
    (b'{"choices":{}}', 'R_MAX'), (b'{"choices":"invalid"}', 'R_MAX'),
    (b'{"choices":null,"usage":{"prompt_tokens":-1,"completion_tokens":2}}', 'R_MAX'),
    (b'{"model":"other","usage":{"prompt_tokens":10,"completion_tokens":2}}', 'usage'),
])
def test_noncompletion_200_is_call_error_without_s5_or_baseline(tmp_path, raw, basis):
    adapter, ledger = adapter_for(tmp_path, FakeTransport([rv.HTTPResponse(200, raw)]), baseline='server-model')
    trace = rv.trace_hallucinations('q', 'answer', ['Evidence'], '', adapter)
    assert trace['trace_status'] == 'call_error' and adapter.s5_reason is None
    assert adapter.baseline == 'server-model' and adapter.call['returned_model'] is None
    assert adapter.call['completion'] is False and ledger.rows[-1]['completion'] is False
    assert ledger.rows[-1]['returned_model'] is None and ledger.rows[-1]['s5_reason'] is None
    assert ledger.rows[-1]['basis'] == basis
    assert ledger.rows[-1]['cost_cny'] == pytest.approx(rv.R_MAX if basis == 'R_MAX' else 0.000036)
    assert (tmp_path / 'raw/item.json').read_bytes() == raw
    assert adapter.call['raw_response_sha256'] == hashlib.sha256(raw).hexdigest()
    assert ledger.rows[-1]['raw_response_sha256'] == adapter.call['raw_response_sha256']
    assert len(ledger.rows) == 2


def test_completion_with_leading_blank_lines_is_ok_and_saved_verbatim(tmp_path):
    raw = b'\n\r\n' + http_reply().body + b'\n \r\n'
    adapter, _ = adapter_for(tmp_path, FakeTransport([rv.HTTPResponse(200, raw)]))
    trace = rv.trace_hallucinations('q', 'answer', ['Evidence'], '', adapter)
    assert trace['trace_status'] == 'ok' and adapter.s5_reason is None
    assert adapter.call['completion'] is True
    assert adapter.call['returned_model'] == adapter.baseline == 'server-model'
    assert (tmp_path / 'raw/item.json').read_bytes() == raw


@pytest.mark.parametrize('status', (302, 401, 429, 500))
def test_adapter_non_200_is_call_error_no_s5_and_body_is_bounded(tmp_path, status):
    transport = FakeTransport([rv.HTTPResponse(status, b'x' * 70_000)])
    adapter, ledger = adapter_for(tmp_path, transport)
    trace = rv.trace_hallucinations('q', 'answer', ['Evidence'], '', adapter)
    assert trace['trace_status'] == 'call_error' and adapter.s5_reason is None
    assert adapter.call['error_type'] == 'HTTPStatusError' and adapter.call['http_status'] == status
    assert len((tmp_path / 'raw/item.json').read_bytes()) == 65_536
    assert ledger.total == rv.Decimal(str(rv.R_MAX)) and len(transport.bodies) == 1
    assert ledger.rows[-1]['raw_response_sha256'] == rv.sha256_file(tmp_path / 'raw/item.json')
    assert len(ledger.rows) == 2


def test_timeout_is_single_attempt_and_only_type_is_recorded(tmp_path):
    transport = FakeTransport([TimeoutError('transport detail must stay private')])
    adapter, ledger = adapter_for(tmp_path, transport)
    trace = rv.trace_hallucinations('q', 'a', ['Evidence'], '', adapter)
    assert trace['trace_status'] == 'call_error' and adapter.call['error_type'] == 'TimeoutError'
    assert ledger.rows[-1]['cost_cny'] == rv.R_MAX and len(transport.bodies) == 1
    assert 'transport detail' not in json.dumps(adapter.call)
    assert not (tmp_path / 'raw/item.json').exists()
    assert ledger.rows[-1]['raw_response_sha256'] is None and len(ledger.rows) == 2


@pytest.mark.parametrize('message', [{'content': []}, {'content': 7}, {}, None, []])
def test_invalid_completion_content_preserves_server_metadata_and_length_count(tmp_path, message):
    payload = reply_payload(finish_reason='length')
    payload['choices'][0]['message'] = message
    adapter, ledger = adapter_for(tmp_path, FakeTransport([http_reply(payload)]))
    trace = rv.trace_hallucinations('q', 'answer', ['Evidence'], '', adapter)
    assert trace['trace_status'] == 'call_error'
    assert adapter.call['returned_model'] == 'server-model' and adapter.call['finish_reason'] == 'length'
    assert ledger.rows[-1]['basis'] == 'usage' and len(ledger.rows) == 2
    job = {'id': 'item', 'group': 'N2', 'task': 'QA', 'quality': 'good', 'generated_model': 'synthetic'}
    trace['trace_diagnostics'].update(returned_model='trace-only-model', finish_reason='stop')
    row = rv.public_record(job, {'status': 'ok', 'trace': trace, 'call': adapter.call})
    assert row['returned_model'] == 'server-model' and row['finish_reason'] == 'length'
    assert row['conclusion'] == 'UNVERIFIED'
    assert rv.analyze_items([row])['counts']['finish_reason_length'] == 1
    untraced = rv.public_record(job, {'status': 'interrupted', 'trace': None, 'call': adapter.call})
    assert untraced['returned_model'] is None and untraced['finish_reason'] is None


def test_redirect_handler_raises_instead_of_following():
    request = urllib.request.Request(rv.ENDPOINT)
    with pytest.raises(urllib.error.HTTPError) as caught:
        rv.NoRedirect().redirect_request(request, io.BytesIO(b'redirect body'), 307, '', {}, 'https://invalid.example/')
    assert caught.value.code == 307


def test_default_transport_installs_no_redirect_and_handles_http_errors(monkeypatch):
    observed = []

    class FakeOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 302, '', {}, io.BytesIO(b'redirect body'))

    def build_opener(handler):
        observed.append(handler)
        return FakeOpener()

    monkeypatch.setattr(urllib.request, 'build_opener', build_opener)
    result = rv.urllib_transport(urllib.request.Request(rv.ENDPOINT))
    assert result == rv.HTTPResponse(302, b'redirect body') and isinstance(observed[0], rv.NoRedirect)


def test_s1_exact_boundary_and_overrun():
    assert rv.R_MAX == 2.12992 and rv.HARD_CAP == 15
    assert rv.budget_allows('12.87008')
    assert not rv.budget_allows('12.8700800001')


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': 0}, {'completion_tokens': 1},
                                  {'prompt_tokens': -1, 'completion_tokens': 2},
                                  {'prompt_tokens': 1, 'completion_tokens': 2.0},
                                  {'prompt_tokens': True, 'completion_tokens': 2}])
def test_missing_incomplete_or_non_integer_usage_reserves_r_max(usage):
    assert rv.settle_usage(usage) == {'basis': 'R_MAX', 'cost_cny': rv.R_MAX, 'usage': None}


def test_zero_usage_is_valid():
    assert rv.settle_usage({'prompt_tokens': 0, 'completion_tokens': 0}) == {
        'basis': 'usage', 'cost_cny': 0.0, 'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}


def failure_record(index, status='ok', outer='ok'):
    return {'id': str(index), 'status': outer, 'trace': {'trace_status': status}}


def test_s2_starts_at_50_strictly_above_ten_percent_and_excludes_trial():
    records = [failure_record(i, 'parse_error' if i < 6 else 'ok') for i in range(49)]
    assert rv.stopped_by_failures(records) is None
    assert rv.stopped_by_failures(records + [failure_record(49)]) == 'S2'
    exact_ten = [failure_record(i, 'invalid_structure' if i < 5 else 'ok') for i in range(50)]
    assert rv.stopped_by_failures(exact_ten) is None
    exact_ten[5] = failure_record(5, outer='interrupted')
    assert rv.stopped_by_failures(exact_ten) == 'S2'
    assert rv.stopped_by_failures(exact_ten, phase='trial') is None


def test_s3_stops_at_twenty_and_resets_after_other_status():
    errors = [failure_record(i, 'call_error') for i in range(20)]
    assert rv.stopped_by_failures(errors[:19]) is None
    assert rv.stopped_by_failures(errors) == 'S3'
    assert rv.stopped_by_failures(errors[:10] + [failure_record(10, 'parse_error')] + errors[:10]) is None
    assert rv.stopped_by_failures(errors, phase='trial') is None


@pytest.mark.parametrize('model,baseline,reason', [(None, None, 'missing_model'), ('', None, 'missing_model'),
                                                ('other', 'server-model', 'model_changed')])
def test_s5_model_missing_empty_or_changed(tmp_path, model, baseline, reason):
    adapter, _ = adapter_for(tmp_path, FakeTransport([http_reply(reply_payload(model=model))]), baseline=baseline)
    with pytest.raises(rv.BatchInvalid):
        adapter.invoke('p')
    assert adapter.s5_reason == reason
    assert adapter.call['completion'] is True


def test_completion_without_model_still_triggers_s5(tmp_path):
    payload = reply_payload()
    del payload['model']
    adapter, _ = adapter_for(tmp_path, FakeTransport([http_reply(payload)]))
    trace = rv.trace_hallucinations('q', 'answer', ['Evidence'], '', adapter)
    assert trace['trace_status'] == 'call_error' and adapter.s5_reason == 'missing_model'
    assert adapter.call['completion'] is True and adapter.call['returned_model'] is None


@pytest.mark.parametrize('field', ('reasoning_content', 'reasoning', 'thinking'))
@pytest.mark.parametrize('value', ('private reasoning', ['thought'], {'thought': 1}))
def test_s5_nonempty_reasoning_fields(tmp_path, field, value):
    adapter, _ = adapter_for(tmp_path, FakeTransport([http_reply(reply_payload(**{field: value}))]))
    with pytest.raises(rv.BatchInvalid):
        adapter.invoke('p')
    assert adapter.s5_reason == 'reasoning_field'


@pytest.mark.parametrize('value', (None, '', [], {}))
def test_s5_empty_reasoning_fields_are_allowed(value):
    payload = reply_payload(reasoning_content=value, reasoning=value, thinking=value)
    assert rv.check_response_s5(payload, None) == (None, 'server-model')


@pytest.mark.parametrize('tag', ('<think>', '</THINKING>', '<Analysis>', '</reasoning>'))
def test_s5_body_tags(tmp_path, tag):
    adapter, _ = adapter_for(tmp_path, FakeTransport([http_reply(reply_payload(content=tag + 'hidden'))]))
    with pytest.raises(rv.BatchInvalid):
        adapter.invoke('p')
    assert adapter.s5_reason == 'reasoning_tag'


def test_s5_request_mismatch_before_pending_or_network(tmp_path, monkeypatch):
    original = rv.request_body
    monkeypatch.setattr(rv, 'request_body', lambda p: {**original(p), 'max_tokens': 8192})
    transport = FakeTransport()
    adapter, ledger = adapter_for(tmp_path, transport)
    with pytest.raises(rv.BatchInvalid):
        adapter.invoke('p')
    assert adapter.s5_reason == 'request_parameters' and not transport.requests and not ledger.rows
    assert not (tmp_path / 'ledger.jsonl').exists()


def test_authorization_binds_hashes_limits_and_rejects_changed_package(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    auth = rv.authorize(directory, 'trial', 'offline tester')
    assert auth['max_requests'] == 5 and auth['hard_cap_cny'] == 15 and auth['max_retries'] == 0
    assert auth['r_max_cny'] == rv.R_MAX and set(auth['tool_sha256']) == set(rv.HASHED_TOOLS)
    assert auth['package_sha256'] == rv.sha256_file(directory / 'package.json')
    package = rv.read_json(directory / 'package.json')
    package['jobs'][0]['answer'] = 'Changed input'
    rv.write_json(directory / 'package.json', package)
    transport = FakeTransport()
    with pytest.raises(PermissionError, match='hashes'):
        rv.run(directory, 'trial', transport=transport)
    assert not transport.requests


def test_tool_hashes_normalize_crlf(tmp_path):
    for name in rv.HASHED_TOOLS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'line one\r\nline two\r\n')
    hashes = rv.tool_hashes(tmp_path)
    assert set(hashes.values()) == {rv.sha256_bytes(b'line one\nline two\n')}


@pytest.mark.parametrize('environment', ({}, {'VS_ALLOW_PAID': '1'}))
def test_run_rejects_missing_allow_flag_or_credential(tmp_path, environment):
    rv.os.environ.update(environment)
    with pytest.raises(PermissionError):
        rv.run(tmp_path, 'trial', transport=FakeTransport())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('probe', [rv.HTTPResponse(503, b'{}'), rv.HTTPResponse(200, b'{"data":[]}'),
                                 TimeoutError('private probe failure')])
def test_probe_failure_prevents_paid_calls_and_ledger_writes(tmp_path, paid_environment, probe):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    transport = FakeTransport(probe=probe)
    with pytest.raises(rv.ProbeError):
        rv.run(directory, 'trial', transport=transport)
    assert transport.requests == [('GET', 'https://api.deepseek.com/models')]
    assert not transport.bodies and not (directory / 'trial/ledger.jsonl').exists()
    assert (directory / 'trial/log.jsonl').exists() and not (directory / 'run.lock').exists()
    assert 'private probe failure' not in (directory / 'trial/log.jsonl').read_text()


def test_run_uses_production_prompt_once_and_formal_budget_includes_trial(tmp_path, paid_environment):
    directory, package = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    trial_transport = FakeTransport()
    trial = rv.run(directory, 'trial', transport=trial_transport)
    assert trial['completed'] == 5 and trial['stopped'] is None
    first_id = rv.read_json(directory / 'sample_ids.json')['trial_ids'][0]
    job = next(i for i in package['jobs'] if i['id'] == first_id)
    prompt = rv.TRACE_PROMPT_TEMPLATE.format(question=job['question'], answer=job['answer'], ground_truth='',
                                            contexts_text=json.dumps(job['contexts'], ensure_ascii=False))
    assert trial_transport.bodies[0]['messages'] == [{'role': 'user', 'content': prompt}]
    rv.authorize(directory, 'formal', 'offline tester')
    formal_transport = FakeTransport()
    formal = rv.run(directory, 'formal', transport=formal_transport)
    rows = rv.read_jsonl(directory / 'formal/ledger.jsonl')
    assert rows[0]['spent_before_cny'] == trial['spent_cny']
    assert formal['completed'] == 9 and formal['spent_cny'] == pytest.approx(14 * 0.00216)
    assert rv.run(directory, 'formal', transport=formal_transport) == formal
    assert len(formal_transport.bodies) == 9 and len(trial_transport.bodies) == 5


def test_s5_stops_immediately_and_persists_invalidation(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    transport = FakeTransport([http_reply(), http_reply(reply_payload(model='changed'))])
    state = rv.run(directory, 'trial', transport=transport)
    assert state['stopped'] == 'S5' and state['invalidated'] and state['completed'] == 2
    assert len(transport.bodies) == 2 and not rv.analyze(directory)['reportable']
    with pytest.raises(PermissionError, match='S5'):
        rv.authorize(directory, 'formal', 'offline tester')


def test_interrupted_pending_is_settled_without_resend_on_resume(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    interrupted = FakeTransport([KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        rv.run(directory, 'trial', transport=interrupted)
    rows = rv.read_jsonl(directory / 'trial/ledger.jsonl')
    assert len(rows) == 1 and rows[0]['state'] == 'pending'
    resume = FakeTransport()
    state = rv.run(directory, 'trial', transport=resume)
    records = rv.read_jsonl(directory / 'trial/records.jsonl')
    assert state['completed'] == 5 and len(resume.bodies) == 4
    assert records[0]['id'] == rows[0]['id'] and records[0]['status'] == 'interrupted'
    assert records[0]['call']['basis'] == 'R_MAX' and rv.conclusion(records[0]) == 'UNVERIFIED'
    assert state['spent_cny'] == pytest.approx(rv.R_MAX + 4 * .00216)
    assert len(rv.read_jsonl(directory / 'trial/ledger.jsonl')) == 10


def test_settled_without_record_is_also_not_resent(tmp_path):
    ledger = rv.Ledger(tmp_path / 'ledger.jsonl')
    ledger.pending('a', 0, 'synthetic start')
    ledger.settle('a', {'prompt_tokens': 1, 'completion_tokens': 1})
    records = ledger.recover(tmp_path / 'records.jsonl')
    assert records[0]['status'] == 'interrupted'
    assert len(ledger.rows) == 2
    assert ledger.recover(tmp_path / 'records.jsonl') == records


@pytest.mark.parametrize('before_settlement,reasoning', [(True, True), (False, True), (False, False)])
def test_interrupt_at_settlement_boundaries_recovers_without_resend_or_losing_s5(
        tmp_path, paid_environment, monkeypatch, before_settlement, reasoning):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    payload = reply_payload(**({'reasoning_content': 'synthetic reasoning'} if reasoning else {}))
    response = http_reply(payload)
    interrupted = FakeTransport([response])
    original_write_bytes = Path.write_bytes

    def interrupt_settlement(*args, **kwargs):
        raise KeyboardInterrupt()

    def interrupt_raw(path, data):
        if path.parent.name == 'raw':
            assert rv.read_jsonl(directory / 'trial/ledger.jsonl')[-1]['state'] == 'settled'
            raise KeyboardInterrupt()
        return original_write_bytes(path, data)

    with monkeypatch.context() as patch:
        if before_settlement:
            patch.setattr(rv.Ledger, 'settle', interrupt_settlement)
        else:
            patch.setattr(Path, 'write_bytes', interrupt_raw)
        with pytest.raises(KeyboardInterrupt):
            rv.run(directory, 'trial', transport=interrupted)
    rows = rv.read_jsonl(directory / 'trial/ledger.jsonl')
    item_id = rows[0]['id']
    raw_path = directory / 'trial/raw' / f'{item_id}.json'
    assert len(interrupted.bodies) == 1 and not raw_path.exists()
    assert not rv.read_jsonl(directory / 'trial/records.jsonl')
    assert [r['state'] for r in rows] == (['pending'] if before_settlement else ['pending', 'settled'])
    invalidated = reasoning and not before_settlement
    assert rv._invalidated(directory) is invalidated
    if not before_settlement:
        assert rows[-1]['raw_response_sha256'] == hashlib.sha256(response.body).hexdigest()
        assert rows[-1]['basis'] == 'usage'
    resume = FakeTransport()
    if invalidated:
        assert rows[-1]['s5_reason'] == 'reasoning_field'
        with pytest.raises(PermissionError, match='S5'):
            rv.run(directory, 'trial', transport=resume)
        with pytest.raises(PermissionError, match='S5'):
            rv.authorize(directory, 'formal', 'offline tester')
        assert not resume.requests
        assert rv.read_jsonl(directory / 'trial/ledger.jsonl') == rows
    else:
        state = rv.run(directory, 'trial', transport=resume)
        records = rv.read_jsonl(directory / 'trial/records.jsonl')
        assert state['completed'] == 5 and state['invalidated'] is False
        assert len(resume.bodies) == 4 and not raw_path.exists()
        assert records[0]['id'] == item_id and records[0]['status'] == 'interrupted'
        assert records[0]['call']['basis'] == ('R_MAX' if before_settlement else 'usage')
        assert records[0]['call']['cost_cny'] == pytest.approx(rv.R_MAX if before_settlement else .00216)
        rows = rv.read_jsonl(directory / 'trial/ledger.jsonl')
        assert len(rows) == 10 and sum(r['id'] == item_id and r['state'] == 'settled' for r in rows) == 1
        assert not rv._invalidated(directory)


def test_run_lock_rejects_concurrent_run_before_probe(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    (directory / 'run.lock').write_bytes(b'')
    transport = FakeTransport()
    with pytest.raises(PermissionError, match='run.lock'):
        rv.run(directory, 'trial', transport=transport)
    assert not transport.requests and (directory / 'run.lock').exists()


def test_formal_authorization_rejects_incomplete_trial(tmp_path):
    directory, _ = make_package(tmp_path)
    with pytest.raises(PermissionError, match='complete'):
        rv.authorize(directory, 'formal', 'offline tester')


def test_formal_authorization_rejects_trial_without_model_baseline(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    rv.run(directory, 'trial', transport=FakeTransport([rv.HTTPResponse(500, b'{}')] * 5))
    with pytest.raises(PermissionError, match='baseline'):
        rv.authorize(directory, 'formal', 'offline tester')


def test_formal_authorization_rejects_five_noncompletion_200s_without_baseline(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    transport = FakeTransport([rv.HTTPResponse(200, b'\n\n')] * 5)
    state = rv.run(directory, 'trial', transport=transport)
    assert state['completed'] == 5 and state['stopped'] is None and not state['invalidated']
    assert state['model_baseline'] is None and state['spent_cny'] == pytest.approx(5 * rv.R_MAX)
    records = rv.read_jsonl(directory / 'trial/records.jsonl')
    assert all(r['trace']['trace_status'] == 'call_error' and r['call']['completion'] is False for r in records)
    with pytest.raises(PermissionError, match='baseline'):
        rv.authorize(directory, 'formal', 'offline tester')


def test_formal_authorization_rejects_excess_extrapolation(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    large = reply_payload(usage={'prompt_tokens': 20_000, 'completion_tokens': 0})
    rv.run(directory, 'trial', transport=FakeTransport([http_reply(large)] * 5))
    with pytest.raises(PermissionError, match='extrapolation'):
        rv.authorize(directory, 'formal', 'offline tester')


def test_formal_authorization_rejects_no_usage_settlements(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    rv.run(directory, 'trial', transport=FakeTransport([http_reply(reply_payload(usage=None))] * 5))
    with pytest.raises(PermissionError, match='usage'):
        rv.authorize(directory, 'formal', 'offline tester')


def test_formal_run_rechecks_trial_ledger_hash(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    rv.run(directory, 'trial', transport=FakeTransport())
    rv.authorize(directory, 'formal', 'offline tester')
    path = directory / 'trial/ledger.jsonl'
    path.write_bytes(path.read_bytes() + b'\n')
    transport = FakeTransport()
    with pytest.raises(PermissionError, match='hashes'):
        rv.run(directory, 'formal', transport=transport)
    assert not transport.requests


@pytest.mark.parametrize('status', rv.UNVERIFIED_TRACE)
def test_six_failed_trace_statuses_are_unverified(status):
    assert rv.conclusion({'status': 'ok', 'trace': {'trace_status': status, 'statements': []}}) == 'UNVERIFIED'


@pytest.mark.parametrize('status', ('ok', 'partial'))
@pytest.mark.parametrize('verdict,expected', [('supported', 'PASS'), ('fact_error', 'FAIL'), ('fabrication', 'FAIL')])
def test_verdict_conclusions(status, verdict, expected):
    assert rv.conclusion({'status': 'ok', 'trace': {'trace_status': status,
                                                'statements': [{'verdict': verdict}]}}) == expected


def test_outer_failure_not_run_and_unknown_status():
    assert rv.conclusion(None) == 'NOT_RUN'
    assert rv.conclusion({'status': 'interrupted', 'trace': None}) == 'UNVERIFIED'
    with pytest.raises(ValueError, match='unknown'):
        rv.conclusion({'status': 'ok', 'trace': {'trace_status': 'new_state'}})


def analyzed_item(index, group='N2', conclusion='PASS', task='QA', **extra):
    return {'id': str(index), 'group': group, 'task': task, 'quality': 'good', 'generated_model': 'synthetic',
            'conclusion': conclusion,
            'trace_status': 'model_empty' if conclusion == 'UNVERIFIED' else None if conclusion == 'NOT_RUN' else 'ok',
            'outer_status': None if conclusion == 'NOT_RUN' else 'ok', 'finish_reason': None,
            'rejected_reasons': [], **extra}


def test_registered_metrics_denominators_cis_and_subgroups():
    items = [analyzed_item(0, conclusion='FAIL'),
             analyzed_item(1, trace_status='partial', conclusion='FAIL', rejected_reasons=['invalid_verdict']),
             analyzed_item(2), analyzed_item(3, conclusion='UNVERIFIED'), analyzed_item(4, conclusion='NOT_RUN'),
             analyzed_item(5, 'N1', 'FAIL', finish_reason='length'), analyzed_item(6, 'N1')]
    result = rv.analyze_items(items)
    assert result['metrics']['M2'] == rv._rate(2, 3)
    assert result['metrics']['M2_conservative'] == rv._rate(4, 5)
    assert result['metrics']['M2_ok'] == rv._rate(1, 2)
    assert set(result['metrics']) == {'M2', 'M2_conservative', 'M2_ok', 'M1'}
    assert result['metrics']['M1'] == rv._rate(1, 2)
    assert result['by_task']['Summary']['metrics']['M2'] == rv._rate(0, 0)
    assert result['counts']['partial'] == 1 and result['counts']['finish_reason_length'] == 1
    assert result['counts']['unverified_status'] == {'model_empty': 1}
    assert set(result['counts']) == {'sampled', 'conclusions', 'trace_status', 'partial', 'unverified_status',
                                     'not_run', 'finish_reason_length', 'rejected_reasons', 'quality', 'generated_model'}
    assert result['counts']['rejected_reasons'] == {'invalid_verdict': 1}


def test_zero_denominators_are_null_and_s4_distinguishes_two_cases():
    empty = rv.analyze_items([])
    assert empty['S4'] == 'N1_NO_VALID'
    assert all(m['rate'] is None and m['wilson95'] is None for m in empty['metrics'].values())
    inactive = rv.analyze_items([analyzed_item(0, 'N1')])
    assert inactive['S4'] == 'M1_BELOW_5_PERCENT'
    assert empty['result_wording'] != inactive['result_wording']


@pytest.mark.parametrize('n2_valid,n1_fails,invalidated,reportable', [
    (249, 5, False, False), (250, 5, False, True), (250, 4, False, False), (250, 5, True, False),
])
def test_reportable_conditions_and_exact_five_percent(n2_valid, n1_fails, invalidated, reportable):
    items = [analyzed_item(i, conclusion='PASS' if i < n2_valid else 'NOT_RUN') for i in range(300)]
    items += [analyzed_item(300 + i, 'N1', 'FAIL' if i < n1_fails else 'PASS') for i in range(100)]
    result = rv.analyze_items(items, invalidated=invalidated)
    assert result['reportable'] is reportable
    assert result['S4'] == ('M1_BELOW_5_PERCENT' if n1_fails < 5 else None)
    if reportable:
        assert result['result_wording'].startswith(
            'RAGTruth 测试集按登记抽取 300 条无标注、非固定拒答的回答，逐句核对判 FAIL 的比例为 ')


def test_public_archive_preserves_all_400_ids_four_conclusions_nulls_and_reanalysis(tmp_path):
    directory, package = make_package(tmp_path, group_sizes=rv.GROUP_SIZES, counts=dict.fromkeys(rv.TASKS, 110))
    ids = rv.read_json(directory / 'sample_ids.json')
    job = next(i for i in package['jobs'] if i['id'] == ids['formal_ids'][0])
    trace = rv.trace_hallucinations(job['question'], job['answer'], job['contexts'], '',
                                   type('ReplyLLM', (), {'invoke': lambda self, prompt: type('Reply', (), {
                                       'content': reply_payload()['choices'][0]['message']['content'],
                                       'response_metadata': {'model_name': 'server-model', 'finish_reason': 'stop'}})()})())
    rv.append_jsonl(directory / 'formal/records.jsonl', {
        'id': ids['formal_ids'][0], 'status': 'ok', 'trace': trace,
        'call': {'usage': {'prompt_tokens': 1, 'completion_tokens': 1}, 'raw_response_sha256': 'a' * 64,
                 'returned_model': 'server-model', 'finish_reason': 'stop'}})
    failed = json.loads(json.dumps(trace))
    failed['statements'][0]['verdict'] = 'fabrication'
    rv.append_jsonl(directory / 'formal/records.jsonl', {'id': ids['formal_ids'][1], 'status': 'ok', 'trace': failed})
    rv.append_jsonl(directory / 'formal/records.jsonl', {'id': ids['formal_ids'][2], 'status': 'interrupted', 'trace': None})
    prereg = tmp_path / 'synthetic-prereg.md'
    prereg.write_bytes(b'Synthetic preregistration\n')
    output = tmp_path / 'public'
    summary = rv.archive(directory, output, prereg)
    public = rv.read_json(output / 'items.json')
    assert len(public['formal']) == 400 and len(public['trial']) == 5
    assert {i['id'] for i in public['formal']} == set(ids['formal_ids'])
    assert {i['conclusion'] for i in public['formal']} == set(rv.CONCLUSIONS)
    for row in public['formal'][2:]:
        assert all(row[k] is None for k in ('trace_status', 'verdicts', 'rejected_reasons', 'usage',
                                           'returned_model', 'finish_reason', 'statement_hashes', 'raw_output_sha256'))
    assert public['formal'][0]['statement_hashes'][0]['text_sha256'] == rv.sha256_bytes(b'private synthetic statement')
    assert public['formal'][0]['returned_model'] == 'server-model'
    assert public['formal'][0]['finish_reason'] == 'stop'
    text = (output / 'items.json').read_text(encoding='utf-8')
    assert 'private synthetic statement' not in text and 'Evidence' not in text
    assert 'A private synthetic answer' not in text
    assert all(not {'source_info', 'answer', 'contexts', 'fixed_refusal'} & row.keys()
               for phase in ('formal', 'trial') for row in public[phase])
    assert rv.analyze(output) == {k: v for k, v in summary.items() if k != 'archive'}
    assert summary['archive']['complete'] is False
    assert summary['result_wording'] == '归档不完整，本批次不引用。'
    for line in (output / 'SHA256SUMS.txt').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert rv.sha256_file(output / name) == digest
    assert not list(output.rglob('raw'))


@pytest.fixture
def reportable_run(tmp_path):
    directory, package = make_package(tmp_path, group_sizes=rv.GROUP_SIZES, counts=dict.fromkeys(rv.TASKS, 110))
    for phase in ('trial', 'formal'):
        records = [{'id': job['id'], 'status': 'ok', 'trace': {
            'trace_status': 'ok', 'trace_diagnostics': {}, 'statements': [
                {'text': 'Synthetic statement', 'evidence': 'Evidence',
                 'verdict': 'fabrication' if job['group'] == 'N1' else 'supported'}]}}
                   for job in package['jobs'] if job['phase'] == phase]
        (directory / phase / 'records.jsonl').write_bytes(
            ''.join(json.dumps(r) + '\n' for r in records).encode('utf-8'))
        for name in ('authorization.json', 'run.json'):
            rv.write_json(directory / phase / name, {'synthetic': True, 'invalidated': False})
        for name in ('ledger.jsonl', 'log.jsonl'):
            (directory / phase / name).write_bytes(b'')
    prereg = tmp_path / 'synthetic-prereg.md'
    prereg.write_bytes(b'Synthetic preregistration\n')
    return directory, prereg


def rewrite_archive_checksums(directory):
    path = directory / 'SHA256SUMS.txt'
    names = [line.split('  ', 1)[1] for line in path.read_text(encoding='utf-8').splitlines()]
    path.write_bytes(''.join(f'{rv.sha256_file(directory / name)}  {name}\n' for name in names).encode('utf-8'))


ARCHIVE_REQUIRED_ARTIFACTS = (
    'items.json', 'sample_ids.json', 'summary.json', 'archive.json', 'prereg.md',
    *(f'{phase}/{name}' for phase in ('trial', 'formal')
      for name in ('authorization.json', 'ledger.jsonl', 'log.jsonl', 'run.json')),
)


@pytest.mark.parametrize('name,change,original_complete', [
    pytest.param(name, change, complete,
                 id=f'A{index:02d}-{change}-C{str(complete).lower() if change in ("B1", "B2") else "both"}')
    for index, name in enumerate(ARCHIVE_REQUIRED_ARTIFACTS, 1)
    for change in ('B1', 'B2', 'B3', 'B4')
    for complete in ((True, False) if change in ('B1', 'B2') else (True,))
])
def test_public_archive_completeness_matrix(
        tmp_path, reportable_run, capsys, name, change, original_complete):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    summary = rv.archive(directory, output, prereg)
    original = rv.read_json(output / 'archive.json')
    original['complete'] = original_complete
    if not original_complete:
        original['missing_artifacts'] = ['trial/run.json', 'formal/log.jsonl', 'trial/run.json']
    rv.write_json(output / 'archive.json', original)
    rewrite_archive_checksums(output)
    if change in ('B1', 'B3'):
        (output / name).unlink()
    elif change == 'B4':
        path = output / name
        data = path.read_bytes()
        path.write_bytes(b'!' + data[1:])
    if change in ('B1', 'B2'):
        path = output / 'SHA256SUMS.txt'
        path.write_bytes(''.join(line + '\n' for line in path.read_text(encoding='utf-8').splitlines()
                                if line.split('  ', 1)[1] != name).encode('utf-8'))
    if name in ARCHIVE_REQUIRED_ARTIFACTS[:4] or change in ('B3', 'B4'):
        with pytest.raises(ValueError):
            rv.load_public_archive(output)
        assert capsys.readouterr().out == ''
        assert rv.main(['analyze', '--dir', str(output)]) == 1
        error_type = 'FileNotFoundError' if name == 'items.json' and change in ('B1', 'B3') else 'ValueError'
        assert json.loads(capsys.readouterr().out) == {'error_type': error_type, 'http_status': None}
    else:
        _, completeness = rv.load_public_archive(output)
        assert completeness == {**original, 'complete': False,
                                'missing_artifacts': sorted(set(original['missing_artifacts']) | {name})}
        expected = {k: v for k, v in summary.items() if k != 'archive'}
        expected.update(reportable=False, result_wording='归档不完整，本批次不引用。')
        assert rv.analyze(output) == expected
        assert rv.main(['analyze', '--dir', str(output)]) == 0
        assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.parametrize('complete,missing', [
    pytest.param(False, [], id='original-complete-false'),
    pytest.param(True, ['trial/run.json', 'formal/log.jsonl', 'trial/run.json'], id='original-missing-nonempty'),
])
def test_public_archive_completeness_does_not_upgrade_self_report(
        tmp_path, reportable_run, capsys, complete, missing):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    summary = rv.archive(directory, output, prereg)
    original = rv.read_json(output / 'archive.json')
    original.update(complete=complete, missing_artifacts=missing)
    rv.write_json(output / 'archive.json', original)
    rewrite_archive_checksums(output)
    _, completeness = rv.load_public_archive(output)
    assert completeness == {**original, 'complete': False, 'missing_artifacts': sorted(set(missing))}
    expected = {k: v for k, v in summary.items() if k != 'archive'}
    expected.update(reportable=False, result_wording='归档不完整，本批次不引用。')
    assert rv.analyze(output) == expected
    assert rv.main(['analyze', '--dir', str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_public_archive_completeness_keeps_runtime_analysis_path(tmp_path, reportable_run, capsys, monkeypatch):
    directory, prereg = reportable_run
    summary = rv.archive(directory, tmp_path / 'public', prereg)
    assert not (directory / 'items.json').exists()

    def forbidden_public_loader(path):
        pytest.fail('runtime analysis called the public archive loader')

    monkeypatch.setattr(rv, 'load_public_archive', forbidden_public_loader)
    expected = {k: v for k, v in summary.items() if k != 'archive'}
    assert rv.analyze(directory) == expected
    assert rv.main(['analyze', '--dir', str(directory)]) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_public_archive_completeness_rejects_extra_manifest_entry(tmp_path, reportable_run, capsys):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    rv.archive(directory, output, prereg)
    (output / 'extra.json').write_bytes(b'{}\n')
    path = output / 'SHA256SUMS.txt'
    path.write_bytes(path.read_bytes() + f'{rv.sha256_file(output / "extra.json")}  extra.json\n'.encode('utf-8'))
    with pytest.raises(ValueError, match='unexpected archive checksum entry'):
        rv.load_public_archive(output)
    assert rv.main(['analyze', '--dir', str(output)]) == 1
    assert json.loads(capsys.readouterr().out) == {'error_type': 'ValueError', 'http_status': None}


def test_complete_archive_reanalyzes_and_retains_reportability(tmp_path, reportable_run):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    summary = rv.archive(directory, output, prereg)
    assert set(rv.REQUIRED_ARCHIVE_ARTIFACTS) == set(ARCHIVE_REQUIRED_ARTIFACTS)
    assert summary['archive']['complete'] is True and summary['archive']['missing_artifacts'] == []
    assert summary['reportable'] is True
    assert rv.analyze(output) == {k: v for k, v in summary.items() if k != 'archive'}


@pytest.mark.parametrize('missing', ['prereg.md', 'trial/authorization.json', 'formal/ledger.jsonl'])
def test_missing_archive_artifacts_disable_reportability_in_summary_and_reanalysis(tmp_path, reportable_run, missing):
    directory, prereg = reportable_run
    original = rv.analyze(directory)
    assert original['reportable'] is True
    if missing == 'prereg.md':
        prereg = None
    else:
        (directory / missing).unlink()
    output = tmp_path / 'public'
    summary = rv.archive(directory, output, prereg)
    assert summary['archive']['complete'] is False
    assert summary['archive']['missing_artifacts'] == [missing]
    for result in (summary, rv.read_json(output / 'summary.json'), rv.analyze(output)):
        assert result['reportable'] is False
        assert result['result_wording'] == '归档不完整，本批次不引用。'
        assert result['metrics'] == original['metrics']


@pytest.mark.parametrize('change,rehash', [
    ('remove_formal', False), ('remove_formal', True), ('remove_trial', True),
    ('reorder_formal', True), ('reorder_trial', True), ('duplicate_formal', True),
    ('append_formal', True), ('replace_sample_ids', True),
])
def test_public_archive_rejects_changed_ids_order_and_sample_binding(
        tmp_path, reportable_run, capsys, change, rehash):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    rv.archive(directory, output, prereg)
    if change == 'replace_sample_ids':
        ids = rv.read_json(output / 'sample_ids.json')
        ids['formal_ids'][0] = 'replacement-synthetic-id'
        rv.write_json(output / 'sample_ids.json', ids)
    else:
        public = rv.read_json(output / 'items.json')
        operation, phase = change.split('_', 1)
        rows = public[phase]
        if operation == 'remove':
            rows.pop()
        elif operation == 'reorder':
            rows[0], rows[1] = rows[1], rows[0]
        elif operation == 'duplicate':
            rows[1] = rows[0]
        else:
            rows.append(rows[0])
        rv.write_json(output / 'items.json', public)
    if rehash:
        rewrite_archive_checksums(output)
    expected = 'sample_ids hash' if change == 'replace_sample_ids' else 'IDs' if rehash else 'checksum'
    with pytest.raises(ValueError, match=expected):
        rv.analyze(output)
    assert capsys.readouterr().out == ''
    assert rv.main(['analyze', '--dir', str(output)]) == 1
    assert json.loads(capsys.readouterr().out) == {'error_type': 'ValueError', 'http_status': None}


@pytest.mark.parametrize('name', ['items.json', 'sample_ids.json', 'summary.json', 'archive.json', 'prereg.md',
                                  *[f'{phase}/{artifact}' for phase in ('trial', 'formal')
                                    for artifact in ('authorization.json', 'ledger.jsonl', 'log.jsonl', 'run.json')]])
def test_public_archive_rejects_modification_of_each_listed_artifact(tmp_path, reportable_run, name):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    rv.archive(directory, output, prereg)
    path = output / name
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='checksum'):
        rv.analyze(output)


@pytest.mark.parametrize('missing', ['items.json', 'sample_ids.json', 'summary.json', 'archive.json'])
def test_public_archive_requires_all_four_core_checksums(tmp_path, reportable_run, missing):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    rv.archive(directory, output, prereg)
    path = output / 'SHA256SUMS.txt'
    path.write_bytes(''.join(line + '\n' for line in path.read_text(encoding='utf-8').splitlines()
                            if line.split('  ', 1)[1] != missing).encode('utf-8'))
    with pytest.raises(ValueError, match='checksum list'):
        rv.analyze(output)


@pytest.mark.parametrize('kind', ['absolute', 'parent', 'nested_parent'])
def test_public_archive_rejects_unsafe_checksum_paths(tmp_path, reportable_run, kind):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    rv.archive(directory, output, prereg)
    name = str((output / 'items.json').resolve()) if kind == 'absolute' else (
        '../items.json' if kind == 'parent' else 'nested\\..\\items.json')
    path = output / 'SHA256SUMS.txt'
    path.write_bytes(path.read_bytes() + f'{rv.sha256_file(output / "items.json")}  {name}\n'.encode('utf-8'))
    with pytest.raises(ValueError, match='unsafe'):
        rv.analyze(output)


@pytest.mark.parametrize('kind', ['missing_manifest', 'malformed_manifest', 'missing_listed_file'])
def test_public_archive_rejects_missing_or_malformed_checksum_artifacts(tmp_path, reportable_run, kind):
    directory, prereg = reportable_run
    output = tmp_path / 'public'
    rv.archive(directory, output, prereg)
    if kind == 'missing_manifest':
        (output / 'SHA256SUMS.txt').unlink()
    elif kind == 'malformed_manifest':
        (output / 'SHA256SUMS.txt').write_bytes(b'not a checksum entry\n')
    else:
        (output / 'summary.json').unlink()
    with pytest.raises(ValueError):
        rv.analyze(output)


def test_cli_does_not_expose_budget_or_source_hash_overrides():
    with pytest.raises(SystemExit):
        rv.main(['prepare', '--dir', 'unused'])
    with pytest.raises(SystemExit):
        rv.main(['prepare', '--dir', 'unused', '--source-root', 'unused', '--skip-hashes'])
    with pytest.raises(SystemExit):
        rv.main(['authorize', '--dir', 'unused', '--phase', 'trial', '--by', 'tester', '--budget', '999'])


def test_cli_prepare_analyze_and_archive_smoke(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'source'
    synthetic_source(source)
    monkeypatch.setattr(rv, 'SOURCE_HASHES', {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in rv.SOURCE_HASHES})
    monkeypatch.setattr(rv, 'GROUP_SIZES', {'N2': 6, 'N1': 3})
    first, second = tmp_path / 'first', tmp_path / 'second'
    for directory in (first, second):
        assert rv.main(['prepare', '--dir', str(directory), '--source-root', str(source)]) == 0
        assert json.loads(capsys.readouterr().out)['calls'] == 14
    for name in ('package.json', 'sample_ids.json'):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert rv.main(['analyze', '--dir', str(first)]) == 0
    assert json.loads(capsys.readouterr().out)['reportable'] is False
    prereg, output = tmp_path / 'synthetic-prereg.md', tmp_path / 'archive'
    prereg.write_bytes(b'# PREREG-RAGT-v6 (synthetic)\n')
    assert rv.main(['analyze', '--dir', str(first), '--output', str(output), '--prereg', str(prereg)]) == 0
    assert json.loads(capsys.readouterr().out)['reportable'] is False
    names = {'items.json', 'summary.json', 'sample_ids.json', 'prereg.md', 'archive.json', 'SHA256SUMS.txt'}
    assert {p.name for p in output.iterdir()} == names
    assert (output / 'prereg.md').read_bytes() == prereg.read_bytes()
    checksums = {}
    for line in (output / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
        checksums[name] = digest
    assert checksums.keys() == names - {'SHA256SUMS.txt'}


def test_cli_hash_mismatch_only_prints_error_type_and_http_status(tmp_path, capsys):
    source, directory = tmp_path / 'source', tmp_path / 'run'
    synthetic_source(source)
    assert rv.main(['prepare', '--dir', str(directory), '--source-root', str(source)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'error_type': 'ValueError', 'http_status': None}
    assert captured.err == '' and not directory.exists()


def ready_formal(root, *, group_sizes=None, counts=None):
    directory, package = make_package(root, group_sizes, counts)
    rv.authorize(directory, 'trial', 'offline tester')
    trial = rv.run(directory, 'trial', transport=FakeTransport())
    rv.authorize(directory, 'formal', 'offline tester')
    return directory, package, trial


def test_s1_stops_formal_run_preserves_finished_and_never_supplements(tmp_path, paid_environment):
    directory, _, trial = ready_formal(tmp_path)
    transport = FakeTransport([rv.HTTPResponse(500, b'{}')] * 9)
    state = rv.run(directory, 'formal', transport=transport)
    assert state['stopped'] == 'S1' and state['completed'] == 7 and len(transport.bodies) == 7
    assert state['spent_cny'] == pytest.approx(trial['spent_cny'] + 7 * rv.R_MAX)
    assert rv.analyze(directory)['counts']['not_run'] == 2
    assert rv.run(directory, 'formal', transport=transport) == state and len(transport.bodies) == 7


def test_s2_integrated_run_stops_after_fiftieth_call(tmp_path, paid_environment):
    directory, _, _ = ready_formal(tmp_path, group_sizes={'N2': 45, 'N1': 15}, counts=dict.fromkeys(rv.TASKS, 20))
    bad = http_reply(reply_payload(content='non-JSON trace output'))
    transport = FakeTransport([bad] * 6 + [http_reply()] * 54)
    state = rv.run(directory, 'formal', transport=transport)
    assert state['stopped'] == 'S2' and state['completed'] == 50 and len(transport.bodies) == 50
    assert rv.analyze(directory)['counts']['not_run'] == 10


def test_s3_integrated_run_stops_on_twentieth_call_error(tmp_path, paid_environment):
    directory, _, _ = ready_formal(tmp_path, group_sizes={'N2': 24, 'N1': 6}, counts=dict.fromkeys(rv.TASKS, 10))
    # HTTP 200 with usage but invalid content type: call_error without exhausting S1 first.
    transport = FakeTransport([http_reply(reply_payload(content=['invalid content type']))] * 30)
    state = rv.run(directory, 'formal', transport=transport)
    assert state['stopped'] == 'S3' and state['completed'] == 20 and len(transport.bodies) == 20
    assert state['invalidated'] is False


def test_s5_request_drift_invalidates_batch_without_a_paid_call(tmp_path, paid_environment, monkeypatch):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    original = rv.request_body
    monkeypatch.setattr(rv, 'request_body', lambda p: {**original(p), 'stream': True})
    transport = FakeTransport()
    state = rv.run(directory, 'trial', transport=transport)
    assert state['stopped'] == 'S5' and state['invalidated'] and state['completed'] == 0
    assert not transport.bodies and not (directory / 'trial/ledger.jsonl').exists()
    assert rv.analyze(directory)['invalidated'] is True


def test_first_successful_trial_response_sets_baseline_after_http_error(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    state = rv.run(directory, 'trial', transport=FakeTransport([rv.HTTPResponse(429, b'{}')]))
    auth = rv.authorize(directory, 'formal', 'offline tester')
    assert state['completed'] == 5 and not state['invalidated'] and auth['model_baseline'] == 'server-model'
    assert auth['trial_total_cny'] == pytest.approx(rv.R_MAX + 4 * .00216)


@pytest.mark.parametrize('raw,basis', [
    (b'\n\n', 'R_MAX'),
    (b'{"model":"other","usage":{"prompt_tokens":1000,"completion_tokens":20}}', 'usage'),
])
def test_first_completion_sets_trial_baseline_after_noncompletion_200(tmp_path, paid_environment, raw, basis):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    state = rv.run(directory, 'trial', transport=FakeTransport([rv.HTTPResponse(200, raw)]))
    assert state['completed'] == 5 and state['stopped'] is None and not state['invalidated']
    assert state['model_baseline'] == 'server-model'
    records = rv.read_jsonl(directory / 'trial/records.jsonl')
    assert records[0]['trace']['trace_status'] == 'call_error'
    assert records[0]['call']['completion'] is False and records[0]['call']['returned_model'] is None
    assert all(r['call']['completion'] is True and r['trace']['trace_status'] == 'ok' for r in records[1:])
    auth = rv.authorize(directory, 'formal', 'offline tester')
    assert auth['model_baseline'] == 'server-model'
    assert auth['trial_total_cny'] == pytest.approx((rv.R_MAX if basis == 'R_MAX' else .00216) + 4 * .00216)


def test_resume_restores_first_completion_baseline_and_checks_model_drift(tmp_path, paid_environment):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    interrupted = FakeTransport([rv.HTTPResponse(200, b'\n\n'), http_reply(), KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        rv.run(directory, 'trial', transport=interrupted)
    resume = FakeTransport([http_reply(reply_payload(model='changed'))])
    state = rv.run(directory, 'trial', transport=resume)
    records = rv.read_jsonl(directory / 'trial/records.jsonl')
    assert state['stopped'] == 'S5' and state['invalidated'] and state['model_baseline'] == 'server-model'
    assert state['completed'] == 4 and len(resume.bodies) == 1
    assert records[0]['call']['completion'] is False
    assert records[1]['call']['completion'] is True and records[2]['status'] == 'interrupted'
    assert records[-1]['call']['s5_reason'] == 'model_changed'


def test_run_rechecks_tool_and_sample_id_hashes(tmp_path, paid_environment, monkeypatch):
    directory, _ = make_package(tmp_path)
    rv.authorize(directory, 'trial', 'offline tester')
    actual_hashes = rv.tool_hashes()
    monkeypatch.setattr(rv, 'tool_hashes', lambda: {**actual_hashes, rv.HASHED_TOOLS[0]: 'changed'})
    transport = FakeTransport()
    with pytest.raises(PermissionError):
        rv.run(directory, 'trial', transport=transport)
    assert not transport.requests
    monkeypatch.setattr(rv, 'tool_hashes', lambda: actual_hashes)
    path = directory / 'sample_ids.json'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(PermissionError, match='sample_ids'):
        rv.run(directory, 'trial', transport=transport)
    assert not transport.requests
