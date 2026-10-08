"""Offline checks for archive versioning and integrity; never execute a model."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from tools.competition import run_evidence as runner


SOURCE = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_source(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def frozen_project(tmp_path):
    project = tmp_path / "project"
    write_source(
        project / "backend/adapters/ragas_compat.py",
        'SENTENCE_ADAPTER_VERSION = "zh-sentence-v1"\n'
        'raise AssertionError("source metadata must not import this file")\n',
    )
    write_source(
        project / "backend/services/answering.py",
        'def answering(context, question):\n'
        '    prompt = f"根据资料回答：{context}\\n问题：{question}"\n'
        '    raise AssertionError("must not execute generation")\n',
    )
    write_source(
        project / "backend/services/trace.py",
        'TRACE_PROMPT_TEMPLATE = "仅依据上下文核对陈述\\n{context}"\n'
        'raise AssertionError("must not execute Trace")\n',
    )
    return project


def test_source_versions_reads_adapter_and_prompt_hashes_without_imports(frozen_project):
    versions = runner.source_versions(frozen_project)
    assert versions["sentenceAdapterVersion"] == "zh-sentence-v1"
    trace = versions["traceVersion"]
    assert trace == versions["promptVersions"]["trace"]
    assert trace["hashKind"] == "literal-template-utf8"
    assert trace["sha256"] == hashlib.sha256(
        "仅依据上下文核对陈述\n{context}".encode("utf-8")
    ).hexdigest()
    generator = versions["promptVersions"]["generator"]
    assert generator["hashKind"] == "f-string-ast-including-placeholder-expressions"
    assert generator["path"] == "backend/services/answering.py"
    tree = ast.parse((frozen_project / generator["path"]).read_text(encoding="utf-8"))
    value = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "prompt" for target in node.targets)
    )
    expected = hashlib.sha256(
        ast.dump(value, include_attributes=False).encode("utf-8")
    ).hexdigest()
    assert generator["sha256"] == expected
    assert versions["promptVersions"]["ragas"]["status"] == "unknown"


def test_generator_hash_changes_when_placeholder_expression_changes(frozen_project):
    before = runner.source_versions(frozen_project)
    source = frozen_project / "backend/services/answering.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace("{context}", "{other_context}"),
        encoding="utf-8",
    )
    after = runner.source_versions(frozen_project)
    assert before["promptVersions"]["generator"]["sha256"] != after["promptVersions"]["generator"]["sha256"]
    assert before["traceVersion"] == after["traceVersion"]


def test_source_versions_missing_material_is_explicitly_unknown(tmp_path):
    values = runner.source_versions(tmp_path / "missing-project")
    assert values["sentenceAdapterVersion"]["status"] == "unknown"
    assert values["sentenceAdapterVersion"]["value"] is None
    for name in ("generator", "trace", "ragas"):
        assert values["promptVersions"][name]["status"] == "unknown"
        assert values["promptVersions"][name]["value"] is None


def test_entrypoint_relationship_checks_real_main_wrapper():
    values = runner.entrypoint_relationship(SOURCE)
    assert values["runPipelineDelegatesToBackendEvaluate"] is True
    assert values["mainSha256"] == runner.sha256(SOURCE / "main.py")
    assert "CLI" in values["impact"] and "Gradio" in values["impact"]


def test_entrypoint_relationship_does_not_infer_legacy_from_unchanged_file(tmp_path):
    source = write_source(
        tmp_path / "main.py",
        'def _legacy_run_pipeline():\n'
        '    return "old"\n\n'
        'def run_pipeline():\n'
        '    from backend.core.pipeline import evaluate\n'
        '    return evaluate({})\n',
    )
    values = runner.entrypoint_relationship(tmp_path)
    assert values["runPipelineDelegatesToBackendEvaluate"] is True
    assert values["mainSha256"] == runner.sha256(source)


def test_entrypoint_relationship_absence_is_not_claimed_as_isolation(tmp_path):
    write_source(tmp_path / "main.py", 'def run_pipeline():\n    return "unknown-entry"\n')
    values = runner.entrypoint_relationship(tmp_path)
    assert values["runPipelineDelegatesToBackendEvaluate"] is False
    assert "不能认定CLI隔离" in values["impact"]


def test_entrypoint_relationship_missing_file_is_unknown(tmp_path):
    values = runner.entrypoint_relationship(tmp_path)
    assert values["status"] == "unknown"
    assert values["value"] is None


def test_dependency_versions_normalizes_names_and_preserves_unknown(monkeypatch):
    distributions = [
        SimpleNamespace(metadata={"Name": "Ragas"}, version="0.1.21"),
        SimpleNamespace(metadata={"Name": "langchain_core"}, version="test-core"),
        SimpleNamespace(metadata={"Name": ""}, version="ignored"),
    ]
    monkeypatch.setattr(runner.importlib.metadata, "distributions", lambda: distributions)
    values = runner.dependency_versions()
    assert values["packages"] == {"Ragas": "0.1.21", "langchain_core": "test-core"}
    assert values["relevant"]["ragas"] == "0.1.21"
    assert values["relevant"]["langchain-core"] == "test-core"
    assert values["relevant"]["pysbd"]["status"] == "unknown"
    assert values["relevant"]["pysbd"]["value"] is None


@pytest.fixture
def preparation_root(tmp_path, frozen_project, monkeypatch):
    root = tmp_path / "source"
    (root / "scenarios").mkdir(parents=True)
    shutil.copytree(
        SOURCE / "scenarios/competition_25",
        root / "scenarios/competition_25",
    )
    # Keep this synthetic preparation fixture pending. The real competition
    # manifest is author-confirmed, while this test covers the pre-run schema.
    synthetic_manifest = root / "scenarios/competition_25/case-manifest.json"
    manifest_data = json.loads(synthetic_manifest.read_text(encoding="utf-8"))
    manifest_data["authorValidation"] = {
        "status": "pending",
        "reviewer": None,
        "reviewedAt": None,
        "note": None,
    }
    synthetic_manifest.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(
        SOURCE / "scenarios/deposit_insurance.txt",
        root / "scenarios/deposit_insurance.txt",
    )
    shutil.copytree(frozen_project / "backend", root / "backend")
    write_source(
        root / "main.py",
        'def run_pipeline():\n'
        '    from backend.core.pipeline import evaluate\n'
        '    return evaluate({})\n',
    )
    for name in ("requirements.txt", "requirements_new.txt"):
        write_source(root / name, "# synthetic test requirements\n")

    def git_output(command, **kwargs):
        return "test-commit\n" if command[-1] == "HEAD" else " M backend/core/pipeline.py\n"

    monkeypatch.setattr(runner.subprocess, "check_output", git_output)
    monkeypatch.setattr(
        runner,
        "dependency_versions",
        lambda: {
            "packages": {"ragas": "0.1.21"},
            "relevant": {
                "ragas": "0.1.21",
                "pysbd": runner.unknown("synthetic test has no distribution"),
            },
        },
    )
    return root


def test_prepare_schema_two_freezes_runner_packages_and_integrity(preparation_root):
    old_report = write_source(preparation_root / "ragas_report.html", "old report unchanged")
    archive = runner.prepare(preparation_root, "pilot", "deepseek-chat")
    manifest = read_json(archive / "manifest.json")
    assert manifest["schemaVersion"] == 2
    assert manifest["status"] == "prepared"
    assert manifest["authorValidation"]["status"] == "pending"
    assert manifest["versions"]["sentenceAdapterVersion"] == "zh-sentence-v1"
    assert manifest["versions"]["dependencies"]["ragas"] == "0.1.21"
    assert manifest["entrypoints"]["runPipelineDelegatesToBackendEvaluate"] is True
    assert manifest["workingTreeDirty"] is True
    assert manifest["gitStatus"] == [" M backend/core/pipeline.py"]
    assert manifest["runnerPath"] == "runner/run_evidence.py"
    assert manifest["runnerSha256"] == runner.sha256(archive / manifest["runnerPath"])
    assert runner.sha256(archive / manifest["runnerPath"]) == runner.sha256(Path(runner.__file__))
    assert read_json(archive / "packages.json") == {"ragas": "0.1.21"}
    assert (archive / "project/main.py").read_bytes() == (preparation_root / "main.py").read_bytes()
    assert old_report.read_text(encoding="utf-8") == "old report unchanged"
    assert not (archive / "project/ragas_report.html").exists()
    assert not (archive / "project/.env").exists()
    request = read_json(archive / "request.json")
    assert request["testset_path"].endswith("pilot.json")
    assert request["faith_threshold"] == request["ctx_prec_threshold"] == 0.8
    index = read_json(archive / "checksums.json")
    paths = {record["path"] for record in index["files"]}
    assert {"manifest.json", "request.json", "packages.json", "runner/run_evidence.py", "project/main.py"} <= paths
    assert "checksums.json" not in paths
    assert manifest["integrity"]["index"] == "checksums.json"
    runner.verify_checksums(archive)


@pytest.fixture
def simple_archive(tmp_path):
    archive = tmp_path / "archive"
    write_source(archive / "manifest.json", "{}\n")
    write_source(archive / "api-response.json", '{"answer":"original"}\n')
    write_source(archive / "nested/data.txt", "原始材料")
    runner.write_checksums(archive)
    return archive


@pytest.mark.parametrize("change", ["tamper", "remove", "new"])
def test_verify_checksums_rejects_changed_bytes_deleted_or_added_files(simple_archive, change):
    if change == "tamper":
        (simple_archive / "api-response.json").write_text('{"answer":"changed"}\n', encoding="utf-8")
    elif change == "remove":
        (simple_archive / "api-response.json").unlink()
    else:
        write_source(simple_archive / "unexpected-result.json", "{}\n")
    with pytest.raises(ValueError, match="哈希或文件清单"):
        runner.verify_checksums(simple_archive)


def test_archive_files_excludes_vector_cache_bytecode_and_git(tmp_path):
    archive = tmp_path / "archive"
    included = [
        "manifest.json", "project/backend/config.py", "usage.json",
        "prompt-definitions.json", "nested/ordinary-data.json",
    ]
    excluded = [
        "project/chroma_db/index.sqlite3", "project/backend/__pycache__/config.pyc",
        ".git/HEAD", "nested/chroma_db/cache.bin", "checksums.json",
    ]
    for name in included + excluded:
        write_source(archive / name, "synthetic contents")
    assert [file.relative_to(archive).as_posix() for file in runner.archive_files(archive)] == sorted(included)
    runner.write_checksums(archive)
    runner.verify_checksums(archive)
    write_source(archive / "project/chroma_db/new-index.bin", "ephemeral cache")
    runner.verify_checksums(archive)


def test_checksum_index_records_utf8_bytes_and_sha256(simple_archive):
    index = read_json(simple_archive / "checksums.json")
    assert index["algorithm"] == "SHA-256"
    assert {"chroma_db", "__pycache__", ".git", "checksums.json"} <= set(index["excludes"])
    record = next(item for item in index["files"] if item["path"] == "nested/data.txt")
    assert record["bytes"] == len("原始材料".encode("utf-8"))
    assert record["sha256"] == runner.sha256(simple_archive / "nested/data.txt")


@pytest.mark.parametrize(
    "schema,status",
    [(1, "prepared"), (2, "completed"), (2, "failed"), (2, "running"), (None, "prepared"), (2, None)],
)
def test_execute_rejects_old_or_started_archive_before_socket_or_dotenv(tmp_path, monkeypatch, schema, status):
    archive = tmp_path / "archive"
    manifest = write_source(
        archive / "manifest.json",
        json.dumps({"schemaVersion": schema, "status": status}, ensure_ascii=False),
    )
    original = manifest.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid archive reached model preparation, socket or environment loading")

    monkeypatch.setattr(runner, "verify_checksums", forbidden)
    monkeypatch.setattr(runner, "validate_cases", forbidden)
    monkeypatch.setattr(runner.socket, "socket", forbidden)
    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="只能执行新版本prepared归档"):
        runner.execute(tmp_path / "source", archive, 8002, 30)
    assert manifest.read_bytes() == original
    assert sorted(file.name for file in archive.iterdir()) == ["manifest.json"]


