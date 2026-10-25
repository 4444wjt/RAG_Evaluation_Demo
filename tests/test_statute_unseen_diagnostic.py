"""只用已暴露的三部法规；第三部完整 CLI 演练不是未见验证。"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.competition import statute_unseen_diagnostic as ud

ROOT = ud.ROOT
THIRD = ROOT / "work/statute-injection/onboarding-20261023/run/package/contexts/plain.txt"
OLD = {"information_disclosure": "政府信息公开条例", "deposit_insurance": "存款保险条例"}


def third_source():
    raw = THIRD.read_bytes().decode("utf-8")
    title, blank, source = raw.split("\n", 2)
    assert "不动产登记暂行条例" in title and blank == ""
    return source


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    base = tmp_path_factory.mktemp("unseen-rehearsal")
    source = base / "source.txt"
    source.write_bytes(third_source().encode("utf-8"))
    manifest = ud.make(source, "不动产登记暂行条例", base / "package")
    assert (manifest["E"], manifest["K"]) == (24, 25)
    assert manifest["excludedIds"] == []
    return base


@pytest.fixture(scope="module")
def rehearsal(package):
    metadata = ud.run(package / "package", package / "run")
    assert metadata["completed"], "离线演练异常：立即停止，不修被测代码或凑预期"
    result = ud.analyze(package / "run", package / "analysis.json")
    assert result["H1"]["detected"] == 24 and result["H1"]["falsePositives"] == 0
    assert result["H1"]["statuses"] == {"findings": 24, "no_error_found": 25}
    assert all(v["matched"] == v["applicable"] == v["denominator"] == 49
               for v in result["H2"]["variants"].values()), "与锁定预期不同：停止，不调实现"
    assert all(result[h]["passed"] for h in ("H1", "H2", "H3"))
    return package, result


@pytest.mark.parametrize("name,title", OLD.items())
def test_old_materials_reuse_exact_items(tmp_path, name, title):
    original = ROOT / f"scenarios/{name}.txt"
    text = original.read_bytes().decode("utf-8")
    # 仅包装成旧出题器需要的首行；没有获取新法规。
    expected, excluded = ud.so.build_items(f"{title}（节选）\n\n" + text)
    manifest = ud.make(original, title, tmp_path / "package")
    data = ud.load(tmp_path / "package/items.json")
    assert [{k: v for k, v in i.items() if k != "expectedStatus"} for i in data["items"]] == expected
    assert data["excluded"] == excluded
    assert manifest["E"] == sum(i["expected"] == "not_supported" for i in expected)
    assert manifest["K"] == sum(i["expected"] == "supported" for i in expected)
    assert ud.verify_package(tmp_path / "package") == manifest


def test_only_registered_exclusion_and_stable_ids(tmp_path):
    source = third_source()
    order, _, paragraphs, _ = ud.so.structure(source)
    article = order[0]
    # 只复制已用材料首款到次款，使下一款也逐字含内容；测试唯一剔除规则。
    text = f"{ud.so.label(article)}　{paragraphs[article, 1]}\n{paragraphs[article, 1]}\n"
    path = tmp_path / "source.txt"
    path.write_bytes(text.encode("utf-8"))
    result = ud.make(path, "不动产登记暂行条例", tmp_path / "package")
    assert result["excludedIds"] == [f"paragraph_existing-A{article}P1toP2", f"paragraph_existing-A{article}P2toP1"]
    assert (result["E"], result["K"]) == (1, 3)
    assert len(ud.load(tmp_path / "package/items.json")["items"]) == 4


@pytest.mark.parametrize("name", list(ud.EXPECTED))
def test_each_failure_is_exact_mechanical_mutation(package, name):
    data = ud.load(package / "package/items.json")
    item = data["items"][0]
    payload = ud.load(package / "package/requests/base" / f"{item['id']}.json")
    before = deepcopy(payload)
    variants, na = ud.failure_variants(payload)
    assert payload == before and not na
    job = next(j for j in data["jobs"] if j["itemId"] == item["id"] and j["group"] == name)
    assert (package / "package" / job["file"]).read_bytes() == variants[name]
    assert job["expectedStatus"] == ud.EXPECTED[name]
    if name == "F3":
        with pytest.raises(ValueError):
            json.loads(variants[name])
        assert variants[name] == json.dumps(payload, ensure_ascii=False, sort_keys=True)[:-1].encode("utf-8")
        return
    changed = json.loads(variants[name])
    field = "answer" if name in ("F2", "F7", "F9") else "contexts" if name == "F8" else "structureReference" if name == "F10" else "source"
    assert {k: v for k, v in changed.items() if k != field} == {k: v for k, v in payload.items() if k != field}
    if name == "F1":
        assert changed["source"] == ""
    elif name == "F2":
        assert changed["answer"] == ""
    elif name == "F4":
        assert not ud.HEADER.search(changed["source"])
        assert changed["source"] == ud.HEADER.sub("", payload["source"])
    elif name == "F5":
        first, second = list(ud.HEADER.finditer(payload["source"]))[:2]
        block = payload["source"][first.start():second.start()].rstrip("\n")
        assert changed["source"].count(block) == payload["source"].count(block) + 1
    elif name == "F6":
        assert changed["source"].replace("\ufeff", "", 1) == payload["source"]
        assert "第\ufeff" in changed["source"]
    elif name == "F7":
        assert changed["answer"].replace("\u2060", "", 1) == payload["answer"]
        assert "第\u2060" in changed["answer"]
    elif name == "F8":
        assert changed["contexts"][0].replace("条\u200b", "条", 1) == payload["contexts"][0]
    elif name == "F9":
        assert changed["answer"] == ud.ARTICLE.sub("第一千条", payload["answer"], count=1)
    elif name == "F10":
        ref = ud.reference(payload["source"])
        observed = changed["structureReference"]
        diffs = [n for n, (a, b) in enumerate(zip(ref["units"], observed["units"])) if a != b]
        assert len(diffs) == 2
        a, b = diffs
        assert observed["units"][a] == {**ref["units"][a], "body": ref["units"][b]["body"]}
        assert observed["units"][b] == {**ref["units"][b], "body": ref["units"][a]["body"]}


def test_f7_f9_not_applicable_separate_from_denominator():
    payload = ud.request(third_source(), "按照规定办理。")
    variants, absent = ud.failure_variants(payload)
    assert absent == ["F7", "F9"]
    assert set(variants) == set(ud.EXPECTED) - set(absent)


def test_make_records_not_applicable(tmp_path, monkeypatch):
    original = ud.so.build_items

    def build(text):
        rows, excluded = original(text)
        rows[0]["statement"] = "按照规定办理。"
        return rows, excluded

    monkeypatch.setattr(ud.so, "build_items", build)
    source = tmp_path / "source.txt"
    source.write_bytes(third_source().encode("utf-8"))
    ud.make(source, "不动产登记暂行条例", tmp_path / "package")
    data = ud.load(tmp_path / "package/items.json")
    first = data["items"][0]["id"]
    assert data["notApplicable"] == {"F7": [first], "F9": [first]}
    assert len([j for j in data["jobs"] if j["group"] == "F7"]) == 48


@pytest.mark.parametrize("errors,controls,minimum,maximum", [
    (0, 0, 0, 0), (1, 1, 1, 0), (9, 9, 9, 0), (10, 10, 9, 1),
    (11, 11, 10, 1), (19, 19, 18, 1), (20, 20, 18, 2), (21, 21, 19, 2),
    (24, 25, 22, 2), (31, 31, 28, 3),
])
def test_threshold_integer_boundaries(errors, controls, minimum, maximum):
    assert ud.thresholds(errors, controls) == {"minDetected": minimum, "maxFalsePositives": maximum}


def test_existing_output_refused_before_any_call(package, tmp_path, monkeypatch):
    out = tmp_path / "exists"
    out.mkdir()
    monkeypatch.setattr(ud, "invoke", lambda _: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="已存在"):
        ud.make(package / "source.txt", "不动产登记暂行条例", out)
    with pytest.raises(ValueError, match="已存在"):
        ud.run(package / "package", out)


@pytest.mark.parametrize("mutation", ["source", "request", "schema", "extra", "missing"])
def test_all_manifest_hashes_checked_before_run(package, tmp_path, monkeypatch, mutation):
    copy = tmp_path / "package"
    shutil.copytree(package / "package", copy)
    if mutation == "source":
        (copy / "source.txt").write_bytes(b"changed")
    elif mutation == "request":
        next((copy / "requests/base").glob("*.json")).write_bytes(b"{}")
    elif mutation == "schema":
        (copy / "report-schema.json").write_bytes(b"{}")
    elif mutation == "extra":
        (copy / "extra.txt").write_bytes(b"extra")
    else:
        (copy / "items.json").unlink()
    monkeypatch.setattr(ud, "invoke", lambda _: pytest.fail("hash mismatch must stop before subprocess"))
    with pytest.raises(ValueError, match="不符"):
        ud.run(copy, tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_complete_third_material_rehearsal_and_determinism(rehearsal):
    base, result = rehearsal
    again = ud.analyze(base / "run", base / "analysis-again.json")
    assert (base / "analysis.json").read_bytes() == (base / "analysis-again.json").read_bytes()
    assert again == result
    ud.analyze(base / "run", base / "analysis-again.json")
    assert (base / "analysis.json").read_bytes() == (base / "analysis-again.json").read_bytes()
    assert result["thresholds"] == {"minDetected": 22, "maxFalsePositives": 2}
    assert result["conclusion"] == "成立"
    assert result["reportOnly"]["authorPreparationSeconds"] is None
    assert result["reportOnly"]["authorCorrectionCount"] is None
    assert not result["H1"]["unavailableIds"]
    metadata = ud.load(base / "run/run.json")
    assert len(metadata["jobs"]) == 588  # 49 base + 490 failure + 49 extended
    events = [json.loads(line) for line in (base / "run/events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1176
    for start, end in zip(events[::2], events[1::2]):
        assert start["event"] == "start" and end["event"] == "end"
        assert start["id"] == end["id"] and start["time"] <= end["time"]


@pytest.mark.parametrize("mutation", ["empty", "bad_json", "bad_schema", "exit", "stderr", "missing", "h2"])
def test_analysis_rejects_contract_failures_and_missing(rehearsal, tmp_path, mutation):
    base, _ = rehearsal
    run = tmp_path / "run"
    shutil.copytree(base / "run", run)
    job = ud.load(run / "run.json")["jobs"][0]
    target = run / "results" / job["id"]
    if mutation == "empty":
        (target / "stdout.bin").write_bytes(b"")
    elif mutation == "bad_json":
        (target / "stdout.bin").write_bytes(b"{")
    elif mutation == "bad_schema":
        report = ud.load(target / "stdout.bin")
        del report["limitations"]
        ud.write_json(target / "stdout.bin", report)
    elif mutation == "exit":
        ud.write_json(target / "exit.json", {"returncode": 0})
    elif mutation == "stderr":
        (target / "stderr.bin").write_bytes(b"unexpected warning")
    elif mutation == "missing":
        (target / "stdout.bin").unlink()
    else:
        failure = next(j for j in ud.load(run / "run.json")["jobs"] if j["group"] == "F1")
        shutil.copyfile(target / "stdout.bin", run / "results" / failure["id"] / "stdout.bin")
    result = ud.analyze(run, tmp_path / "a.json")
    assert not result["H3"]["passed"]
    assert result["conclusion"] == ("证据不足" if mutation == "missing" else "不成立")
    if mutation == "h2":
        assert not result["H2"]["passed"]


@pytest.mark.parametrize("detected,fp,expected", [(22, 2, True), (21, 2, False), (22, 3, False)])
def test_h1_decision_at_thresholds(rehearsal, tmp_path, detected, fp, expected):
    base, _ = rehearsal
    run = tmp_path / "run"
    shutil.copytree(base / "run", run)
    items = ud.load(run / "package/items.json")["items"]
    errors = [i for i in items if i["expected"] == "not_supported"]
    controls = [i for i in items if i["expected"] == "supported"]
    # 分析器反例：仅改保存结果，明确不是被测入口实调用。
    for item, status in ([(i, "no_error_found") for i in errors[detected:]] + [(i, "findings") for i in controls[:fp]]):
        directory = run / "results" / f"base--{item['id']}"
        report = ud.load(directory / "stdout.bin")
        report.update(status=status, flagged=status == "findings")
        ud.write_json(directory / "stdout.bin", report)
        ud.write_json(directory / "exit.json", {"returncode": ud.EXIT_CODES[status]})
    result = ud.analyze(run, tmp_path / "a.json")
    assert result["H1"]["passed"] is expected
    assert result["H3"]["passed"]
    assert result["conclusion"] == ("成立" if expected else "不成立")


def test_check_source_appends_attempts_with_real_cli(package, tmp_path):
    log = tmp_path / "attempts.jsonl"
    one = ud.check_source(package / "source.txt", log)
    broken = tmp_path / "source.txt"
    broken.write_bytes(third_source().replace("第八条", "第\ufeff八条", 1).encode("utf-8"))
    # 首条不可见字符后仍有后续合法条号；占位选第一个可识别标题，入口复核原文。
    two = ud.check_source(broken, log)
    assert one["status"] == "no_error_found"
    assert two["status"] == "review" and two["diagnosticCodes"]
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert rows == [one, two] and one["sourceSha256"] != two["sourceSha256"]


def test_uncaught_exception_stops_without_retry(package, tmp_path, monkeypatch):
    calls = []

    def simulated(path):
        calls.append(path)
        return subprocess.CompletedProcess([], 1, b"", b"Traceback (most recent call last):\nSIMULATED\n")

    monkeypatch.setattr(ud, "invoke", simulated)
    metadata = ud.run(package / "package", tmp_path / "run")
    assert len(calls) == 1 and not metadata["completed"]
    assert metadata["stopReason"] == "uncaught_exception"
    result = ud.analyze(tmp_path / "run", tmp_path / "a.json")
    assert not result["H3"]["passed"] and result["conclusion"] == "不成立"


def test_timeout_preserves_partial_streams_and_stops(package, tmp_path, monkeypatch):
    calls = []

    def simulated(path):
        calls.append(path)
        raise subprocess.TimeoutExpired(["SIMULATED"], 60, output=b"partial stdout", stderr=b"partial stderr")

    monkeypatch.setattr(ud, "invoke", simulated)
    metadata = ud.run(package / "package", tmp_path / "run")
    assert len(calls) == 1 and metadata["stopReason"] == "TimeoutExpired"
    directory = tmp_path / "run/results" / metadata["jobs"][0]["id"]
    assert (directory / "stdout.bin").read_bytes() == b"partial stdout"
    assert (directory / "stderr.bin").read_bytes() == b"partial stderr"
    assert ud.load(directory / "exit.json") == {"returncode": None, "runnerError": "TimeoutExpired"}
    result = ud.analyze(tmp_path / "run", tmp_path / "a.json")
    assert not result["H3"]["passed"] and result["conclusion"] == "证据不足"


def test_analyze_uses_only_run_snapshot(rehearsal, tmp_path, monkeypatch):
    base, result = rehearsal
    # 确认不再读取当前 schema 或入口/原题包，也不发起子进程。
    monkeypatch.setattr(ud, "SCHEMA", tmp_path / "absent-schema.json")
    monkeypatch.setattr(ud, "ENTRY", tmp_path / "absent-entry.py")
    monkeypatch.setattr(ud, "invoke", lambda _: pytest.fail("analysis must be offline"))
    assert ud.analyze(base / "run", tmp_path / "a.json") == result


@pytest.mark.parametrize("broken", ["status_list", "status_object", "nonfinite", "exit_record"])
def test_h3_bad_output_is_recorded_without_analyzer_crash(package, tmp_path, monkeypatch, broken):
    # 分析器反例：构造部分保存记录，不冒充真实 CLI 调用。
    run = tmp_path / "run"
    run.mkdir()
    shutil.copytree(package / "package", run / "package")
    data = ud.load(run / "package/items.json")
    jobs = deepcopy(data["jobs"])
    jobs.extend({**j, "id": f"extended--{j['itemId']}", "group": "extended"}
                for j in data["jobs"] if j["group"] == "base")
    ud.write_json(run / "run.json", {"jobs": jobs, "completed": False, "stopReason": "interrupted"})
    directory = run / "results" / jobs[0]["id"]
    directory.mkdir(parents=True)
    report = {"schemaVersion": 1, "status": "no_error_found", "flagged": False, "engine": "base",
              "diagnostics": [], "sourceAudit": None, "result": {}, "provenance": None,
              "limitations": ["分析器构造反例"]}
    if broken == "status_list":
        report["status"] = []
    elif broken == "status_object":
        report["status"] = {}
    elif broken == "nonfinite":
        report["result"] = {"value": float("nan")}
    (directory / "stdout.bin").write_bytes(json.dumps(report, ensure_ascii=False).encode("utf-8"))
    (directory / "stderr.bin").write_bytes(b"")
    ud.write_json(directory / "exit.json", {"returncode": 0})
    if broken == "exit_record":
        (directory / "exit.json").write_bytes(b"{")
    monkeypatch.setattr(ud, "invoke", lambda _: pytest.fail("analysis must not invoke target"))
    result = ud.analyze(run, tmp_path / "a.json")
    assert not result["H3"]["passed"] and result["conclusion"] == "证据不足"
    first = result["H3"]["failures"][0]
    assert first["id"] == jobs[0]["id"]
    if broken == "nonfinite":
        assert "invalid_json: ValueError" in first["problems"]
    elif broken == "exit_record":
        assert "invalid_exit_record" in first["problems"]
    else:
        assert "exit_status_mismatch" in first["problems"]


def test_cli_help_and_refusal(package, tmp_path):
    command = [str(ud.PYTHON), "-I", "-B", str(ROOT / "tools/competition/statute_unseen_diagnostic.py")]
    help_result = subprocess.run([*command, "--help"], capture_output=True, timeout=20)
    assert help_result.returncode == 0
    existing = tmp_path / "exists"
    existing.mkdir()
    refused = subprocess.run([*command, "make", "--source", str(package / "source.txt"),
                              "--title", "不动产登记暂行条例", "--out", str(existing)], capture_output=True, timeout=20)
    assert refused.returncode == 2 and "已存在" in refused.stderr.decode("utf-8")
