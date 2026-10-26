"""法条样式错误注入：同一批陈述，通用裁判与法条结构核对各检出多少。prepare/analyze 离线；run 需独立授权。

底稿是门禁所用 52 条陈述中三位非作者（V、A、B）都判为「支持」、且含条号、数额期限或情态词的陈述。
每条注入只做一处逐字替换（改条号、数额、单位、情态词、极性，或加一个错的款号），对照组为原陈述。
裁判沿用裁判证据敏感性实验（judge-evidence-v1）的固定陈述核查提示词，只看问题、陈述与检索片段。
法条结构核对另外读取法规原文（RAG 应用所索引的文件），这是它与裁判在信息上的差别。
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
import random
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.statute_check import RULES, check_answer  # noqa: E402
from tools.competition import judge_calibration as jc  # noqa: E402
from tools.competition.judge_sensitivity import (SYSTEM_PROMPT, append_event, digest, git_state, judge_config,  # noqa: E402
                                                 load, make_transport, prompt_messages, request_cost, require,
                                                 response_fields, utc, verify_sources)
from tools.competition.run_evidence import dependency_versions, sha256  # noqa: E402

VERSION = 'statute-injection-v1'
SEED = 20261021
SOURCE = 'scenarios/deposit_insurance.txt'
ANNOTATIONS = {
    'V': 'docs/competition/judge-calibration/20261014-annotation-V/annotation-V.json',
    'A': 'docs/competition/judge-calibration/20261021-annotation-AB/annotation-A.json',
    'B': 'docs/competition/judge-calibration/20261021-annotation-AB/annotation-B.json',
}
SOURCE_FILES = (SOURCE, 'frontend/src/fixtures/judge-annotation.json', *ANNOTATIONS.values())
TOOL_FILES = ('tools/competition/statute_injection.py', 'backend/core/statute_check.py',
              'tools/competition/judge_sensitivity.py', 'tools/competition/judge_compare.py',
              'tools/competition/run_evidence.py', 'tools/competition/judge_calibration.py')
NOT_SUPPORTED = ('insufficient', 'contradicted')
TYPES = {
    'control': '原陈述，不改动',
    'control_paragraph': '加一个正确的款号',
    'article_swap': '条号改为节选中的另一条',
    'article_absent': '条号改为节选中没有的条',
    'paragraph_shift': '加一个错的款号',
    'quantity_value': '数额或期限的数值改变',
    'quantity_unit': '期限单位改变（工作日→日、个月→年）',
    'modal': '情态词「应当」改为「可以」',
    'polarity': '「不适用 / 不属于」改为肯定（法条结构核对不覆盖）',
}
# (编号, 底稿陈述, 类型, 替换前, 替换后)。每条只替换一处；替换前须在底稿中恰好出现一次。
RECIPES = (
    ('C-S03', 'S03', 'control', None, None),
    ('C-S07', 'S07', 'control', None, None),
    ('C-S13', 'S13', 'control', None, None),
    ('C-S21', 'S21', 'control', None, None),
    ('C-S27', 'S27', 'control', None, None),
    ('C-S28', 'S28', 'control', None, None),
    ('C-S32', 'S32', 'control', None, None),
    ('C-S36', 'S36', 'control', None, None),
    ('C-S41', 'S41', 'control', None, None),
    ('C-S49', 'S49', 'control', None, None),
    ('C-S50', 'S50', 'control', None, None),
    ('CP-S27', 'S27', 'control_paragraph', '存款保险基金管理机构', '根据第十九条第二款，存款保险基金管理机构'),
    ('CP-S50', 'S50', 'control_paragraph', '本条例施行后', '根据第八条第二款，本条例施行后'),
    ('AS-S03', 'S03', 'article_swap', '第五条', '第四条'),
    ('AS-S21', 'S21', 'article_swap', '第四条', '第三条'),
    ('AS-S28', 'S28', 'article_swap', '第三条', '第一条'),
    ('AS-S32', 'S32', 'article_swap', '第二条', '第四条'),
    ('AS-S36', 'S36', 'article_swap', '第四条', '第五条'),
    ('AS-S41', 'S41', 'article_swap', '第十条', '第九条'),
    ('AS-S49', 'S49', 'article_swap', '第八条', '第二条'),
    ('AA-S03', 'S03', 'article_absent', '第五条', '第六条'),
    ('AA-S21', 'S21', 'article_absent', '第四条', '第七条'),
    ('AA-S28', 'S28', 'article_absent', '第三条', '第十一条'),
    ('AA-S32', 'S32', 'article_absent', '第二条', '第十二条'),
    ('AA-S36', 'S36', 'article_absent', '第四条', '第十三条'),
    ('AA-S41', 'S41', 'article_absent', '第十条', '第十一条'),
    ('AA-S49', 'S49', 'article_absent', '第八条', '第十八条'),
    ('PS-S27', 'S27', 'paragraph_shift', '存款保险基金管理机构', '根据第十九条第一款，存款保险基金管理机构'),
    ('PS-S50', 'S50', 'paragraph_shift', '本条例施行后', '根据第八条第一款，本条例施行后'),
    ('QV-S03', 'S03', 'quantity_value', '50万元', '20万元'),
    ('QV-S07', 'S07', 'quantity_value', '50万元', '100万元'),
    ('QV-S27', 'S27', 'quantity_value', '7个工作日', '10个工作日'),
    ('QV-S50', 'S50', 'quantity_value', '6个月', '3个月'),
    ('QU-S27', 'S27', 'quantity_unit', '7个工作日', '7日'),
    ('QU-S50', 'S50', 'quantity_unit', '6个月', '6年'),
    ('MD-S13', 'S13', 'modal', '应当', '可以'),
    ('MD-S27', 'S27', 'modal', '应当', '可以'),
    ('MD-S41', 'S41', 'modal', '应当', '可以'),
    ('MD-S49', 'S49', 'modal', '应当', '可以'),
    ('MD-S50', 'S50', 'modal', '应当', '可以'),
    ('PO-S21', 'S21', 'polarity', '不属于', '属于'),
    ('PO-S32', 'S32', 'polarity', '不适用', '适用'),
)
# 试跑子集：按配方顺序每类取第一条，再加第二条原陈述作对照。只验证流程、格式与费用，结果不并入正式统计。
PILOT_RULE = '按配方顺序每类取第一条，再加第二条原陈述作对照；试跑结果不并入正式统计，正式运行仍跑全部配方'
PILOT_IDS = tuple([next(r[0] for r in RECIPES if r[2] == kind) for kind in TYPES]
                  + [[r[0] for r in RECIPES if r[2] == 'control'][1]])


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')


def write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding='utf-8', newline='\n')


def checksums(directory: Path):
    lines = [f'{sha256(p)}  {p.relative_to(directory).as_posix()}'
             for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS.txt']
    write_text(directory / 'SHA256SUMS.txt', '\n'.join(lines) + '\n')


def verify_checksums(directory: Path):
    path = directory / 'SHA256SUMS.txt'
    require(path.is_file(), '归档缺少外部校验清单')
    expected = dict(line.split('  ', 1)[::-1] for line in path.read_text(encoding='utf-8').splitlines())
    for name, value in expected.items():
        require(sha256(directory / name) == value, f'归档校验失败：{name}')
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() and p.name != 'SHA256SUMS.txt'}
    require(set(expected) == actual, '归档文件集合与校验清单不符')


def pending() -> dict:
    return {'status': 'pending', 'reviewer': None, 'reviewedAt': None, 'note': None}


def bases(root: Path = ROOT) -> dict:
    """三位标注人都判为「支持」的陈述，连同它所在题的问题与检索片段。"""
    package, key = jc.build_package()
    labels = {name: jc.load_annotation(root / path, package)['labels'] for name, path in ANNOTATIONS.items()}
    questions = {q['id']: q for q in package['questions']}
    found = {}
    for item, k in zip(package['items'], key):
        require(item['id'] == k['id'], '标注材料与答案键顺序不一致')
        if all(labels[name][item['id']] == '支持' for name in ANNOTATIONS):
            q = questions[item['question']]
            found[item['id']] = {'statement': item['statement'], 'question': q['question'], 'contexts': q['contexts'],
                                 'caseId': k['caseId'], 'gateJudge': k['judge'],
                                 'humanLabels': {name: labels[name][item['id']] for name in ANNOTATIONS}}
    return found


def build_items(root: Path = ROOT) -> list[dict]:
    pool = bases(root)
    items = []
    for item_id, base_id, kind, before, after in RECIPES:
        require(base_id in pool, f'{base_id} 不是三人一致判为支持的陈述')
        base = pool[base_id]
        statement = base['statement']
        if before is not None:
            require(statement.count(before) == 1, f'{item_id}：替换前文本须在底稿中恰好出现一次')
            statement = statement.replace(before, after)
        items.append({'id': item_id, 'base': base_id, 'type': kind, 'caseId': base['caseId'],
                      'question': base['question'], 'contexts': base['contexts'], 'statement': statement,
                      'edit': None if before is None else {'before': before, 'after': after},
                      'expected': 'supported' if kind.startswith('control') else 'not_supported',
                      'baseHumanLabels': base['humanLabels'], 'baseGateJudge': base['gateJudge']})
    return items


def schedule(package: dict) -> list[dict]:
    jobs = [{'requestId': f"{item['id']}--{judge}", 'itemId': item['id'], 'judge': judge}
            for item in package['items'] for judge in package['judges']]
    random.Random(package['seed']).shuffle(jobs)
    return jobs


def rule_results(package: dict, root: Path = ROOT) -> dict:
    source = (root / SOURCE).read_text(encoding='utf-8')
    results = {}
    for item in package['items']:
        r = check_answer(item['statement'], item['contexts'], source, item['question'])
        results[item['id']] = {'flagged': r['flagged'], 'codes': sorted({f['code'] for f in r['findings'] if f['severity'] == 'error'}),
                               'findings': r['findings'], 'contextProvisions': r['contextProvisions']}
    return {'schemaVersion': 1, 'experimentVersion': VERSION, 'checker': 'backend/core/statute_check.py',
            'checkerSha256': sha256(ROOT / 'backend/core/statute_check.py'), 'items': results}


def max_requests(package: dict) -> int:
    return len(package['items']) * len(package['judges'])


def validate_package(package: dict, confirmed: bool = False, root: Path = ROOT):
    require(package.get('schemaVersion') == 1 and package.get('experimentVersion') == VERSION, '实验版本不一致')
    require(package.get('promptSha256') == digest(SYSTEM_PROMPT), '裁判提示词版本不一致')
    require(package.get('seed') == SEED and package['judges'] == judge_config(), '模型、参数或随机种子已改变，需重新准备和授权')
    require(set(package['sourceFiles']) == set(SOURCE_FILES), '来源清单不完整或含非实验来源')
    verify_sources(package['sourceFiles'], root)
    expected = build_items(root)
    if 'pilot' in package:
        require(package['pilot'] == pilot_info(), '试跑子集与固定规则不一致')
        expected = [item for item in expected if item['id'] in PILOT_IDS]
    require(package['items'] == expected, '注入材料与固定配方不一致')
    require(len(schedule(package)) == max_requests(package) == len(expected) * 2, '调用规模与配方不符')
    review = package['review']
    require(review['status'] in ('pending', 'confirmed'), '人工确认状态无效')
    if review['status'] == 'pending':
        require(all(review.get(k) is None for k in ('reviewer', 'reviewedAt', 'note')), '待确认材料不能预填人工信息')
    if confirmed or review['status'] == 'confirmed':
        require(review['status'] == 'confirmed' and all(isinstance(review.get(k), str) and review[k].strip()
                                                        for k in ('reviewer', 'reviewedAt', 'note')), '注入材料尚未人工确认')
        date.fromisoformat(review['reviewedAt'])


def estimate(package: dict) -> dict:
    """字节数是保守规划代理值，不是模型 tokenizer 的实际计数。"""
    result = {}
    for key, cfg in package['judges'].items():
        prompts = [sum(len(m['content'].encode('utf-8')) for m in prompt_messages(item, item)) + 256 for item in package['items']]
        result[key] = {'requests': len(prompts), 'inputPlanningTokens': sum(prompts), 'outputMaxTokens': len(prompts) * cfg['maxTokens'],
                       'method': 'UTF-8字节数加每请求256的消息开销代理；非实测token，不保证供应商计费上界。'}
    return result


def authorization_template(package: dict, package_path: Path) -> dict:
    return {'status': 'pending', 'authorizedBy': None, 'authorizedAt': None,
            'packageSha256': sha256(package_path), 'configurationSha256': digest(package['judges']),
            'promptSha256': package['promptSha256'], 'toolFiles': {name: sha256(ROOT / name) for name in TOOL_FILES},
            'maxRequests': max_requests(package), 'maxRetries': 0, 'budgetCny': None, 'outputDirectory': None,
            'acceptNoHardBillingCap': False, 'allowPaidCalls': False, 'estimates': estimate(package),
            'prices': {k: {'inputCnyPerMillion': None, 'outputCnyPerMillion': None, 'source': None, 'checkedAt': None} for k in package['judges']},
            'note': '待作者确认注入材料并单独授权付费。确认字段更新后 packageSha256 必然改变，须重新生成本文件。单价须当日核实，不沿用旧实验价格。'}


def confirmation_sheet(package: dict, authorization: dict, rules: dict) -> str:
    counts = Counter(item['type'] for item in package['items'])
    rows = [f"| {kind} | {TYPES[kind]} | {counts[kind]} | {sum(rules['items'][i['id']]['flagged'] for i in package['items'] if i['type'] == kind)} |"
            for kind in TYPES]
    items = [f"| {i['id']} | {i['base']}（{i['caseId']}） | {i['statement']} | {i['expected']} |" for i in package['items']]
    judges = [f"| {cfg['model']} | {cfg['baseUrl']} | {authorization['estimates'][k]['requests']} | {authorization['estimates'][k]['inputPlanningTokens']:,} | {authorization['estimates'][k]['outputMaxTokens']:,} |"
              for k, cfg in package['judges'].items()]
    return '\n'.join([
        '# 法条样式错误注入：运行确认单（尚未授权）', '',
        '本单由工具整理。材料确认、授权人、日期、单价与预算均待补；没有调用模型。', '',
        '## 范围', '',
        f"{len(package['items'])} 条陈述 × {len(package['judges'])} 个裁判，最多 {max_requests(package)} 次请求，失败也计数，不重试。",
        *([f"**试跑**：从 {package['pilot']['of']} 条配方中{package['pilot']['rule']}。"] if 'pilot' in package else []),
        '底稿为门禁 52 条陈述中 V、A、B 三人都判为「支持」的陈述；每条注入只做一处逐字替换。',
        '裁判只看问题、陈述与检索片段，提示词与 10-12 裁判证据敏感性实验相同；不发送类型、预期或规则结果。', '',
        '## 各类条数与法条结构核对（离线，已完成）', '',
        '| 类型 | 说明 | 条数 | 规则检出 |', '|---|---|---:|---:|', *rows, '',
        '规则结果在准备时离线算出，写在 `rules.json`；规则代码在构造注入集之前定稿，之后未调整。', '',
        '## 逐条材料', '',
        '| 编号 | 底稿 | 陈述 | 预期 |', '|---|---|---|---|', *items, '',
        '## 裁判配置与用量代理估算', '',
        '| 请求模型 | HTTPS地址 | 请求数 | 输入规划值 | 最大输出token |', '|---|---|---:|---:|---:|', *judges, '',
        '统一 temperature=0、timeout=90 秒、maxRetries=0。输入规划值为 UTF-8 字节数加消息开销，不是实际 token。', '',
        '## 待确认', '',
        '1. 作者核对逐条材料与预期：注入是否确属错误、对照是否确属正确；在 package.json 的 review 中填写姓名、日期与依据。',
        '2. 核对两个模型、当日单价与预算，明确接受没有供应商侧扣费硬上限。',
        '3. 确认后重新生成授权草稿，绑定新的材料哈希，并指定一个新的运行目录。', '',
        f"当前材料 SHA-256：`{authorization['packageSha256']}`。", '',
        '本单不代表材料已确认或付费已授权。', '',
    ])


def pilot_info() -> dict:
    return {'ids': list(PILOT_IDS), 'of': len(RECIPES), 'rule': PILOT_RULE}


def prepare(output: Path, root: Path = ROOT, pilot: bool = False) -> dict:
    require(not output.exists(), '输出目录已存在；不覆盖任何准备材料')
    items = build_items(root)
    package = {'schemaVersion': 1, 'experimentVersion': VERSION, 'seed': SEED, 'promptSha256': digest(SYSTEM_PROMPT),
               'judges': judge_config(), 'sourceFiles': {name: sha256(root / name) for name in SOURCE_FILES},
               'types': TYPES, 'items': [i for i in items if i['id'] in PILOT_IDS] if pilot else items, 'review': pending(),
               'scope': '定向注入测试：陈述共享同一份法规节选与 11 条底稿，非统计独立样本，不估计总体检出率。'}
    if pilot:
        package['pilot'] = pilot_info()
    validate_package(package, root=root)
    rules = rule_results(package, root)
    output.mkdir(parents=True)
    write_json(output / 'package.json', package)
    authorization = authorization_template(package, output / 'package.json')
    write_json(output / 'authorization.draft.json', authorization)
    write_json(output / 'rules.json', rules)
    write_text(output / 'RUN_CONFIRMATION.md', confirmation_sheet(package, authorization, rules))
    write_text(output / 'prompt.txt', SYSTEM_PROMPT)
    checksums(output)
    return {'package': package, 'rules': rules}


def validate_authorization(package: dict, package_path: Path, auth: dict):
    require(auth.get('status') == 'authorized' and auth.get('allowPaidCalls') is True and auth.get('acceptNoHardBillingCap') is True, '缺少本次付费授权及费用上限声明')
    require(all(isinstance(auth.get(k), str) and auth[k].strip() for k in ('authorizedBy', 'authorizedAt')), '缺少真实授权人和日期')
    date.fromisoformat(auth['authorizedAt'])
    require(auth['packageSha256'] == sha256(package_path), '授权绑定的材料哈希不一致')
    require(auth['configurationSha256'] == digest(package['judges']) and auth['promptSha256'] == digest(SYSTEM_PROMPT), '授权配置或提示词已变化')
    require(set(auth.get('toolFiles', {})) == set(TOOL_FILES), '授权缺少工具源码版本')
    verify_sources(auth['toolFiles'])
    require(auth['maxRequests'] == max_requests(package) and auth['maxRetries'] == 0, '调用上限或重试配置不符')
    require(type(auth['budgetCny']) in (float, int) and math.isfinite(auth['budgetCny']) and auth['budgetCny'] > 0, '预算未确认')
    for judge in package['judges']:
        price = auth['prices'][judge]
        require(all(type(price.get(k)) in (int, float) and math.isfinite(price[k]) and price[k] >= 0 for k in ('inputCnyPerMillion', 'outputCnyPerMillion')), '单价未知，不能启动付费运行')
        require(isinstance(price.get('source'), str) and price['source'].strip(), '缺少价格依据')
        date.fromisoformat(price['checkedAt'])


def run(package_path: Path, authorization_path: Path, output: Path, *, root: Path = ROOT, transport=None) -> dict:
    package, auth = load(package_path), load(authorization_path)
    validate_package(package, confirmed=True, root=root)
    validate_authorization(package, package_path, auth)
    require(isinstance(auth.get('outputDirectory'), str) and (root / auth['outputDirectory']).resolve() == output.resolve(), '本次授权未绑定该运行目录')
    require(not output.exists(), '运行目录已存在；禁止覆盖、自动续跑或整批重跑')
    send = transport if transport is not None else make_transport(package)
    jobs, limit = schedule(package), max_requests(package)
    output.mkdir(parents=True)
    shutil.copyfile(package_path, output / 'package.json')
    shutil.copyfile(authorization_path, output / 'authorization.json')
    write_json(output / 'schedule.json', jobs)
    write_text(output / 'prompt.txt', SYSTEM_PROMPT)
    (output / 'sources').mkdir()
    for name in TOOL_FILES:
        shutil.copyfile(root / name, output / 'sources' / Path(name).name)
    for name in SOURCE_FILES:
        target = output / 'inputs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    write_json(output / 'runtime.json', {'startedAt': utc(), 'python': sys.version, 'dependencies': dependency_versions(),
                                         'git': git_state(root), 'parameters': package['judges'], 'noHardBillingCap': True})
    items = {item['id']: item for item in package['items']}
    records, calls, cost, stop = [], 0, 0.0, 'completed'
    events = output / 'events.jsonl'
    try:
        for job in jobs:
            if calls >= limit:
                stop = 'request_limit'
                break
            item, cfg, price = items[job['itemId']], package['judges'][job['judge']], auth['prices'][job['judge']]
            messages = prompt_messages(item, item)
            planned = sum(len(m['content'].encode()) for m in messages) + 256
            if cost + request_cost({'prompt_tokens': planned, 'completion_tokens': cfg['maxTokens']}, price) > auth['budgetCny']:
                stop = 'estimated_budget_guard'
                break
            payload = {'model': cfg['model'], 'messages': messages, 'temperature': cfg['temperature'], 'max_tokens': cfg['maxTokens'], 'stream': False}
            # 先持久化一次尝试，再发送；异常也占调用预算。不存在内部重试。
            append_event(events, {'event': 'start', **job, 'at': utc(), 'request': payload})
            calls += 1
            record = {**job, 'status': 'call_error', 'result': None, 'actualModel': None, 'usage': None, 'error': None}
            response = None
            try:
                response = send(job['judge'], payload)
                record.update(response_fields(response, item['contexts']))
                if isinstance(response, dict) and '_transport' in response:
                    problem = response['_transport']
                    record['error'] = {'type': problem['error'], 'httpStatus': problem['httpStatus'], 'note': '原始异常响应体保留在response._transport.body；已去除已知密钥回显。'}
            except Exception as error:
                record['error'] = {'type': type(error).__name__, 'httpStatus': getattr(getattr(error, 'response', None), 'status_code', None),
                                   'note': '异常消息可能包含认证信息，不归档；没有自动重试。'}
            append_event(events, {'event': 'end', **record, 'at': utc(), 'response': response})
            records.append(record)
            if record['usage'] is None:
                stop = 'usage_unknown'
                break
            cost += request_cost(record['usage'], price)
            if record['actualModel'] is None:
                stop = 'actual_model_unknown'
                break
    except BaseException:
        stop = 'interrupted'
        raise
    finally:
        manifest = {'schemaVersion': 1, 'experimentVersion': VERSION, 'finishedAt': utc(), 'stopReason': stop,
                    'attempts': calls, 'completedRecords': len(records), 'plannedRequests': limit,
                    'estimatedCostCny': cost, 'actualBillCny': None,
                    'costNote': '按返回用量与授权单价的估算，不是实际扣费；没有供应商费用自动封顶。'}
        write_json(output / 'manifest.json', manifest)
        write_json(output / 'records.json', records)
        checksums(output)
    return manifest


def read_records(directory: Path, package: dict) -> list[dict]:
    jobs = {j['requestId']: j for j in schedule(package)}
    items = {item['id']: item for item in package['items']}
    starts, ends = {}, {}
    path = directory / 'events.jsonl'
    for line in (path.read_text(encoding='utf-8').splitlines() if path.exists() else []):
        event = json.loads(line)
        identity = event['requestId']
        require(identity in jobs and all(event.get(k) == v for k, v in jobs[identity].items()), '请求与材料配对不一致')
        target = starts if event['event'] == 'start' else ends
        require(event['event'] in ('start', 'end') and identity not in target, '重复或未知事件')
        require(event['event'] != 'end' or identity in starts, '缺少请求原记录')
        target[identity] = event
    records = []
    for identity, start in starts.items():
        job, item = jobs[identity], items[jobs[identity]['itemId']]
        cfg = package['judges'][job['judge']]
        require(start['request'] == {'model': cfg['model'], 'messages': prompt_messages(item, item), 'temperature': cfg['temperature'],
                                     'max_tokens': cfg['maxTokens'], 'stream': False}, '实际请求与固定材料不一致')
        if identity not in ends:
            records.append({**job, 'status': 'incomplete', 'result': None, 'actualModel': None, 'usage': None, 'error': {'type': 'interrupted'}})
            continue
        record = {k: v for k, v in ends[identity].items() if k not in ('event', 'at', 'response')}
        fields = response_fields(ends[identity].get('response'), item['contexts'])
        require(all(record.get(k) == v for k, v in fields.items()), '保存的判定、用量或实际模型与原始响应不一致')
        records.append(record)
    return records


def summarize(package: dict, rules: dict, records: list[dict]) -> dict:
    verdicts = {(r['itemId'], r['judge']): (r['result'] or {}).get('verdict') if r['status'] == 'ok' else None for r in records}
    by_type = {}
    for kind in TYPES:
        members = [i for i in package['items'] if i['type'] == kind]
        row = {'items': len(members), 'expected': 'supported' if kind.startswith('control') else 'not_supported',
               'rulesFlagged': sum(rules['items'][i['id']]['flagged'] for i in members), 'judges': {}}
        for judge in package['judges']:
            got = [verdicts.get((i['id'], judge)) for i in members]
            row['judges'][judge] = {'valid': sum(v is not None for v in got), 'notSupported': sum(v in NOT_SUPPORTED for v in got),
                                    'contradicted': sum(v == 'contradicted' for v in got)}
        by_type[kind] = row
    rows = [{'id': i['id'], 'type': i['type'], 'statement': i['statement'], 'expected': i['expected'],
             'rules': {'flagged': rules['items'][i['id']]['flagged'], 'codes': rules['items'][i['id']]['codes']},
             'judges': {judge: verdicts.get((i['id'], judge)) for judge in package['judges']}} for i in package['items']]
    return {'schemaVersion': 1, 'experimentVersion': VERSION, 'byType': by_type, 'rows': rows,
            'records': {'total': len(records), 'ok': sum(r['status'] == 'ok' for r in records)}}


def analyze(directory: Path, output: Path) -> dict:
    require(not output.exists(), '分析输出已存在；使用新的独立目录')
    verify_checksums(directory)
    package = load(directory / 'package.json')
    validate_package(package, confirmed=True, root=directory / 'inputs')
    require(load(directory / 'schedule.json') == schedule(package), '归档请求计划与固定材料不符')
    auth = load(directory / 'authorization.json')
    require(auth['packageSha256'] == sha256(directory / 'package.json'), '归档授权和材料绑定不符')
    for name in TOOL_FILES:
        require(sha256(directory / 'sources' / Path(name).name) == auth['toolFiles'][name], '归档源码与授权不符')
    records = read_records(directory, package)
    terminal = [r for r in records if r['status'] != 'incomplete']
    require(load(directory / 'records.json') == terminal, '归档结果摘要与原始事件不符')
    # 规则结果用当前仓库的检查器重算，它须与授权时绑定的版本逐字节相同
    require(sha256(ROOT / 'backend/core/statute_check.py') == auth['toolFiles']['backend/core/statute_check.py'], '当前法条结构核对代码与授权时不同')
    rules = rule_results(package, directory / 'inputs')
    summary = summarize(package, rules, records)
    output.mkdir(parents=True)
    write_json(output / 'analysis.json', summary)
    write_json(output / 'rules.json', rules)
    checksums(output)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare', help='离线生成待确认材料与规则结果；不调用模型')
    prep.add_argument('--output', type=Path, required=True)
    prep.add_argument('--pilot', action='store_true', help=f'只准备试跑子集（{len(PILOT_IDS)} 条）')
    execute = sub.add_parser('run', help='付费阶段：需人工材料确认与单独授权')
    execute.add_argument('--package', type=Path, required=True)
    execute.add_argument('--authorization', type=Path, required=True)
    execute.add_argument('--output', type=Path, required=True)
    analysis = sub.add_parser('analyze', help='校验归档并离线分析，不调用模型')
    analysis.add_argument('--run', type=Path, required=True)
    analysis.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output, pilot=args.pilot)
        flagged = Counter(i['type'] for i in result['package']['items'] if result['rules']['items'][i['id']]['flagged'])
        total = Counter(i['type'] for i in result['package']['items'])
        print(f"已准备 {len(result['package']['items'])} 条陈述、{max_requests(result['package'])} 个计划请求；实际调用 0 次。")
        for kind in TYPES:
            print(f"  {kind}: 规则检出 {flagged[kind]} / {total[kind]}")
    elif args.command == 'run':
        print(json.dumps(run(args.package, args.authorization, args.output), ensure_ascii=False))
    else:
        summary = analyze(args.run, args.output)
        print(json.dumps(summary['byType'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
