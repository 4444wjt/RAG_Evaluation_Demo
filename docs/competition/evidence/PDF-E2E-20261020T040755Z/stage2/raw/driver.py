"""Stage-2 preflight. No model calls when either mandatory guard blocks execution.

Usage: venv/Scripts/python.exe work/pdf-e2e-stage2-20261020/driver.py --repo . --source <sample-A.pdf>
Only the six requested configuration paths are changed, before dependent imports.
Budget placeholders below are exclusively planning data, never generated test cases.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
EXPECTED = "fa8f8923bde06cfb3c682e004ebd12fd14ce53cff72a9365e1fff740e92f1a4f"
PATH_NAMES = ("CHROMA_DIR", "SUMMARY_OUTPUT", "HTML_OUTPUT", "CHART_PATH", "HISTORY_PATH", "TREND_CHART_PATH")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def file_state(path):
    info = path.stat()
    return {"size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def path_state(path):
    if not path.exists():
        return {"kind": "missing"}
    if path.is_file():
        return {"kind": "file", **file_state(path)}
    return {"kind": "directory", "mtime_ns": path.stat().st_mtime_ns,
            "directories": sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_dir()),
            "files": {p.relative_to(path).as_posix(): file_state(p) for p in sorted(path.rglob("*")) if p.is_file()}}


def git_status(repo):
    return subprocess.check_output(["git", "status", "--short"], cwd=repo).decode("utf-8")


def literal_assignment(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Missing expected literal assignment: " + name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    repo, source = Path(args.repo).resolve(), Path(args.source).resolve()
    sys.path.insert(0, str(repo))
    work = repo / "work/pdf-e2e-stage2-20261020"
    out = work / "out"
    out.mkdir(parents=True, exist_ok=False)
    status_before = git_status(repo)
    (out / "git-status-before.txt").write_text(status_before, encoding="utf-8", newline="\n")
    fingerprint = {"sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "size_bytes": source.stat().st_size, "extension": source.suffix}
    if fingerprint["sha256"] != EXPECTED:
        write_json(out / "source-mismatch.json", {"expected": EXPECTED, "actual": fingerprint, "executed": False})
        print("Source SHA-256 mismatch; no imports or model calls. Exit 10.")
        return 10

    # Read configuration before importing any module that captures path defaults.
    from backend import config
    original_paths = {name: Path(getattr(config, name)) for name in PATH_NAMES}
    before = {name: {"path": path.relative_to(repo).as_posix(), **path_state(path)} for name, path in original_paths.items()}
    write_json(out / "root-paths-before.json", before)
    evidence_root = repo / "docs/competition/evidence"
    guarded = [p for p in evidence_root.rglob("*") if p.is_file()]
    guarded += [p for p in (repo / "backend").rglob("*.py")]
    guarded += [repo / "main.py", repo / "requirements.txt", repo / "tests/requirements.txt", repo / "frontend/src/fixtures/demo.json",
                repo / "frontend/src/features/landing/LandingPage.tsx", repo / "frontend/src/features/landing/landing.css",
                repo / "frontend/src/features/landing/_backup_LandingPage.tsx.bak-20261018",
                repo / "frontend/src/features/landing/_backup_landing.css.bak-20261018"]
    guarded = sorted(set(p for p in guarded if p.is_file()))
    guarded_before = {p.relative_to(repo).as_posix(): file_state(p) for p in guarded}
    write_json(out / "protected-files-before.json", guarded_before)
    for name, old in original_paths.items():
        setattr(config, name, str(out / old.name))

    from backend.core import pipeline
    from backend.services import retriever
    from backend.reporting import html, charts
    from backend.storage import json_history
    from backend.services.parse_check import check_document
    from backend.services.documents import load_and_split_doc
    from backend.services.negatives import _negative_context
    from backend.services.trace import TRACE_PROMPT_TEMPLATE
    from backend.adapters.ragas_compat import ChineseFaithfulness, ChineseAnswerCorrectness
    from ragas.metrics import ContextPrecision, AnswerRelevancy, ContextRecall
    import tiktoken

    parse_check = check_document(source)
    assert parse_check["source_fingerprint"] == fingerprint
    write_json(out / "parse-check.json", parse_check)
    chunks = load_and_split_doc(str(source))
    selected = config.MODELS_CONFIG[0]["name"]
    selected_configs = pipeline.select_model_configs([selected])
    assert [m["name"] for m in selected_configs] == [selected]
    checks = {"retriever.CHROMA_DIR": retriever.CHROMA_DIR == config.CHROMA_DIR}
    for name in ("SUMMARY_OUTPUT", "HTML_OUTPUT", "CHART_PATH"):
        checks["pipeline." + name] = getattr(pipeline, name) == getattr(config, name)
    for name in ("CHART_PATH", "HTML_OUTPUT"):
        checks["html." + name] = getattr(html, name) == getattr(config, name)
    for fn in (json_history.load_history, json_history.save_eval_record, json_history.history_to_dataframe):
        checks[fn.__name__ + ".path"] = inspect.signature(fn).parameters["path"].default == config.HISTORY_PATH
    checks["generate_trend_chart.path"] = inspect.signature(charts.generate_trend_chart).parameters["path"].default == config.TREND_CHART_PATH
    for parameter, name in (("chart_path", "CHART_PATH"), ("output_path", "HTML_OUTPUT")):
        checks["generate_html_report." + parameter] = inspect.signature(html.generate_html_report).parameters[parameter].default == getattr(config, name)
    assert all(checks.values())
    extra_csv = Path(pipeline.BASE_DIR) / ("ragas_report_" + selected.replace("/", "_") + ".csv")
    extra_csv_before = path_state(extra_csv)
    isolation = {"six_paths": {name: Path(getattr(config, name)).relative_to(repo).as_posix() for name in PATH_NAMES},
                 "import_and_default_parameter_checks": checks, "six_path_checks_passed": all(checks.values()),
                 "all_output_isolated": False, "additional_csv_path": extra_csv.relative_to(repo).as_posix(),
                 "additional_csv_before": extra_csv_before,
                 "blocker": "evaluate passes BASE_DIR to run_models; per-model CSV is not controlled by the six allowed path constants.",
                 "references": ["backend/core/pipeline.py:446", "backend/core/pipeline.py:447", "backend/core/pipeline.py:573"],
                 "BASE_DIR_changed": False, "functions_monkey_patched": False}
    write_json(out / "isolation-check.json", isolation)
    refs = subprocess.check_output(["rg", "-n", "|".join(PATH_NAMES), "backend"], cwd=repo).decode("utf-8")
    (out / "path-references.txt").write_text(refs, encoding="utf-8", newline="\n")

    encoder = tiktoken.get_encoding("cl100k_base")
    def tokens(text):
        return len(encoder.encode(text))
    # Conservative normal-path planning: four actual longest chunks, no retrieval/model execution.
    contexts = [c.page_content for c in sorted(chunks, key=lambda c: len(c.page_content), reverse=True)[:config.TOP_K]]
    corpus = "".join(c.page_content for c in chunks)
    faith, correctness = ChineseFaithfulness(), ChineseAnswerCorrectness()
    precision, relevancy, recall = ContextPrecision(), AnswerRelevancy(strictness=1), ContextRecall()
    call_plan = []
    def add(stage, label, prompt, output_budget):
        prompt = prompt.to_string() if hasattr(prompt, "to_string") else prompt
        call_plan.append({"stage": stage, "call": label, "input_tokens_proxy": tokens(prompt),
                          "output_tokens_budget": output_budget, "server_usage": None})
    testset_source = (repo / "backend/services/testsets.py").read_text(encoding="utf-8-sig")
    negative_source = (repo / "backend/services/negatives.py").read_text(encoding="utf-8-sig")
    positive_template = literal_assignment(testset_source, "prompt_template")
    for index, chunk in enumerate([c for c in chunks if len(c.page_content) >= 50][:config.TEST_QA_NUM]):
        add("testset", f"positive_generation_{index + 1}", positive_template.format(chunk=chunk.page_content), 160)
    generation_rules = literal_assignment(negative_source, "generation_rules")
    review_rules = literal_assignment(negative_source, "review_rules")
    negative_context, _ = _negative_context(chunks, 6000, max_chunks=12)
    positive_questions = [corpus[:40]] * config.TEST_QA_NUM
    # These are source-text slices for a budget model, not claims of real questions/answers.
    add("testset", "negative_generation", generation_rules + json.dumps({"count": 6, "document_chunks": negative_context,
        "positive_questions": positive_questions}, ensure_ascii=False), 2200)
    candidates = [{"id": i, "question": corpus[:80], "strategy": "missing_detail", "source_id": i} for i in range(6)]
    review_context, full = _negative_context(chunks, 12000, questions=[c["question"] for c in candidates], preferred=set(range(6)))
    add("testset", "negative_review", review_rules + json.dumps({"document_chunks": review_context, "full_document": full,
        "candidates": candidates}, ensure_ascii=False), 1600)
    answer_template = literal_assignment((repo / "backend/services/answering.py").read_text(encoding="utf-8-sig").replace('f"""', '"""'), "prompt")
    answer_template = answer_template.replace("{chr(10).join(contexts)}", "{contexts_text}")
    for kind, q, a, gt in (("positive", corpus[:40], corpus[:120], corpus[:60]),
                            ("negative", corpus[:80], "根据提供的文档，无法回答该问题。", "文档未提及")):
        row = {"question": q, "answer": a, "ground_truth": gt, "contexts": contexts}
        statements = [a[i:i + 50] for i in range(0, len(a), 50)]
        ground_statements = [gt[i:i + 50] for i in range(0, len(gt), 50)]
        for case in range(3):
            label = f"{kind}_{case + 1}"
            add("answer", label, answer_template.format(question=q, contexts_text="\n".join(contexts)), 180)
            add("ragas", label + ".faith_statements", faith._create_statements_prompt(row), 200)
            add("ragas", label + ".faith_nli", faith.nli_statements_message.format(context="\n".join(contexts), statements=json.dumps(statements)), 350)
            for index, prompt in enumerate(precision._context_precision_prompt(row)):
                add("ragas", label + f".precision_{index + 1}", prompt, 100)
            add("ragas", label + ".relevancy", relevancy._create_question_gen_prompt(row), 80)
            add("ragas", label + ".correctness_answer", correctness._create_statements_prompt(q, a), 200)
            add("ragas", label + ".correctness_reference", correctness._create_statements_prompt(q, gt), 200)
            add("ragas", label + ".correctness_compare", correctness.correctness_prompt.format(question=q, answer=statements, ground_truth=ground_statements), 350)
            add("ragas", label + ".recall", recall._create_context_recall_prompt(row), 300)
            if config.ENABLE_TRACE:
                add("trace", label, TRACE_PROMPT_TEMPLATE.format(question=q, answer=a, ground_truth=gt, contexts_text="\n".join(contexts)), 400)
    input_total = sum(c["input_tokens_proxy"] for c in call_plan)
    output_total = sum(c["output_tokens_budget"] for c in call_plan)
    estimate = {"tokenizer_proxy": "cl100k_base (not the provider tokenizer)", "planned_call_count": len(call_plan),
                "planned_questions": {"positive": 3, "negative": 3, "total": 6},
                "chunk_count": len(chunks), "context_characters_per_case": sum(map(len, contexts)),
                "context_chunk_characters": list(map(len, contexts)), "input_tokens_proxy": input_total,
                "output_tokens_budget": output_total, "total_tokens_estimate": input_total + output_total,
                "limit": 100000, "exceeds_limit": input_total + output_total > 100000,
                "assumptions": ["Four longest actual chunks for conservative Top-K planning; no real retrieval performed.",
                    "Positive question/answer/reference: 40/120/60 characters sliced from current source for planning only.",
                    "Negative question: 80 source characters; answer: normal refusal; reference: document does not mention it.",
                    "Ragas uses its actual local prompt formatting, including JSON escaping of Chinese strings.",
                    "Normal successful path only; excludes API retries, Ragas retries and JSON repair.",
                    "Output budgets are planning allowances, not max_tokens changes or hard upper bounds.",
                    "Some normal branches may skip negative review or correctness comparison; 83 calls is not a guarantee."],
                "by_stage": {stage: {"calls": sum(c["stage"] == stage for c in call_plan),
                    "input_tokens_proxy": sum(c["input_tokens_proxy"] for c in call_plan if c["stage"] == stage),
                    "output_tokens_budget": sum(c["output_tokens_budget"] for c in call_plan if c["stage"] == stage)}
                    for stage in ("testset", "answer", "ragas", "trace")}, "calls": call_plan}
    write_json(out / "token-estimate.json", estimate)
    # The mandatory cost guard decides before embeddings, vector DB or any model factory.
    assert estimate["exceeds_limit"], "Budget changed: review preflight; do not silently run around the isolation blocker."
    after = {name: {"path": path.relative_to(repo).as_posix(), **path_state(path)} for name, path in original_paths.items()}
    write_json(out / "root-paths-after.json", after)
    guarded_after = {p.relative_to(repo).as_posix(): file_state(p) for p in guarded}
    write_json(out / "protected-files-after.json", guarded_after)
    comparison = {"root_paths_unchanged": before == after,
                  "per_path": {name: before[name] == after[name] for name in PATH_NAMES},
                  "additional_csv_unchanged": extra_csv_before == path_state(extra_csv),
                  "protected_files_count": len(guarded), "protected_files_unchanged": guarded_before == guarded_after,
                  "source_sha256_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == EXPECTED}
    write_json(out / "path-comparison.json", comparison)
    assert all((comparison["root_paths_unchanged"], comparison["additional_csv_unchanged"], comparison["protected_files_unchanged"], comparison["source_sha256_unchanged"]))
    result = {"status": "NOT_EXECUTED", "executed": False, "preflight_exit_code": 20, "evaluation_exit_code": None,
              "run_utc": datetime.now(timezone.utc).isoformat(),
              "reasons": ["TOKEN_ESTIMATE_EXCEEDS_100000", "PER_MODEL_CSV_OUTSIDE_SIX_PATH_ISOLATION"],
              "answer_model": {"configured": selected, "response_model": None},
              "judge_model": {"configured": selected, "mode": "self", "response_model": None},
              "model_selection": "Only the first configured answer model; no model configuration or generation parameter changed.",
              "testset": {"planned_positive": 3, "planned_negative": 3, "planned_total": 6, "generated_total": None, "evaluated_total": 0},
              "token_usage": {"calls": [], "observed_model_api_calls": 0, "prompt_tokens": None,
                  "completion_tokens": None, "total_tokens": None,
                  "reason": "No model API call was made; no server usage exists. Planning proxies are not actual usage."},
              "estimated_tokens": estimate["total_tokens_estimate"],
              "ragas_metrics": {name: None for name in ("faithfulness", "context_precision", "answer_relevancy", "answer_correctness", "context_recall")},
              "gate": {"verdict": "NOT_RUN", "gate_passed": None, "reason": "Evaluation did not run; no gate decision was produced.",
                       "thresholds": {"faithfulness": config.FAITH_THRESHOLD, "context_precision": config.CTX_PREC_THRESHOLD}},
              "parse_check": parse_check, "source_fingerprint": fingerprint,
              "output_isolation": comparison,
              "limitations": ["No embeddings, vector ingestion, retrieval, generation, Ragas, Trace or quality gate executed.",
                  "Budget is a conservative local proxy based on planning lengths, not exact DeepSeek billing.",
                  "Sample B was not processed. No pypdf patches, source changes, dependency changes, commit or push."]}
    write_json(out / "result.json", result)
    (out / "git-status-after-preflight.txt").write_text(git_status(repo), encoding="utf-8", newline="\n")
    print(f"Source SHA-256 matched; parse_check={parse_check['verdict']}; chunks={len(chunks)}.")
    print(f"Planned model={selected}; judge=self; planned questions=6; normal-path calls={len(call_plan)}.")
    print(f"Estimated input={input_total}; output allowance={output_total}; total={input_total + output_total}; limit=100000.")
    print("STOP: mandatory cost redline exceeded; independent per-model CSV isolation conflict recorded.")
    print("No evaluate/model API/embedding/vector DB invocation. Provider usage=null; gate=NOT_RUN.")
    print(f"Six root paths unchanged={comparison['root_paths_unchanged']}; protected files unchanged={comparison['protected_files_unchanged']}.")
    print("Preflight exit=20; evaluation exit=null (not invoked).")
    return 20


if __name__ == "__main__":
    raise SystemExit(main())
