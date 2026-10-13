"""Offline-only checks for the independent judge experiment; never call a model."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.competition import judge_sensitivity as js

REAL_MAKE_TRANSPORT = js.make_transport


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    directory = tmp_path_factory.mktemp("sensitivity-materials") / "prepared"
    package = js.prepare(directory)
    return directory, package


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must never construct the paid transport")
    monkeypatch.setattr(js, "make_transport", forbidden)
    monkeypatch.setattr(js, "git_state", lambda root: {"head": None, "status": None, "note": "synthetic offline test"})
    monkeypatch.setattr(js, "dependency_versions", lambda: {"test": "offline"})


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def approved_files(tmp_path, prepared):
    """Only synthetic test copies receive test identities, never production materials."""
    package = deepcopy(prepared[1])
    for sample in package["samples"]:
        for variant in sample["variants"]:
            variant["review"] = {"status": "confirmed", "reviewer": "offline-test-only", "reviewedAt": "2026-10-12", "note": "Synthetic test fixture; not an author confirmation."}
    package_path = tmp_path / "test-package.json"
    write(package_path, package)
    authorization = js.authorization_template(package, package_path)
    authorization.update(status="authorized", authorizedBy="offline-test-only", authorizedAt="2026-10-12", allowPaidCalls=True, acceptNoHardBillingCap=True, budgetCny=10, outputDirectory=str((tmp_path / "run").resolve()))
    for price in authorization["prices"].values():
        price.update(inputCnyPerMillion=1, outputCnyPerMillion=2, source="synthetic test rate; not a provider quote", checkedAt="2026-10-12")
    authorization_path = tmp_path / "test-authorization.json"
    write(authorization_path, authorization)
    return package, package_path, authorization, authorization_path


def response(content=None, *, usage=True):
    result = {"model": "offline-response-model", "choices": [{"message": {"content": content if content is not None else json.dumps({"verdict": "supported", "reason": "离线模拟判定", "citations": []})}, "finish_reason": "stop"}]}
    if usage:
        result["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return result


def mock_content(payload, verdict="supported"):
    evidence = json.loads(payload["messages"][1]["content"])["contexts"]
    return json.dumps({"verdict": verdict, "reason": "这是离线模拟响应，不是实验结论。", "citations": [{"contextIndex": 0, "quote": evidence[0]["text"][:20]}] if evidence else []}, ensure_ascii=False)


def events(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def test_prepare_preserves_sources_and_keeps_every_review_pending(prepared):
    directory, package = prepared
    js.verify_sources(package["sourceFiles"])
    js.verify_checksums(directory)
    assert len(package["samples"]) == 6
    assert len({(s["source"]["modelId"], s["source"]["rowIndex"]) for s in package["samples"]}) == 6
    assert all(v["review"] == js.pending() for s in package["samples"] for v in s["variants"])
    assert len(js.schedule(package)) == 60
    assert len({job["requestId"] for job in js.schedule(package)}) == 60
    assert not (directory / "events.jsonl").exists()
    assert js.load(directory / "authorization.draft.json")["allowPaidCalls"] is False
    assert js.load(directory / "preview/summary.json")["requestStates"] == {"pending": 60}


def test_five_conditions_only_change_the_evidence_and_repeat_is_identical(prepared):
    for sample in prepared[1]["samples"]:
        original, repeat, shuffled, removed, contradicted = sample["variants"]
        assert [v["id"] for v in sample["variants"]] == list(js.CONDITIONS)
        assert [v["expected"] for v in sample["variants"]] == ["supported"] * 3 + ["insufficient", "contradicted"]
        assert js.prompt_messages(sample, original) == js.prompt_messages(sample, repeat)
        assert sorted(shuffled["contexts"]) == sorted(sample["contexts"])
        assert shuffled["contexts"] != original["contexts"]
        assert removed["contexts"] == [text for i, text in enumerate(sample["contexts"]) if i not in sample["supportContextIndices"]]
        assert contradicted["contexts"] != original["contexts"]
        for variant in sample["variants"]:
            payload = json.loads(js.prompt_messages(sample, variant)[1]["content"])
            assert payload["question"] == sample["question"]
            assert payload["statement"] == sample["statement"]
            assert set(payload) == {"question", "statement", "contexts"}


def test_prompt_does_not_leak_answer_original_verdict_expectation_or_variant(prepared):
    sample = deepcopy(prepared[1]["samples"][0])
    sample.update(answer="ANSWER-SHOULD-NOT-LEAK", originalMachineVerdict="ORIGINAL-VERDICT-SECRET", originalCitation="ORIGINAL-QUOTE-SECRET", ground_truth="REFERENCE-SECRET")
    variant = deepcopy(sample["variants"][0])
    variant.update(id="VARIANT-SECRET", expected="EXPECTED-SECRET", review={"note": "REVIEW-SECRET"})
    messages = js.prompt_messages(sample, variant)
    serialized = json.dumps(messages)
    for sentinel in (sample["answer"], sample["originalMachineVerdict"], sample["originalCitation"], sample["ground_truth"], variant["id"], variant["expected"], variant["review"]["note"]):
        assert sentinel not in serialized
    assert len(messages) == 2 and messages[0]["role"] == "system" and messages[1]["role"] == "user"


def test_schedule_is_deterministic_and_parameters_disable_retries(prepared):
    package = prepared[1]
    assert js.schedule(package) == js.schedule(deepcopy(package))
    assert all(config["maxRetries"] == 0 for config in package["judges"].values())
    assert set(package["judges"]) == {"deepseek", "qwen72b"}
    for sample in package["samples"]:
        assert sum(job["sampleId"] == sample["id"] for job in js.schedule(package)) == 10


def test_pending_materials_block_before_transport(tmp_path, prepared):
    calls = []
    with pytest.raises(ValueError, match="尚未逐条人工确认"):
        js.run(prepared[0] / "package.json", prepared[0] / "authorization.draft.json", tmp_path / "run", transport=lambda *args: calls.append(args))
    assert calls == [] and not (tmp_path / "run").exists()


@pytest.mark.parametrize("field", ["packageSha256", "configurationSha256", "promptSha256", "toolSha256"])
def test_authorization_hash_mismatches_block(tmp_path, prepared, field):
    package, package_path, auth, auth_path = approved_files(tmp_path, prepared)
    auth[field] = "0" * 64
    write(auth_path, auth)
    calls = []
    with pytest.raises(ValueError):
        js.run(package_path, auth_path, tmp_path / "run", transport=lambda *args: calls.append(args))
    assert not calls and not (tmp_path / "run").exists()


@pytest.mark.parametrize("field,value", [("maxRequests", 61), ("maxRetries", 1), ("allowPaidCalls", False), ("acceptNoHardBillingCap", False), ("budgetCny", None)])
def test_authorization_limits_cannot_be_expanded(tmp_path, prepared, field, value):
    package, package_path, auth, _ = approved_files(tmp_path, prepared)
    auth[field] = value
    with pytest.raises(ValueError):
        js.validate_authorization(package, package_path, auth)


@pytest.mark.parametrize("mutation", ["source", "question", "statement", "contexts", "model", "review"])
def test_source_and_material_mutations_are_rejected(prepared, mutation):
    package = deepcopy(prepared[1])
    if mutation == "source":
        package["sourceFiles"][next(iter(package["sourceFiles"]))] = "0" * 64
    elif mutation == "model":
        package["judges"]["deepseek"]["model"] = "unapproved-model"
    elif mutation == "review":
        package["samples"][0]["variants"][0]["review"]["reviewer"] = "not-confirmed"
    else:
        package["samples"][0][mutation] = ["altered context"] if mutation == "contexts" else "altered"
    with pytest.raises(ValueError):
        js.validate_package(package)


@pytest.mark.parametrize("content,status", [(None, "empty_output"), (" ", "empty_output"), ("{broken", "parse_error"), ("[]", "invalid_structure"), ('{"verdict":"supported","reason":"","citations":[]}', "invalid_structure"), ('{"verdict":"invented","reason":"why","citations":[]}', "invalid_structure"), ('{"verdict":"supported","reason":"why","citations":[{"contextIndex":true,"quote":"a"}]}', "invalid_structure")])
def test_empty_malformed_and_invalid_model_outputs_are_distinct(content, status):
    assert js.parse_output(content, ["abc"]) == (status, None)


@pytest.mark.parametrize("citations,expected", [([], "missing"), ([{"contextIndex": 0, "quote": "原文"}], "located"), ([{"contextIndex": 0, "quote": "不存在的引用"}], "unmatched"), ([{"contextIndex": 99, "quote": "原文"}], "unmatched")])
def test_citation_location_is_separate_from_machine_verdict(citations, expected):
    status, result = js.parse_output(json.dumps({"verdict": "supported", "reason": "机器判断", "citations": citations}), ["这是一段原文"])
    assert status == "ok" and result["verdict"] == "supported"
    assert result["citationStatus"] == expected


def test_sixty_attempts_include_parse_failures_without_retry_and_archive_replays(tmp_path, prepared):
    package, package_path, _, auth_path = approved_files(tmp_path, prepared)
    calls = []
    def transport(judge, payload):
        calls.append((judge, payload))
        index = len(calls)
        content = "{invalid-json" if index % 10 == 0 else mock_content(payload)
        return response(content)
    output = tmp_path / "run"
    manifest = js.run(package_path, auth_path, output, transport=transport)
    assert manifest["attempts"] == manifest["completedRecords"] == len(calls) == 60
    assert manifest["stopReason"] == "completed" and manifest["actualBillCny"] is None
    assert manifest["protectedSourcesUnchanged"] is True
    records = js.read_records(output, package)
    assert len(records) == 60 and sum(r["status"] == "parse_error" for r in records) == 6
    assert {r["actualModel"] for r in records} == {"offline-response-model"}
    raw_events = events(output)
    assert len(raw_events) == 120
    assert [e["requestId"] for e in raw_events if e["event"] == "start"] == [j["requestId"] for j in js.schedule(package)]
    js.verify_checksums(output)
    js.verify_sources(package["sourceFiles"])
    first = js.analyze(output, tmp_path / "analysis-a")
    second = js.analyze(output, tmp_path / "analysis-b")
    assert first == second and first["requestStates"] == {"ok": 54, "parse_error": 6}
    assert (tmp_path / "analysis-a/report.html").read_bytes() == (tmp_path / "analysis-b/report.html").read_bytes()
    assert (tmp_path / "analysis-a/README.md").read_bytes() == (tmp_path / "analysis-b/README.md").read_bytes()
    for judge in first["judges"].values():
        for condition in judge["variants"].values():
            assert condition["validVerdicts"] == condition["expectedComparison"]["validDenominator"]
            assert sum(condition["states"].values()) == condition["planned"] == 6
        for pair in judge["pairedConsistency"].values():
            assert pair["validPairs"] + pair["unavailablePairs"] == 6


def test_call_failure_retained_and_missing_usage_stops_without_retry(tmp_path, prepared):
    package, package_path, _, auth_path = approved_files(tmp_path, prepared)
    calls = []
    def transport(*args):
        calls.append(args)
        raise RuntimeError("DO-NOT-LOG-SECRET")
    output = tmp_path / "run"
    manifest = js.run(package_path, auth_path, output, transport=transport)
    assert manifest["attempts"] == len(calls) == 1 and manifest["stopReason"] == "usage_unknown"
    assert manifest["missingUsage"] == 1 and manifest["actualBillCny"] is None
    record = js.read_records(output, package)[0]
    assert record["status"] == "call_error" and record["usage"] is None
    assert "DO-NOT-LOG-SECRET" not in (output / "events.jsonl").read_text(encoding="utf-8")
    summary = js.analyze(output, tmp_path / "analysis")
    assert summary["requestStates"] == {"call_error": 1, "pending": 59}


def test_valid_response_without_usage_stops_with_usage_unknown(tmp_path, prepared):
    package, package_path, _, auth_path = approved_files(tmp_path, prepared)
    calls = []
    def transport(judge, payload):
        calls.append(payload)
        return response(mock_content(payload), usage=False)
    output = tmp_path / "run"
    manifest = js.run(package_path, auth_path, output, transport=transport)
    assert manifest["attempts"] == len(calls) == 1 and manifest["stopReason"] == "usage_unknown"
    assert js.read_records(output, package)[0]["status"] == "ok"


def test_budget_guard_stops_before_first_call(tmp_path, prepared):
    _, package_path, auth, auth_path = approved_files(tmp_path, prepared)
    auth["budgetCny"] = 0.00000001
    write(auth_path, auth)
    calls = []
    output = tmp_path / "run"
    manifest = js.run(package_path, auth_path, output, transport=lambda *args: calls.append(args))
    assert manifest["attempts"] == 0 and manifest["stopReason"] == "estimated_budget_guard"
    assert calls == [] and not (output / "events.jsonl").exists()
    js.verify_checksums(output)


def test_interrupted_request_is_preserved_as_incomplete(tmp_path, prepared):
    package, package_path, _, auth_path = approved_files(tmp_path, prepared)
    def transport(*args):
        raise KeyboardInterrupt()
    output = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt):
        js.run(package_path, auth_path, output, transport=transport)
    assert js.load(output / "manifest.json")["stopReason"] == "interrupted"
    records = js.read_records(output, package)
    assert len(records) == 1 and records[0]["status"] == "incomplete"
    summary = js.analyze(output, tmp_path / "analysis")
    assert summary["requestStates"] == {"incomplete": 1, "pending": 59}


def test_read_records_rejects_rewritten_result_and_wrong_pair(tmp_path, prepared):
    package, package_path, _, auth_path = approved_files(tmp_path, prepared)
    output = tmp_path / "run"
    js.run(package_path, auth_path, output, transport=lambda j, p: response(mock_content(p), usage=False))
    originals = events(output)
    for field, value, error in [("result", {"verdict": "contradicted"}, "保存的判定"), ("sampleId", "different-answer", "配对不一致")]:
        modified = deepcopy(originals)
        modified[1][field] = value
        (output / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in modified) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=error):
            js.read_records(output, package)


def test_analysis_checks_archive_hashes_and_never_overwrites(tmp_path, prepared):
    _, package_path, _, auth_path = approved_files(tmp_path, prepared)
    output = tmp_path / "run"
    js.run(package_path, auth_path, output, transport=lambda j, p: response(mock_content(p), usage=False))
    with pytest.raises(ValueError, match="原运行目录之外"):
        js.analyze(output, output / "nested-report")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="输出已存在"):
        js.analyze(output, existing)
    with (output / "prompt.txt").open("a", encoding="utf-8") as stream:
        stream.write("tamper")
    with pytest.raises(ValueError, match="归档校验失败"):
        js.analyze(output, tmp_path / "tampered-analysis")


def test_prepare_does_not_overwrite_existing_materials(prepared):
    with pytest.raises(ValueError, match="不覆盖"):
        js.prepare(prepared[0])


def test_missing_required_source_hash_rejected(prepared):
    package = deepcopy(prepared[1])
    package["sourceFiles"].pop(next(iter(package["sourceFiles"])))
    with pytest.raises(ValueError):
        js.validate_package(package)


def test_authorization_cannot_be_reused_in_another_directory(tmp_path, prepared):
    _, package_path, _, auth_path = approved_files(tmp_path, prepared)
    calls = []
    with pytest.raises(ValueError):
        js.run(package_path, auth_path, tmp_path / "different-run", transport=lambda *args: calls.append(args))
    assert calls == [] and not (tmp_path / "different-run").exists()


@pytest.mark.parametrize("mutation", ["missing_response", "actual_model", "invalid_choices"])
def test_raw_response_cannot_be_replaced_by_forged_success(tmp_path, prepared, mutation):
    package, package_path, _, auth_path = approved_files(tmp_path, prepared)
    output = tmp_path / "run"
    js.run(package_path, auth_path, output, transport=lambda j, p: response(mock_content(p), usage=False))
    modified = events(output)
    if mutation == "missing_response":
        modified[1]["response"] = None
    elif mutation == "actual_model":
        modified[1]["actualModel"] = "wrong-model"
    else:
        modified[1]["response"]["choices"] = []
    (output / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in modified) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        js.read_records(output, package)


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1}, {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3}, {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 99}])
def test_missing_or_inconsistent_usage_is_not_zero(usage):
    assert js.usage_from({"usage": usage}) is None


@pytest.mark.parametrize("endpoint", ["http://api.deepseek.com", "https://user:secret@api.deepseek.com", "https://api.deepseek.com?api_key=secret", "https://other-provider.example"])
def test_endpoint_rejects_plain_http_credentials_and_provider_switch(monkeypatch, endpoint):
    config = js.judge_config()["deepseek"]
    monkeypatch.setenv(config["baseUrlEnv"], endpoint)
    with pytest.raises(ValueError):
        js.endpoint(config)


def test_report_pending_expectations_have_no_accuracy_denominator(prepared):
    summary = js.load(prepared[0] / "preview/summary.json")
    for judge in summary["judges"].values():
        for variant in judge["variants"].values():
            assert variant["humanConfirmed"] == 0
            assert variant["expectedComparison"] == {"matches": 0, "validDenominator": 0}


def test_missing_actual_response_model_stops_after_one_attempt(tmp_path, prepared):
    _, package_path, _, auth_path = approved_files(tmp_path, prepared)
    calls = []
    def transport(judge, payload):
        calls.append(payload)
        data = response(mock_content(payload))
        data.pop("model")
        return data
    manifest = js.run(package_path, auth_path, tmp_path / "run", transport=transport)
    assert len(calls) == manifest["attempts"] == 1
    assert manifest["stopReason"] == "actual_model_unknown"


def test_manifest_is_in_external_hash_list_without_self_reference(tmp_path, prepared):
    _, package_path, _, auth_path = approved_files(tmp_path, prepared)
    output = tmp_path / "run"
    js.run(package_path, auth_path, output, transport=lambda j, p: response(mock_content(p), usage=False))
    names = [line.split("  ", 1)[1] for line in (output / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()]
    assert "manifest.json" in names and "SHA256SUMS.txt" not in names
    assert len(names) == len(set(names))


@pytest.mark.parametrize("mode,expected_error", [("http_error", "http_error"), ("non_json", "response_json_error"), ("non_object", "response_structure_error"), ("success", None)])
def test_transport_has_no_retry_or_redirect_and_redacts_retained_bodies(monkeypatch, prepared, mode, expected_error):
    import httpx

    package = prepared[1]
    secrets = {"deepseek": "offline-secret-deepseek-123", "qwen72b": "offline-secret-qwen-456"}
    for judge, config in package["judges"].items():
        monkeypatch.setenv(config["apiKeyEnv"], secrets[judge])
        monkeypatch.delenv(config["baseUrlEnv"], raising=False)
    body = f"provider diagnostic, first={secrets['deepseek']}; second={secrets['qwen72b']}"
    constructors, requests, transports = [], [], []

    class FakeResponse:
        is_success = mode != "http_error"
        status_code = 503 if mode == "http_error" else 200
        text = body

        def json(self):
            if mode == "non_json":
                raise ValueError("not JSON")
            if mode == "non_object":
                return [body]
            return {"model": "synthetic", "nested": [{secrets["deepseek"]: body}], "choices": []}

    class FakeClient:
        def __init__(self, **kwargs):
            constructors.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return FakeResponse()

    def fake_http_transport(**kwargs):
        transports.append(kwargs)
        return "offline-http-transport"

    monkeypatch.setattr(httpx, "HTTPTransport", fake_http_transport)
    monkeypatch.setattr(httpx, "Client", FakeClient)
    sender = REAL_MAKE_TRANSPORT(package)
    result = sender("deepseek", {"messages": [], "model": "deepseek-chat"})
    assert transports == [{"retries": 0}]
    assert len(requests) == len(constructors) == 1
    assert constructors[0]["follow_redirects"] is False
    assert requests[0][0] == package["judges"]["deepseek"]["baseUrl"].rstrip("/") + "/chat/completions"
    serialized = json.dumps(result)
    assert all(secret not in serialized for secret in secrets.values())
    assert "[REDACTED]" in serialized
    if expected_error:
        assert result["_transport"]["error"] == expected_error
        assert "provider diagnostic" in result["_transport"]["body"]
        assert result["_transport"]["httpStatus"] == FakeResponse.status_code
    else:
        assert result["nested"][0]["[REDACTED]"].startswith("provider diagnostic")


def test_auxiliary_tool_source_hash_mismatch_blocks_before_call(tmp_path, prepared):
    _, package_path, auth, auth_path = approved_files(tmp_path, prepared)
    helper = next(name for name in auth["toolFiles"] if name.endswith("judge_sensitivity_report.py"))
    auth["toolFiles"][helper] = "0" * 64
    write(auth_path, auth)
    calls = []
    with pytest.raises(ValueError, match="来源哈希不一致"):
        js.run(package_path, auth_path, tmp_path / "run", transport=lambda *args: calls.append(args))
    assert calls == [] and not (tmp_path / "run").exists()


def test_final_source_check_failure_still_preserves_manifest_and_hashes(tmp_path, prepared, monkeypatch):
    package, package_path, _, auth_path = approved_files(tmp_path, prepared)
    original_verify = js.verify_sources
    after_call = False

    def verify(files, root=js.ROOT):
        if after_call and files == package["sourceFiles"]:
            raise ValueError("simulated concurrent source change; no real source modified")
        return original_verify(files, root)

    def transport(judge, payload):
        nonlocal after_call
        after_call = True
        return response(mock_content(payload), usage=False)

    monkeypatch.setattr(js, "verify_sources", verify)
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="来源检查失败；已保留"):
        js.run(package_path, auth_path, output, transport=transport)
    manifest = js.load(output / "manifest.json")
    assert manifest["protectedSourcesUnchanged"] is False
    assert manifest["stopReason"] == "source_changed"
    assert manifest["attempts"] == manifest["completedRecords"] == 1
    assert len(js.load(output / "records.json")) == 1
    js.verify_checksums(output)
    original_verify(package["sourceFiles"])


@pytest.mark.parametrize("mutation", ["schedule", "attempts", "completedRecords", "records"])
def test_analysis_rejects_semantic_archive_tampering_even_with_new_checksums(tmp_path, prepared, mutation):
    _, package_path, _, auth_path = approved_files(tmp_path, prepared)
    output = tmp_path / "run"
    js.run(package_path, auth_path, output, transport=lambda j, p: response(mock_content(p), usage=False))
    if mutation == "schedule":
        jobs = js.load(output / "schedule.json")
        jobs[0], jobs[1] = jobs[1], jobs[0]
        write(output / "schedule.json", jobs)
    elif mutation == "records":
        write(output / "records.json", [])
    else:
        manifest = js.load(output / "manifest.json")
        manifest[mutation] = 60
        write(output / "manifest.json", manifest)
    js.checksums(output)
    with pytest.raises(ValueError):
        js.analyze(output, tmp_path / "tampered-analysis")
    assert not (tmp_path / "tampered-analysis").exists()
