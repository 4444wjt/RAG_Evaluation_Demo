"""Evaluation Engine 接口。

Phase 3-A 建立架构骨架（evaluate 占位接口）；
Phase 3-B-1 迁移 run_pipeline 的资源准备阶段为 prepare_resources；
Phase 3-B-2 迁移测试集准备阶段为 prepare_testset；
Phase 3-B-3 迁移单模型原始评测为 run_single_model_raw；
Phase 3-B-4 迁移单模型成功路径指标聚合为 aggregate_model_metrics；
Phase 3-B-5 迁移单模型完整编排 + 多模型循环为 run_models。
Phase 3-B-7-B 迁移 evaluate 总编排：按旧 run_pipeline 顺序串起
prepare_resources → prepare_testset → run_models → summary/CSV → chart → HTML → gate → history。
复用 backend.services 的叶子函数与 backend.config 的常量，编排与评分定义沿用旧流程；
中文陈述输入使用兼容层；main.py.run_pipeline 与 Web 共用本模块，历史 _legacy_run_pipeline 独立保留。

依赖方向：本模块只 import backend.config / backend.services.* / backend.adapters / 同级 types，
以及外部 ragas/datasets 依赖；禁止反向 import main.py，避免循环依赖。
"""
import json
import os
import time
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd
from datasets import Dataset
from langchain_community.embeddings import HuggingFaceEmbeddings
from ragas import evaluate as ragas_evaluate, RunConfig
from ragas.metrics import (
    context_precision, answer_relevancy, context_recall,
    ContextPrecision, AnswerRelevancy, ContextRecall,
)

from backend.adapters.ragas_compat import ChineseAnswerCorrectness, ChineseFaithfulness
from backend.adapters.models.judging import ModelCallRecorder, build_judge, judge_provenance, observe_judge

from backend.config import (
    BASE_DIR, DOC_PATH, SUMMARY_OUTPUT, HTML_OUTPUT, CHART_PATH,
    CHUNK_SIZE, EMBEDDING_MODEL, TOP_K, MODELS_CONFIG, TEST_QA_NUM, EVAL_TIMEOUT,
    FAITH_THRESHOLD, CTX_PREC_THRESHOLD, VERSION, ENABLE_TRACE,
)
from backend.core.utils import _safe_mean, _is_refusal
from backend.services.documents import load_and_split_doc
from backend.services.parse_check import check_document
from backend.services.retriever import build_vector_store
from backend.services.testsets import generate_testset, load_custom_testset, load_benchmark
from backend.services.answering import rag_answer
from backend.services.trace import trace_hallucinations, trace_unavailable

from backend.reporting import generate_html_report, generate_evaluation_chart
from backend.storage.json_history import save_eval_record

from .gate import check_quality_gate
from .types import EvaluationRequest, RunResources, EvaluationResult, RawModelEvaluation, SummaryEntry, ModelLoopResult

# 与 main.py 一致的 5 项指标及评分定义，仅 Web 使用中文陈述输入兼容实例；
# DeepSeek/阿里百炼仅支持 n=1，
# answer_relevancy 默认 strictness=3 会触发多采样而全部返回 None，故固定为 1
RAGAS_METRICS = [
    ChineseFaithfulness(), context_precision, answer_relevancy,
    ChineseAnswerCorrectness(), context_recall,
]
answer_relevancy.strictness = 1


def prepare_resources(doc_path, progress_cb=None):
    """准备 RAG 评测运行时资源（从旧 run_pipeline 步骤 1~3 原样提取）。

    依次完成：
      0. 检查源文件指纹与 PDF 文本层，FAIL 阻止入库，REVIEW 告警并保留记录
      1. 初始化嵌入模型（HuggingFaceEmbeddings，强制只读本地缓存，不联网）
      2. 加载并切分文档（load_and_split_doc）
      3. 构建向量库（build_vector_store，支持 REUSE_VECTOR_DB 复用）
      4. 创建检索器（Top-K = TOP_K）

    本函数仅覆盖「资源准备」阶段，不涉及模型循环 / Ragas / metrics / gate / report。

    参数：
        doc_path: str — 文档路径
        progress_cb: Optional[Callable[[int, str], None]] — 进度回调（step, desc）

    返回：
        RunResources — 含 embeddings / chunks / retriever 与入库检查元数据；
        testset 字段在本阶段不产生，交由后续 evaluate 编排阶段填充。
    """
    def report_step(step, desc=""):
        # 把 [1/6]~[6/6] 的进度透传给 Web 端（gr.Progress），失败不影响主流程
        if progress_cb:
            try:
                progress_cb(step, desc)
            except Exception:
                pass

    # 0. 在模型加载、切块和建库前检查；检查结论独立于评测质量门禁。
    parse_check = check_document(doc_path)
    if parse_check["verdict"] in ("FAIL", "REVIEW"):
        details = "; ".join(
            f"第 {page['page_number']} 页: {', '.join(page['reasons'])}"
            for page in parse_check["pages"]
            if page["verdict"] == parse_check["verdict"]
        )
        if parse_check["verdict"] == "FAIL":
            raise RuntimeError(f"入库检查 FAIL，已阻止文档入库: {details}")
        print(f"⚠️ 入库检查 REVIEW，需要复核；继续运行并保留检查记录: {details}")

    # 1. 初始化嵌入模型
    report_step(1, "初始化嵌入模型")
    print("\n[1/6] 初始化嵌入模型...")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"local_files_only": True}  # 强制只读本地缓存，不联网
    )

    # 2. 加载切分文档
    report_step(2, "加载并切分文档")
    print("\n[2/6] 加载并切分文档...")
    chunks = load_and_split_doc(doc_path)
    print(f"✅ 文档切分完成，共 {len(chunks)} 个块（chunk_size={CHUNK_SIZE}）")

    # 3. 构建向量库 + 检索器
    report_step(3, "构建向量库")
    print("\n[3/6] 构建向量库...")
    vector_store = build_vector_store(chunks, embeddings)
    retriever = vector_store.as_retriever(search_kwargs={"k": TOP_K})
    print(f"✅ 向量库就绪（Top-K={TOP_K}）")

    return {
        "embeddings": embeddings,
        "chunks": chunks,
        "retriever": retriever,
        "source_fingerprint": parse_check["source_fingerprint"],
        "parse_check": parse_check,
    }


def prepare_testset(request, resources, model_factory=None, progress_cb=None):
    """准备统一测试集（从旧 run_pipeline 步骤 4 原样提取）。

    三分支（优先级与旧代码一致）：
      1. benchmark：load_benchmark(benchmark, benchmark_limit)
      2. testset_path：load_custom_testset(testset_path)
      3. 自动生成：model_factory(MODELS_CONFIG[0]) 出题，失败 fallback MODELS_CONFIG[1]

    自动出题模型选择规则保持不变：DeepSeek（MODELS_CONFIG[0]）→ 通义千问（MODELS_CONFIG[1]）回退，
    不使用所选模型列表、不自动任选模型、不引入新的 fallback 策略。

    本函数仅覆盖「测试集准备」阶段，不涉及模型循环 / Ragas / metrics / gate / report。

    参数：
        request: EvaluationRequest — 评测请求（读 benchmark / testset_path / benchmark_limit）
        resources: RunResources — 运行时资源（自动生成分支读 chunks）
        model_factory: Optional[Callable[[dict], Any]] — 模型工厂，等价 build_llm / get_llm；
            为 None 时复用 backend.adapters.models.registry.build_llm
        progress_cb: Optional[Callable[[int, str], None]] — 进度回调（step, desc）

    返回：
        List[TestCase] — 统一结构的测试集（正/负样本顺序与旧代码一致）
    """
    def report_step(step, desc=""):
        # 把 [1/6]~[6/6] 的进度透传给 Web 端（gr.Progress），失败不影响主流程
        if progress_cb:
            try:
                progress_cb(step, desc)
            except Exception:
                pass

    benchmark = request.get("benchmark")
    testset_path = request.get("testset_path")
    benchmark_limit = request.get("benchmark_limit", 20)
    chunks = resources.get("chunks")

    if benchmark:
        report_step(4, "加载基准数据集")
        print(f"\n[4/6] 加载基准数据集 {benchmark}（limit={benchmark_limit}）...")
        testset = load_benchmark(benchmark, benchmark_limit)
        print(f"✅ 已加载 {len(testset)} 道基准测试题")
    elif testset_path:
        report_step(4, "加载自定义测试集")
        print("\n[4/6] 加载自定义测试集...")
        testset = load_custom_testset(testset_path)
        print(f"✅ 已加载 {len(testset)} 道自定义测试题（正样本 {sum(1 for t in testset if t['case_type'] == 'positive')} / 负样本 {sum(1 for t in testset if t['case_type'] == 'negative')}）")
    else:
        report_step(4, "自动生成测试集")
        print("\n[4/6] 自动生成测试集...")
        if model_factory is None:
            from backend.adapters.models.registry import build_llm
            model_factory = build_llm
        try:
            llm_for_testset = model_factory(MODELS_CONFIG[0])
            testset = generate_testset(chunks, llm_for_testset, TEST_QA_NUM)
        except Exception as e:
            print(f"⚠️ DeepSeek 生成测试集失败，切换通义千问: {str(e)[:60]}")
            llm_for_testset = model_factory(MODELS_CONFIG[1])
            testset = generate_testset(chunks, llm_for_testset, TEST_QA_NUM)
        print(f"✅ 生成 {len(testset)} 道测试题（含3道负样本）")

    return testset


def run_single_model_raw(testset, retriever, embeddings, llm, trace_enabled, *, judge_llm=None):
    """单模型原始评测（从旧 run_pipeline 步骤 5 的单模型体 L1243~1299 原样提取）。

    仅完成单个模型的原始评测执行：RAG 问答 → Ragas evaluate → 幻觉溯源，
    返回 RawModelEvaluation。不涉及多模型循环 / metrics 聚合 / score / ratio /
    gate / report / history / summary 等派生逻辑，也不捕获外围异常。

    参数：
        testset: List[TestCase] — 统一测试集（按原顺序执行）
        retriever: Any — 检索器
        embeddings: Any — 嵌入模型（传给 Ragas）
        llm: Any — 当前被评测模型；只负责生成回答
        trace_enabled: bool — 是否执行幻觉溯源（等价旧 ENABLE_TRACE）
        judge_llm: Any — 可选独立裁判；未指定时保持使用被测模型

    返回：
        RawModelEvaluation — df / trace_total / trace_fact_error /
        trace_fabrication / pos_trace_total / pos_trace_halluc
    """
    # 执行 RAG 问答
    eval_data = {"question": [], "answer": [], "contexts": [], "ground_truth": []}
    for idx, item in enumerate(testset):
        answer, contexts = rag_answer(item["question"], retriever, llm)
        eval_data["question"].append(item["question"])
        eval_data["answer"].append(answer)
        eval_data["contexts"].append(contexts)
        eval_data["ground_truth"].append(item["ground_truth"])
        if (idx + 1) % 2 == 0:
            print(f"  进度: {idx + 1}/{len(testset)}")

    return score_answer_rows(testset, eval_data, embeddings, llm if judge_llm is None else judge_llm,
                             trace_enabled, fresh_metrics=judge_llm is not None)


def score_answer_rows(testset, eval_data, embeddings, judge_llm, trace_enabled, *, fresh_metrics=True):
    """Evaluate already-produced answers and actual contexts; never retrieve or generate here.

    Both the built-in RAG and external applications use these identical five metrics,
    Chinese adapters and Trace processing. Configured/external judges use fresh metrics;
    the unspecified self-judge path preserves the original RAGAS_METRICS instances.
    """
    recorder = ModelCallRecorder()
    judge_llm = observe_judge(judge_llm)
    with recorder.capture(judge_llm):
        raw = _score_answer_rows(testset, eval_data, embeddings, judge_llm, trace_enabled, fresh_metrics=fresh_metrics)
    raw["judge_recorder"] = recorder
    return raw


def _score_answer_rows(testset, eval_data, embeddings, llm, trace_enabled, *, fresh_metrics):
    # Same definitions, defaults and 0.75/0.25 Answer Correctness weights as before.
    dataset = Dataset.from_dict(eval_data)
    result = ragas_evaluate(
        dataset=dataset,
        metrics=([ChineseFaithfulness(), ContextPrecision(), AnswerRelevancy(strictness=1),
                  ChineseAnswerCorrectness(), ContextRecall()] if fresh_metrics else RAGAS_METRICS),
        llm=llm,
        embeddings=embeddings,
        run_config=RunConfig(max_workers=1, timeout=EVAL_TIMEOUT)
    )
    df = result.to_pandas()

    # 幻觉溯源与归因（每题单次 LLM 调用，批量输出 JSON）
    trace_list = []
    trace_total = 0
    trace_fact_error = 0
    trace_fabrication = 0
    pos_trace_total = 0
    pos_trace_halluc = 0
    for i, item in enumerate(testset):
        ctxs = eval_data["contexts"][i]
        ans = eval_data["answer"][i]
        trace = trace_hallucinations(
            eval_data["question"][i], ans, ctxs, eval_data["ground_truth"][i], llm
        ) if trace_enabled else trace_unavailable()
        trace_list.append(json.dumps(trace, ensure_ascii=False))
        stmts = trace.get("statements", [])
        for s in stmts:
            trace_total += 1
            if s["verdict"] == "fact_error":
                trace_fact_error += 1
                if item.get("case_type", "positive") == "positive":
                    pos_trace_halluc += 1
            elif s["verdict"] == "fabrication":
                trace_fabrication += 1
                if item.get("case_type", "positive") == "positive":
                    pos_trace_halluc += 1
            if item.get("case_type", "positive") == "positive":
                pos_trace_total += 1
        if (i + 1) % 5 == 0:
            print(f"  溯源进度: {i + 1}/{len(testset)}")

    df["hallucination_trace"] = trace_list
    df["case_type"] = [item.get("case_type", "positive") for item in testset]

    return {
        "df": df,
        "trace_total": trace_total,
        "trace_fact_error": trace_fact_error,
        "trace_fabrication": trace_fabrication,
        "pos_trace_total": pos_trace_total,
        "pos_trace_halluc": pos_trace_halluc,
    }


def aggregate_model_metrics(raw_result, model_name, elapsed_seconds):
    """单模型成功路径的指标聚合（从旧 run_pipeline L1296~1350 原样提取）。

    消费 Phase 3-B-3 的 RawModelEvaluation，产出单个模型成功时的 summary entry。
    纯聚合函数：不打印、不写盘、不捕获异常、不涉及多模型循环 / gate / reporting / history。

    参数：
        raw_result: RawModelEvaluation — df 与 5 个 trace 计数
        model_name: str — 模型名
        elapsed_seconds: float — 单模型耗时（已 round(..., 2)，由调用方传入）

    返回：
        SummaryEntry — 成功 summary 行，字段与顺序与旧 main.py 完全一致
    """
    df = raw_result["df"]
    trace_total = raw_result["trace_total"]
    trace_fact_error = raw_result["trace_fact_error"]
    trace_fabrication = raw_result["trace_fabrication"]
    pos_trace_total = raw_result["pos_trace_total"]
    pos_trace_halluc = raw_result["pos_trace_halluc"]

    # 溯源比例（trace_total / pos_trace_total 为 0 时为 None，不落成 0）
    trace_ratio = round((trace_fact_error + trace_fabrication) / trace_total, 4) if trace_total else None
    pos_trace_ratio = round(pos_trace_halluc / pos_trace_total, 4) if pos_trace_total else None

    # 分层统计（负样本为对抗测试；门禁仅依赖正向忠实度与上下文精度）
    pos_df = df[df["case_type"] == "positive"]
    neg_df = df[df["case_type"] == "negative"]

    mean_faith = _safe_mean(df.get("faithfulness"))
    mean_ctx = _safe_mean(df.get("context_precision"))
    mean_rel = _safe_mean(df.get("answer_relevancy"))
    mean_corr = _safe_mean(df.get("answer_correctness"))
    mean_recall = _safe_mean(df.get("context_recall"))

    pos_faith = _safe_mean(pos_df.get("faithfulness")) if len(pos_df) else None
    pos_ctx = _safe_mean(pos_df.get("context_precision")) if len(pos_df) else None
    neg_faith = _safe_mean(neg_df.get("faithfulness")) if len(neg_df) else None
    neg_ctx = _safe_mean(neg_df.get("context_precision")) if len(neg_df) else None

    # 拒答准确率：负样本中正确拒答（而非编造）的比例
    neg_answers = neg_df["answer"].tolist() if len(neg_df) else []
    refusal_hits = sum(1 for a in neg_answers if _is_refusal(a))
    refusal_accuracy = round(refusal_hits / len(neg_answers), 4) if neg_answers else None

    hallucination = round(1 - pos_faith, 4) if pos_faith is not None else None
    score = round((pos_faith + pos_ctx) / 2, 4) if (pos_faith is not None and pos_ctx is not None) else None

    return {
        "model": model_name,
        "faithfulness": mean_faith,
        "context_precision": mean_ctx,
        "answer_relevancy": mean_rel,
        "answer_correctness": mean_corr,
        "context_recall": mean_recall,
        "pos_faithfulness": pos_faith,
        "pos_context_precision": pos_ctx,
        "neg_faithfulness": neg_faith,
        "neg_context_precision": neg_ctx,
        "refusal_accuracy": refusal_accuracy,
        "hallucination_rate": hallucination,
        "score": score,
        "hallucination_statement_count": trace_fact_error + trace_fabrication,
        "hallucination_ratio": trace_ratio,
        "trace_statement_count": trace_total,
        "trace_fact_error_count": trace_fact_error,
        "trace_fabrication_count": trace_fabrication,
        "pos_trace_hallucination_ratio": pos_trace_ratio,
        "elapsed_seconds": elapsed_seconds,
    }


def select_model_configs(model_names=None):
    """等价 main.py L1168 的模型筛选：按 MODELS_CONFIG 顺序过滤。

    model_names=None → 全部内置模型；逆序输入 → 仍按 MODELS_CONFIG 顺序；重复 → 不重复；
    未知名 → 忽略；空列表 → 结果为空。不做 sort / set / 去重 / fallback。
    本机登记的自定义模型（models.local.json）只在按名称点选时加入，排在内置模型之后。
    """
    if model_names is None:
        return list(MODELS_CONFIG)
    from backend.core import model_registry
    local = model_registry.load(BASE_DIR, [c["name"] for c in MODELS_CONFIG])
    return [c for c in MODELS_CONFIG + local if c["name"] in model_names]


def run_models(model_cfgs, resources, model_factory, *, base_dir, trace_enabled, judge_cfg=None):
    """多模型循环（从旧 run_pipeline 步骤 5 的模型循环体 L1234~1377 原样提取）。

    按 model_cfgs 顺序逐模型执行：创建 llm → run_single_model_raw → model_dfs 登记 →
    CSV → aggregate_model_metrics → 成功/失败 summary 行。不涉及 gate / chart / HTML /
    history / total elapsed / evaluate 总编排。

    参数：
        model_cfgs: List[dict] — 已筛选的模型配置列表（顺序即执行顺序）
        resources: RunResources — 运行时资源（testset / retriever / embeddings）
        model_factory: Callable[[dict], Any] — 模型工厂，等价 build_llm / get_llm
        base_dir: str — CSV 输出根目录
        trace_enabled: bool — 是否执行幻觉溯源（等价旧 ENABLE_TRACE）

    返回：
        ModelLoopResult — summary / model_dfs
    """
    summary = []
    model_dfs = {}
    # Resolve/build before generation. A bad judge never silently falls back to self-judging.
    judge_llm = build_judge(judge_cfg, model_factory) if judge_cfg is not None else None

    for model_cfg in model_cfgs:
        print(f"\n{'=' * 50}")
        print(f"🎯 开始评测模型: {model_cfg['name']}")
        print('=' * 50)

        t_model = time.time()
        try:
            llm = model_factory(model_cfg)

            # 单模型原始评测（复用 Phase 3-B-3，不复制其内部逻辑）
            judge_args = {"judge_llm": judge_llm} if judge_cfg is not None else {}
            raw_result = run_single_model_raw(
                resources["testset"],
                resources["retriever"],
                resources["embeddings"],
                llm,
                trace_enabled,
                **judge_args,
            )
            df = raw_result["df"]
            provenance = judge_provenance(
                judge_cfg if judge_cfg is not None else model_cfg,
                raw_result.get("judge_recorder") or ModelCallRecorder(),
                configured=judge_cfg is not None,
            )
            # Keep the eight-field public envelope; provenance travels with each new row/summary.
            for key in ("judge_model", "judge_mode", "judge_response_scope"):
                df[key] = provenance[key]
            df["judge_response_models"] = [list(provenance["judge_response_models"]) for _ in range(len(df))]

            # 立即登记 model_dfs（必须先于 CSV 写入）
            model_dfs[model_cfg["name"]] = df

            # 逐题 CSV 写入（模型名中的 "/" 替换为 "_"，仍处于 try 内）
            safe_name = model_cfg["name"].replace("/", "_")  # 把斜杠替换成下划线
            csv_path = os.path.join(base_dir, f"ragas_report_{safe_name}.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")

            # 指标聚合（复用 Phase 3-B-4，不重新实现公式）；elapsed 在聚合后采样
            entry = aggregate_model_metrics(raw_result, model_cfg["name"], 0.0)
            entry.update(provenance)
            entry["elapsed_seconds"] = round(time.time() - t_model, 2)
            summary.append(entry)

            # 成功日志（位于 try 内、summary append 之后；若 print 抛异常会进入 except，
            # 导致同一模型同时出现成功行 + 失败行，这是旧行为的异常边缘，必须保留）
            print(f"✅ {model_cfg['name']} 评测完成 | 忠实度: {entry['faithfulness']} | 上下文精度: {entry['context_precision']} | 答案相关性: {entry['answer_relevancy']} | 事实一致性: {entry['answer_correctness']} | 回答完整性: {entry['context_recall']} | 拒答准确率: {entry['refusal_accuracy']}")

        except Exception as e:
            # 失败日志先行，然后才构造失败 summary（失败耗时含失败日志自身耗时）
            print(f"❌ 模型 {model_cfg['name']} 评测失败: {str(e)[:100]}")
            summary.append({
                "model": model_cfg["name"],
                "faithfulness": None,
                "context_precision": None,
                "answer_relevancy": None,
                "answer_correctness": None,
                "context_recall": None,
                "pos_faithfulness": None,
                "pos_context_precision": None,
                "neg_faithfulness": None,
                "neg_context_precision": None,
                "refusal_accuracy": None,
                "hallucination_rate": None,
                "score": None,
                "hallucination_statement_count": None,
                "hallucination_ratio": None,
                "trace_statement_count": None,
                "trace_fact_error_count": None,
                "trace_fabrication_count": None,
                "pos_trace_hallucination_ratio": None,
                "elapsed_seconds": round(time.time() - t_model, 2),
            })

    # 全部失败边界处理：只检查 faithfulness，0.0 / float("nan") 均算有效
    valid_summary = [s for s in summary if s["faithfulness"] is not None]
    if not valid_summary:
        raise RuntimeError("所有模型评测全部失败")

    return {
        "summary": summary,
        "model_dfs": model_dfs,
    }


def evaluate(request, resources=None, model_factory=None, progress_cb=None):
    """执行一次完整的多模型 RAG 评测（Phase 3-B-7-B：总编排）。

    按旧 run_pipeline 顺序编排已迁移的叶子函数：
      prepare_resources → prepare_testset → run_models → summary/CSV → chart → HTML
      → gate → history → EvaluationResult。

    参数：
        request: EvaluationRequest — 评测请求（文档 / 测试集 / 基准 / 模型 / 阈值 / 版本标签）
        resources: Optional[RunResources] — 运行时资源（嵌入 / 切分结果 / 检索器 / 测试集）；
            None 时执行 prepare_resources + prepare_testset；非 None 直接使用，不重复准备、不自动补齐缺失字段
        model_factory: Optional[Callable[[dict], Any]] — 模型工厂；None 使用 build_llm，非 None 使用传入工厂
        progress_cb: Optional[Callable[[int, str], None]] — 进度回调（step, desc）

    返回：
        EvaluationResult — 与 run_pipeline 返回结构完全一致（8 字段）

    异常边界：资源/测试集/CSV/chart/HTML 失败直接抛；模型失败由 run_models 处理；
    全部失败 RuntimeError 保持；history 失败保持旧 warning 行为（save_eval_record 内部吞掉）。
    """
    # 1. 解析 request 默认值（缺失键 → None，不改变旧语义）
    doc_path = request.get("doc_path") or DOC_PATH
    benchmark = request.get("benchmark")
    testset_path = request.get("testset_path")
    model_names = request.get("model_names")
    version_label = request.get("version_label")

    # 2. 阈值默认值：None → 默认常量；0.0 保留（不做 falsy 归一化）
    faith_threshold = request.get("faith_threshold")
    ctx_prec_threshold = request.get("ctx_prec_threshold")
    faith_threshold = FAITH_THRESHOLD if faith_threshold is None else faith_threshold
    ctx_prec_threshold = CTX_PREC_THRESHOLD if ctx_prec_threshold is None else ctx_prec_threshold

    # 3. 模型工厂：None → build_llm（懒加载，避免 import 期依赖）
    if model_factory is None:
        from backend.adapters.models.registry import build_llm
        model_factory = build_llm

    # 4. 模型配置（保持 MODELS_CONFIG 顺序，复用 select_model_configs）
    model_cfgs = select_model_configs(model_names)
    judge_name = request.get("judge_model_name")
    judge_cfg = None
    if judge_name is not None:
        judges = select_model_configs([judge_name])
        if len(judges) != 1:
            raise ValueError("裁判模型不支持，请先在本机登记模型。")
        judge_cfg = judges[0]

    # 5. 开始计时
    t_start = time.time()

    def report_step(step, desc=""):
        # 把 [1/6]~[6/6] 的进度透传给 Web 端（gr.Progress），失败不影响主流程
        if progress_cb:
            try:
                progress_cb(step, desc)
            except Exception:
                pass

    print("=" * 50)
    print(f"🚀 鉴真全域 VeriScope AI {VERSION} 启动")
    print("=" * 50)
    print("[健壮性] 已内置空值过滤、异常重试、超时保护、JSON格式容错")

    # 6. 资源准备 + 测试集准备（resources 非 None 直接使用，不重复准备、不自动补齐）
    if resources is None:
        resources = prepare_resources(doc_path, progress_cb)
        testset = prepare_testset(request, resources, model_factory, progress_cb)
        resources["testset"] = testset

    # 7. 多模型横向评测（步骤 5 进度：多模型横向评测）
    report_step(5, "多模型横向评测")
    print("\n[5/6] 开始多模型横向评测...")
    loop_result = run_models(
        model_cfgs,
        resources,
        model_factory,
        base_dir=BASE_DIR,
        trace_enabled=ENABLE_TRACE,
        **({"judge_cfg": judge_cfg} if judge_cfg is not None else {}),
    )
    summary = loop_result["summary"]
    model_dfs = loop_result["model_dfs"]

    # 8. 多模型对比汇总 + CSV（步骤 6 进度：汇总对比与报告）
    report_step(6, "汇总对比与报告")
    print("\n" + "=" * 50)
    print("📊 多模型横向对比总结")
    print("=" * 50)
    summary_df = pd.DataFrame(summary)
    print(summary_df.to_string(index=False))
    summary_df.to_csv(SUMMARY_OUTPUT, index=False, encoding="utf-8-sig")
    print(f"\n✅ 汇总报告已保存: {SUMMARY_OUTPUT}")

    # 9. 真实评测可视化图表（禁止 draw_summary_chart）
    print("\n[6/6] 生成可视化图表...")
    generate_evaluation_chart(summary, faith_threshold, CHART_PATH)

    # 10. 总耗时采样（必须在 chart 之后、HTML 之前）
    total_elapsed = round(time.time() - t_start, 2)

    # 11. HTML 报告
    generate_html_report(
        summary,
        chart_path=CHART_PATH,
        output_path=HTML_OUTPUT,
        faith_threshold=faith_threshold,
        ctx_prec_threshold=ctx_prec_threshold,
        total_elapsed=total_elapsed,
        case_counts={
            "total": len(resources["testset"]),
            "positive": sum(item.get("case_type", "positive") == "positive" for item in resources["testset"]),
            "negative": sum(item.get("case_type", "positive") == "negative" for item in resources["testset"]),
            "other": sum(item.get("case_type", "positive") not in ("positive", "negative") for item in resources["testset"]),
        },
        model_dfs=model_dfs,
    )

    # 12. 质量门禁
    gate_result = check_quality_gate(summary, model_dfs, faith_threshold, ctx_prec_threshold)
    gate_passed = gate_result["gate_passed"]
    gate_message = gate_result["gate_message"]

    # 13. 历史记录（通过/拦截都保存；失败由 save_eval_record 内部吞掉并打印 warning）
    record = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": version_label or datetime.now().strftime("%Y%m%d-%H%M"),
        "doc_name": os.path.basename(doc_path),
        # 外部预置资源可能未检查：保留缺失值，不读取现文件冒充入库时的状态。
        "source_fingerprint": resources.get("source_fingerprint"),
        "parse_check": resources.get("parse_check"),
        "testset_source": benchmark if benchmark else (os.path.basename(testset_path) if testset_path else "自动生成"),
        "thresholds": {"faithfulness": faith_threshold, "context_precision": ctx_prec_threshold},
        "gate_passed": gate_passed,
        "models": summary,
        "total_elapsed_seconds": total_elapsed,
    }
    save_eval_record(record)

    # 14. 返回（与旧 run_pipeline 结构完全一致）
    return {
        "summary": summary,
        "summary_df": summary_df,
        "gate_passed": gate_passed,
        "gate_message": gate_message,
        "chart_path": CHART_PATH,
        "html_path": HTML_OUTPUT,
        "model_dfs": model_dfs,
        "total_elapsed_seconds": total_elapsed,
    }
