"""Real local subprocess replay; synthetic examples do not establish legal accuracy."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend.core.citation_version import check
from test_citation_version import XML, body

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "citation_version_check.py"


def run(*args):
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_one_batch_cli_matches_core_and_replays_self_contained_bundle(tmp_path):
    request = body()
    extra = copy.deepcopy(request["citations"][0]); extra["id"] = "conflict"
    extra["request"]["extent"] = "S"
    request["citations"].append(extra)
    source = tmp_path / "request.json"; report = tmp_path / "report.json"; bundle = tmp_path / "bundle.json"
    write(source, request)
    result = run("check", "--input", source, "--report", report, "--bundle", bundle)
    assert result.returncode == 0, result.stderr
    assert json.loads(report.read_text(encoding="utf-8")) == check(request)
    saved = json.loads(bundle.read_text(encoding="utf-8"))
    assert saved == {"schema": "CitationVersionBundleV1", "input": request, "report": check(request)}
    source.unlink()
    verified = run("verify", "--bundle", bundle)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["verified"] is True


@pytest.mark.parametrize("change", ["counts", "bool_count", "method", "bytes", "request", "checker", "delete_row"])
def test_bundle_tampering_rejected_by_real_recomputation(tmp_path, change):
    data = body()
    bundle = {"schema": "CitationVersionBundleV1", "input": data, "report": check(data)}
    if change == "counts": bundle["report"]["counts"]["invalid"] = 9
    if change == "bool_count": bundle["report"]["counts"]["total"] = True
    if change == "method": bundle["report"]["rows"][0]["output"]["method"] = "A_root_interval"
    if change == "bytes": bundle["input"]["files"][0]["base64"] = base64.b64encode(XML + b" ").decode()
    if change == "request": bundle["input"]["citations"][0]["request"]["check_date"] = "2021-01-01"
    if change == "checker": bundle["report"]["checker"]["source_sha256"]["rules"] = "0" * 64
    if change == "delete_row": bundle["report"]["rows"] = []
    path = tmp_path / "bundle.json"; write(path, bundle)
    result = run("verify", "--bundle", path)
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["verified"] is False


def test_cli_duplicate_json_and_unreadable_input_fail_without_paths(tmp_path):
    path = tmp_path / "private-path-marker.json"
    path.write_text('{"schema":0,"schema":1}', encoding="utf-8")
    result = run("check", "--input", path)
    assert result.returncode == 2 and "private-path-marker" not in result.stderr
    result = run("check", "--input", tmp_path / "absent-private-path-marker.json")
    assert result.returncode == 2 and "private-path-marker" not in result.stderr


@pytest.mark.parametrize("collision", ["input", "existing", "shared"])
def test_cli_refuses_output_collisions_before_any_write(tmp_path, collision):
    source = tmp_path / "input.json"
    write(source, body())
    original = source.read_bytes()
    report, bundle = tmp_path / "report.json", tmp_path / "bundle.json"
    if collision == "input":
        bundle = source
    elif collision == "existing":
        bundle.write_bytes(b"keep this previous record")
    else:
        bundle = report
    result = run("check", "--input", source, "--report", report, "--bundle", bundle)
    assert result.returncode == 2
    assert not report.exists()
    assert source.read_bytes() == original
    if collision == "existing":
        assert bundle.read_bytes() == b"keep this previous record"


def test_correcting_requirement_keeps_source_and_other_row_bindings(tmp_path):
    old = body()
    old["citations"][0]["request"]["extent"] = "S"
    second = copy.deepcopy(old["citations"][0]); second["id"] = "untouched"
    old["citations"].append(second)
    new = copy.deepcopy(old)
    new["citations"][0]["request"]["extent"] = "E"
    reports = []
    for name, request in (("before", old), ("after", new)):
        source = tmp_path / (name + ".json"); bundle = tmp_path / (name + "-bundle.json")
        write(source, request)
        result = run("check", "--input", source, "--bundle", bundle)
        assert result.returncode == 0, result.stderr
        assert run("verify", "--bundle", bundle).returncode == 0
        reports.append(json.loads(bundle.read_text(encoding="utf-8"))["report"])
    before, after = reports
    assert before["rows"][0]["output"]["subchecks"]["extent"]["state"] == "MISMATCH"
    assert after["rows"][0]["output"]["subchecks"]["extent"]["state"] == "MATCH"
    assert before["inputs"]["files"] == after["inputs"]["files"]
    assert before["rows"][1] == after["rows"][1]
    assert before["inputs"]["citations"][1] == after["inputs"]["citations"][1]
    assert before["rows"][0]["output"]["axes"]["known_by"] == after["rows"][0]["output"]["axes"]["known_by"] == "REVIEW"
