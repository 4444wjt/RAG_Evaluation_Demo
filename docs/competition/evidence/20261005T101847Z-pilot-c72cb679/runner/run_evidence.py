"""Archive the unchanged local Web API in an isolated directory. No model call by default."""
from __future__ import annotations

import argparse
from contextvars import ContextVar
from datetime import date, datetime, timezone
import hashlib
import http.client
import importlib.metadata
import json
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


def prepare(root: Path, batch: str, model: str) -> Path:
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
    for relative in [Path(manifest['document']['path']), *[CASE_DIR / name for name in ('testset.json', 'pilot.json', 'case-manifest.json')]]:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
        copied.append({'path': relative.as_posix(), 'sha256': sha256(target)})
    for name in ('requirements.txt', 'requirements_new.txt'):
        shutil.copy2(root / name, archive / name)
    try:
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True))
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    request = {'doc_path': manifest['document']['path'], 'testset_path': (CASE_DIR / ('pilot.json' if batch == 'pilot' else 'testset.json')).as_posix(), 'model_names': [model], 'faith_threshold': 0.8, 'ctx_prec_threshold': 0.8, 'version_label': archive.name}
    write_json(archive / 'request.json', request)
    write_json(archive / 'manifest.json', {
        'schemaVersion': 1, 'kind': 'current-web-api-independent-run', 'status': 'prepared',
        'preparedAt': datetime.now(timezone.utc).isoformat(), 'codeCommit': commit,
        'workingTreeDirty': dirty, 'runtime': {'python': platform.python_version(), 'platform': platform.platform()},
        'runnerSha256': sha256(Path(__file__)), 'sourceFiles': copied,
        'sourceTreeSha256': hashlib.sha256(json.dumps(copied, sort_keys=True).encode()).hexdigest(),
        'caseIds': manifest['pilotIds'] if batch == 'pilot' else [r['id'] for r in manifest['cases']],
        'authorValidation': manifest['authorValidation'], 'request': request,
        'limitations': ['生成、Ragas Judge与Trace使用当前同一模型；不是独立裁判。', '不是旧冻结Demo复跑、阈值校准或总体准确率证明。', '同机隔离源码目录使用既有Python依赖与Embedding缓存，不是全新操作系统验证。', '实际扣费仅能由作者账单确认，token观测不是账单。'],
    })
    return archive


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
            self.record({'event': 'start', 'runId': str(run_id), 'messages': [[{'role': m.type, 'content': m.content} for m in batch] for batch in messages]})

        def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
            self.record({'event': 'start', 'runId': str(run_id), 'prompts': prompts})

        def on_llm_end(self, response, *, run_id, **kwargs):
            self.record({'event': 'end', 'runId': str(run_id), 'llmOutput': response.llm_output, 'generations': [[g.text for g in batch] for batch in response.generations]})

        def on_llm_error(self, error, *, run_id, **kwargs):
            self.record({'event': 'error', 'runId': str(run_id), 'errorType': type(error).__name__})

    observer = Observer()
    register_configure_hook(ContextVar('competition_evidence_observer', default=observer), True)
    import uvicorn
    uvicorn.run('backend.api.app:create_app', factory=True, host='127.0.0.1', port=port, workers=1, log_level='info')


def summarize_events(archive: Path) -> dict:
    events_path = archive / 'llm-events.jsonl'
    events = [json.loads(line) for line in events_path.read_text(encoding='utf-8').splitlines()] if events_path.exists() else []
    ends = [item for item in events if item['event'] == 'end']
    usages = [(item.get('llmOutput') or {}).get('token_usage') for item in ends]
    reported = [value for value in usages if isinstance(value, dict)]
    return {'startedCalls': sum(item['event'] == 'start' for item in events), 'completedCalls': len(ends), 'errorEvents': sum(item['event'] == 'error' for item in events), 'callsWithTokenUsage': len(reported), 'promptTokens': sum(value.get('prompt_tokens', 0) for value in reported), 'completionTokens': sum(value.get('completion_tokens', 0) for value in reported), 'actualCharge': None, 'note': '观察到的LangChain调用及提供方返回token，不保证覆盖HTTP层重试；实际费用未取得账单。'}


def execute(root: Path, archive: Path, port: int, timeout: int, *, allow_draft: bool = False) -> dict:
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
    if not os.environ.get(selected['api_key_env']):
        raise ValueError('所选模型凭据未配置。')
    endpoint = urlsplit(selected['base_url'])
    if endpoint.scheme != 'https' or endpoint.username or endpoint.password or endpoint.query:
        raise ValueError('仅接受不含嵌入凭据的HTTPS模型地址。')
    metadata = json.loads((archive / 'manifest.json').read_text(encoding='utf-8'))
    metadata['purpose'] = 'draft-token-pilot' if allow_draft else 'author-confirmed-evaluation'
    metadata['authorConfirmed'] = material['authorValidation']['status'] == 'confirmed'
    if allow_draft:
        metadata['limitations'].append('用户授权使用5题待确认草稿测量消耗，不代表参考答案已由作者核验。')
    metadata.update(status='running', startedAt=datetime.now(timezone.utc).isoformat(), configuration={'generatorModel': model, 'ragasJudgeModel': model, 'traceJudgeModel': model, 'endpoint': selected['base_url'], 'temperature': 0, 'traceEnabled': config.ENABLE_TRACE, 'chunkSize': config.CHUNK_SIZE, 'chunkOverlap': config.CHUNK_OVERLAP, 'topK': config.TOP_K, 'embedding': config.EMBEDDING_MODEL, 'llmTimeout': config.LLM_TIMEOUT, 'llmMaxRetries': config.LLM_MAX_RETRIES, 'evaluationTimeout': config.EVAL_TIMEOUT, 'promptVersion': 'source hashes plus observed expanded messages in llm-events.jsonl'})
    packages = sorted({d.metadata['Name']: d.version for d in importlib.metadata.distributions() if d.metadata['Name']}.items())
    write_json(archive / 'packages.json', dict(packages))
    write_json(archive / 'manifest.json', metadata)
    environment = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
    environment.pop('PYTHONPATH', None)
    # Remove optional remote tracing; the observer is local and never stores headers.
    environment['LANGCHAIN_TRACING_V2'] = 'false'
    environment['LANGSMITH_TRACING'] = 'false'
    secrets = [value for key, value in environment.items() if ('KEY' in key.upper() or 'TOKEN' in key.upper()) and len(value) >= 8]
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve', str(archive), '--port', str(port)], cwd=archive / 'project', env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')

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
        metadata.update(finishedAt=datetime.now(timezone.utc).isoformat(), wallSeconds=round(time.monotonic() - started, 2), tokenObservation=summarize_events(archive))
        artifacts = [p for p in (archive / 'project').iterdir() if p.is_file() and p.suffix in ('.json', '.csv', '.html', '.png')]
        metadata['artifacts'] = [{'path': p.relative_to(archive).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in sorted(artifacts)]
        write_json(archive / 'manifest.json', metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description='独立归档当前Web API；默认仅准备，--execute才调用模型。')
    parser.add_argument('--batch', choices=('pilot', 'full'), default='pilot')
    parser.add_argument('--model', choices=MODELS, default='deepseek-chat')
    parser.add_argument('--execute', action='store_true')
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
    archive = prepare(ROOT, arguments.batch, arguments.model)
    print(json.dumps({'archive': str(archive), 'status': 'prepared', 'paidModelCalls': 0}, ensure_ascii=False), flush=True)
    if arguments.execute:
        result = execute(ROOT, archive, arguments.port, arguments.timeout, allow_draft=arguments.allow_draft_pilot)
        print(json.dumps({'archive': str(archive), 'status': result['status'], 'answers': result['answerCount'], 'gatePassed': result['gatePassed'], 'tokenObservation': result['tokenObservation']}, ensure_ascii=False))
        if result['status'] != 'completed':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
