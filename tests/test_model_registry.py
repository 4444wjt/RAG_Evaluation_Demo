"""本机模型登记：校验、读写、接口不泄露密钥值、只读与本机限制。全部离线。"""

import asyncio
import json
import socket

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api import service
from backend.config import MODELS_CONFIG
from backend.core import model_registry, pipeline

BUILTIN = [model["name"] for model in MODELS_CONFIG]
SECRET = "offline-test-key-value-0123456789"


def entry(**changes):
    model = {"name": "qwen2.5-7b", "display_name": "内网 Qwen",
             "base_url": "http://127.0.0.1:11434/v1", "api_key_env": "VERISCOPE_OLLAMA_API_KEY"}
    model.update(changes)
    return model


# 收集阶段（离线守卫生效前）保存的原始 connect。Windows 事件循环建立时要用本机
# socketpair 连回 127.0.0.1；只在建循环的这一刻放行，请求本身仍在守卫之下。
_LOOPBACK_CONNECT = socket.socket.connect


def _new_loop():
    guarded = socket.socket.connect
    socket.socket.connect = _LOOPBACK_CONNECT
    try:
        return asyncio.new_event_loop()
    finally:
        socket.socket.connect = guarded


def client(root, host="127.0.0.1"):
    return TestClient(create_app(project_root=root), client=(host, 50000),
                      backend_options={"loop_factory": _new_loop})


@pytest.fixture(autouse=True)
def writable(monkeypatch):
    monkeypatch.delenv("VERISCOPE_READ_ONLY", raising=False)


def test_validate_normalizes_and_defaults_display_name():
    result = model_registry.validate([entry(display_name=None, name="  qwen2.5-7b ")], BUILTIN)
    assert result == [{"name": "qwen2.5-7b", "display_name": "qwen2.5-7b",
                       "base_url": "http://127.0.0.1:11434/v1",
                       "api_key_env": "VERISCOPE_OLLAMA_API_KEY"}]


@pytest.mark.parametrize("bad", [
    entry(api_key=SECRET),                              # 密钥本身不能登记
    entry(api_key_env="OPENAI_API_KEY"),                # 不能借用内置模型的密钥
    entry(api_key_env="VERISCOPE_ollama_API_KEY"),
    entry(base_url="ftp://127.0.0.1/v1"),
    entry(base_url="http://user:pass@127.0.0.1/v1"),
    entry(base_url="http://127.0.0.1/v1?key=x"),
    entry(name="deepseek-chat"),                        # 与内置模型重名
    entry(name="qwen2.5:7b"),                           # 冒号会写出 Windows 备用数据流
    entry(name="..\\escape"),
    entry(name=""),
    entry(display_name="a\nb"),
])
def test_validate_rejects_unsafe_entries(bad):
    with pytest.raises(model_registry.ModelConfigError):
        model_registry.validate([bad], BUILTIN)


def test_validate_rejects_duplicates_and_too_many():
    with pytest.raises(model_registry.ModelConfigError):
        model_registry.validate([entry(), entry()], BUILTIN)
    many = [entry(name=f"m{i}") for i in range(model_registry.MAX_MODELS + 1)]
    with pytest.raises(model_registry.ModelConfigError):
        model_registry.validate(many, BUILTIN)


def test_save_then_load_round_trip_and_bad_file_is_reported(tmp_path):
    assert model_registry.load(tmp_path, BUILTIN) == []
    model_registry.save(tmp_path, [entry()], BUILTIN)
    data = json.loads((tmp_path / model_registry.FILE_NAME).read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1 and data["models"][0]["name"] == "qwen2.5-7b"
    assert [m["source"] for m in model_registry.load(tmp_path, BUILTIN)] == ["local"]
    assert not list(tmp_path.glob(".models.*.tmp"))
    (tmp_path / model_registry.FILE_NAME).write_text("{", encoding="utf-8")
    with pytest.raises(model_registry.ModelConfigError):
        model_registry.load(tmp_path, BUILTIN)


def test_get_models_reports_key_status_without_key_value(tmp_path, monkeypatch):
    model_registry.save(tmp_path, [entry()], BUILTIN)
    monkeypatch.setenv("VERISCOPE_OLLAMA_API_KEY", SECRET)
    response = client(tmp_path).get("/models")
    assert response.status_code == 200
    assert SECRET not in response.text
    models = response.json()["models"]
    assert [m["name"] for m in models] == BUILTIN + ["qwen2.5-7b"]
    assert [m["source"] for m in models] == ["builtin"] * len(BUILTIN) + ["local"]
    assert models[-1]["keyConfigured"] is True
    assert set(models[-1]) == {"name", "displayName", "baseUrl", "apiKeyEnv", "keyConfigured", "source"}


def test_put_models_saves_from_this_machine_only(tmp_path):
    response = client(tmp_path).put("/models/local", json={"models": [entry()]})
    assert response.status_code == 200
    assert response.json()["models"][-1]["keyConfigured"] is False
    assert (tmp_path / model_registry.FILE_NAME).exists()

    remote = client(tmp_path, host="203.0.113.7").put("/models/local", json={"models": []})
    assert remote.status_code == 403
    assert len(model_registry.load(tmp_path, BUILTIN)) == 1


def test_put_models_rejects_key_fields_and_leaves_file_unchanged(tmp_path):
    model_registry.save(tmp_path, [entry()], BUILTIN)
    before = (tmp_path / model_registry.FILE_NAME).read_bytes()
    with client(tmp_path) as http:
        rejected = http.put("/models/local", json={"models": [entry(api_key=SECRET)]})
        assert rejected.status_code == 422
        assert SECRET not in rejected.text
        assert http.put("/models/local", json=[entry()]).status_code == 422
    assert (tmp_path / model_registry.FILE_NAME).read_bytes() == before


def test_read_only_deployment_blocks_model_config(tmp_path, monkeypatch):
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")
    with client(tmp_path) as http:
        assert http.get("/models").status_code == 423
        assert http.put("/models/local", json={"models": [entry()]}).status_code == 423
    assert not (tmp_path / model_registry.FILE_NAME).exists()


def test_pipeline_adds_local_models_only_when_named(tmp_path, monkeypatch):
    model_registry.save(tmp_path, [entry()], BUILTIN)
    monkeypatch.setattr(pipeline, "BASE_DIR", str(tmp_path))
    assert pipeline.select_model_configs(None) == list(MODELS_CONFIG)
    chosen = pipeline.select_model_configs(["qwen2.5-7b", BUILTIN[0]])
    assert [c["name"] for c in chosen] == [BUILTIN[0], "qwen2.5-7b"]
    assert chosen[1]["api_key_env"] == "VERISCOPE_OLLAMA_API_KEY"


def test_service_accepts_registered_model_names(tmp_path):
    model_registry.save(tmp_path, [entry()], BUILTIN)
    assert service._configured_model_names(tmp_path) == set(BUILTIN) | {"qwen2.5-7b"}
