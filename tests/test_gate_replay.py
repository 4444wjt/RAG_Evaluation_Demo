"""Offline gate replay must reproduce the archived verdicts and signal them through exit codes."""

from pathlib import Path
import importlib.util
import json
import subprocess
import sys

import pytest

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
