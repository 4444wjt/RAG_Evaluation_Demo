"""Offline gate replay must reproduce the archived verdicts and signal them through exit codes."""

from pathlib import Path
import importlib.util
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "gate_replay.py"
FULL_RUN = PROJECT_ROOT / "docs" / "competition" / "evidence" / "20261005T144504Z-full-39511424" / "api-response.json"
DEMO = PROJECT_ROOT / "frontend" / "src" / "fixtures" / "demo.json"

spec = importlib.util.spec_from_file_location("gate_replay", SCRIPT)
gate_replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate_replay)


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize(("path", "expected"), [(FULL_RUN, 1), (DEMO, 0)], ids=["full-run-blocked", "demo-passed"])
def test_cli_exit_code_matches_archived_verdict(path, expected):
    completed = _run(path)
    assert completed.returncode == expected, completed.stdout + completed.stderr
    assert "一致" in completed.stdout and "不一致" not in completed.stdout


@pytest.mark.parametrize("path", [FULL_RUN, DEMO], ids=["full-run", "demo"])
def test_replay_reproduces_archived_gate(path):
    result = gate_replay.load_result(path)
    assert gate_replay.replay(result)["gate_passed"] is result["gate_passed"]


def test_stricter_threshold_blocks_a_passing_snapshot():
    assert gate_replay.main([str(DEMO), "--faith", "1.01"]) == gate_replay.EXIT_BLOCK


def test_missing_summary_is_rejected(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"gate_passed": True}), encoding="utf-8")
    assert gate_replay.main([str(broken)]) == gate_replay.EXIT_INVALID


# ---- CI 报告：JUnit XML 与作业摘要 ----
def _junit(path, *extra, tmp_path):
    report = tmp_path / "gate.junit.xml"
    code = gate_replay.main([str(path), "--junit", str(report), *extra])
    return code, ET.parse(report).getroot()


def test_junit_names_the_failing_metric_and_gap(tmp_path):
    code, root = _junit(FULL_RUN, tmp_path=tmp_path)
    assert code == gate_replay.EXIT_BLOCK
    assert root.get("tests") == "2" and root.get("failures") == "1"
    failed = [case for case in root.iter("testcase") if case.find("failure") is not None]
    assert len(failed) == 1 and "pos_faithfulness" in failed[0].get("name")
    message = failed[0].find("failure").get("message")
    assert "0.7375" in message and "0.8" in message and "0.0625" in message


def test_junit_has_no_failures_when_gate_passes(tmp_path):
    code, root = _junit(DEMO, tmp_path=tmp_path)
    assert code == gate_replay.EXIT_PASS
    assert root.get("failures") == "0" and len(list(root.iter("testcase"))) == 2


@pytest.mark.parametrize(("path", "faith", "ctx"), [
    (DEMO, "1.01", "0.8"), (DEMO, "0.8", "1.01"), (FULL_RUN, "0.7", "0.8"), (FULL_RUN, "0.7375", "0.9834"),
])
def test_junit_failures_agree_with_gate_verdict(tmp_path, path, faith, ctx):
    code, root = _junit(path, "--faith", faith, "--ctx", ctx, tmp_path=tmp_path)
    assert (root.get("failures") != "0") == (code == gate_replay.EXIT_BLOCK)


def test_gate_checks_treat_missing_value_as_failure():
    result = {"summary": [{"model": "m", "faithfulness": 0.9, "context_precision": 0.9,
                           "pos_faithfulness": float("nan"), "pos_context_precision": 0.9}]}
    model, checks = gate_replay.gate_checks(result, 0.8, 0.8)
    assert model == "m" and [c["passed"] for c in checks] == [False, True]
    assert gate_replay.replay(result, 0.8, 0.8)["gate_passed"] is False


def test_summary_is_appended_as_markdown_table(tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("existing\n", encoding="utf-8")
    assert gate_replay.main([str(FULL_RUN), "--summary", str(summary)]) == gate_replay.EXIT_BLOCK
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("existing\n")
    assert "FAIL（拦截）" in text and "| 正向忠实度 `pos_faithfulness` | 0.7375 | 0.8 | ❌ 差 0.0625 |" in text
    assert "与本次回放一致" in text


def test_invalid_input_writes_error_case(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")
    code, root = _junit(broken, tmp_path=tmp_path)
    assert code == gate_replay.EXIT_INVALID
    assert root.get("errors") == "1" and root.find(".//error") is not None


def test_action_runs_gate_replay_with_declared_inputs():
    text = (PROJECT_ROOT / "action.yml").read_text(encoding="utf-8")
    action = yaml.safe_load(text)
    assert action["runs"]["using"] == "composite" and action["branding"]
    steps = action["runs"]["steps"]
    assert all("shell" in step for step in steps if "run" in step)
    replay_step = next(step for step in steps if step.get("id") == "replay")
    assert "$GITHUB_ACTION_PATH/scripts/gate_replay.py" in replay_step["run"]
    referenced = set(re.findall(r"\$\{\{ inputs\.([\w-]+) \}\}", text))
    assert referenced == set(action["inputs"])
