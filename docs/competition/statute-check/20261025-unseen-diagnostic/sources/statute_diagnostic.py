"""Offline diagnostic for user-supplied, explicitly line-structured statute text.

Run with --input <UTF-8 JSON> [--json]. Exit 0 means no covered rule found an
error, NOT a quality-gate PASS or a finding of factual/legal correctness.
Exit 1 means error findings; exit 2 means invalid input or review required.
The existing rule modules and their thresholds are used without modification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import statute_check as sc  # noqa: E402
from backend.core.statute_locate import check_answer_extended  # noqa: E402

MAX_BYTES = 1024 * 1024
FIELDS = {"schemaVersion", "sourceFormat", "source", "answer", "contexts", "question", "engine", "structureReference"}
REQUIRED = {"schemaVersion", "sourceFormat", "source", "answer", "contexts"}
NONCONTRACT_ITEM = re.compile(r"^(?:[0-9]+\.(?![0-9])|[零〇一二三四五六七八九十百千两]+、)")
LIMITS = [
    "只诊断现有规则覆盖的引用、数额和情态词问题；未检出不等于事实有依据或门禁通过。",
    "原文必须由提供者确认：第N条标题带正文、每个物理行对应一款、（一）起始行为上一款的项；空行结束当前条。",
    "机械格式检查不证明法律语义上的条款划分正确；不会自动重排、补标签或从回答补造题设。",
    "回指可能只有部分位置或未定，不代表已证明建议位置正确；不调用模型、不参与原门禁。",
    "未提供独立结构参照时，全部款项定位结果的真实款界均未核验；同款折行或多款粘连可能改变款号与项归属。",
    "结构参照匹配只证明与所给参照一致，不证明法律划分正确；参照不能由当前被测解析器生成后冒充独立依据。",
]


def _issue(code: str, message: str, line: int | None = None) -> dict:
    return {"code": code, "message": message, **({"line": line} if line is not None else {})}


def _base() -> dict:
    return {"schemaVersion": 1, "status": "invalid_input", "flagged": None,
            "engine": None, "diagnostics": [], "sourceAudit": None,
            "result": None, "structureCheck": None, "provenance": None, "limitations": list(LIMITS)}


def _location_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 1 <= value <= 999 and value % 1 == 0


def _valid_structure_reference(reference) -> bool:
    """Validate supplied evidence, without generating it from the parser."""
    if not isinstance(reference, dict) or set(reference) != {"schemaVersion", "units"}:
        return False
    version = reference["schemaVersion"]
    if isinstance(version, bool) or not isinstance(version, (int, float)) or version != 1:
        return False
    units = reference["units"]
    if not isinstance(units, list) or not units:
        return False
    seen, active, paragraph, item = set(), None, 0, 0
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {"article", "paragraph", "item", "body"}:
            return False
        if not all(_location_number(unit[k]) for k in ("article", "paragraph")):
            return False
        if unit["item"] is not None and not _location_number(unit["item"]):
            return False
        if not isinstance(unit["body"], str) or not unit["body"].strip():
            return False
        try:
            unit["body"].encode("utf-8")
        except UnicodeError:
            return False
        if unit["article"] != active:
            if unit["article"] in seen:
                return False
            active, paragraph, item = unit["article"], 0, 0
            seen.add(active)
        if unit["item"] is None:
            if unit["paragraph"] != paragraph + 1:
                return False
            paragraph, item = unit["paragraph"], 0
        else:
            if not paragraph or unit["paragraph"] != paragraph or unit["item"] != item + 1:
                return False
            item = unit["item"]
    return True


def _validate_request(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return [_issue("REQUEST_TYPE", "请求必须是 JSON 对象。")]
    issues = []
    if set(payload) - FIELDS:
        issues.append(_issue("UNKNOWN_FIELDS", "请求包含未支持的字段。"))
    if REQUIRED - set(payload):
        issues.append(_issue("MISSING_FIELDS", "缺少 schemaVersion/sourceFormat/source/answer/contexts 必填字段。"))
    version = payload.get("schemaVersion")
    if isinstance(version, bool) or not isinstance(version, (int, float)) or version != 1:
        issues.append(_issue("UNKNOWN_VERSION", "schemaVersion 必须是整数 1。"))
    if payload.get("sourceFormat") != "article-lines-v1":
        issues.append(_issue("SOURCE_FORMAT", "只支持显式声明的 article-lines-v1 行结构，不接受任意排版。"))
    for field in ("source", "answer"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            issues.append(_issue("TEXT_REQUIRED", f"{field} 必须是非空字符串。"))
    contexts = payload.get("contexts")
    if not isinstance(contexts, list) or not contexts or any(not isinstance(c, str) or not c.strip() for c in contexts):
        issues.append(_issue("CONTEXTS_TYPE", "contexts 必须是非空字符串数组，不接受单个字符串或空片段。"))
    if not isinstance(payload.get("question", ""), str):
        issues.append(_issue("QUESTION_TYPE", "question 必须是字符串；未提供时保持为空。"))
    if payload.get("engine", "base") not in ("base", "extended"):
        issues.append(_issue("ENGINE_UNSUPPORTED", "engine 只支持 base 或 extended。"))
    if "structureReference" in payload and not _valid_structure_reference(payload["structureReference"]):
        issues.append(_issue("STRUCTURE_REFERENCE_INVALID", "结构参照须含 schemaVersion=1 和完整有序的条/款/项正文；不接受空、缺项、重复或乱序。"))
    texts = [payload.get(field) for field in ("source", "answer", "question")]
    if isinstance(contexts, list):
        texts.extend(contexts)
    try:
        for text in texts:
            if isinstance(text, str):
                text.encode("utf-8")
    except UnicodeError:
        issues.append(_issue("TEXT_ENCODING_INVALID", "文本包含孤立 Unicode 代理字符，不能输出有效 UTF-8；不自动替换。"))
    return issues


def _unsupported_prefix(text: str) -> bool:
    return bool(re.match(rf"^第{sc.NUM}款", text)
                or re.match(r"^[（(][0-9一二三四五六七八九十]+[）)]", text)
                or NONCONTRACT_ITEM.match(text))


def invisible_format_issues(payload: dict, *, allow_initial_source_bom: bool = False) -> list[dict]:
    """Inspect validated text verbatim, grouping by field and Cf code point."""
    texts = [(field, payload[field]) for field in ("source", "answer", "statement", "question")
             if field in payload]
    texts.extend((f"contexts[{i}]", text) for i, text in enumerate(payload.get("contexts", [])))
    texts.extend((f"structureReference.units[{i}].body", unit["body"])
                 for i, unit in enumerate(payload.get("structureReference", {}).get("units", [])))
    issues = []
    for field, text in texts:
        occurrences = {}
        # splitlines preserves CRLF as one physical line and leaves columns in
        # Unicode characters, without normalizing or removing input characters.
        for line, raw in enumerate(text.splitlines(keepends=True), 1):
            for column, char in enumerate(raw, 1):
                # Only the raw-source envelope entry opts into this encoding marker.
                if allow_initial_source_bom and field == "source" and line == column == 1 and char == "\ufeff":
                    continue
                if unicodedata.category(char) == "Cf":
                    entry = occurrences.setdefault(char, [line, column, 0])
                    entry[2] += 1
        for char, (line, column, count) in occurrences.items():
            issues.append(_issue("INVISIBLE_FORMAT_CHARACTER",
                                 f"{field}：首次出现于第{line}行第{column}列；"
                                 f"U+{ord(char):04X} {unicodedata.name(char, '无名称')}；"
                                 f"出现{count}次。不自动删除；请清理后重试。"))
    return issues


def _audit_source(source: str) -> tuple[dict, list[dict]]:
    """Check coverage of the existing parser's declared layout, not a new parser."""
    article, paragraph = None, 0
    articles, items = set(), set()
    nonblank, parsed, unparsed, issues = 0, 0, [], []
    for number, raw in enumerate(sc.normalize(source).splitlines(), 1):
        line = raw.strip()
        if not line:
            article = None
            continue
        nonblank += 1
        head = sc.HEADER.match(line)
        if head:
            article, paragraph = sc.cn_number(head.group(1)), 1
            if article is None or not 1 <= article <= 999:
                issues.append(_issue("ARTICLE_NUMBER_UNSUPPORTED", "条号超出现有标签函数的 1–999 范围。", number))
            if article in articles:
                issues.append(_issue("DUPLICATE_ARTICLE", "条号重复，无法确定唯一条款归属。", number))
            articles.add(article)
            body = head.group(2).strip()
            if not body:
                issues.append(_issue("EMPTY_ARTICLE_BODY", "条标题独占一行，现有解析会产生空的第一款。", number))
            if sc.ITEM.match(body):
                issues.append(_issue("ITEM_ON_HEADER_LINE", "项标记与条标题同一行，现有解析不会将其识别为项。", number))
            if re.match(rf"^第{sc.NUM}款", body):
                issues.append(_issue("LABELED_LAYOUT_UNSUPPORTED", "原文已显式带款标签，不属于当前声明的行结构格式。", number))
            elif not sc.ITEM.match(body) and _unsupported_prefix(body):
                issues.append(_issue("PROVISION_LAYOUT_UNSUPPORTED", "条头正文以不支持的款或项标记开头，请核对原文结构；不自动改写。", number))
            parsed += 1
        elif article is None:
            unparsed.append(number)
            issues.append(_issue("UNPARSED_SOURCE_LINE", "非空行未归属任何条；不自动忽略前言或空行后的正文。", number))
        else:
            item = sc.ITEM.match(line)
            if item:
                key = (article, paragraph, sc.cn_number(item.group(1)))
                if not line[item.end():].strip():
                    issues.append(_issue("EMPTY_ITEM_BODY", "项标记后没有正文，不能完成该项核查。", number))
                if key in items:
                    issues.append(_issue("DUPLICATE_ITEM", "同一款内项号重复。", number))
                items.add(key)
            else:
                if _unsupported_prefix(line):
                    issues.append(_issue("PROVISION_LAYOUT_UNSUPPORTED", "该行带不支持的款或项标记，不自动当作普通新款。", number))
                paragraph += 1
            parsed += 1
    if not articles:
        issues.append(_issue("NO_PROVISIONS", "原文没有现有解析器可识别的第N条标题，未执行核查。"))
    return {"nonblankLines": nonblank, "parsedLines": parsed,
            "unparsedLines": unparsed, "articleCount": len(articles)}, issues


def _check_structure(source: str, reference: dict | None) -> dict:
    """Compare complete ordered units; counts alone cannot detect shifted bounds."""
    actual = []
    for unit in sc.parse_provisions(source):
        body = unit.text
        if unit.item is not None:
            marker = sc.ITEM.match(body)
            if marker:
                body = body[marker.end():]
        actual.append({"article": unit.article, "paragraph": unit.paragraph,
                       "item": unit.item, "body": body.strip()})
    result = {"status": "not_provided", "referenceSha256": None, "expectedUnits": None,
              "observedUnits": len(actual), "scope": "all_paragraph_item_locations"}
    if reference is not None:
        raw = json.dumps(reference, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = [{**unit, "body": sc.normalize(unit["body"]).strip()} for unit in reference["units"]]
        result.update(status="matched" if expected == actual else "mismatch",
                      referenceSha256=hashlib.sha256(raw).hexdigest(), expectedUnits=len(expected))
    return result


def analyze_request(payload) -> dict:
    """Validate first; no unsupported source is compiled to a no-error result."""
    report = _base()
    report["diagnostics"] = _validate_request(payload)
    if report["diagnostics"]:
        return report
    issues = invisible_format_issues(payload)
    if issues:
        report.update(status="review", diagnostics=issues)
        return report
    report["engine"] = payload.get("engine", "base")
    try:
        audit, issues = _audit_source(payload["source"])
        report["sourceAudit"] = audit
        if not issues:
            report["structureCheck"] = _check_structure(payload["source"], payload.get("structureReference"))
            if report["structureCheck"]["status"] == "mismatch":
                issues.append(_issue("SOURCE_STRUCTURE_MISMATCH", "逐单位款项归属或正文与结构参照不一致，可能有折行、粘连或版本差异；未执行核查，不自动合并。"))
        # cn_label has a finite range; malformed integers must not crash it.
        for match in sc.REF.finditer(sc.normalize(payload["answer"])):
            if any(group is not None and not 1 <= sc.cn_number(group) <= 999 for group in match.groups()):
                issues.append(_issue("REFERENCE_NUMBER_UNSUPPORTED", "回答的引用编号超出现有标签函数的 1–999 范围。"))
                break
    except (ValueError, TypeError, OverflowError):
        report.update(status="review", diagnostics=[_issue("SOURCE_OR_REFERENCE_UNSUPPORTED", "原文或引用编号无法由现有规则处理。")])
        return report
    if issues:
        report.update(status="review", diagnostics=issues)
        return report
    try:
        runner = sc.check_answer if report["engine"] == "base" else check_answer_extended
        result = runner(payload["answer"], payload["contexts"], payload["source"], payload.get("question", ""))
    except (ValueError, TypeError, IndexError, KeyError, OverflowError, RecursionError):
        report.update(status="review", diagnostics=[_issue("ENGINE_FAILURE", "现有规则未能完成核查；未生成无错误结论。")])
        return report
    report.update(status="findings" if result["flagged"] else "no_error_found",
                  flagged=result["flagged"], result=result)
    return report


def _strict_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def diagnose_file(path: Path) -> dict:
    report = _base()
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        report["diagnostics"] = [_issue("INPUT_UNREADABLE", "无法读取输入文件。")]
        return report
    if len(raw) > MAX_BYTES:
        report["diagnostics"] = [_issue("INPUT_TOO_LARGE", "请求文件超过 1 MiB；未执行核查。")]
        return report  # Do not claim that a truncated read hashes the whole input.
    else:
        try:
            payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
        except (UnicodeError, ValueError, RecursionError):
            report["diagnostics"] = [_issue("INPUT_JSON_INVALID", "需要有效 UTF-8 JSON，拒绝重复键和非有限数值。")]
        else:
            report = analyze_request(payload)
    code_paths = ("scripts/statute_diagnostic.py", "backend/core/statute_check.py", "backend/core/statute_locate.py")
    report["provenance"] = {"inputSha256": hashlib.sha256(raw).hexdigest(),
                            "codeSha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in code_paths},
                            "pythonVersion": sys.version.split()[0]}
    return report


def exit_code(report: dict) -> int:
    return {"no_error_found": 0, "findings": 1, "review": 2, "invalid_input": 2}[report["status"]]


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, type=Path, help="UTF-8 JSON 请求；相对路径按当前工作目录解析")
    parser.add_argument("--json", action="store_true", help="stdout 仅输出结构化 JSON")
    args = parser.parse_args(argv)
    report = diagnose_file(args.input)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        titles = {"invalid_input": "输入无效", "review": "需要复核，未完成核查", "findings": "检出规则错误", "no_error_found": "当前规则未检出错误"}
        print(titles[report["status"]])
        for issue in report["diagnostics"]:
            print(f"{issue['code']}：{issue['message']}")
        print(LIMITS[0])
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
