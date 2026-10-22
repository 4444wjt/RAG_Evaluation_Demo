"""事后法条归属扩展：保留原规则，另列项级发现与逐层回指。纯函数，不调用模型。"""
from __future__ import annotations

from collections import defaultdict

from backend.core import statute_check as sc


def _rank(claim, groups):
    """稳定排序仅用于展示；并列或差距不足时不选择位置。"""
    scores = sorted(((key, sc._coverage(claim, texts)) for key, texts in groups.items()),
                    key=lambda pair: (-pair[1], pair[0]))
    if not scores:
        return {"best": None, "coverage": None, "runnerUpCoverage": None}
    key, coverage = scores[0]
    runner = scores[1][1] if len(scores) > 1 else 0.0
    certain = coverage >= sc.ATTRIBUTION_MIN and coverage - runner >= sc.ATTRIBUTION_GAP
    return {"best": key if certain else None, "coverage": coverage,
            "runnerUpCoverage": runner}


def _groups(provisions, field):
    groups = defaultdict(list)
    for provision in provisions:
        key = getattr(provision, field)
        if key is not None:
            groups[key].append(provision.text)
    return groups


def locate_claim(text: str, provisions: list[sc.Provision]) -> dict:
    """逐层比较条、款、项；祖先不确定则停止。每项只使用该项正文。

    coverage 即该层最高覆盖率，即使位置不确定也保留以供审计；未计算层为 None。
    recovered 只写已确定的前缀，不把部分回指冒充完整款项定位。
    """
    claim = sc._bigrams(sc.REF.sub("", sc.normalize(text)))
    levels = {name: {"best": None, "coverage": None, "runnerUpCoverage": None}
              for name in ("article", "paragraph", "item")}
    remaining = list(provisions)
    for name in levels:
        levels[name] = _rank(claim, _groups(remaining, name))
        best = levels[name]["best"]
        if best is None:
            break
        remaining = [p for p in remaining if getattr(p, name) == best]
    location = {name: level["best"] for name, level in levels.items()}
    recovered = (sc._ref_label(location["article"], location["paragraph"], location["item"])
                 if location["article"] is not None else None)
    return {"location": location, "recovered": recovered, "levels": levels}


def _sentences(answer, quotes):
    for sentence in sc.SENTENCE.finditer(answer):
        refs = [m for m in sc.REF.finditer(sentence.group())
                if not sc._inside(sentence.start() + m.start(), quotes)]
        yield sentence.group(), refs


def _item_findings(sentences, provisions):
    findings = []
    for text, refs in sentences:
        if not refs:
            continue
        article, paragraph, item = sc._ref_value(refs[0])
        if item is None or not any(sc._matches(p, article, paragraph, item) for p in provisions):
            continue
        # 未写款号时仍不假定第一款；项号在同条多款重复则不能唯一确定所引项的款。
        parents = {p.paragraph for p in provisions if sc._matches(p, article, paragraph, item)}
        if len(parents) != 1:
            continue
        parent = next(iter(parents))
        own = [p for p in provisions if p.article == article and p.paragraph == parent and p.item is not None]
        claim = sc._bigrams(sc.REF.sub("", text))
        scores = {i: sc._coverage(claim, texts) for i, texts in _groups(own, "item").items()}
        near = max(sorted(scores), key=scores.get)
        mentioned = set().union(*(sc._mentions(p, provisions) for p in own if p.item == near))
        # 与 _check_refs 完全相同：最佳者对所引者的差值，而非修改成第一、二名差值。
        exempt = (article, parent) in mentioned or (article, None) in mentioned
        if (near != item and scores[near] >= sc.ATTRIBUTION_MIN
                and scores[item] <= scores[near] - sc.ATTRIBUTION_GAP and not exempt):
            cited = sc._ref_label(article, paragraph, item)
            located = sc._ref_label(article, parent, near)
            findings.append({"code": "ITEM_MISATTRIBUTED", "severity": "error",
                             "title": "内容出自同款另一项，与所引项不符", "text": text.strip(),
                             "detail": f"所引为{cited}，内容最接近{located}（覆盖 {scores[near]:.2f}，所引 {scores[item]:.2f}）",
                             "cited": cited, "located": located})
    return findings


def check_answer_extended(answer: str, contexts: list[str], source: str, question: str = "") -> dict:
    """original 原样返回；extensionFindings 与 recoveries 并行存放，避免改写原发现。"""
    original = sc.check_answer(answer, contexts, source, question)
    normalized = sc.normalize(answer)
    provisions = sc.parse_provisions(source)
    quotes = sc._quoted_spans(normalized, [source, *[sc.normalize(c) for c in contexts or []]])
    sentences = list(_sentences(normalized, quotes))
    extension = _item_findings(sentences, provisions)
    recoveries = []
    for origin, findings in (("original", original["findings"]), ("extension", extension)):
        occurrences = defaultdict(int)
        for index, finding in enumerate(findings):
            code = finding["code"]
            if finding["severity"] != "error" or code not in (
                    "ARTICLE_NOT_IN_SOURCE", "PROVISION_NOT_IN_SOURCE", "MISATTRIBUTED", "ITEM_MISATTRIBUTED"):
                continue
            if code in ("ARTICLE_NOT_IN_SOURCE", "PROVISION_NOT_IN_SOURCE"):
                matches = [text for text, refs in sentences for ref in refs if ref.group() == finding["text"]]
            else:
                matches = [text for text, _ in sentences if text.strip() == finding["text"]]
            key = (code, finding["text"])
            text = matches[occurrences[key]]
            occurrences[key] += 1
            recoveries.append({"origin": origin, "findingIndex": index, "code": code,
                               "text": finding["text"], "sentence": text.strip(),
                               **locate_claim(text, provisions)})
    return {"original": original, "extensionFindings": extension, "recoveries": recoveries,
            "flagged": original["flagged"] or bool(extension)}
