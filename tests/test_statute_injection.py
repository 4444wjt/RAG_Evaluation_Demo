"""法条样式错误注入实验：只做离线检查，永不调用模型。"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tools.competition import statute_injection as si


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    directory = tmp_path_factory.mktemp("statute-injection") / "prepared"
    return directory, si.prepare(directory)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must never construct the paid transport")
    monkeypatch.setattr(si, "make_transport", forbidden)
    monkeypatch.setattr(si, "git_state", lambda root: {"head": None, "status": None, "note": "synthetic offline test"})
    monkeypatch.setattr(si, "dependency_versions", lambda: {"test": "offline"})


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def approved_files(tmp_path, prepared, budget=10):
    """只给测试副本填测试身份，不是作者确认或付费授权。"""
    package = deepcopy(prepared[1]["package"])
    package["review"] = {"status": "confirmed", "reviewer": "offline-test-only", "reviewedAt": "2026-10-21", "note": "Synthetic test fixture."}
    package_path = tmp_path / "test-package.json"
    write(package_path, package)
    authorization = si.authorization_template(package, package_path)
    authorization.update(status="authorized", authorizedBy="offline-test-only", authorizedAt="2026-10-21", allowPaidCalls=True,
                         acceptNoHardBillingCap=True, budgetCny=budget, outputDirectory=str((tmp_path / "run").resolve()))
    for price in authorization["prices"].values():
        price.update(inputCnyPerMillion=1, outputCnyPerMillion=2, source="synthetic test rate", checkedAt="2026-10-21")
    authorization_path = tmp_path / "test-authorization.json"
    write(authorization_path, authorization)
    return package, package_path, authorization_path


def response(verdict="supported"):
    content = json.dumps({"verdict": verdict, "reason": "离线模拟判定，不是实验结论。", "citations": []}, ensure_ascii=False)
    return {"model": "offline-response-model", "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def test_items_are_single_edits_of_unanimously_supported_statements(prepared):
    package = prepared[1]["package"]
    assert len(package["items"]) == len(si.RECIPES) == 42
    for item in package["items"]:
        assert set(item["baseHumanLabels"].values()) == {"支持"}
        assert (item["edit"] is None) == (item["type"] == "control")
        assert item["expected"] == ("supported" if item["type"].startswith("control") else "not_supported")
    assert package["review"]["status"] == "pending"
    si.verify_checksums(prepared[0])


def test_rules_results_are_fixed_before_any_judge_call(prepared):
    package, rules = prepared[1]["package"], prepared[1]["rules"]
    flagged = {kind: sum(rules["items"][i["id"]]["flagged"] for i in package["items"] if i["type"] == kind) for kind in si.TYPES}
    assert flagged == {"control": 0, "control_paragraph": 0, "article_swap": 6, "article_absent": 7, "paragraph_shift": 1,
                       "quantity_value": 4, "quantity_unit": 2, "modal": 4, "polarity": 0}


def test_prompt_does_not_leak_type_expectation_edit_or_rules(prepared):
    for item in prepared[1]["package"]["items"]:
        system, user = si.prompt_messages(item, item)
        assert system["content"] == si.SYSTEM_PROMPT
        payload = json.loads(user["content"])
        assert set(payload) == {"question", "statement", "contexts"} and payload["statement"] == item["statement"]
        for leak in (item["type"], item["id"], "not_supported", "MISATTRIBUTED", "支持", item["base"]):
            assert leak not in user["content"]


def test_pending_review_blocks_before_transport(tmp_path, prepared):
    calls = []
    with pytest.raises(ValueError, match="人工确认"):
        si.run(prepared[0] / "package.json", prepared[0] / "authorization.draft.json", tmp_path / "run", transport=lambda *a: calls.append(a))
    assert calls == []


def test_full_offline_run_archives_and_replays(tmp_path, prepared):
    package, package_path, auth_path = approved_files(tmp_path, prepared)
    calls = []
    def transport(judge, payload):
        calls.append(judge)
        return response("contradicted" if len(calls) % 2 else "supported")
    output = tmp_path / "run"
    manifest = si.run(package_path, auth_path, output, transport=transport)
    assert manifest["attempts"] == manifest["completedRecords"] == len(calls) == 84 and manifest["stopReason"] == "completed"
    first = si.analyze(output, tmp_path / "analysis-a")
    second = si.analyze(output, tmp_path / "analysis-b")
    assert first == second and first["records"] == {"total": 84, "ok": 84}
    assert sum(row["items"] for row in first["byType"].values()) == 42
    assert (tmp_path / "analysis-a/analysis.json").read_bytes() == (tmp_path / "analysis-b/analysis.json").read_bytes()


def test_budget_guard_stops_before_first_call(tmp_path, prepared):
    _, package_path, auth_path = approved_files(tmp_path, prepared, budget=0.000001)
    calls = []
    manifest = si.run(package_path, auth_path, tmp_path / "run", transport=lambda *a: calls.append(a))
    assert calls == [] and manifest["stopReason"] == "estimated_budget_guard"


def test_changed_material_is_rejected(prepared):
    package = deepcopy(prepared[1]["package"])
    package["items"][0]["statement"] += "。"
    with pytest.raises(ValueError, match="固定配方"):
        si.validate_package(package)


def test_pilot_subset_is_fixed_and_runs_offline(tmp_path):
    directory = tmp_path / "pilot"
    pilot = si.prepare(directory, pilot=True)
    package = pilot["package"]
    assert [i["id"] for i in package["items"]] == [r[0] for r in si.RECIPES if r[0] in si.PILOT_IDS]
    assert len(package["items"]) == 10 and {i["type"] for i in package["items"]} == set(si.TYPES)
    assert package["pilot"] == si.pilot_info() and si.max_requests(package) == 20
    si.verify_checksums(directory)
    tampered = deepcopy(package)
    tampered["pilot"]["ids"] = tampered["pilot"]["ids"][:-1]
    with pytest.raises(ValueError, match="试跑子集"):
        si.validate_package(tampered)
    calls = []
    _, package_path, auth_path = approved_files(tmp_path, (directory, pilot))
    manifest = si.run(package_path, auth_path, tmp_path / "run", transport=lambda judge, payload: calls.append(judge) or response())
    assert manifest["attempts"] == len(calls) == 20 and manifest["stopReason"] == "completed"
    assert si.analyze(tmp_path / "run", tmp_path / "analysis")["records"] == {"total": 20, "ok": 20}
