"""从两次 25 题正式运行的冻结原始响应，提取「修复验证」四步导览与复核卡所用的 F03、F04 逐字证据。

只读归档，不调用模型，不改任何已有文件：

  - 用到的每个文件先按所在归档的 SHA256SUMS.txt 核对，哈希写入输出，页面与复核卡据此标注来源；
  - 题目按基线 analysis.json 的题号定位，再按问题文本在两份 api-response.json 里各找唯一一行；
  - 回答、检索上下文（顺序不变）与逐句判定原样拷贝，不改写、不补判定；原记录为空的逐句判定保持为空；
  - 高亮片段必须出自该题在 analysis.json 登记的原文依据（sourceEvidence），且在检索上下文里只出现一次；
  - 两题的回答与逐句判定条数，须与 fix-verification.json 的逐题记录一致。

任何一项对不上（来源哈希不符、题目缺失、匹配不唯一）就报错退出，不生成文件。

  python tools/competition/build_fix_case.py            写出 frontend/src/fixtures/fix-case.json
  python tools/competition/build_fix_case.py --check    只读校验：已有文件与证据重新提取的结果一致
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.competition.run_evidence import sha256, write_json  # noqa: E402

EVIDENCE = ROOT / 'docs' / 'competition' / 'evidence'
BASELINE = EVIDENCE / '20261005T144504Z-full-39511424'
FIXED = EVIDENCE / '20261014T073859Z-full-1c6f091f'
FIX_FIXTURE = ROOT / 'frontend' / 'src' / 'fixtures' / 'fix-verification.json'
OUTPUT = ROOT / 'frontend' / 'src' / 'fixtures' / 'fix-case.json'
MODEL = 'deepseek-chat'
# 页面与复核卡的来源链接固定到这次已核对的公开提交，分支移动后链接不跟着变。
PINNED = {'repository': 'https://github.com/4444wjt/RAG_Evaluation_Demo',
          'commit': '15896e4ef216a8abe0cb953c0daf4d311970381a'}
# 按该固定提交的 git cat-file blob 原始字节独立核对。与归档检出文件的
# sha256 分开：baseline analysis.json 在 Git 对象中为 LF，归档检出为 CRLF。
# 固定映射让归档副本也能 --check，不要求本机 Git、完整历史或联网。
GIT_BLOB_SHA256 = {
    'baseline': {
        'api-response.json': '769865042388b9020f8d70fe212af5066e221eb812e194d460a3103ccafadb9d',
        'analysis.json': 'bc21c4de408e0d5e7dc19698a21ede494332aa843d322e032c4c3ae0066fcdce',
    },
    'fixed': {
        'api-response.json': '482c390ce8314f738f50904e6bb7b72238036f72589b3bb73c178f125663dd35',
    },
}
RUNS = {'baseline': (BASELINE, ('api-response.json', 'analysis.json')), 'fixed': (FIXED, ('api-response.json',))}
# 主案例与对照。标题是展示用的说明；高亮只能从该题已登记的原文依据里取，脚本逐条核对。
CASES = (
    {'id': 'F03', 'role': 'main', 'title': '谁应缴纳保费',
     'highlights': ('投保机构向存款保险基金管理机构交纳保费', '投保机构应当按照存款保险基金管理机构的规定，每6个月交纳一次保费')},
    {'id': 'F04', 'role': 'contrast', 'title': '仍未修好',
     'highlights': ('7个工作日内足额偿付存款',)},
)


class CaseError(ValueError):
    """证据对不上：不猜测、不跳过，直接停止。"""


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify_hashes(runs: dict) -> dict:
    """核对用到的每个文件都与所在归档的 SHA256SUMS.txt 记录一致。"""
    hashes = {}
    for key, (folder, names) in runs.items():
        lines = (folder / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines()
        expected = {name.lstrip('*'): digest for digest, name in (line.split(maxsplit=1) for line in lines if line.strip())}
        hashes[key] = {}
        for name in names:
            if name not in expected:
                raise CaseError(f'{folder.name}/SHA256SUMS.txt 没有登记 {name}。')
            actual = sha256(folder / name)
            if actual != expected[name]:
                raise CaseError(f'来源哈希不符：{folder.name}/{name} 为 {actual}，SHA256SUMS.txt 登记为 {expected[name]}。')
            hashes[key][name] = actual
    return hashes


def _record(records: list[dict], case_id: str) -> dict:
    found = [r for r in records if r.get('id') == case_id]
    if not found:
        raise CaseError(f'题目缺失：基线 analysis.json 中没有题号 {case_id}。')
    if len(found) > 1:
        raise CaseError(f'匹配不唯一：基线 analysis.json 中题号 {case_id} 出现 {len(found)} 次。')
    return found[0]


def _row(rows: list[dict], question: str, case_id: str, run: str) -> tuple[int, dict]:
    found = [(index, row) for index, row in enumerate(rows) if row.get('question') == question]
    if not found:
        raise CaseError(f'题目缺失：{run} 的 api-response.json 中找不到 {case_id} 的问题。')
    if len(found) > 1:
        raise CaseError(f'匹配不唯一：{case_id} 的问题在 {run} 的 api-response.json 中出现 {len(found)} 次。')
    return found[0]


def _trace(row: dict) -> dict:
    """逐句判定原样保留；原记录为空就是空，不转成「不支持」或「通过」。"""
    raw = row.get('hallucination_trace')
    trace = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    return {'status': trace.get('trace_status'), 'statements': trace.get('statements') or []}


def _highlights(spec: dict, record: dict, contexts: list[str]) -> list[dict]:
    evidence = record.get('sourceEvidence') or []
    marks = []
    for text in spec['highlights']:
        registered = [e['referenceId'] for e in evidence if text in e.get('quote', '')]
        if not registered:
            raise CaseError(f'{spec["id"]} 的高亮片段「{text}」不在 analysis.json 登记的原文依据里。')
        hits = [(index, context.count(text)) for index, context in enumerate(contexts) if text in context]
        total = sum(count for _, count in hits)
        if total != 1:
            raise CaseError(f'匹配不唯一：{spec["id"]} 的高亮片段「{text}」在检索上下文中出现 {total} 次。')
        marks.append({'text': text, 'context': hits[0][0], 'referenceIds': sorted(set(registered))})
    return marks


def extract(baseline: Path = BASELINE, fixed: Path = FIXED, fixture_path: Path = FIX_FIXTURE) -> dict:
    runs = {'baseline': (baseline, RUNS['baseline'][1]), 'fixed': (fixed, RUNS['fixed'][1])}
    hashes = verify_hashes(runs)
    records = _read(baseline / 'analysis.json')['records']
    responses = {key: _read(folder / 'api-response.json')['model_dfs'][MODEL] for key, (folder, _) in runs.items()}
    fixture_rows = {row['id']: row for row in _read(fixture_path)['formal']['rows']}

    cases = []
    for spec in CASES:
        record = _record(records, spec['id'])
        (before_index, before), (after_index, after) = (
            _row(responses[key], record['question'], spec['id'], key) for key in ('baseline', 'fixed'))
        if not before_index == after_index == record['rowIndex']:
            raise CaseError(f'{spec["id"]} 在两次运行与 analysis.json 中的行号不一致。')
        for field in ('ground_truth', 'contexts', 'case_type'):
            if before[field] != after[field]:
                raise CaseError(f'{spec["id"]} 两次运行的 {field} 不同，不能并列展示。')
        if record['contexts'] != before['contexts']:
            raise CaseError(f'{spec["id"]} 的检索上下文与 analysis.json 记录不同。')
        sides = {'before': {'answer': before['answer'], 'trace': _trace(before)},
                 'after': {'answer': after['answer'], 'trace': _trace(after)}}
        shown = fixture_rows.get(spec['id'])
        if shown is None:
            raise CaseError(f'题目缺失：fix-verification.json 中没有 {spec["id"]}。')
        for side in ('before', 'after'):
            if shown[side]['answer'] != sides[side]['answer'] \
                    or shown[side]['trace']['statements'] != len(sides[side]['trace']['statements']):
                raise CaseError(f'{spec["id"]} 的{side}回答或逐句判定条数与 fix-verification.json 不一致。')
        cases.append({'id': spec['id'], 'role': spec['role'], 'title': spec['title'], 'rowIndex': record['rowIndex'],
                      'question': record['question'], 'groundTruth': before['ground_truth'],
                      'contexts': before['contexts'], 'highlights': _highlights(spec, record, before['contexts']),
                      **sides})

    return {
        'purpose': '历史案例展示，未执行新的评测',
        'pinned': PINNED,
        'sources': {key: {'path': _rel(folder), 'run': folder.name, 'sha256': hashes[key],
                         'gitBlobSha256': dict(GIT_BLOB_SHA256[key])}
                    for key, (folder, _) in runs.items()},
        'cases': cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='只读校验，不写文件')
    args = parser.parse_args()
    try:
        case = extract()
    except CaseError as error:
        print(f'提取失败：{error}', file=sys.stderr)
        return 2
    if args.check:
        if not OUTPUT.is_file() or _read(OUTPUT) != case:
            print('fix-case.json 与证据重新提取的结果不一致。', file=sys.stderr)
            return 1
        print('一致。')
        return 0
    write_json(OUTPUT, case)
    print(f'已写出 {_rel(OUTPUT)}：{"、".join(c["id"] for c in case["cases"])}。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
