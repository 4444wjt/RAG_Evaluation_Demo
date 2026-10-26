"""裁判判错校准：请非作者人工标注门禁所用的 52 条逐条判定，估计裁判误差，给出同时计入题目抽样与裁判判错的区间。

门禁的正向忠实度来自 Ragas 的逐条判定：20 道正向题的回答被拆成 52 条陈述，裁判逐条判断能否由检索资料推出。
本工具分三步：

  prepare   从冻结证据生成盲标注材料：题目顺序打乱、题号换成 Q01–Q20，不含裁判判定、原题号、回答与参考答案；
            写入工作台「人工标注」页使用的 frontend/src/fixtures/judge-annotation.json；
  status    未标注时的敏感性分析：最少改判几条陈述门禁结论才会翻转；裁判灵敏度低于多少时，校正后的点估计会越过门槛；
  analyze   读取 1–2 位标注人导出的结果：标注一致性（Cohen's κ）、裁判的灵敏度与特异度、人工口径的门禁指标与区间。
            全部陈述都已标注时，人工口径直接替代裁判，区间只剩题目抽样误差；只标了部分题时改用 PPI，
            且只用从 Q01 起连续标完、至少 10 题的前缀，达不到时不给区间。
            所有估计都用与门禁相同的分母（每题陈述总数）；两人分歧与「无法判断」不剔除，
            按「全部算不支持」「全部算支持」给出点估计上下界；存在未决时不给置信区间或区间结论。
            同时写出 calibration.json，供以后在同一裁判、同一提示词、同一领域的运行上做校准校正。

不调用模型、不改门禁规则与已记录的结论；证据目录只读。

  python tools/competition/judge_calibration.py prepare [--check]
  python tools/competition/judge_calibration.py status [--json]
  python tools/competition/judge_calibration.py analyze --annotations <a.json> [<b.json>] [--output <目录>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import judge_error as je  # noqa: E402
from tools.competition import build_replay_fixture as replay  # noqa: E402

SEED = 20261013
PACKAGE = ROOT / "frontend" / "src" / "fixtures" / "judge-annotation.json"
PACKAGE_SCHEMA = "judge-annotation-package.v1"
ANNOTATION_SCHEMA = "judge-annotation.v1"
GUIDE = "docs/competition/judge-calibration/README.md"
MANIFEST = "inputs/case-manifest.json"
LABELS = (
    {"key": "1", "value": "支持", "hint": "仅凭资料就能读出或直接推出"},
    {"key": "2", "value": "矛盾", "hint": "资料中有与之相反的内容"},
    {"key": "3", "value": "无依据", "hint": "资料中既没有支持，也没有相反的内容"},
    {"key": "4", "value": "无法判断", "hint": "陈述含义不清，或拿不准（尽量少用）"},
)
BINARY = {"支持": 1, "矛盾": 0, "无依据": 0, "无法判断": None}
RULES = (
    "只依据「资料」判断。题目只用来理解陈述在说什么，题目里给出的数字和情形不算依据。",
    "不用自己的知识：即使你知道陈述是对的，资料里没有也判「无依据」。",
    "陈述的每一部分都要有依据，包括条号、数字、主体和范围：有一处与资料相反判「矛盾」，有一处找不到判「无依据」。",
    "「根据提供的文档」「根据参考资料」这类开头不影响判断；英文陈述按意思判断。",
    "陈述说「资料中没有答案」时：资料里确实没有相关内容才判「支持」，资料里有就判「矛盾」。",
    "资料片段可能在句中截断，只按显示出来的文字判断。",
    "内容只来自题目条件（例如题目给出的金额）时，判「无依据」，并勾选「内容来自题目」。",
)
REDACTED = "<REDACTED_LABEL>"
REDACTION_NOTE = "原词在归档时被脱敏程序误替换为 <REDACTED_LABEL>；按上下文，这里说的是条文中的「风险差别费率」。"
TIPPING_SPECIFICITIES = (1.0, 0.95, 0.9)
MIN_PPI_QUESTIONS = 10  # 与模拟检验中 PPI 的标注题数一致


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _questions():
    """门禁所用的 20 道正向题与逐条判定（核对证据哈希，并逐题核对判定比例与归档 faithfulness 一致）。"""
    fixture = replay.build()
    expected = replay._expected_hashes(replay.EVIDENCE)
    if expected.get(MANIFEST) != replay.sha256(replay.EVIDENCE / MANIFEST):
        raise ValueError("题目清单哈希与 SHA256SUMS.txt 不一致。")
    response = json.loads((replay.EVIDENCE / "api-response.json").read_text(encoding="utf-8"))
    rows = response["model_dfs"][replay.MODEL]
    verdicts = replay.map_statement_verdicts(replay.EVIDENCE, rows)
    ids = {case["rowIndex"]: case["id"] for case in
           json.loads((replay.EVIDENCE / MANIFEST).read_text(encoding="utf-8"))["cases"]}
    questions = [{"rowIndex": row["rowIndex"], "caseId": ids[row["rowIndex"]], "refusal": row["refusal"],
                  "question": rows[row["rowIndex"]]["question"], "contexts": rows[row["rowIndex"]]["contexts"],
                  "statements": verdicts[row["rowIndex"]]} for row in fixture["rows"]]
    return fixture, questions


def build_package():
    """返回（盲标注材料, 答案键）。答案键含裁判判定与原题号，只在分析时重新生成，不写入任何文件。"""
    fixture, questions = _questions()
    order = list(range(len(questions)))
    random.Random(f"{SEED}:questions").shuffle(order)
    shown, items, key = [], [], []
    for position, index in enumerate(order, 1):
        question, qid = questions[index], f"Q{position:02d}"
        shown.append({"id": qid, "question": question["question"], "contexts": question["contexts"]})
        for number, statement in enumerate(question["statements"]):
            sid = f"S{len(items) + 1:02d}"
            item = {"id": sid, "question": qid, "statement": statement["statement"]}
            if REDACTED in statement["statement"]:
                item["note"] = REDACTION_NOTE
            items.append(item)
            key.append({"id": sid, "question": qid, "rowIndex": question["rowIndex"], "caseId": question["caseId"],
                        "statementIndex": number, "judge": statement["verdict"], "refusal": question["refusal"],
                        "statement": statement["statement"]})
    body = {"questions": shown, "items": items}
    package = {"schema": PACKAGE_SCHEMA, "packageId": hashlib.sha256(_canonical(body)).hexdigest(),
               "source": {"runId": fixture["source"]["runId"], "evidence": fixture["source"]["evidence"]},
               "guide": GUIDE, "labels": list(LABELS), "rules": list(RULES), **body}
    return package, key


def _gate_rows(questions):
    return [[s["verdict"] for s in q["statements"]] for q in questions]


def _describe(chosen, questions):
    return [{"caseId": questions[q]["caseId"], "statement": j + 1, "text": questions[q]["statements"][j]["statement"],
             "refusal": questions[q]["refusal"]} for q, j in chosen]


def status() -> dict:
    """未标注时的敏感性分析：不需要任何人工标签。"""
    fixture, questions = _questions()
    rows = _gate_rows(questions)
    threshold = fixture["thresholds"]["faithfulness"]
    values = je.question_values(rows)
    value = sum(values) / len(values)
    statements = sum(len(row) for row in rows)
    refusals = [i for i, q in enumerate(questions) if q["refusal"]]
    overall = je.min_flips(rows, threshold)
    answered = je.min_flips(rows, threshold, exclude=refusals)
    sampling = je.sampling_interval(values, statements=statements)
    unsupported = [[q, j] for q, row in enumerate(rows) for j, verdict in enumerate(row) if verdict == 0]
    return {
        "source": fixture["source"], "threshold": threshold, "judgeValue": value,
        "questions": len(rows), "statements": statements, "judgedSupported": sum(map(sum, rows)),
        "unsupported": _describe(unsupported, questions),
        "sampling": {"interval": sampling["interval"], "nEff": sampling["nEff"],
                     "state": je.three_state(*sampling["interval"], threshold)},
        "flips": {"overall": {"flips": overall["flips"], "after": overall["after"]},
                  "answeredOnly": {"flips": answered["flips"], "after": answered["after"],
                                   "statements": _describe(answered["statements"], questions)}},
        "tipping": [{"specificity": q0, "sensitivity": je.tipping_sensitivity(value, threshold, q0)}
                    for q0 in TIPPING_SPECIFICITIES],
    }


def load_annotation(path: Path, package: dict) -> dict:
    """读取并校验一份标注结果。校验规则与 schemas/judge-annotation.v1.schema.json 一致：schema 拒绝的文件这里也拒绝。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != ANNOTATION_SCHEMA:
        raise ValueError(f"{path}：不是 {ANNOTATION_SCHEMA} 格式的标注结果。")
    extra = set(data) - {"schema", "packageId", "annotator", "notAuthor", "savedAt", "labels"}
    if extra:
        raise ValueError(f"{path}：未知字段 {'、'.join(sorted(extra))}。")
    if data.get("packageId") != package["packageId"]:
        raise ValueError(f"{path}：标注所用的材料与当前材料不一致（packageId 不同）。")
    annotator = data.get("annotator")
    if not isinstance(annotator, str) or not annotator.strip() or len(annotator) > 40:
        raise ValueError(f"{path}：缺少标注人代号，或代号超过 40 个字符。")
    if data.get("notAuthor") is not True:
        raise ValueError(f"{path}：标注人未确认没有参与本项目的开发、题目与参考答案的编写（notAuthor）。")
    if "savedAt" in data and not isinstance(data["savedAt"], str):
        raise ValueError(f"{path}：savedAt 必须是字符串。")
    if not isinstance(data.get("labels"), dict):
        raise ValueError(f"{path}：缺少 labels。")
    known = {item["id"] for item in package["items"]}
    labels, from_question, notes = {}, set(), {}
    for sid, entry in data["labels"].items():
        if sid not in known:
            raise ValueError(f"{path}：未知的陈述编号 {sid}。")
        if not isinstance(entry, dict) or entry.get("label") not in BINARY:
            raise ValueError(f"{path}：{sid} 的标签必须是 {'、'.join(BINARY)} 之一。")
        if set(entry) - {"label", "fromQuestion", "note"}:
            raise ValueError(f"{path}：{sid} 含未知字段。")
        if not isinstance(entry.get("fromQuestion", False), bool):
            raise ValueError(f"{path}：{sid} 的 fromQuestion 必须是 true 或 false。")
        if not isinstance(entry.get("note", ""), str) or len(entry.get("note", "")) > 500:
            raise ValueError(f"{path}：{sid} 的备注必须是不超过 500 字的文字。")
        labels[sid] = entry["label"]
        if entry.get("fromQuestion"):
            from_question.add(sid)
        if entry.get("note"):
            notes[sid] = entry["note"]
    return {"annotator": annotator.strip(), "notAuthor": True, "labels": labels,
            "fromQuestion": from_question, "notes": notes}


def _agreement(first, second, ids):
    both = [sid for sid in ids if sid in first["labels"] and sid in second["labels"]]
    if not both:
        return None
    a, b = [first["labels"][s] for s in both], [second["labels"][s] for s in both]
    binary = [sid for sid in both if BINARY[first["labels"][sid]] is not None and BINARY[second["labels"][sid]] is not None]
    ab, bb = [BINARY[first["labels"][s]] for s in binary], [BINARY[second["labels"][s]] for s in binary]
    return {"statements": len(both), "agreement": sum(x == y for x, y in zip(a, b)) / len(both),
            "kappa": je.cohen_kappa(a, b),
            "binaryStatements": len(binary),
            "binaryAgreement": sum(x == y for x, y in zip(ab, bb)) / len(binary) if binary else None,
            "binaryKappa": je.cohen_kappa(ab, bb) if binary else None}


def _consensus(annotations, sid):
    labels = [a["labels"].get(sid) for a in annotations]
    if any(label is None for label in labels):
        return "unlabeled", None
    values = {BINARY[label] for label in labels}
    if len(values) == 1 and None not in values:
        return "resolved", values.pop()
    return "unresolved", None


def _complete(group) -> bool:
    return all(state != "unlabeled" for state, _ in group)


def _human_value(group, fill) -> float:
    """一题的人工口径占比。分母始终是该题的陈述总数，与门禁一致；未决陈述（两人分歧或「无法判断」）取 fill。"""
    return sum(value if state == "resolved" else fill for state, value in group) / len(group)


def _design(groups) -> dict:
    """选择估计方法；不能估计时写明原因。

    全部题都标完：人工口径直接替代裁判。否则只用「从 Q01 起连续标完」的题做 PPI：标注页的题目顺序由固定种子
    打乱，并须在标注前确定本批题数；跳着标完的题不计入，避免按难易挑题带来的偏差。前缀少于 MIN_PPI_QUESTIONS
    （模拟检验过的规模）时不给区间。
    """
    complete = [_complete(group) for group in groups]
    if all(complete):
        return {"method": "human", "labeled": list(range(len(groups))), "outsidePrefix": 0, "reason": None}
    prefix = complete.index(False)
    reason = None
    if prefix < MIN_PPI_QUESTIONS:
        reason = (f"从 Q01 起连续标完的只有 {prefix} 题，少于 {MIN_PPI_QUESTIONS} 题（PPI 在模拟中检验过的规模），不给区间；"
                  "部分标注须从 Q01 起按顺序连续完成，跳着标完的题不能当作随机样本。")
    elif len(groups) - prefix < 2:
        reason = "未标完的题少于 2 题，无法估计其方差；请标完全部题目。"
    return {"method": None if reason else "ppi", "labeled": list(range(prefix)),
            "outsidePrefix": sum(complete[prefix:]), "reason": reason}


def _estimate(groups, judge_values, design, threshold, fill) -> dict:
    labeled = design["labeled"]
    human = [_human_value(groups[i], fill) for i in labeled]
    if design["method"] == "human":
        result = je.sampling_interval(human, statements=sum(map(len, groups)))
    else:
        rest = [v for i, v in enumerate(judge_values) if i >= len(labeled)]
        result = je.ppi_interval(rest, [judge_values[i] for i in labeled], human)
    return {"point": result["point"], "interval": result["interval"],
            "state": je.three_state(*result["interval"], threshold),
            "pointPasses": round(result["point"], 4) >= threshold, **({"nEff": result["nEff"]} if "nEff" in result else {})}


def _estimates(groups, judge_values, threshold) -> dict:
    """保留原分母；未决只报告点估计边界，端点区间不能包住混合赋值的区间。"""
    design = _design(groups)
    base = {"method": design["method"], "labeledQuestions": len(design["labeled"]),
            "outsidePrefix": design["outsidePrefix"], "reason": design["reason"],
            "unresolved": sum(1 for i in design["labeled"] for state, _ in groups[i] if state == "unresolved")}
    if design["method"] is None:
        return base
    lower = _estimate(groups, judge_values, design, threshold, 0)
    upper = _estimate(groups, judge_values, design, threshold, 1)
    passes = {lower["pointPasses"], upper["pointPasses"]}
    if base["unresolved"]:
        reason = "存在未决标签：两个端点区间不能保证覆盖混合赋值的区间，故只报告点估计上下界，不给95%区间或区间结论。"
        for boundary in (lower, upper):
            boundary["interval"] = None
            boundary["state"] = None
        return {**base, "reason": reason, "lower": lower, "upper": upper,
                "point": [lower["point"], upper["point"]], "interval": None, "state": None,
                "pointPasses": passes.pop() if len(passes) == 1 else None}
    interval = lower["interval"]
    return {**base, "lower": lower, "upper": upper, "point": [lower["point"], upper["point"]], "interval": interval,
            "state": je.three_state(*interval, threshold), "pointPasses": passes.pop() if len(passes) == 1 else None}


def analyze(paths) -> dict:
    if not 1 <= len(paths) <= 2:
        raise ValueError("请提供 1–2 份标注结果。")
    package, key = build_package()
    fixture = replay.build()
    threshold = fixture["thresholds"]["faithfulness"]
    annotations = [load_annotation(Path(p), package) for p in paths]
    if len({a["annotator"] for a in annotations}) != len(annotations):
        raise ValueError("两份标注结果的标注人代号相同。")
    ids = [k["id"] for k in key]
    states = {sid: _consensus(annotations, sid) for sid in ids}

    # 按标注页的题目顺序（Q01–Q20）分组；每题的分母是该题全部陈述数，与门禁一致
    order = [q["id"] for q in package["questions"]]
    members = {qid: [k for k in key if k["question"] == qid] for qid in order}
    groups = [[states[k["id"]] for k in members[qid]] for qid in order]
    judge_values = je.question_values([[k["judge"] for k in members[qid]] for qid in order])
    if abs(sum(judge_values) / len(judge_values) - fixture["gate"]["posFaithfulness"]) > 5e-5:
        raise ValueError("按标注材料分组重算的裁判口径与归档门禁值不一致。")

    pairs = [(states[k["id"]][1], k["judge"]) for k in key if states[k["id"]][0] == "resolved"]
    per_question = []
    for i, qid in enumerate(order):
        done = _complete(groups[i])
        per_question.append({"id": qid, "caseId": members[qid][0]["caseId"], "statements": len(groups[i]),
                             "judge": judge_values[i], "complete": done,
                             "humanLower": _human_value(groups[i], 0) if done else None,
                             "humanUpper": _human_value(groups[i], 1) if done else None})

    statements = []
    for k in key:
        state, value = states[k["id"]]
        statements.append({"id": k["id"], "question": k["question"], "caseId": k["caseId"], "statement": k["statementIndex"] + 1,
                           "refusal": k["refusal"], "judge": k["judge"],
                           "labels": {a["annotator"]: a["labels"].get(k["id"]) for a in annotations},
                           "fromQuestion": [a["annotator"] for a in annotations if k["id"] in a["fromQuestion"]],
                           "notes": {a["annotator"]: a["notes"][k["id"]] for a in annotations if k["id"] in a["notes"]},
                           "state": state, "human": value,
                           "disagreesWithJudge": state == "resolved" and value != k["judge"]})
    counts = {name: sum(1 for s in states.values() if s[0] == name) for name in ("resolved", "unresolved", "unlabeled")}
    return {
        "schemaVersion": 1, "packageId": package["packageId"], "source": fixture["source"], "threshold": threshold,
        "annotators": [{"annotator": a["annotator"], "notAuthor": a["notAuthor"], "labeled": len(a["labels"]),
                        "fromQuestion": len(a["fromQuestion"])} for a in annotations],
        "statements": len(ids), "counts": counts,
        "agreement": _agreement(*annotations, ids) if len(annotations) == 2 else None,
        "calibration": je.calibration_counts(pairs),
        "judge": {"value": sum(judge_values) / len(judge_values), "passed": fixture["gate"]["passed"]},
        "estimate": _estimates(groups, judge_values, threshold),
        "questions": per_question,
        "rows": statements,
    }


def calibration_file(result: dict) -> dict:
    """供以后的运行做校准校正：只在裁判模型、提示词与领域都不变时适用（Lee et al. 的裁判不变假设）。"""
    c = result["calibration"]
    return {"schemaVersion": 1, "packageId": result["packageId"], "source": result["source"],
            "q0": c["q0"], "m0": c["m0"], "q1": c["q1"], "m1": c["m1"],
            "q0Wilson95": c["q0Wilson95"], "q1Wilson95": c["q1Wilson95"],
            "assumption": "裁判判定在本次与目标运行之间不变：同一裁判模型、同一 Ragas 忠实度提示词、同一领域。"}


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def render_status(report: dict) -> str:
    pct = lambda value: f"{value * 100:.1f}%"
    flips = report["flips"]
    low, high = report["sampling"]["interval"]
    lines = [f"门禁裁判口径：{pct(report['judgeValue'])}（{report['judgedSupported']} / {report['statements']} 条判为支持，"
             f"{report['questions']} 题），门槛 {pct(report['threshold'])}",
             f"只计题目抽样：{pct(low)}–{pct(high)}（有效样本量 {report['sampling']['nEff']:.1f}），{report['sampling']['state']}",
             f"最少改判：{flips['overall']['flips']} 条（含拒答题）；只改作答题则需 {flips['answeredOnly']['flips']} 条，"
             f"改后 {pct(flips['answeredOnly']['after'])}"]
    lines += [f"  {s['caseId']} 第 {s['statement']} 条：{s['text']}" for s in flips["answeredOnly"]["statements"]]
    lines += [f"特异度 {t['specificity']:.2f} 时，裁判灵敏度低于 {t['sensitivity']:.3f}，校正后的点估计即达到门槛"
              for t in report["tipping"]]
    return "\n".join(lines)


def render_analysis(result: dict) -> str:
    pct = lambda value: "—" if value is None else f"{value * 100:.1f}%"
    c, counts, e = result["calibration"], result["counts"], result["estimate"]
    lines = [f"标注人：{'、'.join(a['annotator'] for a in result['annotators'])}；"
             f"已定 {counts['resolved']}、未决（分歧或无法判断）{counts['unresolved']}、未标 {counts['unlabeled']}（共 {result['statements']} 条）"]
    if len(result["annotators"]) == 1:
        lines.append("单人标注：没有标注一致性指标，结论须注明是单人标注")
    if result["agreement"]:
        g = result["agreement"]
        kappa = "—" if g["kappa"] is None else f"{g['kappa']:.2f}"
        lines.append(f"两人一致率 {pct(g['agreement'])}，Cohen's κ {kappa}")
    lines.append(f"裁判灵敏度 {pct(c['q1'])}（{c['m1']} 条人工判为支持），特异度 {pct(c['q0'])}（{c['m0']} 条人工判为不支持）；"
                 "仅为描述，不参与下面的区间")
    if e["method"] is None:
        lines.append(f"不给区间：{e['reason']}")
        return "\n".join(lines)
    method = "全部人工标注" if e["method"] == "human" else f"PPI，Q01–Q{e['labeledQuestions']:02d} 已标注"
    low, high = e["point"]
    point = pct(low) if low == high else f"{pct(low)}–{pct(high)}"
    if e["interval"] is None:
        lines.append(f"人工口径（{method}）：点估计边界 {point}")
        lines.append(f"不给95%区间：{e['reason']}")
        lines.append(f"  {e['unresolved']} 条未决陈述保留原分母，分别按全部算不支持、全部算支持计算点估计边界；边界不是置信区间。")
    else:
        lines.append(f"人工口径（{method}）：点估计 {point}，95% 区间 {pct(e['interval'][0])}–{pct(e['interval'][1])}，{e['state']}")
    if e["outsidePrefix"]:
        lines.append(f"  另有 {e['outsidePrefix']} 题不在 Q01 起的连续前缀内，未计入 PPI")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_cmd = sub.add_parser("prepare", help="生成盲标注材料")
    prepare_cmd.add_argument("--check", action="store_true", help="只核对已提交的材料")
    status_cmd = sub.add_parser("status", help="未标注时的敏感性分析")
    status_cmd.add_argument("--json", action="store_true")
    analyze_cmd = sub.add_parser("analyze", help="分析标注结果")
    analyze_cmd.add_argument("--annotations", type=Path, nargs="+", required=True)
    analyze_cmd.add_argument("--output", type=Path, help="写出 analysis.json 与 calibration.json 的目录")
    args = parser.parse_args(argv)

    if args.command == "prepare":
        package, _ = build_package()
        if args.check:
            if json.loads(PACKAGE.read_text(encoding="utf-8")) != package:
                print("judge-annotation.json 与证据重新生成的结果不一致。", file=sys.stderr)
                return 1
            print("一致。")
            return 0
        _write(PACKAGE, package)
        print(f"已写出 {PACKAGE.relative_to(ROOT)}：{len(package['questions'])} 题、{len(package['items'])} 条陈述。")
        return 0
    if args.command == "status":
        report = status()
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_status(report))
        return 0
    result = analyze(args.annotations)
    print(render_analysis(result))
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        _write(args.output / "analysis.json", result)
        _write(args.output / "calibration.json", calibration_file(result))
        print(f"已写出 {args.output / 'analysis.json'} 与 calibration.json。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
