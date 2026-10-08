"""当前隐含数据结构的类型定义（仅文档化，不做运行时校验）。

这些类型描述 main.py 中流通的裸 dict/list 结构，用于提升可读性与可维护性，
不参与任何运行时校验、不改变任何行为。字段均标记为可选（total=False），
以如实反映运行时各阶段字段可能存在或缺失的现状。

对应来源（main.py）：
  - TestCase           → load_custom_testset / load_benchmark / generate_testset 返回的测试集条目
  - TraceStatement     → trace_hallucinations 返回的 statements 元素
  - SummaryEntry       → run_pipeline 中 summary 列表的每一项（单模型评测结果）
  - EvalRecord         → save_eval_record 写入 eval_history.json 的一条记录
  - EvaluationRequest  → evaluate 接口的评测请求（run_pipeline 的输入参数）
  - RunResources       → evaluate 接口的运行时资源
  - EvaluationResult   → evaluate / run_pipeline 的返回值（EvalResult 为其别名）
"""
from typing import TypedDict, List, Dict, Optional, Any


class TestCase(TypedDict, total=False):
    """一条测试用例。"""
    question: str          # 必填：问题文本
    ground_truth: str      # 选填：标准答案
    contexts: List[str]    # 选填：预定义上下文（仅 JSON 支持，暂不参与检索）
    case_type: str         # positive / negative，默认 positive


class TraceStatement(TypedDict, total=False):
    """幻觉溯源中的一条原子语句。"""
    text: str              # 语句文本
    verdict: str           # supported / fact_error / fabrication
    evidence: str          # 对应的检索证据


class TraceResult(TypedDict, total=False):
    """trace_hallucinations 的返回值。"""
    statements: List[TraceStatement]
    trace_status: str  # ok / empty_answer / model_empty / parse_error 等
    trace_attempts: int
    trace_diagnostics: Dict[str, Any]


class SummaryEntry(TypedDict, total=False):
    """单模型评测结果（summary 列表的一项）。"""
    model: str
    faithfulness: Optional[float]
    context_precision: Optional[float]
    answer_relevancy: Optional[float]
    answer_correctness: Optional[float]
    context_recall: Optional[float]
    pos_faithfulness: Optional[float]
    pos_context_precision: Optional[float]
    neg_faithfulness: Optional[float]
    neg_context_precision: Optional[float]
    refusal_accuracy: Optional[float]
    hallucination_rate: Optional[float]
    score: Optional[float]
    hallucination_statement_count: Optional[int]
    hallucination_ratio: Optional[float]
    trace_statement_count: Optional[int]
    trace_fact_error_count: Optional[int]
    trace_fabrication_count: Optional[int]
    pos_trace_hallucination_ratio: Optional[float]
    elapsed_seconds: float


class EvalRecord(TypedDict, total=False):
    """写入 eval_history.json 的一条评测历史记录。"""
    timestamp: str
    version: str
    doc_name: str
    testset_source: str
    thresholds: Dict[str, float]
    gate_passed: bool
    models: List[SummaryEntry]
    total_elapsed_seconds: float


class EvaluationRequest(TypedDict, total=False):
    """evaluate 接口的评测请求（对应 run_pipeline 的输入参数）。"""
    doc_path: str
    testset_path: Optional[str]
    benchmark: Optional[str]
    benchmark_limit: int
    model_names: Optional[List[str]]
    faith_threshold: Optional[float]
    ctx_prec_threshold: Optional[float]
    version_label: Optional[str]


class RunResources(TypedDict, total=False):
    """evaluate 接口的运行时资源（嵌入、切分结果、检索器、测试集）。"""
    embeddings: Any
    chunks: Any
    retriever: Any
    testset: List[TestCase]


class RawModelEvaluation(TypedDict, total=False):
    """单模型原始评测结果（run_single_model_raw 的返回值）。

    仅承载原始评测产物，不包含 metrics 聚合 / score / ratio / summary 等派生结果，
    那些由后续 Phase 3-B-4/3-B-5 的聚合层计算。
    """
    df: Any                # Ragas result.to_pandas() 结果，已追加 hallucination_trace 与 case_type 两列
    trace_total: int       # 溯源语句总数
    trace_fact_error: int  # fact_error 判定数
    trace_fabrication: int  # fabrication 判定数
    pos_trace_total: int   # 正样本溯源语句总数
    pos_trace_halluc: int  # 正样本幻觉语句数（fact_error + fabrication）


class ModelLoopResult(TypedDict, total=False):
    """多模型循环结果（run_models 的返回值）。"""
    summary: List[SummaryEntry]     # 每个模型一行（成功或失败）
    model_dfs: Dict[str, Any]       # model name -> Ragas df（仅成功登记）


class GateResult(TypedDict, total=False):
    """质量门禁结果（check_quality_gate 的返回值）。"""
    gate_passed: bool       # 是否通过门禁
    gate_message: str       # 固定门禁消息（通过/拦截）


class EvaluationResult(TypedDict, total=False):
    """evaluate / run_pipeline 的返回值。"""
    summary: List[SummaryEntry]
    summary_df: Any
    gate_passed: bool
    gate_message: str
    chart_path: str
    html_path: str
    model_dfs: Dict[str, Any]
    total_elapsed_seconds: float


EvalResult = EvaluationResult  # 向后兼容别名（Phase 1 定义）
