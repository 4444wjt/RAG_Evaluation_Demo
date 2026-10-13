"""鲁棒性体检：对任意 RAG 应用改动检索资料（打乱、加噪、移除依据、反事实），检验生成与核对两侧是否依据资料。

  python scripts/robustness_check.py prepare   --input <评测结果.json | 题目.jsonl> --dir <工作目录>
  python scripts/robustness_check.py authorize --dir <工作目录> --by <授权人> --budget <元>
  python scripts/robustness_check.py run       --dir <工作目录>
  python scripts/robustness_check.py analyze   --dir <工作目录> [--output <归档目录>]

输入两种格式：
  平台评测结果（api-response.json）：取门禁使用的主模型，只取正向题；可用 --case-manifest 换成题号；
  JSONL：每行 {"id", "question", "contexts": [...], "answer", "ground_truth"}，answer 与 ground_truth 至少有一个。
生成侧两种接法：
  默认用平台的问答提示（rag_answer）和所配模型重新作答；
  --generator-endpoint URL：把 {"question", "contexts"} 以 JSON POST 给被测应用，读取返回的 {"answer"}。
    被测应用须直接用传入的 contexts 作答、不再自行检索，体检才能控制资料。--endpoint-token-env 可指定
    存放访问令牌的环境变量名（以 Bearer 方式发送，不写入任何文件）。
核对侧总用平台的逐句核对（trace_hallucinations），不另写提示词。

prepare 只做离线构造，材料写入 package.json，付费前可逐条检查；构造方法见 backend/core/robustness.py。
付费调用前须授权：授权文件绑定材料包与运行代码的哈希，每个请求只尝试一次、不重试，按预算与请求数上限停止。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402,F401  加载 .env（与平台相同），不读取或保存密钥本身
from backend.core import robustness as rb  # noqa: E402
from backend.core.attribution import is_refusal  # noqa: E402
from backend.services.answering import rag_answer  # noqa: E402
from backend.services.trace import TRACE_PROMPT_TEMPLATE, trace_hallucinations  # noqa: E402

SEED = 20261013
MODEL = {'name': 'deepseek-chat', 'baseUrl': 'https://api.deepseek.com', 'baseUrlEnv': 'DEEPSEEK_BASE_URL',
         'apiKeyEnv': 'OPENAI_API_KEY', 'temperature': 0, 'timeoutSeconds': 180, 'maxRetries': 0}
PRICE = {'inputCnyPerMillion': 2, 'outputCnyPerMillion': 8,
         'source': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing（缓存未命中价，保守取值）'}
# 保守估算：中文按每字 1 个 token 计（实际约 0.6–0.7），输出按上限计。
OUTPUT_ALLOWANCE = {'trace': 800, 'answer': 400}
ANSWER_PROMPT_CHARS = 200
HTTP_TIMEOUT = 60
TOOL_FILES = ('scripts/robustness_check.py', 'backend/core/robustness.py', 'backend/services/trace.py',
              'backend/services/answering.py', 'backend/core/attribution.py')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


# ---------- 输入 ----------

def _check_item(item: dict, where: str) -> dict:
    contexts = item.get('contexts')
    if not isinstance(item.get('question'), str) or not item['question'].strip():
        raise ValueError(f'{where} 缺少 question')
    if not isinstance(contexts, list) or not contexts or not all(isinstance(c, str) and c.strip() for c in contexts):
        raise ValueError(f'{where} 的 contexts 必须是非空字符串列表')
    for key in ('answer', 'groundTruth'):
        if item.get(key) is not None and not isinstance(item[key], str):
            raise ValueError(f'{where} 的 {key} 必须是字符串')
    if not (item.get('answer') or item.get('groundTruth')):
        raise ValueError(f'{where} 至少要有 answer 或 ground_truth，才能定位依据')
    return item


def load_items(path: Path, model: str | None = None, manifest: Path | None = None):
    """读取输入，返回 (来源说明, 题目列表)。题目字段：id、question、contexts、answer、groundTruth。"""
    text = path.read_text(encoding='utf-8')
    if path.suffix.lower() == '.jsonl':
        items = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            items.append(_check_item({'id': str(row.get('id', number)), 'question': row.get('question'),
                                      'contexts': row.get('contexts'), 'answer': row.get('answer'),
                                      'groundTruth': row.get('ground_truth')}, f'第 {number} 行'))
        return {'format': 'jsonl'}, items
    data = json.loads(text)
    data = data.get('result', data) if isinstance(data, dict) else data
    if not isinstance(data, dict) or not isinstance(data.get('model_dfs'), dict):
        raise ValueError('无法识别的输入：需要平台评测结果（含 model_dfs）或 .jsonl 题目文件')
    if model is None:
        # 与门禁相同：取第一个 faithfulness 非空的模型
        model = next(s['model'] for s in data['summary'] if s.get('faithfulness') is not None)
    rows = data['model_dfs'].get(model)
    if not isinstance(rows, list):
        raise ValueError(f'评测结果中没有模型 {model} 的逐题结果')
    ids = {}
    if manifest is not None:
        ids = {case['rowIndex']: case['id'] for case in read_json(manifest)['cases']}
    items = []
    for index, row in enumerate(rows):
        if row.get('case_type') != 'positive':
            continue
        items.append(_check_item({'id': ids.get(index, f'row-{index}'), 'rowIndex': index, 'question': row.get('question'),
                                  'contexts': row.get('contexts'), 'answer': row.get('answer'),
                                  'groundTruth': row.get('ground_truth')}, f'第 {index} 行'))
    return {'format': 'platform', 'model': model}, items


# ---------- 估算、材料包与授权 ----------

def _trace_chars(question, answer, contexts):
    return len(TRACE_PROMPT_TEMPLATE) + len(question) + len(answer) + len(json.dumps(contexts, ensure_ascii=False))


def job_estimate(job: dict, item: dict, generator: dict) -> dict:
    contexts = item['conditions'][job['condition']]['contexts']
    if job['side'] == 'judge':
        return {'paidCalls': 1, 'httpCalls': 0, 'input': _trace_chars(item['question'], item['answer'], contexts),
                'output': OUTPUT_ALLOWANCE['trace']}
    answer = 'x' * OUTPUT_ALLOWANCE['answer']
    estimate = {'paidCalls': 1, 'httpCalls': 0, 'input': _trace_chars(item['question'], answer, contexts),
                'output': OUTPUT_ALLOWANCE['trace']}
    if generator['kind'] == 'http':
        estimate['httpCalls'] = 1
    else:
        estimate['paidCalls'] += 1
        estimate['input'] += ANSWER_PROMPT_CHARS + len(item['question']) + sum(len(c) for c in contexts)
        estimate['output'] += OUTPUT_ALLOWANCE['answer']
    return estimate


def cost(input_tokens: float, output_tokens: float, price: dict) -> float:
    return (input_tokens * price['inputCnyPerMillion'] + output_tokens * price['outputCnyPerMillion']) / 1_000_000


def estimate(package: dict, jobs=None) -> dict:
    items = {item['id']: item for item in package['items']}
    parts = [job_estimate(job, items[job['item']], package['generator']) for job in (package['jobs'] if jobs is None else jobs)]
    input_tokens, output_tokens = sum(p['input'] for p in parts), sum(p['output'] for p in parts)
    return {'jobs': len(parts), 'paidCalls': sum(p['paidCalls'] for p in parts), 'httpCalls': sum(p['httpCalls'] for p in parts),
            'inputTokensUpperBound': input_tokens, 'outputTokensUpperBound': output_tokens,
            'costCnyUpperBound': round(cost(input_tokens, output_tokens, package['price']), 2),
            'assumption': '中文按每字 1 token、输出按上限计，偏保守；被测应用自身的调用不计费'}


def prepare(input_path: Path, directory: Path, *, model: str | None = None, manifest: Path | None = None,
            endpoint: str | None = None, token_env: str | None = None, limit: int | None = None,
            judge: dict | None = None, price: dict | None = None, seed: int = SEED,
            noise_count: int = rb.NOISE_COUNT, min_shared: int = rb.MIN_SHARED, df_ratio: float = rb.DF_RATIO) -> dict:
    source, items = load_items(input_path, model, manifest)
    if limit:
        items = items[:limit]
    if endpoint:
        if urlsplit(endpoint).scheme not in ('http', 'https'):
            raise ValueError('--generator-endpoint 只接受 http 或 https 地址')
        generator = {'kind': 'http', 'endpoint': endpoint, 'tokenEnv': token_env, 'timeoutSeconds': HTTP_TIMEOUT}
    else:
        generator = {'kind': 'builtin', 'prompt': 'backend/services/answering.py:rag_answer', 'model': (judge or MODEL)['name']}
    built = rb.plan(items, seed, noise_count, min_shared, df_ratio, refusal=is_refusal)
    package = {'schemaVersion': 1, 'version': rb.VERSION, 'createdAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
               'seed': seed, 'input': {**source, 'path': _display_path(input_path), 'sha256': sha256_file(input_path),
                                        'items': len(items)},
               'parameters': {'minShared': min_shared, 'dfRatio': df_ratio, 'noiseCount': noise_count,
                              'poolSize': built['poolSize'], 'dfLimit': built['dfLimit']},
               'judge': judge or MODEL, 'generator': generator, 'price': price or PRICE,
               'tracePromptSha256': sha256_bytes(TRACE_PROMPT_TEMPLATE.encode('utf-8')),
               'items': [{**item, 'answerIsRefusal': bool(item.get('answer')) and is_refusal(item['answer'])}
                         for item in built['items']],
               'jobs': built['jobs']}
    package['estimate'] = estimate(package)
    write_json(directory / 'package.json', package)
    return package


def tool_hashes(root: Path = ROOT) -> dict:
    return {name: sha256_bytes((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in TOOL_FILES}


def authorize(directory: Path, by: str, budget: float, note: str = '') -> dict:
    package_path = directory / 'package.json'
    package = read_json(package_path)
    if budget < package['estimate']['costCnyUpperBound']:
        raise ValueError(f'预算 {budget} 元低于保守估算上限 {package["estimate"]["costCnyUpperBound"]} 元。')
    auth = {'packageSha256': sha256_file(package_path), 'authorizedBy': by,
            'authorizedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'budgetCny': budget,
            'maxPaidCalls': package['estimate']['paidCalls'], 'maxHttpCalls': package['estimate']['httpCalls'],
            'maxRetries': 0, 'price': package['price'], 'toolSha256': tool_hashes(), 'note': note}
    write_json(directory / 'authorization.json', auth)
    return auth


# ---------- 运行 ----------

class Recorder:
    """包一层 LLM：记录每次调用的用量与返回模型，不改变提示词与返回内容。"""

    def __init__(self, llm):
        self.llm, self.calls = llm, []

    def invoke(self, prompt):
        started = time.monotonic()
        try:
            response = self.llm.invoke(prompt)
        except Exception as error:  # 只记类型，避免把可能含密钥或请求头的异常文本写入档案
            self.calls.append({'kind': 'model', 'error': type(error).__name__, 'seconds': round(time.monotonic() - started, 2)})
            raise
        usage = getattr(response, 'usage_metadata', None) or {}
        metadata = getattr(response, 'response_metadata', None) or {}
        self.calls.append({'kind': 'model', 'inputTokens': usage.get('input_tokens'), 'outputTokens': usage.get('output_tokens'),
                           'model': metadata.get('model_name'), 'seconds': round(time.monotonic() - started, 2),
                           'promptSha256': sha256_bytes(str(prompt).encode('utf-8'))})
        return response


class FixedRetriever:
    """rag_answer 需要检索器；这里返回构造好的资料，生成提示与生产代码完全相同。"""

    def __init__(self, contexts):
        self.contexts = contexts

    def invoke(self, _question):
        return [type('Doc', (), {'page_content': text})() for text in self.contexts]


def build_llm(judge: dict):
    from langchain_openai import ChatOpenAI
    api_key = os.environ.get(judge['apiKeyEnv'])
    if not api_key:
        raise RuntimeError(f'未设置环境变量 {judge["apiKeyEnv"]}，不能运行。')
    return ChatOpenAI(model=judge['name'], base_url=os.environ.get(judge['baseUrlEnv'], judge['baseUrl']), api_key=api_key,
                      temperature=judge['temperature'], max_retries=judge['maxRetries'], timeout=judge['timeoutSeconds'])


def http_answer(generator: dict, question: str, contexts: list, calls: list) -> str:
    """调用被测应用：POST {"question", "contexts"}，读取 {"answer"}。只尝试一次。"""
    body = json.dumps({'question': question, 'contexts': contexts}, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(generator['endpoint'], data=body, method='POST',
                                     headers={'Content-Type': 'application/json; charset=utf-8'})
    if generator.get('tokenEnv'):
        token = os.environ.get(generator['tokenEnv'])
        if not token:
            raise RuntimeError(f'未设置环境变量 {generator["tokenEnv"]}')
        request.add_header('Authorization', f'Bearer {token}')
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=generator.get('timeoutSeconds', HTTP_TIMEOUT)) as response:
            payload = json.loads(response.read().decode('utf-8'))
        answer = payload.get('answer') if isinstance(payload, dict) else None
        if not isinstance(answer, str):
            raise ValueError('被测应用的返回中缺少 answer 字符串')
    except Exception as error:
        calls.append({'kind': 'http', 'error': type(error).__name__, 'seconds': round(time.monotonic() - started, 2)})
        raise
    calls.append({'kind': 'http', 'seconds': round(time.monotonic() - started, 2)})
    return answer


def run_job(job: dict, item: dict, generator: dict, llm) -> dict:
    recorder = Recorder(llm)
    contexts = item['conditions'][job['condition']]['contexts']
    record = {'id': job['id'], 'item': job['item'], 'side': job['side'], 'condition': job['condition']}
    try:
        if job['side'] == 'judge':
            answer = item['answer']
        elif generator['kind'] == 'http':
            answer = http_answer(generator, item['question'], contexts, recorder.calls)
        else:
            answer, _ = rag_answer(item['question'], FixedRetriever(contexts), recorder)
        if job['side'] == 'generator':
            record['answer'] = answer
        record['trace'] = trace_hallucinations(item['question'], answer, contexts, item.get('groundTruth') or '', recorder)
        record['status'] = 'ok'
    except Exception as error:
        record['status'], record['errorType'] = 'call_error', type(error).__name__
    record['calls'] = recorder.calls
    return record


def call_cost(call: dict, price: dict) -> float:
    if call.get('kind') != 'model' or call.get('inputTokens') is None:
        return 0.0
    return cost(call['inputTokens'], call.get('outputTokens') or 0, price)


def read_records(directory: Path) -> list:
    path = directory / 'records.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def run(directory: Path, llm=None) -> dict:
    package_path, auth_path = directory / 'package.json', directory / 'authorization.json'
    if not auth_path.exists():
        raise PermissionError('没有授权文件，不能发起付费调用。')
    auth = read_json(auth_path)
    if auth['packageSha256'] != sha256_file(package_path):
        raise PermissionError('授权绑定的材料包哈希与当前 package.json 不一致。')
    if auth.get('toolSha256') != tool_hashes():
        raise PermissionError('授权后运行代码（体检工具、逐句核对、问答提示）有改动，需重新授权。')
    package = read_json(package_path)
    items = {item['id']: item for item in package['items']}
    done = read_records(directory)
    finished = {record['id'] for record in done}
    calls = [call for record in done for call in record['calls']]
    paid = sum(1 for c in calls if c.get('kind') == 'model')
    http = sum(1 for c in calls if c.get('kind') == 'http')
    spent = sum(call_cost(c, package['price']) for c in calls)
    llm = llm or build_llm(package['judge'])
    stopped, completed = None, len(finished)
    with (directory / 'records.jsonl').open('a', encoding='utf-8') as handle:
        for job in package['jobs']:
            if job['id'] in finished:
                continue
            need = estimate(package, [job])
            if paid + need['paidCalls'] > auth['maxPaidCalls'] or http + need['httpCalls'] > auth['maxHttpCalls']:
                stopped = '达到请求数上限'
                break
            if spent + need['costCnyUpperBound'] > auth['budgetCny']:
                stopped = '剩余预算不足以完成下一项'
                break
            record = run_job(job, items[job['item']], package['generator'], llm)
            paid += sum(1 for c in record['calls'] if c.get('kind') == 'model')
            http += sum(1 for c in record['calls'] if c.get('kind') == 'http')
            spent += sum(call_cost(c, package['price']) for c in record['calls'])
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            handle.flush()
            completed += 1
    return {'paidCalls': paid, 'httpCalls': http, 'spentCny': round(spent, 4), 'stopped': stopped,
            'completed': completed, 'total': len(package['jobs'])}


# ---------- 分析与归档 ----------

def analyze(directory: Path) -> dict:
    package = read_json(directory / 'package.json')
    records = read_records(directory)
    calls = [c for r in records for c in r['calls']]
    models = {}
    for call in calls:
        if call.get('model'):
            models[call['model']] = models.get(call['model'], 0) + 1
    result = rb.summarize(package['items'], records, is_refusal)
    rows = result.pop('rows')
    summary = {
        'schemaVersion': 1, 'version': package['version'], 'packageSha256': sha256_file(directory / 'package.json'),
        'input': package['input'], 'parameters': package['parameters'], 'generator': package['generator'],
        'judgeModel': package['judge']['name'], 'returnedModels': models,
        'jobs': {'planned': len(package['jobs']), 'completed': len(records)},
        'calls': {'paid': sum(1 for c in calls if c.get('kind') == 'model'), 'http': sum(1 for c in calls if c.get('kind') == 'http'),
                  'errors': sum(1 for c in calls if c.get('error'))},
        'usage': {'inputTokens': sum(c.get('inputTokens') or 0 for c in calls), 'outputTokens': sum(c.get('outputTokens') or 0 for c in calls),
                  'estimatedCostCny': round(sum(call_cost(c, package['price']) for c in calls), 4)},
        **result,
    }
    return {'summary': summary, 'rows': rows}


def archive(directory: Path, output: Path) -> dict:
    """归档：汇总、逐题结果、材料包、调用记录与授权；材料包与记录足以离线重算全部汇总。"""
    result = analyze(directory)
    write_json(output / 'summary.json', result['summary'])
    write_json(output / 'items.json', {'packageSha256': result['summary']['packageSha256'], 'rows': result['rows']})
    output.mkdir(parents=True, exist_ok=True)
    for name in ('package.json', 'authorization.json', 'records.jsonl'):
        (output / name).write_bytes((directory / name).read_bytes())
    return result['summary']


def _pct(entry) -> str:
    if not entry or entry.get('rate') is None:
        return '—'
    low, high = entry['wilson95']
    return f"{entry['count']}/{entry['total']}（{entry['rate']:.0%}，95% 区间 {low:.0%}–{high:.0%}）"


def report(summary: dict) -> str:
    judge, generator = summary['judge'], summary['generator']
    lines = [f"鲁棒性体检：{summary['items']} 题，完成 {summary['jobs']['completed']}/{summary['jobs']['planned']} 项，"
             f"约 ¥{summary['usage']['estimatedCostCny']}",
             '核对侧（原回答不变，只换资料）：',
             f"  打乱后结论不变 {_pct(judge['shuffle']['unchanged'])}；加噪后结论不变 {_pct(judge['noise']['unchanged'])}",
             f"  移除依据后标出 {_pct(judge['remove']['flagged'])}；反事实后标出 {_pct(judge['counterfactual']['flagged'])}",
             '生成侧（用改动后的资料重新作答）：',
             f"  原样拒答 {_pct(generator['original']['refused'])}；加噪拒答 {_pct(generator['noise']['refused'])}",
             f"  移除依据后拒答 {_pct(generator['remove']['refused'])}；作答且被标出 {_pct(generator['remove']['answeredFlagged'])}",
             f"  反事实跟随改动 {_pct(generator['counterfactual']['adoption']['followed'])}；"
             f"沿用原值 {_pct(generator['counterfactual']['adoption']['kept'])}"]
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'authorize', 'run', 'analyze'):
        command = sub.add_parser(name)
        command.add_argument('--dir', type=Path, required=True, help='工作目录（材料包、授权与调用记录）')
        if name == 'prepare':
            command.add_argument('--input', type=Path, required=True, help='平台评测结果 JSON，或 .jsonl 题目文件')
            command.add_argument('--model', help='平台评测结果中取哪个模型的逐题结果；默认与门禁相同')
            command.add_argument('--case-manifest', type=Path, help='题目清单（cases[].rowIndex → id），用于显示题号')
            command.add_argument('--generator-endpoint', help='被测应用的接口地址；不给则用平台问答提示重新作答')
            command.add_argument('--endpoint-token-env', help='存放被测应用访问令牌的环境变量名')
            command.add_argument('--limit', type=int, help='只取前 N 题（试跑用）')
            command.add_argument('--seed', type=int, default=SEED)
            command.add_argument('--noise', type=int, default=rb.NOISE_COUNT, help='加噪条件插入的无关资料条数')
            command.add_argument('--judge-model', default=MODEL['name'])
            command.add_argument('--judge-base-url', default=MODEL['baseUrl'])
            command.add_argument('--judge-key-env', default=MODEL['apiKeyEnv'])
            command.add_argument('--price-input', type=float, default=PRICE['inputCnyPerMillion'], help='每百万输入 token 的价格（元）')
            command.add_argument('--price-output', type=float, default=PRICE['outputCnyPerMillion'], help='每百万输出 token 的价格（元）')
        if name == 'authorize':
            command.add_argument('--by', required=True)
            command.add_argument('--budget', type=float, required=True)
            command.add_argument('--note', default='')
        if name == 'analyze':
            command.add_argument('--output', type=Path, help='归档目录；不给则只打印')
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if args.command == 'prepare':
        judge = {**MODEL, 'name': args.judge_model, 'baseUrl': args.judge_base_url, 'apiKeyEnv': args.judge_key_env}
        price = PRICE if (args.price_input, args.price_output) == (PRICE['inputCnyPerMillion'], PRICE['outputCnyPerMillion']) \
            else {'inputCnyPerMillion': args.price_input, 'outputCnyPerMillion': args.price_output, 'source': '命令行指定'}
        package = prepare(args.input, args.dir, model=args.model, manifest=args.case_manifest,
                          endpoint=args.generator_endpoint, token_env=args.endpoint_token_env, limit=args.limit,
                          judge=judge, price=price, seed=args.seed, noise_count=args.noise)
        built = {c: sum('contexts' in i['conditions'][c] for i in package['items']) for c in rb.CONDITIONS}
        print(json.dumps({'items': len(package['items']), 'built': built, 'estimate': package['estimate']}, ensure_ascii=False, indent=2))
    elif args.command == 'authorize':
        print(json.dumps(authorize(args.dir, args.by, args.budget, args.note), ensure_ascii=False, indent=2))
    elif args.command == 'run':
        print(json.dumps(run(args.dir), ensure_ascii=False, indent=2))
    else:
        summary = archive(args.dir, args.output) if args.output else analyze(args.dir)['summary']
        print(report(summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())
