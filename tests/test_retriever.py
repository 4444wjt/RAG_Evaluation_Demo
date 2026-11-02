"""Offline regression for both vector-store builders and their shared-path client lifecycle."""
import ast
import os
from pathlib import Path
import shutil

import pytest


@pytest.fixture(params=["backend/services/retriever.py", "main.py"])
def vector_builder(request, tmp_path):
    # Chroma caches its system by persistent directory even after a filesystem deletion.
    # Keep that lifecycle in this substitute; assert document contents, not API call order.
    class CachedChroma:
        collections = {}
        clients = []
        failure = None

        class Client:
            closed = False

            def close(self):
                self.closed = True

        def __init__(self, *, persist_directory, embedding_function, client=None):
            self._client = client
            if self._client is None:
                self._client = self.Client()
                self.clients.append(self._client)
            self.path = persist_directory
            Path(self.path).mkdir(parents=True, exist_ok=True)
            self.documents = self.collections.setdefault(self.path, [])

        @classmethod
        def from_documents(cls, *, documents, embedding, persist_directory, client=None):
            store = cls(persist_directory=persist_directory, embedding_function=embedding, client=client)
            if cls.failure == "add":
                raise RuntimeError("add failed")
            store.documents.extend(documents)
            return store

        def delete_collection(self):
            if self.failure == "delete":
                raise RuntimeError("delete failed")
            del self.collections[self.path]

        def get(self):
            return {"documents": list(self.documents)}

    source_path = Path(__file__).resolve().parents[1] / request.param
    tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_vector_store"]
    assert len(definitions) == 1
    namespace = {
        "os": os, "shutil": shutil, "Chroma": CachedChroma,
        "REUSE_VECTOR_DB": False, "CHROMA_DIR": str(tmp_path / "chroma"),
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace["build_vector_store"], CachedChroma


@pytest.mark.parametrize("reuse", [False, True])
def test_sequential_vector_stores_respect_reuse(vector_builder, reuse):
    build, _ = vector_builder
    build.__globals__["REUSE_VECTOR_DB"] = reuse
    assert build(["old document"], object()).get()["documents"] == ["old document"]
    second = build(["new document"], object())
    assert second.get()["documents"] == (["old document"] if reuse else ["new document"])


@pytest.mark.parametrize("outcome", ["success", "delete", "add"])
def test_rebuild_client_ownership_and_failure_cleanup(vector_builder, outcome):
    build, chroma = vector_builder
    chroma.failure = None if outcome == "success" else outcome
    if outcome == "success":
        store = build(["new document"], object())
        assert not store._client.closed
        store._client.close()
    else:
        with pytest.raises(RuntimeError, match=f"{outcome} failed"):
            build(["new document"], object())
    assert len(chroma.clients) == 1
    assert all(client.closed for client in chroma.clients)
