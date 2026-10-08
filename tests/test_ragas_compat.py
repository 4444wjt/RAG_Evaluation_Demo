"""真实 Ragas 0.1.21 的离线兼容测试，不调用模型或读取凭据。"""
from copy import deepcopy
from importlib.metadata import version
import json
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest
from langchain_core.outputs import Generation, LLMResult
from ragas.metrics import (
    AnswerCorrectness, Faithfulness, answer_correctness, answer_relevancy,
    context_precision, context_recall, faithfulness,
)
from ragas.metrics._answer_correctness import AnswerCorrectnessClassification
from ragas.metrics._faithfulness import StatementFaithfulnessAnswers

from backend.adapters.ragas_compat import (
    ChineseAnswerCorrectness, ChineseFaithfulness,
    SENTENCE_ADAPTER_VERSION, SUPPORTED_RAGAS_VERSION,
)
from backend.core import pipeline


@pytest.fixture(params=[ChineseAnswerCorrectness, ChineseFaithfulness])
def metric(request):
    return request.param()


def make_prompt(metric, text, question="测试问题"):
    if isinstance(metric, AnswerCorrectness):
        return metric._create_statements_prompt(question, text)
    return metric._create_statements_prompt({"question": question, "answer": text})


def prompt_fields(prompt):
    # Ragas 用 JSON 编码字符串，包括中文 Unicode 转义。
    task = prompt.to_string().rsplit("Your actual task:", 1)[1]
    fields = {}
    for line in task.splitlines():
        key, separator, value = line.partition(": ")
        if separator and key in {"question", "answer", "sentences"}:
            fields[key] = json.loads(value)
    return fields


@pytest.mark.parametrize("text,expected", [
    ("人民币50万元。", ["人民币50万元。"]),
    ("存款人需要缴费吗？", ["存款人需要缴费吗？"]),
    ("由投保机构缴费！", ["由投保机构缴费！"]),
    ("人民币50万元", ["人民币50万元"]),
    ("RAG 使用 Qwen2.5-7B。", ["RAG 使用 Qwen2.5-7B。"]),
    ("费率为3.5%。", ["费率为3.5%。"]),
    ("模型为Qwen2.5-7B。", ["模型为Qwen2.5-7B。"]),
    ("本金49万元。利息2万元。", ["本金49万元。", "利息2万元。"]),
    ("限额为50万元。利息合并计算", ["限额为50万元。", "利息合并计算"]),
    ("答案是“人民币50万元。”", ["答案是“人民币50万元。”"]),
    ("RAG。", ["RAG。"]),
])
def test_chinese_sentences_keep_original_text_and_order(metric, text, expected):
    fields = prompt_fields(make_prompt(metric, text))
    assert fields["question"] == "测试问题"
    assert fields["answer"] == text
    assert fields["sentences"] == "\n".join(
        f"{index}:{sentence}" for index, sentence in enumerate(expected)
    )


@pytest.mark.parametrize("text", [
    "The limit is 500,000 yuan.",
    "The rate is 3.5 percent. The model is Qwen2.5-7B.",
    "Is this covered?", "No ending punctuation", "Qwen2.5-7B",
])
def test_english_keeps_parent_behavior_even_with_chinese_question(metric, text):
    parent = AnswerCorrectness() if isinstance(metric, AnswerCorrectness) else Faithfulness()
    assert make_prompt(metric, text).to_string() == make_prompt(parent, text).to_string()


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_input_does_not_invent_sentences(metric, text):
    assert prompt_fields(make_prompt(metric, text))["sentences"] == ""


def test_reference_answer_and_answer_are_processed_independently():
    metric = ChineseAnswerCorrectness()
    assert prompt_fields(make_prompt(metric, "人民币50万元。"))["sentences"] == "0:人民币50万元。"
    assert prompt_fields(make_prompt(metric, "人民币50万元"))["sentences"] == "0:人民币50万元"
    assert prompt_fields(make_prompt(metric, "50 million RMB"))["sentences"] == ""


def test_faithfulness_does_not_mutate_the_evaluation_row():
    row = {"question": "限额是多少？", "answer": "人民币50万元。",
           "ground_truth": "人民币50万元。", "contexts": ["限额为50万元。"]}
    original = deepcopy(row)
    ChineseFaithfulness()._create_statements_prompt(row)
    assert row == original


def test_supported_version_and_adapter_identity():
    assert version("ragas") == SUPPORTED_RAGAS_VERSION == "0.1.21"
    assert SENTENCE_ADAPTER_VERSION == "zh-sentence-v1"
    assert ChineseAnswerCorrectness.sentence_adapter_version == SENTENCE_ADAPTER_VERSION
    assert ChineseFaithfulness.sentence_adapter_version == SENTENCE_ADAPTER_VERSION


def test_scoring_initialization_and_parsing_still_inherit_ragas():
    assert ChineseAnswerCorrectness._ascore is AnswerCorrectness._ascore
    assert ChineseAnswerCorrectness._compute_statement_presence is AnswerCorrectness._compute_statement_presence
    assert ChineseAnswerCorrectness.init is AnswerCorrectness.init
    assert ChineseFaithfulness._ascore is Faithfulness._ascore
    assert ChineseFaithfulness._compute_score is Faithfulness._compute_score
    assert ChineseFaithfulness.init is Faithfulness.init
    for compat, parent in [(ChineseAnswerCorrectness(), AnswerCorrectness()),
                           (ChineseFaithfulness(), Faithfulness())]:
        assert isinstance(compat, type(parent))
        assert compat.name == parent.name
        assert compat.evaluation_mode == parent.evaluation_mode
        assert compat.max_retries == parent.max_retries
    assert ChineseAnswerCorrectness().weights == [0.75, 0.25]
    assert ChineseFaithfulness().reproducibility == Faithfulness().reproducibility == 1


def test_correctness_factual_formula_is_unchanged():
    prediction = AnswerCorrectnessClassification(
        TP=[{"statement": "甲"}, {"statement": "乙"}],
        FP=[{"statement": "丙"}], FN=[{"statement": "丁"}],
    )
    assert ChineseAnswerCorrectness()._compute_statement_presence(prediction) == pytest.approx(2 / 3)


def test_faithfulness_formula_and_empty_result_are_unchanged():
    prediction = StatementFaithfulnessAnswers.parse_obj([
        {"statement": str(index), "reason": "模拟判定", "verdict": verdict}
        for index, verdict in enumerate([1, 1, 0])
    ])
    metric = ChineseFaithfulness()
    assert metric._compute_score(prediction) == pytest.approx(2 / 3)
    assert math.isnan(metric._compute_score(StatementFaithfulnessAnswers.parse_obj([])))


def llm_response(value):
    return LLMResult(generations=[[Generation(text=json.dumps(value))]])


def test_correctness_weighted_scoring_with_fake_judge():
    metric = ChineseAnswerCorrectness()
    metric.llm = SimpleNamespace(generate=AsyncMock(side_effect=[
        llm_response([{"sentence_index": 0, "simpler_statements": ["甲", "乙", "丙"]}]),
        llm_response([{"sentence_index": 0, "simpler_statements": ["甲", "乙", "丁"]}]),
        llm_response({"TP": [{"statement": "甲"}, {"statement": "乙"}],
                      "FP": [{"statement": "丙"}], "FN": [{"statement": "丁"}]}),
    ]))
    metric.answer_similarity = SimpleNamespace(ascore=AsyncMock(return_value=0.8))
    row = {"question": "哪些成立？", "answer": "甲成立。乙成立。丙成立。",
           "ground_truth": "甲成立。乙成立。丁成立。"}
    original = deepcopy(row)
    # 所有 await 均为立即完成的模拟调用；直接驱动协程，避免 Windows
    # 事件循环创建本地 socket，与现有严格离线网络阻断冲突。
    operation = metric._ascore(row, [])
    try:
        with pytest.raises(StopIteration) as completed:
            operation.send(None)
    finally:
        operation.close()
    score = completed.value.value
    assert score == pytest.approx(0.75 * (2 / 3) + 0.25 * 0.8)
    assert metric.llm.generate.await_count == 3
    assert row == original
    for call in metric.llm.generate.await_args_list[:2]:
        assert prompt_fields(call.args[0])["sentences"]
    metric.answer_similarity.ascore.assert_awaited_once_with(row, callbacks=[])


def test_compat_instances_do_not_mutate_global_metrics_or_prompts():
    correct = ChineseAnswerCorrectness()
    faithful = ChineseFaithfulness()
    for compat, original, attributes in [
        (correct, answer_correctness, ["long_form_answer_prompt", "correctness_prompt"]),
        (faithful, faithfulness, ["statement_prompt", "nli_statements_message"]),
    ]:
        assert compat is not original
        assert compat.sentence_segmenter is not original.sentence_segmenter
        for attribute in attributes:
            copied_prompt = getattr(compat, attribute)
            original_prompt = getattr(original, attribute)
            assert copied_prompt is not original_prompt
            assert copied_prompt.dict() == original_prompt.dict()
            assert copied_prompt.examples is not original_prompt.examples
    correct.long_form_answer_prompt.examples[0]["question"] = "仅测试本实例"
    correct.weights[0] = 0.5
    assert ChineseAnswerCorrectness().weights == answer_correctness.weights == [0.75, 0.25]
    assert faithful.statement_prompt.examples[0]["question"] != "仅测试本实例"
    assert answer_correctness.long_form_answer_prompt.examples[0]["question"] != "仅测试本实例"
    assert prompt_fields(make_prompt(answer_correctness, "人民币50万元。"))["sentences"] == ""
    assert prompt_fields(make_prompt(faithfulness, "人民币50万元。"))["sentences"] == ""


def test_web_pipeline_only_replaces_the_two_compatible_metrics():
    metrics = pipeline.RAGAS_METRICS
    assert [metric.name for metric in metrics] == [
        "faithfulness", "context_precision", "answer_relevancy",
        "answer_correctness", "context_recall",
    ]
    assert isinstance(metrics[0], ChineseFaithfulness)
    assert isinstance(metrics[3], ChineseAnswerCorrectness)
    assert metrics[0] is not faithfulness
    assert metrics[3] is not answer_correctness
    assert metrics[1] is context_precision
    assert metrics[2] is answer_relevancy
    assert metrics[4] is context_recall
    assert answer_relevancy.strictness == 1


def test_raw_pipeline_passes_unchanged_data_and_metrics_to_fake_evaluator(monkeypatch):
    answer = "人民币50万元。"
    contexts = ["最高偿付限额为人民币50万元。"]
    cases = [{"question": "限额是多少？", "ground_truth": "人民币50万元。", "case_type": "positive"}]
    original = deepcopy(cases)
    frame = pd.DataFrame({"answer": [answer], "question": [cases[0]["question"]]})
    evaluator = Mock(return_value=SimpleNamespace(to_pandas=lambda: frame.copy(deep=True)))
    trace_value = {"statements": [{"text": answer, "verdict": "supported", "evidence": contexts[0]}]}
    trace = Mock(return_value=trace_value)
    monkeypatch.setattr(pipeline, "rag_answer", Mock(return_value=(answer, contexts)))
    monkeypatch.setattr(pipeline, "ragas_evaluate", evaluator)
    monkeypatch.setattr(pipeline, "trace_hallucinations", trace)
    result = pipeline.run_single_model_raw(cases, "retriever", "embeddings", "llm", True)
    arguments = evaluator.call_args.kwargs
    assert arguments["dataset"].to_dict() == {
        "question": [cases[0]["question"]], "answer": [answer],
        "contexts": [contexts], "ground_truth": [cases[0]["ground_truth"]],
    }
    assert arguments["metrics"] is pipeline.RAGAS_METRICS
    assert arguments["llm"] == "llm" and arguments["embeddings"] == "embeddings"
    assert arguments["run_config"].max_workers == 1
    assert arguments["run_config"].timeout == pipeline.EVAL_TIMEOUT
    trace.assert_called_once_with(cases[0]["question"], answer, contexts, cases[0]["ground_truth"], "llm")
    assert json.loads(result["df"].iloc[0]["hallucination_trace"]) == trace_value
    assert result["trace_total"] == result["pos_trace_total"] == 1
    assert result["trace_fact_error"] == result["trace_fabrication"] == 0
    assert cases == original
