"""款项归属事后扩展的确定性离线复算；不调用模型。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import statute_check as sc  # noqa: E402
from backend.core.statute_locate import check_answer_extended  # noqa: E402
from tools.competition import statute_injection as si  # noqa: E402
from tools.competition import statute_pinpoint as sp  # noqa: E402


def _counts(items, results):
    original = [i["id"] for i in items if results[i["id"]]["original"]["flagged"]]
    extended = [i["id"] for i in items if results[i["id"]]["flagged"]]
    return {"total": len(items), "originalFlagged": len(original), "extendedFlagged": len(extended),
            "originalIds": original, "extendedIds": extended,
            "addedIds": [identity for identity in extended if identity not in original]}


def _recovery_status(recoveries, expected):
    if not recoveries or expected is None:
        return "undetermined"
    statuses = []
    for recovery in recoveries:
        actual = recovery["location"]
        # 确定前缀与目标不同已可判不一致；目标要求的层未确定则不猜。
        if any(actual[k] is not None and actual[k] != expected[k] for k in actual):
            statuses.append("inconsistent")
        elif any(expected[k] is not None and actual[k] is None for k in actual):
            statuses.append("undetermined")
        else:
            statuses.append("consistent")
    if "inconsistent" in statuses:
        return "inconsistent"
    return "undetermined" if "undetermined" in statuses else "consistent"


def analyze(directory: Path, output: Path) -> dict:
    si.verify_checksums(directory)
    sp.empty_output(output)
    si.require(not output.resolve().is_relative_to(directory.resolve()), '输出不得写入输入归档')
    package_path = directory / "package.json"
    source_path = directory / "inputs" / si.SOURCE
    rules_path = directory.parent / "analysis/rules.json"
    package, archived = si.load(package_path), si.load(rules_path)
    si.verify_checksums(rules_path.parent)
    source = source_path.read_text(encoding="utf-8")
    items = package["items"]
    si.require(len({i["id"] for i in items}) == len(items), '材料 ID 重复')
    results = {i["id"]: check_answer_extended(i["statement"], i["contexts"], source, i["question"])
               for i in items}
    original_ids = sorted(k for k, r in results.items() if r["original"]["flagged"])
    archived_ids = sorted(k for k, r in archived["items"].items() if r["flagged"])
    si.require(set(results) == set(archived["items"]) and original_ids == archived_ids,
               '原规则 flagged 集合与归档不一致')
    si.require(all(results[k]["original"]["findings"] == archived["items"][k]["findings"] for k in results),
               '原规则 findings 与归档不一致')
    pinpoint = package["experimentVersion"] == sp.VERSION
    if pinpoint:
        si.require(package["types"] == sp.TYPES, '款项类型口径与预注册不一致')
        by_type = {kind: _counts([i for i in items if i["type"] == kind], results) for kind in sp.TYPES}
        groups = {"paragraph": ("paragraph_existing", "paragraph_absent"),
                  "item": ("item_existing", "item_absent"), "all_errors": sp.ERROR_TYPES,
                  "supported_controls": tuple(k for k in sp.TYPES if k not in sp.ERROR_TYPES)}
        summary = {name: _counts([i for i in items if i["type"] in kinds], results)
                   for name, kinds in groups.items()}
        recovery = {}
        for name, supported in (("errors", False), ("supported_controls", True)):
            entries = []
            for item in items:
                if (item["expected"] == "supported") != supported or not results[item["id"]]["flagged"]:
                    continue
                recovered = results[item["id"]]["recoveries"]
                entries.append({"id": item["id"], "sourceLocation": item.get("sourceLocation"),
                                "status": _recovery_status(recovered, item.get("sourceLocation")),
                                "recoveries": recovered})
            recovery[name] = {status: {"count": sum(e["status"] == status for e in entries),
                                       "ids": [e["id"] for e in entries if e["status"] == status]}
                              for status in ("consistent", "inconsistent", "undetermined")}
            recovery[name]["entries"] = entries
        report = {"byType": by_type, "summary": summary, "recovery": recovery, "items": results}
    else:
        si.require(package["experimentVersion"] == si.VERSION, '未知材料版本')
        # 第二份材料只报告扩展相对原规则新增的标记，不合并检出结果。
        report = {"addedByExpected": {label: {"total": sum(i["expected"] == label for i in items),
                    "addedIds": [i["id"] for i in items if i["expected"] == label
                                 and results[i["id"]]["flagged"] and not results[i["id"]]["original"]["flagged"]]}
                    for label in sorted({i["expected"] for i in items})}}
        for row in report["addedByExpected"].values():
            row["addedCount"] = len(row["addedIds"])
    report = {"schemaVersion": 1, "mode": "pinpoint" if pinpoint else "formal-added-only",
              "consistency": {"matched": True, "originalFlaggedIds": original_ids},
              "inputSha256": {"run/SHA256SUMS.txt": si.sha256(directory / "SHA256SUMS.txt"),
                              "run/package.json": si.sha256(package_path),
                              "run/inputs/" + si.SOURCE: si.sha256(source_path),
                              "analysis/rules.json": si.sha256(rules_path),
                              "analysis/SHA256SUMS.txt": si.sha256(rules_path.parent / "SHA256SUMS.txt")},
              "ruleModuleSha256": {name: si.sha256(ROOT / name) for name in (
                  "backend/core/statute_check.py", "backend/core/statute_locate.py",
                  "tools/competition/statute_posthoc.py", "tools/competition/statute_injection.py",
                  "tools/competition/statute_pinpoint.py")},
              "methods": ["事后扩展，同批材料样本内检验，不是独立验证。仅 error 级算标记。",
                          "逐项读取 package.items 的 statement、contexts、question、expected；不读取标签决定规则结果。",
                          "规则读取 run/inputs/scenarios/deposit_insurance.txt 完整节选。",
                          f"复用 ATTRIBUTION_MIN={sc.ATTRIBUTION_MIN}、ATTRIBUTION_GAP={sc.ATTRIBUTION_GAP}，不新增阈值。",
                          "项级比较仅使用项正文；不把款总领句拼入各项。项级判错比较最佳项与所引项。",
                          "回指每层比较第一、二名；不足阈值则该层及以下为 None，只返回确定前缀。",
                          "回指以每条引用 error 所在整句计算，不更改原 finding；并行列表用 origin 与 findingIndex 关联。",
                          "按材料汇总回指：任一确定层冲突为不一致，否则任一目标层未定为 undetermined，其余一致；无引用 error 亦为 undetermined。",
                          "formal 材料只报告新增标记，按 expected 分组，不参与款项材料汇总。"],
              **report}
    output.mkdir(parents=True, exist_ok=True)
    si.write_json(output / "posthoc.json", report)
    si.checksums(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("analyze")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.run, args.output)
    rows = result.get("summary", result.get("addedByExpected"))
    counts = {key: {k: v for k, v in row.items() if k not in ("originalIds", "extendedIds")}
              for key, row in rows.items()}
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
