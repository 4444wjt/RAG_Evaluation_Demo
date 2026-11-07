"""Document input errors become explicit client statuses, never server faults."""

import textwrap

from backend.input_errors import DocumentInputError
from test_api import _run_evaluation_http


def test_document_input_error_is_422_with_code_and_releases_lock(tmp_path):
    _run_evaluation_http(tmp_path, textwrap.dedent(r"""
        from backend.input_errors import DocumentInputError
        message = "文档没有可提取的文字层，疑似扫描件（第 1 页）。请先用识别插件转成文字并复核，再导入评测。"
        fake = Mock(side_effect=[DocumentInputError("SCANNED_DOCUMENT", message), sample_result()])
        with client_for(fake) as client:
            failed = client.post("/evaluate", json={})
            assert failed.status_code == 422
            assert failed.json() == {"detail": message, "code": "SCANNED_DOCUMENT"}
            # The lock is released: the next evaluation runs normally.
            assert client.post("/evaluate", json={}).status_code == 200
        assert fake.call_count == 2
    """))


def test_other_runtime_errors_keep_generic_500_without_code(tmp_path):
    _run_evaluation_http(tmp_path, textwrap.dedent(r"""
        fake = Mock(side_effect=RuntimeError("provider secret detail"))
        with client_for(fake) as client:
            failed = client.post("/evaluate", json={})
            assert failed.status_code == 500
            assert failed.json() == {"detail": service._RUNTIME_ERROR}
            assert "secret" not in failed.text
    """))


def test_document_input_error_is_still_a_runtime_error():
    error = DocumentInputError("EMPTY_DOCUMENT", "文档切分后为空，请检查文档内容长度")
    assert isinstance(error, RuntimeError)
    assert str(error) == error.message
