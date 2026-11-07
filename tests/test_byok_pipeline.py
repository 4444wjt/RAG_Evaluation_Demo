"""BYOK reuses the real engine hooks, without author credentials or shared paths."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from backend.core import pipeline


def test_visitor_test_generation_has_no_builtin_fallback(monkeypatch):
    selected = {"name": "visitor", "base_url": "https://api.deepseek.com/v1"}
    factory = Mock(return_value=object())
    generate = Mock(side_effect=RuntimeError("offline failure"))
    monkeypatch.setattr(pipeline, "generate_testset", generate)
    with pytest.raises(RuntimeError, match="offline failure"):
        pipeline.prepare_testset({}, {"chunks": ["chunk"]}, factory, testset_model=selected)
    assert factory.call_args_list == [((selected,), {})]
    assert generate.call_count == 1


def test_visitor_runtime_requires_explicit_factory_before_any_resources(monkeypatch, tmp_path):
    prepare = Mock(side_effect=AssertionError("must not prepare resources"))
    monkeypatch.setattr(pipeline, "prepare_resources", prepare)
    with pytest.raises(ValueError, match="访客.*模型工厂"):
        pipeline.evaluate({"model_names": ["visitor"]}, runtime={
            "output_dir": str(tmp_path), "model_configs": [{"name": "visitor"}], "testset_model": "visitor"})
    prepare.assert_not_called()


def test_runtime_does_not_read_registry_and_writes_every_artifact_under_run(monkeypatch, tmp_path):
    configs = [{"name": "visitor", "base_url": "https://api.deepseek.com/v1"}]
    paths = []
    monkeypatch.setattr(pipeline, "select_model_configs", Mock(side_effect=AssertionError("no server registry")))
    monkeypatch.setattr(pipeline, "generate_evaluation_chart", lambda summary, threshold, path: paths.append(path))
    monkeypatch.setattr(pipeline, "generate_html_report", lambda summary, **kwargs: paths.extend([kwargs["chart_path"], kwargs["output_path"]]))
    monkeypatch.setattr(pipeline, "save_eval_record", lambda record, **kwargs: paths.append(kwargs["path"]))
    loop = Mock(return_value={"summary": [{"model": "visitor", "faithfulness": .9, "context_precision": .9,
        "pos_faithfulness": .9, "pos_context_precision": .9}], "model_dfs": {"visitor": pd.DataFrame()}})
    monkeypatch.setattr(pipeline, "run_models", loop)
    payload = pipeline.evaluate({"model_names": ["visitor"]}, resources={"testset": []},
        model_factory=lambda config: None, runtime={"output_dir": str(tmp_path), "model_configs": configs, "testset_model": "visitor"})
    assert (tmp_path / "multi_model_summary.csv").exists()
    assert all(Path(path).parent == tmp_path for path in paths)
    assert Path(payload["chart_path"]).parent == tmp_path
    assert Path(payload["html_path"]).parent == tmp_path
    assert loop.call_args.kwargs["base_dir"] == str(tmp_path)
    assert loop.call_args.kwargs["safe_errors"] is True
    assert loop.call_args.args[0] == configs


def test_visitor_adapter_uses_explicit_key_and_no_retries_or_redirects(monkeypatch):
    from backend.adapters.models.openai_compatible import build_visitor_llm
    from backend.adapters.models.judging import observe_judge
    monkeypatch.setenv("OPENAI_API_KEY", "author-secret-must-not-be-used")
    llm = build_visitor_llm({"name": "visitor", "base_url": "https://api.deepseek.com/v1", "api_key": "visitor-secret"})
    try:
        assert llm.openai_api_key.get_secret_value() == "visitor-secret"
        assert llm.max_retries == 0
        assert llm.http_client.follow_redirects is False
        assert llm.http_async_client.follow_redirects is False
        # Judge observation must not replace the subclass that contains error text.
        assert observe_judge(llm) is llm
    finally:
        llm.http_client.close()
    with pytest.raises(ValueError, match="缺失"):
        build_visitor_llm({"name": "visitor", "base_url": "https://api.deepseek.com/v1"})


def test_visitor_adapter_contains_provider_exception_text(monkeypatch):
    from langchain_openai import ChatOpenAI
    from backend.adapters.models.openai_compatible import build_visitor_llm
    def upstream(*args, **kwargs):
        raise RuntimeError("Authorization: Bearer visitor-secret")
    monkeypatch.setattr(ChatOpenAI, "_generate", upstream)
    llm = build_visitor_llm({"name": "visitor", "base_url": "https://api.deepseek.com/v1", "api_key": "visitor-secret"})
    try:
        with pytest.raises(RuntimeError) as failure:
            llm._generate([])
        assert "visitor-secret" not in str(failure.value)
    finally:
        llm.http_client.close()


def test_visitor_adapter_redacts_reflected_key_before_downstream_parsing(monkeypatch):
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from backend.adapters.models.openai_compatible import build_visitor_llm
    monkeypatch.setattr(ChatOpenAI, "_generate", lambda *args, **kwargs: ChatResult(generations=[
        ChatGeneration(message=AIMessage(content="provider echoed visitor-secret"))]))
    llm = build_visitor_llm({"name": "visitor", "base_url": "https://api.deepseek.com/v1", "api_key": "visitor-secret"})
    try:
        response = llm._generate([])
        assert "visitor-secret" not in response.generations[0].message.content
        assert "visitor-secret" not in response.generations[0].text
    finally:
        llm.http_client.close()


@pytest.mark.parametrize("visitor,fail_generation", [(True, False), (True, True), (False, True)])
def test_chroma_client_closes_once_only_for_visitor_runs(monkeypatch, tmp_path, visitor, fail_generation):
    client = Mock()
    resources = {"retriever": SimpleNamespace(vectorstore=SimpleNamespace(_client=client))}
    monkeypatch.setattr(pipeline, "prepare_resources", Mock(return_value=resources))
    monkeypatch.setattr(pipeline, "prepare_testset", Mock(
        side_effect=RuntimeError("generation failed") if fail_generation else None, return_value=[]))
    monkeypatch.setattr(pipeline, "select_model_configs", lambda names: [{"name": "visitor"}])
    monkeypatch.setattr(pipeline, "run_models", Mock(return_value={"summary": [], "model_dfs": {}}))
    monkeypatch.setattr(pipeline, "generate_evaluation_chart", Mock())
    monkeypatch.setattr(pipeline, "generate_html_report", Mock())
    monkeypatch.setattr(pipeline, "check_quality_gate", lambda *args: {"gate_passed": False, "gate_message": "FAIL"})
    monkeypatch.setattr(pipeline, "save_eval_record", Mock())
    runtime = {"output_dir": str(tmp_path), "model_configs": [{"name": "visitor"}], "testset_model": "visitor"} if visitor else None
    if fail_generation:
        with pytest.raises(RuntimeError, match="generation failed"):
            pipeline.evaluate({"model_names": ["visitor"]}, model_factory=lambda config: None, runtime=runtime)
    else:
        pipeline.evaluate({"model_names": ["visitor"]}, model_factory=lambda config: None, runtime=runtime)
    assert client.close.call_count == (1 if visitor else 0)
