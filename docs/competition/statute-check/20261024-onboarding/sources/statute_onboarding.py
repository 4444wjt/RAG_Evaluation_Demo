"""新法规接入：按登记规则出题、授权运行与离线回放（四臂，同复现）。

出题规则、节选规则和判定门槛按 work/statute-injection/onboarding-20261023/登记.md（已锁定）。
条数、请求数和门槛都由节选按规则推出，不写死。题包由同目录的 make_package.py 生成；
本工具在 template、run、analyze 前都按同一套函数从节选重建全部陈述、请求、标签与规则参照并逐项核对。
用法：python -I tools/competition/statute_onboarding.py {template,run,analyze} ...
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import importlib.util
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

from backend.core import statute_check as sc  # noqa: E402
from tools.competition import statute_injection as si  # noqa: E402
from tools.competition import statute_labeled as sl  # noqa: E402

VERSION = 'statute-onboarding-v1'
REGISTRATION = 'work/statute-injection/onboarding-20261023/登记.md'
REGISTRATION_SHA256 = '9d4dbb6665ff67f54187c3804989ea0b472092357de9e4ac22bca2eb9564ca5b'
CANDIDATES = 'work/statute-injection/onboarding-20261023/候选法规清单.md'
CANDIDATES_SHA256 = '40d7ef61df0244721899abc93ce1c0f66f126c84a7be0c45bb020c71ce4101a3'
CRITERION_REGISTRATION = 'work/statute-injection/criterion-20261023/登记.md'
CRITERION_REGISTRATION_SHA256 = 'c9459f21c5a9fc7f1d3e9a4e627e4b11619ce36f93c38054ff53bf21a2506daf'
CRITERION_MAKER = 'work/statute-injection/criterion-20261023/make_package.py'
CRITERION = ('陈述写明的条、款、项属于陈述内容：陈述内容须出自其所引的那一条（款、项）才判 supported；'
             '内容虽见于上下文，但出自其他条款，或所引条款在上下文中不存在的，判 contradicted。')
PROMPTS = {'C0': si.SYSTEM_PROMPT, 'C1': si.SYSTEM_PROMPT + '\n' + CRITERION}
# 同复现题包 work/statute-injection/replication-20261022/package/package.json 的裁判配置；make_package 生成时核对。
JUDGE = {'model': 'deepseek-flash', 'baseUrl': 'https://api.deepseek.com', 'baseUrlEnv': 'DEEPSEEK_BASE_URL',
         'apiKeyEnv': 'OPENAI_API_KEY', 'temperature': 0, 'maxTokens': 768, 'timeoutSeconds': 90,
         'maxRetries': 0, 'thinking': {'type': 'disabled'}}
JUDGE_SOURCE = 'work/statute-injection/replication-20261022/package/package.json'
# 费用估算的参照：复现运行的实计费（按 usage 估算）与当时核实的单价。
REFERENCE_RUN = 'work/statute-injection/replication-20261022/run'
MAKER = 'work/statute-injection/onboarding-20261023/make_package.py'
# 字面基线沿用阶段 3 的词面最近邻（docs/competition/statute-check/20261023-stage3-baselines 里的 X 臂），哈希同该归档。
LEXICAL = 'work/statute-injection/lexical-baseline-20261022/lexical_baseline.py'
LEXICAL_SHA256 = 'cd6233558a95a91aab3b20c29ed2632dcfa42018d7e0c7110dbd390e22465351'
TOOL_FILES = (*si.TOOL_FILES, 'tools/competition/statute_labeled.py', 'tools/competition/statute_onboarding.py',
              'tests/test_statute_onboarding.py', MAKER, LEXICAL)
PACKAGE_FILES = ('package.json', 'requests.jsonl', 'estimates.json', 'rules.json',
                 'contexts/plain.txt', 'contexts/labeled.txt')
ARMS = ('U-C0', 'L-C0', 'U-C1', 'L-C1')
TYPES = ('paragraph_existing', 'paragraph_absent', 'control_pinpoint', 'control_article')
ERROR_TYPES = ('paragraph_existing', 'paragraph_absent')
MIN_MULTI_PARAGRAPH_ARTICLES = 6
MIN_ERROR_STATEMENTS = 24
MAX_EXCERPT_BYTES = 9000
ENFORCE_EXCERPT_RULE = True  # 只有离线测试会关掉；命令行始终要求节选规则全部满足。
BUDGET_CAP_CNY = 5.0
THROTTLE_SECONDS = 1.0
STOP_REASONS = ('completed', 'request_limit', 'estimated_budget_guard', 'actual_model_unknown',
                'reservation_exceeded', 'interrupted')
COST_NOTE = '费用为授权单价估算，不是账单。无 usage（含失败）按本次预留计；不重试。'
TITLE = re.compile(r'^(.+?)（[^（）]*节选）$')
RULES = {
    'excerpt': (f'从第二章第一条起（不分章的从第一条起）按条序连续取整条，取到多款条 ≥{MIN_MULTI_PARAGRAPH_ARTICLES} 个'
                f'且款级错误陈述 ≥{MIN_ERROR_STATEMENTS} 条为止；不取附则，不够时从第一条起往后补取；'
                f'不超过 {MAX_EXCERPT_BYTES} 字节。作者按官方文本填第二章第一条的条号（不分章的填 1）。本工具只能核对：'
                '条号升序；主段从该条起连续，补取段从第一条起连续；去掉最后取的一条（有补取段时为补取段末条）就不满足条数要求；'
                '每个非空行都能解析。该条号填得对不对、是否进了附则，由作者对照官方文本负责。'),
    'format': '首行「法规全称（版本，节选）」，第二行空行；第X条后接全角空格，第一款同行；其余各款各占一行；项另起一行；条间空行。',
    'statement': '「按照<所引>规定，<内容>」。内容取该款原文第一句（到第一个「。」为止，没有句号则取整款）；'
                 '该款以「：」结尾并带项时，取该款加其全部项原文直接相连。逐字取原文。',
    'question': '所有陈述用同一个问题：「《<法规全称>》节选对此有什么规定？」。',
    'paragraph_existing': '每个多款条的每一款 j，引为同条下一款（最后一款引回第一款），内容取第 j 款；'
                          '内容若在所引那一款里也逐字出现，不算错误，剔除并列入 excluded。',
    'paragraph_absent': '节选中每一条，引为「该条款数 + 1」款，内容取第一款。',
    'control_pinpoint': '每一条的每一款（含单款条的第一款），引用正确的条和款，内容取该款。',
    'control_article': '每一条只引条号；多款条内容取最后一款，单款条取第一款。',
    'order': '陈述按条序，同一条内按 paragraph_existing、paragraph_absent、control_pinpoint、control_article。',
    'labels': sl.LABEL_RULE,
    'schedule': '按陈述顺序，每条陈述的四臂连续发送，臂的起点按陈述序号轮换；每个请求只发一次，不重试。',
    'baselines': '规则（statute_check.check_answer，上下文为整份原节选，问题留空，有 error 即检出）与字面基线'
                 '（lexical_baseline.baseline，所引单元覆盖率低于同级其他单元即检出）在同一批陈述上离线运行，只作参照。',
    'constructionErrors': '运行后发现的构造错误照记并列出 ID；主判定同时报剔除前和剔除后，门槛沿用题包按全部陈述推出的值，不改。',
}


def normalized(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


# ---- 出题函数：structure、content、label、strip_labels 复制自 blindspot make_exam.py（content 补了无句号的情形），不导入该文件 ----

def structure(text):
    """返回条序、各款原文行、各款的项原文行（行内容去掉换行符）。"""
    paragraphs, items, order = {}, {}, []
    for row in sl.provision_lines(text):
        loc = row['location']
        if not loc:
            continue
        article, paragraph, item = loc
        raw = row['raw'].rstrip('\r\n')
        if item is None:
            if row['header']:
                order.append(article)
                raw = raw[sc.HEADER.match(sc.normalize(raw)).start(2):]
            paragraphs[(article, paragraph)] = raw
        else:
            items.setdefault((article, paragraph), []).append(raw)
    counts = {a: sum(1 for (x, _) in paragraphs if x == a) for a in order}
    return order, counts, paragraphs, items


def content(unit, paragraphs, items):
    body = paragraphs[unit]
    if body.endswith('：') and unit in items:
        return body + ''.join(items[unit])
    end = body.find('。')
    # 登记：取该款第一句；该款没有句号时，整款就是第一句。
    return body if end < 0 else body[:end + 1]


def unit_text(unit, paragraphs, items):
    return paragraphs[unit] + ''.join(items.get(unit, []))


def label(article, paragraph=None):
    return f'第{sc.cn_label(article)}条' + (f'第{sc.cn_label(paragraph)}款' if paragraph else '')


def strip_labels(labeled, insertions):
    for ins in sorted(insertions, key=lambda i: i['offset'], reverse=True):
        si.require(labeled[ins['offset']:ins['offset'] + len(ins['text'])] == ins['text'], '插入位置不符')
        labeled = labeled[:ins['offset']] + labeled[ins['offset'] + len(ins['text']):]
    return labeled

# ---- 复制结束 ----


def title_of(text):
    lines = text.split('\n')
    match = TITLE.match(lines[0])
    si.require(match is not None and len(lines) > 1 and lines[1] == '',
               '首行须为「法规全称（版本，节选）」，第二行须为空行')
    return match.group(1)


def kept_existing(article, counts, paragraphs, items):
    """该条 paragraph_existing 中不被剔除的 (j, k)。"""
    n = counts[article]
    if n < 2:
        return []
    pairs = [(j, j % n + 1) for j in range(1, n + 1)]
    return [(j, k) for j, k in pairs
            if content((article, j), paragraphs, items) not in unit_text((article, k), paragraphs, items)]


def excerpt_report(text, chapter_two_first):
    """按登记核对节选规则中能机械核对的部分；不抛出规则不满足，只报告。

    chapter_two_first 是官方文本第二章第一条的条号（不分章的为 1），由作者对照官方文本填写。
    条号不小于它的是主段，须从它起连续；小于它的是补取段，须从第一条起连续。最后取的一条：有补取段时是补取段末条，否则是主段末条。
    """
    si.require(type(chapter_two_first) is int and chapter_two_first >= 1, '第二章第一条的条号须为正整数')
    raw = text.encode('utf-8')
    lines = text.split('\n')
    rows = sl.provision_lines(text)
    order, counts, paragraphs, items = structure(text)
    numbers = {}
    for row in rows:
        loc = row['location']
        if loc and loc[2] is not None:
            numbers.setdefault(loc[:2], []).append(loc[2])
    main = [a for a in order if a >= chapter_two_first]
    backfill = [a for a in order if a < chapter_two_first]
    errors = {a: len(kept_existing(a, counts, paragraphs, items)) + 1 for a in order}
    multi = sum(counts[a] > 1 for a in order)
    total = sum(errors.values())
    last = backfill[-1] if backfill else (main[-1] if main else None)
    without = ({'multiParagraphArticles': multi - (counts[last] > 1), 'errorStatements': total - errors[last]}
               if last is not None else {'multiParagraphArticles': 0, 'errorStatements': 0})
    checks = {
        'noCR': '\r' not in text,
        'titleLine': TITLE.match(lines[0]) is not None and len(lines) > 1 and lines[1] == '',
        'allLinesParsed': all(r['location'] is not None for r in rows[1:] if r['raw'].strip()),
        'itemsContiguous': all(v == list(range(1, len(v) + 1)) for v in numbers.values()),
        'articlesAscending': all(x < y for x, y in zip(order, order[1:])),
        'mainFromChapterTwo': bool(main) and main == list(range(chapter_two_first, chapter_two_first + len(main))),
        'backfillFromFirst': backfill == list(range(1, len(backfill) + 1)),
        'minMultiParagraphArticles': multi >= MIN_MULTI_PARAGRAPH_ARTICLES,
        'minErrorStatements': total >= MIN_ERROR_STATEMENTS,
        'maxBytes': len(raw) <= MAX_EXCERPT_BYTES,
        'minimal': not (without['multiParagraphArticles'] >= MIN_MULTI_PARAGRAPH_ARTICLES and
                        without['errorStatements'] >= MIN_ERROR_STATEMENTS),
    }
    return {'chapterTwoFirstArticle': chapter_two_first, 'bytes': len(raw), 'articles': order,
            'paragraphsPerArticle': {str(a): counts[a] for a in order}, 'mainArticles': main,
            'backfillArticles': backfill, 'multiParagraphArticles': multi, 'errorStatements': total,
            'controlStatements': sum(counts.values()) + len(order), 'lastTaken': last,
            'withoutLastTaken': without, 'checks': checks, 'passed': all(checks.values())}


def build_items(text):
    title = title_of(text)
    question = f'《{title}》节选对此有什么规定？'
    order, counts, paragraphs, items = structure(text)
    si.require(bool(order), '节选里没有可解析的条')
    rows, excluded = [], []

    def add(kind, cited, unit, suffix):
        rows.append({'id': f'{kind}-{suffix}', 'type': kind,
                     'expected': 'not_supported' if kind in ERROR_TYPES else 'supported',
                     'cited': label(*cited), 'contentFrom': label(*unit),
                     'citedArticle': cited[0], 'citedParagraph': cited[1] if len(cited) > 1 else None,
                     'citedArticleParagraphs': counts[cited[0]], 'question': question,
                     'statement': f'按照{label(*cited)}规定，{content(unit, paragraphs, items)}'})

    for a in order:
        n = counts[a]
        kept = kept_existing(a, counts, paragraphs, items)
        if n > 1:
            for j in range(1, n + 1):
                k = j % n + 1
                if (j, k) in kept:
                    add('paragraph_existing', (a, k), (a, j), f'A{a}P{j}toP{k}')
                else:
                    excluded.append({'id': f'paragraph_existing-A{a}P{j}toP{k}',
                                     'reason': f'{label(a, j)}的内容在所引{label(a, k)}里也逐字出现，不算错误。'})
        add('paragraph_absent', (a, n + 1), (a, 1), f'A{a}toP{n + 1}')
        for j in range(1, n + 1):
            add('control_pinpoint', (a, j), (a, j), f'A{a}P{j}')
        add('control_article', (a,), (a, n), f'A{a}')
    check_items(rows, text)
    return rows, excluded


def check_items(rows, text):
    units = {(p.article, p.paragraph, p.item) for p in sc.parse_provisions(text)}
    lines = set(text.split('\n'))
    for row in rows:
        match = sc.REF.search(sc.normalize(row['statement']))
        si.require(match is not None and sc._ref_label(*sc._ref_value(match)) == row['cited'], f"{row['id']} 引用解析不符")
        article, paragraph, item = sc._ref_value(match)
        si.require(item is None and article == row['citedArticle'] and paragraph == row['citedParagraph'],
                   f"{row['id']} 引用位置不符")
        exists = (article, paragraph or 1, None) in units
        si.require(exists == (row['type'] != 'paragraph_absent'), f"{row['id']} 存在性不符")
        si.require((row['cited'] == row['contentFrom']) == (row['type'] == 'control_pinpoint'),
                   f"{row['id']} 引用与内容来源关系不符")
        body = row['statement'].split('规定，', 1)[1]
        pieces = [p for p in re.split(r'(?=（[一二三四五六七八九十]+）)', body) if p]
        si.require(all(any(p in line for line in lines) for p in pieces), f"{row['id']} 内容不是逐字原文")
    si.require(len({r['id'] for r in rows}) == len(rows), '陈述 id 重复')
    si.require(len({r['statement'] for r in rows}) == len(rows), '陈述文本重复')


def build_requests(rows, contexts):
    requests = []
    for n, row in enumerate(rows):
        for arm in ARMS[n % 4:] + ARMS[:n % 4]:
            context, prompt = arm.split('-')
            messages = si.prompt_messages(row, {'contexts': [contexts[context]]})
            messages[0]['content'] = PROMPTS[prompt]
            payload = {'model': JUDGE['model'], 'messages': messages, 'temperature': JUDGE['temperature'],
                       'max_tokens': JUDGE['maxTokens'], 'stream': False, 'thinking': dict(JUDGE['thinking'])}
            requests.append({'requestId': f"{row['id']}--{arm}--deepseek", 'itemId': row['id'], 'arm': arm,
                             'context': context, 'prompt': prompt, 'judge': 'deepseek', 'payload': payload})
    return requests


def totals_of(rows):
    errors = sum(r['expected'] == 'not_supported' for r in rows)
    return {'errors': errors, 'controls': len(rows) - errors}


def thresholds_of(totals):
    """复现在 E = K = 31 时的 28/31、3/31、+3，按比例换算：⌈0.9E⌉、⌊0.1K⌋、⌊K/9⌋。"""
    errors, controls = totals['errors'], totals['controls']
    return {'simpleSufficientMinDetected': -(-9 * errors // 10), 'simpleSufficientMaxFalsePositives': controls // 10,
            'labelGainMinDifference': 5, 'labelGainMaxP': '1/20', 'labelGainFalsePositiveTolerance': controls // 9}


def derive(text, chapter_two_first):
    """从节选（和作者填的第二章第一条条号）按登记规则推出题包的全部内容。"""
    si.require('\r' not in text, '节选含 CR')
    report = excerpt_report(text, chapter_two_first)
    rows, excluded = build_items(text)
    labeled, insertions = sl.label_source(text)
    si.require(strip_labels(labeled, insertions) == text, '补标签节选去掉标签后与原文不一致')
    si.require(len(insertions) == sum(1 for p in sc.parse_provisions(text) if not p.item), '标签数与款数不符')
    requests = build_requests(rows, {'U': text, 'L': labeled})
    return {'report': report, 'rows': rows, 'excluded': excluded, 'labeled': labeled, 'insertions': insertions,
            'requests': requests, 'rules': baselines_of(rows, text)}


def lexical_module():
    si.require(si.sha256(ROOT / LEXICAL) == LEXICAL_SHA256, '字面基线源码与阶段 3 归档不一致')
    spec = importlib.util.spec_from_file_location('onboarding_lexical', ROOT / LEXICAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baselines_of(rows, text):
    lexical, provisions = lexical_module(), sc.parse_provisions(text)
    items = {}
    for r in rows:
        rule = sc.check_answer(r['statement'], [text], text)
        lex = lexical.baseline(r['statement'], provisions)
        items[r['id']] = {
            'rule': {'flagged': any(f['severity'] == 'error' for f in rule['findings']),
                     'errorCodes': sorted({f['code'] for f in rule['findings'] if f['severity'] == 'error'})},
            'lexical': {k: lex[k] for k in ('flagged', 'cited', 'exists', 'best', 'margin')}}
    return {'note': '规则与字面基线只作参照，不是真值，不作优越性论据；真值来自构造。', 'items': items}


def request_lines(requests):
    return ''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in requests)


def estimate(requests):
    """期望：按复现运行实计费的每字节均价折算；上限：每请求 (消息字节 + 256) 输入 + maxTokens 输出，按复现授权单价。"""
    reference = ROOT / REFERENCE_RUN
    si.verify_checksums(reference)
    starts, cost, size = {}, Decimal(0), 0
    for line in (reference / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event['event'] == 'start':
            starts[event['requestId']] = event
        elif event['event'] in ('end', 'failure'):
            size += message_bytes(starts.pop(event['requestId'])['request']['messages'])
            cost += Decimal(event['charge']['costCny'])
    price = si.load(reference / 'authorization.json')['prices']['deepseek']
    new = [message_bytes(r['payload']['messages']) for r in requests]
    upper = sum((token_cost({'prompt_tokens': b + 256, 'completion_tokens': r['payload']['max_tokens']}, price)
                 for b, r in zip(new, requests)), Decimal(0))
    return normalized({
        'requests': len(requests), 'messageBytes': sum(new),
        'expectedCostCny': str((cost * sum(new) / size).quantize(Decimal('0.0001'))),
        'reservationUpperCny': str(upper.quantize(Decimal('0.0001'))),
        'reference': {'run': REFERENCE_RUN, 'costCny': str(cost), 'messageBytes': size,
                      'eventsSha256': si.sha256(reference / 'events.jsonl')},
        'price': price,
        'method': '期望 = 复现运行按 usage 估算的费用 × 新/旧消息总字节；上限 = 每请求 (消息字节 + 256) 输入 + maxTokens 输出，'
                  '按复现授权时核实的单价。运行当天须重新核价；都是估算，不是账单。'})


def expected_package(source, text, derived, requests_sha256):
    rows, totals = derived['rows'], totals_of(derived['rows'])
    return normalized({
        'schemaVersion': 1, 'experimentVersion': VERSION,
        'registration': {'path': REGISTRATION, 'sha256': REGISTRATION_SHA256},
        'candidateList': {'path': CANDIDATES, 'sha256': CANDIDATES_SHA256},
        'criterionRegistration': {'path': CRITERION_REGISTRATION, 'sha256': CRITERION_REGISTRATION_SHA256},
        'source': {'path': source, 'sha256': sha256_text(text), 'bytes': len(text.encode('utf-8'))},
        'statute': {'title': title_of(text), 'question': rows[0]['question']},
        'excerptRule': derived['report'],
        'contexts': {'U': {'file': 'contexts/plain.txt', 'sha256': sha256_text(text), 'note': '原节选'},
                     'L': {'file': 'contexts/labeled.txt', 'sha256': sha256_text(derived['labeled']),
                           'note': '每款开头补「第X条第Y款」', 'insertions': derived['insertions']}},
        'arms': {arm: dict(zip(('context', 'prompt'), arm.split('-'))) for arm in ARMS},
        'prompts': {'C0': {'sha256': si.digest(PROMPTS['C0'])},
                    'C1': {'sha256': si.digest(PROMPTS['C1']), 'criterion': CRITERION}},
        'judges': {'deepseek': JUDGE}, 'judgeSource': JUDGE_SOURCE,
        'rules': RULES, 'items': rows, 'excluded': derived['excluded'],
        'counts': {t: sum(r['type'] == t for r in rows) for t in TYPES},
        'totals': totals, 'thresholds': thresholds_of(totals),
        'requestsSha256': requests_sha256, 'maxRequests': len(derived['requests']), 'maxRetries': 0,
        'detection': '裁判 verdict 为 contradicted 或 insufficient 记为检出；supported 记为未检出；失败或解析失败单列为不可用。',
        'scope': '一部法规、自动构造、单裁判单次、作者本人操作；不参与 Quality Gate；只报条数。',
    })


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def code_hashes(root=ROOT):
    return {'makePackage': si.sha256(root / MAKER),
            'statuteOnboarding': si.sha256(root / 'tools/competition/statute_onboarding.py'),
            'statuteLabeled': si.sha256(root / 'tools/competition/statute_labeled.py'),
            'statuteCheck': si.sha256(root / 'backend/core/statute_check.py'),
            'statuteInjection': si.sha256(root / 'tools/competition/statute_injection.py'),
            'lexicalBaseline': si.sha256(root / LEXICAL)}


def criterion_constant():
    """四臂题包脚本里的 CRITERION 常量；只解析字面量，不执行该脚本。"""
    tree = ast.parse((ROOT / CRITERION_MAKER).read_text(encoding='utf-8'))
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and
              [getattr(t, 'id', None) for t in node.targets] == ['CRITERION']]
    si.require(len(values) == 1, '四臂题包脚本里找不到唯一的 CRITERION 常量')
    return values[0]


def check_registrations():
    for path, digest in ((REGISTRATION, REGISTRATION_SHA256), (CANDIDATES, CANDIDATES_SHA256),
                         (CRITERION_REGISTRATION, CRITERION_REGISTRATION_SHA256)):
        si.require(si.sha256(ROOT / path) == digest, f'{path} 哈希不符：登记或清单已被改动')
    si.require(criterion_constant() == CRITERION, '判据句与四臂题包脚本的 CRITERION 常量不一致')
    si.require(CRITERION in (ROOT / CRITERION_REGISTRATION).read_text(encoding='utf-8'), '判据句与四臂登记不一致')
    si.require(CRITERION in (ROOT / REGISTRATION).read_text(encoding='utf-8'), '判据句与本登记不一致')


def source_path(path):
    path = Path(path).resolve()
    si.require(path.is_relative_to(ROOT.resolve()) and path.is_file(), '节选必须是仓库内的文件')
    return path.relative_to(ROOT.resolve()).as_posix()


def load_requests(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def validate_package(directory):
    directory = Path(directory)
    si.verify_checksums(directory)
    files = {p.relative_to(directory).as_posix() for p in directory.rglob('*')
             if p.is_file() and p.name != 'SHA256SUMS.txt'}
    si.require(files == set(PACKAGE_FILES), '题包文件集合不符')
    check_registrations()
    package = si.load(directory / 'package.json')
    si.require(package.get('experimentVersion') == VERSION, '题包版本不符')
    source = package['source']['path']
    si.require(isinstance(source, str) and source_path(ROOT / source) == source, '源材料路径不符')
    plain = directory / 'contexts/plain.txt'
    si.require(si.sha256(ROOT / source) == package['source']['sha256'] == si.sha256(plain), '源材料哈希不符')
    text = plain.read_bytes().decode('utf-8')
    rule = package.get('excerptRule')
    derived = derive(text, rule.get('chapterTwoFirstArticle') if isinstance(rule, dict) else None)
    if ENFORCE_EXCERPT_RULE:
        si.require(derived['report']['passed'], '节选不满足登记的节选规则')
    expected = expected_package(source, text, derived, si.sha256(directory / 'requests.jsonl'))
    si.require({k: v for k, v in package.items() if k != 'code'} == expected, '题包内容与按规则重建的结果不符')
    si.require(package.get('code') == code_hashes(), '题包生成时的代码与当前不符')
    si.require((directory / 'contexts/labeled.txt').read_bytes() == derived['labeled'].encode('utf-8'), '补标签节选与重建不符')
    si.require((directory / 'requests.jsonl').read_bytes() == request_lines(derived['requests']).encode('utf-8'),
               '请求与重建不符')
    requests = load_requests(directory / 'requests.jsonl')
    si.require(len({r['requestId'] for r in requests}) == len(requests) == package['maxRequests'], '请求数量或 id 不符')
    si.require(si.load(directory / 'rules.json') == normalized(derived['rules']), '规则参照与重建不符')
    si.require(si.load(directory / 'estimates.json') == estimate(requests), '费用估算与重建不符')
    return package, requests, {'U': [text], 'L': [derived['labeled']]}


def write_package(source, out, chapter_two_first):
    """make_package.py 的唯一写入口：按规则推出题包并写入新的空目录。"""
    out = Path(out)
    empty_output(out)
    check_registrations()
    source = source_path(source)
    text = (ROOT / source).read_bytes().decode('utf-8')
    derived = derive(text, chapter_two_first)
    if ENFORCE_EXCERPT_RULE:
        failed = [k for k, v in derived['report']['checks'].items() if not v]
        si.require(not failed, f'节选不满足登记的节选规则：{failed}')
    (out / 'contexts').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / source, out / 'contexts/plain.txt')
    si.write_text(out / 'contexts/labeled.txt', derived['labeled'])
    si.write_text(out / 'requests.jsonl', request_lines(derived['requests']))
    si.write_json(out / 'rules.json', derived['rules'])
    si.write_json(out / 'estimates.json', estimate(derived['requests']))
    package = {**expected_package(source, text, derived, si.sha256(out / 'requests.jsonl')), 'code': code_hashes()}
    si.write_json(out / 'package.json', package)
    si.checksums(out)
    validate_package(out)
    return package


# ---- 预算、响应解析与回放函数复制自 statute_replication，不导入该工具 ----

def empty_output(output):
    si.require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
               '输出必须是新的空目录；禁止覆盖或续跑')


def message_bytes(messages):
    return sum(len(m['content'].encode('utf-8')) for m in messages)


def token_cost(tokens, price):
    return (Decimal(tokens['prompt_tokens']) * Decimal(str(price['inputCnyPerMillion'])) +
            Decimal(tokens['completion_tokens']) * Decimal(str(price['outputCnyPerMillion']))) / Decimal(1_000_000)


def reservation(package, auth, job, payload):
    tokens = {'prompt_tokens': message_bytes(payload['messages']) + 256,
              'completion_tokens': package['judges'][job['judge']]['maxTokens']}
    return {'tokens': tokens, 'costCny': str(token_cost(tokens, auth['prices'][job['judge']]))}


def charge(record, reserved, auth):
    if record['usage'] is None:
        return {'basis': 'pre_request_reservation', **deepcopy(reserved)}
    return {'basis': 'response_usage', 'tokens': record['usage'],
            'costCny': str(token_cost(record['usage'], auth['prices'][record['judge']]))}


def iso_date(value):
    si.require(isinstance(value, str) and bool(value.strip()), '缺少 ISO 日期')
    try:
        valid = date.fromisoformat(value).isoformat() == value
    except ValueError:
        valid = False
    si.require(valid, 'ISO 日期无效')


def halt(record, reserved, response):
    if isinstance(response, dict) and '_transport' not in response and record['actualModel'] is None:
        return 'actual_model_unknown'
    if record['usage'] is not None and Decimal(record['charge']['costCny']) > Decimal(reserved['costCny']):
        return 'reservation_exceeded'
    return None


def response_record(job, response, contexts, exception=None):
    try:
        fields = si.response_fields(response, contexts)
    except Exception:
        # 畸形响应造成解析器异常，也应记缺失后继续；回放走同一分支。
        model = response.get('model') if isinstance(response, dict) else None
        fields = {'status': 'parse_error', 'result': None, 'usage': None,
                  'actualModel': model if isinstance(model, str) else None}
    record = {**job, **fields, 'error': None}
    if isinstance(response, dict) and '_transport' in response:
        problem = response['_transport']
        record['error'] = ({'type': problem.get('error'), 'httpStatus': problem.get('httpStatus')}
                           if isinstance(problem, dict) else {'type': record['status']})
    elif record['status'] != 'ok':
        record['error'] = {'type': record['status']}
    if exception is not None:
        record['error'] = {'type': type(exception).__name__, 'note': '不归档异常消息；不重试。'}
    return record


def model_halt(record, response, package):
    if record['actualModel'] != package['judges'][record['judge']]['model']:
        return 'actual_model_unknown'
    return None


def incomplete_record(job, reserved):
    return {**job, 'status': 'incomplete', 'result': None, 'actualModel': None, 'usage': None,
            'error': {'type': 'interrupted'}, 'charge': {'basis': 'pre_request_reservation', **deepcopy(reserved)}}


def tool_hashes(root=ROOT):
    return {name: si.sha256(root / name) for name in TOOL_FILES}

# ---- 复制结束 ----


def authorization_template(package_dir, root=ROOT):
    package_dir = Path(package_dir)
    package, _, _ = validate_package(package_dir)
    return {'status': 'draft', 'allowPaidCalls': False, 'authorizedBy': None, 'authorizedAt': None, 'note': None,
            'packageSha256': si.sha256(package_dir / 'package.json'),
            'checksumsSha256': si.sha256(package_dir / 'SHA256SUMS.txt'),
            'requestsSha256': package['requestsSha256'], 'sourceSha256': package['source']['sha256'],
            'registrationSha256': REGISTRATION_SHA256, 'candidateListSha256': CANDIDATES_SHA256,
            'criterionRegistrationSha256': CRITERION_REGISTRATION_SHA256,
            'configurationSha256': si.digest(package['judges']),
            'c0PromptSha256': package['prompts']['C0']['sha256'], 'c1PromptSha256': package['prompts']['C1']['sha256'],
            'toolFiles': tool_hashes(root), 'maxRequests': package['maxRequests'], 'maxRetries': 0, 'budgetCny': None,
            'prices': {'deepseek': {'inputCnyPerMillion': None, 'outputCnyPerMillion': None,
                                    'source': None, 'checkedAt': None}},
            'outputDirectory': None}


BOUND = ('packageSha256', 'checksumsSha256', 'requestsSha256', 'sourceSha256', 'registrationSha256',
         'candidateListSha256', 'criterionRegistrationSha256', 'configurationSha256', 'c0PromptSha256',
         'c1PromptSha256', 'toolFiles')


def validate_authorization(package_dir, auth, output, root=ROOT, *, today=None):
    """output 为 None 时是回放核对：不查输出目录和当天日期。"""
    si.require(auth.get('status') == 'authorized' and auth.get('allowPaidCalls') is True, '未授权付费')
    for name in ('authorizedBy', 'note'):
        si.require(isinstance(auth.get(name), str) and bool(auth[name].strip()), f'授权缺少 {name}')
    iso_date(auth.get('authorizedAt'))
    template = authorization_template(package_dir, root)
    si.require(all(auth.get(k) == template[k] for k in BOUND), '授权哈希不符')
    si.require(type(auth.get('maxRequests')) is int and auth['maxRequests'] == template['maxRequests'] and
               type(auth.get('maxRetries')) is int and auth['maxRetries'] == 0, '请求上限须等于题包请求数且不重试')
    si.require(type(auth.get('budgetCny')) in (int, float) and math.isfinite(auth['budgetCny']) and
               auth['budgetCny'] > 0, '预算必须为正有限数')
    si.require(auth['budgetCny'] <= BUDGET_CAP_CNY, f'预算超过脚本上限 ¥{BUDGET_CAP_CNY}')
    price = auth.get('prices', {}).get('deepseek') if isinstance(auth.get('prices'), dict) else None
    si.require(isinstance(price, dict) and all(type(price.get(k)) in (int, float) and math.isfinite(price[k]) and
               price[k] >= 0 for k in ('inputCnyPerMillion', 'outputCnyPerMillion')), '单价无效')
    si.require(isinstance(price.get('source'), str) and bool(price['source'].strip()), '单价缺少来源')
    iso_date(price.get('checkedAt'))
    si.require(price['checkedAt'] == auth['authorizedAt'], '单价须在授权当天核实')
    if output is not None:
        si.require(price['checkedAt'] == (today or date.today().isoformat()), '运行当天须重新核价和授权')
        si.require(isinstance(auth.get('outputDirectory'), str) and bool(auth['outputDirectory'].strip()) and
                   (root / auth['outputDirectory']).resolve() == output.resolve(), '授权未绑定本次输出目录')


def job_of(request):
    return {k: request[k] for k in ('requestId', 'itemId', 'arm', 'judge')}


def run(package_dir, authorization_path, output, *, root=ROOT, transport=None, sleep=time.sleep, today=None):
    package_dir, output = Path(package_dir), Path(output)
    auth = si.load(authorization_path)
    package, requests, contexts = validate_package(package_dir)
    validate_authorization(package_dir, auth, output, root, today=today)
    empty_output(output)
    send = transport if transport is not None else si.make_transport(package)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package_dir, output / 'package')
    shutil.copyfile(authorization_path, output / 'authorization.json')
    for name in TOOL_FILES:
        target = output / 'sources' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    records, calls, cost, stop = [], 0, Decimal(0), 'interrupted'
    pending = None
    events, budget = output / 'events.jsonl', Decimal(str(auth['budgetCny']))
    try:
        for request in requests:
            if calls >= auth['maxRequests']:
                stop = 'request_limit'
                break
            job, payload = job_of(request), request['payload']
            reserved = reservation(package, auth, job, payload)
            if cost + Decimal(reserved['costCny']) > budget:
                stop = 'estimated_budget_guard'
                break
            if calls:
                sleep(THROTTLE_SECONDS)
            si.append_event(events, {'event': 'start', **job, 'at': si.utc(), 'request': payload, 'reservation': reserved})
            pending = (job, reserved)
            calls += 1
            try:
                response = send(job['judge'], payload)
            except Exception:
                stop = 'interrupted'
                break
            if isinstance(response, dict) and '_transport' in response:
                stop = 'interrupted'
                break
            record = response_record(job, response, contexts[job['arm'].split('-')[0]])
            record['charge'] = charge(record, reserved, auth)
            cost += Decimal(record['charge']['costCny'])
            si.append_event(events, {'event': 'end' if record['status'] == 'ok' else 'failure', **record,
                                     'response': response, 'at': si.utc()})
            pending = None
            records.append(record)
            stop = model_halt(record, response, package) or halt(record, reserved, response)
            if stop:
                break
        else:
            stop = 'completed'
    except BaseException:
        stop = 'interrupted'
        raise
    finally:
        if pending is not None:
            job, reserved = pending
            records.append(incomplete_record(job, reserved))
            cost += Decimal(reserved['costCny'])
        si.append_event(events, {'event': 'stop', 'reason': stop, 'attempts': calls,
                                 'estimatedCostCny': str(cost), 'at': si.utc()})
        si.write_json(output / 'manifest.json', {
            'schemaVersion': 1, 'experimentVersion': VERSION, 'stopReason': stop, 'attempts': calls,
            'completedRecords': sum(r['status'] != 'incomplete' for r in records), 'plannedRequests': len(requests),
            'estimatedCostCny': str(cost), 'actualBillCny': None, 'costNote': COST_NOTE})
        si.write_json(output / 'records.json', records)
        si.checksums(output)
    return si.load(output / 'manifest.json')


def read_records(directory, package, requests, contexts, auth):
    """从 events.jsonl 重建每条结果，并核对请求体、计费、停止原因、manifest 与 records。"""
    starts, records, stopped, current, reserved, halted = [], [], None, None, None, None
    cost, budget = Decimal(0), Decimal(str(auth['budgetCny']))
    for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        si.require(stopped is None, '停止后出现事件')
        kind = event.get('event')
        if kind == 'stop':
            si.require(current is None or event.get('reason') == 'interrupted', '请求未结束但未标记中断')
            if current is not None:
                records.append(incomplete_record(current, reserved))
                cost += Decimal(reserved['costCny'])
            si.require(event.get('attempts') == len(starts) and event.get('estimatedCostCny') == str(cost), '停止计数/费用不符')
            stopped = event
            continue
        si.require(kind in ('start', 'end', 'failure'), '未知事件')
        if kind == 'start':
            si.require(current is None and halted is None and len(starts) < min(len(requests), auth['maxRequests']),
                       '请求越过停止条件')
            request = requests[len(starts)]
            job = job_of(request)
            si.require(all(event.get(k) == v for k, v in job.items()) and event.get('request') == request['payload'],
                       '调度或请求体不符')
            reserved = reservation(package, auth, job, request['payload'])
            si.require(event.get('reservation') == reserved and cost + Decimal(reserved['costCny']) <= budget, '预留/预算不符')
            starts.append(job)
            current = job
            continue
        si.require(current is not None and all(event.get(k) == v for k, v in current.items()), '结束事件无对应开始')
        record = {k: v for k, v in event.items() if k not in ('event', 'at', 'response')}
        si.require(set(record) == set(current) | {'status', 'result', 'actualModel', 'usage', 'error', 'charge'}, '记录字段不符')
        response = event.get('response')
        fields = response_record(current, response, contexts[current['arm'].split('-')[0]])
        si.require(record['error'] == fields['error'], '错误字段与原始响应不符')
        si.require(all(record.get(k) == fields[k] for k in ('status', 'result', 'actualModel', 'usage')), '结果/用量与原始响应不符')
        si.require((kind == 'end') == (record['status'] == 'ok'), '结束事件类型不符')
        si.require(record.get('charge') == charge(record, reserved, auth), '计费不符')
        cost += Decimal(record['charge']['costCny'])
        halted = model_halt(record, response, package) or halt(record, reserved, response)
        records.append(record)
        current = None
    si.require(stopped is not None and stopped.get('reason') in STOP_REASONS, '缺少或未知停止事件')
    reason = stopped['reason']
    if reason != 'interrupted':
        if halted:
            expected = halted
        elif len(starts) == len(requests):
            expected = 'completed'
        elif len(starts) >= auth['maxRequests']:
            expected = 'request_limit'
        else:
            upcoming = reservation(package, auth, job_of(requests[len(starts)]), requests[len(starts)]['payload'])
            expected = 'estimated_budget_guard' if cost + Decimal(upcoming['costCny']) > budget else None
        si.require(reason == expected, '停止原因与事件不符')
    manifest = si.load(directory / 'manifest.json')
    si.require(manifest.get('stopReason') == reason and manifest.get('attempts') == len(starts) and
               manifest.get('completedRecords') == sum(r['status'] != 'incomplete' for r in records) and
               manifest.get('plannedRequests') == len(requests) and
               manifest.get('estimatedCostCny') == str(cost), 'manifest 与事件不符')
    si.require(si.load(directory / 'records.json') == records, 'records 与事件不符')
    return records


def exact_mcnemar(b, c):
    n = b + c
    p = Fraction(sum(math.comb(n, k) for k in range(b, n + 1)), 2 ** n) if n else Fraction(1)
    with localcontext() as ctx:
        ctx.prec = 50
        decimal = format(Decimal(p.numerator) / Decimal(p.denominator), '.6f')
    return p, {'fraction': f'{p.numerator}/{p.denominator}', 'decimal': decimal}


def flagged(row):
    return row['available'] and row['verdict'] in si.NOT_SUPPORTED


def paired(by_cell, error_ids, before, after):
    """同一批错误陈述上 after 对 before 的配对比较：b = after 检出而 before 未检出。"""
    b_ids, c_ids, missing = [], [], []
    for item_id in error_ids:
        x, y = by_cell[item_id, before], by_cell[item_id, after]
        if not x['available'] or not y['available']:
            missing.append(item_id)
        if flagged(y) and not flagged(x):
            b_ids.append(item_id)
        if flagged(x) and not flagged(y):
            c_ids.append(item_id)
    p, display = exact_mcnemar(len(b_ids), len(c_ids))
    return p, {'b': len(b_ids), 'c': len(c_ids), 'd': len(b_ids) - len(c_ids), 'p': display,
               'bIds': b_ids, 'cIds': c_ids, 'unavailableErrorIds': missing}


CONTROL_KINDS = {
    'singleParagraphFirst': lambda r: r['type'] == 'control_pinpoint' and r['citedArticleParagraphs'] == 1,
    'multiParagraphPinpoint': lambda r: r['type'] == 'control_pinpoint' and r['citedArticleParagraphs'] > 1,
    'articleOnly': lambda r: r['type'] == 'control_article',
}


def summarize(package, records, rules, construction_errors=None):
    indexed = {(r['itemId'], r['arm']): r for r in records}
    si.require(len(indexed) == len(records), '结果格重复')
    totals = totals_of(package['items'])
    limits = thresholds_of(totals)
    details = []
    for item in package['items']:
        for arm in ARMS:
            row = indexed.get((item['id'], arm), {})
            available = row.get('status') == 'ok' and (row.get('result') or {}).get('verdict') in (
                'supported', 'contradicted', 'insufficient')
            details.append({'itemId': item['id'], 'arm': arm, 'type': item['type'], 'expected': item['expected'],
                            'citedArticleParagraphs': item['citedArticleParagraphs'],
                            'status': row.get('status', 'not_attempted'), 'available': available,
                            'verdict': row['result']['verdict'] if available else None,
                            'reason': (row.get('result') or {}).get('reason')})

    def count(rows):
        return {'items': len(rows),
                'detected': sum(r['expected'] == 'not_supported' and flagged(r) for r in rows),
                'falsePositives': sum(r['expected'] == 'supported' and flagged(r) for r in rows),
                'unavailable': sum(not r['available'] for r in rows),
                'unavailableIds': [r['itemId'] for r in rows if not r['available']]}

    arms = {}
    for arm in ARMS:
        rows = [r for r in details if r['arm'] == arm]
        arms[arm] = {**count(rows), 'detectionTotal': totals['errors'], 'falsePositiveTotal': totals['controls'],
                     'byType': {t: count([r for r in rows if r['type'] == t]) for t in TYPES},
                     'falsePositivesByControlKind': {k: count([r for r in rows if r['expected'] == 'supported' and f(r)])
                                                     for k, f in CONTROL_KINDS.items()}}
    by_cell = {(r['itemId'], r['arm']): r for r in details}
    error_ids = [i['id'] for i in package['items'] if i['expected'] == 'not_supported']

    def primary_of(kept):
        """主比较 L-C1 对 U-C1；kept 为参与判定的陈述 ID，门槛一律用题包按全部陈述推出的值。"""
        u, l = (count([r for r in details if r['arm'] == arm and r['itemId'] in kept]) for arm in ('U-C1', 'L-C1'))
        p, result = paired(by_cell, [i for i in error_ids if i in kept], 'U-C1', 'L-C1')
        d = result['d']
        if (u['detected'] >= limits['simpleSufficientMinDetected'] and
                u['falsePositives'] <= limits['simpleSufficientMaxFalsePositives']):
            decision = 'simple_sufficient'
        elif (d >= limits['labelGainMinDifference'] and p <= Fraction(limits['labelGainMaxP']) and
              l['falsePositives'] <= u['falsePositives'] + limits['labelGainFalsePositiveTolerance']):
            decision = 'label_gain'
        elif d <= 0:
            decision = 'no_gain'
        else:
            decision = 'insufficient_evidence'
        unavailable = bool(u['unavailable'] or l['unavailable'])
        return {'decision': decision, **result,
                'U-C1': {k: u[k] for k in ('detected', 'falsePositives', 'unavailable')},
                'L-C1': {k: l[k] for k in ('detected', 'falsePositives', 'unavailable')},
                'containsUnavailable': unavailable, 'note': '含不可用，结论可能变化' if unavailable else None}

    all_ids = {i['id'] for i in package['items']}
    primary = primary_of(all_ids)
    removal = None
    if construction_errors:
        removed = [e['id'] for e in construction_errors]
        removal = {**primary_of(all_ids - set(removed)), 'removedIds': removed, 'constructionErrors': construction_errors,
                   'thresholdNote': '门槛不改：沿用题包按全部陈述推出的值，不按剔除后的条数重算'}
    _, label_c0 = paired(by_cell, error_ids, 'U-C0', 'L-C0')
    label_c0['falsePositives'] = {'U-C0': arms['U-C0']['falsePositives'], 'L-C0': arms['L-C0']['falsePositives']}

    def difference(after, before):
        return {k: arms[after][k] - arms[before][k] for k in ('detected', 'falsePositives')}

    effects = {'criterionU': difference('U-C1', 'U-C0'), 'criterionL': difference('L-C1', 'L-C0'),
               'labelC0': difference('L-C0', 'U-C0'), 'labelC1': difference('L-C1', 'U-C1')}
    effects['interaction'] = {k: effects['criterionL'][k] - effects['criterionU'][k]
                              for k in ('detected', 'falsePositives')}
    changes = {}
    for context in ('U', 'L'):
        moved = {k: [] for k in ('newDetections', 'lostDetections', 'newFalsePositives',
                                 'reducedFalsePositives', 'unavailable')}
        for item in package['items']:
            before, after = by_cell[item['id'], context + '-C0'], by_cell[item['id'], context + '-C1']
            if not before['available'] or not after['available']:
                moved['unavailable'].append(item['id'])
                continue
            if flagged(after) != flagged(before):
                key = (('newDetections' if flagged(after) else 'lostDetections') if item['expected'] == 'not_supported'
                       else ('newFalsePositives' if flagged(after) else 'reducedFalsePositives'))
                moved[key].append(item['id'])
        changes[context] = moved
    reference = {}
    for engine in ('rule', 'lexical'):
        rows = [{'itemId': i['id'], 'type': i['type'], 'expected': i['expected'], 'available': True,
                 'verdict': 'contradicted' if rules['items'][i['id']][engine]['flagged'] else 'supported'}
                for i in package['items']]
        reference[engine] = {**count(rows), 'byType': {t: count([r for r in rows if r['type'] == t]) for t in TYPES}}
    return {'totals': totals, 'thresholds': limits, 'arms': arms,
            'primary': primary, 'primaryAfterConstructionRemoval': removal,
            'secondaryLabelC0': {**label_c0, 'note': '只报告，不判定'},
            'effects': effects, 'effectsNote': '只报告条数差；含不可用时差值可能变化',
            'pairedC0toC1': changes, 'offlineBaselines': {**reference, 'note': rules['note']},
            'details': details, 'excluded': package['excluded'],
            'scope': package['scope'],
            'limitations': ['一部法规、一个裁判、跑一次、作者本人操作，结论不外推',
                            '陈述自动构造，问句中性，不是真实用户问题',
                            f"{totals['errors']} 条错误来自若干款，同一款有多个变体，不是独立样本"]}


def load_construction_errors(path, package):
    """运行后发现的构造错误：{"items": [{"id": 陈述 ID, "reason": 说明}, ...]}。只列出，不改题包。"""
    if path is None:
        return None
    value = si.load(Path(path))
    ids = {i['id'] for i in package['items']}
    si.require(isinstance(value, dict) and set(value) == {'items'} and isinstance(value['items'], list) and
               bool(value['items']), '构造错误清单格式不符')
    for entry in value['items']:
        si.require(isinstance(entry, dict) and set(entry) == {'id', 'reason'} and entry['id'] in ids and
                   isinstance(entry['reason'], str) and bool(entry['reason'].strip()), '构造错误条目不符')
    si.require(len({e['id'] for e in value['items']}) == len(value['items']), '构造错误 ID 重复')
    return value['items']


def analyze(directory, output, construction_errors=None):
    directory, output = Path(directory), Path(output)
    si.require(not output.exists(), '分析输出已存在；禁止覆盖')
    si.require(not output.resolve().is_relative_to(directory.resolve()), '分析输出不能写入运行归档')
    si.verify_checksums(directory)
    si.require(tool_hashes(directory / 'sources') == tool_hashes(ROOT), '当前实现与归档 sources 不一致')
    package, requests, contexts = validate_package(directory / 'package')
    auth = si.load(directory / 'authorization.json')
    validate_authorization(directory / 'package', auth, None, directory / 'sources')
    records = read_records(directory, package, requests, contexts, auth)
    errors = load_construction_errors(construction_errors, package)
    result = {'schemaVersion': 1, 'experimentVersion': VERSION, 'registrationSha256': REGISTRATION_SHA256,
              **summarize(package, records, si.load(directory / 'package/rules.json'), errors)}
    output.parent.mkdir(parents=True, exist_ok=True)
    si.write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    template = sub.add_parser('template')
    template.add_argument('--package', type=Path, required=True)
    execute = sub.add_parser('run')
    execute.add_argument('--package', type=Path, required=True)
    execute.add_argument('--authorization', type=Path, required=True)
    execute.add_argument('--out', type=Path, required=True)
    analysis = sub.add_parser('analyze')
    analysis.add_argument('--run', type=Path, required=True)
    analysis.add_argument('--out', type=Path, required=True)
    analysis.add_argument('--construction-errors', type=Path)
    args = parser.parse_args()
    if args.command == 'template':
        result = authorization_template(args.package)
    elif args.command == 'run':
        result = run(args.package, args.authorization, args.out)
    else:
        result = analyze(args.run, args.out, args.construction_errors)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
