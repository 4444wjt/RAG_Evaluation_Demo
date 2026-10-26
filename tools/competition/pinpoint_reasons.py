"""对已归档裁判漏检理由做离线文本分类，不调用模型。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.statute_check import cn_label  # noqa: E402
from tools.competition import statute_injection as si  # noqa: E402

PARAGRAPH = re.compile(r'第[一二三四五六七八九十]+款')
ARTICLE_PARAGRAPH = re.compile(r'第[一二三四五六七八九十]+条第[一二三四五六七八九十]+款')
CATEGORIES = ('no_paragraph_in_reason', 'repeats_stated_paragraph', 'names_other_paragraph')
TYPES = ('paragraph_existing', 'paragraph_absent')
METHOD = [
    '只取 package.json 中 type 为 paragraph_existing 或 paragraph_absent、expected 为 not_supported 的材料；'
    'records.json 中 status == ok 且 result.verdict == supported 的记录计为漏检。original 与 pinpoint 分别报告，对外主张只引用 original。',
    '用正则 第[一二三四五六七八九十]+款 抽取款号集合；stated 来自 statement，said 来自 result.reason；不匹配单个字符「款」。',
    '分类优先级：said 为空为 no_paragraph_in_reason；否则 stated 与 said 有交集为 repeats_stated_paragraph；否则为 names_other_paragraph。',
    'namesCorrectParagraph 仅在 names_other_paragraph 且 said 等于 {第<cn_label(sourceLocation.paragraph)>款} 时为 true；'
    'saidContainsCorrectParagraph 记录 said 是否含正确款号，包含 repeats_stated_paragraph 的情况，不改变分类。',
    'hasArticleBoundParagraph 用正则 第[一二三四五六七八九十]+条第[一二三四五六七八九十]+款 抽取连续写法，'
    '检查其中是否含 sourceLocation 的正确条号与正确款号绑定的写法，不改变分类。',
]


def classify(statement: str, reason: str, source_location: dict) -> dict:
    stated, said = set(PARAGRAPH.findall(statement)), set(PARAGRAPH.findall(reason))
    correct = f"第{cn_label(source_location['paragraph'])}款"
    bound = f"第{cn_label(source_location['article'])}条{correct}"
    category = (CATEGORIES[0] if not said else CATEGORIES[1] if stated & said else CATEGORIES[2])
    return {'stated': sorted(stated), 'said': sorted(said), 'category': category,
            'namesCorrectParagraph': category == CATEGORIES[2] and said == {correct},
            'saidContainsCorrectParagraph': correct in said,
            'hasArticleBoundParagraph': bound in ARTICLE_PARAGRAPH.findall(reason)}


def analyze(run: Path, output: Path) -> dict:
    si.verify_checksums(run)
    si.require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
               '输出必须是新的空目录；禁止覆盖或续跑')
    package, records = si.load(run / 'package.json'), si.load(run / 'records.json')
    items = {item['id']: item for item in package['items']}
    si.require(len(items) == len(package['items']), '材料编号重复')
    for item in items.values():
        if item['type'] in TYPES:
            si.require(item['expected'] == 'not_supported', '款级错误材料预期不符')
    prompts, judges = ('original', 'pinpoint'), sorted(package['judges'])
    counts = {p: {j: dict.fromkeys(CATEGORIES, 0) for j in judges} for p in prompts}
    rows, seen = [], set()
    for record in records:
        identity = tuple(record[k] for k in ('itemId', 'judge', 'prompt'))
        si.require(identity not in seen, '结果定位重复')
        seen.add(identity)
        item_id, judge, prompt = identity
        si.require(item_id in items and judge in judges and prompt in prompts, '未知结果定位')
        item = items[item_id]
        if (item['type'] not in TYPES or record['status'] != 'ok'
                or (record.get('result') or {}).get('verdict') != 'supported'):
            continue
        fields = classify(item['statement'], record['result']['reason'], item['sourceLocation'])
        rows.append({'itemId': item_id, 'judge': judge, 'prompt': prompt, 'type': item['type'], **fields})
        counts[prompt][judge][fields['category']] += 1
    rows.sort(key=lambda r: (r['prompt'], r['judge'], r['itemId']))
    result = {'schemaVersion': 1, 'method': METHOD,
              'inputSha256': {name: si.sha256(run / name) for name in ('package.json', 'records.json')},
              'recordLocator': '在输入 records.json 中以 itemId + judge + prompt 唯一定位；理由见 result.reason。',
              'byPromptJudgeCategory': counts,
              'byPromptCategory': {p: {c: sum(counts[p][j][c] for j in judges) for c in CATEGORIES} for p in prompts},
              'misses': rows,
              'namesOtherParagraphIds': {p: [{'itemId': r['itemId'], 'judge': r['judge']} for r in rows
                                           if r['prompt'] == p and r['category'] == CATEGORIES[2]] for p in prompts}}
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / 'reasons.json', result)
    si.checksums(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    analysis = sub.add_parser('analyze', help='先校验归档，再离线分类')
    analysis.add_argument('--run', type=Path, required=True)
    analysis.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.run, args.output)
    print(json.dumps(result['byPromptJudgeCategory'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
