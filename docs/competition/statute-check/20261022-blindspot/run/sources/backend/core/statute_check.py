"""法条结构核对：回答里的条、款、项引用，数额期限与情态词，是否与法规原文的结构一致。

用法规原文（RAG 应用所索引的文件）还原条、款、项结构，给缺少条号标题的检索片段补回所属条款，
再逐句核对。纯函数：不调用模型、不读写文件、不参与门禁。门禁仍只读 pos_faithfulness 与
pos_context_precision（见 gate.py），本模块的结果是独立的诊断通道。

只认 error 级发现为「检出」；warn、info 只作提示。规则覆盖范围见 RULES，范围之外的错误
（例如「适用 / 不适用」的极性反转）本模块不负责。

依赖仅限标准库。
"""
from __future__ import annotations

from dataclasses import dataclass
import re

NUM = "[0-9零〇一二三四五六七八九十百千两]+"
_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000}
_WIDTH = str.maketrans({**{chr(0xFF10 + i): str(i) for i in range(10)}, "％": "%", "．": "."})

HEADER = re.compile(rf"^第({NUM})条[　 ]*(.*)$")
ITEM = re.compile(r"^（([一二三四五六七八九十]+)）")
REF = re.compile(rf"第({NUM})条(?:第({NUM})款)?(?:第({NUM})项)?")
RELATIVE = re.compile(r"前款|前两款|本款|本条|前条|上一条")
QUANTITY = re.compile(rf"({NUM}(?:\.[0-9]+)?)\s*(万元|亿元|元|%|个工作日|工作日|个自然日|自然日|日|天|个月|年|倍|小时)")
UNIT = {"万元": "万元", "亿元": "亿元", "元": "元", "%": "%", "个工作日": "工作日", "工作日": "工作日",
        "个自然日": "日", "自然日": "日", "日": "日", "天": "日", "个月": "月", "年": "年", "倍": "倍", "小时": "小时"}
CONTRAST = ("而非", "并非", "不是", "而不是")
MODALS = ("应当", "可以", "不得", "必须", "禁止", "不应", "有权")
NEGATION = re.compile(r"(?:并未|未曾|没有|未)(?:规定|写明|涉及|提及|明确)")
TOPICS = {"时限": "period", "期限": "period", "时间": "period", "日内": "period",
          "限额": "money", "金额": "money", "数额": "money", "费率": "rate", "比例": "rate"}
UNIT_CLASS = {"工作日": "period", "日": "period", "月": "period", "年": "period", "小时": "period",
              "元": "money", "万元": "money", "亿元": "money", "%": "rate"}
SENTENCE = re.compile(r"[^。；;！？!?\n]+")
CLAUSE = re.compile(r"[^，,。；;！？!?\n]+")
QUOTE_MIN = 10     # 与原文或片段逐字相同、不短于此长度的片段视为引用原文，其中的「前款」等不算回答自己的引用
LOCATE_MIN = 8     # 片段的一行不短于此长度且逐字出现在某款中，才算覆盖该款（片段可能截断）
# 内容归属：句子的字符二元组被各条（款）覆盖的比例。最接近的条款覆盖率不低于 ATTRIBUTION_MIN，
# 且所引条款比它低 ATTRIBUTION_GAP 以上，才判为归属不符。
ATTRIBUTION_MIN = 0.6
ATTRIBUTION_GAP = 0.25
FILLER = re.compile(r"根据|依据|按照|参考资料|提供的文档|所提供的|资料|节选|规定|本条例|的")

RULES = {
    "ARTICLE_NOT_IN_SOURCE": ("error", "所引条号不在法规原文中"),
    "PROVISION_NOT_IN_SOURCE": ("error", "所引款、项超出该条的结构"),
    "MISATTRIBUTED": ("error", "内容出自另一条款，与所引条款不符"),
    "NEGATION_CONTRADICTED": ("error", "称某条未规定某类数额或期限，但该条有"),
    "UNIT_MISMATCH": ("error", "数值相同而单位与资料不同"),
    "QUANTITY_UNSUPPORTED": ("error", "数额或期限在资料与题目中都找不到"),
    "MODAL_SWAP": ("error", "情态词与资料不同（如应当 / 可以）"),
    "ARTICLE_NOT_RETRIEVED": ("warn", "所引条款在原文中，但检索片段里没有它的内容"),
    "FROM_QUESTION": ("info", "数额来自题目条件"),
    "DERIVED": ("info", "数额由题目或资料中的数额加减得到"),
    "RELATIVE_REF": ("info", "回答自己用了「前款」「本条」等相对引用，未解析"),
}


def normalize(text: str) -> str:
    return (text or "").translate(_WIDTH)


def cn_number(text: str) -> int | None:
    """「十九」→19，「二十一」→21，「一百零五」→105；阿拉伯数字原样。不认识的字符返回 None。"""
    if text.isdigit():
        return int(text)
    total, current = 0, 0
    for char in text:
        if char in _DIGITS:
            current = _DIGITS[char]
        elif char in _UNITS:
            total += (current or 1) * _UNITS[char]
            current = 0
        else:
            return None
    return total + current


def cn_label(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" + (digits[value % 10] if value % 10 else "")
    if value < 100:
        return digits[value // 10] + "十" + (digits[value % 10] if value % 10 else "")
    hundreds, rest = divmod(value, 100)
    tail = "" if rest == 0 else ("零" + digits[rest] if rest < 10 else cn_label(rest) if rest >= 20 else "一" + cn_label(rest))
    return digits[hundreds] + "百" + tail


@dataclass(frozen=True)
class Provision:
    article: int
    paragraph: int
    item: int | None
    text: str

    @property
    def label(self) -> str:
        item = f"第{cn_label(self.item)}项" if self.item else ""
        return f"第{cn_label(self.article)}条第{cn_label(self.paragraph)}款{item}"


def parse_provisions(text: str) -> list[Provision]:
    """按「第N条」标题切条；条内每一行是一款，「（一）」开头的行是上一款的项。空行结束一条。"""
    provisions, article, paragraph = [], None, 0
    for raw in normalize(text).splitlines():
        line = raw.strip()
        if not line:
            article = None
            continue
        head = HEADER.match(line)
        if head:
            article, paragraph = cn_number(head.group(1)), 1
            provisions.append(Provision(article, 1, None, head.group(2).strip()))
            continue
        if article is None:
            continue
        item = ITEM.match(line)
        if item:
            provisions.append(Provision(article, paragraph, cn_number(item.group(1)), line))
            continue
        paragraph += 1
        provisions.append(Provision(article, paragraph, None, line))
    return provisions


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", normalize(text))


def locate(context: str, provisions: list[Provision]) -> list[Provision]:
    """检索片段覆盖了哪些条款。片段可能在句中截断、缺少条号标题，按逐字包含双向匹配。"""
    flat = _compact(context)
    lines = [_compact(x) for x in normalize(context).splitlines()]
    found = []
    for p in provisions:
        body = _compact(p.text)
        if (body and body in flat) or any(len(x) >= LOCATE_MIN and x in body for x in lines):
            found.append(p)
    return found


def _quantities(text: str) -> list[tuple[int, int, float, str]]:
    found = []
    for m in QUANTITY.finditer(normalize(text)):
        raw = m.group(1)
        value = float(raw) if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw) else cn_number(raw)
        if value is None:
            continue
        # 「第十九条」「十九日」之类：数字前紧挨「第」的是序号，不是数额
        if m.start() > 0 and normalize(text)[m.start() - 1] == "第":
            continue
        found.append((m.start(), m.end(), float(value), UNIT[m.group(2)]))
    return found


def _quantity_set(texts) -> set[tuple[float, str]]:
    return {(value, unit) for text in texts for _, _, value, unit in _quantities(text)}


def _bigrams(text: str) -> set[str]:
    flat = re.sub(r"[^\w]", "", FILLER.sub("", _compact(text)))
    return {flat[i:i + 2] for i in range(len(flat) - 1)}


def _coverage(claim: set[str], texts) -> float:
    pool = set().union(*(_bigrams(t) for t in texts)) if texts else set()
    return len(claim & pool) / len(claim) if claim else 0.0


def _quoted_spans(answer: str, corpus: list[str]) -> list[tuple[int, int]]:
    """回答中与原文或片段逐字相同、长度不少于 QUOTE_MIN 的区间。"""
    pool = [_compact(x) for x in corpus if x]
    spans, i = [], 0
    while i < len(answer):
        j = i
        while j < len(answer) and any(answer[i:j + 1] in text for text in pool):
            j += 1
        if j - i >= QUOTE_MIN:
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def _inside(position: int, spans) -> bool:
    return any(start <= position < end for start, end in spans)


def _finding(code: str, text: str, detail: str, **extra) -> dict:
    severity, title = RULES[code]
    return {"code": code, "severity": severity, "title": title, "text": text, "detail": detail, **extra}


def _ref_value(match) -> tuple[int, int | None, int | None]:
    article, paragraph, item = (cn_number(g) if g else None for g in match.groups())
    return article, paragraph, item


def _matches(p: Provision, article, paragraph, item) -> bool:
    return p.article == article and (paragraph is None or p.paragraph == paragraph) and (item is None or p.item == item)


def _mentions(provision: Provision, provisions: list[Provision]) -> set[tuple[int, int | None]]:
    """条款原文自己引用了哪些条款：「前款」「前两款」「本条」「前条」解析为条号，显式「第N条第M款」原样取出。
    回答把这些被引用的条款写成绝对条号（如把「不适用前款规定」写成「不适用第二条第一款」）时，不算归属错误。"""
    a, n = provision.article, provision.paragraph
    order = sorted({p.article for p in provisions})
    found = set()
    if "前两款" in provision.text:
        found |= {(a, n - 1), (a, n - 2)}
    elif "前款" in provision.text:
        found.add((a, n - 1))
    if "本条" in provision.text:
        found.add((a, None))
    if "前条" in provision.text and order.index(a) > 0:
        found.add((order[order.index(a) - 1], None))
    for m in REF.finditer(provision.text):
        article, paragraph, _ = _ref_value(m)
        found.add((article, paragraph))
    return found


def _ref_label(article, paragraph, item) -> str:
    return (f"第{cn_label(article)}条" + (f"第{cn_label(paragraph)}款" if paragraph else "")
            + (f"第{cn_label(item)}项" if item else ""))


def _check_refs(answer, provisions, covered, quotes, findings):
    articles = {p.article for p in provisions}
    for sentence in SENTENCE.finditer(answer):
        refs = [m for m in REF.finditer(sentence.group()) if not _inside(sentence.start() + m.start(), quotes)]
        for position, m in enumerate(refs):
            article, paragraph, item = _ref_value(m)
            label = _ref_label(article, paragraph, item)
            if article not in articles:
                findings.append(_finding("ARTICLE_NOT_IN_SOURCE", m.group(), f"原文只有：{'、'.join(_ref_label(a, None, None) for a in sorted(articles))}"))
                continue
            if not any(_matches(p, article, paragraph, item) for p in provisions):
                findings.append(_finding("PROVISION_NOT_IN_SOURCE", m.group(), f"原文没有{label}"))
                continue
            if not any(_matches(p, article, paragraph, item) for p in covered):
                findings.append(_finding("ARTICLE_NOT_RETRIEVED", m.group(), f"检索片段里没有{label}的内容"))
            if position:
                continue  # 只核对每句第一个引用的内容归属；同句后面的引用多是条文自身的交叉引用
            claim = _bigrams(REF.sub("", sentence.group()))
            # 先按条比较；所引带款号时，再在该条内按款比较
            by_article = {a: _coverage(claim, [p.text for p in provisions if p.article == a]) for a in articles}
            best = max(by_article, key=by_article.get)
            mentioned = set().union(*(_mentions(p, provisions) for p in provisions if p.article == best))
            if (best != article and by_article[best] >= ATTRIBUTION_MIN and by_article[article] <= by_article[best] - ATTRIBUTION_GAP
                    and article not in {m[0] for m in mentioned}):
                findings.append(_finding("MISATTRIBUTED", sentence.group().strip(),
                                         f"所引为{label}，内容最接近{_ref_label(best, None, None)}（覆盖 {by_article[best]:.2f}，所引 {by_article[article]:.2f}）",
                                         cited=label, located=_ref_label(best, None, None)))
                continue
            if paragraph is not None:
                own = {p.paragraph for p in provisions if p.article == article}
                by_paragraph = {n: _coverage(claim, [p.text for p in provisions if p.article == article and p.paragraph == n]) for n in own}
                near = max(by_paragraph, key=by_paragraph.get)
                mentioned = set().union(*(_mentions(p, provisions) for p in provisions if p.article == article and p.paragraph == near))
                if (near != paragraph and by_paragraph[near] >= ATTRIBUTION_MIN and by_paragraph[paragraph] <= by_paragraph[near] - ATTRIBUTION_GAP
                        and (article, paragraph) not in mentioned):
                    findings.append(_finding("MISATTRIBUTED", sentence.group().strip(),
                                             f"所引为{label}，内容最接近{_ref_label(article, near, None)}（覆盖 {by_paragraph[near]:.2f}，所引 {by_paragraph[paragraph]:.2f}）",
                                             cited=label, located=_ref_label(article, near, None)))


def _check_negation(answer, provisions, findings):
    for clause in CLAUSE.finditer(answer):
        text = clause.group()
        ref, negation = REF.search(text), NEGATION.search(text)
        if not ref or not negation or negation.start() < ref.end():
            continue
        kinds = {kind for word, kind in TOPICS.items() if word in text[negation.start():]}
        article = _ref_value(ref)[0]
        held = [p for p in provisions if p.article == article
                and any(UNIT_CLASS.get(unit) in kinds for _, _, _, unit in _quantities(p.text))]
        if kinds and held:
            findings.append(_finding("NEGATION_CONTRADICTED", text.strip(), f"{held[0].label}：{held[0].text}", located=held[0].label))


def _check_quantities(answer, contexts, question, findings):
    in_context = _quantity_set(contexts)
    in_question = _quantity_set([question])
    pool = in_context | in_question
    # 推算值至少用到一个题目给出的数额；只由资料数额相加减得到的不算，以免掩盖改错的数额
    pairs = [(a, b, u) for a, u in in_question for b, w in pool if u == w]
    for start, end, value, unit in _quantities(answer):
        before = re.sub(r"[“”\"「」『』‘’\s]", "", answer[max(0, start - 6):start])
        if before.endswith(CONTRAST):
            continue  # 「而非“7 个自然日”」：回答在否定这个数额
        text = answer[start:end]
        if (value, unit) in in_context:
            continue
        if (value, unit) in in_question:
            findings.append(_finding("FROM_QUESTION", text, "题目中有这一数额，资料中没有"))
        elif any(u == unit and (a + b == value or abs(a - b) == value) for a, b, u in pairs):
            findings.append(_finding("DERIVED", text, "由题目或资料中的数额加减得到"))
        elif any(v == value and u != unit for v, u in in_context):
            same = sorted(u for v, u in in_context if v == value)
            findings.append(_finding("UNIT_MISMATCH", text, f"资料中同一数值的单位为：{'、'.join(same)}"))
        else:
            findings.append(_finding("QUANTITY_UNSUPPORTED", text, "资料与题目中都没有这一数额"))


def _check_modals(answer, contexts, quotes, findings):
    flat = _compact("\n".join(contexts))
    for m in re.finditer("|".join(MODALS), answer):
        if _inside(m.start(), quotes):
            continue
        tail = _compact(answer[m.end():m.end() + 6])
        if len(tail) < 4 or (m.group() + tail) in flat:
            continue
        for other in MODALS:
            if other != m.group() and (other + tail) in flat:
                findings.append(_finding("MODAL_SWAP", m.group() + tail, f"资料为「{other}{tail}」"))
                break


def check_answer(answer: str, contexts: list[str], source: str, question: str = "") -> dict:
    """核对一个回答。source 是法规原文；contexts 是本题实际检索片段；question 只用来识别题目给出的数额。"""
    answer = normalize(answer)
    contexts = [normalize(c) for c in contexts or []]
    provisions = parse_provisions(source)
    covered = sorted({p for c in contexts for p in locate(c, provisions)}, key=lambda p: (p.article, p.paragraph, p.item or 0))
    quotes = _quoted_spans(answer, [source, *contexts])
    findings: list[dict] = []
    _check_refs(answer, provisions, covered, quotes, findings)
    _check_negation(answer, provisions, findings)
    _check_quantities(answer, contexts, normalize(question), findings)
    _check_modals(answer, contexts, quotes, findings)
    for m in RELATIVE.finditer(answer):
        if not _inside(m.start(), quotes):
            findings.append(_finding("RELATIVE_REF", m.group(), "回答中的相对引用，未解析到具体条款"))
    return {
        "flagged": any(f["severity"] == "error" for f in findings),
        "findings": findings,
        "contextProvisions": [[p.label for p in locate(c, provisions)] for c in contexts],
    }
