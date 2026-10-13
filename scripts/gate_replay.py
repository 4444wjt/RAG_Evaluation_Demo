"""门禁回放：对已归档的评测结果离线重新执行质量门禁裁决。

不调用模型、不需要密钥：读取已保存的评测结果 JSON（API 返回体，或带 `result` 字段的演示快照），
用 backend/core/gate.py 中与正式评测相同的 check_quality_gate 重新裁决。

退出码：0 = 通过（PASS）；1 = 拦截（BLOCK）；2 = 输入无法读取或缺少门禁所需字段。
可直接作为 CI 步骤：裁决不通过时该步骤失败。

可选输出（供 CI 页面展示，不影响裁决与退出码）：
    --junit PATH    JUnit XML：每个门禁指标一个用例，未达标的用例写明实际值、阈值与差值；
    --summary PATH  追加一段 Markdown 表格，传 $GITHUB_STEP_SUMMARY 即显示在 GitHub Actions 的作业摘要中。

回归对比（可选）：
    --baseline PATH 与基线评测逐题对比（backend/core/regression.py）。某个门禁指标「确定退步」——
                    按题配对重抽样的 95% 区间整体低于 0，且平均下降超过同一裁判重判的波动——时也拦截（退出码 1）；
                    只是「可能退步」时仅提示，不拦截。--noise-faith / --noise-ctx 可覆盖裁判波动的默认值。

用法：
    python scripts/gate_replay.py docs/competition/evidence/20261005T144504Z-full-39511424/api-response.json
    python scripts/gate_replay.py frontend/src/fixtures/demo.json --faith 0.85 --ctx 0.8
    python scripts/gate_replay.py result.json --junit gate.junit.xml --summary "$GITHUB_STEP_SUMMARY"
    python scripts/gate_replay.py new.json --baseline old.json
"""
from pathlib import Path
import argparse
import contextlib
import io
import json
import sys
import xml.etree.ElementTree as ET

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import CTX_PREC_THRESHOLD, FAITH_THRESHOLD  # noqa: E402
from backend.core.gate import check_quality_gate  # noqa: E402
from backend.core import attribution as attr  # noqa: E402
from backend.core import regression as reg  # noqa: E402

EXIT_PASS, EXIT_BLOCK, EXIT_INVALID = 0, 1, 2


def load_result(path):
    """读取归档结果；演示快照把结果放在 `result` 字段下。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data = data["result"]
    if not isinstance(data, dict) or not isinstance(data.get("summary"), list):
        raise ValueError("缺少 summary 列表，无法执行门禁")
    if not any(entry.get("faithfulness") is not None for entry in data["summary"]):
        raise ValueError("summary 中没有有效模型（faithfulness 均为空）")
    return data


def replay(result, faith_threshold=FAITH_THRESHOLD, ctx_prec_threshold=CTX_PREC_THRESHOLD):
    model_dfs = {name: pd.DataFrame(rows) for name, rows in (result.get("model_dfs") or {}).items() if isinstance(rows, list)}
    return check_quality_gate(result["summary"], model_dfs, faith_threshold, ctx_prec_threshold)


def gate_checks(result, faith_threshold=FAITH_THRESHOLD, ctx_prec_threshold=CTX_PREC_THRESHOLD):
    """逐项列出门禁指标，供报告展示；裁决仍以 check_quality_gate 为准。

    与门禁相同：取第一个 faithfulness 非空的模型，比较其正向均值；值缺失或 NaN 视为未达标。
    """
    model = next(s for s in result["summary"] if s.get("faithfulness") is not None)
    checks = []
    for key, label, threshold in (
        ("pos_faithfulness", "正向忠实度", faith_threshold),
        ("pos_context_precision", "正向上下文精度", ctx_prec_threshold),
    ):
        value = model.get(key)
        missing = value is None or pd.isna(value)
        passed = not missing and value >= threshold
        checks.append({
            "key": key,
            "label": label,
            "value": None if missing else float(value),
            "threshold": threshold,
            "passed": passed,
            "gap": None if missing or passed else round(threshold - value, 4),
        })
    return model["model"], checks


def _fmt(value):
    return "缺失" if value is None else f"{value:.4f}"


def _failure_message(check):
    if check["value"] is None:
        return f"{check['label']}缺失，按未达标处理（阈值 {check['threshold']:g}）"
    return f"{check['label']} {_fmt(check['value'])} 低于阈值 {check['threshold']:g}（差 {check['gap']:.4f}）"


STATUS_MARK = {reg.CONFIRMED: "❌", reg.POSSIBLE: "⚠️", reg.NONE: "✅", reg.UNPAIRED: "➖"}


def _interval(metric):
    return "—" if metric["interval"] is None else f"[{metric['interval'][0]:.4f}, {metric['interval'][1]:.4f}]"


def _delta(value):
    return "—" if value is None else f"{value:+.4f}"


def _question(text, width=30):
    text = str(text).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= width else text[:width] + "…"


def _regression_message(metric):
    if metric["status"] == reg.CONFIRMED:
        return (f"{metric['label']}逐题平均下降 {-metric['mean_delta']:.4f}，95% 区间 {_interval(metric)} 整体低于 0，"
                f"且超过同一裁判重判的波动 {metric['noise']:g}")
    if metric["status"] == reg.POSSIBLE:
        return (f"{metric['label']}逐题平均下降 {-metric['mean_delta']:.4f}，但 95% 区间 {_interval(metric)} 跨过 0 "
                f"或未超过裁判波动 {metric['noise']:g}，不能确定是真实退步")
    if metric["status"] == reg.UNPAIRED:
        return f"{metric['label']}没有两次都有分数的题，无法逐题对比"
    return f"{metric['label']}逐题平均变化 {_delta(metric['mean_delta'])}，未退步"


def print_regression(comparison, baseline_source, baseline_passed, candidate_passed):
    print("\n" + "=" * 50)
    print(f"🔁 相对基线的回归对比：{baseline_source}")
    print("=" * 50)
    print(f"基线主评测模型：{comparison['baseline_model']}；本次：{comparison['candidate_model']}")
    print(f"配对题目 {comparison['shared']}（仅基线有 {comparison['only_baseline']}，仅本次有 {comparison['only_candidate']}）")
    print(f"门禁结论：基线 {'PASS' if baseline_passed else 'FAIL'} → 本次 {'PASS' if candidate_passed else 'FAIL'}")
    for metric in comparison["metrics"]:
        print(f"  {STATUS_MARK[metric['status']]} {metric['status']}：{_regression_message(metric)}")
        for drop in metric["drops"][:5]:
            print(f"      {_question(drop['question'])}：{drop['baseline']:.4f} → {drop['candidate']:.4f}（{drop['delta']:+.4f}）")
        for item in metric["one_sided"]:
            print(f"      只有一侧有分数：{_question(item['question'])}：{_fmt(item['baseline'])} → {_fmt(item['candidate'])}")


def write_junit(path, source, model, checks, error=None, comparison=None):
    """写出 JUnit XML。error 不为空时写一个出错用例（输入无法读取）；comparison 不为空时追加回归对比用例。"""
    cases = [] if error else checks
    metrics = [] if error or comparison is None else comparison["metrics"]
    failures = sum(not c["passed"] for c in cases) + sum(m["status"] == reg.CONFIRMED for m in metrics)
    errors = 1 if error else 0
    tests = len(cases) + len(metrics) + errors
    suites = ET.Element("testsuites", name="VeriScope quality gate", tests=str(tests), failures=str(failures), errors=str(errors))
    suite = ET.SubElement(suites, "testsuite", name="quality-gate", tests=str(tests), failures=str(failures), errors=str(errors), skipped="0", time="0")
    props = ET.SubElement(suite, "properties")
    ET.SubElement(props, "property", name="result", value=str(source))
    if model is not None:
        ET.SubElement(props, "property", name="model", value=str(model))
    if error:
        case = ET.SubElement(suite, "testcase", classname="quality-gate", name="读取评测结果", time="0")
        ET.SubElement(case, "error", message=str(error), type="InvalidInput")
    for check in cases:
        name = f"{check['label']} {check['key']} >= {check['threshold']:g}"
        case = ET.SubElement(suite, "testcase", classname=f"quality-gate.{model}", name=name, time="0")
        if not check["passed"]:
            failure = ET.SubElement(case, "failure", message=_failure_message(check), type="GateThreshold")
            failure.text = f"实际值 {_fmt(check['value'])}；阈值 {check['threshold']:g}；主评测模型 {model}；输入 {source}"
    for metric in metrics:
        case = ET.SubElement(suite, "testcase", classname=f"quality-gate.{model}", name=f"相对基线未确定退步 {metric['label']} {metric['key']}", time="0")
        if metric["status"] == reg.CONFIRMED:
            failure = ET.SubElement(case, "failure", message=_regression_message(metric), type="GateRegression")
            failure.text = "\n".join(f"{d['question']}：{d['baseline']:.4f} → {d['candidate']:.4f}（{d['delta']:+.4f}）" for d in metric["drops"])
        else:
            ET.SubElement(case, "system-out").text = _regression_message(metric)
    ET.indent(suites)
    ET.ElementTree(suites).write(path, encoding="utf-8", xml_declaration=True)


def regression_summary_lines(comparison, baseline_source, baseline_passed, candidate_passed):
    changed = "（结论变化）" if baseline_passed != candidate_passed else ""
    lines = [
        f"#### 相对基线：`{baseline_source}`",
        "",
        f"门禁结论：基线 {'PASS' if baseline_passed else 'FAIL'} → 本次 {'PASS' if candidate_passed else 'FAIL'}{changed}。"
        f"配对题目 {comparison['shared']}（仅基线有 {comparison['only_baseline']}，仅本次有 {comparison['only_candidate']}）；"
        f"基线主评测模型 `{comparison['baseline_model']}`，本次 `{comparison['candidate_model']}`。",
        "",
        "| 指标 | 基线 | 本次 | 配对题数 | 逐题平均变化 | 95% 区间 | 裁判波动 | 判定 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in comparison["metrics"]:
        lines.append(f"| {m['label']} | {_fmt(m['baseline_mean'])} | {_fmt(m['candidate_mean'])} | {m['paired']} | "
                     f"{_delta(m['mean_delta'])} | {_interval(m)} | {m['noise']:g} | {STATUS_MARK[m['status']]} {m['status']} |")
    lines += ["", "基线、本次为门禁使用的正向均值；逐题平均变化只统计两次都有分数的题。"
              "「确定退步」要求 95% 区间整体低于 0 且平均下降超过裁判波动，此时拦截；「可能退步」只提示。", ""]
    details = []
    for m in comparison["metrics"]:
        for d in m["drops"][:5]:
            details.append(f"| {m['label']} | {_question(d['question'])} | {d['baseline']:.4f} → {d['candidate']:.4f} | {d['delta']:+.4f} |")
        for item in m["one_sided"]:
            details.append(f"| {m['label']} | {_question(item['question'])} | {_fmt(item['baseline'])} → {_fmt(item['candidate'])} | 只有一侧有分数 |")
    if details:
        lines += ["| 指标 | 题目 | 基线 → 本次 | 变化 |", "|---|---|---|---|", *details, ""]
    return lines


def attribution_lines(result):
    """门禁拦截时的失败归因：问题出在检索还是生成。缺少逐题结果时返回空列表。"""
    try:
        report = attr.attribute(result)
    except (ValueError, StopIteration):
        return []
    labels = [c for c in attr.CATEGORIES if c != attr.OK and report["counts"][c]]
    if not labels:
        return []
    lines = ["#### 失败归因：该修检索还是生成", "", "| 类别 | 题数 | 拉低正向忠实度 | 建议 |", "|---|---|---|---|"]
    advice = {item["category"]: item["advice"] for item in report["advice"]}
    for c in labels:
        lines.append(f"| {c} | {report['counts'][c]} | {report['faithfulnessLoss'][c]:.4f} | {advice.get(c, '—')} |")
    if report["primaryStage"]:
        lines += ["", f"主要问题在**{report['primaryStage']}**环节（检索 {report['retrievalLoss']:.4f}，生成 {report['generationLoss']:.4f}）。"
                  "归因为诊断参考，不影响门禁裁决。"]
    return lines + [""]


def write_summary(path, source, model, checks, passed, archived, error=None, regression_lines=None, attribution=None):
    """向 path 追加一段 Markdown（GitHub Actions 作业摘要会渲染它）。"""
    if error:
        lines = ["### 质量门禁回放：无法执行", "", f"输入 `{source}`：{error}", ""]
    else:
        lines = [
            f"### 质量门禁回放：{'PASS（放行）' if passed else 'FAIL（拦截）'}",
            "",
            "| 指标 | 实际值 | 阈值 | 结果 |",
            "|---|---|---|---|",
        ]
        for check in checks:
            verdict = "✅ 达标" if check["passed"] else ("❌ 缺失" if check["value"] is None else f"❌ 差 {check['gap']:.4f}")
            lines.append(f"| {check['label']} `{check['key']}` | {_fmt(check['value'])} | {check['threshold']:g} | {verdict} |")
        lines += ["", f"主评测模型：`{model}`；输入：`{source}`。"]
        if isinstance(archived, bool):
            same = "一致" if archived == passed else "不一致（阈值或门禁逻辑与归档时不同）"
            lines.append(f"归档记录：{'PASS' if archived else 'FAIL'}，与本次回放{same}。")
        lines += ["回放只重新执行门禁裁决，不调用模型、不重新计算指标。", ""]
        lines += attribution or []
        lines += regression_lines or []
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="对已归档的评测结果离线重新执行质量门禁")
    parser.add_argument("result", help="评测结果 JSON（API 返回体或演示快照）")
    parser.add_argument("--faith", type=float, default=FAITH_THRESHOLD, help=f"正向忠实度阈值，默认 {FAITH_THRESHOLD}")
    parser.add_argument("--ctx", type=float, default=CTX_PREC_THRESHOLD, help=f"正向上下文精度阈值，默认 {CTX_PREC_THRESHOLD}")
    parser.add_argument("--junit", metavar="PATH", help="写出 JUnit XML，供 CI 测试报告展示")
    parser.add_argument("--summary", metavar="PATH", help="追加 Markdown 摘要，例如 $GITHUB_STEP_SUMMARY")
    parser.add_argument("--baseline", metavar="PATH", help="基线评测结果 JSON：逐题对比，确定退步时也拦截")
    parser.add_argument("--noise-faith", type=float, default=reg.NOISE_FLOOR["pos_faithfulness"],
                        help=f"正向忠实度的裁判波动，默认 {reg.NOISE_FLOOR['pos_faithfulness']}（同一裁判重判实测）")
    parser.add_argument("--noise-ctx", type=float, default=reg.NOISE_FLOOR["pos_context_precision"],
                        help=f"正向上下文精度的裁判波动，默认 {reg.NOISE_FLOOR['pos_context_precision']}（同一裁判重判实测）")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    try:
        result = load_result(args.result)
        comparison = baseline_passed = None
        if args.baseline:
            try:
                baseline = load_result(args.baseline)
            except (OSError, ValueError) as error:
                raise ValueError(f"基线 {args.baseline}：{error}") from error
            comparison = reg.compare(baseline, result, {"pos_faithfulness": args.noise_faith,
                                                        "pos_context_precision": args.noise_ctx})
            with contextlib.redirect_stdout(io.StringIO()):
                baseline_passed = replay(baseline, args.faith, args.ctx)["gate_passed"]
    except (OSError, ValueError) as error:
        print(f"无法回放：{error}", file=sys.stderr)
        if args.junit:
            write_junit(args.junit, args.result, None, [], error=error)
        if args.summary:
            write_summary(args.summary, args.result, None, [], False, None, error=error)
        return EXIT_INVALID

    gate = replay(result, args.faith, args.ctx)
    archived = result.get("gate_passed")
    if isinstance(archived, bool):
        same = "一致" if archived == gate["gate_passed"] else "不一致（阈值或门禁逻辑与归档时不同）"
        print(f"\n归档记录：{'PASS' if archived else 'FAIL'}；本次回放：{'PASS' if gate['gate_passed'] else 'FAIL'}；{same}")

    attribution = [] if gate["gate_passed"] else attribution_lines(result)
    if attribution:
        print("\n" + "\n".join(line for line in attribution if line))

    regression_lines = None
    if comparison is not None:
        print_regression(comparison, args.baseline, baseline_passed, gate["gate_passed"])
        regression_lines = regression_summary_lines(comparison, args.baseline, baseline_passed, gate["gate_passed"])
        if comparison["confirmed"]:
            print("\n⛔ 相对基线确定退步，拦截")

    if args.junit or args.summary:
        model, checks = gate_checks(result, args.faith, args.ctx)
        if args.junit:
            write_junit(args.junit, args.result, model, checks, comparison=comparison)
        if args.summary:
            write_summary(args.summary, args.result, model, checks, gate["gate_passed"], archived, regression_lines=regression_lines,
                          attribution=attribution)
    blocked = not gate["gate_passed"] or (comparison is not None and comparison["confirmed"])
    return EXIT_BLOCK if blocked else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
