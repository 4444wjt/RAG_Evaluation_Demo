"""Exercise coverage reporting with real pytest outcomes in a tiny child suite."""

import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest
from conftest import _TestCoverageSummary


CONFTEST = Path(__file__).with_name("conftest.py")


def _run_suite(tmp_path, source, filename="test_cases.py", tests_root=False):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_bytes(CONFTEST.read_bytes())
    (tests / filename).write_text(source, encoding="utf-8")
    if tests_root:
        (tests / "pytest.ini").write_bytes(CONFTEST.with_name("pytest.ini").read_bytes())
    summary = tmp_path / "step-summary.md"
    env = os.environ.copy()
    env.update(GITHUB_STEP_SUMMARY=str(summary), PYTHONIOENCODING="utf-8",
               PYTEST_ADDOPTS="", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-ra", "tests"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=60,
    )
    counts = {name: int(value) for name, value in
              re.findall(r"^([a-z_]+)=(\d+)$", result.stdout, re.MULTILINE)}
    summary_text = summary.read_text(encoding="utf-8")
    for name, value in counts.items():
        assert f"{name}={value}\n" in summary_text
    return result, counts, summary_text


@pytest.mark.parametrize("tests_root", [False, True])
def test_reports_real_case_outcomes_and_material_skip_reasons(tmp_path, tests_root):
    result, counts, summary = _run_suite(tmp_path, '''
import pytest

@pytest.fixture
def setup_error():
    raise RuntimeError("intentional setup error")

@pytest.fixture
def teardown_error():
    yield
    raise RuntimeError("intentional teardown error")

@pytest.fixture
def local_material():
    pytest.skip("missing-local-material: work/fixture.json")

@pytest.fixture
def setup_and_teardown_error(teardown_error):
    raise RuntimeError("intentional setup error after yield fixture")

def test_pass():
    pass

def test_fail():
    assert False

def test_setup_error(setup_error):
    pass

def test_teardown_error(teardown_error):
    pass

def test_fail_and_teardown_error(teardown_error):
    assert False

def test_two_error_reports(setup_and_teardown_error):
    pass

def test_local_skip(local_material):
    pass

def test_call_local_skip():
    pytest.skip("missing-local-material: work/in-call.json")

@pytest.mark.skip(reason="ordinary platform skip")
def test_other_skip():
    pass

def test_legacy_local_skip():
    pytest.skip("work/ 下没有真实题包")

@pytest.mark.xfail(reason="known failure")
def test_xfail():
    assert False
''', filename="test_statute_blindspot.py", tests_root=tests_root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert counts == {
        "collected": 11, "actually_executed": 4, "passed": 2, "failed": 2,
        "error_cases": 4, "error_reports": 5, "skipped": 4,
        "local_material_skipped": 3, "other_skipped": 1, "call_phase_reached": 7,
        "xfailed": 1, "collection_errors": 0, "collection_skips": 0,
    }
    for suffix, reason in [
        ("test_local_skip", "missing-local-material: work/fixture.json"),
        ("test_call_local_skip", "missing-local-material: work/in-call.json"),
        ("test_legacy_local_skip", "work/ 下没有真实题包"),
    ]:
        prefix = "" if tests_root else "tests/"
        line = f"{prefix}test_statute_blindspot.py::{suffix}: {reason}"
        assert line in result.stdout
        assert line in summary


@pytest.mark.parametrize("tests_root", [False, True])
def test_zero_missing_material_does_not_misclassify_similar_skip(tmp_path, tests_root):
    result, counts, summary = _run_suite(tmp_path, '''
import pytest

def test_pass():
    pass

def test_same_reason_in_other_file():
    pytest.skip("work/ 下没有真实题包")
''', tests_root=tests_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert counts["collected"] == 2
    assert counts["actually_executed"] == counts["passed"] == 1
    assert counts["skipped"] == counts["other_skipped"] == 1
    assert counts["local_material_skipped"] == counts["error_cases"] == 0
    assert "Local material skips (not validated in this run):\n  none" in summary


@pytest.mark.parametrize("nodeid, reason, local", [
    (r"tests\test_statute_blindspot.py::test_real", "work/ 下没有真实题包", 1),
    (r"C:\repo\tests\test_statute_blindspot.py::test_real", "work/ 下没有真实题包", 1),
    (r"tests\test_other.py::test_real", "work/ 下没有真实题包", 0),
    ("tests/prefix_test_statute_blindspot.py::test_real", "work/ 下没有真实题包", 0),
    ("test_statute_blindspot.py::test_real", "work/ 下没有真实题包 extra", 0),
    ("test_other.py::test_real", "missing-local-material: work/fixture.json", 1),
])
def test_local_skip_classification_keeps_exact_file_and_reason(nodeid, reason, local, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    reporter = _TestCoverageSummary()
    reporter.pytest_runtest_logreport(SimpleNamespace(
        nodeid=nodeid, when="setup", passed=False, failed=False, skipped=True,
        longrepr=("file.py", 1, f"Skipped: {reason}"),
    ))
    reporter.pytest_sessionfinish(SimpleNamespace(testscollected=1))
    lines = []
    reporter.pytest_terminal_summary(SimpleNamespace(section=lambda title: None, write_line=lines.append))
    assert f"local_material_skipped={local}" in lines
    assert f"other_skipped={1 - local}" in lines
    assert (f"  {nodeid}: {reason}" in lines) == bool(local)


@pytest.mark.parametrize("source, exitcode, errors, skips", [
    ("raise RuntimeError('intentional collection error')", 2, 1, 0),
    ("import pytest; pytest.skip('module unavailable', allow_module_level=True)", 5, 0, 1),
])
def test_collection_outcomes_are_not_counted_as_test_cases(tmp_path, source, exitcode, errors, skips):
    result, counts, _ = _run_suite(tmp_path, source)
    assert result.returncode == exitcode, result.stdout + result.stderr
    assert counts["collected"] == counts["actually_executed"] == 0
    assert counts["error_cases"] == counts["error_reports"] == counts["skipped"] == 0
    assert counts["collection_errors"] == errors
    assert counts["collection_skips"] == skips
