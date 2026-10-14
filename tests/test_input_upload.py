"""Offline raw input import contracts; no model requests or real input changes."""

import asyncio
from pathlib import Path

import pytest
from starlette.requests import Request

from backend.api import input_files
from backend.api.service import EvaluationAPIError, _checked_path
from test_api import _run_evaluation_http


def _http(tmp_path, case):
    # Reuse the existing API tests' isolated interpreter. Windows asyncio's
    # internal wakeup sockets exist before the external-network guard applies.
    setup = """
        import re
        from backend.api import input_files
        from backend.api.service import _checked_path
        from starlette.requests import Request
        os.environ.pop("VERISCOPE_READ_ONLY", None)

        def upload(client, filename="资料.txt", content=b"content", kind="document"):
            return client.post(
                "/inputs", params={"kind": kind, "filename": filename},
                headers={"content-type": "application/octet-stream"}, content=content,
            )

        def imported_files():
            return list((root / "work" / "uploads").glob("*"))
    """
    import textwrap
    _run_evaluation_http(tmp_path, textwrap.dedent(setup) + textwrap.dedent(case))


def test_import_preserves_raw_bytes_chinese_names_and_independent_files(tmp_path):
    _http(tmp_path, r"""
        raw = "中文资料\r\n".encode("utf-8") + b"\x00\xff\n"
        with client_for() as client:
            response = upload(client, "中文资料.MD", raw)
            assert response.status_code == 200
            saved = response.json()
            assert saved == {
                "kind": "document", "file_name": "中文资料.MD",
                "relative_path": saved["relative_path"], "size_bytes": len(raw),
            }
            assert re.fullmatch(r"work/uploads/[0-9a-f]{32}\.md", saved["relative_path"])
            assert (root / saved["relative_path"]).read_bytes() == raw
            assert str(root) not in response.text
            second = upload(client, "中文资料.MD", b"different").json()
            assert second["relative_path"] != saved["relative_path"]
            assert (root / saved["relative_path"]).read_bytes() == raw
            assert (root / second["relative_path"]).read_bytes() == b"different"
    """)


def test_supported_extensions_match_existing_evaluation_path_guard(tmp_path):
    _http(tmp_path, """
        with client_for() as client:
            for kind, filenames in (
                ("document", ["doc.txt", "doc.pdf", "doc.docx", "doc.md"]),
                ("testset", ["cases.json", "cases.csv", "cases.txt"]),
            ):
                for filename in filenames:
                    response = upload(client, filename, kind=kind)
                    assert response.status_code == 200, (kind, filename, response.text)
                    relative = response.json()["relative_path"]
                    assert _checked_path(relative, root, testset=kind == "testset") == str((root / relative).resolve())
    """)


def test_invalid_names_kinds_extensions_and_body_format_do_not_write(tmp_path):
    _http(tmp_path, r"""
        with client_for() as client:
            cases = [
                ("document", "../doc.txt"), ("document", r"folder\doc.txt"),
                ("document", "C:doc.txt"), ("document", "资料\x00.txt"),
                ("document", "资料\n.txt"), ("document", ""),
                ("document", "a" * 256 + ".txt"), ("document", "中" * 90 + ".txt"),
                ("document", " doc.txt"), ("document", "doc.exe"),
                ("document", "doc.json"), ("testset", "cases.pdf"),
                ("unsupported", "doc.txt"),
            ]
            for kind, filename in cases:
                response = upload(client, filename, kind=kind)
                assert response.status_code == 422, (kind, filename, response.text)
                assert not (root / "work").exists()
            response = client.post(
                "/inputs", params={"kind": "document", "filename": "doc.txt"},
                json={"content": "text"},
            )
            assert response.status_code == 415
            assert not (root / "work").exists()
    """)


def test_empty_file_and_declared_size_limits(tmp_path):
    _http(tmp_path, """
        assert input_files.MAX_INPUT_BYTES == 10 * 1024 * 1024
        with client_for() as client:
            assert upload(client, content=b"").status_code == 422
            assert imported_files() == []
            with patch.object(input_files, "MAX_INPUT_BYTES", 16):
                assert upload(client, content=b"a" * 16).status_code == 200
                assert upload(client, content=b"a" * 17).status_code == 413
                assert len(imported_files()) == 1
    """)


def test_read_only_does_not_read_request_stream_or_create_directory(tmp_path):
    _http(tmp_path, """
        async def unexpected_stream(request):
            raise AssertionError("Read-only import must not read its body")
            yield b""

        with client_for() as client:
            with patch.dict(os.environ, {"VERISCOPE_READ_ONLY": "1"}), patch.object(Request, "stream", unexpected_stream):
                response = upload(client, content=b"private bytes")
                assert response.status_code == 423
                assert not (root / "work").exists()
    """)


def test_imported_inputs_reach_evaluate_without_contract_change(tmp_path):
    _http(tmp_path, """
        evaluator = Mock(return_value=sample_result())
        with client_for(evaluator) as client:
            document = upload(client, content="本机文档".encode()).json()
            testset = upload(client, "题集.json", b"[]", kind="testset").json()
            response = client.post("/evaluate", json={
                "doc_path": document["relative_path"],
                "testset_path": testset["relative_path"],
            })
            assert response.status_code == 200, response.text
            assert response.json()["gate_passed"] is False
            evaluator.assert_called_once()
            parameters = evaluator.call_args.args[0]
            assert parameters["doc_path"] == str((root / document["relative_path"]).resolve())
            assert parameters["testset_path"] == str((root / testset["relative_path"]).resolve())
    """)


def _files(root):
    return list((root / "work" / "uploads").glob("*"))


def _request(events, headers=()):
    iterator = iter(events)

    async def receive():
        event = next(iterator)
        if isinstance(event, BaseException):
            raise event
        return event

    return Request({
        "type": "http", "method": "POST", "path": "/inputs",
        "headers": [(b"content-type", b"application/octet-stream"), *headers],
    }, receive)


def _save(request, root):
    # Fake receive returns immediately; avoid creating Windows socketpair
    # inside the offline guard. Real ASGI awaits are covered by HTTP tests.
    coroutine = input_files.import_input_file(
        request, kind="document", filename="doc.txt", project_root=root,
    )
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("Unexpected blocking receive in unit test")


def test_stream_limit_cannot_be_bypassed_without_content_length(tmp_path, monkeypatch):
    monkeypatch.setattr(input_files, "MAX_INPUT_BYTES", 16)
    request = _request([
        {"type": "http.request", "body": b"a" * 10, "more_body": True},
        {"type": "http.request", "body": b"b" * 10, "more_body": False},
    ])
    with pytest.raises(EvaluationAPIError) as captured:
        _save(request, tmp_path)
    assert captured.value.status_code == 413
    assert _files(tmp_path) == []


@pytest.mark.parametrize("failure", [
    {"type": "http.disconnect"}, RuntimeError("stream failed"),
    OSError("I/O failed"), asyncio.CancelledError(),
])
def test_disconnect_or_exception_removes_partial_file(tmp_path, failure):
    request = _request([
        {"type": "http.request", "body": b"partial", "more_body": True}, failure,
    ])
    with pytest.raises((EvaluationAPIError, asyncio.CancelledError)):
        _save(request, tmp_path)
    assert _files(tmp_path) == []


def test_incorrect_content_length_removes_partial_file(tmp_path):
    request = _request(
        [{"type": "http.request", "body": b"short", "more_body": False}],
        [(b"content-length", b"10")],
    )
    with pytest.raises(EvaluationAPIError) as captured:
        _save(request, tmp_path)
    assert captured.value.status_code == 422
    assert _files(tmp_path) == []


@pytest.mark.parametrize("length,status", [(b"-1", 422), (b"unknown", 422), (b"99999999", 413)])
def test_invalid_or_excessive_declared_length_does_not_create_file(tmp_path, length, status):
    with pytest.raises(EvaluationAPIError) as captured:
        _save(_request([], [(b"content-length", length)]), tmp_path)
    assert captured.value.status_code == status
    assert not (tmp_path / "work").exists()


def test_disk_write_failure_removes_created_file(tmp_path, monkeypatch):
    original_open = Path.open

    class BrokenWriter:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def write(self, _chunk):
            raise OSError("disk write unavailable")

    def open_file(path, mode="r", *args, **kwargs):
        opened = original_open(path, mode, *args, **kwargs)
        return BrokenWriter(opened) if mode == "xb" else opened

    monkeypatch.setattr(Path, "open", open_file)
    request = _request([{"type": "http.request", "body": b"data", "more_body": False}])
    with pytest.raises(EvaluationAPIError) as captured:
        _save(request, tmp_path)
    assert captured.value.status_code == 500
    assert _files(tmp_path) == []


def test_symlink_escape_rejected_without_writing(tmp_path, monkeypatch):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    original_resolve = Path.resolve

    def resolved(path, *args, **kwargs):
        if path == tmp_path / "work":
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolved)
    with pytest.raises(EvaluationAPIError) as captured:
        _save(_request([]), tmp_path)
    assert captured.value.status_code == 403
    assert list(outside.iterdir()) == []


def test_uuid_collision_does_not_delete_existing_file(tmp_path, monkeypatch):
    identifier = "a" * 32
    monkeypatch.setattr(input_files, "uuid4", lambda: type("Identifier", (), {"hex": identifier})())
    request = lambda: _request([{"type": "http.request", "body": b"first", "more_body": False}])
    first = _save(request(), tmp_path)
    with pytest.raises(EvaluationAPIError):
        _save(request(), tmp_path)
    assert (tmp_path / first["relative_path"]).read_bytes() == b"first"


@pytest.mark.parametrize("path", [
    "work/uploads/ordinary.txt", "work/uploads/" + "a" * 32 + ".exe",
    "work/uploads/nested/" + "a" * 32 + ".txt", "work/private.txt",
])
def test_new_path_allowlist_does_not_open_other_work_files(tmp_path, path):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"private")
    with pytest.raises(EvaluationAPIError):
        _checked_path(path, tmp_path)
