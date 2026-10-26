"""Offline tests of the actual function bodies in main.py, without importing it.

Importing main.py would read .env, patch sockets/codecs and import model runtimes.
Compile only named functions and literal constants instead; do not rewrite their
bodies. Real pandas/numpy exercise the data logic; external services are mocked.
This deliberately does not test application startup or third-party integration.
"""

import ast
import base64
import builtins
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

# 让 backend 包可被测试导入：项目根目录加入 sys.path（无论从项目根还是 tests/ 启动）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 阻断 python-dotenv：backend.config 在 import 期会 load_dotenv 读取真实 .env，
# 破坏测试离线性（不读密钥、不依赖环境）。设 load_dotenv=None 令其跳过加载。
_dotenv_stub = ModuleType("dotenv")
_dotenv_stub.load_dotenv = None
sys.modules["dotenv"] = _dotenv_stub


SOURCE = Path(__file__).resolve().parents[1] / "main.py"
FUNCTIONS = {
    "safe_json_loads", "load_custom_testset", "load_benchmark", "_safe_mean",
    "load_history", "save_eval_record", "history_to_dataframe",
    "build_display_df", "empty_display_df", "gate_card_html", "generate_html_report",
    "friendly_error_message", "error_gate_html", "_demo_case_rows",
    "build_demo_result", "_legacy_run_pipeline", "generate_testset",
    "_negative_question_key", "_negative_context", "_fallback_negative_testset",
    "generate_negative_testset", "trace_hallucinations", "_is_refusal", "_short_model_name",
}
CONSTANTS = {
    "VERSION", "FAITH_THRESHOLD", "CTX_PREC_THRESHOLD",
    "SINGLE_CASE_WARN_THRESHOLD", "CHUNK_SIZE", "TOP_K", "TEST_QA_NUM",
    "TRACE_DISPLAY_COLUMNS", "CHUNK_OVERLAP", "EVAL_TIMEOUT", "METRICS", "TRACE_PROMPT_TEMPLATE", "REFUSAL_KEYWORDS",
}


class _TestCoverageSummary:
    """Report executed cases separately from unavailable local research material."""

    def __init__(self):
        self.reports = []
        self.collection_reports = []
        self.collected = 0

    def pytest_runtest_logreport(self, report):
        self.reports.append(report)

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.collection_reports.append(report)

    def pytest_sessionfinish(self, session):
        self.collected = session.testscollected

    def pytest_terminal_summary(self, terminalreporter):
        calls = [report for report in self.reports if report.when == "call"]
        errors = [report for report in self.reports
                  if report.failed and report.when != "call"]
        skipped = {report.nodeid: report for report in self.reports
                   if report.skipped and not hasattr(report, "wasxfail")}
        local_skips = {}
        for nodeid, report in skipped.items():
            reason = str(report.longrepr[2]) if isinstance(report.longrepr, tuple) else str(report.longrepr)
            reason = reason.removeprefix("Skipped: ")
            filename = nodeid.split("::", 1)[0].replace("\\", "/").rsplit("/", 1)[-1]
            if (reason.startswith("missing-local-material:") or
                    (filename == "test_statute_blindspot.py" and
                     reason == "work/ 下没有真实题包")):
                local_skips[nodeid] = reason

        counts = {
            "collected": self.collected,
            "actually_executed": len({r.nodeid for r in calls if r.passed or r.failed}),
            "passed": len({r.nodeid for r in calls if r.passed}),
            "failed": len({r.nodeid for r in calls if r.failed}),
            "error_cases": len({r.nodeid for r in errors}),
            "error_reports": len(errors),
            "skipped": len(skipped),
            "local_material_skipped": len(local_skips),
            "other_skipped": len(skipped) - len(local_skips),
            "call_phase_reached": len({r.nodeid for r in calls}),
            "xfailed": len({r.nodeid for r in self.reports
                            if r.skipped and hasattr(r, "wasxfail")}),
            "collection_errors": sum(r.failed for r in self.collection_reports),
            "collection_skips": sum(r.skipped for r in self.collection_reports),
        }
        lines = [f"{name}={value}" for name, value in counts.items()]
        lines.extend([
            "actually_executed: unique cases with a passed/failed call report; skips excluded.",
            "passed/failed: call outcomes; error_cases: setup/teardown failures (may overlap).",
            "error_reports: setup/teardown reports; collection errors/skips are separate.",
            "call_phase_reached includes in-call skips; xfailed is separate from skipped.",
            "Local material skips (not validated in this run):",
        ])
        lines.extend(f"  {nodeid}: {reason}" for nodeid, reason in sorted(local_skips.items()))
        if not local_skips:
            lines.append("  none")
        terminalreporter.section("Test execution coverage")
        for line in lines:
            terminalreporter.write_line(line)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            try:
                with open(summary_path, "a", encoding="utf-8") as summary:
                    summary.write("\n### Test execution coverage\n\n```text\n" + "\n".join(lines) + "\n```\n")
            except OSError:
                terminalreporter.write_line("Could not write GITHUB_STEP_SUMMARY; counts remain in this log.")


def pytest_configure(config):
    config.pluginmanager.register(_TestCoverageSummary(), "test-execution-coverage")


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch, tmp_path):
    """Fail immediately on networking or dotenv reads; confine relative writes."""
    def no_network(*args, **kwargs):
        pytest.fail("Offline test attempted a network operation")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket.socket, "sendto", no_network)

    def guarded_open(original):
        def open_file(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                name = Path(os.fsdecode(file)).name.lower()
                if name == ".env" or name.startswith(".env."):
                    pytest.fail("Offline test attempted to open a dotenv file")
            return original(file, *args, **kwargs)
        return open_file

    monkeypatch.setattr(builtins, "open", guarded_open(builtins.open))
    monkeypatch.setattr(io, "open", guarded_open(io.open))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def app(tmp_path, offline_guard):
    """Isolated namespace, with all source functions kept intact, per test."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"), filename=str(SOURCE))
    module = ModuleType("rag_under_test")
    module.__dict__.update(
        base64=base64, json=json, re=re, os=os, time=time, datetime=datetime, pd=pd, np=np,
        ENABLE_TRACE=True, MODEL_DISPLAY_MAP={},
        BASE_DIR=str(tmp_path), HISTORY_PATH=str(tmp_path / "history.json"),
        DOC_PATH=str(tmp_path / "doc.txt"), CHART_PATH=str(tmp_path / "chart.png"),
        SUMMARY_OUTPUT=str(tmp_path / "summary.csv"),
        HTML_OUTPUT=str(tmp_path / "report.html"), EMBEDDING_MODEL="fake-local-model",
    )
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in CONSTANTS:
                module.__dict__[target.id] = ast.literal_eval(node.value)
                found.add(target.id)
    assert found == CONSTANTS, "A required source constant changed or disappeared"
    definitions = [node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
    assert {node.name for node in definitions} == FUNCTIONS
    selected = ast.Module(body=definitions, type_ignores=[])
    exec(compile(selected, str(SOURCE), "exec"), module.__dict__)
    module.METRIC_KEYS = [metric["key"] for metric in module.METRICS]
    return module


@pytest.fixture
def run_fake_pipeline(app, monkeypatch, tmp_path):
    """Run the complete production pipeline function with fake service outputs.

    Keep pandas aggregation, score calculation, gate decisions, CSV and history
    writing real. Replace API/embedding/vector-store/evaluator/chart/report I/O.
    """
    fake_matplotlib = ModuleType("matplotlib")
    fake_matplotlib.__path__ = []
    fake_fonts = ModuleType("matplotlib.font_manager")
    fake_fonts.findfont = Mock(return_value="")
    fake_fonts.FontProperties = Mock()
    fake_matplotlib.font_manager = fake_fonts
    monkeypatch.setitem(sys.modules, "matplotlib", fake_matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.font_manager", fake_fonts)

    def run(frames, case_types=None, **options):
        successful = next((frame for frame in frames if isinstance(frame, pd.DataFrame)), None)
        count = len(successful) if successful is not None else 1
        case_types = case_types if case_types is not None else ["positive"] * count
        cases = [{"question": f"q{i}", "ground_truth": f"a{i}", "case_type": case_type}
                 for i, case_type in enumerate(case_types)]
        testset_path = tmp_path / "testset.json"
        testset_path.write_text(json.dumps(cases), encoding="utf-8")
        app.MODELS_CONFIG = [{"name": f"model-{i}"} for i in range(len(frames))]
        app.HuggingFaceEmbeddings = Mock(return_value=object())
        app.load_and_split_doc = Mock(return_value=[SimpleNamespace(page_content="offline text")])
        app.build_vector_store = Mock()
        app.get_llm = Mock(return_value=Mock(invoke=Mock(
            return_value=SimpleNamespace(content='{"statements": []}'))))
        app.rag_answer = Mock(return_value=("offline answer", ["offline context"]))
        app.Dataset = SimpleNamespace(from_dict=Mock(side_effect=lambda data: data))
        app.RunConfig = Mock()
        app.RAGAS_METRICS = [object() for _ in app.METRICS]
        app.evaluate = Mock(side_effect=[
            frame if isinstance(frame, Exception)
            else SimpleNamespace(to_pandas=Mock(return_value=(
                frame.copy(deep=True) if "answer" in frame
                else frame.assign(answer="offline answer"))))
            for frame in frames
        ])
        app.plt = SimpleNamespace(
            rcParams={}, subplots=Mock(return_value=(Mock(), Mock(bar=Mock(return_value=[])))),
            tight_layout=Mock(), savefig=Mock(), close=Mock(),
        )
        app.generate_html_report = Mock()
        return app._legacy_run_pipeline(testset_path=str(testset_path), **options)

    return run
