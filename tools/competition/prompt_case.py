"""外部案例：同一模型、同一批资料，只换问答提示词，比较不同 RAG 项目默认提示词的作答行为。

  python tools/competition/prompt_case.py prepare   --dir <工作目录>
  python tools/competition/prompt_case.py authorize --dir <工作目录> --by <授权人> --budget <元> --max-requests <次>
  python tools/competition/prompt_case.py run       --dir <工作目录>
  python tools/competition/prompt_case.py analyze   --dir <工作目录> [--output <归档目录>]

题目与资料取自 25 题正式运行的冻结证据（检索结果固定，只有提示词不同）。每题两次调用：作答一次、逐句核对一次。
对照组是平台自己的问答提示（rag_answer），与被测提示同一模型、同一温度、同一次运行，避免拿旧结果比较。
被测提示按其项目的原始拼装方式填入：资料用 "\\n\\n" 连接，整段作为一条用户消息。
付费调用前须授权：授权文件绑定材料包与运行代码的哈希，每个请求只尝试一次、不重试，按预算与请求数上限停止。
这里不计算 Ragas 指标，不给门禁结论；只比较作答行为（是否拒答）与逐句核对结果。
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.attribution import is_refusal  # noqa: E402
from backend.services.answering import rag_answer  # noqa: E402
from backend.services.trace import TRACE_PROMPT_TEMPLATE, trace_hallucinations  # noqa: E402

RUN = 'docs/competition/evidence/20261005T144504Z-full-39511424'
SOURCE_FILES = (f'{RUN}/api-response.json', f'{RUN}/inputs/case-manifest.json')
SOURCE_MODEL = 'deepseek-chat'
MODEL = {'name': 'deepseek-chat', 'baseUrl': 'https://api.deepseek.com', 'baseUrlEnv': 'DEEPSEEK_BASE_URL',
         'apiKeyEnv': 'OPENAI_API_KEY', 'temperature': 0, 'timeoutSeconds': 180, 'maxRetries': 0}
TOOL_FILES = ('tools/competition/prompt_case.py', 'backend/services/trace.py',
              'backend/services/answering.py', 'backend/core/attribution.py')
PRICE = {'inputCnyPerMillion': 2, 'outputCnyPerMillion': 8,
         'source': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing（缓存未命中价，保守取值）'}
OUTPUT_ALLOWANCE = {'trace': 800, 'answer': 400}
ANSWER_PROMPT_CHARS = 200
CATEGORY = {'D': '直接问答', 'C': '组合推理', 'F': '错误前提', 'N': '资料无答案'}
# 核对失败只指调用或解析出错；拒答没有可核对的陈述（empty_answer、model_empty），不算失败。
TRACE_FAILED = ('call_error', 'parse_error', 'invalid_structure')

# 被测提示。原文逐字取自对应项目的固定提交，许可证允许在注明出处的前提下引用。
ARMS = {
    'platform': {
        'label': '本项目 rag_answer',
        'source': 'backend/services/answering.py',
    },
    'langchain-chatchat': {
        'label': 'Langchain-Chatchat 默认知识库提示词',
        'repository': 'https://github.com/chatchat-space/Langchain-Chatchat',
        'commit': '49165d6af4438aa7e8a1f71ce276db55f4405151',
        'path': 'libs/chatchat-server/chatchat/settings.py（PromptSettings.rag["default"]）',
        'assembly': 'libs/chatchat-server/chatchat/server/chat/kb_chat.py：资料以 "\\n\\n" 连接，模板整段作为一条用户消息',
        'license': 'Apache-2.0',
        'template': ('【指令】根据已知信息，简洁和专业的来回答问题。'
                     '如果无法从中得到答案，请说 “根据已知信息无法回答该问题”，不允许在答案中添加编造成分，答案请使用中文。\n\n'
                     '【已知信息】{{context}}\n\n'
                     '【问题】{{question}}\n'),
    },
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def tool_hashes(root: Path = ROOT) -> dict:
    return {name: sha256_file(root / name) for name in TOOL_FILES}


# ---------- 构造材料包 ----------

def load_items(root: Path = ROOT) -> list:
    """25 题正式运行的题目、固定资料与参考答案；题号来自题目清单。"""
    data = read_json(root / SOURCE_FILES[0])
    rows = data.get('result', data)['model_dfs'][SOURCE_MODEL]
    cases = {case['rowIndex']: case for case in read_json(root / SOURCE_FILES[1])['cases']}
    items = []
    for index, row in enumerate(rows):
        case = cases[index]
        if case['question'] != row['question']:
            raise ValueError(f'第 {index} 行题目与题目清单不一致')
        contexts = row['contexts']
        if not isinstance(contexts, list) or not all(isinstance(c, str) for c in contexts):
            raise ValueError(f'第 {index} 行资料不是字符串列表')
        items.append({'id': case['id'], 'rowIndex': index, 'category': case['id'][0], 'caseType': row['case_type'],
                      'question': row['question'], 'contexts': contexts, 'groundTruth': row.get('ground_truth') or ''})
    return items


def render(arm: str, question: str, contexts: list) -> str:
    """按被测项目的原始方式拼出提示；平台组不经过这里，直接调用 rag_answer。"""
    template = ARMS[arm]['template']
    return template.replace('{{context}}', '\n\n'.join(contexts)).replace('{{question}}', question)


def job_estimate(job: dict, item: dict) -> dict:
    answer_chars = OUTPUT_ALLOWANCE['answer']
    if job['arm'] == 'platform':
        prompt_chars = ANSWER_PROMPT_CHARS + len(item['question']) + sum(len(c) for c in item['contexts'])
    else:
        prompt_chars = len(render(job['arm'], item['question'], item['contexts']))
    trace_chars = (len(TRACE_PROMPT_TEMPLATE) + len(item['question']) + answer_chars + len(item['groundTruth'])
                   + len(json.dumps(item['contexts'], ensure_ascii=False)))
    return {'calls': 2, 'input': prompt_chars + trace_chars, 'output': OUTPUT_ALLOWANCE['answer'] + OUTPUT_ALLOWANCE['trace']}


def estimate(package: dict, jobs=None) -> dict:
    items = {item['id']: item for item in package['items']}
    parts = [job_estimate(job, items[job['item']]) for job in (package['jobs'] if jobs is None else jobs)]
    input_tokens, output_tokens = sum(p['input'] for p in parts), sum(p['output'] for p in parts)
    cost = (input_tokens * PRICE['inputCnyPerMillion'] + output_tokens * PRICE['outputCnyPerMillion']) / 1_000_000
    return {'jobs': len(parts), 'calls': sum(p['calls'] for p in parts), 'inputTokensUpperBound': input_tokens,
            'outputTokensUpperBound': output_tokens, 'costCnyUpperBound': round(cost, 2),
            'assumption': '中文按每字 1 token、输出按上限计，偏保守'}


def prepare(directory: Path, root: Path = ROOT) -> dict:
    items = load_items(root)
    jobs = [{'id': f'{arm}:{item["id"]}', 'arm': arm, 'item': item['id']} for arm in ARMS for item in items]
    package = {
        'schemaVersion': 1,
        'source': {'run': RUN, 'model': SOURCE_MODEL, 'sha256': {name: sha256_file(root / name) for name in SOURCE_FILES}},
        'model': MODEL, 'price': PRICE, 'arms': ARMS, 'items': items, 'jobs': jobs,
    }
    package['estimate'] = estimate(package)
    write_json(directory / 'package.json', package)
    return package


def authorize(directory: Path, by: str, budget: float, max_requests: int, note: str = '') -> dict:
    auth = {'packageSha256': sha256_file(directory / 'package.json'), 'authorizedBy': by,
            'authorizedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'budgetCny': budget, 'maxRequests': max_requests, 'maxRetries': 0, 'price': PRICE,
            'toolSha256': tool_hashes(), 'note': note}
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
            self.calls.append({'error': type(error).__name__, 'seconds': round(time.monotonic() - started, 2)})
            raise
        usage = getattr(response, 'usage_metadata', None) or {}
        metadata = getattr(response, 'response_metadata', None) or {}
        self.calls.append({'inputTokens': usage.get('input_tokens'), 'outputTokens': usage.get('output_tokens'),
                           'model': metadata.get('model_name'), 'seconds': round(time.monotonic() - started, 2),
                           'promptSha256': sha256_bytes(str(prompt).encode('utf-8'))})
        return response


class FixedRetriever:
    """rag_answer 需要检索器；这里返回正式运行中固定的资料，生成提示与生产代码完全相同。"""

    def __init__(self, contexts):
        self.contexts = contexts

    def invoke(self, _question):
        return [type('Doc', (), {'page_content': text})() for text in self.contexts]


def build_llm():
    from langchain_openai import ChatOpenAI
    api_key = os.environ.get(MODEL['apiKeyEnv'])
    if not api_key:
        raise RuntimeError(f'未设置环境变量 {MODEL["apiKeyEnv"]}，不能运行。')
    return ChatOpenAI(model=MODEL['name'], base_url=os.environ.get(MODEL['baseUrlEnv'], MODEL['baseUrl']), api_key=api_key,
                      temperature=MODEL['temperature'], max_retries=MODEL['maxRetries'], timeout=MODEL['timeoutSeconds'])


def call_cost(call: dict) -> float:
    if call.get('inputTokens') is None:
        return 0.0
    return (call['inputTokens'] * PRICE['inputCnyPerMillion'] + (call.get('outputTokens') or 0) * PRICE['outputCnyPerMillion']) / 1_000_000


def answer_with(arm: str, item: dict, llm) -> str:
    if arm == 'platform':
        answer, _ = rag_answer(item['question'], FixedRetriever(item['contexts']), llm)
        return answer
    response = llm.invoke(render(arm, item['question'], item['contexts']))
    return str(getattr(response, 'content', response)).strip()


def run_job(job: dict, item: dict, llm) -> dict:
    recorder = Recorder(llm)
    record = {'id': job['id'], 'arm': job['arm'], 'item': job['item']}
    try:
        record['answer'] = answer_with(job['arm'], item, recorder)
        record['trace'] = trace_hallucinations(item['question'], record['answer'], item['contexts'], item['groundTruth'], recorder)
        record['status'] = 'ok'
    except Exception as error:
        record['status'], record['errorType'] = 'call_error', type(error).__name__
    record['calls'] = recorder.calls
    return record


def run(directory: Path, llm=None) -> dict:
    package_path, auth_path = directory / 'package.json', directory / 'authorization.json'
    if not auth_path.exists():
        raise PermissionError('没有授权文件，不能发起付费调用。')
    auth = read_json(auth_path)
    if auth['packageSha256'] != sha256_file(package_path):
        raise PermissionError('授权绑定的材料包哈希与当前 package.json 不一致。')
    if auth.get('toolSha256') != tool_hashes():
        raise PermissionError('授权后运行代码（工具、逐句核对、问答提示、拒答判定）有改动，需重新授权。')
    package = read_json(package_path)
    items = {item['id']: item for item in package['items']}
    records_path = directory / 'records.jsonl'
    done, spent, requests = set(), 0.0, 0
    if records_path.exists():
        for line in records_path.read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            done.add(record['id'])
            requests += len(record['calls'])
            spent += sum(call_cost(c) for c in record['calls'])
    llm = llm or build_llm()
    stopped, completed = None, len(done)
    with records_path.open('a', encoding='utf-8') as handle:
        for job in package['jobs']:
            if job['id'] in done:
                continue
            if requests + 2 > auth['maxRequests']:
                stopped = '达到请求数上限'
                break
            if spent + estimate(package, [job])['costCnyUpperBound'] > auth['budgetCny']:
                stopped = '剩余预算不足以完成下一项'
                break
            record = run_job(job, items[job['item']], llm)
            requests += len(record['calls'])
            spent += sum(call_cost(c) for c in record['calls'])
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            handle.flush()
            completed += 1
    return {'requests': requests, 'spentCny': round(spent, 4), 'stopped': stopped,
            'completed': completed, 'total': len(package['jobs'])}


# ---------- 分析 ----------

def _row(record: dict, item: dict) -> dict:
    trace = record.get('trace') or {}
    statements = trace.get('statements') or []
    verdicts = Counter(s.get('verdict') for s in statements)
    return {
        'id': item['id'], 'category': item['category'], 'question': item['question'], 'groundTruth': item['groundTruth'],
        'status': record['status'], 'answer': record.get('answer'),
        'refused': is_refusal(record.get('answer')) if record['status'] == 'ok' else None,
        'answerMode': (trace.get('trace_diagnostics') or {}).get('answer_mode'),
        'traceStatus': trace.get('trace_status'),
        'statements': [{'text': s.get('text'), 'verdict': s.get('verdict'), 'evidence': s.get('evidence')} for s in statements],
        'supported': verdicts.get('supported', 0), 'total': len(statements),
    }


def analyze(directory: Path) -> tuple[dict, dict]:
    package = read_json(directory / 'package.json')
    items = {item['id']: item for item in package['items']}
    records = [json.loads(line) for line in (directory / 'records.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = {arm: [] for arm in package['arms']}
    for record in records:
        rows[record['arm']].append(_row(record, items[record['item']]))
    calls = [c for record in records for c in record['calls']]
    arms = {}
    for arm, arm_rows in rows.items():
        by_category = {}
        for key, label in CATEGORY.items():
            group = [r for r in arm_rows if r['category'] == key and r['status'] == 'ok']
            by_category[key] = {
                'label': label, 'questions': len(group), 'refused': sum(r['refused'] for r in group),
                'statements': sum(r['total'] for r in group), 'supported': sum(r['supported'] for r in group),
                'refusedIds': [r['id'] for r in group if r['refused']],
            }
        unsupported = [{'id': r['id'], 'text': s['text'], 'verdict': s['verdict']}
                       for r in arm_rows for s in r['statements'] if s['verdict'] != 'supported']
        arms[arm] = {'label': package['arms'][arm]['label'], 'completed': sum(r['status'] == 'ok' for r in arm_rows),
                     'errors': sum(r['status'] != 'ok' for r in arm_rows), 'byCategory': by_category,
                     'unsupportedStatements': unsupported,
                     'traceStatus': dict(Counter(r['traceStatus'] for r in arm_rows if r['status'] == 'ok')),
                     'traceFailed': [r['id'] for r in arm_rows if r['status'] == 'ok' and r['traceStatus'] in TRACE_FAILED]}
    summary = {
        'schemaVersion': 1, 'source': package['source'], 'model': {k: package['model'][k] for k in ('name', 'temperature')},
        'returnedModels': dict(Counter(c.get('model') for c in calls if 'error' not in c)),
        'arms': arms,
        'usage': {'requests': len(calls), 'errors': sum('error' in c for c in calls),
                  'inputTokens': sum(c.get('inputTokens') or 0 for c in calls),
                  'outputTokens': sum(c.get('outputTokens') or 0 for c in calls),
                  'costCny': round(sum(call_cost(c) for c in calls), 4)},
        'scope': '一次运行、一个领域、25 题、一个模型；只换问答提示词。不计算 Ragas 指标，不给门禁结论，不代表被测项目实际部署的效果。',
    }
    return summary, {'arms': {arm: package['arms'][arm] for arm in package['arms']}, 'rows': rows}


def archive(directory: Path, output: Path) -> dict:
    summary, items = analyze(directory)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'summary.json', summary)
    write_json(output / 'items.json', items)
    (output / 'authorization.json').write_bytes((directory / 'authorization.json').read_bytes())
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'authorize', 'run', 'analyze'):
        command = sub.add_parser(name)
        command.add_argument('--dir', type=Path, required=True, help='工作目录（材料包、授权与调用记录）')
        if name == 'authorize':
            command.add_argument('--by', required=True)
            command.add_argument('--budget', type=float, required=True)
            command.add_argument('--max-requests', type=int, required=True)
            command.add_argument('--note', default='')
        if name == 'analyze':
            command.add_argument('--output', type=Path, help='归档目录；不给则只打印')
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        print(json.dumps(prepare(args.dir)['estimate'], ensure_ascii=False, indent=2))
    elif args.command == 'authorize':
        print(json.dumps(authorize(args.dir, args.by, args.budget, args.max_requests, args.note), ensure_ascii=False, indent=2))
    elif args.command == 'run':
        print(json.dumps(run(args.dir), ensure_ascii=False, indent=2))
    else:
        summary = archive(args.dir, args.output) if args.output else analyze(args.dir)[0]
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
