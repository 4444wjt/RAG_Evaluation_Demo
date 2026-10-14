"""从冻结证据生成工作台「门禁审计」页的数据：门禁可信度四问的结论，以及门禁代码自检的结果。

每一项都从仓库里已归档的文件重新计算或读取，并核对文件哈希；任何一项与归档对不上就报错退出，不生成文件。
证据目录只读，不改动其中任何文件，也不调用模型。

  ① 能否复算：由逐句判定重算 25 题正式运行的门禁，再用正式的 check_quality_gate 离线回放；
  ② 换裁判：独立裁判对照的 summary.json（原裁判重判、Qwen2.5-72B）；
  ③ 证据敏感性：裁判证据敏感性实验的离线分析 summary.json；
  ④ 结论确定性：gate_uncertainty 按题重抽；
  自检：tests/test_gate_properties.py 中每条性质测试的说明，以及 docs/competition/gate-self-check 的变异测试归档；
  回归对比：两次同题 5 题预跑，按 backend/core/regression.py 逐题对比；
  失败归因：25 题正式运行，按 backend/core/attribution.py 逐题判断问题出在检索还是生成；
  外部基准：docs/competition/external-validation 的汇总须能由同目录逐条判定重新算出；
  裁判判错校准：未标注时的敏感性分析（由逐句判定重算），模拟归档须与当前题目结构和裁判误差设定一致，
  盲标注材料须与证据重新生成的一致；人工标注完成前状态为「待标注」。

  python tools/competition/build_audit_fixture.py            写出 frontend/src/fixtures/gate-audit.json
  python tools/competition/build_audit_fixture.py --check    只校验已提交的文件是否与证据重新生成的结果一致
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import attribution, regression  # noqa: E402
from scripts import gate_replay  # noqa: E402
from scripts.mutation_summary import summarize  # noqa: E402
from tools.competition import build_replay_fixture as replay  # noqa: E402
from tools.competition import external_validation  # noqa: E402
from tools.competition import gate_uncertainty as uncertainty  # noqa: E402
from tools.competition import judge_calibration  # noqa: E402
from tools.competition import judge_error_simulation as simulation  # noqa: E402
from tools.competition.run_evidence import sha256, write_json  # noqa: E402

OUTPUT = ROOT / 'frontend' / 'src' / 'fixtures' / 'gate-audit.json'
EVIDENCE_ROOT = ROOT / 'docs' / 'competition' / 'evidence'
JUDGE_COMPARISON = ROOT / 'docs' / 'competition' / 'judge-comparison' / '20261011-qwen72b'
SENSITIVITY = ROOT / 'docs' / 'competition' / 'judge-sensitivity' / 'analysis-20261012'
SELF_CHECK = ROOT / 'docs' / 'competition' / 'gate-self-check'
EXTERNAL = ROOT / 'docs' / 'competition' / 'external-validation'
CALIBRATION = ROOT / 'docs' / 'competition' / 'judge-calibration'
# 非作者人工标注的归档：标注原件、分析结果与哈希清单
ANNOTATION = CALIBRATION / '20261014-annotation-V'
# ANAH 可抽样句子的标签分布（prepare 的候选池，见外部验证 README），用于把等量抽样的精确率折算到实际比例
ANAH_POOL = {'none': 1406, 'contradictory': 531, 'unverifiable': 565}
PROPERTIES = ROOT / 'tests' / 'test_gate_properties.py'
GATE = ROOT / 'backend' / 'core' / 'gate.py'
REGRESSION_PAIR = ('20261005T101847Z-pilot-c72cb679', '20261005T131353Z-pilot-35f623cb')
JUDGES = (('original', '原判定'), ('self-rerun', '原裁判重判'), ('independent', 'Qwen2.5-72B'))
SENSITIVITY_JUDGES = (('deepseek', 'DeepSeek'), ('qwen72b', 'Qwen2.5-72B'))
VARIANTS = (('original', '原始证据', '支持'), ('repeat', '原样重复', '支持'), ('shuffle', '打乱片段顺序', '支持'),
            ('removal', '移除支持片段', '依据不足'), ('contradiction', '改成明确矛盾', '矛盾'))


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _archived(folder: Path, name: str) -> tuple[dict, dict]:
    """读取归档 JSON，并核对它与同目录 SHA256SUMS.txt 的记录一致；返回内容与来源记录。"""
    digest = sha256(folder / name)
    if replay._expected_hashes(folder).get(name) != digest:
        raise ValueError(f'{_rel(folder / name)} 的哈希与 SHA256SUMS.txt 不一致。')
    return json.loads((folder / name).read_text(encoding='utf-8')), {'path': _rel(folder / name), 'sha256': digest}


def _source_hash(path: Path) -> str:
    """源代码按 LF 计算哈希，使 Windows 按 CRLF 检出时与记录一致。"""
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def recompute() -> dict:
    fixture = replay.build()  # 已核对证据哈希，并逐题核对逐句判定与归档 faithfulness 一致
    rows, gate, thresholds = fixture['rows'], fixture['gate'], fixture['thresholds']
    mean = sum(row['supported'] / row['total'] for row in rows) / len(rows)
    recomputed = mean >= thresholds['faithfulness'] and gate['posContextPrecision'] >= thresholds['context_precision']
    response = json.loads((replay.EVIDENCE / 'api-response.json').read_text(encoding='utf-8'))
    with contextlib.redirect_stdout(io.StringIO()):
        replayed = gate_replay.replay(response, thresholds['faithfulness'], thresholds['context_precision'])['gate_passed']
    if not (round(mean, 4) == gate['posFaithfulness'] and recomputed == replayed == gate['passed']):
        raise ValueError('重算或回放的门禁结论与归档不一致。')
    return {
        'source': fixture['source'], 'thresholds': thresholds,
        'questions': len(rows), 'statements': sum(row['total'] for row in rows),
        'supported': sum(row['supported'] for row in rows), 'refusals': sum(row['refusal'] for row in rows),
        'recomputedFaithfulness': mean, 'recordedFaithfulness': gate['posFaithfulness'],
        'recordedContextPrecision': gate['posContextPrecision'],
        'recordedPassed': gate['passed'], 'recomputedPassed': recomputed, 'replayedPassed': replayed,
    }


def judges() -> dict:
    summary, source = _archived(JUDGE_COMPARISON, 'summary.json')
    full = summary['fullRun']
    noise = {metric: {'sameJudge': full['vsOriginal']['self-rerun'][metric]['meanAbsDiff'],
                      'crossJudge': full['vsOriginal']['independent'][metric]['meanAbsDiff'],
                      'crossingSameJudge': full['vsOriginal']['self-rerun'][metric]['crossing0.8'],
                      'crossingCrossJudge': full['vsOriginal']['independent'][metric]['crossing0.8']}
             for metric in ('faithfulness', 'context_precision')}
    if noise['faithfulness']['sameJudge'] != regression.NOISE_FLOOR['pos_faithfulness'] \
            or noise['context_precision']['sameJudge'] != regression.NOISE_FLOOR['pos_context_precision']:
        raise ValueError('回归对比的噪声底线与独立裁判对照的实测值不一致。')
    return {'source': source, 'questions': full['rows'],
            'runs': [{'key': key, 'label': label, **full['gate'][key]} for key, label in JUDGES],
            'noise': noise}


def sensitivity() -> dict:
    summary, source = _archived(SENSITIVITY, 'summary.json')
    cells = []
    for judge, judge_label in SENSITIVITY_JUDGES:
        for variant, label, expected in VARIANTS:
            item = summary['judges'][judge]['variants'][variant]
            cells.append({'judge': judge, 'judgeLabel': judge_label, 'variant': variant, 'variantLabel': label,
                          'expected': expected, 'planned': item['planned'], 'valid': item['validVerdicts'],
                          'matches': item['expectedComparison']['matches'],
                          'parseErrors': item['states'].get('parse_error', 0)})
    totals = {'planned': sum(c['planned'] for c in cells), 'valid': sum(c['valid'] for c in cells),
              'matches': sum(c['matches'] for c in cells), 'parseErrors': sum(c['parseErrors'] for c in cells)}
    if totals['planned'] != summary['plannedRequests'] or totals['parseErrors'] != summary['requestStates'].get('parse_error', 0):
        raise ValueError('证据敏感性汇总与分析结果不一致。')
    return {'source': source, 'statements': cells[0]['planned'], 'cells': cells, 'totals': totals}


def certainty() -> dict:
    report = uncertainty.analyze()
    pooled = report['faithfulnessViews']['pooledStatements']
    return {'seed': report['seed'], 'resamples': report['resamples'], 'thresholds': report['thresholds'],
            'metrics': report['metrics'],
            'views': {'perQuestionRefusalAsZero': report['faithfulnessViews']['perQuestionRefusalAsZero']['value'],
                      'pooledStatements': {'value': pooled['value'], 'wilson95': pooled['wilson95']},
                      'perQuestionRefusalExcluded': report['faithfulnessViews']['perQuestionRefusalExcluded']['value']}}


def properties() -> dict:
    tree = ast.parse(PROPERTIES.read_text(encoding='utf-8'))
    items = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
            doc = ast.get_docstring(node)
            if not doc:
                raise ValueError(f'性质测试 {node.name} 缺少一句说明。')
            items.append({'test': node.name, 'description': doc})
    return {'path': _rel(PROPERTIES), 'items': items}


def mutation() -> dict:
    manifest = json.loads((SELF_CHECK / 'manifest.json').read_text(encoding='utf-8'))
    gate_hash = _source_hash(GATE)
    if gate_hash != manifest['targetSha256']:
        raise ValueError('backend/core/gate.py 已改动，变异测试归档不再对应当前代码，请重新运行变异测试并更新归档。')
    report = SELF_CHECK / manifest['report']
    cases, killed, survived, timeouts = summarize(report)
    notes = manifest['survivors']
    if sorted(case.get('name') for case in survived) != sorted(notes):
        raise ValueError('存活变异与 manifest.json 中的人工说明不对应。')
    survivors = []
    for case in survived:
        changed = [line[1:] for line in case.findtext('failure').splitlines() if line.startswith('+') and not line.startswith('+++')]
        survivors.append({'name': case.get('name'), 'line': int(case.get('line')),
                          'original': case.findtext('system-out').strip(), 'mutated': changed[0].strip(),
                          'note': notes[case.get('name')]})
    status = lambda case: 'survived' if case.find('failure') is not None else 'timeout' if case.find('error') is not None else 'killed'
    return {
        'tool': manifest['tool'], 'date': manifest['date'], 'environment': manifest['environment'],
        'target': manifest['target'], 'targetSha256': gate_hash,
        'disabledMutationTypes': manifest['disabledMutationTypes'], 'tests': manifest['tests'],
        'source': {'path': _rel(report), 'sha256': sha256(report),
                   'manifest': {'path': _rel(SELF_CHECK / 'manifest.json'), 'sha256': sha256(SELF_CHECK / 'manifest.json')}},
        'total': len(cases), 'killed': killed, 'survived': len(survived), 'timeouts': len(timeouts),
        'mutants': [{'id': int(re.search(r'\d+', case.get('name')).group(0)), 'line': int(case.get('line')), 'status': status(case)}
                    for case in cases],
        'survivors': survivors,
    }


def regression_example() -> dict:
    loaded, sources = [], []
    for run_id in REGRESSION_PAIR:
        folder = EVIDENCE_ROOT / run_id
        result, source = _archived(folder, 'api-response.json')
        loaded.append(result)
        sources.append({'runId': run_id, **source})
    baseline, candidate = loaded
    comparison = regression.compare(baseline, candidate)
    metrics = [{'key': m['key'], 'label': m['label'], 'baseline': m['baseline_mean'], 'candidate': m['candidate_mean'],
                'paired': m['paired'], 'meanDelta': m['mean_delta'], 'interval': list(m['interval']),
                'noise': m['noise'], 'status': m['status'], 'oneSided': len(m['one_sided'])}
               for m in comparison['metrics']]
    return {'baseline': sources[0], 'candidate': sources[1],
            'baselinePassed': baseline['gate_passed'], 'candidatePassed': candidate['gate_passed'],
            'resamples': regression.BOOTSTRAP_RESAMPLES, 'seed': regression.BOOTSTRAP_SEED,
            'confirmed': comparison['confirmed'], 'metrics': metrics}


def attribution_example() -> dict:
    folder = EVIDENCE_ROOT / replay.RUN_ID
    result, source = _archived(folder, 'api-response.json')
    report = attribution.attribute(result)
    rows = result['model_dfs'][report['model']]
    flagged = [{**{k: q[k] for k in ('index', 'question', 'category', 'stage', 'faithfulness', 'context_recall', 'context_precision')},
                'answer': str(rows[q['index']].get('answer', '')), 'groundTruth': str(rows[q['index']].get('ground_truth', ''))}
               for q in report['questions'] if q['stage']]
    return {'source': {'runId': replay.RUN_ID, **source}, 'model': report['model'], 'questions': len(report['questions']),
            'counts': report['counts'], 'faithfulnessLoss': report['faithfulnessLoss'],
            'retrievalLoss': report['retrievalLoss'], 'generationLoss': report['generationLoss'],
            'primaryStage': report['primaryStage'], 'flagged': flagged, 'advice': report['advice'],
            'floors': {'recall': attribution.RECALL_FLOOR, 'precision': attribution.PRECISION_FLOOR,
                       'faithfulness': attribution.FAITH_FLOOR}}


def external() -> dict:
    summary, summary_source = _archived(EXTERNAL, 'summary.json')
    items, items_source = _archived(EXTERNAL, 'items.json')
    auth, auth_source = _archived(EXTERNAL, 'authorization.json')
    if not items['packageSha256'] == summary['packageSha256'] == auth['packageSha256']:
        raise ValueError('外部验证的汇总、逐条判定与授权绑定的样本包哈希不一致。')
    if (external_validation.analyze_anah(items['anah']) != summary['anah']
            or external_validation.analyze_rgb(items['rgb']) != summary['rgb']):
        raise ValueError('外部验证汇总不能由逐条判定重新算出。')
    answered = [i for i in items['rgb'] if i['condition'] == 'counterfactual' and 'refused' in i and not i['refused']]
    only_fake = [i for i in answered if i['adoptedFake'] and not i['correct']]
    # 反事实条件按字符串细分：只给错误答案 / 正误并现（含数据特例，见归档 README）/ 只给正确答案
    counterfactual = {'answered': len(answered), 'onlyFake': len(only_fake),
                      'onlyFakeFlagged': sum(bool(i['flagged']) for i in only_fake),
                      'both': sum(1 for i in answered if i['adoptedFake'] and i['correct']),
                      'correctOnly': sum(1 for i in answered if i['correct'] and not i['adoptedFake'])}
    binary = summary['anah']['binary']
    prevalence = (ANAH_POOL['contradictory'] + ANAH_POOL['unverifiable']) / sum(ANAH_POOL.values())
    hit, false_alarm = binary['recall'] * prevalence, (1 - binary['specificity']) * (1 - prevalence)
    adjusted = {'pool': ANAH_POOL, 'prevalence': round(prevalence, 4), 'precision': round(hit / (hit + false_alarm), 4)}
    return {'sources': summary['sources'], 'model': summary['model'], 'returnedModels': summary['returnedModels'],
            'requests': summary['requests'], 'requestErrors': summary['requestErrors'], 'usage': summary['usage'],
            'budgetCny': auth['budgetCny'], 'anah': summary['anah'], 'rgb': summary['rgb'], 'counterfactual': counterfactual,
            'adjusted': adjusted,
            'source': {'summary': summary_source, 'items': items_source, 'authorization': auth_source}}


def human_annotation(package: dict) -> dict:
    """读取归档的人工标注分析，核对哈希，并用归档的标注原件重算，与归档逐字段一致才写入。"""
    analysis, analysis_source = _archived(ANNOTATION, 'analysis.json')
    files = sorted(ANNOTATION.glob('annotation-*.json'))
    sources = [_archived(ANNOTATION, path.name)[1] for path in files]
    if not files or json.loads(json.dumps(judge_calibration.analyze(files), ensure_ascii=False)) != analysis:
        raise ValueError('人工标注分析不能由归档的标注文件重新算出。')
    if analysis['packageId'] != package['packageId']:
        raise ValueError('人工标注所用的材料与当前盲标注材料不一致。')
    texts = {item['id']: item['statement'] for item in package['items']}
    rows = analysis['rows']
    pick = lambda row: {'id': row['id'], 'caseId': row['caseId'], 'statement': row['statement'],  # noqa: E731
                        'refusal': row['refusal'], 'text': texts[row['id']]}
    estimate = analysis['estimate']
    return {'status': 'done', 'statements': len(package['items']), 'questions': len(package['questions']),
            'packageId': package['packageId'], 'annotators': [a['annotator'] for a in analysis['annotators']],
            'counts': analysis['counts'], 'agreement': analysis['agreement'], 'judgeValue': analysis['judge']['value'],
            'calibration': analysis['calibration'],
            'estimate': {key: estimate[key] for key in ('method', 'point', 'interval', 'state', 'pointPasses', 'reason')},
            'unresolved': [pick(row) for row in rows if row['state'] == 'unresolved'],
            'disagreements': [pick(row) for row in rows if row['disagreesWithJudge']],
            'humanNotSupported': [pick(row) for row in rows if row['human'] == 0],
            'source': {'analysis': analysis_source, 'annotations': sources}}


def calibration() -> dict:
    report = judge_calibration.status()
    sim, sim_source = _archived(CALIBRATION, 'simulation.json')
    if sim['structure'] != simulation.formal_structure() or sim['judgeRates'] != simulation.judge_rates():
        raise ValueError('模拟归档的题目结构或裁判误差设定与当前证据不一致。')
    package, _ = judge_calibration.build_package()
    if json.loads(judge_calibration.PACKAGE.read_text(encoding='utf-8')) != package:
        raise ValueError('盲标注材料与证据重新生成的结果不一致。')
    coverage = {}
    for scenario in sim['settings']['scenarios']:
        cells = [cell for cell in sim['cells'] if cell['scenario'] == scenario]
        coverage[scenario] = {name: [min(c['estimators'][name]['coverage'] for c in cells),
                                     max(c['estimators'][name]['coverage'] for c in cells)]
                              for name in simulation.ESTIMATORS}
    naive_fail = [c['estimators']['naive']['decisions']['确定不通过'] for c in sim['cells']
                  if c['scenario'] == 'invariant' and c['theta'] == 0.9]
    return {'judgeValue': report['judgeValue'], 'threshold': report['threshold'], 'statements': report['statements'],
            'judgedSupported': report['judgedSupported'], 'questions': report['questions'],
            'flips': report['flips'], 'tipping': report['tipping'],
            'simulation': {'replicates': sim['replicates'], 'cells': len(sim['cells']), 'judgeRates': sim['judgeRates'],
                           'calibrations': sim['settings']['calibrations'], 'thetas': sim['settings']['thetas'],
                           'estimators': sim['estimators'], 'coverage': coverage,
                           'naiveFailWhenPassing': [min(naive_fail), max(naive_fail)], 'source': sim_source},
            'annotation': human_annotation(package)}


def build() -> dict:
    return {'schemaVersion': 1, 'recompute': recompute(), 'judges': judges(), 'sensitivity': sensitivity(),
            'certainty': certainty(), 'properties': properties(), 'mutation': mutation(),
            'regression': regression_example(), 'attribution': attribution_example(), 'external': external(),
            'calibration': calibration()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='只校验，不写文件')
    args = parser.parse_args()
    fixture = build()
    if args.check:
        saved = json.loads(OUTPUT.read_text(encoding='utf-8'))
        if saved != fixture:
            print('gate-audit.json 与证据重新生成的结果不一致。', file=sys.stderr)
            return 1
        print('一致。')
        return 0
    write_json(OUTPUT, fixture)
    mutation_result = fixture['mutation']
    print(f'已写出 {OUTPUT.relative_to(ROOT)}：{len(fixture["properties"]["items"])} 条性质，'
          f'{mutation_result["total"]} 个变异抓住 {mutation_result["killed"]} 个。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
