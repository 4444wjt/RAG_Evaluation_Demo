"""Single-origin delivery checks against the real static build and transaction API."""

import asyncio
import importlib
import importlib.util
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import textwrap

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "tools" / "finance" / "local_app.py"
LAUNCHER = ROOT / "tools" / "finance" / "start-local.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
_LOOP = asyncio.new_event_loop()


def local_module():
    assert ENTRY.is_file(), "The single-origin local app factory is missing."
    return importlib.import_module("tools.finance.local_app")


def request(app, method, path, **kwargs):
    async def invoke():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)

    return _LOOP.run_until_complete(invoke())


def teardown_module():
    _LOOP.close()


def source_request():
    return {
        "schemaVersion": 1,
        "currency": "CNY",
        "transactions": [
            {"transaction_id": "t1", "timestamp": "2026-11-01T10:00:00Z", "customer_id": "甲", "terminal_id": "m1", "amount": "8000.00"},
            {"transaction_id": "t2", "timestamp": "2026-11-01T10:05:00Z", "customer_id": "甲", "terminal_id": "m1", "amount": "3000.00"},
            {"transaction_id": "t3", "timestamp": "2026-11-01T10:06:00Z", "customer_id": "乙", "terminal_id": "m2", "amount": "12000.00"},
        ],
        "rules": {"highAmount": "10000.00", "windowMinutes": 10, "minTransactions": 2},
        "claims": [
            {"transaction_id": "t2", "field": "window_total", "value": "11000.00", "evidence_ids": ["t1", "t2"]},
            {"transaction_id": "t3", "field": "fraud_confirmed", "value": True, "evidence_ids": ["t3"]},
        ],
    }


def test_built_desktop_and_referenced_asset_are_served_from_single_origin():
    app = local_module().create_app()
    response = request(app, "GET", "/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.content == (ROOT / "frontend" / "dist" / "index.html").read_bytes()
    asset_path = re.search(r'<script[^>]+src="(/assets/[^\"]+)"', response.text).group(1)
    asset = request(app, "GET", asset_path)
    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]
    assert asset.content == (ROOT / "frontend" / "dist" / asset_path.lstrip("/")).read_bytes()


def test_hash_deep_link_refresh_returns_the_same_built_html():
    app = local_module().create_app()
    # URL fragments stay in the browser: a refresh requests the root document.
    response = request(app, "GET", "/#/lab/transaction-review")
    assert response.status_code == 200
    assert response.content == (ROOT / "frontend" / "dist" / "index.html").read_bytes()


def test_api_mount_precedes_a_colliding_static_file_and_reserves_the_namespace(tmp_path, monkeypatch):
    module = local_module()
    dist = tmp_path / "frontend" / "dist"
    (dist / "api").mkdir(parents=True)
    (dist / "index.html").write_text("<html>fixture build</html>", encoding="utf-8")
    (dist / "api" / "health").write_text("static collision", encoding="utf-8")
    (dist / "api" / "unknown").write_text("should not be public", encoding="utf-8")
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    app = module.create_app()
    response = request(app, "GET", "/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    missing = request(app, "GET", "/api/unknown")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("path", [
    "/.env", "/.env.local", "/backend/core/transaction_review.py",
    "/tools/finance/local_app.py", "/%2e%2e/backend/core/transaction_review.py",
    "/%2e%2e/.env", "/health", "/lab/transaction-review",
])
def test_static_mount_does_not_expose_repository_files_or_invent_history_routes(path):
    response = request(local_module().create_app(), "GET", path)
    assert response.status_code == 404


def test_mounted_api_analyzes_and_replays_real_transaction_bundle():
    app = local_module().create_app()
    source = source_request()
    analyzed = request(app, "POST", "/api/transaction-review/analyze", json=source)
    assert analyzed.status_code == 200, analyzed.text
    report = analyzed.json()
    assert report["status"] == "review"
    assert report["summary"] == {
        "totalTransactions": 3, "flaggedTransactions": 2,
        "consistentClaims": 1, "inconsistentClaims": 0, "reviewClaims": 1,
    }
    assert [case["id"] for case in report["cases"]] == ["t2", "t3"]
    assert report["cases"][0]["window"]["total"] == "11000.00"
    from backend.core.transaction_review import analyze_request
    assert report == analyze_request(source)
    bundle = {
        "schema": "TransactionReviewBundleV1", "request": source, "report": report,
        "decisions": [{"transaction_id": "t2", "decision": "need_more", "reason": "模拟复核：补充客户说明。"}],
    }
    replayed = request(app, "POST", "/api/transaction-review/replay", json=bundle)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == {**bundle, "replayed": True}
    bundle["report"]["cases"][0]["window"]["total"] = "1.00"
    rejected = request(app, "POST", "/api/transaction-review/replay", json=bundle)
    assert rejected.status_code == 422
    assert set(rejected.json()) == {"detail"}


@pytest.mark.parametrize("operation", ["analyze", "replay"])
@pytest.mark.parametrize("body", [b"{", b"{}"])
def test_malformed_or_incomplete_requests_keep_api_422(operation, body):
    response = request(local_module().create_app(), "POST", "/api/transaction-review/" + operation,
                       content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"]


@pytest.mark.parametrize("operation", ["analyze", "replay"])
def test_read_only_423_survives_mount_and_precedes_body_or_media_type(monkeypatch, operation):
    from starlette.requests import Request
    monkeypatch.setenv("VERISCOPE_READ_ONLY", "1")

    async def unreadable(_self):
        raise AssertionError("Mounted read-only API consumed a request body.")
        yield b""

    monkeypatch.setattr(Request, "stream", unreadable)
    response = request(local_module().create_app(), "POST", "/api/transaction-review/" + operation,
                       content=b"invalid", headers={"Content-Type": "text/plain"})
    assert response.status_code == 423


@pytest.mark.parametrize("directory_exists", [False, True])
def test_import_is_lazy_but_factory_clearly_rejects_missing_built_index(tmp_path, directory_exists):
    local_module()
    copied = tmp_path / "tools" / "finance" / "local_app.py"
    copied.parent.mkdir(parents=True)
    shutil.copyfile(ENTRY, copied)
    if directory_exists:
        (tmp_path / "frontend" / "dist").mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("isolated_finance_local_app", copied)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(RuntimeError, match=r"frontend/dist/index\.html"):
        module.create_app()


def test_factory_and_health_do_not_load_models_config_keys_or_network(tmp_path):
    local_module()
    script = textwrap.dedent("""
        import asyncio, builtins, importlib.abc, io, os, socket, sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        loop = asyncio.new_event_loop()
        blocked = ('main', 'backend.config', 'backend.core', 'backend.adapters',
                   'backend.services', 'backend.storage', 'backend.reporting', 'dotenv',
                   'langchain', 'langchain_core', 'langchain_openai', 'ragas', 'chromadb',
                   'torch', 'transformers', 'sentence_transformers', 'datasets')
        class NoEngine(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
                    raise AssertionError('Local delivery loaded engine/config: ' + fullname)
                return None
        sys.meta_path.insert(0, NoEngine())
        def no_network(*args, **kwargs):
            raise AssertionError('Local factory/health attempted networking')
        socket.create_connection = socket.getaddrinfo = no_network
        socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = no_network
        def guarded(original):
            def open_file(file, *args, **kwargs):
                if isinstance(file, (str, bytes, os.PathLike)):
                    name = Path(os.fsdecode(file)).name.lower()
                    if name == '.env' or name.startswith('.env.'):
                        raise AssertionError('Local factory/health read dotenv')
                return original(file, *args, **kwargs)
            return open_file
        builtins.open, io.open = guarded(builtins.open), guarded(io.open)
        from tools.finance.local_app import create_app
        from fastapi.testclient import TestClient
        try:
            with TestClient(create_app(), backend_options={'loop_factory': lambda: loop}) as client:
                assert client.get('/api/health').json() == {'status': 'ok'}
        finally:
            if not loop.is_closed(): loop.close()
        assert not any(module == name or module.startswith(name + '.')
                       for module in sys.modules for name in blocked)
        print('factory-health: no model/config/dotenv/network access')
    """)
    result = subprocess.run([sys.executable, "-c", script, str(ROOT)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "factory-health:" in result.stdout


def run_launcher(script, python, tmp_path, port=8096):
    assert LAUNCHER.is_file(), "The foreground loopback launcher is missing."
    assert POWERSHELL, "This Windows launcher test requires an existing PowerShell."
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-PythonExecutable", str(python), "-Port", str(port)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15, check=False,
    )


def test_launcher_rejects_nonexistent_explicit_interpreter(tmp_path):
    result = run_launcher(LAUNCHER, tmp_path / "missing-python.exe", tmp_path)
    assert result.returncode != 0
    assert "PythonExecutable" in result.stdout + result.stderr


def test_launcher_resolves_its_own_root_and_rejects_missing_build(tmp_path):
    assert LAUNCHER.is_file(), "The foreground loopback launcher is missing."
    copied = tmp_path / "package" / "tools" / "finance" / "start-local.ps1"
    copied.parent.mkdir(parents=True)
    shutil.copyfile(LAUNCHER, copied)
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    result = run_launcher(copied, sys.executable, unrelated_cwd)
    assert result.returncode != 0
    assert "frontend/dist/index.html" in result.stdout + result.stderr


def test_launcher_refuses_occupied_loopback_port_without_stopping_listener(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        result = run_launcher(LAUNCHER, sys.executable, tmp_path, port)
        assert result.returncode != 0
        assert str(port) in result.stdout + result.stderr
        assert "occupied" in (result.stdout + result.stderr).lower()
        assert listener.getsockname() == ("127.0.0.1", port)


def test_launcher_rejects_invalid_port_before_starting_anything(tmp_path):
    result = run_launcher(LAUNCHER, sys.executable, tmp_path, 0)
    assert result.returncode != 0
    assert "Port" in result.stdout + result.stderr
