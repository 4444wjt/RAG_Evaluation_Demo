"""外部数据验证：用公开、带人工标注的数据检验逐句核对，并在第二个领域做鲁棒性体检。

两部分都直接调用生产代码，不另写提示词：
  ① ANAH（上海人工智能实验室，ACL 2024，Apache-2.0）中文部分：每句回答都有人工标注的幻觉类型
     （无 / 矛盾 / 无法验证）。把单句作为回答、该主题的参考文档作为上下文，交给 trace_hallucinations，
     比较机器判定与人工标注，得到逐句核对的准确率与召回率。
  ② RGB（AAAI 2024，CC BY-NC-SA 4.0，仅非商业使用）中文部分：新闻领域。用 rag_answer 在三种证据条件下作答
     （含噪声、全是无关文档、文档含反事实错误），再用 trace_hallucinations 核对，检验拒答与逐句核对的表现。

数据不入库：源文件放在 work/external/（已忽略），只记录固定版本与 SHA-256，样本由固定种子从源文件重建。
付费调用必须先有绑定样本包哈希的授权文件，受预算与请求数上限约束；每个请求只尝试一次，不重试。

  python tools/competition/external_validation.py prepare   --dir <工作目录>
  python tools/competition/external_validation.py authorize --dir <工作目录> --by <授权人> --budget <元>
  python tools/competition/external_validation.py run       --dir <工作目录>
  python tools/competition/external_validation.py analyze   --dir <工作目录> --output <归档目录>
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.attribution import is_refusal  # noqa: E402
from backend.services.answering import rag_answer  # noqa: E402
from backend.services.trace import TRACE_PROMPT_TEMPLATE, trace_hallucinations  # noqa: E402

SOURCE_ROOT = ROOT / 'work' / 'external'
SOURCES = {
    'anah': {
        'name': 'ANAH', 'repository': 'https://huggingface.co/datasets/opencompass/anah',
        'revision': '12238ed44444a5ab4df100a1ef2b395cf49b624e', 'license': 'Apache-2.0',
        'citation': 'Ji et al., ANAH: Analytical Annotation of Hallucinations in Large Language Models, ACL 2024',
        'files': {
            'anah/event.jsonl': 'b5dd7f55a3d79b299bb1c34ccd277c49407eeb0534ac03f8fa1a9d8c760d3bc0',
            'anah/location.jsonl': 'd0d63bf9e18b27fb02e1916e68079de29c7dece61cccf8811b501ca6cee7b913',
            'anah/person.jsonl': '3a414d011b5021eaf65b3b13dd190893ede3b1de00c819fca046078551cb0009',
            'anah/thing.jsonl': '1cd4c1446f7e0fbbb3b03043c258bbcd08a5055b1cc3f42c609f821217e471fe',
        },
    },
    'rgb': {
        'name': 'RGB', 'repository': 'https://github.com/chen700564/RGB',
        'revision': '65ec39e40e7dc9abb50e9bf1b4f32be3f6f16615', 'license': 'CC BY-NC-SA 4.0（仅非商业使用）',
        'citation': 'Chen et al., Benchmarking Large Language Models in Retrieval-Augmented Generation, AAAI 2024',
        'files': {
            'rgb/zh_refine.json': '7a5c7547dcfa1b83429b53a41644453508f820f502d3817312cbf8051afb4563',
            'rgb/zh_fact.json': '171c4bc97860635092ce099f2da095c6ba8d07f0c6f7a19d0231373192f07da0',
        },
    },
}
SEED = 20261013
ANAH_PER_LABEL = 100
ANAH_MAX_DOC_CHARS = 12000
ANAH_MAX_PER_TOPIC = 2
ANAH_LABELS = {'无': 'none', '矛盾': 'contradictory', '无法验证': 'unverifiable'}
ANAH_ANSWERS = (('GPT3.5_answers_D', 'human_GPT3.5_answers_D_ann'), ('InternLM_answers', 'human_InternLM_answers_ann'))
RGB_NOISE_ITEMS = 40
RGB_FACT_ITEMS = 20
CONDITIONS = {'noise': '3 篇相关 + 2 篇无关', 'reject': '5 篇都无关（应拒答）', 'counterfactual': '相关文档含事实错误'}
# 保守估算：中文按每字 1 个 token 计（实际约 0.6–0.7），输出按上限计。
OUTPUT_ALLOWANCE = {'trace': 800, 'answer': 400}
MODEL = {'name': 'deepseek-chat', 'baseUrl': 'https://api.deepseek.com', 'baseUrlEnv': 'DEEPSEEK_BASE_URL',
         'apiKeyEnv': 'OPENAI_API_KEY', 'temperature': 0, 'timeoutSeconds': 180, 'maxRetries': 0}
TOOL_FILES = ('tools/competition/external_validation.py', 'backend/services/trace.py',
              'backend/services/answering.py', 'backend/core/attribution.py')
TRACE_FAILED = ('call_error', 'parse_error', 'invalid_structure')
PRICE = {'inputCnyPerMillion': 2, 'outputCnyPerMillion': 8,
         'source': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing（高峰价，缓存未命中，保守取值）'}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify_sources(source_root: Path = SOURCE_ROOT, sources: dict = SOURCES) -> None:
    for source in sources.values():
        for name, digest in source['files'].items():
            path = source_root / name
            if not path.exists():
                raise FileNotFoundError(f'缺少源文件 {name}：请按 README 从 {source["repository"]} 的 {source["revision"]} 版本下载。')
            if sha256_file(path) != digest:
                raise ValueError(f'源文件 {name} 的 SHA-256 与记录不一致，不能使用。')


# ---------- ① ANAH ----------

def parse_annotation(text: str):
    """解析 ANAH 中文标注 `[<要点>原句\\n]<幻觉>标签\\n…`，返回 (原句或 None, 标签)。

    标签不是单一的 无 / 矛盾 / 无法验证（如 `<无事实>`、标签后夹带解释）时返回 None，不猜。
    """
    label = re.match(r'\s*(?:<要点>(.*?)\n)?<幻觉>([^\n<]*)(?:\n|$)', text, re.S)
    if not label:
        return None
    gold = ANAH_LABELS.get(label.group(2).strip().rstrip('。'))
    if not gold:
        return None
    return ((label.group(1) or '').strip() or None, gold)


def split_sentences(answer: str):
    """按中文句末标点切句并去掉列表序号；ANAH 标注与回答按此顺序逐句对应（要点原句与之仅差序号和空白）。"""
    parts = [p.strip() for p in re.split(r'(?<=[。！？!?])', answer) if p.strip()]
    return [re.sub(r'^\d+[.、．]\s*', '', p) for p in parts]


def anah_candidates(source_root: Path):
    for name in sorted(SOURCES['anah']['files']):
        with (source_root / name).open(encoding='utf-8') as handle:
            for line_no, line in enumerate(handle):
                row = json.loads(line)
                if row.get('language') != 'zh':
                    continue
                documents = [str(d) for d in row.get('documents') or []]
                if not documents or sum(len(d) for d in documents) > ANAH_MAX_DOC_CHARS:
                    continue
                questions = row.get('selected_questions') or []
                for answer_key, ann_key in ANAH_ANSWERS:
                    for q_index, annotations in enumerate(row.get(ann_key) or []):
                        answers = row.get(answer_key) or []
                        if not isinstance(annotations, list) or q_index >= min(len(questions), len(answers)):
                            continue
                        aligned = split_sentences(str(answers[q_index]))
                        for s_index, text in enumerate(annotations):
                            parsed = parse_annotation(str(text))
                            if not parsed:
                                continue
                            sentence, source = parsed[0], 'annotation'
                            if sentence is None:
                                # 标注未写要点时按句序取原句；句数对不上就跳过，不猜。
                                if len(aligned) != len(annotations):
                                    continue
                                sentence, source = aligned[s_index], 'aligned'
                            yield {'id': f'{Path(name).stem}:{line_no}:{answer_key}:{q_index}:{s_index}',
                                   'topic': row.get('name'), 'question': questions[q_index],
                                   'sentence': sentence, 'sentenceSource': source, 'gold': parsed[1],
                                   'documents': documents}


def sample_anah(candidates, per_label=ANAH_PER_LABEL, seed=SEED, max_per_topic=ANAH_MAX_PER_TOPIC):
    """每类各抽 per_label 句；同一主题最多 max_per_topic 句，减少同一文档带来的相关性。"""
    by_label = {label: [] for label in ANAH_LABELS.values()}
    for item in candidates:
        by_label[item['gold']].append(item)
    rng, per_topic, chosen = random.Random(seed), Counter(), []
    for label in ANAH_LABELS.values():
        pool = by_label[label]
        rng.shuffle(pool)
        taken = 0
        for item in pool:
            if taken == per_label:
                break
            if per_topic[item['topic']] >= max_per_topic:
                continue
            per_topic[item['topic']] += 1
            chosen.append(item)
            taken += 1
        if taken < per_label:
            raise ValueError(f'ANAH 标签 {label} 只有 {taken} 句可用，少于 {per_label}。')
    return chosen


# ---------- ② RGB ----------

def _load_rgb(path: Path):
    text = path.read_text(encoding='utf-8').strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def sample_rgb(source_root: Path, seed=SEED, noise_items=RGB_NOISE_ITEMS, fact_items=RGB_FACT_ITEMS):
    rng = random.Random(seed)
    refine = [r for r in _load_rgb(source_root / 'rgb/zh_refine.json') if len(r['positive']) >= 3 and len(r['negative']) >= 7]
    fact = [r for r in _load_rgb(source_root / 'rgb/zh_fact.json') if len(r['positive_wrong']) >= 2 and len(r['negative']) >= 2]
    jobs = []
    for row in rng.sample(refine, noise_items):
        noise = rng.sample(row['positive'], 3) + rng.sample(row['negative'], 2)
        rng.shuffle(noise)
        base = {'rgbId': str(row['id']), 'question': row['query'], 'answer': row['answer']}
        jobs.append({**base, 'id': f'rgb-noise:{row["id"]}', 'condition': 'noise', 'contexts': noise})
        jobs.append({**base, 'id': f'rgb-reject:{row["id"]}', 'condition': 'reject', 'contexts': rng.sample(row['negative'], 5)})
    for row in rng.sample(fact, fact_items):
        contexts = row['positive_wrong'][:3] + rng.sample(row['negative'], 2)
        rng.shuffle(contexts)
        jobs.append({'id': f'rgb-fact:{row["id"]}', 'condition': 'counterfactual', 'rgbId': str(row['id']),
                     'question': row['query'], 'answer': row['answer'], 'fakeAnswer': row['fakeanswer'], 'contexts': contexts})
    return jobs


def rgb_correct(prediction: str, answer) -> bool:
    """RGB 官方 checkanswer 的判定：每个要点都要出现在回答中，列表内的要点任一同义写法出现即可。"""
    prediction = prediction.lower()
    answers = answer if isinstance(answer, list) else [answer]
    for item in answers:
        options = [str(i).lower() for i in item] if isinstance(item, list) else [str(item).lower()]
        if not any(option in prediction for option in options):
            return False
    return True


# ---------- 样本包与授权 ----------

def _trace_prompt_chars(question, answer, contexts):
    return len(TRACE_PROMPT_TEMPLATE) + len(question) + len(answer) + len(json.dumps(contexts, ensure_ascii=False))


def estimate(jobs) -> dict:
    calls = input_tokens = output_tokens = 0
    for job in jobs:
        if job['kind'] == 'anah':
            calls += 1
            input_tokens += _trace_prompt_chars(job['question'], job['sentence'], job['documents'])
            output_tokens += OUTPUT_ALLOWANCE['trace']
        else:
            calls += 2
            context_chars = sum(len(c) for c in job['contexts'])
            input_tokens += 200 + len(job['question']) + context_chars
            input_tokens += _trace_prompt_chars(job['question'], 'x' * OUTPUT_ALLOWANCE['answer'], job['contexts'])
            output_tokens += OUTPUT_ALLOWANCE['answer'] + OUTPUT_ALLOWANCE['trace']
    cost = (input_tokens * PRICE['inputCnyPerMillion'] + output_tokens * PRICE['outputCnyPerMillion']) / 1_000_000
    return {'calls': calls, 'inputTokensUpperBound': input_tokens, 'outputTokensUpperBound': output_tokens,
            'costCnyUpperBound': round(cost, 2), 'assumption': '中文按每字 1 token、输出按上限计，偏保守'}


def prepare(directory: Path, source_root: Path = SOURCE_ROOT) -> dict:
    verify_sources(source_root)
    anah = [{**item, 'kind': 'anah'} for item in sample_anah(anah_candidates(source_root))]
    rgb = [{**job, 'kind': 'rgb'} for job in sample_rgb(source_root)]
    jobs = anah + rgb
    package = {'schemaVersion': 1, 'createdAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
               'seed': SEED, 'sources': SOURCES, 'model': MODEL, 'price': PRICE,
               'parameters': {'anahPerLabel': ANAH_PER_LABEL, 'anahMaxDocChars': ANAH_MAX_DOC_CHARS,
                              'anahMaxPerTopic': ANAH_MAX_PER_TOPIC, 'rgbNoiseItems': RGB_NOISE_ITEMS,
                              'rgbFactItems': RGB_FACT_ITEMS, 'conditions': CONDITIONS},
               'tracePromptSha256': sha256_bytes(TRACE_PROMPT_TEMPLATE.encode('utf-8')),
               'jobs': jobs, 'estimate': estimate(jobs)}
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
            'maxRequests': package['estimate']['calls'], 'maxRetries': 0, 'price': PRICE,
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
    """rag_answer 需要检索器；这里返回样本中固定的上下文，生成提示与生产代码完全相同。"""

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


def run_job(job: dict, llm) -> dict:
    recorder = Recorder(llm)
    record = {'id': job['id'], 'kind': job['kind']}
    try:
        if job['kind'] == 'anah':
            record['trace'] = trace_hallucinations(job['question'], job['sentence'], job['documents'], '', recorder)
        else:
            answer, _ = rag_answer(job['question'], FixedRetriever(job['contexts']), recorder)
            record['answer'] = answer
            record['trace'] = trace_hallucinations(job['question'], answer, job['contexts'], '', recorder)
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
        raise PermissionError('授权绑定的样本包哈希与当前 package.json 不一致。')
    if auth.get('toolSha256') != tool_hashes():
        raise PermissionError('授权后运行代码（工具、逐句核对、问答提示）有改动，需重新授权。')
    package = read_json(package_path)
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
            needed = 1 if job['kind'] == 'anah' else 2
            if requests + needed > auth['maxRequests']:
                stopped = '达到请求数上限'
                break
            if spent + estimate([job])['costCnyUpperBound'] > auth['budgetCny']:
                stopped = '剩余预算不足以完成下一项'
                break
            record = run_job(job, llm)
            requests += len(record['calls'])
            spent += sum(call_cost(c) for c in record['calls'])
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            handle.flush()
            completed += 1
    return {'requests': requests, 'spentCny': round(spent, 4), 'stopped': stopped,
            'completed': completed, 'total': len(package['jobs'])}


# ---------- 分析 ----------

def wilson(successes: int, total: int):
    if total == 0:
        return None
    z = 1.959963984540054
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(centre - half, 4), round(centre + half, 4)]


def predicted_label(trace: dict) -> str:
    """把逐句核对结果映射到 ANAH 的标签：有矛盾→contradictory，有无依据→unverifiable，全部支持→none。"""
    verdicts = [s.get('verdict') for s in trace.get('statements') or []]
    if not verdicts:
        return 'no_statement'
    if 'fact_error' in verdicts:
        return 'contradictory'
    if 'fabrication' in verdicts:
        return 'unverifiable'
    return 'none'


def _rate(hits: int, total: int) -> dict:
    return {'count': hits, 'total': total, 'rate': round(hits / total, 4) if total else None, 'wilson95': wilson(hits, total)}


def _trace_failed(record: dict) -> bool:
    return record['status'] != 'ok' or record['trace'].get('trace_status') in TRACE_FAILED


def anah_items(jobs: dict, records: list) -> list:
    return [{'id': r['id'], 'gold': jobs[r['id']]['gold'], 'sentenceSource': jobs[r['id']]['sentenceSource'],
             'traceStatus': r.get('trace', {}).get('trace_status', r['status']),
             'predicted': 'failed' if _trace_failed(r) else predicted_label(r['trace'])}
            for r in records if r['kind'] == 'anah']


def analyze_anah(items: list) -> dict:
    rows = [(i['gold'], i['predicted']) for i in items if i['predicted'] != 'failed']
    matrix = {gold: dict(Counter(pred for g, pred in rows if g == gold)) for gold in ANAH_LABELS.values()}
    judged = [(g, p) for g, p in rows if p != 'no_statement']
    hallucinated = lambda label: label in ('contradictory', 'unverifiable')
    tp = sum(hallucinated(g) and hallucinated(p) for g, p in judged)
    fp = sum(not hallucinated(g) and hallucinated(p) for g, p in judged)
    fn = sum(hallucinated(g) and not hallucinated(p) for g, p in judged)
    tn = sum(not hallucinated(g) and not hallucinated(p) for g, p in judged)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    specificity = tn / (tn + fp) if tn + fp else None
    return {
        'sentences': len(items), 'traceFailed': len(items) - len(rows),
        'traceStatus': dict(Counter(i['traceStatus'] for i in items)),
        'coverage': _rate(len(judged), len(rows)),
        'confusion': matrix,
        'binary': {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
                   'precision': None if precision is None else round(precision, 4), 'precisionWilson95': wilson(tp, tp + fp),
                   'recall': None if recall is None else round(recall, 4), 'recallWilson95': wilson(tp, tp + fn),
                   'specificity': None if specificity is None else round(specificity, 4), 'specificityWilson95': wilson(tn, tn + fp),
                   'f1': None if f1 is None else round(f1, 4),
                   'balancedAccuracy': None if recall is None or specificity is None else round((recall + specificity) / 2, 4)},
        'perLabelExact': {gold: _rate(sum(1 for g, p in rows if g == gold and p == gold), sum(1 for g, _ in rows if g == gold))
                          for gold in ANAH_LABELS.values()},
        'note': '样本按标签分层、各类等量抽取，指标反映各类别上的表现，不等于 ANAH 总体分布下的数值；'
                '二分类把 矛盾 与 无法验证 都算作幻觉，未产出陈述的句子不计入准确率与召回率，单列为覆盖率。',
    }


def _flagged(trace: dict) -> bool:
    return any(s.get('verdict') in ('fact_error', 'fabrication') for s in trace.get('statements') or [])


def rgb_items(jobs: dict, records: list) -> list:
    items = []
    for r in records:
        if r['kind'] != 'rgb':
            continue
        job = jobs[r['id']]
        item = {'id': r['id'], 'rgbId': job['rgbId'], 'condition': job['condition'],
                'traceStatus': r.get('trace', {}).get('trace_status', r['status'])}
        if r['status'] == 'ok':
            answer = r['answer']
            item.update(refused=is_refusal(answer), flagged=None if _trace_failed(r) else _flagged(r['trace']),
                        correct=rgb_correct(answer, job['answer']))
            if job['condition'] == 'counterfactual':
                item['adoptedFake'] = str(job['fakeAnswer']).lower() in answer.lower()
        items.append(item)
    return items


def analyze_rgb(items: list) -> dict:
    result = {}
    for condition in CONDITIONS:
        group = [i for i in items if i['condition'] == condition]
        rows = [i for i in group if 'refused' in i]
        answered = [i for i in rows if not i['refused']]
        checked = [i for i in answered if i['flagged'] is not None]
        item = {'items': len(group), 'answerFailed': len(group) - len(rows),
                'traceStatus': dict(Counter(i['traceStatus'] for i in group)),
                'refusal': _rate(len(rows) - len(answered), len(rows)),
                'answeredFlaggedByTrace': _rate(sum(i['flagged'] for i in checked), len(checked))}
        if condition == 'noise':
            correct = [i for i in checked if i['correct']]
            wrong = [i for i in checked if not i['correct']]
            item['accuracy'] = _rate(sum(i['correct'] for i in answered), len(rows))
            item['flaggedWhenCorrect'] = _rate(sum(i['flagged'] for i in correct), len(correct))
            item['flaggedWhenWrong'] = _rate(sum(i['flagged'] for i in wrong), len(wrong))
        if condition == 'counterfactual':
            adopted = [i for i in checked if i['adoptedFake']]
            item['adoptedFakeAnswer'] = _rate(sum(i['adoptedFake'] for i in answered), len(rows))
            item['flaggedWhenAdopted'] = _rate(sum(i['flagged'] for i in adopted), len(adopted))
        result[condition] = item
    return result


def analyze(directory: Path) -> dict:
    package = read_json(directory / 'package.json')
    jobs = {job['id']: job for job in package['jobs']}
    records = [json.loads(line) for line in (directory / 'records.jsonl').read_text(encoding='utf-8').splitlines()]
    calls = [c for r in records for c in r['calls']]
    return {
        'schemaVersion': 1, 'packageSha256': sha256_file(directory / 'package.json'),
        'sources': {k: {f: v[f] for f in ('name', 'repository', 'revision', 'license', 'citation')} for k, v in SOURCES.items()},
        'model': MODEL['name'], 'returnedModels': dict(Counter(c.get('model') for c in calls if c.get('model'))),
        'requests': len(calls), 'requestErrors': sum(1 for c in calls if c.get('error')),
        'usage': {'inputTokens': sum(c.get('inputTokens') or 0 for c in calls), 'outputTokens': sum(c.get('outputTokens') or 0 for c in calls),
                  'estimatedCostCny': round(sum(call_cost(c) for c in calls), 4)},
        'anah': analyze_anah(anah_items(jobs, records)), 'rgb': analyze_rgb(rgb_items(jobs, records)),
    }


def archive(directory: Path, output: Path) -> dict:
    """归档可公开的部分：汇总、逐条判定（只含编号与标签，不含 RGB 原文）、授权与样本包哈希。"""
    package = read_json(directory / 'package.json')
    jobs = {job['id']: job for job in package['jobs']}
    records = [json.loads(line) for line in (directory / 'records.jsonl').read_text(encoding='utf-8').splitlines()]
    summary = analyze(directory)
    write_json(output / 'summary.json', summary)
    write_json(output / 'items.json', {'packageSha256': summary['packageSha256'], 'seed': package['seed'],
                                       'anah': anah_items(jobs, records), 'rgb': rgb_items(jobs, records)})
    write_json(output / 'authorization.json', read_json(directory / 'authorization.json'))
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'run', 'analyze', 'authorize'):
        command = sub.add_parser(name)
        command.add_argument('--dir', type=Path, required=True)
        if name == 'authorize':
            command.add_argument('--by', required=True)
            command.add_argument('--budget', type=float, required=True)
            command.add_argument('--note', default='')
        if name == 'analyze':
            command.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if args.command == 'prepare':
        package = prepare(args.dir)
        print(json.dumps(package['estimate'], ensure_ascii=False, indent=2))
    elif args.command == 'authorize':
        print(json.dumps(authorize(args.dir, args.by, args.budget, args.note), ensure_ascii=False, indent=2))
    elif args.command == 'run':
        print(json.dumps(run(args.dir), ensure_ascii=False, indent=2))
    else:
        summary = archive(args.dir, args.output) if args.output else analyze(args.dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
