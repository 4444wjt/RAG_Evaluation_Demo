"""等信息与门禁 Ragas 同材料对照。prepare/analyze 离线，run 必须独立授权。

不改变陈述、标签、规则或 Ragas 提示。所有 HTTP（含格式修复）经过 Guard。
离线回放用原解析器的同步 parse 重建同一提示树，不调用模型或格式修复服务。
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.exceptions import OutputParserException  # noqa: E402
from langchain_core.outputs import Generation, LLMResult  # noqa: E402
from ragas.llms.base import BaseRagasLLM  # noqa: E402
from ragas.llms.output_parser import FIX_OUTPUT_FORMAT  # noqa: E402
from ragas.metrics._faithfulness import (Faithfulness, _faithfulness_output_parser,  # noqa: E402
                                         _statements_output_parser)
from ragas.run_config import RunConfig  # noqa: E402

from backend.adapters.ragas_compat import ChineseFaithfulness  # noqa: E402
from tools.competition import statute_injection as si  # noqa: E402
from tools.competition import statute_pinpoint as sp  # noqa: E402

VERSION = 'statute-equalinfo-v2'
BASE = 'docs/competition/statute-check/20261021-pinpoint-run/run/package.json'
CONFIRMED_BASE = 'work/statute-injection/pinpoint-20261021/confirmed/package.json'
BASE_SHA256 = 'a70506f4502780194fce6c3f611cc494e1bb371f9c5b2d36749fbc32e9fc958f'
OLD_RECORDS = 'docs/competition/statute-check/20261021-pinpoint-run/run/records.json'
HISTORY = 'docs/competition/evidence/20261005T144504Z-full-39511424/llm-events.jsonl'
TOOL_FILES = (*si.TOOL_FILES, 'tools/competition/statute_pinpoint.py',
              'tools/competition/statute_equalinfo.py', 'backend/adapters/ragas_compat.py')
PREPARATION_TOOLS = ('tools/competition/statute_equalinfo.py', 'tests/test_statute_equalinfo.py')
CONDITIONS = {
    'J1': {'method': 'judge', 'prompt': 'original', 'context': 'original'},
    'J2': {'method': 'judge', 'prompt': 'original', 'context': 'full'},
    'R1': {'method': 'ragas', 'prompt': 'ragas', 'context': 'original'},
    'R2': {'method': 'ragas', 'prompt': 'ragas', 'context': 'full'},
}
PROMPTS = {'original': si.SYSTEM_PROMPT}
JUDGES = deepcopy(si.judge_config())
JUDGES['deepseek']['model'] = 'deepseek-flash'
# 作者 10-22 选 A：deepseek-flash 默认开思考，显式关闭以保持 10-21 实际的非思考模式；随 judges 绑定进 configurationSha256。
JUDGES['deepseek']['thinking'] = {'type': 'disabled'}
MAX_REQUESTS = {'pilot': 160, 'full': 1320}
MAX_REQUESTS_FORMULA = {'pilot': '8*(2*2+2*2*4)=160', 'full': '66*(2*2+2*2*4)=1320'}
PILOT_RULE = '按材料顺序每个statute_pinpoint.TYPES分组取第一条，再加第二条paragraph_existing；按材料顺序调度，共8条。'
HALT_ON = ('actual_model_unknown', 'reservation_exceeded')
COST_POLICY = {
    'reservation': '消息UTF-8字节+256输入、该调用maxTokens输出，按授权单价；累计计费+本次预留>budgetCny则拒发。',
    'usage': '有效usage按授权单价计费；无usage优先此前同裁判同类别有usage调用的费用均值，再同裁判任意类别，再本次预留。',
    'kinds': ['judge', 'ragas_statements', 'ragas_nli', 'ragas_fix'],
    'mean': 'Decimal求和/计数，不作货币位数舍入；无限小数仅受固定28位有效数字、ROUND_HALF_EVEN算术上下文限制；保存分子与计数供精确复算。',
    'decimalPrecision': 28, 'decimalRounding': 'ROUND_HALF_EVEN',
    'history': '仅本运行此前有usage调用；插补调用不参与均值；不重试。',
}
THROTTLE_SECONDS = 1.0
# 未知子陈述/修复文本不能给出有限字节上界。拒绝超长请求（不截断），使费用硬上界可执行。
RAGAS_MESSAGE_BYTES_LIMIT = 65536
NUMERALS = '一二三四五六七八九十百零〇两'
LABEL_PATTERN = rf'第[{NUMERALS}]+条(?:第[{NUMERALS}]+款)?(?:第[{NUMERALS}]+项)?'
CJK_PATTERN = r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]'
SCOPE = '单一法规、共享底稿、单次配对；不参与 Quality Gate；费用是估算不是账单。'
ANALYSIS_PLAN = {
    'J': 'contradicted/insufficient=检出；supported=未检出；调用或解析失败=不可用，单列。',
    'R': 'faithfulness<1=检出；=1=未检出；NaN或任何失败=不可用，单列。',
    'rules': '仅error级检出；从inputs重算，当前规则源码哈希必须等于授权toolFiles。',
    'groups': [*sp.TYPES, 'paragraph_errors', 'item_errors', 'all_errors', 'support_controls'],
    'pairs': [['J1', 'J2'], ['R1', 'R2']],
    'countsOnly': True,
    'independentConditions': '四组各自报告，不合成总分或跨条件总并集；不利结果照实，缺失不计未检出。',
    'historicalComparison': 'J1与10-21原提示归档的同日对照背景；DeepSeek请求名从deepseek-chat变为deepseek-flash（显式关闭思考）；不是稳定性结论。',
    'thinking': 'DeepSeek请求均发送thinking=disabled；逐裁判×调用类别报告返回reasoning_content的响应条数（预期0）、reasoning token与输出token均值/最大值；不为0照实报告，不过滤。',
    'sensitivity': 'withoutS26剔除S26全部变体，复算主表、配对、规则比较与并集。',
    'mechanism': '有效抽取上按逐字标签保留full/truncated/absent分类；无CJK另列；未检出错误中的标签损失仅描述，不推断因果。',
    'limitations': SCOPE,
}
STOP_REASONS = ('completed', 'estimated_budget_guard', 'request_limit',
                'actual_model_unknown', 'reservation_exceeded', 'runner_error', 'interrupted')
PRICE_RATES = {'deepseek': (2, 8), 'qwen72b': (4.13, 4.13)}
COST_NOTE = ('各HTTP发送前按消息UTF-8字节+256输入、该调用maxTokens输出及授权单价预留；'
             '累计计费加本次预留超过budgetCny则拒发并停止。有usage按授权单价计费；'
             '无usage按此前同裁判同类别有usage调用的费用均值插补，再退回同裁判任意类别均值，最后本次预留。'
             '调用或解析失败记缺失后继续、不重试；实际模型缺失或超预留仅在haltOn列出时停止。'
             '消息最多65536字节，超限单元缺失，不发送不计费。费用是按授权单价的估算，不是账单或供应商扣费硬上限。')


def prompt_objects():
    metric = ChineseFaithfulness()
    return {'statement_prompt': metric.statement_prompt,
            'nli_statements_message': metric.nli_statements_message,
            'FIX_OUTPUT_FORMAT': FIX_OUTPUT_FORMAT}


def ragas_metadata():
    metric = ChineseFaithfulness()
    si.require(version('ragas') == '0.1.21', '必须使用 Ragas 0.1.21')
    return {'version': version('ragas'), 'class': 'backend.adapters.ragas_compat.ChineseFaithfulness',
            'sentence_adapter_version': metric.sentence_adapter_version,
            'promptHashes': {k: si.digest(p.to_string()) for k, p in prompt_objects().items()},
            'max_retries': metric.max_retries, '_reproducibility': metric._reproducibility,
            'RunConfig': {'max_retries': 1, 'max_workers': 1, 'timeout': 90},
            'n': 1, 'maxTokens': 2048, 'maxMessageBytes': RAGAS_MESSAGE_BYTES_LIMIT,
            'maxTokensReason': '避免长上下文NLI JSON被截断而触发格式修复。',
            'temperature': 0, 'temperatureOverride': '使用judge_config的0，替换Ragas默认1e-8。',
            'stepIdentification': '按PromptValue.to_string()的instruction前缀识别statements/nli/fix；先检查fix。',
            'messageBytesLimitReason': '未知子陈述与修复文本长度；超限拒发、不截断，使硬上界可执行。'}


def configuration(package):
    return {k: package[k] for k in ('judges', 'ragas', 'conditions', 'throttleSeconds', 'costPolicy', 'pilot')}


def pilot_ids(items):
    selected = {next(i['id'] for i in items if i['type'] == kind) for kind in sp.TYPES}
    selected.add([i['id'] for i in items if i['type'] == 'paragraph_existing'][1])
    return [i['id'] for i in items if i['id'] in selected]


def build_package(root=ROOT):
    si.require(si.sha256(root / BASE) == BASE_SHA256, '基础材料SHA-256不符')
    base = si.load(root / BASE)
    sp.validate_package(base, root)
    # work副本只作可选核对，不能成为公开重放的输入依赖。
    if (root / CONFIRMED_BASE).exists():
        si.require(si.sha256(root / CONFIRMED_BASE) == BASE_SHA256, 'work确认副本与公开基础材料字节不同')
    sources = (*base['sourceFiles'], BASE, OLD_RECORDS, HISTORY)
    return {'schemaVersion': 1, 'experimentVersion': VERSION,
            'basePackage': {'path': BASE, 'confirmedPath': CONFIRMED_BASE, 'sha256': BASE_SHA256,
                            'confirmedSha256': BASE_SHA256, 'byteIdenticalWhenPresent': True,
                            'byteComparison': '公开副本哈希锁定；work副本存在则校验同一SHA-256（字节相同），不存在则仅校验公开副本。',
                            'confirmedReviews': 67}, 'items': deepcopy(base['items']),
            'conditions': deepcopy(CONDITIONS), 'prompts': deepcopy(PROMPTS),
            'promptSha256': si.digest(si.SYSTEM_PROMPT),
            'fullContext': {'path': si.SOURCE, 'sha256': si.sha256(root / si.SOURCE)},
            'judges': deepcopy(JUDGES), 'ragas': ragas_metadata(), 'throttleSeconds': THROTTLE_SECONDS,
            'judgeRequestNote': ('10-21请求deepseek-chat、实际返回deepseek-flash；本次按授权直接请求deepseek-flash。'
                                 'deepseek-flash默认开思考，按作者10-22补充决定（A）请求体加thinking={"type":"disabled"}，'
                                 '与10-21实际的非思考模式一致；J与Ragas的DeepSeek请求都加；其余参数不变。'),
            'costPolicy': deepcopy(COST_POLICY),
            'pilot': {'rule': PILOT_RULE, 'ids': pilot_ids(base['items'])},
            'maxRequestsFormula': MAX_REQUESTS_FORMULA,
            'sourceFiles': {p: si.sha256(root / p) for p in sources},
            'analysisPlan': deepcopy(ANALYSIS_PLAN), 'labelPattern': LABEL_PATTERN,
            'types': deepcopy(sp.TYPES), 'scope': SCOPE}


def validate_package(package, root=ROOT):
    si.require(isinstance(package, dict), '材料包必须为对象')
    expected = build_package(root)
    # 包含逐字段items/review比较、基础材料与所有来源哈希及全部预注册配置。
    si.require(package == expected, '材料、基础材料逐字段、配置、提示或分析口径不一致')


def contexts_for(item, condition, root=ROOT):
    return ([(root / si.SOURCE).read_text(encoding='utf-8')]
            if CONDITIONS[condition]['context'] == 'full' else item['contexts'])


def ragas_row(item, condition, root=ROOT):
    return {'question': item['question'], 'answer': item['statement'],
            'contexts': contexts_for(item, condition, root)}


def prompt_messages(item, condition, root=ROOT):
    ctx = contexts_for(item, condition, root)
    messages = si.prompt_messages(item, {'contexts': ctx})
    messages[0]['content'] = PROMPTS[CONDITIONS[condition]['prompt']]
    return messages


def payload_for(package, job, messages):
    cfg = package['judges'][job['judge']]
    payload = {'model': cfg['model'], 'messages': messages, 'temperature': cfg['temperature'],
               'max_tokens': package['ragas']['maxTokens'] if job['condition'].startswith('R') else cfg['maxTokens'],
               'stream': False}
    if 'thinking' in cfg:
        payload['thinking'] = deepcopy(cfg['thinking'])
    return payload


def reasoning_fields(response):
    """响应侧事实：是否返回 reasoning_content、reasoning token 数（供应商给出才记）；不过滤。"""
    message = reasoning = None
    if (isinstance(response, dict) and isinstance(response.get('choices'), list) and response['choices']
            and isinstance(response['choices'][0], dict) and isinstance(response['choices'][0].get('message'), dict)):
        message = response['choices'][0]['message']
    usage = response.get('usage') if isinstance(response, dict) else None
    details = usage.get('completion_tokens_details') if isinstance(usage, dict) else None
    if isinstance(details, dict) and type(details.get('reasoning_tokens')) is int:
        reasoning = details['reasoning_tokens']
    content = message.get('reasoning_content') if message is not None else None
    return {'reasoningContentReturned': isinstance(content, str) and content != '',
            'reasoningContentChars': len(content) if isinstance(content, str) else None,
            'reasoningTokens': reasoning}


def schedule(package, phase='full'):
    return [{'unitId': f"{item['id']}--{condition}--{judge}", 'itemId': item['id'],
             'judge': judge, 'condition': condition}
            for item in phase_items(package, phase) for condition in CONDITIONS for judge in JUDGES]


def phase_items(package, phase):
    si.require(phase in MAX_REQUESTS, 'phase必须为pilot或full')
    return [i for i in package['items'] if phase == 'full' or i['id'] in package['pilot']['ids']]


def message_bytes(messages):
    return sum(len(m['content'].encode('utf-8')) for m in messages)


def step_for(text):
    objects = prompt_objects()
    for step, name in (('fix', 'FIX_OUTPUT_FORMAT'), ('statements', 'statement_prompt'), ('nli', 'nli_statements_message')):
        if text.startswith(objects[name].instruction):
            return step
    raise ValueError('未知Ragas提示前缀')


def nli_prompt(metric, row, statements):
    # 与Faithfulness._create_nli_prompt相同；后者仅为未设置llm的离线估算加了assert。
    return metric.nli_statements_message.format(context='\n'.join(row['contexts']), statements=json.dumps(statements))


def historical_ragas(root):
    starts, rows = {}, {'statements': [], 'nli': []}
    for line in (root / HISTORY).read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event['event'] == 'start':
            starts[event['runId']] = event
        elif event['event'] == 'end' and event['runId'] in starts:
            start = starts[event['runId']]
            messages = [m for batch in start['messages'] for m in batch]
            text = messages[0]['content']
            try:
                step = step_for(text)
            except ValueError:
                continue
            usage = (event.get('llmOutput') or {}).get('token_usage')
            model = (start.get('requestParameters') or {}).get('model', '')
            if step in rows and model.startswith('deepseek') and usage:
                rows[step].append((message_bytes(messages), usage['prompt_tokens'], usage['completion_tokens']))
    result = {}
    for step, values in rows.items():
        si.require(bool(values), '历史Ragas抽取/NLI用量缺失')
        result[step] = {'requests': len(values), 'messageBytes': sum(v[0] for v in values),
                        'promptTokens': sum(v[1] for v in values), 'completionTokens': sum(v[2] for v in values)}
    return result


def cost_estimate(package, root=ROOT, *, phase='full'):
    items = phase_items(package, phase)
    count = len(items)
    historical = si.load(root / sp.FORMAL / 'package.json')
    records = si.load(root / sp.FORMAL / 'records.json')
    old_items = {i['id']: i for i in historical['items']}
    same_day = si.load(root / OLD_RECORDS)
    history = historical_ragas(root)
    metric = ChineseFaithfulness()
    result = {}
    for judge in JUDGES:
        previous = [r for r in records if r['judge'] == judge]
        si.require(len(previous) == 42 and all(r['status'] == 'ok' and r['usage'] for r in previous), '历史用量不完整')
        old_bytes = sum(message_bytes(si.prompt_messages(old_items[r['itemId']], old_items[r['itemId']])) for r in previous)
        rates = {'inputCnyPerMillion': PRICE_RATES[judge][0], 'outputCnyPerMillion': PRICE_RATES[judge][1]}
        conditions = {}
        hard = Decimal(0)
        for condition in CONDITIONS:
            if condition.startswith('J'):
                byte_count = sum(message_bytes(prompt_messages(i, condition, root)) for i in items)
                tokens = {'prompt_tokens': math.ceil(sum(r['usage']['prompt_tokens'] for r in previous) * byte_count / old_bytes),
                          'completion_tokens': math.ceil(sum(r['usage']['completion_tokens'] for r in previous) * count / 42)}
                row = {'requests': count, 'messageBytes': byte_count, 'inputTokensEstimate': tokens['prompt_tokens'],
                       'outputTokensEstimate': tokens['completion_tokens'], 'costCnyEstimate': float(sp.token_cost(tokens, rates))}
                if condition == 'J1':
                    prompt = CONDITIONS[condition]['prompt']
                    reference = [r for r in same_day if r['judge'] == judge and r['prompt'] == prompt
                                 and r['itemId'] in {i['id'] for i in items}]
                    row['sameDayActualUsageReference'] = {
                        'path': OLD_RECORDS, 'requests': len(reference),
                        'promptTokens': sum(r['usage']['prompt_tokens'] for r in reference if r.get('usage')),
                        'completionTokens': sum(r['usage']['completion_tokens'] for r in reference if r.get('usage'))}
                for item in items:
                    hard += sp.token_cost({'prompt_tokens': message_bytes(prompt_messages(item, condition, root)) + 256,
                                           'completion_tokens': package['judges'][judge]['maxTokens']}, rates)
            else:
                sizes = {'statements': 0, 'nliLower': 0, 'nliPlanning': 0}
                for item in items:
                    data = ragas_row(item, condition, root)
                    sizes['statements'] += len(metric._create_statements_prompt(data).to_string().encode('utf-8'))
                    sizes['nliLower'] += len(nli_prompt(metric, data, [item['statement']]).to_string().encode('utf-8'))
                    sizes['nliPlanning'] += len(nli_prompt(metric, data, [item['statement']] * 3).to_string().encode('utf-8'))
                steps = {}
                for step, key in (('statements', 'statements'), ('nli', 'nliPlanning')):
                    h = history[step]
                    tokens = {'prompt_tokens': math.ceil(h['promptTokens'] * sizes[key] / h['messageBytes']),
                              'completion_tokens': math.ceil(h['completionTokens'] * count / h['requests'])}
                    steps[step] = {'messageBytes': sizes[key], 'inputTokensEstimate': tokens['prompt_tokens'],
                                   'outputTokensEstimate': tokens['completion_tokens'],
                                   'costCnyEstimate': float(sp.token_cost(tokens, rates))}
                cap = sp.token_cost({'prompt_tokens': package['ragas']['maxMessageBytes'] + 256,
                                     'completion_tokens': 2048}, rates)
                row = {'requestsPlanning': count * 2, 'requestsUpper': count * 4, 'messageBytes': sizes,
                       'steps': steps, 'formatRepairsPlanning': 0, 'formatRepairsUpper': count * 2,
                       'costCnyEstimate': sum(v['costCnyEstimate'] for v in steps.values()),
                       'allRowsTwoRepairsCostCnyUpper': float(cap * count * 4),
                       'qwenTokenRatioAssumption': judge == 'qwen72b'}
                hard += cap * count * 4
            conditions[condition] = row
        result[judge] = {'byCondition': conditions,
                         'costCnyEstimate': round(sum(c['costCnyEstimate'] for c in conditions.values()), 6),
                         'reservationHardUpperCny': str(hard)}
    return {'phase': phase, 'items': count, 'maxRequests': MAX_REQUESTS[phase],
            'method': 'J沿用pinpoint：历史实际输入token×新消息字节/历史消息字节，输出用历史每请求均值；向上取整。',
            'deepseekHistoryNote': '历史usage来自deepseek-chat请求名的运行，实际返回deepseek-flash；本次直接请求deepseek-flash。',
            'ragasMethod': '抽取提示精确构造；NLI下界为原陈述一条、规划值为原陈述重复3条；输出沿用历史抽取/NLI均值；规划无格式修复。',
            'hardUpperMethod': 'J逐个固定消息预留；R每个单元最多4请求，每请求按65536字节+256及2048输出预留；超字节上限拒发、不截断。',
            'historicalRagas': {'path': HISTORY, 'steps': history}, 'judges': result,
            'totalCostCnyEstimate': round(sum(r['costCnyEstimate'] for r in result.values()), 6),
            'reservationHardUpperCny': str(sum((Decimal(r['reservationHardUpperCny']) for r in result.values()), Decimal(0))),
            'pricesAsOf': '2026-10-21', 'prices': PRICE_RATES,
            'limitation': '估算不是账单；Qwen无历史Ragas用量，沿用DeepSeek同比例是假设；运行当天须重新核价。'}


def authorization_template(package, path, estimates, root=ROOT):
    auth = si.authorization_template(package, path)
    auth.update(status='draft', configurationSha256=si.digest(configuration(package)),
                promptSetSha256=si.digest(PROMPTS), promptHashes={k: si.digest(v) for k, v in PROMPTS.items()},
                costPolicySha256=si.digest(package['costPolicy']),
                ragasPromptHashes=package['ragas']['promptHashes'],
                toolFiles={p: si.sha256(root / p) for p in TOOL_FILES},
                preparationToolFiles={p: si.sha256(root / p) for p in PREPARATION_TOOLS},
                phase=None, maxRequests=None, maxRetries=0, haltOn=[], supplementaryDecisions=[],
                budgetCny=None, outputDirectory=None, estimates=estimates,
                note='未授权、未调用模型。须填写真实作者授权、当天核价、phase、对应maxRequests、预算、全新输出目录与显式haltOn。promptSetSha256只绑定original。')
    auth['prices'] = {j: {'inputCnyPerMillion': rates[0], 'outputCnyPerMillion': rates[1],
                          'source': '任务指定的2026-10-21单价；运行当天必须重新核价。', 'checkedAt': None}
                      for j, rates in PRICE_RATES.items()}
    return auth


def prepare(output, root=ROOT):
    sp.empty_output(output)
    package = build_package(root)
    estimates = {'phases': {phase: cost_estimate(package, root, phase=phase) for phase in MAX_REQUESTS}}
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / 'package.json', package)
    auth = authorization_template(package, output / 'package.json', estimates, root)
    si.write_json(output / 'cost-estimate.json', estimates)
    si.write_json(output / 'authorization.draft.json', auth)
    write_prompts(output)
    table = ['|条件|方法|提示|上下文|', '|---|---|---|---|']
    table += [f"|{k}|{v['method']}|{v['prompt']}|{v['context']}|" for k, v in CONDITIONS.items()]
    si.write_text(output / 'RUN_CONFIRMATION.md', '\n'.join([
        '# 等信息与Ragas对照（未授权、未调用模型）', '', *table, '',
        'pilot：8×(2×2+2×2×4)=160；full：66×(2×2+2×2×4)=1320。',
        PILOT_RULE,
        '调度：条目→J1/J2/R1/R2→deepseek/qwen72b；请求间隔1.0秒。',
        *[f"{phase}规划费用：¥{e['totalCostCnyEstimate']:.6f}；预留费用硬上界：¥{e['reservationHardUpperCny']}。"
          for phase, e in estimates['phases'].items()],
        'Ragas未知子陈述/修复文本受65536消息字节守卫约束，超长拒发、不截断；每行两次修复的费用上界见cost-estimate.json。',
        '任何调用或解析失败记缺失后继续；R行失败后不再发送该行后续调用；不重试。',
        'haltOn必须显式填写（默认[]），只可选actual_model_unknown、reservation_exceeded；列出才触发停止。',
        '运行器程序错误归runner_error：先归档停止与已有记录，再重新抛出；不能当普通缺失吞掉。',
        '无usage按此前同裁判同类别有usage调用的平均费用插补，再退回同裁判均值、最后本次预留；costPolicy运行前锁定。',
        '作者总预算¥8（pilot+full合计），代码不硬编码；两phase独立授权、独立新空目录，full预算由作者设为8减去pilot估算花费。',
        'pilot只报条数与费用、不给结论；full重新跑全部66条（含pilot的8条），pilot结果不并入full。',
        '待填：phase、对应maxRequests、haltOn、授权人/日期、付费声明、budgetCny、全新输出目录、当日单价/来源/checkedAt。',
        '未授权、未调用模型；已有Ll逐条确认保持原样，不重新代填确认。', SCOPE, '']))
    si.checksums(output)
    return {'package': package, 'authorization': auth, 'estimates': estimates}


def write_prompts(output):
    for name, text in PROMPTS.items():
        si.write_text(output / f'prompt.{name}.txt', text)
    for name, prompt in prompt_objects().items():
        si.write_text(output / f'prompt.ragas.{name}.txt', prompt.to_string())


def validate_authorization(package, path, auth, output, root=ROOT, *, today=None):
    si.require(isinstance(auth, dict), '授权必须为对象')
    si.require(auth.get('status') == 'authorized' and auth.get('allowPaidCalls') is True
               and auth.get('acceptNoHardBillingCap') is True, '缺少付费授权及费用上限声明')
    si.require(isinstance(auth.get('authorizedBy'), str) and auth['authorizedBy'].strip(), '缺少授权人')
    sp.iso_date(auth.get('authorizedAt'), '缺少有效授权日期')
    si.require(auth.get('packageSha256') == si.sha256(path), '授权材料哈希不一致')
    si.require(auth.get('configurationSha256') == si.digest(configuration(package)), '授权配置哈希不一致')
    si.require(auth.get('promptSha256') == si.digest(si.SYSTEM_PROMPT)
               and auth.get('promptSetSha256') == si.digest(PROMPTS)
               and auth.get('promptHashes') == {k: si.digest(v) for k, v in PROMPTS.items()}
               and auth.get('ragasPromptHashes') == package['ragas']['promptHashes'], '授权提示哈希不一致')
    for field, names in (('toolFiles', TOOL_FILES), ('preparationToolFiles', PREPARATION_TOOLS)):
        si.require(auth.get(field) == {p: si.sha256(root / p) for p in names}, f'授权{field}哈希不一致')
    si.require(auth.get('phase') in ('pilot', 'full'), 'phase必须为pilot或full')
    si.require(type(auth.get('maxRequests')) is int and auth['maxRequests'] == MAX_REQUESTS[auth['phase']]
               and type(auth.get('maxRetries')) is int and auth['maxRetries'] == 0, '调用上限须匹配phase（160/1320）且不重试')
    si.require(isinstance(auth.get('haltOn'), list) and all(isinstance(v, str) and v in HALT_ON for v in auth['haltOn'])
               and len(set(auth['haltOn'])) == len(auth['haltOn']), 'haltOn须显式给出且只含允许选项、无重复')
    si.require(auth.get('costPolicySha256') == si.digest(package['costPolicy']), '授权计费口径哈希不一致')
    # 授权原文留在 note 不改；作者后续补充决定逐字另列，注明决定人与日期。
    decisions = auth.get('supplementaryDecisions')
    si.require(isinstance(decisions, list) and all(
        isinstance(d, dict) and set(d) == {'decidedBy', 'decidedAt', 'text'}
        and all(isinstance(d[k], str) and d[k].strip() for k in d) for d in decisions), 'supplementaryDecisions格式无效')
    for decision in decisions:
        sp.iso_date(decision['decidedAt'], '补充决定日期无效')
    si.require(type(auth.get('budgetCny')) in (int, float) and math.isfinite(auth['budgetCny'])
               and auth['budgetCny'] > 0, '预算必须为正数')
    si.require(isinstance(auth.get('outputDirectory'), str) and auth['outputDirectory'].strip(), '缺少输出目录')
    if output is not None:
        si.require((root / auth['outputDirectory']).resolve() == output.resolve(), '授权未绑定本次输出目录')
    si.require(isinstance(auth.get('estimates'), dict)
               and set(auth['estimates'].get('phases', {})) == set(MAX_REQUESTS), '缺少estimates')
    prices = auth.get('prices')
    si.require(isinstance(prices, dict) and set(prices) == set(JUDGES), '缺少单价')
    for price in prices.values():
        si.require(isinstance(price, dict) and all(type(price.get(k)) in (int, float)
                   and math.isfinite(price[k]) and price[k] >= 0 for k in ('inputCnyPerMillion', 'outputCnyPerMillion')), '单价无效')
        si.require(isinstance(price.get('source'), str) and price['source'].strip(), '缺少单价来源')
        sp.iso_date(price.get('checkedAt'), '单价核实日期无效')
        si.require(price['checkedAt'] == auth['authorizedAt'], '单价须在授权当天核实')
        if output is not None:
            si.require(price['checkedAt'] == (today or date.today().isoformat()), '运行当天须重新核价和授权')


class GuardStopped(RuntimeError):
    """不可恢复的运行停止；任何后续发送均拒绝。"""


class UnitUnavailable(RuntimeError):
    """仅终止当前单元；不得执行该行后续Ragas调用。"""


def reservation(package, auth, job, payload):
    tokens = {'prompt_tokens': message_bytes(payload['messages']) + 256,
              'completion_tokens': payload['max_tokens']}
    return {'tokens': tokens, 'costCny': str(sp.token_cost(tokens, auth['prices'][job['judge']]))}


def raw_fields(response, *, ragas=False):
    fields = {'status': 'call_error', 'content': None, 'actualModel': None, 'usage': None}
    if not isinstance(response, dict):
        return fields
    # 借用冻结的响应校验；Ragas内容不走J裁判JSON解析。
    common = si.response_fields(response, [])
    fields.update(actualModel=common['actualModel'], usage=common['usage'])
    if '_transport' in response:
        return fields
    choices = response.get('choices')
    if (isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
            and (ragas or choices[0].get('finish_reason') in (None, 'stop'))
            and isinstance(choices[0].get('message'), dict)
            and isinstance(choices[0]['message'].get('content'), str)):
        fields.update(status='ok', content=choices[0]['message']['content'])
    else:
        fields['status'] = 'response_error'
    return fields


def call_kind(record):
    return 'judge' if record['ragasStep'] is None else 'ragas_' + record['ragasStep']


def charge(record, reserved, auth, previous):
    """只从此前有usage调用求和/计数；保存分子，重放使用同一Decimal上下文。"""
    if record['usage'] is not None:
        return {'basis': 'response_usage', 'tokens': record['usage'], 'meanCallCount': 0,
                'meanCostSumCny': None,
                'costCny': str(sp.token_cost(record['usage'], auth['prices'][record['judge']]))}
    candidates = [c for c in previous if c['judge'] == record['judge'] and c['usage'] is not None]
    same_kind = [c for c in candidates if call_kind(c) == call_kind(record)]
    chosen = same_kind or candidates
    if chosen:
        with localcontext() as context:
            context.prec, context.rounding = 28, ROUND_HALF_EVEN
            total = sum((Decimal(c['charge']['costCny']) for c in chosen), Decimal(0))
            mean = total / Decimal(len(chosen))
        return {'basis': 'imputed_mean_same_kind' if same_kind else 'imputed_mean_same_judge',
                'tokens': None, 'meanCallCount': len(chosen), 'meanCostSumCny': str(total), 'costCny': str(mean)}
    return {'basis': 'pre_request_reservation', 'tokens': reserved['tokens'], 'meanCallCount': 0,
            'meanCostSumCny': None, 'costCny': reserved['costCny']}


class Guard:
    """所有HTTP的唯一出口；先fsync start/预留，再发送，费用与停止由此统一维护。"""

    def __init__(self, package, auth, transport, emit, sleep=time.sleep):
        self.package, self.auth, self.transport, self.emit, self.sleep = package, auth, transport, emit, sleep
        self.calls, self.cost = [], Decimal(0)
        self.stop = None
        self.blocked_units = {}
        self.by_condition = Counter()
        self.last_response = None

    def refuse(self, reason):
        self.stop = self.stop or reason
        raise GuardStopped(self.stop)

    def unavailable(self, job, reason):
        self.blocked_units[job['unitId']] = reason
        raise UnitUnavailable(reason)

    def request(self, job, payload, step=None, call_index=1):
        if self.stop:
            raise GuardStopped(self.stop)
        if job['unitId'] in self.blocked_units:
            raise UnitUnavailable(self.blocked_units[job['unitId']])
        if len(self.calls) >= self.auth['maxRequests']:
            self.refuse('request_limit')
        if message_bytes(payload['messages']) > self.package['ragas']['maxMessageBytes']:
            self.unavailable(job, 'request_size_limit')
        reserved = reservation(self.package, self.auth, job, payload)
        if self.cost + Decimal(reserved['costCny']) > Decimal(str(self.auth['budgetCny'])):
            self.refuse('estimated_budget_guard')
        if self.calls:
            self.sleep(self.package['throttleSeconds'])
        identity = {**job, 'requestId': f"{job['unitId']}--call-{call_index}",
                    'ragasStep': step, 'ragasCall': call_index if step else None,
                    'thinkingRequested': deepcopy(payload.get('thinking'))}
        self.emit({'event': 'start', **identity, 'request': payload, 'reservation': reserved})
        record = {**identity, 'status': 'call_error', 'content': None, 'actualModel': None, 'usage': None, 'error': None,
                  'reasoning': reasoning_fields(None)}
        self.calls.append(record)
        self.by_condition[job['condition']] += 1
        response = None
        exception = False
        try:
            response = self.transport(job['judge'], payload)
            # Ragas按原算法解析截断文本，必要时走其格式修复；J保持冻结协议的finish_reason校验。
            record.update(raw_fields(response, ragas=job['condition'].startswith('R')))
            record['reasoning'] = reasoning_fields(response)
            if isinstance(response, dict) and '_transport' in response:
                problem = response['_transport']
                record['error'] = {'type': problem.get('error'), 'httpStatus': problem.get('httpStatus')}
        except BaseException as error:
            exception = True
            record['error'] = {'type': getattr(error, 'archived_type', type(error).__name__),
                               'httpStatus': getattr(getattr(error, 'response', None), 'status_code', None),
                               'interrupted': not isinstance(error, Exception)}
        self.last_response = response
        record['charge'] = charge(record, reserved, self.auth, self.calls[:-1])
        self.cost += Decimal(record['charge']['costCny'])
        self.emit({'event': 'end' if record['status'] == 'ok' else 'failure', **record, 'response': response})
        if exception and record['error']['interrupted']:
            self.refuse('interrupted')
        for reason, triggered in (
                ('actual_model_unknown', record['actualModel'] is None),
                ('reservation_exceeded', Decimal(record['charge']['costCny']) > Decimal(reserved['costCny']))):
            if triggered and reason in self.auth['haltOn']:
                self.refuse(reason)
        if exception:
            self.unavailable(job, 'transport_error')
        if isinstance(response, dict) and '_transport' in response:
            self.unavailable(job, 'http_error' if response['_transport'].get('error') == 'http_error' else 'response_error')
        if record['status'] != 'ok':
            self.unavailable(job, 'response_error')
        return record['content']


class GuardedRagasLLM(BaseRagasLLM):
    def __init__(self, guard, job):
        super().__init__(run_config=RunConfig(**guard.package['ragas']['RunConfig']))
        self.guard, self.job, self.index = guard, job, 0

    def generate_text(self, *args, **kwargs):
        raise NotImplementedError('仅支持异步Ragas调用')

    async def agenerate_text(self, prompt, n=1, temperature=None, stop=None, callbacks=None):
        si.require(type(n) is int and n == 1, 'Ragas只允许n=1')
        self.index += 1
        text = prompt.to_string()
        payload = payload_for(self.guard.package, self.job, [{'role': 'user', 'content': text}])
        content = self.guard.request(self.job, payload, step_for(text), self.index)
        return LLMResult(generations=[[Generation(text=content)]])


def empty_ragas_result():
    return {'statementsRaw': None, 'statementsFixRaw': None, 'statements': None,
            'nliRaw': None, 'nliFixRaw': None, 'verdicts': None, 'formatFixUsed': False,
            'score': None, 'unavailable': True, 'extractionValid': False}


def ragas_protocol(row, result=None):
    """同步重放0.1.21的单代抽取/NLI/各一次格式修复；失败不猜测结果。"""
    metric = ChineseFaithfulness()
    result = empty_ragas_result() if result is None else result
    for step, parser, prompt in (
            ('statements', _statements_output_parser, metric._create_statements_prompt(row)),
            ('nli', _faithfulness_output_parser, None)):
        if step == 'nli':
            prompt = nli_prompt(metric, row, result['statements'])
        text = yield step, prompt
        result[step + 'Raw'] = text
        try:
            parsed = parser.parse(text)
        except OutputParserException:
            result['formatFixUsed'] = True
            fix = FIX_OUTPUT_FORMAT.format(prompt=prompt.to_string(), completion=text)
            text = yield 'fix', fix
            result[step + 'FixRaw'] = text
            try:
                parsed = parser.parse(text)
            except OutputParserException:
                return result
        if step == 'statements':
            result['statements'] = [s for sentence in parsed.dicts() for s in sentence['simpler_statements']]
            result['extractionValid'] = True
        else:
            result['verdicts'] = parsed.dicts()
            score = Faithfulness._compute_score(metric, parsed)
            result['score'] = float(score) if math.isfinite(score) else None
            result['unavailable'] = result['score'] is None
    return result


def replay_ragas_calls(package, job, row, calls):
    """从原始HTTP内容与同步parse重算；中断时保留已解析的抽取产物。"""
    result = empty_ragas_result()
    protocol = ragas_protocol(row, result)
    expected = next(protocol)
    for index, call in enumerate(calls, 1):
        step, _ = expected
        si.require(call['ragasStep'] == step and call['ragasCall'] == index, 'Ragas步骤/内部调用次序不符')
        # 完整payload另由Guard重放校验；这里检验原始文本的解析依赖链。
        if call['status'] != 'ok':
            break
        try:
            expected = protocol.send(call['content'])
        except StopIteration as done:
            result = done.value
            si.require(index == len(calls), 'Ragas结束后出现多余调用')
            break
    protocol.close()
    return result


def run_ragas_offline(guard, job, row):
    """仅analyze使用：生成与真实metric相同的请求树，但transport仅重放本地事件。"""
    protocol = ragas_protocol(row)
    step, prompt = next(protocol)
    index = 0
    while True:
        index += 1
        text = guard.request(job, payload_for(guard.package, job, [{'role': 'user', 'content': prompt.to_string()}]), step, index)
        try:
            step, prompt = protocol.send(text)
        except StopIteration as done:
            return done.value['score']


def execute(package, auth, root, transport, emit, sleep, *, offline=False):
    guard = Guard(package, auth, transport, emit, sleep)
    records = []
    items = {i['id']: i for i in package['items']}
    pending_error = None
    jobs = schedule(package, auth['phase'])
    for job in jobs:
        before = len(guard.calls)
        item = items[job['itemId']]
        record = {**job, 'status': 'unavailable', 'result': None, 'ragas': None, 'calls': [], 'error': None}
        score = None
        try:
            if job['condition'].startswith('J'):
                guard.request(job, payload_for(package, job, prompt_messages(item, job['condition'], root)))
                # frozen response_fields preserves citation validation against this condition's contexts.
                parsed = si.response_fields(guard.last_response, contexts_for(item, job['condition'], root))
                record.update(status='ok' if parsed['status'] == 'ok' else 'unavailable', result=parsed['result'])
                if parsed['status'] != 'ok':
                    record['error'] = {'type': parsed['status']}
            else:
                row = ragas_row(item, job['condition'], root)
                if offline:
                    score = run_ragas_offline(guard, job, row)
                else:
                    metric = ChineseFaithfulness(llm=GuardedRagasLLM(guard, job))
                    metric.init(RunConfig(**package['ragas']['RunConfig']))
                    value = asyncio.run(metric.ascore(row))
                    score = float(value) if math.isfinite(value) else None
        except (GuardStopped, UnitUnavailable) as error:
            record['error'] = {'type': type(error).__name__, 'reason': str(error)}
        except OutputParserException:
            record['error'] = {'type': 'parse_error'}
        except BaseException as error:
            guard.stop = 'runner_error' if isinstance(error, Exception) else 'interrupted'
            record['error'] = {'type': type(error).__name__, 'reason': guard.stop}
            pending_error = error
        calls = guard.calls[before:]
        if not calls and guard.stop:
            break  # 未发送的单元不伪造为已执行。
        record['calls'] = [c['requestId'] for c in calls]
        if job['condition'].startswith('R'):
            try:
                record['ragas'] = replay_ragas_calls(package, job, ragas_row(item, job['condition'], root), calls)
                if record['error'] is not None:
                    record['ragas'].update(score=None, unavailable=True)
                if record['error'] is None:
                    si.require(record['ragas']['score'] == score, '真实Ragas分数与离线原解析器复算不符')
                    if score is None:
                        record['error'] = {'type': 'ragas_unavailable'}
                    else:
                        record['status'] = 'ok'
            except BaseException as error:
                guard.stop = 'runner_error' if isinstance(error, Exception) else 'interrupted'
                pending_error = error
                record.update(status='unavailable', error={'type': type(error).__name__, 'reason': guard.stop})
        records.append(record)
        emit({'event': 'unit', 'record': record})
        if guard.stop:
            break
    reason = guard.stop or 'completed'
    stop = {'event': 'stop', 'reason': reason, 'attempts': len(guard.calls),
            'estimatedCostCny': str(guard.cost), 'phase': auth['phase']}
    emit(stop)
    manifest = {'schemaVersion': 1, 'experimentVersion': VERSION, 'stopReason': reason,
                'attempts': len(guard.calls), 'completedRecords': len(records), 'plannedRequests': MAX_REQUESTS[auth['phase']],
                'plannedUnits': len(jobs), 'estimatedCostCny': str(guard.cost), 'actualBillCny': None,
                'costNote': COST_NOTE, 'phase': auth['phase'], 'haltOn': auth['haltOn'],
                'requestsByCondition': {c: guard.by_condition[c] for c in CONDITIONS}}
    if pending_error is not None:
        pending_error.equalinfo_records = records
        pending_error.equalinfo_manifest = manifest
        raise pending_error
    return records, manifest


def run(package_path, authorization_path, output, *, root=ROOT, transport=None, sleep=time.sleep):
    package, auth = si.load(package_path), si.load(authorization_path)
    validate_package(package, root)
    validate_authorization(package, package_path, auth, output, root)
    sp.empty_output(output)
    si.require(not output.resolve().is_relative_to(package_path.parent.resolve()), '运行目录不能位于准备目录内')
    send = transport if transport is not None else si.make_transport(package)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package_path, output / 'package.json')
    shutil.copyfile(authorization_path, output / 'authorization.json')
    si.write_json(output / 'schedule.json', schedule(package, auth['phase']))
    write_prompts(output)
    for folder, paths in (('sources', dict.fromkeys((*TOOL_FILES, *PREPARATION_TOOLS))), ('inputs', package['sourceFiles'])):
        for name in paths:
            target = output / folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
    validate_package(package, output / 'inputs')
    si.write_json(output / 'runtime.json', {'startedAt': si.utc(), 'python': sys.version,
                                           'dependencies': {**si.dependency_versions(), 'ragas': version('ragas'),
                                                            'langchain': version('langchain'), 'langchain-core': version('langchain-core')},
                                           'git': si.git_state(root), 'parameters': configuration(package), 'costNote': COST_NOTE,
                                           'thinking': {j: c.get('thinking') for j, c in package['judges'].items()},
                                           'thinkingNote': ('DeepSeek每个请求体（J与Ragas）显式发送thinking={"type":"disabled"}；'
                                                            'Qwen不发送该字段。响应侧逐条记录reasoning字段，analyze汇总返回reasoning_content的条数。')})
    def emit(event):
        si.append_event(output / 'events.jsonl', {**event, 'at': si.utc()})
    try:
        records, manifest = execute(package, auth, output / 'inputs', send, emit, sleep)
    except BaseException as error:
        if hasattr(error, 'equalinfo_manifest'):
            si.write_json(output / 'records.json', error.equalinfo_records)
            si.write_json(output / 'manifest.json', {**error.equalinfo_manifest, 'finishedAt': si.utc()})
            si.checksums(output)
        raise
    manifest['finishedAt'] = si.utc()
    si.write_json(output / 'records.json', records)
    si.write_json(output / 'manifest.json', manifest)
    si.checksums(output)
    return manifest


class ArchivedException(Exception):
    pass


class ArchivedInterrupt(BaseException):
    pass


def read_records(directory, package, auth):
    events = [json.loads(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    cursor = 0

    def emit(actual):
        nonlocal cursor
        si.require(cursor < len(events), '事件缺失')
        saved = {k: v for k, v in events[cursor].items() if k != 'at'}
        si.require(saved == actual, f'逐事件重放不符（{cursor}）：请求、响应解析、预留、计费、顺序或停止原因已改变')
        cursor += 1

    def transport(judge, payload):
        si.require(cursor < len(events) and events[cursor].get('event') in ('end', 'failure'), '缺少HTTP结束事件')
        event = events[cursor]
        response = event.get('response')
        if response is None and event.get('error'):
            saved = event['error']
            error = (ArchivedInterrupt if saved.get('interrupted') else ArchivedException)()
            error.archived_type = saved['type']
            if saved.get('httpStatus') is not None:
                error.response = type('Status', (), {'status_code': saved['httpStatus']})()
            raise error
        return response

    records, expected = execute(package, auth, directory / 'inputs', transport, emit, lambda _: None, offline=True)
    si.require(cursor == len(events), '停止后出现多余事件')
    manifest = si.load(directory / 'manifest.json')
    si.require(manifest.get('stopReason') in STOP_REASONS
               and all(manifest.get(k) == v for k, v in expected.items()), 'manifest与重放不符')
    si.require(si.load(directory / 'records.json') == records, 'records与原始响应离线复算不符')
    return records


def counted(ids):
    return {'count': len(ids), 'ids': ids}


def groups_for(items):
    groups = {kind: [i for i in items if i['type'] == kind] for kind in sp.TYPES}
    groups.update(paragraph_errors=[i for i in items if i['type'] in sp.ERROR_TYPES[:2]],
                  item_errors=[i for i in items if i['type'] in sp.ERROR_TYPES[2:]],
                  all_errors=[i for i in items if i['expected'] == 'not_supported'],
                  support_controls=[i for i in items if i['expected'] == 'supported'])
    return groups


def detection(record):
    if record is None or record['status'] != 'ok':
        return None
    return record['result']['verdict'] in si.NOT_SUPPORTED if record['condition'].startswith('J') else record['ragas']['score'] < 1


def label_retention(statement, statements):
    labels = re.findall(LABEL_PATTERN, statement)
    if not labels:
        return None
    missing = [label for label in labels if not any(label in s for s in statements)]
    if not missing:
        return 'full'
    articles = [re.match(rf'第[{NUMERALS}]+条', label).group() for label in missing]
    return 'truncated' if all(any(article in s for s in statements) for article in articles) else 'absent'


def summarize(package, rules, records):
    lookup = {(r['itemId'], r['judge'], r['condition']): r for r in records}
    def value(item, judge, condition):
        return detection(lookup.get((item['id'], judge, condition)))
    flagged = {i['id'] for i in package['items'] if any(f['severity'] == 'error' for f in rules['items'][i['id']]['findings'])}
    by_type = {}
    def union_row(members, selected):
        errors = [i['id'] for i in members if i['id'] in selected and i['expected'] == 'not_supported']
        false = [i['id'] for i in members if i['id'] in selected and i['expected'] == 'supported']
        return {'detectedErrors': counted(errors), 'falsePositives': counted(false)}
    for group, members in groups_for(package['items']).items():
        row = {'items': len(members), 'rules': union_row(members, flagged), 'conditions': {}}
        for condition in CONDITIONS:
            row['conditions'][condition] = {}
            for judge in JUDGES:
                values = [value(i, judge, condition) for i in members]
                row['conditions'][condition][judge] = {
                    'valid': sum(v is not None for v in values),
                    'unavailable': sum(v is None and (i['id'], judge, condition) in lookup for i, v in zip(members, values)),
                    'notExecuted': sum((i['id'], judge, condition) not in lookup for i in members),
                    **union_row(members, {i['id'] for i, v in zip(members, values) if v is True})}
        by_type[group] = row
    paired, comparison, unions = {}, {}, {}
    for judge in JUDGES:
        paired[judge], comparison[judge] = {}, {}
        for before, after in ANALYSIS_PLAN['pairs']:
            changes = {k: [] for k in ('newDetections', 'lostDetections', 'newFalsePositives', 'reducedFalsePositives', 'unavailable')}
            for item in package['items']:
                first, second = value(item, judge, before), value(item, judge, after)
                if first is None or second is None:
                    changes['unavailable'].append(item['id'])
                elif first != second:
                    key = (('newDetections' if second else 'lostDetections') if item['expected'] == 'not_supported'
                           else ('newFalsePositives' if second else 'reducedFalsePositives'))
                    changes[key].append(item['id'])
            paired[judge][f'{before}->{after}'] = {k: counted(v) for k, v in changes.items()}
        for condition in CONDITIONS:
            judge_only, rules_only, unavailable = [], [], []
            for item in package['items']:
                if item['expected'] != 'not_supported':
                    continue
                detected = value(item, judge, condition)
                if detected is None:
                    unavailable.append(item['id'])
                elif detected and item['id'] not in flagged:
                    judge_only.append(item['id'])
                elif not detected and item['id'] in flagged:
                    rules_only.append(item['id'])
            comparison[judge][condition] = {'judgeOnly': counted(judge_only), 'rulesOnly': counted(rules_only),
                                             'unavailable': counted(unavailable)}
    for condition in CONDITIONS:
        selected = flagged | {i['id'] for i in package['items'] if any(value(i, j, condition) is True for j in JUDGES)}
        unions[condition] = union_row(package['items'], selected)
    retention = {}
    for condition in ('R1', 'R2'):
        retention[condition] = {}
        for judge in JUDGES:
            classifications, non_cjk = {}, set()
            for item in package['items']:
                record = lookup.get((item['id'], judge, condition))
                ragas = record.get('ragas') if record else None
                if not ragas or not ragas['extractionValid']:
                    continue
                label = label_retention(item['statement'], ragas['statements'])
                if label:
                    classifications[item['id']] = label
                    if not any(re.search(CJK_PATTERN, s) for s in ragas['statements']):
                        non_cjk.add(item['id'])
            grouped = {}
            for name, members in groups_for(package['items']).items():
                ids = [i['id'] for i in members]
                grouped[name] = {label: counted([i for i in ids if classifications.get(i) == label]) for label in ('full', 'truncated', 'absent')}
                grouped[name]['noCjk'] = counted([i for i in ids if i in non_cjk])
                grouped[name]['validExtractionsWithLabels'] = sum(i in classifications for i in ids)
            missed = [i['id'] for i in package['items'] if i['expected'] == 'not_supported'
                      and value(i, judge, condition) is False and classifications.get(i['id']) in ('truncated', 'absent')]
            retention[condition][judge] = {'byType': grouped, 'undetectedErrorsWithLabelLoss': counted(missed),
                                           'interpretation': '机制解释候选，仅描述，不推断因果。'}
    present = {r['unitId'] for r in records}
    return {'byType': by_type, 'paired': paired, 'rulesComparison': comparison, 'unions': unions,
            'labelRetention': retention, 'failures': [r for r in records if r['error'] is not None],
            'unavailable': [r['unitId'] for r in records if r['status'] != 'ok'],
            'notExecuted': [j for j in schedule(package) if j['unitId'] not in present],
            'counts': {'executed': len(records), 'valid': sum(r['status'] == 'ok' for r in records),
                       'unavailable': sum(r['status'] != 'ok' for r in records),
                       'notExecuted': len(schedule(package)) - len(records)}}


def same_day_background(package, records, root):
    old = {(r['itemId'], r['judge'], r['prompt']): r for r in si.load(root / OLD_RECORDS)}
    current = {(r['itemId'], r['judge'], r['condition']): r for r in records}
    result = {'interpretation': ANALYSIS_PLAN['historicalComparison'], 'judges': {}}
    for judge in JUDGES:
        result['judges'][judge] = {}
        for condition, prompt in (('J1', 'original'),):
            changed, unavailable = [], []
            for item in package['items']:
                a, b = old.get((item['id'], judge, prompt)), current.get((item['id'], judge, condition))
                if not a or not b or a['status'] != 'ok' or b['status'] != 'ok':
                    unavailable.append(item['id'])
                elif a['result']['verdict'] != b['result']['verdict']:
                    changed.append(item['id'])
            result['judges'][judge][condition] = {'verdictDifferences': counted(changed), 'unavailable': counted(unavailable)}
    return result


def thinking_evidence(calls):
    """按裁判×调用类别报告思考关闭的实测证据；预期返回 reasoning_content 0 条，不为 0 也照实报告。"""
    result = {}
    for judge in JUDGES:
        result[judge] = {}
        for kind in ('all', *COST_POLICY['kinds']):
            selected = [c for c in calls if c['judge'] == judge and (kind == 'all' or call_kind(c) == kind)]
            outputs = [c['usage']['completion_tokens'] for c in selected if c['usage'] is not None]
            reasoning = [c['reasoning']['reasoningTokens'] for c in selected if c['reasoning']['reasoningTokens'] is not None]
            result[judge][kind] = {
                'requests': len(selected),
                'thinkingRequested': sorted({json.dumps(c['thinkingRequested'], sort_keys=True) for c in selected}),
                'reasoningContentReturned': counted([c['requestId'] for c in selected if c['reasoning']['reasoningContentReturned']]),
                'reasoningTokensReported': len(reasoning), 'reasoningTokensSum': sum(reasoning),
                'reasoningTokensMax': max(reasoning) if reasoning else None,
                'outputTokens': {'usageRequests': len(outputs),
                                 'mean': str(Decimal(sum(outputs)) / len(outputs)) if outputs else None,
                                 'max': max(outputs) if outputs else None}}
    return result


def pilot_report(package, auth, records, calls):
    """费用/token均基于本次事件；缺usage不补token，费用使用已锁定的插补口径。"""
    by_kind, extrapolation = {}, {}
    full_count, pilot_count = len(package['items']), len(package['pilot']['ids'])
    for judge in JUDGES:
        by_kind[judge], extrapolation[judge] = {}, {}
        for kind in COST_POLICY['kinds']:
            selected = [c for c in calls if c['judge'] == judge and call_kind(c) == kind]
            usage = [c for c in selected if c['usage'] is not None]
            inputs = sum(c['usage']['prompt_tokens'] for c in usage)
            outputs = sum(c['usage']['completion_tokens'] for c in usage)
            by_kind[judge][kind] = {
                'requests': len(selected), 'usageRequests': len(usage), 'missingUsageRequests': len(selected) - len(usage),
                'inputTokens': inputs, 'outputTokens': outputs,
                'meanInputTokensPerUsageRequest': str(Decimal(inputs) / len(usage)) if usage else None,
                'meanOutputTokensPerUsageRequest': str(Decimal(outputs) / len(usage)) if usage else None,
                'usageCostCny': str(sum((sp.token_cost(c['usage'], auth['prices'][judge]) for c in usage), Decimal(0))),
                'chargedCostCny': str(sum((Decimal(c['charge']['costCny']) for c in selected), Decimal(0)))}
        for condition in CONDITIONS:
            selected = [c for c in calls if c['judge'] == judge and c['condition'] == condition]
            rows = [r for r in records if r['judge'] == judge and r['condition'] == condition]
            usage = [c for c in selected if c['usage'] is not None]
            scale = Decimal(full_count) / pilot_count
            measured_cost = sum((Decimal(c['charge']['costCny']) for c in selected), Decimal(0))
            row = {'fullUnits': full_count, 'pilotUnits': pilot_count, 'executedPilotUnits': len(rows),
                   'pilotRequests': len(selected), 'pilotCostCny': str(measured_cost),
                   'scale': str(scale), 'completePilotUnits': len(rows) == pilot_count,
                   'fullRequestsEstimate': str(Decimal(len(selected)) * scale) if rows else None,
                   'fullInputTokensEstimate': str(Decimal(sum(c['usage']['prompt_tokens'] for c in usage)) * scale) if usage else None,
                   'fullOutputTokensEstimate': str(Decimal(sum(c['usage']['completion_tokens'] for c in usage)) * scale) if usage else None,
                   'fullCostCnyEstimate': str(measured_cost * scale) if rows else None}
            if condition.startswith('R'):
                row['measuredRequestsPerRow'] = str(Decimal(len(selected)) / len(rows)) if rows else None
                row['fullRequestsFromMeasuredCallsPerRow'] = str(Decimal(len(selected)) / len(rows) * full_count) if rows else None
            extrapolation[judge][condition] = row
    projections = [v['fullCostCnyEstimate'] for cs in extrapolation.values() for v in cs.values()]
    observed = [Decimal(v) for v in projections if v is not None]
    return {'schemaVersion': 1, 'phase': 'pilot', 'conclusion': None,
            'method': '按裁判×条件分别以全量66单元/试算8单元×试算实测外推；R另按实测请求数/已执行行×66估请求数。token仅合计有usage的调用，不填零补缺失；费用含插补，不是账单。',
            'limitation': '未完成或存在缺失时外推可能偏低；无数据的条件外推为null，缺usage不补token；试算只报条数与费用，不作效果结论，结果不并入全量。',
            'prices': auth['prices'], 'byJudgeAndKind': by_kind, 'extrapolation': extrapolation,
            'thinkingEvidence': thinking_evidence(calls),
            'fullCostCnyEstimate': str(sum(observed, Decimal(0))) if all(v is not None for v in projections) else None,
            'observedExtrapolatedCostSubtotalCny': str(sum(observed, Decimal(0))) if observed else None,
            'pilotCostCny': str(sum((Decimal(c['charge']['costCny']) for c in calls), Decimal(0))),
            'unavailable': [{'unitId': r['unitId'], 'error': r['error']} for r in records if r['status'] != 'ok'],
            'failedCalls': [{'requestId': c['requestId'], 'error': c['error'], 'status': c['status']} for c in calls if c['status'] != 'ok'],
            'notExecuted': [j for j in schedule(package, 'pilot') if j['unitId'] not in {r['unitId'] for r in records}]}


def analyze(directory, output):
    sp.empty_output(output)
    si.require(not output.resolve().is_relative_to(directory.resolve()), '分析输出必须在原归档之外')
    si.verify_checksums(directory)
    package, auth = si.load(directory / 'package.json'), si.load(directory / 'authorization.json')
    validate_package(package, directory / 'inputs')
    validate_authorization(package, directory / 'package.json', auth, None, directory / 'sources')
    si.require(si.load(directory / 'schedule.json') == schedule(package, auth['phase']), '归档调度不符')
    for name, text in {**{f'prompt.{k}.txt': v for k, v in PROMPTS.items()},
                       **{f'prompt.ragas.{k}.txt': p.to_string() for k, p in prompt_objects().items()}}.items():
        si.require((directory / name).read_bytes() == text.encode('utf-8'), '归档提示不符')
    si.require(si.sha256(ROOT / 'backend/core/statute_check.py') == auth['toolFiles']['backend/core/statute_check.py'], '当前规则源码与授权不符')
    records = read_records(directory, package, auth)
    rules = si.rule_results(package, directory / 'inputs')
    rules['experimentVersion'] = VERSION
    events = [json.loads(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    calls = [e for e in events if e['event'] in ('end', 'failure')]
    if auth['phase'] == 'pilot':
        report = pilot_report(package, auth, records, calls)
        summary = {'schemaVersion': 1, 'experimentVersion': VERSION, 'phase': 'pilot', 'conclusion': None,
                   'counts': {'executed': len(records), 'valid': sum(r['status'] == 'ok' for r in records),
                              'unavailable': sum(r['status'] != 'ok' for r in records),
                              'notExecuted': len(schedule(package, 'pilot')) - len(records)},
                   'estimatedCostCny': report['pilotCostCny'], 'report': 'pilot-report.json'}
    else:
        summary = {'schemaVersion': 1, 'experimentVersion': VERSION, 'phase': 'full', 'analysisPlan': ANALYSIS_PLAN,
                   **summarize(package, rules, records),
                   'sameDayComparisonBackground': same_day_background(package, records, directory / 'inputs'),
                   'thinkingEvidence': thinking_evidence(calls)}
        reduced = {**package, 'items': [i for i in package['items'] if i['base'] != 'S26']}
        kept = {i['id'] for i in reduced['items']}
        summary['withoutS26'] = summarize(reduced, rules, [r for r in records if r['itemId'] in kept])
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / 'analysis.json', summary)
    si.write_json(output / 'rules.json', rules)
    if auth['phase'] == 'pilot':
        si.write_json(output / 'pilot-report.json', report)
    si.checksums(output)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', help='离线生成未授权草稿；不调用模型')
    prep.add_argument('--output', type=Path, required=True)
    execute_parser = commands.add_parser('run', help='需独立绑定授权；不重试、不续跑')
    for name in ('package', 'authorization', 'output'):
        execute_parser.add_argument(f'--{name}', type=Path, required=True)
    analysis = commands.add_parser('analyze', help='逐事件离线复算')
    analysis.add_argument('--run', type=Path, required=True)
    analysis.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output)
        print(json.dumps(result['estimates'], ensure_ascii=False))
    elif args.command == 'run':
        print(json.dumps(run(args.package, args.authorization, args.output), ensure_ascii=False))
    else:
        analyze(args.run, args.output)
        print('离线分析完成。')


if __name__ == '__main__':
    main()
