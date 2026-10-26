"""External RAG end-to-end evaluation. prepare/analyze are offline; run requires opt-in.

The application retrieves and generates. VeriScope uses exactly those returned
contexts for the existing five Ragas metrics, Trace, aggregation and Quality Gate.
No platform retriever, answer regeneration, automatic batch retry or model fallback.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.adapters.rag_application import (
    ApplicationResponse, SCHEMA_VERSION, HTTP_TIMEOUT, access_token,
    call_application, checked_endpoint, request_body,
)

METRICS = ("faithfulness", "context_precision", "answer_relevancy", "answer_correctness", "context_recall")
SOURCE_PATHS = (
    "tools/competition/evaluate_external.py", "backend/adapters/rag_application.py",
    "backend/adapters/models/judging.py", "backend/adapters/models/registry.py",
    "backend/adapters/models/openai_compatible.py", "backend/adapters/ragas_compat.py",
    "backend/core/pipeline.py", "backend/core/gate.py", "backend/core/utils.py",
    "backend/services/trace.py", "backend/config.py",
)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_cases(path):
    items = read_json(path)
    if not isinstance(items, list) or not items:
        raise ValueError("题集必须是非空 JSON 数组。")
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("题集条目必须是对象。")
        request_body(item)
        if item["id"] in seen:
            raise ValueError("题号重复，不能进行端到端评测。")
        seen.add(item["id"])
        if not isinstance(item.get("ground_truth"), str) or not item["ground_truth"].strip():
            raise ValueError("完整五项评测要求每题提供非空 ground_truth；不会自动生成参考答案。")
        if item.get("case_type", "positive") not in ("positive", "negative"):
            raise ValueError("case_type 只接受 positive 或 negative。")
    return items


def model_config(name):
    from backend.config import BASE_DIR, MODELS_CONFIG
    from backend.core.model_registry import load
    configs = [*MODELS_CONFIG, *load(Path(BASE_DIR), [item["name"] for item in MODELS_CONFIG])]
    matches = [item for item in configs if item["name"] == name]
    if len(matches) != 1:
        raise ValueError("裁判模型未配置，请先在本机登记。")
    return matches[0]


def public_config(config):
    return {key: config[key] for key in ("name", "base_url", "api_key_env", "provider") if key in config}


def checksums(directory):
    directory = Path(directory)
    lines = [f"{digest(path)}  {path.relative_to(directory).as_posix()}" for path in sorted(directory.rglob("*"))
             if path.is_file() and path.name != "SHA256SUMS.txt"]
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify(directory, *, check_sources=False):
    directory = Path(directory)
    for line in (directory / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or digest(path) != expected:
            raise ValueError("归档文件校验失败，停止。")
    manifest = read_json(directory / "manifest.json")
    if digest(directory / "testset.json") != manifest["inputSha256"]:
        raise ValueError("题集哈希与锁定记录不一致，停止。")
    if check_sources:
        for name, expected in manifest["sourceSha256"].items():
            if digest(ROOT / name) != expected:
                raise ValueError("运行源码与准备时不一致，请新建准备目录，不能继续旧批次。")
    return manifest


def prepare(input_path, directory, endpoint, judge_model, *, token_env=None, application_name="外部 RAG 应用"):
    items = load_cases(input_path)
    endpoint = checked_endpoint(endpoint)
    if token_env is not None and not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", token_env):
        raise ValueError("tokenEnv 必须是环境变量名。")
    config = model_config(judge_model)
    checked_endpoint(config["base_url"])
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError("输出目录已存在；每次批次必须新建独立目录。")
    directory.mkdir(parents=True)
    (directory / "testset.json").write_bytes(Path(input_path).read_bytes())
    versions = {}
    for name in ("ragas", "langchain", "langchain-core", "langchain-openai", "datasets", "httpx", "sentence-transformers"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "unknown"
    from backend.config import EMBEDDING_MODEL, EVAL_TIMEOUT, LLM_MAX_RETRIES, LLM_TIMEOUT, FAITH_THRESHOLD, CTX_PREC_THRESHOLD
    for name in SOURCE_PATHS:
        target = directory / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    except (OSError, subprocess.CalledProcessError):
        head, dirty = "unknown", "unknown"
    manifest = {
        "schemaVersion": "veriscope.e2e.run.v1", "contractVersion": SCHEMA_VERSION,
        "status": "prepared", "preparedAt": now(), "applicationName": application_name,
        "retrievalParty": "application", "platformRetrieval": False,
        "endpoint": endpoint, "tokenEnv": token_env, "applicationTimeoutSeconds": HTTP_TIMEOUT,
        "applicationRetries": 0, "itemCount": len(items), "judge": public_config(config),
        "inputSha256": digest(directory / "testset.json"),
        "sourceSha256": {name: digest(ROOT / name) for name in SOURCE_PATHS},
        "dependencies": versions, "python": sys.version.split()[0], "gitHead": head, "gitStatus": dirty,
        "sentenceAdapter": "zh-sentence-v1", "embedding": EMBEDDING_MODEL,
        "metrics": list(METRICS), "traceEnabled": True,
        "judgeTimeoutSeconds": LLM_TIMEOUT, "judgeMaxRetries": LLM_MAX_RETRIES,
        "ragasTimeoutSeconds": EVAL_TIMEOUT,
        "thresholds": {"faithfulness": FAITH_THRESHOLD, "context_precision": CTX_PREC_THRESHOLD},
        "cost": {"status": "unknown", "note": "需单独授权付费运行；无供应商费用自动封顶。题数不等于裁判调用数。"},
    }
    write_json(directory / "manifest.json", manifest)
    checksums(directory)
    return manifest


SENSITIVE_KEYS = {"authorization", "apikey", "token", "accesstoken", "password", "secret", "clientsecret"}


def redact(value, secrets=()):
    """Only known credential values and credential-shaped fields; never enumerate the environment."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.sub(r"[^a-z]", "", key.lower()) in SENSITIVE_KEYS else redact(item, secrets)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r'(?i)("(?:api[_-]?key|access[_-]?token|token|authorization|password|secret)"\s*:\s*")[^"\r\n]*',
                       r'\1[REDACTED]', value)
        return re.sub(r"(?i)Bearer\s+[^\s\"<>]+|\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
    return value


def write_record(stream, record, secrets):
    safe = redact(record, secrets)
    safe["redactionApplied"] = safe != record
    stream.write(json.dumps(safe, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()


def evaluate_returned(item, response, embeddings, judge_llm):
    """Reference answers stay local; only actual, ordered generation contexts enter scoring."""
    from backend.api.serialization import _json_value
    from backend.core.pipeline import score_answer_rows
    from backend.core.utils import _is_refusal
    contexts = [part["text"] for part in response["contexts"]]
    data = {"question": [item["question"]], "answer": [response["answer"]],
            "contexts": [contexts], "ground_truth": [item["ground_truth"]]}
    raw = score_answer_rows([item], data, embeddings, judge_llm, True)
    row = _json_value(raw["df"].to_dict(orient="records")[0])
    row.update(id=item["id"], application_status=response["status"],
               application_contexts=response["contexts"], application_meta=response.get("meta"),
               missing_application_contexts=not contexts,
               refusal_detector=_is_refusal(response["answer"]),
               refusal_status_agrees=(response["status"] == "refused") == _is_refusal(response["answer"]))
    row["judge_calls"] = raw["judge_recorder"].snapshot()
    row["judge_response_models"] = raw["judge_recorder"].response_models
    return row


def default_resources(config):
    from backend.adapters.models.judging import build_judge
    from backend.config import EMBEDDING_MODEL
    from langchain_community.embeddings import HuggingFaceEmbeddings
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL, model_kwargs={"local_files_only": True})
    return embeddings, build_judge(config)


def run(directory, *, allow_model_calls=False, application_call=None, resources_factory=None, scorer=None):
    if not allow_model_calls:
        raise ValueError("未授权模型调用；prepare 和 analyze 均不会调用模型。")
    directory = Path(directory)
    manifest = verify(directory, check_sources=True)
    if manifest["status"] != "prepared" or (directory / "records.jsonl").exists():
        raise ValueError("本批次已经尝试运行，不自动整批重跑；请保留记录。")
    items = load_cases(directory / "testset.json")
    config = model_config(manifest["judge"]["name"])
    if public_config(config) != manifest["judge"]:
        raise ValueError("裁判配置与准备时不一致，停止。")
    token = access_token(manifest["tokenEnv"])
    judge_key = os.environ.get(config["api_key_env"])
    if not judge_key:
        raise ValueError("裁判密钥环境变量未设置，停止。")
    secrets = [secret for secret in (token, judge_key) if secret]
    manifest.update(status="running", startedAt=now())
    write_json(directory / "manifest.json", manifest)
    checksums(directory)
    caller = application_call or call_application
    scorer = scorer or evaluate_returned
    started = time.monotonic()
    try:
        embeddings, judge_llm = (resources_factory or default_resources)(config)
        with (directory / "records.jsonl").open("x", encoding="utf-8") as stream:
            for item in items:
                record = {"id": item["id"], "at": now(), "applicationRequest": request_body(item),
                          "applicationAttempted": False, "evaluation": None}
                from backend.adapters.models.judging import ModelCallRecorder
                observer = ModelCallRecorder()
                try:
                    record["applicationAttempted"] = True
                    call = caller(manifest["endpoint"], item, token_env=manifest["tokenEnv"], timeout=HTTP_TIMEOUT)
                    record["applicationCall"] = asdict(call)
                    if call.usable:
                        # Validate injected/mock clients identically; no trust in an adapter's flag alone.
                        response = ApplicationResponse.model_validate(call.response)
                        if response.id != item["id"]:
                            raise ValueError("id_mismatch")
                        judge_input = {"answer": call.response["answer"], "contexts": call.response["contexts"]}
                        if redact(judge_input, secrets) != judge_input:
                            record["evaluationError"] = "sensitive_input"
                        else:
                            with observer.capture(judge_llm):
                                record["evaluation"] = scorer(item, call.response, embeddings, judge_llm)
                            record["evaluation"].update(judge_model=config["name"], judge_mode="configured", judge_response_scope="item")
                    else:
                        record["evaluationError"] = call.outcome
                except Exception as error:
                    record["evaluationError"] = type(error).__name__
                except BaseException as error:
                    record["evaluationError"] = type(error).__name__
                    record["judgeCalls"] = observer.snapshot()
                    write_record(stream, record, secrets)
                    raise
                record["judgeCalls"] = observer.snapshot()
                write_record(stream, record, secrets)
        manifest.update(status="completed", finishedAt=now(), elapsedSeconds=round(time.monotonic() - started, 3))
    except BaseException as error:
        manifest.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                        errorType=type(error).__name__, finishedAt=now())
        raise
    finally:
        write_json(directory / "manifest.json", redact(manifest, secrets))
        checksums(directory)
    return analyze(directory)


def _raw_metrics(rows):
    import pandas as pd
    frame = pd.DataFrame(rows)
    for name in METRICS:
        if name not in frame:
            frame[name] = None
    total = fact_error = fabrication = pos_total = pos_halluc = 0
    for row in rows:
        value = row.get("hallucination_trace")
        trace = json.loads(value) if isinstance(value, str) else value or {}
        for statement in trace.get("statements", []):
            total += 1
            verdict = statement.get("verdict")
            fact_error += verdict == "fact_error"
            fabrication += verdict == "fabrication"
            if row.get("case_type", "positive") == "positive":
                pos_total += 1
                pos_halluc += verdict in ("fact_error", "fabrication")
    return {"df": frame, "trace_total": total, "trace_fact_error": fact_error,
            "trace_fabrication": fabrication, "pos_trace_total": pos_total, "pos_trace_halluc": pos_halluc}


def analyze(directory):
    directory = Path(directory)
    manifest = verify(directory)
    record_file = directory / "records.jsonl"
    records = [json.loads(line) for line in record_file.read_text(encoding="utf-8").splitlines() if line] if record_file.exists() else []
    rows = [record["evaluation"] for record in records if isinstance(record.get("evaluation"), dict)]
    failures = [{"id": record["id"], "reason": record.get("evaluationError", "unknown"),
                 "applicationErrorCode": (record.get("applicationCall") or {}).get("error_code")}
                for record in records if not isinstance(record.get("evaluation"), dict)]
    seen = {record["id"] for record in records}
    pending = [item["id"] for item in load_cases(directory / "testset.json") if item["id"] not in seen]
    summary, gate = None, {"passed": None, "status": "not_evaluated", "message": "没有有效指标，门禁未计算。"}
    valid_counts = {key: sum(isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
                            and math.isfinite(row[key]) for row in rows) for key in METRICS}
    if rows:
        from backend.core.pipeline import aggregate_model_metrics
        from backend.core.gate import check_quality_gate
        raw = _raw_metrics(rows)
        summary = aggregate_model_metrics(raw, manifest["applicationName"], manifest.get("elapsedSeconds", 0))
        returned = list(dict.fromkeys(name for row in rows for name in row.get("judge_response_models", [])))
        summary.update(judge_model=manifest["judge"]["name"], judge_mode="configured", judge_response_models=returned)
        if summary["faithfulness"] is not None:
            thresholds = manifest["thresholds"]
            computed = check_quality_gate([summary], {manifest["applicationName"]: raw["df"]}, thresholds["faithfulness"], thresholds["context_precision"])
            gate = {"passed": computed["gate_passed"], "status": "PASS" if computed["gate_passed"] else "FAIL", "message": computed["gate_message"]}
    calls = [call for record in records for call in record.get("judgeCalls", [])]
    usage = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [event.get("usage", {}).get(key) for event in calls]
        usage[key] = sum(values) if values and all(isinstance(value, int) for value in values) else None
    result = {"kind": "external-rag-e2e", "retrievalParty": "application", "applicationName": manifest["applicationName"],
              "judgeRequestedModel": manifest["judge"]["name"],
              "status": manifest["status"], "total": manifest["itemCount"], "evaluated": len(rows),
              "failed": failures, "notAttempted": pending, "validCounts": valid_counts,
              "missingMetrics": {key: len(rows) - count for key, count in valid_counts.items()},
              "metricSampleScope": "evaluated_answers",
              "summary": summary, "gate": gate, "rows": rows,
              "applicationCalls": sum(record.get("applicationAttempted", False) for record in records),
              "judgeCalls": len(calls), "usage": usage,
              "cost": {"status": "unknown", "note": "未提供供应商单价与账单，不将 token 用量写成实际扣费。"},
              "warnings": ["失败题不计入指标均值，未填零；应同时查看有效数量与未完成数量。",
                           "contexts 由被测应用声明为实际生成资料，平台未独立验证应用内部链路。",
                           "外部应用与平台检索运行分别归档，检索条件不同不能直接排名。"]}
    cases = load_cases(directory / "testset.json")
    result["gate"]["sampleScope"] = {
        "positiveTotal": sum(item.get("case_type", "positive") == "positive" for item in cases),
        "positiveEvaluated": sum(row.get("case_type", "positive") == "positive" for row in rows),
        "validPositiveFaithfulness": sum(row.get("case_type", "positive") == "positive" and isinstance(row.get("faithfulness"), (int, float))
                                           and math.isfinite(row["faithfulness"]) for row in rows),
        "validPositiveContextPrecision": sum(row.get("case_type", "positive") == "positive" and isinstance(row.get("context_precision"), (int, float))
                                              and math.isfinite(row["context_precision"]) for row in rows),
    }
    result["emptyTraceCount"] = sum(
        isinstance(trace := json.loads(row["hallucination_trace"]) if isinstance(row.get("hallucination_trace"), str)
                   else row.get("hallucination_trace"), dict) and trace.get("statements") == [] for row in rows
    )
    write_json(directory / "result.json", result)
    write_report(directory, result)
    checksums(directory)
    return result


def write_report(directory, result):
    lines = ["# 外部 RAG 端到端评测", "", "检索方：被测应用。平台未检索、未重新生成回答。", "",
             f"题目 {result['total']}；已评测 {result['evaluated']}；失败 {len(result['failed'])}；未尝试 {len(result['notAttempted'])}。",
             f"门禁：{result['gate']['status']}。{result['gate']['message']}", "",
             "## 五项指标", "", "| 指标 | 均值 | 有效数量 |", "|---|---|---|"]
    for key in METRICS:
        value = (result["summary"] or {}).get(key)
        lines.append(f"| {key} | {value if value is not None else '缺失'} | {result['validCounts'][key]} / {result['total']} |")
    lines += ["", "## 逐题记录", "", "| 题号 | 应用状态 | 实际资料段数 | Trace 状态 | 拒答识别 |", "|---|---|---|---|---|"]
    for row in result["rows"]:
        value = row.get("hallucination_trace")
        trace = json.loads(value) if isinstance(value, str) else value or {}
        note = "一致" if row.get("refusal_status_agrees") else "不一致（保留两者，不改判）"
        if row.get("missing_application_contexts"):
            note += "；应用未提供检索资料"
        lines.append(f"| {row['id']} | {row['application_status']} | {len(row['contexts'])} | {trace.get('trace_status', '未记录')} | {note} |")
    lines += ["", "## 失败与限制", "", *[f"- {item['id']}：{item['reason']}" for item in result["failed"]],
              *[f"- {item_id}：尚未尝试" for item_id in result["notAttempted"]],
              *["- " + text for text in result["warnings"]], "- 实际费用未知；没有费用自动封顶。", "",
              "完整应用请求、响应与异常见 records.jsonl；模型判定不是人工认证。"]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Inert, self-contained HTML: no executable application output or network assets.
    def html_text(value):
        return escape(str(value))
    metric_rows = "".join(f"<tr><th>{key}</th><td>{html_text((result['summary'] or {}).get(key, '缺失'))}</td>"
                          f"<td>{result['validCounts'][key]} / {result['total']}</td></tr>" for key in METRICS)
    cases_html = []
    for row in result["rows"]:
        trace_value = row.get("hallucination_trace")
        trace = json.loads(trace_value) if isinstance(trace_value, str) else trace_value or {}
        passages = "".join(f"<li><p>{html_text(passage)}</p></li>" for passage in row.get("contexts", []))
        statements = "".join(f"<li><p><strong>{html_text(statement.get('verdict', '未知判定'))}</strong> · {html_text(statement.get('text', ''))}</p>"
                             f"<blockquote>{html_text(statement.get('evidence', '无引用'))}</blockquote></li>" for statement in trace.get("statements", []))
        note = "应用未提供检索资料。" if row.get("missing_application_contexts") else ""
        if not row.get("refusal_status_agrees"):
            note += "应用拒答状态与平台识别不一致，保留两者，不改判。"
        cases_html.append(f"<details><summary>{html_text(row['id'])} · {html_text(row.get('question', ''))}</summary>"
                          f"<p>应用状态：{html_text(row.get('application_status'))}；Trace：{html_text(trace.get('trace_status', '未记录'))}</p>"
                          f"<p>{html_text(note)}</p><h3>原始回答</h3><p class='original'>{html_text(row.get('answer', ''))}</p>"
                          f"<h3>参考答案</h3><p class='original'>{html_text(row.get('ground_truth', ''))}</p>"
                          f"<h3>实际检索资料</h3><ol>{passages}</ol><h3>逐句机器判定</h3><ol>{statements}</ol></details>")
    failure_html = "".join(f"<li>{html_text(item['id'])}：{html_text(item['reason'])}</li>" for item in result["failed"])
    warning_html = "".join(f"<li>{html_text(value)}</li>" for value in result["warnings"])
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
    html += '<title>外部 RAG 端到端评测</title><style>body{background:#10161f;color:#f5f7fa;font:16px/1.8 sans-serif;max-width:1080px;margin:40px auto;padding:24px;overflow-wrap:anywhere}h1{color:#f2c879}h2{margin-top:32px}table{width:100%;border-collapse:collapse}td,th{padding:10px;text-align:left;border-bottom:1px solid #38404c}details{padding:18px 0;border-bottom:1px solid #38404c}summary{cursor:pointer;color:#f2c879}summary:focus-visible{outline:2px solid #f2c879;outline-offset:4px}.original,blockquote{white-space:pre-wrap}blockquote{margin-inline:0;padding:12px;background:#1b2430}li{margin-block:12px}</style>'
    html += '<h1>外部 RAG 端到端评测</h1><p>检索方：被测应用。失败题不填零；判定不代表人工认证。</p>'
    html += f'<p>应用：{html_text(result["applicationName"])}；裁判请求模型：{html_text(result["judgeRequestedModel"])}</p>'
    html += f'<p>已评测 {result["evaluated"]}/{result["total"]}；失败 {len(result["failed"])}；未尝试 {len(result["notAttempted"])} · 门禁 {html_text(result["gate"]["status"])}</p>'
    html += f'<h2>五项指标</h2><table><thead><tr><th>指标</th><th>均值</th><th>有效 / 全部题目</th></tr></thead><tbody>{metric_rows}</tbody></table>'
    html += f'<h2>逐题记录</h2>{"".join(cases_html)}<h2>失败与限制</h2><ul>{failure_html}{warning_html}</ul><p>实际费用未知；没有费用自动封顶。完整应用请求与响应见 records.jsonl。</p></html>'
    (directory / "report.html").write_text(html, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="外部 RAG 端到端评测；prepare/analyze 离线，run 才会调用应用和裁判。")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--input", type=Path, required=True)
    prep.add_argument("--dir", type=Path, required=True)
    prep.add_argument("--endpoint", required=True)
    prep.add_argument("--judge-model", required=True)
    prep.add_argument("--token-env")
    prep.add_argument("--application-name", default="外部 RAG 应用")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--dir", type=Path, required=True)
    run_parser.add_argument("--allow-model-calls", action="store_true")
    analysis = sub.add_parser("analyze")
    analysis.add_argument("--dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(args.input, args.dir, args.endpoint, args.judge_model,
                token_env=args.token_env, application_name=args.application_name)
        print("准备完成；未调用应用或模型。请核对材料、模型与费用后再授权 run。")
    elif args.command == "run":
        result = run(args.dir, allow_model_calls=args.allow_model_calls)
        print(f"已评测 {result['evaluated']}/{result['total']}；门禁 {result['gate']['status']}。")
    else:
        analyze(args.dir)
        print("离线分析完成。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Do not echo server error bodies, endpoints or exception strings into terminal logs.
        print(f"未完成：{type(error).__name__}。请检查输入、配置与归档状态；没有自动重试。", file=sys.stderr)
        raise SystemExit(2)
