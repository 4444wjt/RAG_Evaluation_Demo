"""裁判判错校准的模拟：在 25 题正式运行的题目结构下，比较五种区间同时计入题目抽样与裁判判错的效果。

每次模拟先按正式运行的结构（20 道正向题、各题陈述数相同）生成真实标签，再让一个会判错的裁判逐条判定，
另取一批人工校准样本估计裁判的灵敏度与特异度。比较的五种区间：

  只计题目抽样   裁判口径的指标，按题聚集的有效样本量代入 Agresti–Coull 区间，把每条判定都当作正确；
  校准校正·独立  Lee et al. 的调整区间，测试样本量取陈述总数 52，即把陈述当作相互独立；
  校准校正·聚集  同上，测试样本量取按题方差折算的有效样本量（本项目的推广）；
  PPI           随机人工标注 10 道题，按题做预测驱动推断；
  全部人工标注   20 道题全部人工标注，区间同「只计题目抽样」，只剩题目抽样误差。

「只计题目抽样」与「全部人工标注」用同一种区间，恰是校准校正·聚集在裁判完全正确时的特例，
所以各方法之间的差别只来自对裁判判错的处理。

设定：
  - 真实支持率 θ ∈ {0.7, 0.8, 0.9}，分别低于、等于、高于门槛 0.8；
  - 各题支持率 θ_q ~ Beta(θc, (1−θ)c)。c = 0.40 由正式运行的题间方差按矩估计得到（题间差异大，
    多数题非全对即全错）；c = 4 为题间差异较小的对照；
  - 裁判误差取外部基准验证中逐句核对在 ANAH 上的实测值：灵敏度 72/99，特异度 178/200。
    这只是一个有实测依据的设定，不代表门禁所用 Ragas 判定的真实误差；
  - 校准样本 (m0, m1) ∈ {(10, 40), (50, 50)}：前者约等于人工标注本次 52 条判定能得到的规模；
  - 「判定不变」时，测试时的裁判与校准时相同；「判定漂移」时，测试时的灵敏度比校准时低 0.1，
    即违反了 Lee et al. 的裁判不变假设。

不调用模型、不读取任何密钥；随机种子固定，同一 Python 版本下结果完全可复算。

  python tools/competition/judge_error_simulation.py                  运行并打印报告
  python tools/competition/judge_error_simulation.py --output <文件>   同时写出 JSON
  python tools/competition/judge_error_simulation.py --check          按归档设定重算，核对与归档一致
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
REPLICATES = 2000
THRESHOLD = 0.8
THETAS = (0.7, 0.8, 0.9)
CONCENTRATIONS = (0.4, 4.0)
CALIBRATIONS = ((10, 40), (50, 50))
SCENARIOS = {"invariant": 0.0, "shifted": -0.1}
PPI_LABELED = 10
EXTERNAL = ROOT / "docs" / "competition" / "external-validation" / "summary.json"
ARCHIVE = ROOT / "docs" / "competition" / "judge-calibration" / "simulation.json"
ESTIMATORS = ("naive", "leeIndependent", "leeClustered", "ppi", "human")
LABELS = {"naive": "只计题目抽样", "leeIndependent": "校准校正·独立", "leeClustered": "校准校正·聚集",
          "ppi": "PPI（标注 10 题）", "human": "全部人工标注"}
SCENARIO_LABELS = {"invariant": "判定不变", "shifted": "判定漂移"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def formal_structure() -> dict:
    """正式运行的题目结构与按矩估计的题间集中度 c（核对证据哈希与逐句判定）。"""
    rows = replay.build()["rows"]
    sizes = [row["total"] for row in rows]
    values = [row["supported"] / row["total"] for row in rows]
    g = sum(values) / len(values)
    s2 = sum((v - g) ** 2 for v in values) / (len(values) - 1)
    h = sum(1 / n for n in sizes) / len(sizes)
    # 题内二项 + 题间 Beta：E[s²] ≈ θ(1−θ)(1 + c·h)/(1 + c)，解出 c
    concentration = (g * (1 - g) - s2) / (s2 - g * (1 - g) * h)
    return {"sizes": sizes, "statements": sum(sizes), "judgeValue": g, "between": s2,
            "concentration": round(concentration, 4)}


def judge_rates(path: Path = EXTERNAL) -> dict:
    """外部基准验证中逐句核对在 ANAH 上的实测混淆矩阵：幻觉为阳性，换算成「支持」口径的灵敏度与特异度。"""
    binary = json.loads(path.read_text(encoding="utf-8"))["anah"]["binary"]
    return {"q1": binary["tn"] / (binary["tn"] + binary["fp"]), "q1Counts": [binary["tn"], binary["tn"] + binary["fp"]],
            "q0": binary["tp"] / (binary["tp"] + binary["fn"]), "q0Counts": [binary["tp"], binary["tp"] + binary["fn"]],
            "source": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": _sha256(path)}


def replicate(rng, sizes, theta, concentration, q0, q1, test_q1, m0, m1, labeled):
    """一次模拟：返回各区间的（点估计, 区间）；无法计算时为 None。"""
    statements = sum(sizes)
    rates = [rng.betavariate(theta * concentration, (1 - theta) * concentration) for _ in sizes]
    truth = [[int(rng.random() < rate) for _ in range(n)] for rate, n in zip(rates, sizes)]
    judged = [[int(rng.random() < test_q1) if z else int(rng.random() >= q0) for z in row] for row in truth]
    calibration = {"q1": sum(rng.random() < q1 for _ in range(m1)) / m1, "m1": m1,
                   "q0": sum(rng.random() < q0 for _ in range(m0)) / m0, "m0": m0}
    g_judge, g_true = je.question_values(judged), je.question_values(truth)
    p_hat = sum(g_judge) / len(g_judge)
    chosen = set(rng.sample(range(len(sizes)), labeled))

    naive = je.sampling_interval(g_judge, statements=statements)
    human = je.sampling_interval(g_true, statements=statements)
    result = {"naive": (naive["point"], naive["interval"]), "human": (human["point"], human["interval"])}
    independent = je.corrected_interval(p_hat, statements, calibration["q0"], m0, calibration["q1"], m1)
    clustered = je.clustered_interval(g_judge, calibration, statements=statements)
    result["leeIndependent"] = None if independent is None else (independent["point"], independent["interval"])
    result["leeClustered"] = None if clustered is None else (clustered["point"], clustered["interval"])
    ppi = je.ppi_interval([g for q, g in enumerate(g_judge) if q not in chosen],
                          [g for q, g in enumerate(g_judge) if q in chosen],
                          [g for q, g in enumerate(g_true) if q in chosen])
    result["ppi"] = (ppi["point"], ppi["interval"])
    return result


def run_cell(seed, sizes, theta, concentration, rates, calibration, scenario, replicates):
    rng = random.Random(f"{seed}:{theta}:{concentration}:{calibration[0]}:{calibration[1]}:{scenario}")
    test_q1 = rates["q1"] + SCENARIOS[scenario]
    totals = {name: {"covered": 0, "width": 0.0, "bias": 0.0, je.PASS: 0, je.FAIL: 0, je.UNSURE: 0, "undefined": 0}
              for name in ESTIMATORS}
    for _ in range(replicates):
        for name, outcome in replicate(rng, sizes, theta, concentration, rates["q0"], rates["q1"], test_q1,
                                       calibration[0], calibration[1], PPI_LABELED).items():
            tally = totals[name]
            if outcome is None or outcome[0] is None:
                tally["undefined"] += 1
                continue
            point, (low, high) = outcome
            tally["covered"] += low <= theta <= high
            tally["width"] += high - low
            tally["bias"] += point - theta
            tally[je.three_state(low, high, THRESHOLD)] += 1
    cell = {"theta": theta, "concentration": concentration, "m0": calibration[0], "m1": calibration[1],
            "scenario": scenario, "testSensitivity": round(test_q1, 4), "estimators": {}}
    for name, tally in totals.items():
        valid = replicates - tally["undefined"]
        cell["estimators"][name] = {
            "coverage": round(tally["covered"] / valid, 4), "meanWidth": round(tally["width"] / valid, 4),
            "bias": round(tally["bias"] / valid, 4),
            "decisions": {state: round(tally[state] / valid, 4) for state in (je.PASS, je.UNSURE, je.FAIL)},
            "undefined": tally["undefined"]}
    return cell


def simulate(seed: int = SEED, replicates: int = REPLICATES) -> dict:
    structure, rates = formal_structure(), judge_rates()
    cells = [run_cell(seed, structure["sizes"], theta, concentration, rates, calibration, scenario, replicates)
             for scenario in SCENARIOS for concentration in CONCENTRATIONS
             for calibration in CALIBRATIONS for theta in THETAS]
    return {"schemaVersion": 1, "seed": seed, "replicates": replicates, "threshold": THRESHOLD, "confidence": 0.95,
            "pythonVersion": ".".join(map(str, sys.version_info[:2])),
            "structure": structure, "judgeRates": rates, "ppiLabeledQuestions": PPI_LABELED,
            "settings": {"thetas": list(THETAS), "concentrations": list(CONCENTRATIONS),
                         "calibrations": [list(c) for c in CALIBRATIONS],
                         "scenarios": {name: {"label": SCENARIO_LABELS[name], "sensitivityShift": shift}
                                       for name, shift in SCENARIOS.items()}},
            "estimators": LABELS, "cells": cells}


def render(report: dict) -> str:
    pct = lambda value: f"{value * 100:.1f}%"
    rates, structure = report["judgeRates"], report["structure"]
    lines = [f"题目结构：{len(structure['sizes'])} 题、{structure['statements']} 句（正式运行），"
             f"题间集中度矩估计 c = {structure['concentration']}",
             f"裁判误差（ANAH 实测）：灵敏度 {pct(rates['q1'])}，特异度 {pct(rates['q0'])}",
             f"每格模拟 {report['replicates']} 次，种子 {report['seed']}，门槛 {report['threshold']}", ""]
    for cell in report["cells"]:
        lines.append(f"θ={cell['theta']} c={cell['concentration']} 校准 m0={cell['m0']} m1={cell['m1']} "
                     f"{SCENARIO_LABELS[cell['scenario']]}（测试时灵敏度 {pct(cell['testSensitivity'])}）")
        for name in ESTIMATORS:
            e = cell["estimators"][name]
            d = e["decisions"]
            lines.append(f"  {LABELS[name]:<14} 覆盖 {pct(e['coverage']):>6}  宽度 {e['meanWidth']:.3f}  偏差 {e['bias']:+.3f}  "
                         f"确定通过 {pct(d[je.PASS]):>6}  证据不足 {pct(d[je.UNSURE]):>6}  确定不通过 {pct(d[je.FAIL]):>6}"
                         + (f"  无法计算 {e['undefined']}" if e["undefined"] else ""))
    return "\n".join(lines)


def same(saved, fresh, tolerance: float = 1e-4) -> bool:
    """逐项比较；浮点允许极小差异（不同平台的数学库在末位上可能不同）。"""
    if isinstance(saved, dict) and isinstance(fresh, dict):
        return saved.keys() == fresh.keys() and all(same(saved[k], fresh[k], tolerance) for k in saved)
    if isinstance(saved, list) and isinstance(fresh, list):
        return len(saved) == len(fresh) and all(same(a, b, tolerance) for a, b in zip(saved, fresh))
    if isinstance(saved, float) or isinstance(fresh, float):
        return isinstance(saved, (int, float)) and isinstance(fresh, (int, float)) and abs(saved - fresh) <= tolerance
    return saved == fresh


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="写出 JSON 的路径")
    parser.add_argument("--check", action="store_true", help="按归档设定重算并核对")
    parser.add_argument("--replicates", type=int, default=REPLICATES)
    args = parser.parse_args(argv)
    if args.check:
        saved = json.loads(ARCHIVE.read_text(encoding="utf-8"))
        if saved["pythonVersion"] != ".".join(map(str, sys.version_info[:2])):
            print(f"归档由 Python {saved['pythonVersion']} 生成，随机数序列可能不同，请用同一版本核对。", file=sys.stderr)
            return 2
        if not same(saved, simulate(saved["seed"], saved["replicates"])):
            print("模拟结果与归档不一致。", file=sys.stderr)
            return 1
        print("一致。")
        return 0
    report = simulate(replicates=args.replicates)
    print(render(report))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
