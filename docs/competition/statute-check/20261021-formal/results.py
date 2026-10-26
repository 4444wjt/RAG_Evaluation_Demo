"""按运行前确认的口径（run/package.json 的 review.note）把 analysis/analysis.json 分组计数。

只用标准库，不联网、不调用模型。用法（在本目录执行）：
    python results.py          # 重新计算并与 results.json 比对，一致时输出 OK
    python results.py --write  # 重新生成 results.json
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
METHODS = ('rules', 'deepseek', 'qwen72b')
JUDGES = ('deepseek', 'qwen72b')

# 口径第 2–4 项：按类型划定各组正例与反例。
GROUPS = {
    'content': {'title': '主指标（内容 faithfulness）',
                'positive': ('quantity_value', 'quantity_unit', 'polarity'), 'negative': ('control',)},
    'citation': {'title': '条号检测',
                 'positive': ('article_swap', 'article_absent', 'paragraph_shift'), 'negative': ('control', 'control_paragraph')},
    'modal': {'title': '「应当」改「可以」（口径依赖项，单列）', 'positive': ('modal',), 'negative': ()},
}
# 口径第 5 项：AI 预检标为存疑的 8 条口径依赖项。
CALIBER_DEPENDENT = ('QV-S27', 'QU-S50', 'AS-S32', 'MD-S13', 'MD-S27', 'MD-S41', 'MD-S49', 'MD-S50')
# 本规则覆盖的内容错误类型；「否定改肯定」不在覆盖范围内。
RULE_COVERED_CONTENT = ('quantity_value', 'quantity_unit')


def detected(row, method):
    """规则：flagged；裁判：判为 contradicted 或 insufficient（即不被支持）。"""
    return row['rules']['flagged'] if method == 'rules' else row['judges'][method] != 'supported'


def count(rows, positive_types, negative_types, exclude=()):
    pos = [r for r in rows if r['type'] in positive_types and r['id'] not in exclude]
    neg = [r for r in rows if r['type'] in negative_types and r['id'] not in exclude]
    out = {'positives': len(pos), 'negatives': len(neg), 'methods': {}, 'unions': {}}
    for m in METHODS:
        out['methods'][m] = {
            'detected': sum(detected(r, m) for r in pos),
            'falsePositives': sum(detected(r, m) for r in neg),
            'missed': [r['id'] for r in pos if not detected(r, m)],
            'falsePositiveIds': [r['id'] for r in neg if detected(r, m)],
        }
    for j in JUDGES:
        out['unions'][f'rules+{j}'] = {
            'detected': sum(detected(r, 'rules') or detected(r, j) for r in pos),
            'falsePositives': sum(detected(r, 'rules') or detected(r, j) for r in neg),
        }
    return out


def build():
    analysis = json.loads((HERE / 'analysis' / 'analysis.json').read_text(encoding='utf-8'))
    records = json.loads((HERE / 'run' / 'records.json').read_text(encoding='utf-8'))
    rows = analysis['rows']
    usage = {}
    for j in JUDGES:
        recs = [r for r in records if r['judge'] == j]
        usage[j] = {'requests': len(recs), 'ok': sum(r['status'] == 'ok' for r in recs),
                    'actualModels': sorted({r['actualModel'] for r in recs}),
                    'promptTokens': sum((r.get('usage') or {}).get('prompt_tokens', 0) for r in recs),
                    'completionTokens': sum((r.get('usage') or {}).get('completion_tokens', 0) for r in recs)}
    covered = [r for r in rows if r['type'] in RULE_COVERED_CONTENT]
    return {
        'protocol': '口径见 run/package.json 的 review.note（运行前确定）；本文件只按该口径对 analysis/analysis.json 计数。',
        'detectionDefinition': {'rules': 'rules.flagged 为 true',
                                'judges': '裁判判定为 contradicted 或 insufficient（不被支持）；supported 记为未检出'},
        'groups': {key: {'title': g['title'], 'positiveTypes': list(g['positive']), 'negativeTypes': list(g['negative']),
                         **count(rows, g['positive'], g['negative'])} for key, g in GROUPS.items()},
        'ruleCoveredContent': {'types': list(RULE_COVERED_CONTENT), 'positives': len(covered),
                               'rulesDetected': sum(detected(r, 'rules') for r in covered)},
        'sensitivity': {'excluded': list(CALIBER_DEPENDENT),
                        'groups': {key: count(rows, g['positive'], g['negative'], CALIBER_DEPENDENT) for key, g in GROUPS.items()}},
        'judgeVerdicts': {j: dict(sorted(Counter(r['judges'][j] for r in rows).items())) for j in JUDGES},
        'usage': usage,
    }


def main():
    text = json.dumps(build(), ensure_ascii=False, indent=2) + '\n'
    target = HERE / 'results.json'
    if '--write' in sys.argv[1:]:
        target.write_text(text, encoding='utf-8', newline='\n')
        print('written')
    else:
        same = target.exists() and target.read_text(encoding='utf-8') == text
        print('OK' if same else 'MISMATCH')
        sys.exit(0 if same else 1)


if __name__ == '__main__':
    main()
