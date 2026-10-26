"""Read completed pilot outputs, independently check arithmetic, write internal report."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
sys.path.insert(0,str(OUT))
import run_pilot as pilot


def main():
    state=pilot.summary()
    frozen=json.loads((OUT/"freeze.json").read_bytes())
    schedule=json.loads((OUT/"schedule.json").read_bytes())
    results=pilot.read_jsonl(OUT/"results.jsonl")
    ledger=pilot.read_jsonl(OUT/"budget-ledger.jsonl")
    pilot.metric_class()
    from ragas.metrics._faithfulness import _faithfulness_output_parser, _statements_output_parser
    audit=[]
    for result in results:
        item={k:result[k] for k in ["case_id","judge_id","repeat","combination_id","score","status","failure"]}
        item["extracted_statements"]=None
        item["judged_statements"]=None
        item["supported_statements"]=None
        item["arithmetic_matches"]=None
        for rid in result["request_ids"]:
            path=OUT/"requests"/(rid+".response.json")
            if not path.exists():continue
            data=json.loads(path.read_bytes())
            try:
                content=data["choices"][0]["message"]["content"]
                if rid.endswith("-extraction"):
                    parsed=_statements_output_parser.parse(content)
                    item["extracted_statements"]=sum(len(x.simpler_statements) for x in parsed.__root__)
                    item["extraction_statement_texts"]=[s for x in parsed.__root__ for s in x.simpler_statements]
                else:
                    parsed=_faithfulness_output_parser.parse(content)
                    verdicts=[x.verdict for x in parsed.__root__]
                    item["judged_statements"]=len(verdicts)
                    item["supported_statements"]=sum(bool(v) for v in verdicts)
                    item["nli_statement_texts"]=[x.statement for x in parsed.__root__]
                    item["nli_exact_statement_list_preserved"]=item.get("extraction_statement_texts")==item["nli_statement_texts"]
                    item["nli_statement_count_preserved"]=item["extracted_statements"]==len(verdicts)
                    item["binary_verdicts_only"]=all(v in [0,1] for v in verdicts)
                    recomputed=sum(bool(v) for v in verdicts)/len(verdicts) if verdicts else None
                    item["independently_recomputed_score"]=recomputed
                    item["arithmetic_matches"]=result["score"] is not None and recomputed==result["score"]
            except Exception as exc:
                item["offline_parse_error"]=type(exc).__name__
        audit.append(item)
    pilot.write_json(OUT/"arithmetic-audit.json",audit)
    durations=[e["elapsed_seconds"] for e in ledger if e["event"]=="response_received"]
    rank=sorted(durations)
    latency={"responses":len(rank),"p50_seconds":statistics.median(rank) if rank else None,
             "p95_nearest_rank_seconds":rank[math.ceil(.95*len(rank))-1] if rank else None}
    settled=[e for e in ledger if e["event"]=="settled"]
    tokens={"prompt_tokens":sum(e["usage"]["prompt_tokens"] for e in settled),
            "completion_tokens":sum(e["usage"]["completion_tokens"] for e in settled),
            "unknown_usage_requests":state["reserved_requests"]-len(settled)}
    ensemble=[]
    for repeat in [1,2]:
        rr=[r for r in results if r["repeat"]==repeat and r["score"] is not None]
        complete=len(rr)==30
        value=sum(r["score"] for r in rr)/30 if complete else None
        ensemble.append(dict(repeat=repeat,mean=value,subset_gate_at_0_8="PASS" if value is not None and value>=.8 else "FAIL" if value is not None else "REVIEW"))
    analysis=dict(frozen_sample_ids=frozen["sample_ids"],planned=len(schedule),arithmetic_scored=len([a for a in audit if a["arithmetic_matches"] is True]),
                  arithmetic_mismatches=[a["combination_id"] for a in audit if a["arithmetic_matches"] is False],
                  nli_text_changed_combinations=[a["combination_id"] for a in audit if a.get("nli_exact_statement_list_preserved") is False],
                  nli_count_changed_combinations=[a["combination_id"] for a in audit if a.get("nli_statement_count_preserved") is False],
                  latency=latency,tokens=tokens,unweighted_ensemble_descriptive=ensemble,
                  labels=None,accuracy=None,false_positive_rate=None,model_quality_ranking=None,
                  sample_mean_is_not_historical_gate=True)
    pilot.write_json(OUT/"analysis.json",analysis)
    lines=["# 多裁判冻结回答试跑报告（2026-10-21）","",
           f"完成 {state['attempted_combinations']}/60 个组合，获得 {state['finite_scores']} 个可解析的数值评分（不代表语义正确）。未重新生成回答。",
           f"按冻结最高价估算费用 ¥{state['estimated_ceiling_cost_cny']}；用量不明的占用预留 ¥{state['unknown_usage_reserved_cny']}；本轮上限 ¥5。无账单，不能称为实付。","",
           f"模型请求 {state['reserved_requests']} 次，已返回 {state['completed_http_responses']} 次；输入 {tokens['prompt_tokens']} tokens，输出 {tokens['completion_tokens']} tokens，用量未知请求 {tokens['unknown_usage_requests']} 次。","",
           "## 范围与方法","",
           "只读使用 20261021 AnythingLLM 正式归档的已评测回答，20条正向样本按固定种子与题号哈希排序选10条，未依据原分数选题。",
           "样本："+"、".join(frozen["sample_ids"])+"。",
           "Qwen2.5-72B、DeepSeek Flash、GLM-4-32B 各重复2次；每次独立进行陈述抽取与NLI。冻结原ChineseFaithfulness提示、解析与公式；不修复转义格式、不运行其他指标或Trace。",
           "配置为temperature=0、max_tokens=2048、每阶段最多一次请求，无解析修复、无HTTP重试。与历史完整评测配置存在差异，不能据此替换旧分数。",
           "每次请求前按完整上下文上限与最大输出的最高价持久化预留，完整usage返回后才释放差额；缺失usage保留预留。DeepSeek缓存与闲时优惠未用于降低该估算。","",
           "## 实测结果","",
           "| 裁判 | 第1次10题均值 | 第2次10题均值 | 0.8阈值（仅子集） | 题级评分变化 | 估算费用上限 |",
           "|---|---:|---:|---|---:|---:|"]
    for name,m in state["model_results"].items():
        a,b=m["repetitions"]["1"],m["repetitions"]["2"]
        fmt=lambda x:f"{x:.6f}" if x is not None else "未测完整"
        pairs=m["paired_cases"]
        changed=sum(p["absolute_difference"]>0 for p in pairs)
        lines.append(f"| {name} | {fmt(a['all_ten_mean'])} | {fmt(b['all_ten_mean'])} | {a['exploratory_subset_gate_at_0_8']} / {b['exploratory_subset_gate_at_0_8']} | {changed}/{len(pairs)} | ¥{m['estimated_ceiling_cost_cny']} |")
    lines += ["", "三裁判简单平均（仅描述）："+"；".join(f"第{x['repeat']}次={x['mean']:.6f}，{x['subset_gate_at_0_8']}" if x['mean'] is not None else f"第{x['repeat']}次未完成" for x in ensemble)+"。",
              f"数值评分算术独立复核：{analysis['arithmetic_scored']}项一致；不一致项：{analysis['arithmetic_mismatches']}。",
              f"机械字段核对：{len(analysis['nli_text_changed_combinations'])}个组合的NLI返回陈述文本与抽取列表不完全相同，{len(analysis['nli_count_changed_combinations'])}个组合的条数不同。文本差异不自动等同语义错误，本轮未改原评分或过滤这些组合。",
              "表中保留原流程的全部数值，包括上述中间步骤异常；这些探索性PASS不是经新增契约校验后可用于放行的结论。",
              f"HTTP响应耗时：p50={latency['p50_seconds']}秒，p95（最近秩、描述性）={latency['p95_nearest_rank_seconds']}秒。", "",
              "## 解释边界","",
              "这轮能证明冻结回答可不重新生成而被独立重评分，并测得调用用量与重复结果。不能证明多数或平均裁判更正确，也不能证明减少误报。",
              "算术复核只说明分子分母计算一致，不证明抽取忠实、NLI语义正确或评分能代表回答整体质量。",
              "每次陈述抽取和NLI使用同一裁判，跨裁判差异同时包含抽取陈述集合差异与蕴含判定差异，不能只归因于NLI裁判质量。",
              "无独立正确性标签，accuracy、真实误报率与裁判质量排名均为空。两次重复与单批10题不支持稳定性总体估计或显著性结论。",
              "均值基于这10条正向子集，不是原正式20条正向样本的门禁；其PASS/FAIL仅为固定0.8阈值的探索性观察。原正式FAIL与归档分数不变。",
              "裁判服务名可指向更新后的模型权重，实际返回model/system_fingerprint保留在原始响应中，不宣称锁定不可变模型。", "",
              "## 失败与隔离","",
              "首个目录 judge-gate-pilot-20261021 的60个组合均在本地组装记录时因重复关键字失败，模型请求为0、费用0。没有删除这些失败。修复后在本目录r2用相同样本重新执行；两目录不是两轮付费采样。",
              "r2离线安全检查10项通过，含真实适配器的模拟HTTP路径与独占锁；不是效果准确率测试。",
              f"受保护文件哈希一致：{state['protected_files_unchanged']}；Git状态前后一致：{state['git_status_unchanged']}。未安装依赖、未改生产代码/配置、未提交或推送。", "",
              "若Git状态不一致，只能将工作区整体隔离项记为未通过。运行期间其他路径的新增改动见final-audit.json，本任务未写入或撤销这些路径；不能从状态变化直接推断改动者。", "",
              "## 复核入口","",
              "- freeze.json / inputs.jsonl / schedule.json / prompts.json：预先固定的范围与输入。",
              "- pricing.json / budget-ledger.jsonl：价表、逐请求预留与usage，实际账单为空。",
              "- requests/：准确请求体、原始响应；不保存Authorization。",
              "- results.jsonl / arithmetic-audit.json / analysis.json / summary.json：结果与离线复算。",
              "- final-audit.json：120次请求的费用、输入/提示/响应哈希复核，以及Git状态前后差异。",
              "- offline-tests.txt / console.log：原始检查与运行记录。", ""]
    (OUT/"完成报告.md").write_bytes(pilot.checked("\n".join(lines).encode()))
    print(json.dumps(dict(attempted=state["attempted_combinations"],finite=state["finite_scores"],arithmetic_checked=analysis["arithmetic_scored"],estimated_ceiling_cost_cny=state["estimated_ceiling_cost_cny"],held_cny=state["unknown_usage_reserved_cny"],unchanged=state["protected_files_unchanged"] and state["git_status_unchanged"]),ensure_ascii=False))


if __name__=="__main__":main()
