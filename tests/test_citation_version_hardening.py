"""Resource and loaded-source boundaries; synthetic examples are not legal evidence."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend.core import citation_version as cv
from test_citation_version import XML, body, inspection, make_request
from test_citation_version_cli import run, write


def effect_source(depth=70, text_size=110000):
    fragment = b"<ukm:UnappliedEffect>" * depth + b"x" * text_size
    fragment += b"</ukm:UnappliedEffect>" * depth
    return XML.replace(b"</ukm:Metadata>", fragment + b"</ukm:Metadata>")


def repeated_body(raw, count=20):
    value = body(raw)
    value["citations"] = [dict(copy.deepcopy(value["citations"][0]), id=str(i)) for i in range(count)]
    return value


def forbidden_rule(*_args, **_kwargs):
    raise AssertionError("resource/source rejection must precede B1 evaluation")


@pytest.mark.parametrize("operation", ["inspect", "check", "evaluate"])
def test_nested_evidence_is_rejected_before_rule_or_observation_expansion(monkeypatch, operation):
    raw = effect_source()
    assert len(raw) < 120000
    monkeypatch.setattr(cv.rules, "evaluate", forbidden_rule)
    monkeypatch.setattr(cv, "_observe", forbidden_rule)
    if operation == "inspect":
        item = cv.inspect(inspection(raw))["files"][0]
        assert item["status"] == "INVALID_INPUT" and "预算" in item["error"]
    else:
        output = cv.evaluate(raw, make_request(raw)) if operation == "evaluate" else cv.check(body(raw))["rows"][0]["output"]
        assert output["axes"] == dict.fromkeys(cv.rules.AXES, "INVALID_INPUT")
        assert "预算" in output["input_errors"][0]


def test_long_expanded_paths_reject_before_b1_path_allocation(monkeypatch):
    fragment = ('<x:A xmlns:x="urn:' + "p" * 20000 + '">' + "<x:A>" * 69 + "</x:A>" * 70).encode()
    raw = XML.replace(b"</Legislation>", fragment + b"</Legislation>")
    monkeypatch.setattr(cv.rules, "evaluate", forbidden_rule)
    result = cv.check(body(raw))
    assert result["counts"]["invalid"] == 1
    assert "路径" in result["rows"][0]["output"]["input_errors"][0]


def test_nested_metadata_duplicate_evidence_budget(monkeypatch):
    fragment = b"<ukm:Metadata>" * 80 + b"<dc:modified>" + b"x" * 110000 + b"</dc:modified>" + b"</ukm:Metadata>" * 80
    raw = XML.replace(b"</Legislation>", fragment + b"</Legislation>")
    monkeypatch.setattr(cv.rules, "evaluate", forbidden_rule)
    result = cv.check(body(raw))
    assert result["counts"]["invalid"] == 1
    assert "预算" in result["rows"][0]["output"]["input_errors"][0]


def test_batch_budget_rejects_all_rows_before_first_b1_call(monkeypatch):
    raw = effect_source(depth=30, text_size=100000)
    assert cv.inspect(inspection(raw))["files"][0]["status"] == "READY"
    monkeypatch.setattr(cv.rules, "evaluate", forbidden_rule)
    with pytest.raises(cv.CitationVersionInputError, match="整批") as failure:
        cv.check(repeated_body(raw))
    assert failure.value.status_code == 413


def test_original_twenty_row_reproduction_yields_a_replayable_rejection_bundle(tmp_path):
    request = tmp_path / "input.json"
    report, bundle = tmp_path / "report.json", tmp_path / "bundle.json"
    write(request, repeated_body(effect_source()))
    checked = run("check", "--input", request, "--report", report, "--bundle", bundle)
    assert checked.returncode == 0, checked.stderr
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["counts"]["total"] == saved["counts"]["invalid"] == 20
    assert bundle.stat().st_size < 128 * 1024 * 1024
    replayed = run("verify", "--bundle", bundle)
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["verified"] is True


def drift(monkeypatch, source="wrapper", missing=False):
    target = Path(cv.__file__ if source == "wrapper" else cv.rules.__file__).resolve()
    original = Path.read_bytes
    def changed(path):
        if path.resolve() == target:
            if missing:
                raise OSError("private-path-marker")
            return original(path) + b"\n# disk changed after this process loaded\n"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", changed)


@pytest.mark.parametrize("source", ["wrapper", "rules"])
@pytest.mark.parametrize("operation", ["inspect", "check", "evaluate"])
def test_source_drift_is_409_before_stale_code_is_used(monkeypatch, source, operation):
    drift(monkeypatch, source)
    monkeypatch.setattr(cv.rules, "evaluate", forbidden_rule)
    with pytest.raises(cv.CitationVersionInputError, match="重启") as failure:
        if operation == "evaluate":
            cv.evaluate(XML, make_request())
        else:
            getattr(cv, operation)(inspection() if operation == "inspect" else body())
    assert failure.value.status_code == 409


def test_source_disappearing_is_409_without_disclosing_path(monkeypatch):
    drift(monkeypatch, missing=True)
    with pytest.raises(cv.CitationVersionInputError) as failure:
        cv.check(body())
    assert failure.value.status_code == 409 and "private-path-marker" not in failure.value.detail


def test_drift_during_rule_execution_never_returns_a_mislabelled_report(monkeypatch):
    original = cv.rules.evaluate
    def change_after_calculation(*args, **kwargs):
        result = original(*args, **kwargs)
        drift(monkeypatch)
        return result
    monkeypatch.setattr(cv.rules, "evaluate", change_after_calculation)
    with pytest.raises(cv.CitationVersionInputError) as failure:
        cv.check(body())
    assert failure.value.status_code == 409


def test_report_hashes_bind_loaded_files():
    result = cv.check(body())
    assert result["checker"]["source_sha256"] == {
        "rules": hashlib.sha256(Path(cv.rules.__file__).read_bytes()).hexdigest(),
        "wrapper": hashlib.sha256(Path(cv.__file__).read_bytes()).hexdigest(),
    }


def test_allowed_expanded_evidence_has_a_real_cli_replay(tmp_path):
    raw = effect_source(depth=30, text_size=100000)
    request, bundle = tmp_path / "allowed.json", tmp_path / "allowed-bundle.json"
    write(request, body(raw))
    checked = run("check", "--input", request, "--bundle", bundle)
    assert checked.returncode == 0, checked.stderr
    result = json.loads(bundle.read_text(encoding="utf-8"))
    assert result["report"]["counts"]["valid_input"] == 1
    assert result["report"]["rows"][0]["output"] == cv.rules.evaluate(raw, make_request(raw), "B1")
    assert run("verify", "--bundle", bundle).returncode == 0


@pytest.mark.parametrize("limit_kind", ["report", "bundle"])
def test_serialized_byte_boundary_is_shared_with_cli_writer(monkeypatch, tmp_path, limit_kind):
    from scripts import citation_version_check as cli
    request = body()
    request["citations"][0]["label"] = '汉字/quotes:"/tabs:\t'
    report = cv.check(request)
    value = {"schema": "CitationVersionBundleV1", "input": request, "report": report} if limit_kind == "bundle" else report
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    constant = "MAX_BUNDLE_BYTES" if limit_kind == "bundle" else "MAX_REPORT_BYTES"
    monkeypatch.setattr(cv, constant, len(encoded))
    path = tmp_path / "at-boundary.json"
    cli._write(str(path), value)
    assert path.read_bytes() == encoded
    if limit_kind == "bundle":
        assert cli.verify_bundle(cli._read(str(path), limit=len(encoded))) is True
    monkeypatch.setattr(cv, constant, len(encoded) - 1)
    rejected = tmp_path / "over-boundary.json"
    with pytest.raises(cv.CitationVersionInputError) as failure:
        cli._write(str(rejected), value)
    assert failure.value.status_code == 413 and not rejected.exists()


@pytest.mark.parametrize("depth,allowed", [(122, True), (124, False)])
def test_bundle_nesting_boundary_accounts_for_envelope_levels(monkeypatch, tmp_path, depth, allowed):
    nested = 0
    for _ in range(depth):
        nested = {"x": nested}
    request = body()
    request["citations"][0]["request"] = nested
    if not allowed:
        monkeypatch.setattr(cv.rules, "evaluate", forbidden_rule)
        with pytest.raises(cv.CitationVersionInputError, match="复核包"):
            cv.check(request)
        return
    path, bundle = tmp_path / "nested.json", tmp_path / "nested-bundle.json"
    write(path, request)
    checked = run("check", "--input", path, "--bundle", bundle)
    assert checked.returncode == 0, checked.stderr
    assert run("verify", "--bundle", bundle).returncode == 0


@pytest.mark.parametrize("raw", [XML, effect_source(10, 1000), XML.replace(b"Example.", b'\t&amp; &quot;'),
                               XML.replace(b"</ukm:Metadata>", b"<ukm:Metadata><dct:valid>\t2020-06-01</dct:valid></ukm:Metadata></ukm:Metadata>")])
def test_preflight_budget_covers_serialized_unmodified_b1_and_observations(raw):
    root, budget = cv._parse_xml(raw)
    output = cv.rules.evaluate(raw, make_request(raw), "B1")
    for value in (output, cv._observe(root)):
        actual = len((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        assert actual <= budget <= cv.MAX_SOURCE_EVIDENCE_BYTES
