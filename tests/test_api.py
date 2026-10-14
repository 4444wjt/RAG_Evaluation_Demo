"""Offline contracts and HTTP integration tests for the local evaluation API."""

from pathlib import Path
import subprocess
import sys
import textwrap

from copy import deepcopy
import json

import numpy as np
import pandas as pd
from pydantic import ValidationError
import pytest

from backend.api.schemas import EvaluateRequest, EvaluateResponse
from backend.api.serialization import serialize_evaluation_result

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _check_isolated_api(entry_point, tmp_path):
    # A fresh interpreter prevents other backend tests' module cache from
    # hiding an accidental engine import. Networking is blocked in the child.
    script = textwrap.dedent("""
        import asyncio
        import importlib.abc
        import socket
        import sys

        sys.path.insert(0, sys.argv[1])

        # Windows builds asyncio's internal socketpair using loopback TCP.
        # Create only that loop before blocking sockets; application import,
        # lifespan startup and all ASGI requests stay under the network guard.
        loop = asyncio.new_event_loop()

        blocked = (
            "main", "backend.config", "backend.core", "backend.adapters",
            "backend.services", "backend.storage", "backend.reporting",
            "dotenv", "langchain", "langchain_community", "langchain_core",
            "langchain_openai", "ragas", "chromadb", "sentence_transformers",
            "torch", "transformers", "datasets", "matplotlib",
        )

        class BlockEngineImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + ".")
                       for name in blocked):
                    raise AssertionError("API loaded engine dependency: " + fullname)
                return None

        sys.meta_path.insert(0, BlockEngineImports())

        def no_network(*args, **kwargs):
            raise AssertionError("API skeleton attempted a network operation")

        socket.create_connection = no_network
        socket.getaddrinfo = no_network
        socket.socket.connect = no_network
        socket.socket.connect_ex = no_network
        socket.socket.sendto = no_network

        from fastapi.testclient import TestClient
        from backend.api.app import app, create_app

        application = app if sys.argv[2] == "asgi" else create_app()
        try:
            with TestClient(application,
                            backend_options={"loop_factory": lambda: loop}) as client:
                response = client.get("/health")
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}
                if sys.argv[2] == "scope":
                    assert set(client.get("/openapi.json").json()["paths"]) == {"/health", "/evaluate", "/inputs"}
                    assert client.post("/evaluate", json={"resources": {}}).status_code == 422
                    assert client.post("/uploads").status_code == 404
        finally:
            if not loop.is_closed():
                loop.close()

        assert not any(module == name or module.startswith(name + ".")
                       for module in sys.modules for name in blocked)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(PROJECT_ROOT), entry_point],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_startup_and_health_do_not_load_evaluation_engine(tmp_path):
    _check_isolated_api("factory", tmp_path)


def test_asgi_entry_point_exposes_health(tmp_path):
    _check_isolated_api("asgi", tmp_path)


def test_api_exposes_raw_inputs_but_no_legacy_upload_endpoint(tmp_path):
    _check_isolated_api("scope", tmp_path)


def test_evaluate_request_defaults():
    assert EvaluateRequest().model_dump() == {
        "doc_path": None,
        "testset_path": None,
        "benchmark": None,
        "benchmark_limit": 20,
        "model_names": None,
        "faith_threshold": None,
        "ctx_prec_threshold": None,
        "version_label": None,
    }


def test_evaluate_request_preserves_zero_thresholds_and_model_order():
    supplied = {
        "doc_path": "scenarios/example/document.txt",
        "testset_path": "scenarios/example/cases.csv",
        "benchmark": "truthful_qa",
        "benchmark_limit": 1,
        "model_names": ["model-b", "model-a"],
        "faith_threshold": 0.0,
        "ctx_prec_threshold": 0.0,
        "version_label": "实验备注\n第二行",
    }
    assert EvaluateRequest.model_validate(supplied).model_dump() == supplied


@pytest.mark.parametrize("supplied", [
    {"resources": {}},
    {"benchmark_limit": 0},
    {"benchmark_limit": -1},
    {"model_names": []},
    {"benchmark": "unsupported"},
    {"faith_threshold": -0.01},
    {"faith_threshold": 1.01},
    {"faith_threshold": float("nan")},
    {"ctx_prec_threshold": -0.01},
    {"ctx_prec_threshold": 1.01},
    {"ctx_prec_threshold": float("inf")},
])
def test_evaluate_request_rejects_invalid_parameters(supplied):
    with pytest.raises(ValidationError):
        EvaluateRequest.model_validate(supplied)


@pytest.fixture
def fake_evaluation_result():
    trace = ' {"statements": [{"text": "原子事实", "verdict": "supported", "evidence": "证据"}]}\n'
    summary = [{
        "model": "model-b",
        "score": np.float64(0.0),
        "hallucination_statement_count": np.int64(0),
        "trace_fact_error_count": np.int32(0),
        "trace_fabrication_count": np.int64(0),
        "hallucination_ratio": np.float64(np.nan),
        "refusal_accuracy": None,
    }, {
        "model": "model-a",
        "score": np.float64(0.8756),
        "hallucination_statement_count": None,
        "trace_fact_error_count": None,
        "trace_fabrication_count": None,
        "hallucination_ratio": None,
        "refusal_accuracy": np.float64(1.0),
    }]
    return {
        "summary": summary,
        "summary_df": pd.DataFrame(summary),
        "gate_passed": np.bool_(False),
        "gate_message": "质量门禁拦截。\n请检查证据。",
        "chart_path": "result_chart.png",
        "html_path": "ragas_report.html",
        "model_dfs": {
            "model-b": pd.DataFrame([{
                "question": "中文问题\n第二行",
                "answer": "",
                "contexts": np.array(["证据一", "证据二"]),
                "hallucination_trace": trace,
                "faithfulness": np.float64(0.0),
                "missing": pd.NA,
            }]),
            "model-a": pd.DataFrame(columns=["question", "answer"]),
        },
        "total_elapsed_seconds": np.float64(0.0),
    }


def test_evaluation_result_serializes_all_eight_fields(fake_evaluation_result):
    result = fake_evaluation_result
    payload = serialize_evaluation_result(result)
    assert set(payload) == {
        "summary", "summary_df", "gate_passed", "gate_message",
        "chart_path", "html_path", "model_dfs", "total_elapsed_seconds",
    }
    expected_summary = [{
        "model": "model-b", "score": 0.0,
        "hallucination_statement_count": 0,
        "trace_fact_error_count": 0, "trace_fabrication_count": 0,
        "hallucination_ratio": None, "refusal_accuracy": None,
    }, {
        "model": "model-a", "score": 0.8756,
        "hallucination_statement_count": None,
        "trace_fact_error_count": None, "trace_fabrication_count": None,
        "hallucination_ratio": None, "refusal_accuracy": 1.0,
    }]
    assert payload["summary"] == expected_summary
    assert payload["summary_df"] == expected_summary
    assert payload["gate_passed"] is False
    assert payload["gate_message"] == result["gate_message"]
    assert payload["chart_path"] == result["chart_path"]
    assert payload["html_path"] == result["html_path"]
    assert payload["total_elapsed_seconds"] == 0.0
    assert payload["model_dfs"] == {
        "model-b": [{
            "question": "中文问题\n第二行",
            "answer": "",
            "contexts": ["证据一", "证据二"],
            "hallucination_trace": result["model_dfs"]["model-b"].iloc[0]["hallucination_trace"],
            "faithfulness": 0.0,
            "missing": None,
        }],
        "model-a": [],
    }
    assert type(payload["summary"][0]["hallucination_statement_count"]) is int
    assert type(payload["summary"][0]["score"]) is float
    assert json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False)) == payload
    response = EvaluateResponse.model_validate(payload)
    assert response.model_dump(mode="json") == payload
    assert json.loads(response.model_dump_json()) == payload


@pytest.mark.parametrize("missing", [
    None, float("nan"), np.float64(np.nan), pd.NA, pd.NaT,
    float("inf"), float("-inf"), np.float64(np.inf),
])
def test_evaluation_result_missing_and_nonfinite_values_become_null(
    fake_evaluation_result, missing,
):
    fake_evaluation_result["summary"][0]["hallucination_ratio"] = missing
    fake_evaluation_result["chart_path"] = None
    fake_evaluation_result["html_path"] = np.nan
    fake_evaluation_result["total_elapsed_seconds"] = np.inf
    payload = serialize_evaluation_result(fake_evaluation_result)
    assert payload["summary"][0]["hallucination_ratio"] is None
    assert payload["chart_path"] is None
    assert payload["html_path"] is None
    assert payload["total_elapsed_seconds"] is None
    assert EvaluateResponse.model_validate(payload).model_dump(mode="json") == payload
    assert '"hallucination_ratio": null' in json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize("value, expected", [
    (np.int64(0), 0), (np.float32(0.0), 0.0), (np.bool_(False), False),
    ("", ""), ("nan", "nan"), ("null", "null"), (np.str_("中文"), "中文"),
])
def test_evaluation_result_preserves_zero_false_and_strings(
    fake_evaluation_result, value, expected,
):
    fake_evaluation_result["summary"][0]["model"] = value
    payload = serialize_evaluation_result(fake_evaluation_result)
    actual = payload["summary"][0]["model"]
    assert actual == expected
    assert type(actual) is type(expected)


def test_evaluation_result_serialization_does_not_mutate_source(fake_evaluation_result):
    result = fake_evaluation_result
    keys = tuple(result)
    summary = result["summary"]
    before_summary = deepcopy(summary)
    summary_df = result["summary_df"]
    before_summary_df = summary_df.copy(deep=True)
    model_dfs = result["model_dfs"]
    before_model_dfs = {name: frame.copy(deep=True) for name, frame in model_dfs.items()}
    contexts = model_dfs["model-b"].iloc[0]["contexts"]
    before_contexts = contexts.copy()

    payload = serialize_evaluation_result(result)
    payload["summary"][0]["model"] = "changed output only"
    payload["model_dfs"]["model-b"][0]["contexts"].append("new output only")

    assert tuple(result) == keys
    assert result["summary"] is summary
    pd.testing.assert_frame_equal(pd.DataFrame(summary), pd.DataFrame(before_summary))
    assert type(summary[0]["hallucination_statement_count"]) is np.int64
    assert np.isnan(summary[0]["hallucination_ratio"])
    assert result["summary_df"] is summary_df
    pd.testing.assert_frame_equal(summary_df, before_summary_df)
    assert result["model_dfs"] is model_dfs
    for name, frame in model_dfs.items():
        pd.testing.assert_frame_equal(frame, before_model_dfs[name])
    assert model_dfs["model-b"].iloc[0]["contexts"] is contexts
    np.testing.assert_array_equal(contexts, before_contexts)
    assert model_dfs["model-b"].iloc[0]["missing"] is pd.NA
    assert type(result["gate_passed"]) is np.bool_
    assert type(result["total_elapsed_seconds"]) is np.float64


def test_evaluation_response_requires_complete_contract(fake_evaluation_result):
    payload = serialize_evaluation_result(fake_evaluation_result)
    missing_field = {key: value for key, value in payload.items() if key != "gate_passed"}
    with pytest.raises(ValidationError):
        EvaluateResponse.model_validate(missing_field)
    with pytest.raises(ValidationError):
        EvaluateResponse.model_validate({**payload, "extra_metric": 1})


def test_evaluation_result_rejects_unsupported_objects(fake_evaluation_result):
    fake_evaluation_result["summary"][0]["model"] = object()
    with pytest.raises(TypeError, match="Unsupported result value type"):
        serialize_evaluation_result(fake_evaluation_result)


def _run_evaluation_http(tmp_path, case):
    """Exercise actual ASGI requests in a fresh, network-blocked interpreter."""
    setup = textwrap.dedent(r"""
        import asyncio
        import builtins
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import nullcontext
        import importlib.abc
        import io
        import os
        from pathlib import Path
        import socket
        import sys
        from threading import Event
        from types import ModuleType
        from unittest.mock import Mock, patch

        sys.path.insert(0, sys.argv[1])
        # Bootstrap only stdlib wakeup sockets before applying the offline guard.
        loops = [asyncio.new_event_loop(), asyncio.new_event_loop()]
        blocked = (
            "main", "backend.config", "backend.core", "backend.adapters",
            "backend.services", "backend.storage", "backend.reporting",
            "dotenv", "langchain", "langchain_community", "langchain_core",
            "langchain_openai", "ragas", "chromadb", "sentence_transformers",
            "torch", "transformers", "datasets", "matplotlib",
        )

        class BlockEngineImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + ".")
                       for name in blocked):
                    raise AssertionError("Real engine dependency imported: " + fullname)
                return None

        sys.meta_path.insert(0, BlockEngineImports())

        def no_network(*args, **kwargs):
            raise AssertionError("Offline API test attempted network access")

        socket.create_connection = no_network
        socket.getaddrinfo = no_network
        socket.socket.connect = no_network
        socket.socket.connect_ex = no_network
        socket.socket.sendto = no_network

        def guarded_open(original):
            def open_file(file, *args, **kwargs):
                if isinstance(file, (str, bytes, os.PathLike)):
                    name = Path(os.fsdecode(file)).name.lower()
                    if name == ".env" or name.startswith(".env."):
                        raise AssertionError("Offline API test attempted dotenv access")
                return original(file, *args, **kwargs)
            return open_file

        builtins.open = guarded_open(builtins.open)
        io.open = guarded_open(io.open)

        import numpy as np
        import pandas as pd
        from fastapi.testclient import TestClient
        from backend.api.app import create_app
        import backend.api.service as service

        root = Path(sys.argv[2]).resolve()
        (root / "scenarios").mkdir()
        (root / "demo.txt").write_text("offline document", encoding="utf-8")
        (root / "scenarios" / "document.md").write_text("offline document", encoding="utf-8")
        (root / "scenarios" / "cases.csv").write_text("question,ground_truth\nq,a\n", encoding="utf-8")
        names = {"deepseek-chat", "Pro/Qwen/Qwen2.5-7B-Instruct", "glm-5.2-fast-preview"}
        service._configured_model_names = Mock(return_value=names)

        def sample_result():
            summary = [{
                "model": "deepseek-chat",
                "score": np.float64(0.0),
                "hallucination_statement_count": np.int64(0),
                "hallucination_ratio": np.nan,
            }]
            return {
                "summary": summary,
                "summary_df": pd.DataFrame(summary),
                "gate_passed": np.bool_(False),
                "gate_message": "质量门禁拦截",
                "chart_path": "result_chart.png",
                "html_path": "ragas_report.html",
                "model_dfs": {
                    "deepseek-chat": pd.DataFrame([{
                        "question": "离线问题", "answer": "",
                        "contexts": np.array(["原文证据"]),
                        "hallucination_trace": ' {"statements": []}\n',
                    }]),
                },
                "total_elapsed_seconds": np.float64(0.0),
            }

        def client_for(evaluator=None, index=0):
            application = create_app(evaluator, project_root=root)
            return TestClient(
                application, backend_options={"loop_factory": lambda: loops[index]},
            )
    """)
    script = setup + "\ntry:\n" + textwrap.indent(textwrap.dedent(case), "    ")
    script += "\nfinally:\n    for loop in loops:\n        if not loop.is_closed():\n            loop.close()\n"
    result = subprocess.run(
        [sys.executable, "-c", script, str(PROJECT_ROOT), str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_post_evaluate_returns_eight_fields_and_passes_parameters_once(tmp_path):
    _run_evaluation_http(tmp_path, r"""
        result = sample_result()
        fake = Mock(return_value=result)
        supplied = {
            "doc_path": "scenarios/document.md",
            "testset_path": "scenarios/cases.csv",
            "benchmark": None,
            "benchmark_limit": 3,
            "model_names": ["glm-5.2-fast-preview", "deepseek-chat", "deepseek-chat"],
            "faith_threshold": 0.0,
            "ctx_prec_threshold": 0.0,
            "version_label": "实验备注\n第二行",
        }
        with client_for(fake) as client:
            assert client.get("/health").json() == {"status": "ok"}
            fake.assert_not_called()
            response = client.post("/evaluate", json=supplied)
        assert response.status_code == 200
        assert set(response.json()) == {
            "summary", "summary_df", "gate_passed", "gate_message",
            "chart_path", "html_path", "model_dfs", "total_elapsed_seconds",
        }
        assert response.json() == service.serialize_evaluation_result(result)
        assert response.json()["gate_passed"] is False
        assert response.json()["summary"][0]["hallucination_ratio"] is None
        assert response.json()["model_dfs"]["deepseek-chat"][0]["contexts"] == ["原文证据"]
        expected = dict(supplied)
        expected["doc_path"] = str((root / "scenarios" / "document.md").resolve())
        expected["testset_path"] = str((root / "scenarios" / "cases.csv").resolve())
        fake.assert_called_once_with(
            expected, resources=None, model_factory=None, progress_cb=None,
        )
        assert isinstance(result["summary_df"], pd.DataFrame)
        assert np.isnan(result["summary"][0]["hallucination_ratio"])
    """)


def test_post_evaluate_default_engine_is_loaded_lazily_and_called_once(tmp_path):
    _run_evaluation_http(tmp_path, r"""
        engine = Mock(return_value=sample_result())
        fake_core = ModuleType("backend.core")
        fake_core.__path__ = []
        fake_pipeline = ModuleType("backend.core.pipeline")
        fake_pipeline.evaluate = engine
        with client_for() as client:
            assert client.get("/health").status_code == 200
            engine.assert_not_called()
            with patch.dict(sys.modules, {
                "backend.core": fake_core,
                "backend.core.pipeline": fake_pipeline,
            }):
                response = client.post("/evaluate", json={})
        assert response.status_code == 200
        engine.assert_called_once_with(
            {
                "doc_path": str((root / "demo.txt").resolve()),
                "testset_path": None, "benchmark": None, "benchmark_limit": 20,
                "model_names": None, "faith_threshold": None,
                "ctx_prec_threshold": None, "version_label": None,
            },
            resources=None, model_factory=None, progress_cb=None,
        )
        assert os.environ["MPLBACKEND"] == "Agg"
        service._configured_model_names.assert_not_called()
    """)


def test_post_evaluate_controlled_files_and_benchmark_precedence(tmp_path):
    _run_evaluation_http(tmp_path, r"""
        fake = Mock(side_effect=lambda *args, **kwargs: sample_result())
        with client_for(fake) as client:
            for suffix in (".txt", ".md", ".pdf", ".docx"):
                path = root / "scenarios" / ("input" + suffix)
                path.write_text("offline", encoding="utf-8")
                assert client.post("/evaluate", json={
                    "doc_path": "scenarios/" + path.name,
                }).status_code == 200
                assert fake.call_args.args[0]["doc_path"] == str(path.resolve())
            for name in ("testset_example.json", "testset_example.csv",
                         "scenarios/input.json", "scenarios/input.csv", "scenarios/input.txt"):
                path = root / name
                path.write_text("offline", encoding="utf-8")
                assert client.post("/evaluate", json={"testset_path": name}).status_code == 200
                assert fake.call_args.args[0]["testset_path"] == str(path.resolve())
            assert client.post("/evaluate", json={
                "doc_path": r"scenarios\document.md",
                "benchmark": "truthful_qa",
                "testset_path": "../../unused.csv",
                "benchmark_limit": 1,
            }).status_code == 200
            assert fake.call_args.args[0]["benchmark"] == "truthful_qa"
            assert fake.call_args.args[0]["testset_path"] == "../../unused.csv"
            assert fake.call_args.args[0]["doc_path"] == str((root / "scenarios/document.md").resolve())
        assert fake.call_count == 10
    """)


def test_post_evaluate_read_only_mode_blocks_before_engine(tmp_path):
    _run_evaluation_http(tmp_path, r"""
        fake = Mock(return_value=sample_result())
        with patch.dict(os.environ, {"VERISCOPE_READ_ONLY": "1"}):
            with client_for(fake) as client:
                response = client.post("/evaluate", json={})
                assert response.status_code == 423, response.text
                assert isinstance(response.json()["detail"], str)
                assert client.get("/health").json() == {"status": "ok"}
        assert fake.call_count == 0
        with client_for(fake, 1) as client:
            assert client.post("/evaluate", json={"benchmark_limit": 51}).status_code == 422
        assert fake.call_count == 0
    """)


def test_post_evaluate_request_errors_are_safe_and_do_not_call_engine(tmp_path):
    _run_evaluation_http(tmp_path, r"""
        fake = Mock(return_value=sample_result())
        (root / "scenarios" / "folder.txt").mkdir()
        marker = "LOCAL_SENSITIVE_MARKER"
        cases = [
            ({"api_key": marker}, 422),
            ({"resources": {}}, 422),
            ({"model_names": []}, 422),
            ({"model_names": [marker]}, 422),
            ({"faith_threshold": -0.1}, 422),
            ({"benchmark_limit": 0}, 422),
            ({"doc_path": "../outside.txt"}, 403),
            ({"doc_path": str(root / "demo.txt")}, 403),
            ({"doc_path": r"C:\outside.txt"}, 403),
            ({"doc_path": r"C:relative.txt"}, 403),
            ({"doc_path": r"\\server\share\outside.txt"}, 403),
            ({"doc_path": r"\outside.txt"}, 403),
            ({"doc_path": "scenarios/document.md:stream"}, 403),
            ({"doc_path": "scenarios/../../outside.txt"}, 403),
            ({"doc_path": "README.md"}, 403),
            ({"doc_path": ".env"}, 403),
            ({"doc_path": ""}, 422),
            ({"doc_path": "scenarios/\x00.txt"}, 422),
            ({"doc_path": "scenarios/unknown.exe"}, 422),
            ({"doc_path": "scenarios/missing.txt"}, 404),
            ({"doc_path": "scenarios/folder.txt"}, 422),
            ({"testset_path": "../outside.csv"}, 403),
            ({"testset_path": "scenarios/missing.csv"}, 404),
            ({"testset_path": "scenarios/input.xml"}, 422),
        ]
        with client_for(fake) as client:
            for supplied, status in cases:
                response = client.post("/evaluate", json=supplied)
                assert response.status_code == status, (supplied, response.text)
                assert isinstance(response.json()["detail"], str)
                assert marker not in response.text
                assert "Traceback" not in response.text
            assert client.post("/evaluate", content="{", headers={
                "Content-Type": "application/json",
            }).status_code == 422
        fake.assert_not_called()
    """)


@pytest.mark.parametrize("failure", ["engine", "serialization", "response_validation"])
def test_post_evaluate_runtime_failure_is_safe_and_releases_lock(tmp_path, failure):
    case = r"""
        marker = "LOCAL_PROVIDER_SECRET_MARKER"
        mode = FAILURE
        good = sample_result()
        bad = sample_result()
        if mode == "engine":
            first = RuntimeError("Traceback provider error " + marker)
        elif mode == "serialization":
            bad["summary"][0]["model"] = object()
            first = bad
        else:
            bad["gate_message"] = {"private_error": marker}
            first = bad
        fake = Mock(side_effect=[first, good])
        with client_for(fake) as client:
            failed = client.post("/evaluate", json={})
            assert failed.status_code == 500
            assert failed.json() == {"detail": service._RUNTIME_ERROR}
            assert marker not in failed.text
            assert "Traceback" not in failed.text
            assert "RuntimeError" not in failed.text
            retry = client.post("/evaluate", json={})
            assert retry.status_code == 200
            assert retry.json() == service.serialize_evaluation_result(good)
        assert fake.call_count == 2
    """.replace("FAILURE", repr(failure))
    _run_evaluation_http(tmp_path, case)


@pytest.mark.parametrize("stage, second_app", [
    ("engine", False), ("serialization", False), ("engine", True),
])
def test_post_evaluate_busy_returns_409_until_execution_and_serialization_finish(
    tmp_path, stage, second_app,
):
    case = r"""
        stage = STAGE
        second_app = SECOND_APP
        started = Event()
        release = Event()
        result = sample_result()
        original_serializer = service.serialize_evaluation_result

        def wait_for_release():
            started.set()
            assert release.wait(10), "Test did not release the first request"

        def execute(*args, **kwargs):
            if stage == "engine":
                wait_for_release()
            return result

        def serialize(value):
            if stage == "serialization":
                wait_for_release()
            return original_serializer(value)

        fake = Mock(side_effect=execute)
        service.serialize_evaluation_result = serialize
        with client_for(fake) as first_client:
            second_context = client_for(fake, index=1) if second_app else nullcontext(first_client)
            with second_context as second_client, ThreadPoolExecutor(max_workers=1) as pool:
                first_request = pool.submit(first_client.post, "/evaluate", json={})
                try:
                    assert started.wait(10), "First request did not reach the blocking stage"
                    busy = second_client.post("/evaluate", json={})
                    assert busy.status_code == 409
                    fake.assert_called_once()
                    assert second_client.get("/health").json() == {"status": "ok"}
                finally:
                    release.set()
                assert first_request.result(timeout=10).status_code == 200
                assert second_client.post("/evaluate", json={}).status_code == 200
        assert fake.call_count == 2
    """.replace("STAGE", repr(stage)).replace("SECOND_APP", repr(second_app))
    _run_evaluation_http(tmp_path, case)


@pytest.mark.parametrize("destination, expected_status", [
    ("outside", 403), ("private_root", 403), ("wrong_extension", 422),
])
def test_controlled_path_rejects_resolved_symlink_targets(
    tmp_path, monkeypatch, fake_evaluation_result, destination, expected_status,
):
    from unittest.mock import Mock
    import backend.api.service as service

    root = tmp_path / "project"
    root.mkdir()
    (root / "scenarios").mkdir()
    link = root / "scenarios" / "link.txt"
    targets = {
        "outside": tmp_path / "outside.txt",
        "private_root": root / "private.txt",
        "wrong_extension": root / "scenarios" / "private.bin",
    }
    target = targets[destination]
    original_resolve = Path.resolve

    # Simulate resolve() output; real symlink creation requires privileges on
    # some Windows hosts. The actual service policy and file checks are used.
    def resolve(path, *args, **kwargs):
        return target if path == link else original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    fake = Mock(return_value=fake_evaluation_result)
    with pytest.raises(service.EvaluationAPIError) as error:
        service.run_evaluation(
            EvaluateRequest(doc_path="scenarios/link.txt"), fake, project_root=root,
        )
    assert error.value.status_code == expected_status
    fake.assert_not_called()
