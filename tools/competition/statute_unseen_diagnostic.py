"""未见法规诊断：确定性出题、真实 CLI 运行及离线复算；不抽签、不联网。

仅按已锁定登记第 4、5 节执行。旧材料演练不构成未见验证证据。
用法：venv/Scripts/python.exe -B tools/competition/statute_unseen_diagnostic.py --help
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402
from tools.competition import statute_onboarding as so  # noqa: E402

PYTHON = ROOT / "venv/Scripts/python.exe"
ENTRY = ROOT / "scripts/statute_diagnostic.py"
SCHEMA = ROOT / "schemas/statute-diagnostic.v1.schema.json"
EXPECTED = {f"F{i}": "invalid_input" if i <= 3 else "review" for i in range(1, 11)}
EXIT_CODES = {"no_error_found": 0, "findings": 1, "review": 2, "invalid_input": 2}
ARTICLE = re.compile(rf"第{so.sc.NUM}条")
HEADER = re.compile(rf"(?m)^[ \t\u3000]*第{so.sc.NUM}条")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def reject_constant(value):
    raise ValueError(f"非标准 JSON 常量：{value}")


def report_status(report):
    value = report.get("status") if isinstance(report, dict) else None
    return value if isinstance(value, str) else None


def write_json(path, value):
    Path(path).write_bytes((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                     allow_nan=False) + "\n").encode("utf-8"))


def now():
    return datetime.now(timezone.utc).isoformat()


def append_event(path, value):
    with Path(path).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def fresh(path):
    path = Path(path)
    require(not path.exists(), "输出目录已存在，禁止覆盖或续跑")
    return path


def request(source, answer, question=""):
    return {"schemaVersion": 1, "sourceFormat": "article-lines-v1", "source": source,
            "answer": answer, "contexts": [source], "question": question, "engine": "base"}


def reference(source):
    """机械参照只用于 F10，不是独立结构真值。保留款和项各自的正文。"""
    units = []
    for row in so.sl.provision_lines(source):
        loc = row["location"]
        if loc is None:
            continue
        article, paragraph, item = loc
        body = so.sc.normalize(row["raw"]).strip()
        if row["header"]:
            body = so.sc.HEADER.match(body).group(2)
        elif item is not None:
            body = body[so.sc.ITEM.match(body).end():]
        units.append({"article": article, "paragraph": paragraph, "item": item, "body": body.strip()})
    return {"schemaVersion": 1, "units": units}


def failure_variants(payload):
    """返回变体及不适用编号；F7/F9 无引用时不造新引用、不计入实运行分母。"""
    source = payload["source"]
    heads = list(HEADER.finditer(source))
    require(bool(heads), "无法构造失效集：原文没有条标题")
    variants, not_applicable = {}, []
    for name in EXPECTED:
        value = deepcopy(payload)
        if name == "F1":
            value["source"] = ""
        elif name == "F2":
            value["answer"] = ""
        elif name == "F3":
            # 确定性的截断文本，禁止经 JSON writer 再次编码为合法字符串。
            variants[name] = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:-1].encode("utf-8")
            continue
        elif name == "F4":
            value["source"] = HEADER.sub("", source)
        elif name == "F5":
            end = heads[1].start() if len(heads) > 1 else len(source)
            block = source[heads[0].start():end].rstrip("\r\n")
            value["source"] = source[:end].rstrip("\r\n") + "\n\n" + block + "\n\n" + source[end:]
        elif name == "F6":
            pos = source.index("第", heads[0].start()) + 1
            value["source"] = source[:pos] + "\ufeff" + source[pos:]
        elif name in ("F7", "F9"):
            match = ARTICLE.search(value["answer"])
            if match is None:
                not_applicable.append(name)
                continue
            start, end = match.span()
            if name == "F7":
                value["answer"] = value["answer"][:start + 1] + "\u2060" + value["answer"][start + 1:]
            else:
                value["answer"] = value["answer"][:start] + "第一千条" + value["answer"][end:]
        elif name == "F8":
            pos = value["contexts"][0].index("条") + 1
            text = value["contexts"][0]
            value["contexts"][0] = text[:pos] + "\u200b" + text[pos:]
        elif name == "F10":
            ref = reference(source)
            paragraphs = [u for u in ref["units"] if u["item"] is None]
            pair = next(((a, b) for a, b in zip(paragraphs, paragraphs[1:])
                         if a["article"] == b["article"] and a["paragraph"] == 1 and b["paragraph"] == 2), None)
            require(pair is not None, "F10 无法构造：节选没有多款条")
            pair[0]["body"], pair[1]["body"] = pair[1]["body"], pair[0]["body"]
            value["structureReference"] = ref
        variants[name] = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return variants, not_applicable


def make(source_path, title, out):
    out = fresh(out)
    raw = Path(source_path).read_bytes()
    source = raw.decode("utf-8")
    # 旧函数需要标题，仅在内存中包装；输出请求中的 source/contexts 逐字节保留。
    rows, excluded = so.build_items(f"{title}（节选）\n\n" + source)
    items, jobs, contents, na = [], [], {}, {"F7": [], "F9": []}
    for row in rows:
        item = {**row, "expectedStatus": "findings" if row["expected"] == "not_supported" else "no_error_found"}
        items.append(item)
        payload = request(source, row["statement"], row["question"])
        base_file = f"requests/base/{row['id']}.json"
        contents[base_file] = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        jobs.append({"id": f"base--{row['id']}", "itemId": row["id"], "group": "base",
                     "file": base_file, "expectedStatus": item["expectedStatus"]})
        variants, absent = failure_variants(payload)
        for name in absent:
            na[name].append(row["id"])
        for name, data in variants.items():
            file = f"requests/{name}/{row['id']}.json"
            contents[file] = data
            jobs.append({"id": f"{name}--{row['id']}", "itemId": row["id"], "group": name,
                         "file": file, "expectedStatus": EXPECTED[name]})
    errors = sum(i["expected"] == "not_supported" for i in items)
    out.mkdir(parents=True)
    (out / "source.txt").write_bytes(raw)
    (out / "report-schema.json").write_bytes(SCHEMA.read_bytes())
    write_json(out / "items.json", {"items": items, "excluded": excluded, "jobs": jobs, "notApplicable": na})
    for file, data in contents.items():
        path = out / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {"version": 1, "title": title, "sourceSha256": hashlib.sha256(raw).hexdigest(),
                "E": errors, "K": len(items) - errors, "excludedIds": [i["id"] for i in excluded],
                "files": {p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob("*")) if p.is_file()}}
    write_json(out / "manifest.json", manifest)
    return manifest


def verify_package(package):
    package = Path(package)
    manifest = load(package / "manifest.json")
    actual = {p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file()}
    require(actual == set(manifest["files"]) | {"manifest.json"}, "manifest 文件清单不符")
    for file, expected in manifest["files"].items():
        path = (package / file).resolve()
        require(path.is_relative_to(package.resolve()), "manifest 文件越界")
        require(digest(path) == expected, f"SHA-256 不符：{file}")
    require(digest(package / "source.txt") == manifest["sourceSha256"], "原文 SHA-256 不符")
    return manifest


def invoke(path):
    # 固定仓库 venv；隔离模式，不继承 Python 路径配置，不生成仓库缓存。
    return subprocess.run([str(PYTHON), "-I", "-B", str(ENTRY), "--input", str(Path(path).resolve()), "--json"],
                          cwd=ROOT, capture_output=True, timeout=60)


def check_source(source_path, log):
    source = Path(source_path).read_bytes().decode("utf-8")
    match = HEADER.search(source)
    require(match is not None, "原文没有首条条号，无法生成占位请求")
    article = ARTICLE.search(match.group()).group()
    with tempfile.TemporaryDirectory(prefix="statute-source-") as temp:
        path = Path(temp) / "request.json"
        write_json(path, request(source, f"按照{article}规定。"))
        completed = invoke(path)
    try:
        report = json.loads(completed.stdout)
    except (ValueError, UnicodeError):
        report = {}
    row = {"time": now(), "sourceSha256": digest(source_path), "status": report.get("status"),
           "diagnosticCodes": [d["code"] for d in report.get("diagnostics", [])],
           "returncode": completed.returncode, "stdout": completed.stdout.decode("utf-8", errors="replace"),
           "stderr": completed.stderr.decode("utf-8", errors="replace")}
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    append_event(log, row)
    require(not completed.stderr and bool(report), "被测入口异常，已记录；停止，不重试")
    return row


def run(package, out):
    package, out = Path(package).resolve(), fresh(out)
    verify_package(package)  # 核对全部字节后才创建输出目录。
    out.mkdir(parents=True)
    shutil.copytree(package, out / "package")
    verify_package(out / "package")
    data = load(out / "package/items.json")
    jobs = deepcopy(data["jobs"])
    # extended 是另一次真实 CLI 调用；仅有效题，不影响主判定 H1/H2。
    for job in data["jobs"]:
        if job["group"] == "base":
            payload = load(out / "package" / job["file"])
            payload["engine"] = "extended"
            file = f"extended/{job['itemId']}.json"
            (out / "extended").mkdir(exist_ok=True)
            write_json(out / file, payload)
            jobs.append({**job, "id": f"extended--{job['itemId']}", "group": "extended", "file": file})
    for job in jobs:
        job["input"] = job["file"] if job["group"] == "extended" else f"package/{job['file']}"
    metadata = {"jobs": jobs, "completed": False, "stopReason": None,
                "codeSha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in
                               (ENTRY, ROOT / "backend/core/statute_check.py", ROOT / "backend/core/statute_locate.py",
                                Path(__file__).resolve())}}
    write_json(out / "run.json", metadata)
    (out / "results").mkdir()
    for job in jobs:
        append_event(out / "events.jsonl", {"event": "start", "id": job["id"], "time": now()})
        directory = out / "results" / job["id"]
        directory.mkdir()
        try:
            result = invoke(out / job["input"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            metadata["stopReason"] = type(exc).__name__
            (directory / "stdout.bin").write_bytes(getattr(exc, "stdout", None) or b"")
            (directory / "stderr.bin").write_bytes(getattr(exc, "stderr", None) or b"")
            write_json(directory / "exit.json", {"returncode": None, "runnerError": type(exc).__name__})
            append_event(out / "events.jsonl", {"event": "end", "id": job["id"], "time": now(), "error": type(exc).__name__})
            break
        (directory / "stdout.bin").write_bytes(result.stdout)
        (directory / "stderr.bin").write_bytes(result.stderr)
        write_json(directory / "exit.json", {"returncode": result.returncode})
        append_event(out / "events.jsonl", {"event": "end", "id": job["id"], "time": now(), "returncode": result.returncode})
        if b"Traceback (most recent call last)" in result.stderr:
            metadata["stopReason"] = "uncaught_exception"
            break
    else:
        metadata["completed"] = True
        metadata["stopReason"] = "completed"
    write_json(out / "run.json", metadata)
    return metadata


def thresholds(errors, controls):
    return {"minDetected": (9 * errors + 9) // 10, "maxFalsePositives": controls // 10}


def tally(items, statuses):
    errors = [i for i in items if i["expected"] == "not_supported"]
    controls = [i for i in items if i["expected"] == "supported"]
    return {"E": len(errors), "K": len(controls),
            "detected": sum(statuses.get(i["id"]) == "findings" for i in errors),
            "falsePositives": sum(statuses.get(i["id"]) == "findings" for i in controls),
            "unavailableIds": [i["id"] for i in items if statuses.get(i["id"]) not in ("findings", "no_error_found")],
            "statuses": dict(sorted(Counter(statuses.get(i["id"]) or "missing" for i in items).items()))}


def analyze(run_dir, out):
    run_dir, out = Path(run_dir), Path(out)
    metadata = load(run_dir / "run.json")
    manifest = verify_package(run_dir / "package")
    data = load(run_dir / "package/items.json")
    schema = load(run_dir / "package/report-schema.json")
    validator = Draft202012Validator({**schema, "$ref": "#/$defs/report"})
    statuses = {"base": {}, "extended": {}}
    contract_failures, missing, failures, review_codes = [], [], [], Counter()
    variant_rows = {name: [] for name in EXPECTED}
    expected_jobs = {j["id"] for j in data["jobs"]} | {f"extended--{i['id']}" for i in data["items"]}
    require(len(metadata["jobs"]) == len(expected_jobs) and {j["id"] for j in metadata["jobs"]} == expected_jobs,
            "运行计划与题包不符：缺少或重复请求")
    for job in metadata["jobs"]:
        directory = run_dir / "results" / job["id"]
        report, problems, returncode = None, [], None
        files = [directory / name for name in ("stdout.bin", "stderr.bin", "exit.json")]
        if not all(p.is_file() for p in files):
            missing.append(job["id"])
            problems.append("missing_output")
        else:
            try:
                # 明确 UTF-8；空 stdout、JSON 标量和不合契约字段均失败。
                report = json.loads(files[0].read_bytes().decode("utf-8"), parse_constant=reject_constant)
                problems.extend("schema: " + e.message for e in validator.iter_errors(report))
            except (ValueError, UnicodeError) as exc:
                problems.append("invalid_json: " + type(exc).__name__)
            if files[1].read_bytes():
                problems.append("stderr_not_empty")
            status = report_status(report)
            try:
                exit_record = load(files[2])
                require(isinstance(exit_record, dict), "退出记录不是对象")
                returncode = exit_record.get("returncode")
            except (ValueError, UnicodeError):
                problems.append("invalid_exit_record")
            if status not in EXIT_CODES or returncode != EXIT_CODES[status]:
                problems.append("exit_status_mismatch")
        status = report_status(report)
        if problems:
            contract_failures.append({"id": job["id"], "problems": problems})
        if isinstance(report, dict) and status == "review":
            diagnostics = report.get("diagnostics")
            if isinstance(diagnostics, list):
                review_codes.update(d["code"] for d in diagnostics if isinstance(d, dict) and isinstance(d.get("code"), str))
        if job["group"] in statuses:
            statuses[job["group"]][job["itemId"]] = status
        else:
            passed = status == job["expectedStatus"] and status in ("review", "invalid_input") and returncode == 2
            variant_rows[job["group"]].append({"id": job["id"], "status": status, "passed": passed})
            if not passed:
                failures.append(job["id"])
    base = tally(data["items"], statuses["base"])
    bounds = thresholds(base["E"], base["K"])
    require((base["E"], base["K"]) == (manifest["E"], manifest["K"]), "E/K 与题包不符")
    h1 = base["detected"] >= bounds["minDetected"] and base["falsePositives"] <= bounds["maxFalsePositives"]
    h2 = not failures
    h3 = not contract_failures
    completed = metadata["completed"] and not missing
    conclusion = ("不成立" if metadata["stopReason"] == "uncaught_exception" else
                  "证据不足" if not completed else "成立" if h1 and h2 and h3 else "不成立")
    result = {"E": base["E"], "K": base["K"], "excludedIds": manifest["excludedIds"],
              "thresholds": bounds, "H1": {"passed": h1, **base,
                  "missedErrorIds": [i["id"] for i in data["items"] if i["expected"] == "not_supported" and statuses["base"].get(i["id"]) != "findings"],
                  "falsePositiveIds": [i["id"] for i in data["items"] if i["expected"] == "supported" and statuses["base"].get(i["id"]) == "findings"]},
              "H2": {"passed": h2, "failureIds": failures, "variants": {
                  name: {"denominator": len(data["items"]), "applicable": len(rows),
                         "matched": sum(r["passed"] for r in rows), "rows": rows,
                         "notApplicableIds": data["notApplicable"].get(name, [])}
                  for name, rows in variant_rows.items()}},
              "H3": {"passed": h3, "checked": len(metadata["jobs"]), "failures": contract_failures},
              "completed": completed, "missingIds": missing, "conclusion": conclusion,
              "reportOnly": {"baseByType": {kind: tally([i for i in data["items"] if i["type"] == kind], statuses["base"]) for kind in so.TYPES},
                  "extended": tally(data["items"], statuses["extended"]),
                  "extendedByType": {kind: tally([i for i in data["items"] if i["type"] == kind], statuses["extended"]) for kind in so.TYPES},
                  "reviewDiagnosticCodes": dict(sorted(review_codes.items())),
                  "reviewDiagnosticCodeScope": "all_jobs_including_base_extended_and_failure_variants",
                  "authorPreparationSeconds": None, "authorCorrectionCount": None,
                  "authorPreparationNote": "运行目录未提供作者计时和修正记录，保留缺失；check-source 日志单独交付。"},
              "limitations": ["旧材料演练不是未见法规验证，不据此提高竞赛评分。", "未检出不等于支持或质量门禁 PASS。", "F10 仅机械不一致测试，不是独立结构参照。"]}
    write_json(out, result)
    return result


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-source")
    check.add_argument("--source", required=True, type=Path)
    check.add_argument("--log", required=True, type=Path)
    maker = commands.add_parser("make")
    maker.add_argument("--source", required=True, type=Path)
    maker.add_argument("--title", required=True)
    maker.add_argument("--out", required=True, type=Path)
    runner = commands.add_parser("run")
    runner.add_argument("--package", required=True, type=Path)
    runner.add_argument("--out", required=True, type=Path)
    analyzer = commands.add_parser("analyze")
    analyzer.add_argument("--run", required=True, type=Path)
    analyzer.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check-source":
            row = check_source(args.source, args.log)
            print(json.dumps({k: row[k] for k in ("status", "diagnosticCodes")}, ensure_ascii=False))
        elif args.command == "make":
            manifest = make(args.source, args.title, args.out)
            print(json.dumps({k: manifest[k] for k in ("E", "K", "excludedIds")}, ensure_ascii=False))
        elif args.command == "run":
            metadata = run(args.package, args.out)
            print(json.dumps({k: metadata[k] for k in ("completed", "stopReason")}, ensure_ascii=False))
            return 0 if metadata["completed"] else 2
        else:
            result = analyze(args.run, args.out)
            print(json.dumps({k: result[k] for k in ("E", "K", "conclusion")}, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
