"""词面最近邻基线：同一批注入材料、同一份法规节选，检查条号/款号错位在词面上是否本来就可分。

离线、确定性、不调用模型；只读已归档的运行目录，不改规则文件。
本脚本是看过规则与裁判结果之后才编写的，只作材料可分性检查，不是另一种要对外宣传的方法。

逐条判定（只看陈述里的第一个条号引用）：
1. 没有条号引用：不标记。
2. 所引条/款/项不在节选中：标记（存在性）。
3. 否则在所引层级的同级单元之间比较 `statute_check._coverage`
   （陈述去掉条号引用后的字符二元组，被该单元原文覆盖的比例）：
   只引到条时比较节选中的各条，引到款时比较同条各款，引到项时比较同款各项。
   有别的单元覆盖率严格高于所引单元，即标记。
   不设最小覆盖率、不设差值、不用交叉引用豁免。
余量 = 所引单元覆盖率 − 其余同级单元的最高覆盖率；负数表示内容更像别处。

用法：python lexical_baseline.py --out <空目录>
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from backend.core import statute_check as sc  # noqa: E402

ARCHIVE = ROOT / 'docs/competition/statute-check'
SOURCE = 'inputs/scenarios/deposit_insurance.txt'
CHECKER = ROOT / 'backend/core/statute_check.py'

# 类型 → (说明, 分组)。分组为 None 的类型不针对条号，只逐条列出，不进分组合计。
MATERIALS = {
    'pinpoint': ('20261021-pinpoint-run', {
        'paragraph_existing': ('同条存在的另一款', '款级错误'),
        'paragraph_absent': ('不存在的款', '款级错误'),
        'item_existing': ('同款存在的另一项', '项级错误'),
        'item_absent': ('不存在的项', '项级错误'),
        'control': ('原陈述对照', '支持对照'),
        'control_pinpoint': ('正确款项定位对照', '支持对照'),
        'paragraph_omitted': ('省略款号对照', '支持对照'),
    }),
    'formal': ('20261021-formal', {
        'article_swap': ('改为节选中的另一条', '条号类错误'),
        'article_absent': ('改为节选中没有的条', '条号类错误'),
        'paragraph_shift': ('加一个错的款号', '条号类错误'),
        'control': ('原陈述对照', '对照'),
        'control_paragraph': ('加一个正确的款号', '对照'),
        'quantity_value': ('改数额', None),
        'quantity_unit': ('改单位', None),
        'modal': ('改情态词', None),
        'polarity': ('改极性', None),
    }),
}
SAME_AS_PINPOINT = '20261022-equalinfo'


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verified(directory: Path, name: str) -> bytes:
    """读取文件，并与该目录 SHA256SUMS.txt 里的记录比对；不符即停止。"""
    table = {}
    for line in (directory / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        if line.strip():
            digest, path = line.strip().split(maxsplit=1)
            table[path.lstrip('*')] = digest
    raw = (directory / name).read_bytes()
    if table.get(name) != sha256(raw):
        sys.exit(f'{directory / name} 与 SHA256SUMS.txt 不符，停止')
    return raw


def baseline(statement: str, provisions) -> dict:
    text = sc.normalize(statement)
    match = next(sc.REF.finditer(text), None)
    if match is None:
        return {'cited': None, 'level': None, 'exists': None, 'coverage': {}, 'best': None,
                'margin': None, 'flagged': False}
    article, paragraph, item = sc._ref_value(match)
    cited = sc._ref_label(article, paragraph, item)
    if not any(sc._matches(p, article, paragraph, item) for p in provisions):
        return {'cited': cited, 'level': None, 'exists': False, 'coverage': {}, 'best': None,
                'margin': None, 'flagged': True}
    claim = sc._bigrams(sc.REF.sub('', text))
    if paragraph is None:
        level, pool, key = 'article', provisions, (lambda p: (p.article, None, None))
    elif item is None:
        level, pool, key = 'paragraph', [p for p in provisions if p.article == article], (lambda p: (p.article, p.paragraph, None))
    else:
        level = 'item'
        pool = [p for p in provisions if p.article == article and p.paragraph == paragraph and p.item]
        key = (lambda p: (p.article, p.paragraph, p.item))
    scores = {unit: sc._coverage(claim, [p.text for p in pool if key(p) == unit]) for unit in sorted({key(p) for p in pool})}
    mine = (article, paragraph, item)
    margin = scores[mine] - max((v for unit, v in scores.items() if unit != mine), default=0.0)
    return {'cited': cited, 'level': level, 'exists': True,
            'coverage': {sc._ref_label(*unit): round(v, 4) for unit, v in scores.items()},
            'best': sc._ref_label(*max(scores, key=scores.get)),
            'margin': round(margin, 4), 'flagged': margin < 0}


def analyse(name: str) -> dict:
    folder, types = MATERIALS[name]
    run, analysis = ARCHIVE / folder / 'run', ARCHIVE / folder / 'analysis'
    package_raw = verified(run, 'package.json')
    source_raw = verified(run, SOURCE)
    rules_raw = verified(analysis, 'rules.json')
    package, rules = json.loads(package_raw), json.loads(rules_raw)
    checker = sha256(CHECKER.read_bytes())
    if rules['checkerSha256'] != checker:
        sys.exit(f'{folder} 的规则结果来自另一版 statute_check.py，停止')
    unknown = {i['type'] for i in package['items']} - set(types)
    if unknown:
        sys.exit(f'{folder} 有未登记的类型 {sorted(unknown)}，停止')
    provisions = sc.parse_provisions(source_raw.decode('utf-8'))

    items = []
    for item in package['items']:
        rule = rules['items'][item['id']]
        result = baseline(item['statement'], provisions)
        items.append({'id': item['id'], 'type': item['type'], 'expected': item['expected'],
                      'cited': result['cited'], 'level': result['level'], 'exists': result['exists'],
                      'coverage': result['coverage'], 'best': result['best'], 'margin': result['margin'],
                      'baselineFlagged': result['flagged'], 'ruleFlagged': rule['flagged'],
                      'ruleErrorCodes': sorted({f['code'] for f in rule['findings'] if f['severity'] == 'error'})})

    def count(rows):
        return {'n': len(rows), 'rule': sum(r['ruleFlagged'] for r in rows),
                'baseline': sum(r['baselineFlagged'] for r in rows),
                'onlyRule': [r['id'] for r in rows if r['ruleFlagged'] and not r['baselineFlagged']],
                'onlyBaseline': [r['id'] for r in rows if r['baselineFlagged'] and not r['ruleFlagged']]}

    by_type = []
    for kind, (label, group) in types.items():
        rows = [r for r in items if r['type'] == kind]
        margins = [r['margin'] for r in rows if r['margin'] is not None]
        by_type.append({'type': kind, 'label': label, 'group': group, **count(rows),
                        'marginMin': min(margins, default=None), 'marginMax': max(margins, default=None),
                        'noCitation': sum(r['cited'] is None for r in rows)})
    groups = []
    for group in dict.fromkeys(g for _, g in types.values() if g):
        rows = [r for r in items if types[r['type']][1] == group]
        groups.append({'group': group, **count(rows)})

    output = {
        'schemaVersion': 1,
        'material': folder,
        'definition': __doc__.split('\n\n')[2].strip(),
        'inputs': {
            'package': {'path': f'docs/competition/statute-check/{folder}/run/package.json', 'sha256': sha256(package_raw)},
            'source': {'path': f'docs/competition/statute-check/{folder}/run/{SOURCE}', 'sha256': sha256(source_raw)},
            'rules': {'path': f'docs/competition/statute-check/{folder}/analysis/rules.json', 'sha256': sha256(rules_raw)},
        },
        'code': {'script': sha256(Path(__file__).read_bytes()), 'statuteCheck': checker},
        'provisionUnits': len(provisions),
        'groups': groups,
        'byType': by_type,
        'items': items,
    }
    if name == 'pinpoint':
        other = ARCHIVE / SAME_AS_PINPOINT / 'run'
        twin = json.loads(verified(other, 'package.json'))
        fields = ('id', 'type', 'statement', 'expected')
        output['sameMaterial'] = {
            'run': f'docs/competition/statute-check/{SAME_AS_PINPOINT}/run',
            'itemsIdentical': [[i[f] for f in fields] for i in twin['items']] == [[i[f] for f in fields] for i in package['items']],
            'sourceIdentical': sha256(verified(other, SOURCE)) == sha256(source_raw),
        }
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--out', required=True, type=Path)
    out = parser.parse_args().out
    if out.exists() and any(out.iterdir()):
        sys.exit(f'输出目录 {out} 不为空，停止')
    results = {name: analyse(name) for name in MATERIALS}
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, result in results.items():
        raw = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        (out / f'{name}.json').write_bytes(raw)
        lines.append(f'{sha256(raw)}  {name}.json\n')
    (out / 'SHA256SUMS.txt').write_bytes(''.join(sorted(lines, key=lambda s: s.split()[1])).encode('utf-8'))

    for name, result in results.items():
        print(f"== {result['material']}（节选 {result['provisionUnits']} 个单元）")
        for row in result['byType']:
            print(f"   {row['label']:<10s} n={row['n']:2d} 规则={row['rule']:2d} 基线={row['baseline']:2d} "
                  f"余量[{row['marginMin']}, {row['marginMax']}] 无引用={row['noCitation']} "
                  f"仅规则={row['onlyRule']} 仅基线={row['onlyBaseline']}")
        for row in result['groups']:
            print(f"   [{row['group']}] n={row['n']} 规则={row['rule']} 基线={row['baseline']}")
        if 'sameMaterial' in result:
            print('   与 10-22 信息对等运行同材料：', result['sameMaterial'])


if __name__ == '__main__':
    main()
