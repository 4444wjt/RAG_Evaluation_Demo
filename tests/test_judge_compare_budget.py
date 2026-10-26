"""Offline budget admission, recovery and custom-input tests; all model calls are fake."""

import builtins
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from tools.competition import judge_compare as jc


@pytest.fixture(autouse=True)
def guard_secrets_and_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A budget test attempted network access')

    for name in ('create_connection', 'getaddrinfo'):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, forbidden)

    def guard(original):
        def opened(file, *args, **kwargs):
            if isinstance(file, (str, bytes, Path)):
                name = Path(file).name.lower() if not isinstance(file, bytes) else ''
                if name == 'models.local.json' or name == '.env' or name.startswith('.env.'):
                    pytest.fail('A budget test attempted to read local credentials')
            return original(file, *args, **kwargs)
        return opened

    monkeypatch.setattr(builtins, 'open', guard(builtins.open))
    monkeypatch.setattr(io, 'open', guard(io.open))


@pytest.fixture
def observer_factory(monkeypatch, tmp_path):
    """Replace only LangChain's callback registration, retaining the real observer."""
    callbacks = ModuleType('langchain_core.callbacks')
    callbacks.BaseCallbackHandler = type('BaseCallbackHandler', (), {})
    context = ModuleType('langchain_core.tracers.context')
    hooks = []
    context.register_configure_hook = lambda variable, inheritable: hooks.append(variable)
    real_install = jc.install_observer
    for name, module in [('langchain_core', ModuleType('langchain_core')),
                         ('langchain_core.callbacks', callbacks), ('langchain_core.tracers.context', context)]:
        monkeypatch.setitem(sys.modules, name, module)

    def install(budget, archive=None, current=None):
        archive = archive or tmp_path
        archive.mkdir(parents=True, exist_ok=True)
        cleanup = real_install(archive, budget, current if current is not None else {'group': 'test', 'judge': 'deepseek'})
        return hooks[-1].get(), cleanup, hooks[-1]

    return install


def start(observer, run_id, text='offline', **params):
    observer.on_chat_model_start({}, [[SimpleNamespace(content=text)]], run_id=run_id,
                                 invocation_params=params)


def end(observer, run_id, usage=None):
    observer.on_llm_end(SimpleNamespace(llm_output={'token_usage': usage} if usage is not None else {},
                                       generations=[[SimpleNamespace(text='OK')]]), run_id=run_id)


def reservation(judge='deepseek'):
    return jc.Budget.charge(judge, jc.RESERVE_PROMPT_TOKENS, jc.RESERVE_COMPLETION_TOKENS)


def events(archive):
    path = archive / 'llm-events.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def test_reject_before_request_and_do_not_count_rejection(observer_factory, tmp_path):
    budget = jc.Budget(float(reservation()) / 2, 10)
    observer, cleanup, variable = observer_factory(budget)
    assert observer.raise_error and observer.run_inline
    with pytest.raises(jc.BudgetStopped):
        start(observer, 'denied')
    observer.on_llm_error(RuntimeError(), run_id='denied')
    assert budget.calls == 0 and budget.cost == 0 and not events(tmp_path)
    assert budget.snapshot()['errorCalls'] == 0
    cleanup()
    assert variable.get() is None


def test_in_flight_reservations_block_concurrent_requests(observer_factory):
    budget = jc.Budget(float(reservation()), 10)
    observer, _, _ = observer_factory(budget)

    def attempt(index):
        try:
            start(observer, str(index))
            return True
        except jc.BudgetStopped:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(8))) == 1
    status = budget.snapshot()
    assert status['inFlightCalls'] == 1 and status['calls'] == 1
    assert status['settledCny'] == 0 and status['reservedCny'] == float(reservation())


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': 0, 'completion_tokens': 0},
                                  {'prompt_tokens': 50}, {'prompt_tokens': 50, 'completion_tokens': 0}])
def test_unknown_usage_settles_reservation(observer_factory, usage):
    budget = jc.Budget(1, 10)
    observer, _, _ = observer_factory(budget)
    start(observer, 'unknown')
    end(observer, 'unknown', usage)
    assert budget.cost == reservation() and budget.calls == 1
    assert budget.snapshot()['unknownUsageCalls'] == 1
    assert budget.snapshot()['reservedCny'] == 0


def test_actual_usage_releases_reservation_and_can_admit_next(observer_factory):
    budget = jc.Budget(float(reservation()) + 0.001, 10)
    observer, _, _ = observer_factory(budget)
    start(observer, 'first')
    end(observer, 'first', {'prompt_tokens': 100, 'completion_tokens': 20})
    assert budget.cost == jc.Budget.charge('deepseek', 100, 20)
    start(observer, 'second')
    assert budget.calls == 2 and budget.snapshot()['unknownUsageCalls'] == 0


def test_error_costs_reservation_and_counts_toward_max_calls(observer_factory):
    budget = jc.Budget(1, 1)
    observer, _, _ = observer_factory(budget)
    start(observer, 'failed')
    observer.on_llm_error(TimeoutError(), run_id='failed')
    end(observer, 'failed')  # A duplicate terminal callback cannot charge twice.
    assert budget.cost == reservation() and budget.calls == 1
    assert budget.snapshot()['errorCalls'] == 1 and budget.snapshot()['unknownUsageCalls'] == 0
    with pytest.raises(jc.BudgetStopped, match='调用次数'):
        start(observer, 'next')
    assert budget.calls == 1


def test_long_prompt_and_multiple_completions_increase_reservation(observer_factory):
    budget = jc.Budget(10, 10)
    observer, _, _ = observer_factory(budget)
    text = '中' * jc.RESERVE_PROMPT_TOKENS
    start(observer, 'large', text, n=3, max_tokens=4096)
    pending = budget.pending['large']
    assert pending['promptTokens'] >= len(text.encode('utf-8'))
    assert pending['completionTokens'] == 4096 * 3


def test_restore_matches_live_accounting_and_charges_interrupted_start(observer_factory, tmp_path):
    budget = jc.Budget(10, 10, jc.RESERVE_PROMPT_TOKENS * 2, jc.RESERVE_COMPLETION_TOKENS * 2)
    observer, _, _ = observer_factory(budget)
    for run_id in ('known', 'unknown', 'failed', 'interrupted'):
        start(observer, run_id)
    end(observer, 'known', {'prompt_tokens': 123, 'completion_tokens': 45})
    end(observer, 'unknown')
    observer.on_llm_error(TimeoutError(), run_id='failed')
    restored = jc.Budget(10, 20)  # Old reservations must survive changed CLI settings.
    jc.restore_budget(tmp_path, restored)
    live = budget.snapshot()
    status = restored.snapshot()
    assert status['calls'] == 4 and status['inFlightCalls'] == 0
    assert status['settledCny'] == pytest.approx(live['settledCny'] + live['reservedCny'])
    assert status['unknownUsageCalls'] == 2 and status['errorCalls'] == 1


def test_restore_legacy_events_and_orphan_terminal_events(tmp_path):
    records = [
        {'event': 'start', 'runId': 'old-error', 'judge': 'deepseek'},
        {'event': 'error', 'runId': 'old-error', 'judge': 'deepseek'},
        {'event': 'start', 'runId': 'old-interrupted', 'judge': 'deepseek'},
        {'event': 'end', 'runId': 'orphan', 'judge': 'deepseek',
         'llmOutput': {'token_usage': {'prompt_tokens': 100, 'completion_tokens': 20}}},
        {'event': 'end', 'runId': 'orphan', 'judge': 'deepseek'},
    ]
    (tmp_path / 'llm-events.jsonl').write_text('\n'.join(map(json.dumps, records)) + '\n', encoding='utf-8')
    budget = jc.Budget(0, 0)
    jc.restore_budget(tmp_path, budget)
    assert budget.calls == 3 and budget.error_calls == 1 and budget.unknown_usage_calls == 1
    assert budget.cost == 2 * reservation() + jc.Budget.charge('deepseek', 100, 20)
    with pytest.raises(jc.BudgetStopped):
        budget.reserve('new', 'deepseek')


def test_default_reservation_covers_all_archived_usage():
    paths = list((jc.ROOT / 'docs/competition/judge-comparison').glob('*/llm-events.jsonl'))
    assert paths
    maxima = {'prompt_tokens': 0, 'completion_tokens': 0}
    for path in paths:
        for record in events(path.parent):
            if record.get('event') == 'end':
                usage = (record.get('llmOutput') or {}).get('token_usage') or {}
                for key in maxima:
                    maxima[key] = max(maxima[key], usage.get(key, 0) or 0)
    assert maxima['prompt_tokens'] > 0 and maxima['completion_tokens'] > 0
    assert jc.RESERVE_PROMPT_TOKENS >= maxima['prompt_tokens']
    assert jc.RESERVE_COMPLETION_TOKENS >= maxima['completion_tokens']


def input_row(index=0):
    return {'model': 'offline-model', 'rowIndex': index, 'question': '问题', 'answer': '回答',
            'contexts': ['离线上下文'], 'ground_truth': '参考答案', 'case_type': 'positive'}


@pytest.fixture
def fake_run(monkeypatch, observer_factory):
    state = SimpleNamespace(sent=0, judged=[], observer=None, attempts_per_row=2, swallow=False, fail=False)
    state.real_judge_row = jc.judge_row

    def install(archive, budget, current):
        state.observer, cleanup, _ = observer_factory(budget, archive, current)
        return cleanup

    class FakeLLM:
        root_client = SimpleNamespace(max_retries=3)
        root_async_client = SimpleNamespace(max_retries=3)

        def invoke(self, prompt):
            run_id = str(uuid4())
            start(state.observer, run_id, prompt, max_tokens=self.max_tokens)
            state.sent += 1  # Model request occurs strictly AFTER start admitted it.
            if state.fail:
                state.observer.on_llm_error(TimeoutError(), run_id=run_id)
                raise TimeoutError()
            end(state.observer, run_id)  # No usage -> charge the complete reservation.
            return SimpleNamespace(content='OK', response_metadata={})

    llm = FakeLLM()

    def judge_row(row, llm):
        state.judged.append(row)
        for _ in range(state.attempts_per_row):
            try:
                llm.invoke('offline metric')
            except jc.BudgetStopped:
                if not state.swallow:
                    raise
        return {'faithfulness': 0.5, 'context_precision': 1.0, 'trace': {'statements': []}}

    monkeypatch.setattr(jc, 'install_observer', install)
    monkeypatch.setattr(jc, 'build_judge', lambda judge: llm)
    monkeypatch.setattr(jc, 'judge_row', judge_row)
    monkeypatch.setattr(jc, 'dependency_versions', lambda: {'relevant': {}})
    monkeypatch.setattr(jc, 'source_versions', lambda root: {})
    state.llm = llm
    return state


def test_real_judge_row_propagates_ragas_budget_stop_and_trace_fallback(tmp_path, monkeypatch, fake_run):
    datasets = ModuleType('datasets')
    datasets.Dataset = SimpleNamespace(from_dict=lambda data: data)
    ragas = ModuleType('ragas')
    ragas.RunConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    metrics = ModuleType('ragas.metrics')
    metrics.ContextPrecision = lambda: object()
    compat = ModuleType('backend.adapters.ragas_compat')
    compat.ChineseFaithfulness = lambda: object()
    config = ModuleType('backend.config')
    config.EVAL_TIMEOUT = 10
    # Load the real trace module without services/__init__ importing document/model runtimes.
    spec = importlib.util.spec_from_file_location('backend.services.trace', jc.ROOT / 'backend/services/trace.py')
    trace = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trace)
    seen = []

    def evaluate(**kwargs):
        seen.append(kwargs)
        assert kwargs['raise_exceptions'] is True
        assert kwargs['run_config'].max_workers == kwargs['run_config'].max_retries == 1
        kwargs['llm'].invoke('first Ragas request')
        # Real Trace catches a BudgetStopped as a call error; run must still exit 3.
        return SimpleNamespace(to_pandas=lambda: {
            key: SimpleNamespace(iloc=[0.5]) for key in ('faithfulness', 'context_precision')})

    ragas.evaluate = evaluate
    for module in (datasets, ragas, metrics, compat, config, trace):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(jc, 'load_rows', lambda source: [input_row()])
    monkeypatch.setattr(jc, 'judge_row', fake_run.real_judge_row)
    archive = tmp_path / 'trace'
    assert jc.run(archive, ['self-rerun'], 1, 2) == 3
    assert seen and fake_run.sent == 2 and not jc.read_raw(archive)

    def rejecting_evaluate(**kwargs):
        kwargs['llm'].invoke('first Ragas request')
        kwargs['llm'].invoke('second Ragas request')
        pytest.fail('Budget stop was swallowed by judge_row')

    ragas.evaluate = rejecting_evaluate
    assert jc.run(tmp_path / 'ragas', ['self-rerun'], 1, 2) == 3
    assert fake_run.sent == 4


@pytest.mark.parametrize('suffix', ['.json', '.jsonl'])
def test_custom_input_cli_reads_rows_and_records_path_and_hash(tmp_path, fake_run, suffix):
    row = input_row(7 if suffix == '.jsonl' else 0)
    path = tmp_path / ('input' + suffix)
    data = row if suffix == '.jsonl' else {'model_dfs': {row['model']: [row]}}
    path.write_text(json.dumps(data, ensure_ascii=False) + '\n', encoding='utf-8')
    archive = tmp_path / 'run'
    assert jc.main(['run', str(archive), '--input', str(path), '--judge', 'qwen72b', '--group', 'injection',
                    '--reserve-prompt-tokens', '16384', '--reserve-completion-tokens', '4096']) == 0
    request = json.loads((archive / 'request.json').read_text(encoding='utf-8'))
    assert request['input'] == {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    assert request['groups'] == {'injection': {'source': str(path.resolve()), 'judge': jc.JUDGES['qwen72b']['name']}}
    assert fake_run.judged[0]['contexts'] == row['contexts'] and fake_run.judged[0]['answer'] == row['answer']
    assert jc.read_raw(archive)[0]['rowIndex'] == row['rowIndex']
    status = json.loads((archive / 'budget.json').read_text(encoding='utf-8'))
    assert status['calls'] == 3 and status['unknownUsageCalls'] == 3
    assert status['reservePromptTokens'] == 16384 and status['reserveCompletionTokens'] == 4096
    assert fake_run.llm.max_tokens == 4096
    assert fake_run.llm.max_retries == fake_run.llm.root_client.max_retries == fake_run.llm.root_async_client.max_retries == 0


def test_row_interruption_preserves_results_and_resumes(tmp_path, monkeypatch, fake_run, capsys):
    monkeypatch.setattr(jc, 'load_rows', lambda source: [input_row(0), input_row(1)])
    archive = tmp_path / 'run'
    # Preflight (1) + first row (2) + one request in row 2 (1); deny next request.
    assert jc.run(archive, ['self-rerun'], 1, 4) == 3
    assert fake_run.sent == 4 and len(jc.read_raw(archive)) == 1
    assert '预算状态' in capsys.readouterr().out
    before = (archive / 'raw.jsonl').read_bytes()
    assert jc.run(archive, ['self-rerun'], 1, 10) == 0
    assert fake_run.sent == 7 and len(jc.read_raw(archive)) == 2
    assert (archive / 'raw.jsonl').read_bytes().startswith(before)
    status = json.loads((archive / 'budget.json').read_text(encoding='utf-8'))
    assert status['calls'] == 7 and status['unknownUsageCalls'] == 7 and status['reservedCny'] == 0


@pytest.mark.parametrize('swallow', [False, True])
def test_row_budget_rejection_stops_even_when_lower_layer_swallows(tmp_path, monkeypatch, fake_run, swallow):
    monkeypatch.setattr(jc, 'load_rows', lambda source: [input_row()])
    fake_run.swallow = swallow
    archive = tmp_path / 'run'
    assert jc.run(archive, ['self-rerun'], float(reservation()) * 2, 10) == 3
    assert fake_run.sent == 2 and not jc.read_raw(archive)
    status = json.loads((archive / 'budget.json').read_text(encoding='utf-8'))
    assert status['calls'] == 2 and status['settledCny'] <= status['budgetCny']


def test_preflight_is_budgeted_and_rejects_without_sending(tmp_path, monkeypatch, fake_run):
    monkeypatch.setattr(jc, 'load_rows', lambda source: [input_row()])
    archive = tmp_path / 'run'
    assert jc.run(archive, ['self-rerun'], 0, 10) == 3
    assert fake_run.sent == 0 and not events(archive)
    assert json.loads((archive / 'budget.json').read_text(encoding='utf-8'))['calls'] == 0


def test_unexpected_model_error_still_saves_budget(tmp_path, monkeypatch, fake_run):
    monkeypatch.setattr(jc, 'load_rows', lambda source: [input_row()])
    fake_run.fail = True
    archive = tmp_path / 'run'
    with pytest.raises(TimeoutError):
        jc.run(archive, ['self-rerun'], 1, 10)
    status = json.loads((archive / 'budget.json').read_text(encoding='utf-8'))
    assert status['errorCalls'] == 1 and status['calls'] == 1 and status['settledCny'] == float(reservation())


def test_resume_rejects_changed_input_before_calling_judge(tmp_path, fake_run):
    path = tmp_path / 'input.jsonl'
    path.write_text(json.dumps(input_row()) + '\n', encoding='utf-8')
    archive = tmp_path / 'run'
    options = {'input_path': path, 'judge': 'deepseek', 'group': 'injection'}
    assert jc.run(archive, [], 1, 10, **options) == 0
    sent = fake_run.sent
    path.write_text(json.dumps({**input_row(), 'answer': 'changed'}) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA256'):
        jc.run(archive, [], 1, 10, **options)
    assert fake_run.sent == sent


def test_jsonl_rejects_missing_fields_and_duplicate_keys(tmp_path):
    path = tmp_path / 'input.jsonl'
    path.write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='必需字段'):
        jc.load_rows(path)
    path.write_text((json.dumps(input_row()) + '\n') * 2, encoding='utf-8')
    with pytest.raises(ValueError, match='重复'):
        jc.load_rows(path)


@pytest.mark.parametrize('args', [
    ['--input', 'input.json'], ['--judge', 'deepseek'], ['--group', 'injection'],
    ['--input', 'input.json', '--judge', 'deepseek', '--group', 'x', '--groups', 'independent'],
    ['--reserve-prompt-tokens', '1'], ['--reserve-completion-tokens', '1'],
    ['--budget', '-1'], ['--budget', 'nan'], ['--max-calls', '-1'],
])
def test_cli_rejects_invalid_combinations_before_running(monkeypatch, args):
    monkeypatch.setattr(jc, 'run', lambda *a, **k: pytest.fail('Invalid CLI started a run'))
    with pytest.raises(SystemExit) as raised:
        jc.main(['run', 'unused', *args])
    assert raised.value.code == 2


def test_existing_groups_cli_keeps_defaults_and_explicit_selection(monkeypatch):
    calls = []
    monkeypatch.setattr(jc, 'run', lambda *args, **kwargs: calls.append((args, kwargs)) or 0)
    assert jc.main(['run', 'unused']) == 0
    assert calls[-1][0][1] == list(jc.GROUPS)
    assert jc.main(['run', 'unused', '--groups', 'independent', 'demo-independent']) == 0
    assert calls[-1][0][1] == ['independent', 'demo-independent']
