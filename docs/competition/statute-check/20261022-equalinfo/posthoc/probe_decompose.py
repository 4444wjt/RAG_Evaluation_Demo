"""事后补查（运行之后补做，不在事先锁定的分析口径内）：Ragas 被判不支持的子陈述是否带款级定位。

只读本归档的 run/records.json 与 run/package.json，不发请求。用法：python -X utf8 probe_decompose.py > output.txt
"""
import json
import re
from collections import Counter
from pathlib import Path

R = Path(__file__).resolve().parent.parent / 'run'  # 本归档的 run/
records = json.loads((R / 'records.json').read_text(encoding='utf-8'))
items = {i['id']: i for i in json.loads((R / 'package.json').read_text(encoding='utf-8'))['items']}
# 款级定位：中文「第N款」或英文 paragraph/clause/subparagraph + 序数或编号
PARA = re.compile(r'第[0-9一二三四五六七八九十]+款|(?:paragraph|clause|subparagraph)\s*\(?[0-9ivx]+|(?:first|second|third|fourth|fifth|sixth|\d+(?:st|nd|rd|th))\s+(?:paragraph|clause)', re.I)
PARA_TYPES = {'paragraph_existing', 'paragraph_absent'}
CONTROL_PARA = {'control_pinpoint'}  # 正确款号对照（同样带款号）

for cond in ('R1', 'R2'):
    for judge in ('deepseek', 'qwen72b'):
        tab = Counter()
        for r in records:
            if r['condition'] != cond or r['judge'] != judge or r['status'] != 'ok' or not r['ragas'] or r['ragas'].get('unavailable'):
                continue
            item = items[r['itemId']]
            if item['type'] not in PARA_TYPES | CONTROL_PARA:
                continue
            grp = '款级错误' if item['type'] in PARA_TYPES else '正确款号对照'
            verdicts = r['ragas']['verdicts'] or []
            carries = any(PARA.search(v['statement']) for v in verdicts)
            failed = [v for v in verdicts if v.get('verdict') == 0]
            flagged = r['ragas']['score'] is not None and r['ragas']['score'] < 1
            where = ('不支持的子陈述带款号' if any(PARA.search(v['statement']) for v in failed)
                     else '不支持的子陈述不带款号') if flagged else '未标'
            tab[(grp, '子陈述带款号' if carries else '子陈述无款号', where)] += 1
        print(f'== {cond} {judge}')
        for k in sorted(tab):
            print('   ', ' / '.join(k), tab[k])
