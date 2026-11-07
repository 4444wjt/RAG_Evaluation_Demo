"""PDF refusal runs before importing an unavailable full evaluation engine."""

import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from test_parse_check import _minimal_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("pages,code", [
    ([None], "SCANNED_DOCUMENT"),
    ([None, ""], "SCANNED_DOCUMENT"),
    (["\ufffd" * 30], "GARBLED_TEXT_LAYER"),
    (["正文", None], None),
    (["正文"], None),
])
def test_default_pdf_evaluator_rejects_unusable_pdf_before_full_engine_import(tmp_path, pages, code):
    uploads = tmp_path / "work" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / ("0" * 32 + ".pdf")).write_bytes(_minimal_pdf(pages))
    script = textwrap.dedent("""
        import asyncio, importlib.abc, json, os, socket, sys
        from pathlib import Path
        from types import ModuleType
        sys.path.insert(0, sys.argv[1])
        os.environ.pop('VERISCOPE_BYOK', None)
        os.environ.pop('VERISCOPE_READ_ONLY', None)
        dotenv = ModuleType('dotenv')
        dotenv.load_dotenv = None
        sys.modules['dotenv'] = dotenv
        loop = asyncio.new_event_loop()
        injected_loop = asyncio.new_event_loop()
        attempts = []
        class BlockEngine(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'backend.core.pipeline' or fullname.startswith(('datasets', 'ragas', 'langchain', 'torch', 'transformers')):
                    attempts.append(fullname)
                    raise AssertionError('full engine unavailable in this runtime')
                return None
        sys.meta_path.insert(0, BlockEngine())
        def no_network(*args, **kwargs):
            raise AssertionError('offline PDF preflight attempted networking')
        socket.create_connection = no_network
        socket.getaddrinfo = no_network
        socket.socket.connect = no_network
        socket.socket.connect_ex = no_network
        socket.socket.sendto = no_network
        from fastapi.testclient import TestClient
        from backend.api.app import create_app
        expected_code = json.loads(sys.argv[3])
        document = 'work/uploads/' + '0' * 32 + '.pdf'
        try:
            with TestClient(create_app(project_root=Path(sys.argv[2])), backend_options={'loop_factory': lambda: loop}) as client:
                response = client.post('/evaluate', json={'doc_path': document})
                if expected_code is not None:
                    assert response.status_code == 422, response.text
                    assert response.json()['code'] == expected_code, response.text
                    assert attempts == [], attempts
                else:
                    assert response.status_code == 500, response.text
                    assert attempts == ['backend.core.pipeline'], attempts
                # Injected evaluators keep their existing path and never run the preflight.
                result = {'summary': [], 'summary_df': [], 'gate_passed': False, 'gate_message': 'FAIL',
                          'chart_path': None, 'html_path': None, 'model_dfs': {}, 'total_elapsed_seconds': None}
            with TestClient(create_app(lambda *_args, **_kwargs: result, project_root=Path(sys.argv[2])),
                            backend_options={'loop_factory': lambda: injected_loop}) as injected:
                assert injected.post('/evaluate', json={'doc_path': document}).status_code == 200
        finally:
            if not loop.is_closed():
                loop.close()
            if not injected_loop.is_closed():
                injected_loop.close()
    """)
    completed = subprocess.run([sys.executable, "-c", script, str(PROJECT_ROOT), str(tmp_path), json.dumps(code)],
                               capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_services_barrel_keeps_light_exports_and_submodule_imports_without_engine(tmp_path):
    script = textwrap.dedent("""
        import importlib.abc, sys
        from types import ModuleType
        sys.path.insert(0, sys.argv[1])
        dotenv = ModuleType('dotenv')
        dotenv.load_dotenv = None
        sys.modules['dotenv'] = dotenv
        class BlockEngine(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith(('datasets', 'ragas', 'langchain', 'torch', 'transformers')):
                    raise AssertionError('light service import loaded engine: ' + fullname)
                return None
        sys.meta_path.insert(0, BlockEngine())
        from backend.services import trace_unavailable, parse_check
        assert trace_unavailable()['statements'] == []
        assert trace_unavailable()['trace_status'] == 'disabled'
        assert callable(parse_check.check_document)
        import backend.services as services
        assert services.trace_unavailable is trace_unavailable
    """)
    completed = subprocess.run([sys.executable, "-c", script, str(PROJECT_ROOT)], cwd=tmp_path,
                               capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
