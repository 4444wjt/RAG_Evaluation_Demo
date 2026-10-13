"""Deterministic, offline reports for the independent judge evidence experiment."""
from __future__ import annotations

from collections import Counter, defaultdict
from difflib import unified_diff
from html import escape
import json
from pathlib import Path


VARIANTS = {
    'original': '原始证据', 'repeat': '原样重复', 'shuffle': '顺序变化',
    'removal': '依据移除', 'contradiction': '明确矛盾',
}
VERDICTS = {'supported': '有依据', 'insufficient': '依据不足', 'contradicted': '证据矛盾'}
STATUSES = {
    'ok': '已返回判定', 'call_error': '调用失败', 'parse_error': 'JSON 解析失败',
    'empty_output': '返回空内容', 'invalid_structure': '返回结构异常',
    'incomplete': '请求未完整结束', 'pending': '尚无请求记录',
    'duplicate': '存在重复请求记录',
}
CITATIONS = {'located': '引用已定位', 'missing': '无引用', 'unmatched': '引用未匹配'}
BOUNDARY = (
    '这是固定陈述的裁判行为实验，不是模型排名或总体准确率估计。'
    '引用已定位仅说明文字存在，不证明语义支持。合成证据仅用于本实验；'
    '结果不能证明生产 Trace 流程更准确，也不改变 Quality Gate。'
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def _key(record: dict) -> tuple[str, str, str]:
    return (record.get('sampleId', ''), record.get('variant', ''), record.get('judge', ''))


def _valid(record: dict | None) -> bool:
    result = (record or {}).get('result') or {}
    return bool(record and record.get('status') == 'ok' and result.get('verdict') in VERDICTS)


def _confirmed(variant: dict) -> bool:
    return (variant.get('review') or {}).get('status') == 'confirmed'


def _label(value: object) -> str:
    return VERDICTS.get(value, '未知判定')


def _md(value: object) -> str:
    """Escape source content so it cannot become active HTML or Markdown structure."""
    text = escape(str(value), quote=False)
    for char in ('\\', '`', '*', '_', '[', ']', '#', '|'):
        text = text.replace(char, '\\' + char)
    return text.replace('\r\n', '\n').replace('\n', '<br>')


def _h(value: object) -> str:
    return escape(str(value), quote=True)


def _diff(sample: dict, variant: dict) -> str:
    def lines(contexts: list[str]) -> list[str]:
        return '\n\n'.join(f'【片段 {i + 1}】\n{text}' for i, text in enumerate(contexts)).splitlines()
    return '\n'.join(unified_diff(
        lines(sample['contexts']), lines(variant['contexts']),
        fromfile='原始证据', tofile=VARIANTS.get(variant['id'], variant['id']), lineterm='',
    )) or '与原始证据完全相同。'


def _result_text(record: dict | None, *, duplicate: bool = False) -> tuple[str, list[tuple[str, str]]]:
    if duplicate:
        return STATUSES['duplicate'], [('说明', '无法选择唯一结果，统计中不作为有效判定；完整记录保留在异常记录区。')]
    if record is None:
        return STATUSES['pending'], [('说明', '本报告没有该请求的结果，不将其记为通过或失败。')]
    status = STATUSES.get(record.get('status'), '未知请求状态')
    fields = [('请求编号', str(record.get('requestId') or '未记录')),
              ('实际响应模型', str(record.get('actualModel') or '未记录'))]
    if record.get('status') == 'ok':
        result = record.get('result') or {}
        fields.extend([('机器判定', _label(result.get('verdict'))),
                       ('判定理由', str(result.get('reason') or '未记录')),
                       ('引用检查', CITATIONS.get(result.get('citationStatus'), '引用检查未记录'))])
        quotes = result.get('citations') or []
        for quote in quotes:
            index = quote.get('contextIndex')
            position = f'片段 {index + 1}' if isinstance(index, int) and not isinstance(index, bool) else '位置无效'
            fields.append((position, str(quote.get('quote', ''))))
        if not quotes:
            fields.append(('引用说明', '“依据不足”可以没有引用；“有依据”或“证据矛盾”缺少引用时仍需检查。'))
    elif record.get('error'):
        fields.append(('异常记录', _json(record['error']).strip()))
    fields.append(('用量原记录', _json(record.get('usage')).strip() if record.get('usage') is not None else '未知，不按 0 计'))
    return status, fields


def render_reports(package: dict, records: list[dict], output: Path) -> dict:
    """Write summary.json, README.md and report.html; never mutate inputs or call APIs."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    samples = package.get('samples', [])
    judges = package.get('judges', {})
    indexed: dict[tuple, list] = defaultdict(list)
    for record in sorted(records, key=lambda item: (_key(item), str(item.get('requestId', '')), _json(item))):
        indexed[_key(record)].append(record)
    planned = {
        (sample['id'], variant['id'], judge)
        for sample in samples for variant in sample['variants'] for judge in judges
    }
    unexpected = [record for key, values in indexed.items() if key not in planned for record in values]
    duplicates = [{'sampleId': key[0], 'variant': key[1], 'judge': key[2], 'records': values}
                  for key, values in sorted(indexed.items()) if len(values) > 1]

    def unique(sample_id: str, variant_id: str, judge: str) -> dict | None:
        values = indexed.get((sample_id, variant_id, judge), [])
        return values[0] if len(values) == 1 else None

    def state(key: tuple) -> str:
        values = indexed.get(key, [])
        if len(values) > 1:
            return 'duplicate'
        if not values:
            return 'pending'
        record = values[0]
        return 'invalid_structure' if record.get('status') == 'ok' and not _valid(record) else record.get('status', 'incomplete')

    summary = {
        'schemaVersion': 1, 'experimentVersion': package.get('experimentVersion'),
        'analysisMode': 'offline', 'plannedRequests': len(planned), 'recordCount': len(records),
        'requestStates': dict(sorted(Counter(state(key) for key in planned).items())),
        'unexpectedRecords': unexpected, 'duplicateRecords': duplicates,
        'sourceFiles': package.get('sourceFiles', {}), 'judges': {},
        'limitations': [BOUNDARY, '本报告由工具离线整理，结果解释仍需人工确认。',
                        '未确认材料的预期标签仅为草稿，不纳入预期符合统计。',
                        '一次重复仅显示该次差异，不能估计完整波动分布。'],
    }
    for judge in judges:
        by_variant = {}
        for variant_id in VARIANTS:
            scope = [(sample, variant) for sample in samples for variant in sample['variants'] if variant['id'] == variant_id]
            outcomes = Counter()
            citations = Counter()
            states = Counter()
            expected_valid = expected_matches = human_confirmed = 0
            for sample, variant in scope:
                record = unique(sample['id'], variant_id, judge)
                states[state((sample['id'], variant_id, judge))] += 1
                human_confirmed += int(_confirmed(variant))
                if _valid(record):
                    result = record['result']
                    outcomes[result['verdict']] += 1
                    citations[result.get('citationStatus', 'unknown')] += 1
                    if _confirmed(variant):
                        expected_valid += 1
                        expected_matches += int(result['verdict'] == variant.get('expected'))
            by_variant[variant_id] = {
                'planned': len(scope), 'states': dict(sorted(states.items())),
                'validVerdicts': sum(outcomes.values()), 'verdicts': dict(sorted(outcomes.items())),
                'citations': dict(sorted(citations.items())), 'humanConfirmed': human_confirmed,
                'expectedComparison': {'matches': expected_matches, 'validDenominator': expected_valid},
            }
        paired = {}
        for variant_id in ('repeat', 'shuffle'):
            eligible = [sample for sample in samples if {'original', variant_id}.issubset({v['id'] for v in sample['variants']})]
            valid_pairs = same = 0
            for sample in eligible:
                first = unique(sample['id'], 'original', judge)
                second = unique(sample['id'], variant_id, judge)
                if _valid(first) and _valid(second):
                    valid_pairs += 1
                    same += int(first['result']['verdict'] == second['result']['verdict'])
            paired[variant_id] = {'sameVerdict': same, 'validPairs': valid_pairs,
                                  'unavailablePairs': len(eligible) - valid_pairs, 'plannedPairs': len(eligible)}
        summary['judges'][judge] = {'variants': by_variant, 'pairedConsistency': paired}

    cross_judge = []
    judge_ids = list(judges)
    if len(judge_ids) == 2:
        left, right = judge_ids
        for condition in VARIANTS:
            compared, differences = 0, []
            for sample in samples:
                a, b = unique(sample['id'], condition, left), unique(sample['id'], condition, right)
                if _valid(a) and _valid(b):
                    compared += 1
                    if a['result']['verdict'] != b['result']['verdict']:
                        differences.append({'sampleId': sample['id'], left: a['result']['verdict'], right: b['result']['verdict']})
            cross_judge.append({'variant': condition, 'validPairs': compared, 'unavailablePairs': len(samples) - compared, 'differences': differences})
    summary['crossJudgeComparison'] = cross_judge

    title = '裁判证据敏感性验证'
    pending = summary['requestStates'].get('pending', 0)
    completed = summary['requestStates'].get('ok', 0)
    confirmed = sum(_confirmed(v) for s in samples for v in s['variants'])
    variant_count = sum(len(s['variants']) for s in samples)
    lead = f'{len(samples)} 条固定陈述 · {len(judges)} 个裁判 · {len(planned)} 个计划请求；有效判定 {completed}，缺少结果 {pending}。'
    review_note = f'材料确认：{confirmed}/{variant_count} 个变体；未确认的预期仅为 AI 辅助草稿。'
    md = [f'# {title}', '', lead, '', review_note, '', BOUNDARY, '',
          '> 本页是独立实验材料与离线分析，不表示真实调用已完成。请以各请求状态为准。', '',
          '## 判定与配对统计', '',
          '有效分母仅包含结构有效的返回；调用失败、解析失败、缺失和重复记录单列。预期符合只统计已人工确认的材料。', '',
          '| 裁判 | 条件 | 有效 / 计划 | 已确认材料 | 符合预期 / 有效分母 | 异常及缺失 |',
          '|---|---|---|---|---|---|']
    rows_html = []
    for judge, data in summary['judges'].items():
        model = judges[judge].get('model', judge)
        for variant_id, stats in data['variants'].items():
            comparison = stats['expectedComparison']
            match = f"{comparison['matches']} / {comparison['validDenominator']}" if comparison['validDenominator'] else '暂无可比结果'
            problems = '；'.join(f'{STATUSES.get(status, status)} {count}' for status, count in stats['states'].items() if status != 'ok') or '无'
            cells = [model, VARIANTS[variant_id], f"{stats['validVerdicts']} / {stats['planned']}",
                     str(stats['humanConfirmed']), match, problems]
            md.append('| ' + ' | '.join(_md(cell) for cell in cells) + ' |')
            rows_html.append('<tr>' + ''.join(f'<td>{_h(cell)}</td>' for cell in cells) + '</tr>')
    md.extend(['', '### 原样重复与顺序变化', '', '以下仅表示标签相同，不表示判断正确。'])
    pair_html = []
    for judge, data in summary['judges'].items():
        for variant_id, stats in data['pairedConsistency'].items():
            sentence = (f"{judges[judge].get('model', judge)} · {VARIANTS[variant_id]}："
                        f"相同标签 {stats['sameVerdict']} / 有效配对 {stats['validPairs']}；"
                        f"不可比较 {stats['unavailablePairs']} / 计划配对 {stats['plannedPairs']}。")
            md.extend(['', '- ' + _md(sentence)])
            pair_html.append(f'<li>{_h(sentence)}</li>')
    md.extend(['', '### 两个裁判在哪些条件不同', '', '只列标签差异，不判断哪位裁判获胜。'])
    cross_html = []
    for item in cross_judge:
        sentence = f"{VARIANTS[item['variant']]}：不同标签 {len(item['differences'])} / 有效配对 {item['validPairs']}；不可比较 {item['unavailablePairs']}。"
        if item['differences']:
            sentence += ' 对应陈述：' + '、'.join(d['sampleId'] for d in item['differences']) + '。'
        md.extend(['', '- ' + _md(sentence)])
        cross_html.append(f'<li>{_h(sentence)}</li>')
    md.extend(['', '## 逐条材料与实际结果', ''])
    cards = []
    for sample in samples:
        heading = f"{sample['caseId']} · {sample['id']}"
        source_info = dict(sample.get('source', {}))
        if sample.get('sourceLog') is not None:
            source_info['sourceLog'] = sample['sourceLog']
        rationale = sample.get('rationale') or '未提供选择说明，需人工核查。'
        review_checks = sample.get('reviewChecks') or ['尚未提供逐项检查清单，不能据此自动确认。']
        historical_verdict = sample.get('originalMachineVerdict') or '未记录'
        historical_citation = sample.get('originalCitation') or '未记录'
        md.extend([f'### {_md(heading)}', '', '**问题：** ' + _md(sample['question']), '',
                   '**固定陈述：** ' + _md(sample['statement']), '', '**原始回答（不发给裁判）：** ' + _md(sample['answer']), '',
                   '**来源（含原调用日志定位）：** ' + _md(_json(source_info).strip()), '',
                   '**历史机器判定（不是本次实验结果）：** ' + _md(historical_verdict), '',
                   '**历史引用（不是本次裁判返回）：** ' + _md(historical_citation), '',
                   '**选取理由（AI 辅助整理）：** ' + _md(rationale), '', '**作者待检查事项：**', ''])
        for index, check in enumerate(review_checks, 1):
            md.extend([f'{index}. {_md(check)}', ''])
        variant_html = []
        for variant in sample['variants']:
            variant_id = variant['id']
            name = VARIANTS.get(variant_id, variant_id)
            expected = _label(variant.get('expected'))
            review = variant.get('review') or {}
            expected_note = f'已人工确认的预期：{expected}' if _confirmed(variant) else f'草稿预期（待人工确认）：{expected}'
            synthetic_note = '合成测试材料，不是真实法规' if variant.get('synthetic') is True else ''
            md.extend([f'#### {_md(name)}' + (f' · {synthetic_note}' if synthetic_note else ''), '',
                       _md(expected_note), '', '**证据差异：**', '', _md(_diff(sample, variant)), ''])
            human_fields = [('确认状态', '已确认' if _confirmed(variant) else '待确认'),
                            ('确认者', review.get('reviewer') or '未填写'), ('日期', review.get('reviewedAt') or '未填写'),
                            ('确认说明', review.get('note') or '未填写')]
            for label, value in human_fields:
                md.extend([f'**{label}：** {_md(value)}', ''])
            evidence_html = []
            for number, text in enumerate(variant['contexts'], 1):
                md.extend([f'**片段 {number}：**', '', _md(text), ''])
                evidence_html.append(f'<section class="context"><h5>片段 {number}</h5><p>{_h(text)}</p></section>')
            if not variant['contexts']:
                md.extend(['本变体没有剩余证据片段。', ''])
                evidence_html.append('<p class="muted">本变体没有剩余证据片段。</p>')
            results_html = []
            for judge in judges:
                key = (sample['id'], variant_id, judge)
                status, fields = _result_text(unique(*key), duplicate=len(indexed.get(key, [])) > 1)
                model = judges[judge].get('model', judge)
                md.extend([f'**裁判：{_md(model)} · {_md(status)}**', ''])
                for label, value in fields:
                    md.extend([f'- {_md(label)}：{_md(value)}', ''])
                results_html.append(f'<section class="judge"><h5>{_h(model)}</h5><p class="status">{_h(status)}</p><dl>' +
                                    ''.join(f'<dt>{_h(k)}</dt><dd>{_h(v)}</dd>' for k, v in fields) + '</dl></section>')
            variant_html.append(
                f'<details class="variant"><summary>{_h(name)}'
                + (f'<span class="synthetic">{_h(synthetic_note)}</span>' if synthetic_note else '')
                + f'<span>{_h(expected_note)}</span></summary>'
                + '<div class="inner">'
                + (f'<h4 class="synthetic">{_h(synthetic_note)}</h4>' if synthetic_note else '')
                + f'<dl>{"".join(f"<dt>{_h(k)}</dt><dd>{_h(v)}</dd>" for k, v in human_fields)}</dl>'
                f'<h4>证据差异</h4><pre>{_h(_diff(sample, variant))}</pre>'
                f'<h4>本次提供的全部证据</h4>{"".join(evidence_html)}'
                f'<h4>实际请求与判定</h4><div class="judges">{"".join(results_html)}</div></div></details>'
            )
        cards.append(
            f'<article class="sample"><header><p class="eyebrow">{_h(heading)}</p><h3>{_h(sample["question"])}</h3></header>'
            f'<div class="claim"><span>固定陈述</span><p>{_h(sample["statement"])}</p></div>'
            f'<div class="inner"><h4>选取理由 · AI 辅助整理</h4><p>{_h(rationale)}</p><h4>作者待检查事项</h4>'
            f'<ol>{"".join(f"<li>{_h(check)}</li>" for check in review_checks)}</ol></div>'
            f'<details><summary>原始回答与来源</summary><div class="inner"><p>{_h(sample["answer"])}</p>'
            f'<h4>历史机器判定 · 不是本次实验结果</h4><p>{_h(historical_verdict)}</p>'
            f'<h4>历史引用 · 不是本次裁判返回</h4><p>{_h(historical_citation)}</p>'
            f'<h4>来源与原调用日志定位</h4><pre>{_h(_json(source_info))}</pre></div></details>{"".join(variant_html)}</article>'
        )

    extra = {'skipped': package.get('skipped', []), 'unexpectedRecords': unexpected, 'duplicateRecords': duplicates}
    md.extend(['## 选择范围、异常记录与校验来源', '', '**跳过材料与未归属记录：**', '', _md(_json(extra)), '',
               '**来源文件 SHA-256：**', '', _md(_json(package.get('sourceFiles', {}))), '',
               '## 费用与解释边界', '',
               '用量缺失记为未知。费用必须结合另行确认的供应商单价估算；此报告不表示实际扣费，也没有供应商费用自动封顶能力。', '',
               '拒绝将定向样本的结果解释为总体准确率、独立认证或严格公平的模型排名。原始回答、五项指标与门禁不因本实验改写。', ''])
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{title} · 鉴真全域 VeriScope AI</title><style>
:root{{color-scheme:dark;--bg:#0b0e12;--surface:#141a21;--line:#2a323d;--gold:#F2C879;--text:#edf0f4;--muted:#abb4c1}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.75 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:1440px;margin:auto;padding:56px 48px 80px}}h1,h2,h3,h4,h5,p{{margin:0}}h1{{font-size:48px;line-height:1.25;margin:12px 0 24px}}h2{{font-size:28px;margin:40px 0 16px}}h3{{font-size:22px;line-height:1.5}}h4{{font-size:18px;margin:24px 0 12px}}h5{{font-size:16px}}
p,pre,dd,td,summary,li{{overflow-wrap:anywhere;word-break:break-word}}p,dd{{white-space:pre-wrap}}p+p{{margin-top:12px}}.muted,.boundary{{color:var(--muted)}}.eyebrow,.status,.claim>span{{color:var(--gold)}}.eyebrow{{font-size:14px;margin-bottom:8px}}.boundary{{border-left:3px solid var(--gold);padding:12px 20px;margin:24px 0;background:#11161c}}
table{{border-collapse:collapse;width:100%;table-layout:fixed;font-size:14px}}th,td{{border-bottom:1px solid var(--line);padding:14px 12px;text-align:left;vertical-align:top}}th{{color:var(--muted);font-weight:600}}th:first-child{{width:23%}}th:last-child{{width:23%}}.sample{{margin-top:28px;border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow:hidden}}.sample>header{{padding:28px 28px 16px}}.claim{{margin:0 28px 24px;padding:16px 20px;border-left:2px solid var(--gold);background:#10151b}}.claim>span{{font-size:13px}}.claim>p{{font-size:18px;margin-top:8px}}
details{{border-top:1px solid var(--line)}}summary{{cursor:pointer;padding:16px 28px;font-size:16px;font-weight:600}}summary:hover{{background:#1b222b}}summary:focus-visible{{outline:2px solid var(--gold);outline-offset:-4px}}summary>span{{display:block;color:var(--muted);font-size:14px;font-weight:400;margin-left:19px}}.inner{{padding:8px 28px 28px;min-width:0}}pre{{font:13px/1.8 ui-monospace,Consolas,monospace;white-space:pre-wrap;background:#0f141a;border:1px solid var(--line);border-radius:6px;padding:16px;margin:12px 0;max-width:100%}}
dl{{margin:0}}dt{{color:var(--muted);font-size:13px;margin-top:12px}}dd{{margin:3px 0 0;font-size:14px}}.context{{margin:12px 0;padding:16px 20px;border:1px solid var(--line);border-radius:6px}}.context>p{{margin-top:8px;font-size:14px}}.judges{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}}.judge{{min-width:0;border:1px solid var(--line);padding:20px;border-radius:6px}}.judge>h5{{overflow-wrap:anywhere}}.status{{font-size:14px;margin-top:8px}}footer{{margin-top:40px;color:var(--muted);font-size:14px;border-top:1px solid var(--line);padding-top:20px}}ul,ol{{padding-left:24px}}.synthetic,summary>span.synthetic{{color:var(--gold)}}
</style></head><body><main>
<header><p class="eyebrow">鉴真全域 VeriScope AI · 独立实验</p><h1>{title}</h1><p>{_h(lead)}</p><p class="muted">{_h(review_note)}</p></header>
<aside class="boundary">{_h(BOUNDARY)}<p>本报告由工具离线整理，结果解释仍需人工确认。是否完成真实调用，以各请求状态为准。</p></aside>
<h2>判定与配对统计</h2><p class="muted">有效分母只计结构有效返回；未人工确认的草稿不计算预期符合数量。</p>
<table><thead><tr><th>裁判</th><th>条件</th><th>有效 / 计划</th><th>已确认材料</th><th>符合预期 / 有效分母</th><th>异常及缺失</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table>
<h4>原样重复与顺序变化</h4><p class="muted">仅比较标签一致性，不表示判定正确。</p><ul>{''.join(pair_html)}</ul>
<h4>两个裁判在哪些条件不同</h4><p class="muted">标签差异是待解释现象，不代表裁判排名。</p><ul>{''.join(cross_html)}</ul>
<h2>逐条材料与实际结果</h2><p class="muted">先读固定陈述，再展开证据变体。引用定位与语义支持分别检查。</p>{''.join(cards)}
<h2>选择范围、异常与文件校验</h2><details><summary>跳过材料与未归属记录</summary><div class="inner"><pre>{_h(_json(extra))}</pre></div></details>
<details><summary>来源文件 SHA-256</summary><div class="inner"><pre>{_h(_json(package.get('sourceFiles', {})))}</pre></div></details>
<footer><p>用量缺失为未知，不按 0 计。费用需按经确认的供应商单价估算，不代表实际扣费；本工具不提供供应商费用自动封顶。</p><p>定向材料不代表总体准确率、独立认证或模型排名。一次重复不能估计完整波动分布。原始回答、指标与门禁保持原样。</p></footer>
</main></body></html>'''
    (output / 'summary.json').write_text(_json(summary), encoding='utf-8', newline='\n')
    (output / 'README.md').write_text('\n'.join(md), encoding='utf-8', newline='\n')
    (output / 'report.html').write_text(html + '\n', encoding='utf-8', newline='\n')
    return summary
