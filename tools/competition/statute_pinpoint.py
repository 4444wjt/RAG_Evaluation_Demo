"""款、项定位对照：离线准备、作者确认、独立授权双提示运行及离线分析。

复用冻结注入工具的数据、提示词、规则与归档函数，不更改其配方或运行协议。
用法：python tools/competition/statute_pinpoint.py prepare --output <新的空目录>
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal
import difflib
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.statute_check import cn_label, parse_provisions  # noqa: E402
from tools.competition import statute_injection as si  # noqa: E402

VERSION = 'statute-pinpoint-prep-v1'
FORMAL = 'docs/competition/statute-check/20261021-formal/run'
ADDITION = '\n请逐一核对陈述所引的条、款、项是否与检索上下文中的依据一致，不能仅因内容主题相近就接受具体定位。'
PROMPTS = {'original': si.SYSTEM_PROMPT, 'pinpoint': si.SYSTEM_PROMPT + ADDITION}
TYPES = {
    'control': '原陈述',
    'control_pinpoint': '加正确款号（S26 同时加正确项号）',
    'paragraph_existing': '换成同条存在的另一款',
    'paragraph_absent': '换成同条不存在的款',
    'item_existing': '换成同款存在的另一项',
    'item_absent': '换成同款不存在的项',
    'paragraph_omitted': '省去款号、内容仍属该条的非第一款（支持对照）',
}
ERROR_TYPES = ('paragraph_existing', 'paragraph_absent', 'item_existing', 'item_absent')
SCOPES = {'pending': '定向合成定位扰动；作者尚未确认，不是模型运行结果，不参与 Quality Gate。',
          'confirmed': '定向合成定位扰动；作者已确认预期标签，不是模型运行结果，不参与 Quality Gate。'}
# 底稿、条、款、项、唯一替换锚点。S32 的条号是“不适用”的对象，仅保留原样对照。
ANCHORS = (
    ('S03', 5, 1, None, '第五条'),
    ('S07', 5, 1, None, '存款保险'),
    ('S13', 2, 1, None, '根据提供的文档'),
    ('S21', 4, 1, None, '第四条'),
    ('S27', 19, 2, None, '存款保险基金管理机构'),
    ('S28', 3, 1, None, '第三条'),
    ('S36', 4, 1, None, '第四条'),
    ('S41', 10, 2, None, '第十条'),
    ('S49', 8, 1, None, '第八条'),
    ('S50', 8, 2, None, '本条例施行后'),
    ('S01', 5, 2, None, '同一存款人'),
    ('S19', 5, 3, None, '存款保险基金管理机构'),
    ('S26', 19, 1, 3, '根据提供的文档'),
)
METHODS = [
    '拟定口径，必须作者逐条确认后锁定；运行后不改。变更材料或口径须另起版本并重新授权。',
    '主指标：款项定位错误检出条数及支持对照误报条数；四类错误分别报告，并汇总款级与项级；省略款号单列为支持对照。',
    '规则仅 error 级算检出；裁判 contradicted 或 insufficient 算检出；supported 算未检出；调用或解析失败单列，不能当成未检出或从计划总数中静默删除。',
    '逐裁判配对比较 original 与 pinpoint：新增检出、丢失检出、新增误报、减少误报，并列出对应 ID；报告规则与两种提示下裁判任一检出的并集条数。',
    '两种提示使用完全相同的陈述、问题、原检索片段、模型参数与顺序配对；裁判只收到 question、statement、contexts，不发送预期、类型、规则或人审标签。',
    '规则另读完整法规节选，裁判只读原检索片段；信息条件不同，不称严格等信息比较。缺标题或缺前款的证据另列，不把定位缺失强判为矛盾。',
    '原始及正确定位对照允许只说条文的一部分；省去款号不等于引用第一款，内容有依据则拟标 supported。',
    '配方按结构穷举适用底稿的同条其他款、同款其他项；不存在款/项取最大号加一；不按离线检出结果选样。S32 只作原样对照，S41 省款与原样重复故不新增。',
    'S26 的历史标注 V/A=支持、B=无法判断，不能宣称三人一致支持。主表保留其待确认预期；同时固定报告剔除 S26 全部变体的敏感性条数。',
    '只报条数，不报百分比、置信区间或显著性；共享底稿、单一法规、项级仅一份底稿，均非独立样本，不推广总体效果。',
    '单次配对、不重试；未来执行需单独实现并审核双提示运行器及绑定两提示哈希的授权验证，本工具不执行模型请求。',
]


def label(article, paragraph=None, item=None):
    return (f'第{cn_label(article)}条' + (f'第{cn_label(paragraph)}款' if paragraph else '')
            + (f'第{cn_label(item)}项' if item else ''))


def replace_once(text, before, after):
    si.require(text.count(before) == 1 and before != after, '替换锚点必须唯一且确有变化')
    start = text.index(before)
    return text[:start] + after + text[start + len(before):], {'before': before, 'after': after, 'start': start}


def source_pool(root=ROOT):
    pool = si.bases(root)
    package, key = si.jc.build_package()
    labels = {name: si.jc.load_annotation(root / path, package)['labels'] for name, path in si.ANNOTATIONS.items()}
    questions = {q['id']: q for q in package['questions']}
    for item, origin in zip(package['items'], key):
        if item['id'] != 'S26':
            continue
        q = questions[item['question']]
        pool[item['id']] = {'statement': item['statement'], 'question': q['question'], 'contexts': q['contexts'],
                            'caseId': origin['caseId'], 'gateJudge': origin['judge'],
                            'humanLabels': {name: values[item['id']] for name, values in labels.items()}}
    return pool


def build_items(root=ROOT):
    pool = source_pool(root)
    provisions = parse_provisions((root / si.SOURCE).read_text(encoding='utf-8'))
    original_ids = [r[1] for r in si.RECIPES if r[2] == 'control'] + ['S01', 'S19', 'S26']
    items = []

    def add(sid, kind, suffix, statement, edit=None, **extra):
        base = pool[sid]
        items.append({'id': f'{kind}-{sid}{suffix}', 'base': sid, 'type': kind,
                      'caseId': base['caseId'], 'question': base['question'], 'contexts': base['contexts'],
                      'originalStatement': base['statement'], 'statement': statement, 'edit': edit,
                      'expected': 'not_supported' if kind in ERROR_TYPES else 'supported',
                      'baseHumanLabels': base['humanLabels'], 'baseGateJudge': base['gateJudge'],
                      'review': si.pending(), **extra})

    for sid in original_ids:
        add(sid, 'control', '', pool[sid]['statement'],
            reason='沿用底稿，仅按所给资料判断；历史标注不是本实验作者确认。')
    for sid, article, paragraph, item, before in ANCHORS:
        original = pool[sid]['statement']
        correct = label(article, paragraph, item)
        after = correct if before.startswith('第') else ('根据' + correct if before.startswith('根据') else f'根据{correct}，{before}')
        anchor, anchor_edit = replace_once(original, before, after)
        evidence = [p for p in provisions if (p.article, p.paragraph, p.item) == (article, paragraph, item)]
        si.require(len(evidence) == 1, f'{sid} 的正确定位不唯一')
        shared = {'sourceLocation': {'article': article, 'paragraph': paragraph, 'item': item},
                  'evidence': [{'label': p.label, 'quote': p.text} for p in evidence],
                  'anchorStatement': anchor, 'anchorEdit': anchor_edit,
                  'evidenceVisible': any(evidence[0].text in c for c in pool[sid]['contexts'])}
        add(sid, 'control_pinpoint', '', anchor, anchor_edit,
            reason=f'内容依据为{correct}；仅补明确定位，预期支持，待作者确认。', **shared)

        def changed(kind, target, replacement, reason, suffix=''):
            statement, pinpoint_edit = replace_once(anchor, correct, replacement)
            # 对原陈述也是一处替换；同时记录相对正确定位对照的单处扰动。
            mutated_after = after.replace(correct, replacement, 1)
            rebuilt, edit = replace_once(original, before, mutated_after)
            si.require(rebuilt == statement, '原底稿与正确定位对照的替换不一致')
            add(sid, kind, suffix, statement, edit, targetLocation=target,
                pinpointEdit=pinpoint_edit, reason=reason, **shared)

        paragraphs = sorted({p.paragraph for p in provisions if p.article == article})
        if item is None:
            for other in paragraphs:
                if other != paragraph:
                    changed('paragraph_existing', {'article': article, 'paragraph': other, 'item': None},
                            label(article, other), f'内容来自{correct}，所引另一款存在但内容不对应。', f'-P{other}')
        absent = max(paragraphs) + 1
        changed('paragraph_absent', {'article': article, 'paragraph': absent, 'item': item},
                label(article, absent, item), f'节选中该条只有{len(paragraphs)}款，不存在第{cn_label(absent)}款。')
        if item is not None:
            item_numbers = sorted(p.item for p in provisions if p.article == article and p.paragraph == paragraph and p.item is not None)
            for other in item_numbers:
                if other != item:
                    changed('item_existing', {'article': article, 'paragraph': paragraph, 'item': other},
                            label(article, paragraph, other), f'引文来自{correct}，第{cn_label(other)}项规定另一情形。', f'-I{other}')
            absent_item = max(item_numbers) + 1
            changed('item_absent', {'article': article, 'paragraph': paragraph, 'item': absent_item},
                    label(article, paragraph, absent_item), f'该款只有{len(item_numbers)}项，无第{cn_label(absent_item)}项。')
        if paragraph > 1 and item is None:
            omitted = anchor.replace(correct, label(article), 1)
            # S41 的原陈述已仅引条号；不重复发送完全相同的原样对照。
            if omitted != original:
                changed('paragraph_omitted', {'article': article, 'paragraph': None, 'item': None}, label(article),
                        f'只省去第{cn_label(paragraph)}款，内容仍来自{correct}；未声称第一款，拟定为支持。')
    si.require(len({i['id'] for i in items}) == len(items), '材料编号重复')
    return items


def prompt_messages(item, prompt_id):
    messages = deepcopy(si.prompt_messages(item, item))
    messages[0]['content'] = PROMPTS[prompt_id]
    return messages


def cost_estimate(package, root=ROOT):
    """以历史实测 token / 消息字节数作输入折算，输出沿用历史每请求均值。"""
    historical = si.load(root / FORMAL / 'package.json')
    records = si.load(root / FORMAL / 'records.json')
    old_items = {i['id']: i for i in historical['items']}
    prices = {'deepseek': (2, 8), 'qwen72b': (4.13, 4.13)}

    def size(messages):
        return sum(len(m['content'].encode('utf-8')) for m in messages)

    result = {}
    for judge in package['judges']:
        rows = [r for r in records if r['judge'] == judge]
        si.require(len(rows) == 42 and all(r['status'] == 'ok' and isinstance(r.get('usage'), dict) for r in rows), '正式运行用量不完整')
        input_total = sum(r['usage']['prompt_tokens'] for r in rows)
        output_total = sum(r['usage']['completion_tokens'] for r in rows)
        old_bytes = sum(size(si.prompt_messages(old_items[r['itemId']], old_items[r['itemId']])) for r in rows)
        conditions = {}
        for prompt_id in PROMPTS:
            byte_count = sum(size(prompt_messages(i, prompt_id)) for i in package['items'])
            planned_in = math.ceil(input_total * byte_count / old_bytes)
            planned_out = math.ceil(output_total * len(package['items']) / len(rows))
            conditions[prompt_id] = {'requests': len(package['items']), 'messageBytes': byte_count,
                                     'inputTokensEstimate': planned_in, 'outputTokensEstimate': planned_out,
                                     'costCnyEstimate': round((planned_in * prices[judge][0] + planned_out * prices[judge][1]) / 1_000_000, 6)}
        result[judge] = {'historicalRequests': len(rows), 'historicalInputTokens': input_total,
                         'historicalOutputTokens': output_total, 'historicalMessageBytes': old_bytes,
                         'actualModels': sorted({r['actualModel'] for r in rows}), 'byPrompt': conditions,
                         'costCnyEstimate': round(sum(c['costCnyEstimate'] for c in conditions.values()), 6)}
    return {'method': '输入=历史实际输入token×新消息UTF-8字节数/历史消息字节数，向上取整；输出=历史每请求实际均值×请求数，向上取整。',
            'limitation': '不是账单、tokenizer 实测或费用上限；逐款提示可能增加输出；单价按任务指定的2026-10-21价格，未重新联网核价。',
            'judges': result, 'totalCostCnyEstimate': round(sum(r['costCnyEstimate'] for r in result.values()), 6)}


def authorization_template(package, path, estimates):
    auth = si.authorization_template(package, path)
    auth.update(status='draft', maxRequests=len(package['items']) * len(package['judges']) * len(PROMPTS),
                estimates=estimates, promptSetSha256=si.digest(PROMPTS),
                promptHashes={name: si.digest(value) for name, value in PROMPTS.items()},
                preparationToolFiles={name: si.sha256(ROOT / name) for name in
                                      ('tools/competition/statute_pinpoint.py', 'tests/test_statute_pinpoint.py')})
    for judge, rates in {'deepseek': (2, 8), 'qwen72b': (4.13, 4.13)}.items():
        auth['prices'][judge].update(inputCnyPerMillion=rates[0], outputCnyPerMillion=rates[1],
                                     source='任务指定的2026-10-21单价，与正式归档当日单价一致；未来运行须重新核实。', checkedAt='2026-10-21')
    auth['note'] = ('仅沿用 validate_authorization 字段结构，未获授权。旧运行器只支持单提示且固定配方，不能执行本包；'
                    'maxRequests 为陈述数×2裁判×2提示。未来双提示运行器须另行审核并同时绑定 promptSetSha256、'
                    'preparationToolFiles、口径和逐条确认后的 packageSha256。不得直接改 status 后调用旧 run。')
    return auth


def confirmation_sheet(package, auth, rules):
    review = package['review']
    confirmed = review['status'] == 'confirmed'
    counts = Counter(i['type'] for i in package['items'])
    distribution = ['| 分组 | 条数 | 规则 error 检出条数 |', '|---|---:|---:|']
    distribution += [f"| {TYPES[kind]} | {counts[kind]} | {sum(rules['items'][i['id']]['flagged'] for i in package['items'] if i['type'] == kind)} |"
                     for kind in TYPES]
    lines = [f"# 款项定位对照：作者逐条确认单（{'已确认' if confirmed else '未确认'}、未授权）", '',
             '本单由 AI 工具生成；历史标签仅作来源，未代填作者身份。原检索片段完整保存在 package.json。', '']
    if confirmed:
        lines.extend(['## 作者确认记录', '', f"确认人：{review['reviewer']}；日期：{review['reviewedAt']}", '',
                      '确认说明全文：', '', review['note'], ''])
    lines.extend([
             '## 分组与离线结果', '', *distribution, '',
             '## 拟定分组与主指标', '', *[f'{n}. {m}' for n, m in enumerate(METHODS, 1)], '',
             '底稿：沿用原 11 条，补 S01（第五条第二款）、S19（第五条第三款）、S26（第十九条第一款第三项）。',
             'S01、S19 三人均支持；S26 存在历史分歧，项级所有变体依赖这一个底稿，须重点确认。',
             'S32 引用是不适用对象，未构造定位扰动；S41 省款版本等于原陈述，不重复计数。', '',
             '## 逐条核对' + ('（已确认）' if confirmed else '（签名留空）'), ''])
    for item in package['items']:
        r = rules['items'][item['id']]
        lines.extend([f"### {item['id']}", '', f"底稿 {item['base']} / {item['caseId']}；历史标签：{item['baseHumanLabels']}",
                      f"原陈述：{item['originalStatement']}", f"实验陈述：{item['statement']}",
                      f"注入位置（Python字符偏移，非字节）：{item['edit']}",
                      f"相对正确定位对照的扰动：{item.get('pinpointEdit')}",
                      f"拟定预期：{item['expected']}；理由：{item['reason']}",
                      f"离线规则：flagged={r['flagged']}；error代码={r['codes']}"])
        for evidence in item.get('evidence', []):
            lines.append(f"法规引文（{evidence['label']}）：{evidence['quote']}")
        if item['type'] == 'control':
            lines.extend([f'原检索片段 {n}：{c}' for n, c in enumerate(item['contexts'])])
        lines.extend([f"作者已确认：{review['reviewer']}；日期：{review['reviewedAt']}；依据见确认说明全文。" if confirmed
                      else '作者确认：□同意 □需修订；标签与依据：________；签名：________；日期：________', ''])
    lines.extend(['## 提示词新增说明全文', '', ADDITION.lstrip('\n'), '',
                  '原提示逐字保存在 prompt.original.txt；变体仅在尾部添加上述一行，完整差异见 prompts.diff。', '',
                  '## 用量、授权与待确认', '',
                  f"{len(package['items'])} 条 × 2 裁判 × 2 提示 = {auth['maxRequests']} 次请求；不重试。",
                  f"预计费用 ¥{auth['estimates']['totalCostCnyEstimate']:.6f}，非账单或上限；详见 cost-estimate.json。",
                  ('作者已确认' if confirmed else '作者须确认') + '：所有预期、S26 历史分歧、仅引条号对照、原片段定位不足、共享底稿、分组及失败口径。',
                  '另行授权前须核实当日单价、预算、新运行目录，审核未来双提示运行器；本工具没有 run 子命令。',
                  (f"口径总确认：{review['reviewer']}；日期：{review['reviewedAt']}；运行后不改。" if confirmed
                   else '口径总确认签名：________；日期：________；运行后不改。'),
                  f"当前材料 SHA-256：{auth['packageSha256']}；确认字段变更后必须重算并重绑授权。", ''])
    return '\n'.join(lines)


def prepare(output: Path, root: Path = ROOT):
    si.require(not output.exists() or (output.is_dir() and not any(output.iterdir())), '输出必须是新的空目录，不能覆盖既有材料')
    # 仅校验公开归档和源码，不读取本地凭据。
    si.verify_checksums(root / FORMAL)
    si.require((root / FORMAL / 'prompt.txt').read_bytes() == si.SYSTEM_PROMPT.encode('utf-8'), '正式归档与固定提示词字节不一致')
    sources = (*si.SOURCE_FILES, f'{FORMAL}/package.json', f'{FORMAL}/records.json', f'{FORMAL}/prompt.txt')
    package = {'schemaVersion': 1, 'experimentVersion': VERSION, 'seed': si.SEED,
               'promptSha256': si.digest(si.SYSTEM_PROMPT), 'promptSetSha256': si.digest(PROMPTS),
               'prompts': PROMPTS, 'judges': si.judge_config(), 'types': TYPES,
               'sourceFiles': {name: si.sha256(root / name) for name in sources},
               'items': build_items(root), 'review': si.pending(), 'analysisPlan': METHODS, 'scope': SCOPES['pending']}
    rules = si.rule_results(package, root)
    rules['experimentVersion'] = VERSION
    estimates = cost_estimate(package, root)
    return write_preparation(output, package, rules, estimates)


def write_preparation(output, package, rules, estimates):
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / 'package.json', package)
    auth = authorization_template(package, output / 'package.json', estimates)
    si.write_json(output / 'authorization.draft.json', auth)
    si.write_json(output / 'rules.json', rules)
    si.write_json(output / 'cost-estimate.json', estimates)
    si.write_text(output / 'RUN_CONFIRMATION.md', confirmation_sheet(package, auth, rules))
    for name, prompt in PROMPTS.items():
        si.write_text(output / f'prompt.{name}.txt', prompt)
    si.write_text(output / 'prompts.diff', ''.join(difflib.unified_diff(
        (PROMPTS['original'] + '\n').splitlines(keepends=True), (PROMPTS['pinpoint'] + '\n').splitlines(keepends=True),
        fromfile='prompt.original.txt', tofile='prompt.pinpoint.txt')))
    si.checksums(output)
    return {'package': package, 'rules': rules, 'authorization': auth}


def confirm(package_path: Path, reviewer: str, reviewed_at: str, note_file: Path, output: Path, root: Path = ROOT):
    si.require(not output.exists() or (output.is_dir() and not any(output.iterdir())), '输出必须是新的空目录，不能覆盖既有材料')
    si.require(reviewer.strip(), '确认人不能为空')
    si.require(date.fromisoformat(reviewed_at).isoformat() == reviewed_at, '日期必须为 ISO 日期 YYYY-MM-DD')
    # 按原始 UTF-8 解码，保留 CRLF、段内空白和所有文字；只允许去掉末尾换行。
    note = note_file.read_bytes().decode('utf-8').rstrip('\r\n')
    si.require(note.strip(), '确认说明不能为空')
    review = {'status': 'confirmed', 'reviewer': reviewer, 'reviewedAt': reviewed_at, 'note': note}
    package = si.load(package_path)
    items = package['items']
    # 已是同一份确认时重跑得到相同产物；确认人、日期或说明不同则拒绝，不能悄悄改写确认。
    state = 'pending' if package.get('review') == si.pending() else 'confirmed'
    si.require(package.get('review') == (si.pending() if state == 'pending' else review)
               and all(i.get('review') == package['review'] for i in items) and package.get('scope') == SCOPES[state],
               '包级与逐条 review 必须均为 pending，或均为本次同一份确认')
    si.require(len(items) == 66, '材料必须为 66 条')
    si.require(Counter(i['type'] for i in items) == Counter(i['type'] for i in build_items(root)), '分组计数与 prep 配方不一致')
    by_id = {i['id']: i for i in items}
    si.require(len(by_id) == len(items), '材料编号重复')
    for item_id in ('item_existing-S26-I1', 'item_existing-S26-I2', 'item_existing-S26-I4', 'item_absent-S26'):
        si.require(by_id.get(item_id, {}).get('expected') == 'not_supported', f'{item_id} 必须为 not_supported')
    control = by_id.get('control-S26', {})
    si.require(control.get('expected') == 'supported', 'control-S26 必须为 supported')
    si.require(control.get('baseHumanLabels') == {'V': '支持', 'A': '支持', 'B': '无法判断'}, 'control-S26 历史分歧必须原样保留')
    material = [{k: v for k, v in i.items() if k != 'review'} for i in items]
    si.require(material == [{k: v for k, v in i.items() if k != 'review'} for i in build_items(root)], '材料与 prep 配方逐条不一致')
    package['review'] = deepcopy(review)
    package['scope'] = SCOPES['confirmed']
    for item in items:
        item['review'] = deepcopy(review)
    rules = si.rule_results(package, root)
    rules['experimentVersion'] = VERSION
    estimates = cost_estimate(package, root)
    si.require(rules == si.load(package_path.parent / 'rules.json'), '离线规则结果与 prep 不一致')
    si.require(estimates == si.load(package_path.parent / 'cost-estimate.json'), '费用估算与 prep 不一致')
    si.require(package['prompts'] == PROMPTS, '提示词与 prep 配方不一致')
    return write_preparation(output, package, rules, estimates)


PREPARATION_TOOLS = ('tools/competition/statute_pinpoint.py', 'tests/test_statute_pinpoint.py')
COST_NOTE = ('返回 usage 有效时按输入/输出 token 与授权单价累计；usage 缺失或无效（含调用失败）时，'
             '保守地将授权 estimates 对应裁判/提示的整条件 inputTokensEstimate 作为单请求输入规划上界，'
             '输出取该条件 outputTokensEstimate 与 maxTokens 的较大值。不是供应商扣费上界；'
             '达到预算后不再发送下一请求，最后一次响应可能使累计超过预算。')


def empty_output(output):
    si.require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
               '输出必须是新的空目录；禁止覆盖或续跑')


def iso_date(value, message):
    si.require(isinstance(value, str) and value.strip(), message)
    try:
        valid = date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    si.require(valid, message)


def confirmed_review(review, where):
    si.require(isinstance(review, dict) and review.get('status') == 'confirmed'
               and all(isinstance(review.get(k), str) and review[k].strip()
                       for k in ('reviewer', 'reviewedAt', 'note')), f'{where}尚未完整确认')
    iso_date(review['reviewedAt'], f'{where}确认日期必须为 ISO 日期 YYYY-MM-DD')


def validate_package(package, root=ROOT):
    # 包级、逐条分别拒绝，不能以包级签名替代材料逐条确认。
    si.require(isinstance(package, dict), '材料包必须为对象')
    confirmed_review(package.get('review'), '包级 review ')
    items = package.get('items')
    si.require(isinstance(items, list) and len(items) == 66, '材料必须为 66 条')
    for index, item in enumerate(items):
        si.require(isinstance(item, dict), f'逐条材料 {index} 格式无效')
        confirmed_review(item.get('review'), f"逐条 review {item.get('id', index)} ")
    si.require(package.get('schemaVersion') == 1 and package.get('experimentVersion') == VERSION, '实验版本不一致')
    si.require(package.get('scope') == SCOPES['confirmed'], '材料 scope 尚未确认')
    si.require(package.get('seed') == si.SEED and package.get('judges') == si.judge_config(), '模型、参数或种子不一致')
    si.require(package.get('prompts') == PROMPTS and package.get('promptSha256') == si.digest(si.SYSTEM_PROMPT)
               and package.get('promptSetSha256') == si.digest(PROMPTS), '包内提示词哈希不一致')
    si.require(package.get('analysisPlan') == METHODS and package.get('types') == TYPES, '分析口径或分组已变化')
    sources = (*si.SOURCE_FILES, f'{FORMAL}/package.json', f'{FORMAL}/records.json', f'{FORMAL}/prompt.txt')
    si.require(isinstance(package.get('sourceFiles'), dict) and set(package['sourceFiles']) == set(sources), '来源清单不完整')
    si.verify_sources(package['sourceFiles'], root)
    material = lambda rows: [{k: v for k, v in row.items() if k != 'review'} for row in rows]
    si.require(si.digest(material(items)) == si.digest(material(build_items(root))), '材料与 build_items 配方逐字段不一致')


def schedule(package):
    return [{'requestId': f"{item['id']}--{judge}--{prompt}", 'itemId': item['id'], 'judge': judge, 'prompt': prompt}
            for item in package['items'] for judge in package['judges'] for prompt in PROMPTS]


def validate_authorization(package, package_path, auth, output, root=ROOT):
    si.require(isinstance(auth, dict), '授权必须为对象')
    si.require(auth.get('status') == 'authorized' and auth.get('allowPaidCalls') is True
               and auth.get('acceptNoHardBillingCap') is True, '缺少付费授权及费用上限声明')
    si.require(all(isinstance(auth.get(k), str) and auth[k].strip() for k in ('authorizedBy', 'authorizedAt')),
               '缺少授权人或授权日期')
    iso_date(auth['authorizedAt'], '授权日期必须为 ISO 日期 YYYY-MM-DD')
    si.require(auth.get('packageSha256') == si.sha256(package_path), '授权材料哈希不一致')
    si.require(auth.get('configurationSha256') == si.digest(package['judges']), '授权配置哈希不一致')
    si.require(auth.get('promptSha256') == si.digest(si.SYSTEM_PROMPT)
               and auth.get('promptSetSha256') == si.digest(PROMPTS)
               and auth.get('promptHashes') == {k: si.digest(v) for k, v in PROMPTS.items()}, '授权提示词哈希不一致')
    for field, names in (('preparationToolFiles', PREPARATION_TOOLS), ('toolFiles', si.TOOL_FILES)):
        si.require(auth.get(field) == {name: si.sha256(root / name) for name in names}, f'授权 {field} 工具哈希不一致')
    si.require(type(auth.get('maxRequests')) is int and auth['maxRequests'] == 66 * 2 * 2
               and type(auth.get('maxRetries')) is int and auth['maxRetries'] == 0, '调用上限必须为264且不重试')
    si.require(type(auth.get('budgetCny')) in (int, float) and math.isfinite(auth['budgetCny'])
               and auth['budgetCny'] > 0, '预算必须为正数')
    si.require(isinstance(auth.get('outputDirectory'), str) and auth['outputDirectory'].strip(), '授权缺少输出目录')
    if output is not None:
        si.require((root / auth['outputDirectory']).resolve() == output.resolve(), '授权未绑定本次输出目录')
    prices, estimates = auth.get('prices'), auth.get('estimates')
    si.require(isinstance(prices, dict) and isinstance(estimates, dict)
               and isinstance(estimates.get('judges'), dict), '缺少授权单价或 estimates')
    for judge in package['judges']:
        price = prices.get(judge)
        si.require(isinstance(price, dict) and all(type(price.get(k)) in (int, float)
                   and math.isfinite(price[k]) and price[k] >= 0
                   for k in ('inputCnyPerMillion', 'outputCnyPerMillion')), '授权单价无效')
        si.require(isinstance(price.get('source'), str) and price['source'].strip(), '单价缺少来源')
        iso_date(price.get('checkedAt'), '单价核实日期无效')
        estimated = estimates['judges'].get(judge, {})
        si.require(isinstance(estimated, dict) and isinstance(estimated.get('byPrompt'), dict), 'estimates 缺少裁判/提示')
        for prompt in PROMPTS:
            row = estimated['byPrompt'].get(prompt)
            si.require(isinstance(row, dict) and type(row.get('requests')) is int and row['requests'] == 66
                       and all(type(row.get(k)) is int and row[k] > 0
                               for k in ('inputTokensEstimate', 'outputTokensEstimate')), 'estimates 缺少每请求规划上界')


def payload_for(package, item, job):
    cfg = package['judges'][job['judge']]
    return {'model': cfg['model'], 'messages': prompt_messages(item, job['prompt']), 'temperature': cfg['temperature'],
            'max_tokens': cfg['maxTokens'], 'stream': False}


def charge(record, auth, package):
    usage = record['usage']
    basis = 'response_usage'
    if usage is None:
        row = auth['estimates']['judges'][record['judge']]['byPrompt'][record['prompt']]
        usage = {'prompt_tokens': row['inputTokensEstimate'],
                 'completion_tokens': max(row['outputTokensEstimate'], package['judges'][record['judge']]['maxTokens'])}
        basis = 'authorized_estimates_upper_bound'
    price = auth['prices'][record['judge']]
    cost = (Decimal(usage['prompt_tokens']) * Decimal(str(price['inputCnyPerMillion']))
            + Decimal(usage['completion_tokens']) * Decimal(str(price['outputCnyPerMillion']))) / Decimal(1_000_000)
    return {'basis': basis, 'tokens': usage, 'costCny': str(cost)}


def run(package_path, authorization_path, output, *, root=ROOT, transport=None):
    package, auth = si.load(package_path), si.load(authorization_path)
    validate_package(package, root)
    validate_authorization(package, package_path, auth, output, root)
    empty_output(output)
    jobs = schedule(package)
    send = transport if transport is not None else si.make_transport(package)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package_path, output / 'package.json')
    shutil.copyfile(authorization_path, output / 'authorization.json')
    si.write_json(output / 'schedule.json', jobs)
    for name, prompt in PROMPTS.items():
        si.write_text(output / f'prompt.{name}.txt', prompt)
    for name in (*si.TOOL_FILES, *PREPARATION_TOOLS):
        target = output / 'sources' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    for name in package['sourceFiles']:
        target = output / 'inputs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    si.write_json(output / 'runtime.json', {'startedAt': si.utc(), 'python': sys.version,
                                           'parameters': package['judges'], 'costNote': COST_NOTE})
    items = {item['id']: item for item in package['items']}
    records, calls, cost, stop = [], 0, Decimal(0), 'completed'
    events = output / 'events.jsonl'
    try:
        for job in jobs:
            if calls >= auth['maxRequests']:
                stop = 'request_limit'
                break
            if cost >= Decimal(str(auth['budgetCny'])):
                stop = 'budget_limit'
                break
            item = items[job['itemId']]
            payload = payload_for(package, item, job)
            si.append_event(events, {'event': 'start', **job, 'at': si.utc(), 'request': payload})
            calls += 1
            record = {**job, 'status': 'call_error', 'result': None, 'actualModel': None, 'usage': None, 'error': None}
            response = None
            try:
                response = send(job['judge'], payload)
                record.update(si.response_fields(response, item['contexts']))
                if record['status'] != 'ok':
                    record['error'] = {'type': record['status'], 'note': '调用/解析失败，保留原响应，不重试。'}
            except Exception as error:
                record['error'] = {'type': type(error).__name__, 'note': '异常消息可能包含凭据，不归档；不重试。'}
            record['charge'] = charge(record, auth, package)
            cost += Decimal(record['charge']['costCny'])
            si.append_event(events, {'event': 'end' if record['status'] == 'ok' else 'failure', **record,
                                     'at': si.utc(), 'response': response})
            records.append(record)
    except BaseException:
        stop = 'interrupted'
        raise
    finally:
        si.append_event(events, {'event': 'stop', 'reason': stop, 'attempts': calls,
                                 'estimatedCostCny': str(cost), 'at': si.utc()})
        manifest = {'schemaVersion': 1, 'experimentVersion': VERSION, 'finishedAt': si.utc(), 'stopReason': stop,
                    'attempts': calls, 'completedRecords': len(records), 'plannedRequests': len(jobs),
                    'estimatedCostCny': str(cost), 'actualBillCny': None, 'costNote': COST_NOTE}
        si.write_json(output / 'manifest.json', manifest)
        si.write_json(output / 'records.json', records)
        si.checksums(output)
    return manifest


def read_records(directory, package, auth):
    jobs = schedule(package)
    items = {item['id']: item for item in package['items']}
    starts, terminal, records, stopped = [], [], [], None
    current = None
    cost = Decimal(0)
    for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        si.require(stopped is None, '停止后出现额外事件')
        kind = event.get('event')
        if kind == 'stop':
            si.require(event.get('attempts') == len(starts) and event.get('estimatedCostCny') == str(cost), '停止事件计数/费用不符')
            stopped = event
            continue
        si.require(kind in ('start', 'end', 'failure'), '未知请求事件')
        if kind == 'start':
            si.require(current is None and len(starts) < min(len(jobs), auth['maxRequests'])
                       and cost < Decimal(str(auth['budgetCny'])), '请求越过硬上限或前次请求未结束')
            job = jobs[len(starts)]
            si.require(all(event.get(k) == v for k, v in job.items()), '请求配对/固定顺序不一致')
            si.require(event.get('request') == payload_for(package, items[job['itemId']], job), '请求泄露标签或材料不符')
            starts.append(job)
            current = job
            continue
        si.require(current is not None and all(event.get(k) == v for k, v in current.items()), '缺少开始事件或结束配对不符')
        record = {k: v for k, v in event.items() if k not in ('event', 'at', 'response')}
        fields = si.response_fields(event.get('response'), items[current['itemId']]['contexts'])
        si.require(all(record.get(k) == v for k, v in fields.items()), '判定/用量与原始响应不符')
        si.require((kind == 'end') == (record['status'] == 'ok'), '失败事件类型不符')
        si.require(record.get('charge') == charge(record, auth, package), '请求计费与授权口径不符')
        cost += Decimal(record['charge']['costCny'])
        records.append(record)
        terminal.append(record)
        current = None
    si.require(stopped is not None, '缺少停止事件')
    if current is not None:
        si.require(stopped.get('reason') == 'interrupted', '请求未结束但未标记中断')
        records.append({**current, 'status': 'incomplete', 'result': None, 'actualModel': None,
                        'usage': None, 'error': {'type': 'interrupted'}})
    manifest = si.load(directory / 'manifest.json')
    reason = stopped.get('reason')
    si.require(reason in ('request_limit', 'budget_limit', 'completed', 'interrupted'), '未知停止原因')
    if reason != 'interrupted':
        expected = ('completed' if len(starts) == len(jobs) else
                    'request_limit' if len(starts) >= auth['maxRequests'] else
                    'budget_limit' if cost >= Decimal(str(auth['budgetCny'])) else 'completed')
        si.require(reason == expected and (reason != 'completed' or len(starts) == len(jobs)), '停止原因与实际请求不符')
    si.require(manifest.get('stopReason') == reason and manifest.get('attempts') == len(starts)
               and manifest.get('completedRecords') == len(terminal) and manifest.get('plannedRequests') == len(jobs)
               and manifest.get('estimatedCostCny') == str(cost), 'manifest 与事件不符')
    si.require(si.load(directory / 'records.json') == terminal, 'records 与原始事件不符')
    return records


def summarize(package, rules, records):
    verdicts = {(r['itemId'], r['judge'], r['prompt']): r['result']['verdict']
                for r in records if r['status'] == 'ok'}
    # 规则必须有 error 级 finding；warning 或仅 flagged 不能充当检出。
    flagged = {i['id'] for i in package['items']
               if any(f['severity'] == 'error' for f in rules['items'][i['id']]['findings'])}
    by_type = {}
    groups = {kind: [i for i in package['items'] if i['type'] == kind] for kind in TYPES}
    groups.update(paragraph_errors=[i for i in package['items'] if i['type'] in ERROR_TYPES[:2]],
                  item_errors=[i for i in package['items'] if i['type'] in ERROR_TYPES[2:]],
                  all_errors=[i for i in package['items'] if i['expected'] == 'not_supported'],
                  support_controls=[i for i in package['items'] if i['expected'] == 'supported'])
    for kind, members in groups.items():
        row = {'items': len(members), 'rulesFlagged': sum(i['id'] in flagged for i in members), 'judges': {}}
        for judge in package['judges']:
            row['judges'][judge] = {}
            for prompt in PROMPTS:
                values = [verdicts.get((i['id'], judge, prompt)) for i in members]
                valid = sum(v is not None for v in values)
                detected = sum(v in si.NOT_SUPPORTED for v in values)
                row['judges'][judge][prompt] = {'valid': valid, 'notSupported': detected, 'unavailable': len(values) - valid,
                                               'detectedErrors': sum(i['expected'] == 'not_supported' and v in si.NOT_SUPPORTED
                                                                     for i, v in zip(members, values)),
                                               'falsePositives': sum(i['expected'] == 'supported' and v in si.NOT_SUPPORTED
                                                                     for i, v in zip(members, values))}
        union = [i for i in members if i['id'] in flagged or any(
            verdicts.get((i['id'], j, p)) in si.NOT_SUPPORTED for j in package['judges'] for p in PROMPTS)]
        row['union'] = {'flagged': len(union), 'detectedErrors': sum(i['expected'] == 'not_supported' for i in union),
                        'falsePositives': sum(i['expected'] == 'supported' for i in union), 'ids': [i['id'] for i in union]}
        by_type[kind] = row
    paired = {}
    for judge in package['judges']:
        row = {k: [] for k in ('newDetections', 'lostDetections', 'newFalsePositives', 'reducedFalsePositives', 'unavailable')}
        for item in package['items']:
            before, after = [verdicts.get((item['id'], judge, p)) for p in PROMPTS]
            if before is None or after is None:
                row['unavailable'].append(item['id'])
            elif (before in si.NOT_SUPPORTED) != (after in si.NOT_SUPPORTED):
                key = (('newDetections' if after in si.NOT_SUPPORTED else 'lostDetections')
                       if item['expected'] == 'not_supported' else
                       ('newFalsePositives' if after in si.NOT_SUPPORTED else 'reducedFalsePositives'))
                row[key].append(item['id'])
        paired[judge] = {k: {'count': len(ids), 'ids': ids} for k, ids in row.items()}
    present = {(r['itemId'], r['judge'], r['prompt']) for r in records}
    failures = [r for r in records if r['status'] != 'ok']
    not_attempted = [j for j in schedule(package) if (j['itemId'], j['judge'], j['prompt']) not in present]
    return {'byType': by_type, 'paired': paired, 'failures': failures, 'notAttempted': not_attempted,
            'records': {'total': len(records), 'ok': sum(r['status'] == 'ok' for r in records),
                        'failed': len(failures), 'notAttempted': len(not_attempted)}}


def analyze(directory, output):
    empty_output(output)
    si.verify_checksums(directory)
    package, auth = si.load(directory / 'package.json'), si.load(directory / 'authorization.json')
    validate_package(package, directory / 'inputs')
    # 离线回放只读归档授权与源码，不要求旧授权绑定当前运行器。
    validate_authorization(package, directory / 'package.json', auth, None, directory / 'sources')
    si.require(si.load(directory / 'schedule.json') == schedule(package), '归档调度不符')
    for prompt, text in PROMPTS.items():
        si.require((directory / f'prompt.{prompt}.txt').read_bytes() == text.encode('utf-8'), '归档提示词不符')
    si.require(si.sha256(ROOT / 'backend/core/statute_check.py') == auth['toolFiles']['backend/core/statute_check.py'],
               '当前规则检查器与授权版本不符')
    records = read_records(directory, package, auth)
    rules = si.rule_results(package, directory / 'inputs')
    rules['experimentVersion'] = VERSION
    summary = {'schemaVersion': 1, 'experimentVersion': VERSION, 'analysisPlan': package['analysisPlan'],
               **summarize(package, rules, records)}
    sensitivity = deepcopy(package)
    sensitivity['items'] = [i for i in package['items'] if i['base'] != 'S26']
    kept = {i['id'] for i in sensitivity['items']}
    summary['withoutS26'] = summarize(sensitivity, rules, [r for r in records if r['itemId'] in kept])
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / 'analysis.json', summary)
    si.write_json(output / 'rules.json', rules)
    si.checksums(output)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare', help='离线生成材料、规则结果及待确认授权草稿')
    prep.add_argument('--output', required=True, type=Path)
    confirmation = sub.add_parser('confirm', help='离线登记作者确认，重新绑定未授权草稿')
    confirmation.add_argument('--package', required=True, type=Path)
    confirmation.add_argument('--reviewer', required=True)
    confirmation.add_argument('--date', required=True)
    confirmation.add_argument('--note-file', required=True, type=Path)
    confirmation.add_argument('--output', required=True, type=Path)
    execute = sub.add_parser('run', help='作者确认及独立授权后执行双提示配对；不重试')
    execute.add_argument('--package', required=True, type=Path)
    execute.add_argument('--authorization', required=True, type=Path)
    execute.add_argument('--output', required=True, type=Path)
    analysis = sub.add_parser('analyze', help='校验运行归档并离线复现条数分析')
    analysis.add_argument('--run', required=True, type=Path)
    analysis.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'run':
        print(json.dumps(run(args.package, args.authorization, args.output), ensure_ascii=False))
        return
    if args.command == 'analyze':
        print(json.dumps(analyze(args.run, args.output), ensure_ascii=False))
        return
    result = (prepare(args.output) if args.command == 'prepare' else
              confirm(args.package, args.reviewer, args.date, args.note_file, args.output))
    counts = Counter(i['type'] for i in result['package']['items'])
    print(f"{args.command} 完成：{len(result['package']['items'])}条；{dict(counts)}；{result['authorization']['maxRequests']}次拟定请求。未调用模型、未授权付费。")


if __name__ == '__main__':
    main()
