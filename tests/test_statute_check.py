"""法条结构核对：条款结构解析、F04 真实案例、已归档 100 个回答上的检出与误报。"""
import json
from pathlib import Path

from backend.core.statute_check import check_answer, cn_label, cn_number, parse_provisions
from tools.competition.statute_scan import RUNS, rows, scan

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scenarios/deposit_insurance.txt").read_text(encoding="utf-8")
CASES = json.loads((ROOT / "scenarios/competition_25/case-manifest.json").read_text(encoding="utf-8"))["cases"]
CTX_19 = ["（四）经国务院批准的其他情形。\n存款保险基金管理机构应当依照本条例的规定，在前款规定情形发生之日起7个工作日内足额偿付存款。"]


def codes(result, severity="error"):
    return {f["code"] for f in result["findings"] if f["severity"] == severity}


def test_chinese_numbers_round_trip():
    assert [cn_number(x) for x in ("五", "十", "十九", "二十一", "一百零五", "19")] == [5, 10, 19, 21, 105, 19]
    assert [cn_label(x) for x in (5, 10, 19, 21, 105)] == ["五", "十", "十九", "二十一", "一百零五"]


def test_provisions_keep_paragraphs_and_items():
    provisions = parse_provisions(SOURCE)
    assert sorted({p.article for p in provisions}) == [1, 2, 3, 4, 5, 8, 9, 10, 19]
    nineteen = [p.label for p in provisions if p.article == 19]
    assert nineteen == ["第十九条第一款", *[f"第十九条第一款第{x}项" for x in "一二三四"], "第十九条第二款"]
    assert [p.paragraph for p in provisions if p.article == 5] == [1, 2, 3, 4]


def test_headerless_context_is_located_to_its_article():
    result = check_answer("偿付期限为7个工作日。", CTX_19, SOURCE)
    assert result["contextProvisions"] == [["第十九条第一款第四项", "第十九条第二款"]]


def test_f04_archived_answer_is_caught():
    case = next(c for c in CASES if c["id"] == "F04")
    row = rows(ROOT / RUNS["20261014-qwen72b"])[case["rowIndex"]]
    result = check_answer(row["answer"], row["contexts"], SOURCE, row["question"])
    assert codes(result) == {"ARTICLE_NOT_IN_SOURCE", "NEGATION_CONTRADICTED"}
    # 回答在否定题目里的「7个自然日」，不算无依据的数额
    assert codes(result, "info") == {"FROM_QUESTION"}


def test_relative_reference_rewritten_as_absolute_is_not_misattribution():
    answer = "外国银行在中国境内设立的分支机构：不适用第二条第一款关于投保存款保险的规定。"
    contexts = [SOURCE.split("第三条")[0]]
    assert not check_answer(answer, contexts, SOURCE)["flagged"]


def test_quantity_value_unit_and_contrast():
    assert codes(check_answer("应在10个工作日内足额偿付存款。", CTX_19, SOURCE)) == {"QUANTITY_UNSUPPORTED"}
    assert codes(check_answer("应在7日内足额偿付存款。", CTX_19, SOURCE)) == {"UNIT_MISMATCH"}
    assert not check_answer("时限为“7个工作日”而非“7个自然日”。", CTX_19, SOURCE)["flagged"]


def test_amounts_from_question_are_reported_not_flagged():
    question = "本金合计49万元、利息合计2万元，如何计算？"
    contexts = ["第五条　存款保险实行限额偿付，最高偿付限额为人民币50万元。"]
    result = check_answer("合计51万元，超出部分为1万元。", contexts, SOURCE, question)
    assert not result["flagged"] and codes(result, "info") == {"DERIVED"}


def test_modal_swap_is_caught():
    contexts = [SOURCE.split("第三条")[0]]
    answer = "商业银行等吸收存款的银行业金融机构可以依照本条例的规定投保存款保险。"
    assert codes(check_answer(answer, contexts, SOURCE)) == {"MODAL_SWAP"}


def test_archived_answers_flag_only_f04():
    """四次已归档运行共 100 个回答：只有 10-14 的 F04 被检出；其余 99 个没有 error 级发现。"""
    result = scan()
    assert result["answers"] == 100 and result["flagged"] == 1
    assert {name: run["flagged"] for name, run in result["runs"].items()} == {
        "20261005-deepseek": [], "20261014-qwen72b": ["F04"], "20261014-example-app": [], "20261021-anythingllm": []}
