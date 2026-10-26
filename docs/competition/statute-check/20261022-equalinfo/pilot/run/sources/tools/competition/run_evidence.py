"""Archive the unchanged local Web API in an isolated directory. No model call by default."""
from __future__ import annotations

import argparse
import ast
from contextvars import ContextVar
from datetime import date, datetime, timezone
import hashlib
import http.client
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
from threading import Thread
import time
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = Path('scenarios/competition_25')
MODELS = ('deepseek-chat', 'Pro/Qwen/Qwen2.5-7B-Instruct', 'glm-5.2-fast-preview')
ARCHIVE_SCHEMA = 2
METRICS = ('faithfulness', 'context_precision', 'answer_relevancy', 'answer_correctness', 'context_recall')
EXCLUDED_DIRS = {'chroma_db', '__pycache__', '.git'}


def unknown(reason: str) -> dict:
    return {'status': 'unknown', 'value': None, 'reason': reason}


def digest_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def dependency_versions() -> dict:
    packages = dict(sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions() if d.metadata['Name']))
    normalized = {name.lower().replace('_', '-'): version for name, version in packages.items()}
    relevant = ('ragas', 'pysbd', 'langchain-core', 'langchain-openai', 'langchain-community', 'openai', 'sentence-transformers', 'transformers', 'torch', 'huggingface-hub', 'chromadb', 'datasets')
    return {'packages': packages, 'relevant': {name: normalized.get(name, unknown('未找到该发行包的版本信息。')) for name in relevant}}


def source_versions(project: Path) -> dict:
    """Read frozen source without importing config, loading secrets or building models."""
    adapter = project / 'backend/adapters/ragas_compat.py'
    sentence = unknown('冻结源码中未找到明确的句子适配版本。')
    if adapter.exists():
        for node in ast.parse(adapter.read_text(encoding='utf-8-sig')).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SENTENCE_ADAPTER_VERSION' for t in node.targets):
                sentence = ast.literal_eval(node.value)
    prompts = {}
    for name, relative, variable in (
        ('generator', 'backend/services/answering.py', 'prompt'),
        ('trace', 'backend/services/trace.py', 'TRACE_PROMPT_TEMPLATE'),
    ):
        source = project / relative
        info = unknown('冻结源码缺少提示词定义。')
        if source.exists():
            for node in ast.walk(ast.parse(source.read_text(encoding='utf-8-sig'))):
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        info = {'sha256': hashlib.sha256(node.value.value.encode('utf-8')).hexdigest(), 'hashKind': 'literal-template-utf8', 'path': relative}
                    elif isinstance(node.value, ast.JoinedStr):
                        info = {'sha256': hashlib.sha256(ast.dump(node.value, include_attributes=False).encode('utf-8')).hexdigest(), 'hashKind': 'f-string-ast-including-placeholder-expressions', 'path': relative}
        prompts[name] = info
    prompts['ragas'] = unknown('在隔离API进程启动后记录实际指标实例的提示词。')
    return {'sentenceAdapterVersion': sentence, 'traceVersion': prompts['trace'], 'promptVersions': prompts}


def entrypoint_relationship(root: Path) -> dict:
    """Inspect the executable wrapper rather than inferring from unchanged main.py."""
    path = root / 'main.py'
    if not path.exists():
        return unknown('未提供main.py，无法核实CLI入口。')
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    wrapper = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_pipeline'), None)
    shared = wrapper is not None and any(
        isinstance(n, ast.ImportFrom) and n.module == 'backend.core.pipeline' and any(a.name == 'evaluate' for a in n.names)
        for n in ast.walk(wrapper)
    ) and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'evaluate' for n in ast.walk(wrapper))
    return {'mainSha256': sha256(path), 'runPipelineDelegatesToBackendEvaluate': shared,
            'impact': '当前CLI、Gradio与Web API共享后端流水线，均受句子适配影响；保留legacy函数不等于当前CLI使用legacy。' if shared else '未发现已知委托形式；需要人工核对，不能认定CLI隔离。'}


def archive_files(archive: Path):
    for path in sorted(archive.rglob('*')):
        relative = path.relative_to(archive)
        if path.is_file() and not any(part in EXCLUDED_DIRS for part in relative.parts) and path.name != 'checksums.json':
            yield path


def write_checksums(archive: Path) -> None:
    write_json(archive / 'checksums.json', {'algorithm': 'SHA-256', 'excludes': ['checksums.json', *sorted(EXCLUDED_DIRS)],
        'files': [{'path': p.relative_to(archive).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in archive_files(archive)]})


def verify_checksums(archive: Path) -> None:
    index = json.loads((archive / 'checksums.json').read_text(encoding='utf-8'))
    expected = {item['path']: item['sha256'] for item in index['files']}
    actual = {p.relative_to(archive).as_posix(): sha256(p) for p in archive_files(archive)}
    if expected != actual:
        raise ValueError('归档文件哈希或文件清单发生变化，禁止继续调用模型。')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def validate_cases(root: Path, *, require_author: bool = False) -> dict:
    folder = root / CASE_DIR
    manifest = json.loads((folder / 'case-manifest.json').read_text(encoding='utf-8-sig'))
    rows = json.loads((folder / 'testset.json').read_text(encoding='utf-8-sig'))
    pilot = json.loads((folder / 'pilot.json').read_text(encoding='utf-8-sig'))
    if len(rows) != 25 or len({row['question'] for row in rows}) != 25:
        raise ValueError('案例须包含25个不同问题。')
    for name, key in [('testset.json', 'testsetSha256'), ('pilot.json', 'pilotSha256')]:
        if sha256(folder / name) != manifest[key]:
            raise ValueError('案例文件哈希不一致，须重新确认材料。')
    doc = root / manifest['document']['path']
    if not doc.resolve().is_relative_to(root.resolve()) or sha256(doc) != manifest['document']['sha256']:
        raise ValueError('法规节选与冻结材料不对应。')
    lines = doc.read_text(encoding='utf-8-sig').splitlines()
    records = manifest['cases']
    if len(records) != 25 or len({r['id'] for r in records}) != 25:
        raise ValueError('案例索引必须唯一且完整。')
    categories = {'direct': 10, 'conditional': 5, 'missing': 5, 'false_premise': 5}
    if {k: sum(r['category'] == k for r in records) for k in categories} != categories:
        raise ValueError('案例类型范围不符合计划。')
    for index, record in enumerate(records):
        expected = 'negative' if record['category'] == 'missing' else 'positive'
        if record['rowIndex'] != index or rows[index]['case_type'] != expected:
            raise ValueError('案例类型与现有拒答统计口径不对应。')
        if not rows[index]['ground_truth'].strip() or not record['evidence']:
            raise ValueError('参考答案或来源位置缺失。')
        for evidence in record['evidence']:
            if evidence['path'] != manifest['document']['path']:
                raise ValueError('引用必须来自冻结的法规节选。')
            line = evidence['line']
            if not isinstance(line, int) or not 1 <= line <= len(lines) or evidence['quote'] not in lines[line - 1]:
                raise ValueError('引用无法在标记的原文行中定位。')
    ids = manifest['pilotIds']
    selected = [rows[next(i for i, r in enumerate(records) if r['id'] == key)] for key in ids]
    if len(ids) != 5 or len(set(ids)) != 5 or pilot != selected:
        raise ValueError('预跑题与25题来源不一致。')
    if require_author:
        review = manifest['authorValidation']
        if review.get('status') != 'confirmed' or not str(review.get('reviewer') or '').strip() or not str(review.get('note') or '').strip():
            raise ValueError('正式调用前须由作者实际确认25题参考答案与依据，不能把AI草稿当成人工确认。')
        try:
            reviewed = date.fromisoformat(review['reviewedAt'])
        except (ValueError, TypeError, KeyError):
            raise ValueError('作者确认必须有实际日期。') from None
        if reviewed > date.today():
            raise ValueError('作者确认日期不能在未来。')
    return manifest


def configured_model(project: Path, name: str) -> dict:
    """Built-in model first, then the local registry copied into the isolated project; never guess a config."""
    from backend import config
    from backend.core import model_registry
    builtin = [item['name'] for item in config.MODELS_CONFIG]
    found = next((item for item in config.MODELS_CONFIG if item['name'] == name), None) or next(
        (item for item in model_registry.load(project, builtin) if item['name'] == name), None)
    if found is None:
        raise ValueError(f'模型未配置：{name}')
    return found


def prepare(root: Path, batch: str, model: str, judge: str | None = None) -> Path:
    manifest = validate_cases(root)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    archive = root / 'work' / 'competition' / 'runs' / f'{stamp}-{batch}-{uuid4().hex[:8]}'
    project = archive / 'project'
    project.mkdir(parents=True, exist_ok=False)
    copied = []
    for source in sorted((root / 'backend').rglob('*.py')):
        relative = source.relative_to(root)
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({'path': relative.as_posix(), 'sha256': sha256(target)})
    if (root / 'main.py').exists():
        shutil.copy2(root / 'main.py', project / 'main.py')
        copied.append({'path': 'main.py', 'sha256': sha256(project / 'main.py')})
    for relative in [Path(manifest['document']['path']), *[CASE_DIR / name for name in ('testset.json', 'pilot.json', 'case-manifest.json')]]:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
        copied.append({'path': relative.as_posix(), 'sha256': sha256(target)})
    if judge and (root / 'models.local.json').exists():
        # Locally registered judges live here; it holds names, addresses and env-var names, never keys.
        shutil.copy2(root / 'models.local.json', project / 'models.local.json')
        copied.append({'path': 'models.local.json', 'sha256': sha256(project / 'models.local.json')})
    for name in ('requirements.txt', 'requirements_new.txt'):
        shutil.copy2(root / name, archive / name)
    (archive / 'runner').mkdir()
    shutil.copy2(Path(__file__), archive / 'runner/run_evidence.py')
    dependencies = dependency_versions()
    write_json(archive / 'packages.json', dependencies['packages'])
    try:
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        git_status = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).splitlines()
        dirty = bool(git_status)
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
        git_status = unknown('无法取得Git状态；源码哈希仍单独记录。')
    request = {'doc_path': manifest['document']['path'], 'testset_path': (CASE_DIR / ('pilot.json' if batch == 'pilot' else 'testset.json')).as_posix(), 'model_names': [model], 'faith_threshold': 0.8, 'ctx_prec_threshold': 0.8, 'version_label': archive.name}
    if judge:
        request['judge_model_name'] = judge
    write_json(archive / 'request.json', request)
    write_json(archive / 'manifest.json', {
        'schemaVersion': ARCHIVE_SCHEMA, 'kind': 'current-web-api-independent-run', 'status': 'prepared',
        'preparedAt': datetime.now(timezone.utc).isoformat(), 'codeCommit': commit,
        'workingTreeDirty': dirty, 'runtime': {'python': platform.python_version(), 'platform': platform.platform()},
        'runnerSha256': sha256(archive / 'runner/run_evidence.py'), 'runnerPath': 'runner/run_evidence.py', 'sourceFiles': copied,
        'gitStatus': git_status, 'entrypoints': entrypoint_relationship(root),
        'versions': {**source_versions(project), 'dependencies': dependencies['relevant']},
        'sourceTreeSha256': hashlib.sha256(json.dumps(copied, sort_keys=True).encode()).hexdigest(),
        'caseIds': manifest['pilotIds'] if batch == 'pilot' else [r['id'] for r in manifest['cases']],
        'authorValidation': manifest['authorValidation'], 'request': request,
        'integrity': {'index': 'checksums.json', 'manifestChecksum': '见外部checksums.json，不在manifest中自引用。', 'excludes': sorted(EXCLUDED_DIRS)},
        'limitations': [f'生成使用{model}；Ragas Judge与Trace使用单独配置的裁判{judge}。' if judge else '生成、Ragas Judge与Trace使用当前同一模型；不是独立裁判。', '不是旧冻结Demo复跑、阈值校准或总体准确率证明。', '同机隔离源码目录使用既有Python依赖与Embedding缓存，不是全新操作系统验证。', '实际扣费仅能由作者账单确认，token观测不是账单。'],
    })
    write_checksums(archive)
    return archive


def runtime_metadata(archive: Path) -> dict:
    """Capture actual frozen API configuration; never instantiate a model here."""
    from backend import config
    from backend.core.pipeline import RAGAS_METRICS
    from backend.adapters.ragas_compat import SENTENCE_ADAPTER_VERSION
    from ragas import RunConfig
    from ragas.llms.prompt import Prompt
    request = json.loads((archive / 'request.json').read_text(encoding='utf-8'))
    model = request['model_names'][0]
    selected = next(item for item in config.MODELS_CONFIG if item['name'] == model)
    judge = request.get('judge_model_name')
    judge_selected = configured_model(archive / 'project', judge) if judge else selected
    prompts, parameters = {}, {}
    for metric in RAGAS_METRICS:
        definitions = {name: value.dict() for name, value in vars(metric).items() if isinstance(value, Prompt)}
        prompts[metric.name] = {name: {'sha256': digest_json(value), 'hashKind': 'canonical-json-utf8', 'definition': value} for name, value in definitions.items()}
        parameters[metric.name] = {name: getattr(metric, name) for name in ('weights', 'strictness', '_reproducibility', 'max_retries') if hasattr(metric, name)}
    write_json(archive / 'prompt-definitions.json', prompts)
    model_config = {'requestedModel': model, 'actualReturnedModel': unknown('以llm-events.jsonl中的逐次提供方响应为准，尚未调用。'),
        'endpoint': selected['base_url'], 'temperature': 0, 'timeout': config.LLM_TIMEOUT, 'maxRetries': config.LLM_MAX_RETRIES,
        'maxTokens': unknown('未显式设置，提供方默认上限未知。'),
        'extraBody': {'enable_thinking': False} if selected.get('provider') == 'bailian' or model.lower().startswith('glm') else None,
        'modelWeightsRevision': unknown('远端服务未提供不可变权重版本。')}
    judge_config = dict(model_config) if not judge else {**model_config, 'requestedModel': judge, 'endpoint': judge_selected['base_url'],
        'extraBody': {'enable_thinking': False} if judge_selected.get('provider') == 'bailian' or judge.lower().startswith('glm') else None}
    source_info = source_versions(archive / 'project')
    source_info['promptVersions']['ragas'] = {name: {key: {k: v for k, v in item.items() if k != 'definition'} for key, item in definitions.items()} for name, definitions in prompts.items()}
    run_defaults = {name: RunConfig.__dataclass_fields__[name].default for name in ('max_retries', 'max_wait', 'log_tenacity', 'seed')}
    run_defaults.update(max_workers=1, timeout=config.EVAL_TIMEOUT,
        exception_types=[f'{value.__module__}.{value.__name__}' for value in RunConfig.__dataclass_fields__['exception_types'].default])
    return {'capturedAt': datetime.now(timezone.utc).isoformat(), 'sentenceAdapterVersion': SENTENCE_ADAPTER_VERSION,
        'versions': {**source_info, 'sentenceAdapterVersion': SENTENCE_ADAPTER_VERSION,
            'dependencies': dependency_versions()['relevant'], 'ragasPrompts': {name: {key: {k: v for k, v in item.items() if k != 'definition'} for key, item in definitions.items()} for name, definitions in prompts.items()}},
        'configuration': {'generator': model_config, 'ragasJudge': judge_config, 'traceJudge': dict(judge_config),
            'traceEnabled': config.ENABLE_TRACE, 'chunkSize': config.CHUNK_SIZE, 'chunkOverlap': config.CHUNK_OVERLAP,
            'topK': config.TOP_K, 'reuseVectorDb': config.REUSE_VECTOR_DB, 'ragasMaxWorkers': 1,
            'evaluationTimeout': config.EVAL_TIMEOUT, 'ragasRunConfig': run_defaults, 'metricParameters': parameters,
            'gateThresholds': {'faithfulness': request['faith_threshold'], 'contextPrecision': request['ctx_prec_threshold']},
            'embedding': {'configuredIdentifier': config.EMBEDDING_MODEL, 'localFilesOnly': True,
                'implementation': 'langchain_community.embeddings.HuggingFaceEmbeddings',
                'resolvedSource': unknown('现有流水线未暴露实际加载路径，本工具不推测缓存命中。'),
                'weightsRevision': unknown('未取得实际加载权重的不可变版本。'),
                'weightsSha256': unknown('未对实际加载的Embedding权重进行观测。')}}}


def http_json(port: int, method: str, path: str, body: dict | None = None, timeout: float = 10):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request(method, path, None if body is None else json.dumps(body, ensure_ascii=False).encode(), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def serve(archive: Path, port: int) -> None:
    """Observe callbacks without changing prompts, models, API or metric behavior."""
    project = archive / 'project'
    os.chdir(project)
    sys.path.insert(0, str(project))
    os.environ['MPLBACKEND'] = 'Agg'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['ANONYMIZED_TELEMETRY'] = 'False'
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tracers.context import register_configure_hook
    from threading import Lock

    try:
        write_json(archive / 'runtime.json', runtime_metadata(archive))
    except Exception as error:
        write_json(archive / 'runtime.json', {'status': 'unknown', 'reason': '运行配置观测失败，不据此推断版本。', 'errorType': type(error).__name__})
    (archive / 'llm-events.jsonl').touch(exist_ok=False)

    class Observer(BaseCallbackHandler):
        def __init__(self):
            self.lock = Lock()

        def record(self, event: dict) -> None:
            try:
                event['at'] = datetime.now(timezone.utc).isoformat()
                with self.lock, (archive / 'llm-events.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(event, ensure_ascii=False, default=str, allow_nan=False) + '\n')
            except Exception:
                pass  # Observation must not change an evaluation outcome.

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            params = kwargs.get('invocation_params') or {}
            self.record({'event': 'start', 'runId': str(run_id), 'parentRunId': str(kwargs['parent_run_id']) if kwargs.get('parent_run_id') else None,
                'requestParameters': {key: params[key] for key in ('model', 'model_name', 'temperature', 'max_tokens', 'n') if key in params},
                'messages': [[{'role': m.type, 'content': m.content} for m in batch] for batch in messages]})

        def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
            self.record({'event': 'start', 'runId': str(run_id), 'prompts': prompts})

        def on_llm_end(self, response, *, run_id, **kwargs):
            self.record({'event': 'end', 'runId': str(run_id), 'llmOutput': response.llm_output,
                'generations': [[g.text for g in batch] for batch in response.generations],
                'generationInfo': [[g.generation_info for g in batch] for batch in response.generations]})

        def on_llm_error(self, error, *, run_id, **kwargs):
            self.record({'event': 'error', 'runId': str(run_id), 'errorType': type(error).__name__})

    observer = Observer()
    register_configure_hook(ContextVar('competition_evidence_observer', default=observer), True)
    import uvicorn
    uvicorn.run('backend.api.app:create_app', factory=True, host='127.0.0.1', port=port, workers=1, log_level='info')


def summarize_events(archive: Path) -> dict:
    events_path = archive / 'llm-events.jsonl'
    events, unreadable = [], 0
    if events_path.exists():
        for line in events_path.read_text(encoding='utf-8', errors='replace').splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict) or item.get('event') not in ('start', 'end', 'error'):
                    raise ValueError('unknown event')
                events.append(item)
            except ValueError:
                unreadable += 1
    ends = [item for item in events if item['event'] == 'end']
    outputs = [item.get('llmOutput') if isinstance(item.get('llmOutput'), dict) else {} for item in ends]
    usages = [output.get('token_usage') for output in outputs]
    reported = [value for value in usages if isinstance(value, dict)]
    names = {}
    for output in outputs:
        name = output.get('model_name')
        if isinstance(name, str) and name:
            names[name] = names.get(name, 0) + 1
    cached = [value.get('prompt_cache_hit_tokens', (value.get('prompt_tokens_details') or {}).get('cached_tokens')) for value in reported]
    return {'startedCalls': sum(item['event'] == 'start' for item in events), 'completedCalls': len(ends), 'errorEvents': sum(item['event'] == 'error' for item in events),
        'callsWithTokenUsage': len(reported), 'promptTokens': sum(value.get('prompt_tokens', 0) for value in reported),
        'completionTokens': sum(value.get('completion_tokens', 0) for value in reported),
        'totalTokens': sum(value.get('total_tokens', value.get('prompt_tokens', 0) + value.get('completion_tokens', 0)) for value in reported),
        'cacheHitTokens': sum(v for v in cached if isinstance(v, int)), 'callsWithCacheUsage': sum(isinstance(v, int) for v in cached),
        'cacheCoverage': 'reported' if cached and len(cached) == len(ends) and all(isinstance(v, int) for v in cached) else 'partial-or-unknown',
        'actualReturnedModels': names if names else unknown('日志未记录提供方返回模型名。'), 'callsWithoutReturnedModel': len(ends) - sum(names.values()),
        'observationStatus': ('partial' if unreadable else 'observed') if events_path.exists() else 'unknown', 'unreadableEventLines': unreadable,
        'actualCharge': None, 'actualChargeStatus': 'unknown',
        'note': '观察到的LangChain调用及提供方返回token，不保证覆盖HTTP层重试；缺失用量不按零消耗解释，实际费用未取得账单。'}


def summarize_quality(payload: dict) -> dict:
    models = {}
    for model, rows in payload.get('model_dfs', {}).items():
        valid = {name: 0 for name in METRICS}
        trace_counts = {'nonempty': 0, 'empty': 0, 'missing': 0, 'invalid': 0}
        for row in rows:
            for name in METRICS:
                value = row.get(name)
                valid[name] += int(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
            trace = row.get('hallucination_trace')
            if trace is None:
                trace_counts['missing'] += 1
                continue
            try:
                trace = json.loads(trace) if isinstance(trace, str) else trace
            except (TypeError, ValueError):
                trace = None
            if not isinstance(trace, dict) or not isinstance(trace.get('statements'), list):
                trace_counts['invalid'] += 1
            else:
                trace_counts['nonempty' if trace['statements'] else 'empty'] += 1
        models[model] = {'answerCount': len(rows), 'metrics': {name: {'valid': count, 'missing': len(rows) - count} for name, count in valid.items()}, 'trace': trace_counts}
    return {'models': models, 'note': '运行完成、指标覆盖与Trace覆盖分别记录。空Trace可能来自拒答、抽取、调用或解析，不能据空列表确认原因；不重算分数或门禁。'}


def finish_archive(archive: Path, metadata: dict) -> dict:
    if metadata.get('schemaVersion') != ARCHIVE_SCHEMA:
        raise ValueError('仅补全新版本归档，禁止向旧run补写新记录。')
    observation = summarize_events(archive)
    metadata['tokenObservation'] = observation
    write_json(archive / 'usage.json', observation)
    runtime = archive / 'runtime.json'
    if runtime.exists():
        try:
            captured = json.loads(runtime.read_text(encoding='utf-8'))
            if not isinstance(captured, dict):
                raise ValueError('unknown runtime structure')
        except ValueError:
            captured = unknown('运行配置文件不可解析，原文件保留，不推断缺失版本。')
        metadata['runtimeObservation'] = captured
        if isinstance(captured.get('versions'), dict) and isinstance(captured.get('configuration'), dict):
            metadata['versions'] = captured['versions']
            metadata['configuration'] = captured['configuration']
            for role in ('generator', 'ragasJudge', 'traceJudge'):
                if isinstance(metadata['configuration'].get(role), dict):
                    metadata['configuration'][role]['actualReturnedModel'] = {
                        'scope': '全运行日志的返回模型汇总，逐次对应关系见llm-events.jsonl；不推断不可变模型版本。',
                        'models': observation['actualReturnedModels']}
    response = archive / 'api-response.json'
    if response.exists():
        try:
            payload = json.loads(response.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or not isinstance(payload.get('model_dfs'), dict) or any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) for rows in payload['model_dfs'].values()):
                raise ValueError('unknown API result structure')
        except ValueError:
            payload = None
            metadata['apiResponseParseStatus'] = unknown('API响应不可解析为评测结果，原始响应字节已保留。')
        else:
            metadata['apiResponseParseStatus'] = 'parsed'
        metadata['resultCoverage'] = summarize_quality(payload) if payload is not None else unknown('缺少可解析的模型结果，不表示零指标或空Trace。')
        write_json(archive / 'result-coverage.json', metadata['resultCoverage'])
        if payload is not None:
            for name, fields in (
                ('answers', ('question', 'answer', 'contexts', 'ground_truth', 'case_type')),
                ('metrics', METRICS), ('traces', ('hallucination_trace',)),
            ):
                write_json(archive / f'{name}.json', {model: [{'rowIndex': i, **{field: row.get(field) for field in fields}} for i, row in enumerate(rows)] for model, rows in payload.get('model_dfs', {}).items()})
    else:
        metadata['resultCoverage'] = unknown('尚无可解析的API响应，不代表指标为零。')
    metadata['artifacts'] = [{'path': p.relative_to(archive).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in archive_files(archive) if p.name != 'manifest.json']
    write_json(archive / 'manifest.json', metadata)
    write_checksums(archive)
    return metadata


def execute(root: Path, archive: Path, port: int, timeout: int, *, allow_draft: bool = False) -> dict:
    metadata = json.loads((archive / 'manifest.json').read_text(encoding='utf-8'))
    if metadata.get('schemaVersion') != ARCHIVE_SCHEMA or metadata.get('status') != 'prepared':
        raise ValueError('只能执行新版本prepared归档；禁止覆盖旧run或重复执行已开始的run。')
    verify_checksums(archive)
    material = validate_cases(root, require_author=not allow_draft)
    validate_cases(archive / 'project', require_author=not allow_draft)
    request_data = json.loads((archive / 'request.json').read_text(encoding='utf-8'))
    if allow_draft and request_data['testset_path'] != (CASE_DIR / 'pilot.json').as_posix():
        raise ValueError('草稿试跑仅限5题pilot，不允许自动扩展完整题集。')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))  # Never attach to an unrelated API worker.
    from dotenv import load_dotenv
    load_dotenv(root / '.env', override=False)
    sys.path.insert(0, str(archive / 'project'))
    from backend import config
    model = json.loads((archive / 'request.json').read_text(encoding='utf-8'))['model_names'][0]
    selected = next(item for item in config.MODELS_CONFIG if item['name'] == model)
    judge = request_data.get('judge_model_name')
    for chosen in (selected, configured_model(archive / 'project', judge) if judge else selected):
        if not os.environ.get(chosen['api_key_env']):
            raise ValueError('所选模型凭据未配置。')
        endpoint = urlsplit(chosen['base_url'])
        if endpoint.scheme != 'https' or endpoint.username or endpoint.password or endpoint.query:
            raise ValueError('仅接受不含嵌入凭据的HTTPS模型地址。')
    metadata['purpose'] = 'draft-token-pilot' if allow_draft else 'author-confirmed-evaluation'
    metadata['authorConfirmed'] = material['authorValidation']['status'] == 'confirmed'
    if allow_draft:
        metadata['limitations'].append('用户授权使用5题待确认草稿测量消耗，不代表参考答案已由作者核验。')
    metadata.update(status='running', startedAt=datetime.now(timezone.utc).isoformat(), configuration={'generatorModel': model, 'ragasJudgeModel': judge or model, 'traceJudgeModel': judge or model, 'endpoint': selected['base_url'], 'temperature': 0, 'traceEnabled': config.ENABLE_TRACE, 'chunkSize': config.CHUNK_SIZE, 'chunkOverlap': config.CHUNK_OVERLAP, 'topK': config.TOP_K, 'embedding': config.EMBEDDING_MODEL, 'llmTimeout': config.LLM_TIMEOUT, 'llmMaxRetries': config.LLM_MAX_RETRIES, 'evaluationTimeout': config.EVAL_TIMEOUT, 'promptVersion': 'source hashes plus observed expanded messages in llm-events.jsonl'})
    write_json(archive / 'packages.json', dependency_versions()['packages'])
    write_json(archive / 'manifest.json', metadata)
    environment = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
    environment.pop('PYTHONPATH', None)
    # Remove optional remote tracing; the observer is local and never stores headers.
    environment['LANGCHAIN_TRACING_V2'] = 'false'
    environment['LANGSMITH_TRACING'] = 'false'
    secrets = [value for key, value in environment.items() if ('KEY' in key.upper() or 'TOKEN' in key.upper()) and len(value) >= 8]
    try:
        process = subprocess.Popen([sys.executable, str(archive / metadata['runnerPath']), '--serve', str(archive), '--port', str(port)], cwd=archive / 'project', env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    except OSError as error:
        metadata.update(status='failed', failureType=type(error).__name__, finishedAt=datetime.now(timezone.utc).isoformat())
        finish_archive(archive, metadata)
        raise

    def drain():
        with (archive / 'server.log').open('w', encoding='utf-8') as log:
            for line in process.stdout:
                for secret in secrets:
                    line = line.replace(secret, '[REDACTED]')
                log.write(line)
                log.flush()

    reader = Thread(target=drain, daemon=True)
    reader.start()
    started = time.monotonic()
    try:
        for attempt in range(90):
            if process.poll() is not None:
                raise RuntimeError('隔离API启动失败，见归档server.log。')
            try:
                code, _ = http_json(port, 'GET', '/health')
                if code == 200:
                    break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError('隔离API启动超时。')
        request = json.loads((archive / 'request.json').read_text(encoding='utf-8'))
        code, body = http_json(port, 'POST', '/evaluate', request, timeout)
        (archive / 'api-response.json').write_bytes(body)
        payload = json.loads(body)
        expected = len(json.loads((archive / 'project' / request['testset_path']).read_text(encoding='utf-8')))
        complete = code == 200 and len(payload.get('model_dfs', {}).get(model, [])) == expected
        metadata.update(status='completed' if complete else 'failed', httpStatus=code, answerCount=len(payload.get('model_dfs', {}).get(model, [])), gatePassed=payload.get('gate_passed'), gateMessage=payload.get('gate_message'))
    except BaseException as error:
        metadata.update(status='failed', failureType=type(error).__name__)
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        reader.join(timeout=5)
        metadata.update(finishedAt=datetime.now(timezone.utc).isoformat(), wallSeconds=round(time.monotonic() - started, 2))
        finish_archive(archive, metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description='独立归档当前Web API；默认仅准备，--execute才调用模型。')
    parser.add_argument('--batch', choices=('pilot', 'full'), default='pilot')
    parser.add_argument('--model', choices=MODELS, default='deepseek-chat')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--judge-model', help='单独配置的裁判（内置或本机登记模型名）；不指定时沿用被测模型。')
    parser.add_argument('--allow-draft-pilot', action='store_true', help='仅在用户授权时用5题草稿测量消耗，不标记为人工确认。')
    parser.add_argument('--port', type=int, default=8002)
    parser.add_argument('--timeout', type=int, default=7200)
    parser.add_argument('--serve', type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments.serve.resolve(), arguments.port)
        return
    if arguments.allow_draft_pilot and arguments.batch != 'pilot':
        parser.error('草稿试跑仅限pilot。')
    if arguments.execute and not arguments.allow_draft_pilot:
        validate_cases(ROOT, require_author=True)  # Fail before making an archive or calling a model.
    archive = prepare(ROOT, arguments.batch, arguments.model, arguments.judge_model)
    print(json.dumps({'archive': str(archive), 'status': 'prepared', 'paidModelCalls': 0}, ensure_ascii=False), flush=True)
    if arguments.execute:
        result = execute(ROOT, archive, arguments.port, arguments.timeout, allow_draft=arguments.allow_draft_pilot)
        print(json.dumps({'archive': str(archive), 'status': result['status'], 'answers': result['answerCount'], 'gatePassed': result['gatePassed'], 'tokenObservation': result['tokenObservation']}, ensure_ascii=False))
        if result['status'] != 'completed':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
