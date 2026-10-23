"""裁判盲区体检：第二部法规的离线考题包（不调用模型、不联网）。

按 work/competition/feature-pick-20261022/阈值与估分-20261022.md 登记的选题规则，
从《政府信息公开条例》节选自动生成 25 条陈述、40 个请求载荷和补标签节选。
只读 scenarios/ 与已归档的 J2 运行，只 import 冻结模块和 statute_labeled 的标签函数，不改任何文件。

用法：python make_exam.py --out <新的空目录>
"""
import argparse
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.core import statute_check as sc  # noqa: E402
from tools.competition import statute_injection as si  # noqa: E402
from tools.competition import statute_labeled as sl  # noqa: E402

VERSION = 'judge-blindspot-exam-v1'
SOURCE = 'scenarios/information_disclosure.txt'
REGISTRATION = 'work/competition/feature-pick-20261022/阈值与估分-20261022.md'
REGISTRATION_SHA256 = '3703256a6e24f2a1c848a772638d7f71286896843ddfd4682239fc72a47f4d06'
REFERENCE = 'docs/competition/statute-check/20261022-equalinfo/run'
LABEL_TOOL = 'tools/competition/statute_labeled.py'
QUESTION = '《中华人民共和国政府信息公开条例》节选对此有什么规定？'
ARMS = {'U': '完整节选，未补标签（同 J2）', 'L': '完整节选，每款开头补「第X条第Y款」'}
LAYERS = {'paragraph_existing': '款级', 'paragraph_absent': '款级',
          'article_swap': '条级', 'article_absent': '条级', 'control_pinpoint': '对照'}
COUNTS = {'paragraph_existing': 7, 'paragraph_absent': 3, 'article_swap': 5,
          'article_absent': 5, 'control_pinpoint': 5}
RULES = {
    'statement': '「按照<所引>规定，<内容>」。内容取被取用那一款原文的第一句（到第一个「。」为止）；'
                 '该款以「：」结尾并带项时，内容为该款原文加其全部项原文直接相连。逐字取原文，不改写。',
    'question': f'所有陈述用同一个问题：「{QUESTION}」。J2 用的是真实用户问题，这里没有，固定为不提示核对的中性问句。',
    'paragraph_existing': '每个多款条的每一款 j，引为同条下一款（最后一款引回第一款），内容取第 j 款。',
    'paragraph_absent': '按条序取前 3 条，引为「该条款数 + 1」款，内容取该条第一款。',
    'article_swap': '按条序取前 5 条，引为节选中的下一条（只引到条），内容取该条第一款。',
    'article_absent': '节选条号范围（第二十九至四十二条）内不在节选中的条号，按从小到大依次配给前 5 条，'
                      '只引到条，内容取该条第一款。',
    'control_pinpoint': '多款条的各款按条序取前 5 款，引用正确的条和款，内容取该款。',
    'labels': sl.LABEL_RULE,
    'schedule': '先 U 臂再 L 臂，臂内按陈述顺序；L 臂不跑条级（条在原文里本来就有标号）。每个请求只发一次，不重试。',
}


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


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
    si.require(end >= 0, f'{unit} 没有句号')
    return body[:end + 1]


def label(article, paragraph=None):
    return f'第{sc.cn_label(article)}条' + (f'第{sc.cn_label(paragraph)}款' if paragraph else '')


def build_items(text):
    order, counts, paragraphs, items = structure(text)
    multi = [a for a in order if counts[a] > 1]
    say = lambda cited, unit: f'按照{label(*cited)}规定，{content(unit, paragraphs, items)}'
    rows = []

    def add(kind, cited, unit, suffix):
        rows.append({'id': f'{kind}-{suffix}', 'type': kind, 'layer': LAYERS[kind],
                     'expected': 'supported' if kind == 'control_pinpoint' else 'not_supported',
                     'cited': label(*cited), 'contentFrom': label(*unit),
                     'question': QUESTION, 'statement': say(cited, unit)})

    for a in multi:
        for j in range(1, counts[a] + 1):
            k = j % counts[a] + 1
            add('paragraph_existing', (a, k), (a, j), f'A{a}P{j}toP{k}')
    for a in order[:3]:
        add('paragraph_absent', (a, counts[a] + 1), (a, 1), f'A{a}toP{counts[a] + 1}')
    for a in order[:5]:
        b = order[order.index(a) + 1]
        add('article_swap', (b,), (a, 1), f'A{a}toA{b}')
    missing = [x for x in range(order[0], order[-1] + 1) if x not in order]
    si.require(len(missing) >= 5, '节选条号范围内缺的条号不足 5 个')
    for a, b in zip(order[:5], missing):
        add('article_absent', (b,), (a, 1), f'A{a}toA{b}')
    for a, j in [(a, j) for a in multi for j in range(1, counts[a] + 1)][:5]:
        add('control_pinpoint', (a, j), (a, j), f'A{a}P{j}')
    return rows, paragraphs, items


def check_items(rows, text, paragraphs, items):
    units = {(p.article, p.paragraph, p.item) for p in sc.parse_provisions(text)}
    lines = set(text.split('\n'))
    for row in rows:
        match = sc.REF.search(sc.normalize(row['statement']))
        si.require(match is not None and sc._ref_label(*sc._ref_value(match)) == row['cited'], f"{row['id']} 引用解析不符")
        article, paragraph, _ = sc._ref_value(match)
        exists = (article, paragraph or 1, None) in units
        si.require(exists == (row['type'] not in ('paragraph_absent', 'article_absent')), f"{row['id']} 存在性不符")
        si.require((row['cited'] == row['contentFrom']) == (row['type'] == 'control_pinpoint') or row['type'] == 'article_swap',
                   f"{row['id']} 引用与内容来源关系不符")
        body = row['statement'].split('规定，', 1)[1]
        pieces = [p for p in re.split(r'(?=（[一二三四五六七八九十]+）)', body) if p]
        si.require(all(any(p in line for line in lines) for p in pieces), f"{row['id']} 内容不是逐字原文")
    si.require({t: sum(r['type'] == t for r in rows) for t in COUNTS} == COUNTS, '各类型条数与登记不符')
    si.require(len({r['id'] for r in rows}) == len(rows) == 25, '陈述必须为 25 条且 id 唯一')


def strip_labels(labeled, insertions):
    for ins in sorted(insertions, key=lambda i: i['offset'], reverse=True):
        si.require(labeled[ins['offset']:ins['offset'] + len(ins['text'])] == ins['text'], '插入位置不符')
        labeled = labeled[:ins['offset']] + labeled[ins['offset'] + len(ins['text']):]
    return labeled


def build_requests(rows, contexts, judge):
    requests = []
    for arm in ARMS:
        for row in rows:
            if arm == 'L' and row['layer'] == '条级':
                continue
            messages = si.prompt_messages(row, {'contexts': [contexts[arm]]})
            si.require(messages[0]['content'] == si.SYSTEM_PROMPT, '系统提示不是原提示')
            payload = {'model': judge['model'], 'messages': messages, 'temperature': judge['temperature'],
                       'max_tokens': judge['maxTokens'], 'stream': False, 'thinking': dict(judge['thinking'])}
            requests.append({'requestId': f"{row['id']}--{arm}--deepseek", 'itemId': row['id'], 'arm': arm,
                             'judge': 'deepseek', 'payload': payload})
    si.require(len(requests) == 40, '请求必须为 40 个')
    return requests


def estimate(requests, price):
    """期望：按 J2 DeepSeek 实际计费的每字节均价折算；上限：按 statute_labeled 的预留口径。"""
    starts, cost, size = {}, Decimal(0), 0
    for line in (ROOT / REFERENCE / 'events.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event.get('condition') != 'J2' or event.get('judge') != 'deepseek':
            continue
        if event['event'] == 'start':
            starts[event['requestId']] = event
        elif event['event'] in ('end', 'failure'):
            size += sl.message_bytes(starts.pop(event['requestId'])['request']['messages'])
            cost += Decimal(event['charge']['costCny'])
    new = [sl.message_bytes(r['payload']['messages']) for r in requests]
    upper = sum(sl.token_cost({'prompt_tokens': b + 256, 'completion_tokens': r['payload']['max_tokens']}, price)
                for b, r in zip(new, requests))
    return {'requests': len(requests), 'messageBytes': sum(new),
            'expectedCostCny': str((cost * sum(new) / size).quantize(Decimal('0.0001'))),
            'reservationUpperCny': str(upper.quantize(Decimal('0.0001'))),
            'reference': {'run': REFERENCE, 'judge': 'deepseek', 'condition': 'J2', 'costCny': str(cost), 'messageBytes': size,
                          'eventsSha256': si.sha256(ROOT / REFERENCE / 'events.jsonl')},
            'price': price,
            'method': '期望=J2 DeepSeek 实计费×新/旧消息总字节；上限=每请求(消息字节+256)输入+maxTokens输出按授权单价。估算，不是账单。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--out', required=True, type=Path)
    out = parser.parse_args().out
    si.require(not out.exists() or not any(out.iterdir()), f'输出目录 {out} 不为空')
    si.require(si.sha256(ROOT / REGISTRATION) == REGISTRATION_SHA256, '登记文件已被改动，停止')

    raw = (ROOT / SOURCE).read_bytes()
    text = raw.decode('utf-8')
    si.require('\r' not in text, '节选含 CR，与 J2 的 read_text 口径可能不一致')
    labeled, insertions = sl.label_source(text)
    si.require(strip_labels(labeled, insertions) == text, '补标签节选去掉标签后与原文不一致')
    si.require(len(insertions) == sum(1 for p in sc.parse_provisions(text) if not p.item), '标签数与款数不符')

    rows, paragraphs, items = build_items(text)
    check_items(rows, text, paragraphs, items)
    reference = si.load(ROOT / REFERENCE / 'package.json')
    judge = reference['judges']['deepseek']
    requests = build_requests(rows, {'U': text, 'L': labeled}, judge)
    price = si.load(ROOT / REFERENCE / 'authorization.json')['prices']['deepseek']
    rules = {r['id']: {'flagged': (res := sc.check_answer(r['statement'], [text], text))['flagged'],
                       'errorCodes': sorted({f['code'] for f in res['findings'] if f['severity'] == 'error'})}
             for r in rows}

    out.mkdir(parents=True, exist_ok=True)
    (out / 'contexts').mkdir()
    (out / 'contexts' / 'plain.txt').write_bytes(raw)
    (out / 'contexts' / 'labeled.txt').write_bytes(labeled.encode('utf-8'))
    with open(out / 'requests.jsonl', 'w', encoding='utf-8', newline='\n') as f:
        for request in requests:
            f.write(json.dumps(request, ensure_ascii=False, separators=(',', ':')) + '\n')
    package = {
        'schemaVersion': 1, 'experimentVersion': VERSION,
        'registration': {'path': REGISTRATION, 'sha256': REGISTRATION_SHA256},
        'source': {'path': SOURCE, 'sha256': sha256_bytes(raw)},
        'contexts': {'U': {'file': 'contexts/plain.txt', 'sha256': sha256_bytes(raw), 'note': ARMS['U']},
                     'L': {'file': 'contexts/labeled.txt', 'sha256': sha256_bytes(labeled.encode('utf-8')),
                           'note': ARMS['L'], 'insertions': insertions}},
        'judges': {'deepseek': judge}, 'judgeSource': f'{REFERENCE}/package.json',
        'promptSha256': si.digest(si.SYSTEM_PROMPT),
        'code': {'makeExam': si.sha256(Path(__file__)), 'statuteCheck': si.sha256(ROOT / 'backend/core/statute_check.py'),
                 'statuteInjection': si.sha256(ROOT / 'tools/competition/statute_injection.py'),
                 'statuteLabeled': si.sha256(ROOT / LABEL_TOOL)},
        'rules': RULES, 'counts': COUNTS, 'items': rows,
        'requestsSha256': si.sha256(out / 'requests.jsonl'), 'maxRequests': len(requests), 'maxRetries': 0,
        'detection': '裁判 verdict 为 contradicted 或 insufficient 记为检出；supported 记为未检出；失败或解析失败单列为不可用。',
        'scope': '单一法规、自动构造、单裁判单次；不参与 Quality Gate；只报条数。',
    }
    si.write_json(out / 'package.json', package)
    si.write_json(out / 'rules.json', {'note': '规则只作参照，不是真值；真值来自构造。', 'items': rules})
    si.write_json(out / 'estimates.json', estimate(requests, price))
    names = sorted(str(p.relative_to(out)).replace('\\', '/') for p in out.rglob('*') if p.is_file())
    with open(out / 'SHA256SUMS.txt', 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(f'{si.sha256(out / n)}  {n}\n' for n in names)

    print(f'陈述 {len(rows)} 条，请求 {len(requests)} 个，标签 {len(insertions)} 处')
    for kind, n in COUNTS.items():
        hit = sum(rules[r['id']]['flagged'] for r in rows if r['type'] == kind)
        print(f'   {kind:<18s} {n} 条，规则参照标记 {hit}')
    est = json.loads((out / 'estimates.json').read_text(encoding='utf-8'))
    print(f"   估算 ¥{est['expectedCostCny']}，预留上限 ¥{est['reservationUpperCny']}")


if __name__ == '__main__':
    main()
