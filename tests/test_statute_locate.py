"""独立扩展的定位、保守回指及原结果兼容性。"""
import json
from pathlib import Path

import pytest

from backend.core import statute_check as sc
from backend.core.statute_locate import check_answer_extended, locate_claim

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scenarios/deposit_insurance.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("item", [1, 2, 4])
def test_s26_type_item_error_located_and_recovered(item):
    # 仅陈述破产申请这项内容；归档 S26 的长句另行测试，不能用此句替代归档结果。
    answer = f"根据第十九条第一款第{sc.cn_label(item)}项，人民法院裁定受理对投保机构的破产申请。"
    result = check_answer_extended(answer, [SOURCE], SOURCE)
    assert not result["original"]["flagged"]
    assert result["flagged"]
    finding, = result["extensionFindings"]
    assert finding["code"] == "ITEM_MISATTRIBUTED"
    assert finding["located"] == "第十九条第一款第三项"
    recovery, = result["recoveries"]
    assert recovery["recovered"] == finding["located"]
    assert recovery["origin"] == "extension" and recovery["findingIndex"] == 0


def test_correct_item_is_not_flagged():
    result = check_answer_extended("根据第十九条第一款第三项，人民法院裁定受理对投保机构的破产申请。", [SOURCE], SOURCE)
    assert not result["flagged"]
    assert result["extensionFindings"] == []


def test_nonexistent_paragraph_has_recovery_without_changing_original():
    answer = "根据第八条第三款，本条例施行后开业的吸收存款的银行业金融机构，应当自工商行政管理部门颁发营业执照之日起6个月内办理投保手续。"
    original = sc.check_answer(answer, [SOURCE], SOURCE)
    result = check_answer_extended(answer, [SOURCE], SOURCE)
    assert result["original"] == original
    assert all("recovered" not in f for f in result["original"]["findings"])
    recovery, = result["recoveries"]
    assert recovery["code"] == "PROVISION_NOT_IN_SOURCE"
    assert recovery["recovered"] == "第八条第二款"


@pytest.mark.parametrize("source,claim", [
    ("第一条　办理保险业务。", "飞船进入木星轨道。"),
    ("第一条　银行应当办理保险手续。\n\n第二条　银行应当办理保险手续。", "银行应当办理保险手续。"),
    ("", "保险"),
])
def test_low_coverage_ties_and_empty_source_are_undetermined(source, claim):
    result = locate_claim(claim, sc.parse_provisions(source))
    assert result["location"] == {"article": None, "paragraph": None, "item": None}
    assert result["recovered"] is None
    assert result["levels"]["paragraph"]["coverage"] is None


def test_paragraph_tie_stops_before_item():
    source = "第一条　银行应当办理保险手续。\n银行应当办理保险手续。"
    result = locate_claim("银行应当办理保险手续。", sc.parse_provisions(source))
    assert result["location"] == {"article": 1, "paragraph": None, "item": None}
    assert result["recovered"] == "第一条"
    assert result["levels"]["paragraph"]["coverage"] == 1
    assert result["levels"]["item"]["coverage"] is None


def test_quoted_reference_and_later_reference_are_excluded():
    answer = "人民法院裁定受理对投保机构的破产申请，参照第十九条第一款第三项和第十九条第一款第一项。"
    assert not check_answer_extended(answer, [SOURCE], SOURCE)["extensionFindings"]
    quote_source = SOURCE + "\n\n第二十条　根据第十九条第一款第一项，人民法院裁定受理对投保机构的破产申请。"
    assert not check_answer_extended("根据第十九条第一款第一项，人民法院裁定受理对投保机构的破产申请。", [quote_source], quote_source)["extensionFindings"]


def test_cross_reference_exemption():
    source = "第一条　包括以下情形：\n（一）银行办理业务。\n（二）人民法院裁定受理对投保机构的破产申请，适用第一条第一款第一项。"
    answer = "根据第一条第一款第一项，人民法院裁定受理对投保机构的破产申请。"
    assert not check_answer_extended(answer, [source], source)["extensionFindings"]


def test_repeated_missing_reference_recovers_its_own_sentence():
    source = "第一条　银行办理保险手续。\n\n第二条　人民法院受理破产申请。"
    answer = "根据第三条，银行办理保险手续。根据第三条，人民法院受理破产申请。"
    result = check_answer_extended(answer, [source], source)
    assert [r["recovered"] for r in result["recoveries"]] == ["第一条第一款", "第二条第一款"]


def test_archived_full_s26_stays_below_unchanged_item_threshold():
    package = json.loads((ROOT / "docs/competition/statute-check/20261021-pinpoint-run/run/package.json").read_text(encoding="utf-8"))
    for item in package["items"]:
        if item["type"] != "item_existing":
            continue
        result = check_answer_extended(item["statement"], item["contexts"], SOURCE, item["question"])
        assert not result["extensionFindings"]
        located = locate_claim(item["statement"], sc.parse_provisions(SOURCE))
        assert located["levels"]["item"]["coverage"] == pytest.approx(16 / 45)
        assert located["recovered"] == "第十九条第一款"


def test_original_findings_preserved_for_every_archived_item():
    for name in ("20261021-pinpoint-run", "20261021-formal"):
        run = ROOT / "docs/competition/statute-check" / name / "run"
        package = json.loads((run / "package.json").read_text(encoding="utf-8"))
        source = (run / "inputs/scenarios/deposit_insurance.txt").read_text(encoding="utf-8")
        for item in package["items"]:
            original = sc.check_answer(item["statement"], item["contexts"], source, item["question"])
            result = check_answer_extended(item["statement"], item["contexts"], source, item["question"])
            assert result["original"] == original
            for before, after in zip(original["findings"], result["original"]["findings"]):
                assert before == after
