"""鲁棒性体检：对任意 RAG 应用的检索资料做受控改动，检验生成与核对两侧是否真的依据资料。

把 RGB 的三种情形（加噪、应拒答、反事实）和证据敏感性实验（门禁可信度第三问）合成一套可复用的流程。
每道题构造五种资料条件：
  原样      资料不变，作为同一次运行内的对照；
  打乱      只改变资料顺序：结论不应改变；
  加噪      插入其他题目的资料（与本题依据无逐字重合）：结论不应改变；
  移除依据  去掉含依据的资料，用其他题目的资料补足条数：生成应拒答，核对应标出无依据；
  反事实    只改依据原文里的数字和情态词（应当、可以、包括……）：核对应标出矛盾，生成是否跟随改动单独统计。
同一套资料分别用于两侧：
  核对侧    原回答不变，换资料重新逐句核对，看裁判是否随证据改判（第三问的自动化、可复用版本）；
  生成侧    用改动后的资料重新生成回答再核对，看被测应用会不会拒答、会不会被噪声带偏、跟不跟随资料。

依据位置不用模型判断：取回答与参考答案中和资料逐字重合、不少于 MIN_SHARED 个字、且在全部资料中
出现不超过 DF_RATIO 比例的片段（排除「存款保险基金管理机构」这类到处都有的名称），含这些片段的资料即依据资料。
构造过程与被检的裁判无关，同一输入、同一种子总得到同一套材料，付费运行前可以逐条检查。

纯函数：不调用模型、不读写文件、不修改输入；只依赖标准库。
"""
from collections import Counter
from difflib import SequenceMatcher
import math
import random
import re

VERSION = "robustness-v1"
CONDITIONS = ("original", "shuffle", "noise", "remove", "counterfactual")
CONDITION_LABELS = {"original": "原样", "shuffle": "打乱", "noise": "加噪", "remove": "移除依据", "counterfactual": "反事实"}
MIN_SHARED = 5
DF_RATIO = 0.2
NOISE_COUNT = 2
SIDES = ("judge", "generator")

PUNCT = "，。、；：！？（）《》“”‘’,.;:!?()[]【】—…·　 \n\t"
_NUMBER = r"(?<![第\d.])\d+(?:\.\d+)?(?![\d.])"
# 先列带否定的写法，保证「不包括」整体改为「包括」，而不是把其中的「包括」再改一次。
FLIPS = {"不包括": "包括", "不适用": "适用", "不属于": "属于", "不得": "可以", "无权": "有权", "无需": "应当",
         "应当": "无需", "必须": "无需", "可以": "不得", "有权": "无权", "包括": "不包括", "属于": "不属于"}
_TOKEN = re.compile(f"(?P<number>{_NUMBER})|(?P<modal>{'|'.join(FLIPS)})")


def _content_length(text):
    return sum(1 for char in text if char not in PUNCT)


def shared_spans(text, passage, min_len=MIN_SHARED):
    """text 与 passage 逐字重合的片段：每次取最长的一段并遮住，直到短于 min_len；首尾标点去掉。"""
    left, right, spans = list(text or ""), list(passage or ""), []
    matcher = SequenceMatcher(None, autojunk=False)
    while True:
        matcher.set_seqs(left, right)
        match = matcher.find_longest_match(0, len(left), 0, len(right))
        if match.size < min_len:
            return spans
        span = passage[match.b:match.b + match.size].strip(PUNCT)
        if _content_length(span) >= min_len:
            spans.append(span)
        left[match.a:match.a + match.size] = ["\0"] * match.size
        right[match.b:match.b + match.size] = ["\1"] * match.size


def passage_pool(items):
    """全部题目资料去重后的集合，按首次出现顺序；用于计算片段的出现比例，也是加噪资料的来源。"""
    pool = []
    for item in items:
        for passage in item["contexts"]:
            if passage not in pool:
                pool.append(passage)
    return pool


def df_limit(pool, ratio=DF_RATIO):
    return max(1, int(ratio * len(pool)))


def locate_evidence(item, pool, min_len=MIN_SHARED, ratio=DF_RATIO):
    """定位一道题的依据：回答与参考答案中，和本题资料逐字重合且在资料库中少见的片段。

    返回 {"spans": [...], "supportIndices": [...]}；两个都可能为空（例如回答是拒答且没有参考答案）。
    """
    limit = df_limit(pool, ratio)
    spans = []
    for target in (item.get("answer"), item.get("groundTruth")):
        if not target:
            continue
        for passage in item["contexts"]:
            for span in shared_spans(target, passage, min_len):
                if span not in spans and sum(span in p for p in pool) <= limit:
                    spans.append(span)
    support = [i for i, passage in enumerate(item["contexts"]) if any(span in passage for span in spans)]
    return {"spans": spans, "supportIndices": support}


def _rng(seed, item_id, condition):
    # 字符串种子在 Python 3 中经 SHA-512 展开，与进程哈希随机化无关，跨平台可复现。
    return random.Random(f"{seed}:{item_id}:{condition}")


def distractors(item, pool, spans, min_len=MIN_SHARED, ratio=DF_RATIO):
    """可用作噪声的资料：不在本题资料中、不含依据片段，且与回答和参考答案没有少见的逐字重合。

    与定位依据用同一标准：到处都有的名称（出现比例超过 ratio）不算重合，否则同一份文档切出的资料全都会被排除。
    """
    limit = df_limit(pool, ratio)
    targets = [t for t in (item.get("answer"), item.get("groundTruth")) if t]

    def related(passage):
        if any(span in passage for span in spans):
            return True
        return any(sum(s in p for p in pool) <= limit for t in targets for s in shared_spans(t, passage, min_len))

    return [p for p in pool if p not in item["contexts"] and not related(p)]


def shuffled(item, seed):
    contexts = list(item["contexts"])
    if len(set(contexts)) < 2:
        return {"skipped": "资料少于两条不同的内容，无法打乱"}
    rng = _rng(seed, item["id"], "shuffle")
    order = list(range(len(contexts)))
    for _ in range(20):
        rng.shuffle(order)
        if [contexts[i] for i in order] != contexts:
            break
    else:
        order = order[::-1]
    return {"contexts": [contexts[i] for i in order], "order": order}


def noised(item, candidates, seed, count=NOISE_COUNT):
    if len(candidates) < count:
        return {"skipped": f"可用的无关资料少于 {count} 条"}
    rng = _rng(seed, item["id"], "noise")
    chosen = rng.sample(candidates, count)
    contexts = list(item["contexts"])
    for passage in chosen:
        contexts.insert(rng.randint(0, len(contexts)), passage)
    return {"contexts": contexts, "inserted": [i for i, p in enumerate(contexts) if p in chosen]}


def removed(item, candidates, support, seed):
    if not support:
        return {"skipped": "未能定位依据资料"}
    need = len(support)
    if len(candidates) < need:
        return {"skipped": "可用于补足条数的无关资料不足"}
    fill = _rng(seed, item["id"], "remove").sample(candidates, need)
    contexts, filled = list(item["contexts"]), iter(fill)
    for index in support:
        contexts[index] = next(filled)
    return {"contexts": contexts, "removed": list(support)}


def _edit_number(match, text):
    token = match.group(0)
    if "." in token:
        decimals = len(token.split(".")[1])
        return f"{float(token) * 2:.{decimals}f}"
    value = int(token)
    if text[match.end():match.end() + 1] == "年" and 1900 <= value <= 2100:
        return str(value + 1)
    return str(value * 2) if value else "1"


def _unit(text, end):
    """数字后的单位（万元、个月、年、% 等），用于在生成的回答里辨认改动前后的数值。"""
    unit = text[end:end + 1]
    if not unit or unit.isdigit() or unit in PUNCT:
        return ""
    following = text[end + 1:end + 2]
    return unit + following if unit in "万亿千百个" and following and not following.isdigit() and following not in PUNCT else unit


def counterfactual(item, spans):
    """只改依据片段内的数字（翻倍，年份加一）和情态词（应当→无需、可以→不得、包括→不包括……）。"""
    if not spans:
        return {"skipped": "未能定位依据资料"}
    contexts, edits = [], []
    for index, passage in enumerate(item["contexts"]):
        covered = [False] * len(passage)
        for span in spans:
            start = passage.find(span)
            while start != -1:
                covered[start:start + len(span)] = [True] * len(span)
                start = passage.find(span, start + 1)
        pieces, last = [], 0
        for match in _TOKEN.finditer(passage):
            if not all(covered[match.start():match.end()]):
                continue
            kind = "number" if match.group("number") else "modal"
            after = _edit_number(match, passage) if kind == "number" else FLIPS[match.group(0)]
            unit = _unit(passage, match.end()) if kind == "number" else ""
            pieces += [passage[last:match.start()], after]
            last = match.end()
            edits.append({"context": index, "kind": kind, "before": match.group(0) + unit, "after": after + unit})
        contexts.append("".join(pieces) + passage[last:])
    if not edits:
        return {"skipped": "依据中没有可改的数字或情态词"}
    return {"contexts": contexts, "edits": edits}


def build_item(item, pool, seed, noise_count=NOISE_COUNT, min_len=MIN_SHARED, ratio=DF_RATIO):
    """为一道题构造五种资料条件；构造不了的条件写明原因，不硬造。"""
    evidence = locate_evidence(item, pool, min_len, ratio)
    candidates = distractors(item, pool, evidence["spans"], min_len, ratio)
    conditions = {
        "original": {"contexts": list(item["contexts"])},
        "shuffle": shuffled(item, seed),
        "noise": noised(item, candidates, seed, noise_count),
        "remove": removed(item, candidates, evidence["supportIndices"], seed),
        "counterfactual": counterfactual(item, evidence["spans"]),
    }
    return {**item, "evidence": evidence, "conditions": conditions}


def plan(items, seed, noise_count=NOISE_COUNT, min_len=MIN_SHARED, ratio=DF_RATIO, refusal=None):
    """构造全部题目的材料与调用清单。refusal(answer) 判断原回答是否为拒答：拒答没有可核对的陈述，核对侧跳过。"""
    ids = [item["id"] for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError("题目编号重复")
    pool = passage_pool(items)
    built = [build_item(item, pool, seed, noise_count, min_len, ratio) for item in items]
    jobs = []
    for item in built:
        judgeable = bool(item.get("answer")) and not (refusal and refusal(item["answer"]))
        for condition in CONDITIONS:
            if "contexts" not in item["conditions"][condition]:
                continue
            if judgeable:
                jobs.append({"id": f"{item['id']}:judge:{condition}", "item": item["id"], "side": "judge", "condition": condition})
            jobs.append({"id": f"{item['id']}:generator:{condition}", "item": item["id"], "side": "generator", "condition": condition})
    return {"items": built, "jobs": jobs, "poolSize": len(pool), "dfLimit": df_limit(pool, ratio)}


# ---------- 统计 ----------

TRACE_FAILED = ("call_error", "parse_error", "invalid_structure")


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(centre - half, 4), round(centre + half, 4)]


def rate(hits, total):
    return {"count": hits, "total": total, "rate": round(hits / total, 4) if total else None, "wilson95": wilson(hits, total)}


def trace_outcome(trace):
    """核对结果归为：failed（调用或解析失败）、flagged（有矛盾或无依据）、clean（全部支持）、empty（没有留下陈述）。"""
    if not trace or trace.get("trace_status") in TRACE_FAILED:
        return "failed"
    verdicts = [s.get("verdict") for s in trace.get("statements") or []]
    if any(v in ("fact_error", "fabrication") for v in verdicts):
        return "flagged"
    return "clean" if verdicts else "empty"


def adoption(answer, edits, refused):
    """反事实条件下生成侧是否跟随改动；只看数字改动（情态词的改写方式太多，不做字面判断）。"""
    numbers = [e for e in edits if e["kind"] == "number"]
    if not numbers:
        return "not_applicable"
    if refused:
        return "refused"
    followed = any(e["after"] in answer for e in numbers)
    kept = any(e["before"] in answer for e in numbers)
    return {(True, False): "followed", (False, True): "kept", (True, True): "both", (False, False): "neither"}[(followed, kept)]


def outcomes(package_items, records, refusal):
    """把调用记录整理成逐题、逐条件的结果；refusal(answer) 为拒答判断函数。"""
    items = {item["id"]: item for item in package_items}
    rows = {}
    for record in records:
        item = items[record["item"]]
        edits = item["conditions"]["counterfactual"].get("edits", [])
        row = rows.setdefault(record["item"], {
            "id": record["item"], "question": item["question"], "judge": {}, "generator": {},
            "editKind": None if not edits else "number" if any(e["kind"] == "number" for e in edits) else "modal"})
        trace = record.get("trace") if record.get("status") == "ok" else None
        outcome = {"status": record.get("status"), "trace": trace_outcome(trace),
                   "traceStatus": (trace or {}).get("trace_status", record.get("status")),
                   "contradicted": any(s.get("verdict") == "fact_error" for s in (trace or {}).get("statements") or []),
                   "rejectedEvidence": sum(1 for r in ((trace or {}).get("trace_diagnostics") or {}).get("rejected_statements") or []
                                           if r.get("reason") == "evidence_not_in_context")}
        if record["side"] == "generator":
            answer = record.get("answer")
            outcome["answered"] = isinstance(answer, str)
            outcome["refused"] = bool(outcome["answered"] and refusal(answer))
            if record["condition"] == "counterfactual" and outcome["answered"]:
                outcome["adoption"] = adoption(answer, item["conditions"]["counterfactual"]["edits"], outcome["refused"])
        row[record["side"]][record["condition"]] = outcome
    order = [item["id"] for item in package_items]
    return sorted(rows.values(), key=lambda row: order.index(row["id"]))


def _usable(outcome):
    return outcome is not None and outcome["trace"] != "failed"


def summarize_judge(rows):
    """核对侧：原回答不变，只换资料。以同一次运行的原样核对为对照。"""
    result = {}
    baseline = [r for r in rows if _usable(r["judge"].get("original"))]
    result["original"] = {"flagged": rate(sum(r["judge"]["original"]["trace"] == "flagged" for r in baseline), len(baseline))}
    for condition in ("shuffle", "noise"):
        pairs = [r for r in baseline if _usable(r["judge"].get(condition))]
        same = sum((r["judge"][condition]["trace"] == "flagged") == (r["judge"]["original"]["trace"] == "flagged") for r in pairs)
        newly = sum(r["judge"]["original"]["trace"] != "flagged" and r["judge"][condition]["trace"] == "flagged" for r in pairs)
        result[condition] = {"unchanged": rate(same, len(pairs)), "newlyFlagged": newly}
    for condition in ("remove", "counterfactual"):
        pairs = [r for r in baseline if _usable(r["judge"].get(condition))]
        eligible = [r for r in pairs if r["judge"]["original"]["trace"] != "flagged"]
        flagged = [r for r in eligible if r["judge"][condition]["trace"] == "flagged"]
        entry = {"flagged": rate(len(flagged), len(eligible)), "baselineAlreadyFlagged": len(pairs) - len(eligible),
                 "rejectedEvidence": sum(r["judge"][condition]["rejectedEvidence"] for r in pairs)}
        if condition == "counterfactual":
            entry["contradicted"] = rate(sum(r["judge"][condition]["contradicted"] for r in eligible), len(eligible))
            entry["byEdit"] = {kind: rate(sum(r["judge"][condition]["trace"] == "flagged" for r in eligible if r["editKind"] == kind),
                                          sum(1 for r in eligible if r["editKind"] == kind))
                               for kind in ("number", "modal")}
        result[condition] = entry
    result["failed"] = sum(1 for r in rows for o in r["judge"].values() if o["trace"] == "failed")
    return result


def summarize_generator(rows):
    """生成侧：用改动后的资料重新作答。拒答、核对标出、反事实跟随分别统计。"""
    result = {}
    for condition in CONDITIONS:
        group = [r["generator"][condition] for r in rows if condition in r["generator"]]
        answered = [o for o in group if o["answered"]]
        substantive = [o for o in answered if not o["refused"] and o["trace"] != "failed"]
        flagged = sum(o["trace"] == "flagged" for o in substantive)
        entry = {"items": len(group), "answerFailed": len(group) - len(answered),
                 "refused": rate(sum(o["refused"] for o in answered), len(answered)),
                 "answeredFlagged": rate(flagged, len(substantive)), "answeredNotFlagged": len(substantive) - flagged}
        if condition == "counterfactual":
            counted = Counter(o.get("adoption") for o in answered if o.get("adoption") not in (None, "not_applicable"))
            total = sum(counted.values())
            entry["adoption"] = {key: rate(counted.get(key, 0), total) for key in ("followed", "kept", "both", "neither", "refused")}
            # 沿用原值的回答与改动后的资料矛盾：核对应当标出
            kept = [o for o in substantive if o.get("adoption") in ("kept", "both")]
            entry["flaggedWhenKept"] = rate(sum(o["trace"] == "flagged" for o in kept), len(kept))
        result[condition] = entry
    original = {r["id"]: r["generator"].get("original") for r in rows}
    for condition in ("shuffle", "noise", "remove", "counterfactual"):
        pairs = [(original[r["id"]], r["generator"][condition]) for r in rows
                 if condition in r["generator"] and original[r["id"]] and original[r["id"]]["answered"] and r["generator"][condition]["answered"]]
        result[condition]["newlyRefused"] = sum(not a["refused"] and b["refused"] for a, b in pairs)
        result[condition]["pairedWithOriginal"] = len(pairs)
    return result


def summarize(package_items, records, refusal):
    rows = outcomes(package_items, records, refusal)
    built = {condition: sum("contexts" in item["conditions"][condition] for item in package_items) for condition in CONDITIONS}
    skipped = {condition: dict(Counter(item["conditions"][condition]["skipped"] for item in package_items
                                       if "skipped" in item["conditions"][condition])) for condition in CONDITIONS}
    edits = Counter(e["kind"] for item in package_items for e in item["conditions"]["counterfactual"].get("edits", []))
    return {"items": len(package_items), "built": built, "skipped": skipped, "edits": dict(edits),
            "judge": summarize_judge(rows), "generator": summarize_generator(rows), "rows": rows}
