"""Isolated, serial frozen-answer faithfulness pilot. No answer generation."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from urllib import error, request

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SOURCE = ROOT / "docs/competition/external-rag/20261021-anythingllm-qwen72b/records.jsonl"
CAP = Decimal("5.00")
OUTPUT_CAP = 2048
SEED = "judge-gate-pilot-20261021-v1"
SECRETS = []


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked(data):
    if any(s and s in data for s in SECRETS):
        raise RuntimeError("SECRET_IN_OUTPUT_REFUSED")
    return data


def write_json(path, value):
    path.write_bytes(checked(encode(value)))


def append_json(path, value):
    data = checked((json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode())
    with path.open("ab") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()] if path.exists() else []


def git_status():
    return subprocess.check_output(["git", "--no-optional-locks", "status", "--porcelain=v1"], cwd=ROOT).decode("utf-8")


def evidence_hashes():
    targets = [ROOT / "frontend/src/fixtures/demo.json", ROOT / "backend/core/gate.py",
               ROOT / "backend/adapters/ragas_compat.py", ROOT / "venv/Lib/site-packages/ragas/metrics/_faithfulness.py",
               ROOT / "venv/Lib/site-packages/ragas/llms/prompt.py", ROOT / "venv/Lib/site-packages/ragas/llms/output_parser.py"]
    for dirname in ["docs/competition/external-rag/20261021-anythingllm-qwen72b", "docs/competition/evidence/20261005T144504Z-full-39511424"]:
        targets.extend(p for p in (ROOT / dirname).rglob("*") if p.is_file())
    return {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in sorted(set(targets))}


def model_specs():
    return [
        dict(id="qwen72b", model="Qwen/Qwen2.5-72B-Instruct", endpoint="https://api.siliconflow.cn/v1/chat/completions",
             key_env="VERISCOPE_SILICONFLOW_API_KEY", input_cap=32768, input_cny_m="4.13", output_cny_m="4.13", extra={}),
        dict(id="deepseek", model="deepseek-flash", endpoint="https://api.deepseek.com/chat/completions",
             key_env="OPENAI_API_KEY", input_cap=1048576, input_cny_m="2", output_cny_m="8", extra={"thinking": {"type": "disabled"}}),
        dict(id="glm32b", model="THUDM/GLM-4-32B-0414", endpoint="https://api.siliconflow.cn/v1/chat/completions",
             key_env="VERISCOPE_SILICONFLOW_API_KEY", input_cap=32768, input_cny_m="1.89", output_cny_m="1.89", extra={}),
    ]


def upper_cost(model):
    value = (Decimal(model["input_cap"]) * Decimal(model["input_cny_m"]) + Decimal(OUTPUT_CAP) * Decimal(model["output_cny_m"])) / 1000000
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)


class StopPilot(RuntimeError):
    pass


@contextmanager
def execution_lock(path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise StopPilot("ANOTHER_OR_UNRESOLVED_PILOT_PROCESS_LOCK") from None
    try:
        os.write(fd, encode(dict(pid=os.getpid(), started_at=now())))
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        path.unlink()


class Budget:
    def __init__(self, path, cap=CAP):
        self.path, self.cap = path, cap
        self.entries = read_jsonl(path)

    def balances(self):
        reservations, settled = {}, {}
        for e in self.entries:
            if e["event"] == "reserved":
                reservations[e["request_id"]] = Decimal(e["reserved_cny"])
            if e["event"] == "settled":
                settled[e["request_id"]] = Decimal(e["estimated_ceiling_cost_cny"])
        known = sum(settled.values(), Decimal(0))
        held = sum((v for k, v in reservations.items() if k not in settled), Decimal(0))
        return known, held

    def event(self, value):
        append_json(self.path, value)
        self.entries.append(value)

    def reserve(self, request_id, model, metadata):
        if any(e.get("request_id") == request_id for e in self.entries):
            raise StopPilot("REQUEST_ALREADY_RESERVED_DO_NOT_REPEAT")
        known, held = self.balances()
        amount = upper_cost(model)
        if known + held + amount > self.cap:
            raise StopPilot("BUDGET_INSUFFICIENT_FOR_WORST_CASE_RESERVATION")
        self.event(dict(event="reserved", request_id=request_id, at=now(), reserved_cny=str(amount), **metadata))

    def settle(self, request_id, model, usage):
        if not isinstance(usage, dict):
            raise StopPilot("UNKNOWN_USAGE_RESERVATION_RETAINED")
        values = [usage.get(k) for k in ["prompt_tokens", "completion_tokens", "total_tokens"]]
        if any(type(v) is not int or v < 0 for v in values):
            raise StopPilot("INVALID_USAGE_RESERVATION_RETAINED")
        p, c, t = values
        if t != p + c or p > model["input_cap"] or c > OUTPUT_CAP:
            raise StopPilot("USAGE_EXCEEDS_FROZEN_ENVELOPE")
        cost = (Decimal(p) * Decimal(model["input_cny_m"]) + Decimal(c) * Decimal(model["output_cny_m"])) / 1000000
        self.event(dict(event="settled", request_id=request_id, at=now(), usage=usage,
                        estimated_ceiling_cost_cny=str(cost), actual_bill_cny=None,
                        vendor_cache_pricing="all input charged at frozen non-cache peak ceiling"))
        return cost


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_credentials():
    from dotenv import dotenv_values
    file_values = dotenv_values(ROOT / ".env")
    keys = {}
    for name in {m["key_env"] for m in model_specs()}:
        value = os.environ.get(name) or file_values.get(name)
        if not isinstance(value, str) or not value.strip():
            raise StopPilot("MISSING_CREDENTIAL_" + name)
        keys[name] = value
    SECRETS[:] = [v.encode() for v in keys.values()]
    return keys


def metric_class():
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGCHAIN_TRACING"] = "false"
    sys.path.insert(0, str(ROOT))
    from backend.adapters.ragas_compat import ChineseFaithfulness
    return ChineseFaithfulness


def prepare():
    if (OUT / "freeze.json").exists():
        raise StopPilot("FREEZE_ALREADY_EXISTS")
    rows = read_jsonl(SOURCE)
    eligible = [r["evaluation"] for r in rows if r["evaluation"]["case_type"] == "positive"]
    selected = sorted(eligible, key=lambda e: sha((SEED + ":" + e["id"]).encode()))[:10]
    inputs = []
    for e in selected:
        item = {k: e[k] for k in ["id", "case_type", "question", "answer", "contexts", "ground_truth"]}
        if not isinstance(item["contexts"], list) or not all(isinstance(x, str) for x in item["contexts"]):
            raise StopPilot("INVALID_FROZEN_CONTEXTS")
        item["input_sha256"] = sha(encode(item))
        inputs.append(item)
    raw = b"".join((json.dumps(e, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode() for e in inputs)
    (OUT / "inputs.jsonl").write_bytes(raw)
    models = model_specs()
    pricing = dict(frozen_at=now(), currency="CNY", unit="per million tokens", models=models,
                   max_output_tokens=OUTPUT_CAP, budget_cny=str(CAP),
                   sources=["https://www.siliconflow.cn/pricing", "https://github.com/zai-org/GLM-4",
                            "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                            "https://api-docs.deepseek.com/zh-cn/guides/thinking_mode"],
                   note="SiliconFlow public model catalog: Qwen72 32768, GLM32 32768. DeepSeek 1M rounded upward to 1048576; peak non-cache input/output prices frozen. No actual bills available.")
    write_json(OUT / "pricing.json", pricing)
    schedule = []
    for repeat in [1, 2]:
        for index, e in enumerate(inputs):
            order = models[index % 3:] + models[:index % 3]
            for model in order:
                schedule.append(dict(case_id=e["id"], judge_id=model["id"], repeat=repeat,
                                     combination_id=f'{repeat}-{e["id"]}-{model["id"]}'))
    write_json(OUT / "schedule.json", schedule)
    Metric = metric_class()
    metric = Metric(max_retries=0)
    prompts = dict(statement_prompt=metric.statement_prompt.dict(), nli_prompt=metric.nli_statements_message.dict())
    write_json(OUT / "prompts.json", prompts)
    freeze = dict(created_at=now(), archive=SOURCE.relative_to(ROOT).as_posix(), archive_sha256=sha(SOURCE.read_bytes()),
                  sample_ids=[e["id"] for e in inputs], population="20 frozen positive rows of this single application batch",
                  selection="first 10 by ascending SHA256(seed + ':' + id); no score or answer contents used",
                  seed=SEED, comparisons=60, expected_base_requests=120, repetitions=2,
                  parameters=dict(temperature=0, max_tokens=OUTPUT_CAP, stream=False, n=1, parsing_repairs=0, http_retries=0,
                                  timeout_seconds=90, sequential=True, delay_between_requests_seconds=1),
                  metric="installed Ragas 0.1.21 ChineseFaithfulness; original prompts/parser/formula; no ContextPrecision or Trace",
                  differences_from_historical="raw OpenAI-compatible transport; bounded output; no parser repairs/SDK retry; current judge versions. Not a rerun of historical full gate.",
                  versions={k: importlib.metadata.version(k) for k in ["ragas", "langchain-core", "pandas", "numpy"]},
                  python=sys.version, git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
                  git_status_before=git_status(), protected_hashes=evidence_hashes(),
                  frozen_files={name:sha((OUT/name).read_bytes()) for name in ["inputs.jsonl","pricing.json","schedule.json","prompts.json","run_pilot.py"]},
                  independent_correctness_labels=None, actual_bills=None)
    write_json(OUT / "freeze.json", freeze)
    print(json.dumps(dict(event="prepared", sample_ids=freeze["sample_ids"], planned=60, paid_calls=0), ensure_ascii=False), flush=True)


def validate_freeze():
    freeze = json.loads((OUT / "freeze.json").read_bytes())
    for name, expected in freeze["frozen_files"].items():
        if sha((OUT / name).read_bytes()) != expected:
            raise StopPilot("FROZEN_LOCAL_ARTIFACT_CHANGED_" + name)
    if sha(SOURCE.read_bytes()) != freeze["archive_sha256"]:
        raise StopPilot("ARCHIVE_CHANGED")
    return freeze


class PilotLLM:
    def __init__(self, model, combination, budget, key, opener=None):
        self.model, self.combo, self.budget, self.key = model, combination, budget, key
        self.opener = opener or request.build_opener(NoRedirect())
        self.calls = []
        self.stage_index = 0

    async def generate(self, prompt, n=1, callbacks=None, **kwargs):
        from langchain_core.outputs import Generation, LLMResult
        if n != 1 or self.stage_index >= 2 or kwargs:
            raise StopPilot("UNEXPECTED_OR_HIDDEN_LLM_REQUEST")
        stage = ["extraction", "nli"][self.stage_index]
        self.stage_index += 1
        rid = self.combo["combination_id"] + "-" + stage
        body = dict(model=self.model["model"], messages=[dict(role="user", content=prompt.to_string())],
                    temperature=0, max_tokens=OUTPUT_CAP, stream=False, n=1, **self.model["extra"])
        payload = checked(encode(body))
        # Fail before reserving/sending if a body unexpectedly contains credentials.
        reqpath = OUT / "requests" / (rid + ".request.json")
        reqpath.write_bytes(payload)
        meta = dict(**self.combo, stage=stage, request_sha256=sha(payload), request_file=reqpath.name,
                    model=self.model["model"])
        self.budget.reserve(rid, self.model, meta)
        self.calls.append(rid)
        start = time.monotonic()
        req = request.Request(self.model["endpoint"], data=payload, method="POST",
                              headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        response = None
        try:
            with self.opener.open(req, timeout=90) as resp:
                response = resp.read()
                http_status = resp.status
                provider_request_id = resp.headers.get("x-request-id") or resp.headers.get("request-id")
        except error.HTTPError as exc:
            response = exc.read()
            (OUT / "requests" / (rid + ".response.json")).write_bytes(checked(response))
            self.budget.event(dict(event="request_failed", request_id=rid, at=now(), http_status=exc.code,
                                   elapsed_seconds=time.monotonic()-start, error_type="HTTPError", reservation_retained=True))
            raise StopPilot("HTTP_ERROR_NO_RETRY_" + str(exc.code)) from None
        except Exception as exc:
            self.budget.event(dict(event="request_failed", request_id=rid, at=now(),
                                   elapsed_seconds=time.monotonic()-start, error_type=type(exc).__name__, reservation_retained=True))
            raise StopPilot("TRANSPORT_ERROR_NO_RETRY_" + type(exc).__name__) from None
        response_path = OUT / "requests" / (rid + ".response.json")
        response_path.write_bytes(checked(response))
        elapsed = time.monotonic()-start
        try:
            data = json.loads(response)
        except (ValueError, UnicodeError):
            raise StopPilot("INVALID_RESPONSE_JSON_RESERVATION_RETAINED") from None
        self.budget.event(dict(event="response_received", request_id=rid, at=now(), http_status=http_status,
                               elapsed_seconds=elapsed, provider_request_id=provider_request_id,
                               provider_response_id=data.get("id"), response_model=data.get("model"), response_sha256=sha(response),
                               usage=data.get("usage"), response_file=response_path.name))
        self.budget.settle(rid, self.model, data.get("usage"))
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise StopPilot("INVALID_COMPLETION_CHOICES")
        message = choices[0].get("message", {})
        if message.get("reasoning_content"):
            raise StopPilot("UNEXPECTED_REASONING_STOPPED")
        text = message.get("content")
        if not isinstance(text, str):
            raise StopPilot("MISSING_COMPLETION_CONTENT")
        if choices[0].get("finish_reason") != "stop":
            raise StopPilot("NONSTOP_COMPLETION_NO_RETRY")
        time.sleep(1)
        return LLMResult(generations=[[Generation(text=text)]])


def summary():
    frozen = json.loads((OUT / "freeze.json").read_bytes())
    results = read_jsonl(OUT / "results.jsonl")
    entries = read_jsonl(OUT / "budget-ledger.jsonl")
    known, held = Budget(OUT / "budget-ledger.jsonl").balances()
    received = [e for e in entries if e["event"] == "response_received"]
    by_model = {}
    for model in model_specs():
        rr = [r for r in results if r["judge_id"] == model["id"]]
        reps = {}
        for rep in [1,2]:
            subset = [r for r in rr if r["repeat"] == rep]
            valid = [r for r in subset if r["score"] is not None]
            complete = len(subset)==10 and len(valid)==10
            # Do not imply the 10-case subset is the formal 20-positive gate.
            reps[str(rep)] = dict(total=10, attempted=len(subset), valid=len(valid),
                                   valid_only_mean_descriptive=sum(r["score"] for r in valid)/len(valid) if valid else None,
                                   all_ten_mean=sum(r["score"] for r in valid)/10 if complete else None,
                                   exploratory_subset_gate_at_0_8=("PASS" if sum(r["score"] for r in valid)/10 >= 0.8 else "FAIL") if complete else "REVIEW")
        paired=[]
        for cid in frozen["sample_ids"]:
            pair = [r for r in rr if r["case_id"]==cid and r["score"] is not None]
            if len(pair)==2:
                paired.append(dict(case_id=cid, absolute_difference=abs(pair[0]["score"]-pair[1]["score"])))
        settled=[e for e in entries if e["event"]=="settled" and ("-"+model["id"]+"-") in e["request_id"]]
        by_model[model["id"]]=dict(model=model["model"], repetitions=reps, paired_cases=paired,
                                     estimated_ceiling_cost_cny=str(sum((Decimal(e["estimated_ceiling_cost_cny"]) for e in settled), Decimal(0))),
                                     prompt_tokens=sum(e["usage"]["prompt_tokens"] for e in settled),
                                     completion_tokens=sum(e["usage"]["completion_tokens"] for e in settled))
    state=dict(updated_at=now(), planned_combinations=60, attempted_combinations=len(results),
               finite_scores=sum(r["score"] is not None for r in results),
               reserved_requests=sum(e["event"]=="reserved" for e in entries), completed_http_responses=len(received),
               estimated_ceiling_cost_cny=str(known), unknown_usage_reserved_cny=str(held),
               maximum_accounted_exposure_cny=str(known+held), budget_cny=str(CAP), actual_paid_bill_cny=None,
               model_results=by_model, answer_generation_calls=0, other_metrics_calls=0,
               independent_correctness_labels=None, accuracy=None, false_positive_rate=None,
               protected_files_unchanged=evidence_hashes()==frozen["protected_hashes"],
               git_status_unchanged=git_status()==frozen["git_status_before"],
               full_historical_gate_recomputed=False)
    write_json(OUT / "summary.json",state)
    return state


async def _run_locked():
    frozen=validate_freeze()
    keys=load_credentials()
    (OUT / "requests").mkdir(exist_ok=True)
    # Resume skips completed combinations; an unresolved reservation is never replayed.
    budget=Budget(OUT / "budget-ledger.jsonl")
    _, held=budget.balances()
    if held:
        raise StopPilot("UNRESOLVED_PRIOR_RESERVATION_REQUIRES_REVIEW")
    completed={r["combination_id"] for r in read_jsonl(OUT / "results.jsonl")}
    inputs={e["id"]:e for e in read_jsonl(OUT / "inputs.jsonl")}
    models={m["id"]:m for m in model_specs()}
    Metric=metric_class()
    start=time.monotonic()
    for combo in json.loads((OUT / "schedule.json").read_bytes()):
        if combo["combination_id"] in completed:
            continue
        row=inputs[combo["case_id"]]
        combo={**combo, "input_sha256":row["input_sha256"]}
        adapter=PilotLLM(models[combo["judge_id"]],combo,budget,keys[models[combo["judge_id"]]["key_env"]])
        metric=Metric(llm=adapter,max_retries=0)
        t=time.monotonic()
        failure=None
        stop_reason=None
        score=None
        try:
            value=await metric._ascore(row,callbacks=[])
            if math.isfinite(value):
                score=value
            else:
                failure="UNPARSEABLE_OR_NO_STATEMENTS"
        except StopPilot as exc:
            failure=str(exc)
            stop_reason=failure
        except Exception as exc:
            failure="METRIC_EXCEPTION_"+type(exc).__name__
            stop_reason=failure
        result=dict(**combo,at=now(),score=score,status="scored" if score is not None else "unmeasured",
                    failure=failure,request_ids=adapter.calls,elapsed_seconds=time.monotonic()-t)
        append_json(OUT / "results.jsonl",result)
        state=summary()
        print(json.dumps(dict(event="combination",combination=combo["combination_id"],score=score,failure=failure,
                              done=state["attempted_combinations"],finite=state["finite_scores"],
                              estimated_ceiling_cost_cny=state["estimated_ceiling_cost_cny"],
                              unknown_usage_reserved_cny=state["unknown_usage_reserved_cny"]),ensure_ascii=False),flush=True)
        if stop_reason:
            write_json(OUT / "stop.json",dict(at=now(),reason=stop_reason,auto_retry=False))
            break
    state=summary()
    write_json(OUT / "execution.json",dict(started_at=frozen["created_at"],ended_at=now(),elapsed_seconds=time.monotonic()-start,
                                         complete=state["attempted_combinations"]==60 and state["finite_scores"]==60,manual_retries=0))
    print(json.dumps(dict(event="finished",attempted=state["attempted_combinations"],finite=state["finite_scores"],
                          estimated_ceiling_cost_cny=state["estimated_ceiling_cost_cny"],
                          unknown_usage_reserved_cny=state["unknown_usage_reserved_cny"]),ensure_ascii=False),flush=True)


async def run():
    with execution_lock(OUT / "pilot.lock"):
        await _run_locked()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["prepare","run","summary"])
    args=parser.parse_args()
    try:
        if args.action=="prepare":prepare()
        elif args.action=="summary":print(json.dumps(summary(),ensure_ascii=False))
        else:asyncio.run(run())
    except StopPilot as exc:
        print(json.dumps(dict(event="stopped",reason=str(exc)),ensure_ascii=False),flush=True)
        sys.exit(2)
