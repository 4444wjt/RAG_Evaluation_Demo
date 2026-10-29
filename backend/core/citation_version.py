"""Bounded offline CLML observations and the unmodified, explicit-field B1 checker.

Source hashes always cover uploaded raw bytes, not the legacy source registry.
Expectations remain the caller's input. Nothing here fetches or writes a file.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.parsers import expat

from backend.core import _citation_version_rules as rules

MAX_FILES = 10
MAX_CITATIONS = 20
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_XML_DEPTH = 128
MAX_XML_NODES = 50000
MAX_JSON_DEPTH = 128
MAX_XML_EXPANDED_NAME_BYTES = 16 * 1024 * 1024
MAX_XML_INDEX_BYTES = 16 * 1024 * 1024
MAX_SOURCE_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_BATCH_EVIDENCE_BYTES = 48 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
VERSION = "citation-version-b1-v1"

# These describe this process's loaded version, never a later on-disk version.
_SOURCE_PATHS = {"rules": Path(rules.__file__).resolve(), "wrapper": Path(__file__).resolve()}
_LOADED_SOURCE_SHA256 = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for name, path in _SOURCE_PATHS.items()}
_JSON_ESCAPES = re.compile(r'["\\\x00-\x1f]')


class CitationVersionInputError(ValueError):
    """A package-wide error, as distinct from a preserved invalid citation."""

    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _assert_loaded_sources():
    try:
        current = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for name, path in _SOURCE_PATHS.items()}
    except OSError:
        current = None
    if current != _LOADED_SOURCE_SHA256:
        raise CitationVersionInputError("检查器源码已变化或不可读；请重启本地服务后重新检查。", 409)


def _json_size(value, *, limit, indent=2, detail="结果超过大小预算；整包未返回，未截断证据。"):
    """Count serialized UTF8 bytes incrementally, without building a huge dump."""
    size = 1  # The CLI's trailing newline also belongs to the saved artifact.
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, indent=indent, allow_nan=False)
        for chunk in encoder.iterencode(value):
            size += len(chunk.encode("utf-8"))
            if size > limit:
                raise CitationVersionInputError(detail, 413)
    except CitationVersionInputError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise CitationVersionInputError("结果或请求不能表示为受支持的 UTF8 JSON。") from None
    return size


def validate_artifact_size(value, *, bundle=False):
    """The same pretty-JSON byte boundary is used by producers and CLI replay."""
    try:
        _json_values(value)
    except (ValueError, UnicodeError):
        raise CitationVersionInputError("结果或复核包 JSON 嵌套超过 128 层或包含非法值；整包未输出。") from None
    return _json_size(value, limit=MAX_BUNDLE_BYTES if bundle else MAX_REPORT_BYTES,
                      detail="复核包超过 128 MiB；整包未输出。" if bundle else "报告超过 64 MiB；整包未输出。")


def _constant(_value):
    raise ValueError("non-finite JSON number")


def _json_values(value):
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON nesting exceeds supported depth")
        if isinstance(current, str):
            current.encode("utf-8")
        elif isinstance(current, float) and not math.isfinite(current):
            raise ValueError("non-finite JSON number")
        elif isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.keys())
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def load_json(raw: bytes, *, limit: int = MAX_REQUEST_BYTES):
    """Read bounded UTF8 JSON without duplicate keys or nonfinite constants."""
    if len(raw) > limit:
        raise CitationVersionInputError("请求体超过大小限制。", 413)
    try:
        result = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=rules.reject_duplicate_keys,
            parse_constant=_constant,
        )
        _json_values(result)
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CitationVersionInputError("JSON 格式不合法、重复字段、非 UTF8 编码或嵌套超过 128 层。") from None


def _string(value, *, limit: int, empty: bool = False) -> bool:
    return isinstance(value, str) and len(value) <= limit and (empty or bool(value.strip()))


def _decode_package(body, *, check: bool):
    try:
        _json_values(body)
    except (ValueError, UnicodeError):
        raise CitationVersionInputError("请求包含非法 Unicode、非有限数值或嵌套超过 128 层。") from None
    expected = {"schema", "files", "citations"} if check else {"schema", "files"}
    schema = "CitationVersionCheckV1" if check else "CitationVersionFilesV1"
    if not isinstance(body, dict) or set(body) != expected or body.get("schema") != schema:
        raise CitationVersionInputError("请求结构或 schema 不符合引用版本检查合同。")
    files = body["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise CitationVersionInputError("请选择 1 至 10 份 XML 文件。")
    seen = set()
    for value in files:
        if (not isinstance(value, dict) or set(value) != {"id", "name", "base64"}
                or not _string(value["id"], limit=128)
                or not _string(value["name"], limit=255)
                or not isinstance(value["base64"], str)):
            raise CitationVersionInputError("文件条目必须包含字符串 id、name 与 base64。")
        if value["id"] in seen:
            raise CitationVersionInputError("文件 ID 重复；请为每份资料使用唯一 ID。")
        seen.add(value["id"])
    if check:
        citations = body["citations"]
        if not isinstance(citations, list) or not 1 <= len(citations) <= MAX_CITATIONS:
            raise CitationVersionInputError("每次检查需要 1 至 20 条引用。")
        seen = set()
        for item in citations:
            if (not isinstance(item, dict) or set(item) != {"id", "label", "file_id", "request"}
                    or not _string(item["id"], limit=128)
                    or not _string(item["label"], limit=300, empty=True)
                    or not _string(item["file_id"], limit=128)):
                raise CitationVersionInputError("引用条目必须包含 id、label、file_id 与 request。")
            if item["id"] in seen:
                raise CitationVersionInputError("引用 ID 重复；整包未执行。")
            seen.add(item["id"])
    _json_size(body, limit=MAX_REQUEST_BYTES, indent=None,
               detail="请求 JSON 超过 8 MiB；整包未执行。")
    decoded = []
    total = 0
    for value in files:
        raw = None
        error = None
        # Bound decoding allocation even for syntactically invalid base64 input.
        if len(value["base64"]) > 4 * ((MAX_FILE_BYTES + 2) // 3):
            raise CitationVersionInputError("单份文件或文件总量超过 5 MiB。", 413)
        try:
            raw = base64.b64decode(value["base64"], validate=True)
        except (ValueError, binascii.Error):
            error = "文件 base64 编码不合法。"
        if raw is not None:
            total += len(raw)
            if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise CitationVersionInputError("单份文件或文件总量超过 5 MiB。", 413)
        decoded.append({
            "id": value["id"], "name": value["name"], "raw": raw,
            "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "size_bytes": len(raw) if raw is not None else None, "error": error,
        })
    return decoded


def _escaped_size(value):
    """Upper bound for a JSON string's content, excluding its two quotes."""
    if value is None:
        return 4
    return len(value.encode("utf-8")) + sum(
        5 if ord(match.group()) < 32 else 1 for match in _JSON_ESCAPES.finditer(value)
    )


def _xml_resource_budget(root):
    """Bound B1 evidence and observation expansion using lengths, never joins.

    B1's evidence sites are fixed in the copied rules: location is serialized
    twice, dct:valid twice per containing Metadata, conditions once (some
    attributes twice). A 1024-byte allowance per reference covers its containing
    check and JSON indentation. This is a resource estimate, not a new rule.
    All-node path length also bounds B1's recursive path index before it exists.
    """
    budget = 32768
    index_bytes = 0
    max_path = 0
    p1_count = 0
    max_extent = 0
    max_snapshot = 0
    tag_sizes = {}
    text_sizes = {}
    stack = [(root, 0, 0, False)]
    metadata_tag = rules.q(rules.M, "Metadata")
    text_metadata = {rules.q(rules.DC, name): 1 for name in ("title", "identifier", "language", "date", "modified")}
    text_metadata[rules.q(rules.DCT, "valid")] = 2
    resource_error = "XML 证据展开预算超过 4 MiB；整份资料无效，未执行规则或截断证据。"

    while stack:
        node, path_size, metadata_depth, visited = stack.pop()
        if not visited:
            if node.tag not in tag_sizes:
                tag_sizes[node.tag] = _escaped_size(node.tag)
            # Every sibling index is at most MAX_XML_NODES; no path is built.
            path_size += tag_sizes[node.tag] + len(str(MAX_XML_NODES)) + 3
            index_bytes += path_size
            if index_bytes > MAX_XML_INDEX_BYTES:
                raise ValueError("XML 路径展开预算超过 16 MiB；整份资料无效，未执行规则。")
            max_path = max(max_path, path_size)
            metadata_depth += int(node.tag == metadata_tag)
            stack.append((node, path_size, metadata_depth, True))
            stack.extend((child, path_size, metadata_depth, False) for child in reversed(node))
            continue

        # itertext() excludes the current node's tail, includes every child tail.
        subtree_size = _escaped_size(node.text or "") + sum(
            text_sizes.pop(child) + _escaped_size(child.tail or "") for child in node
        )
        text_sizes[node] = subtree_size

        def evidence(count=1, attr=None):
            raw_size = subtree_size if attr is None else _escaped_size(node.get(attr))
            suffix = 0 if attr is None else 2 + _escaped_size(attr)
            return count * (path_size + suffix + raw_size + 1024)

        if node.tag in text_metadata:
            budget += evidence(text_metadata[node.tag] * metadata_depth)
            if node.tag == rules.q(rules.DCT, "valid") and metadata_depth:
                max_snapshot = max(max_snapshot, subtree_size)
        if node.tag == rules.q(rules.ATOM, "link") and node.get("rel") == "self":
            budget += evidence(metadata_depth, "href")
        if node.tag in {rules.q(rules.M, "UnappliedEffect"), rules.q(rules.L, "Versions")}:
            budget += evidence(2 if node.tag == rules.q(rules.L, "Versions") else 1)
            budget += sum(evidence(attr=attr) for attr in node.attrib)
        if node is root:
            budget += evidence(attr="IdURI") + evidence(attr=rules.LANG)
        if node.tag == rules.q(rules.L, "P1"):
            p1_count += 1
            budget += evidence(2, "IdURI") + evidence(attr="DocumentURI")
        if node.tag.startswith("{" + rules.L + "}"):
            for attr in ("RestrictExtent", "RestrictStartDate", "RestrictEndDate", "AltVersionRefs",
                         "Match", "Concurrent", "BlanketAmendment", "Status", "Version"):
                if attr in node.attrib or (attr in {"RestrictStartDate", "RestrictEndDate"}
                                          and node.tag in {rules.q(rules.L, "P1"), rules.q(rules.L, "P1group")}):
                    budget += evidence(2 if attr in {"AltVersionRefs", "Match", "Status"} else 1, attr)
        if "RestrictExtent" in node.attrib:
            max_extent = max(max_extent, _escaped_size(node.get("RestrictExtent")))
        if budget > MAX_SOURCE_EVIDENCE_BYTES:
            raise ValueError(resource_error)

    # Observation copies one selected snapshot/ancestor extent into every P1.
    # Source evidence costs above already cover direct P1 and metadata fields.
    budget += 2 * max_path + p1_count * (max_snapshot + max_extent + 1024)
    if budget > MAX_SOURCE_EVIDENCE_BYTES:
        raise ValueError(resource_error)
    return budget


def _xml_namespace_preflight(text):
    """Bound expanded names before a namespace-aware parser builds any tree.

    Expat with no namespace separator reports lexical QNames and xmlns values;
    it never concatenates a URI into each distinct element/attribute name. The
    largest URI seen so far plus each lexical QName is a conservative byte
    bound, including declarations on the same element and later rebindings.
    This is not an exact peak-memory limit. No expanded names or tree are built.
    """
    parser = expat.ParserCreate(namespace_separator=None)
    namespace_bytes = len("http://www.w3.org/XML/1998/namespace")
    expanded_bytes = nodes = depth = 0

    def count_name(name):
        nonlocal expanded_bytes
        expanded_bytes += len(name.encode("utf-8")) + namespace_bytes + 2
        if expanded_bytes > MAX_XML_EXPANDED_NAME_BYTES:
            raise ValueError("XML 命名空间展开预算超过 16 MiB；整份资料无效，未构树或执行规则。")

    def start(name, attributes):
        nonlocal namespace_bytes, nodes, depth
        nodes += 1
        depth += 1
        if depth > MAX_XML_DEPTH:
            raise ValueError("XML 深度超过安全上限；未截断或执行规则。")
        if nodes > MAX_XML_NODES:
            raise ValueError("XML 节点数超过安全上限；未截断或执行规则。")
        for key, value in attributes.items():
            if key == "xmlns" or key.startswith("xmlns:"):
                namespace_bytes = max(namespace_bytes, len(value.encode("utf-8")))
        count_name(name)
        for key in attributes:
            count_name(key)

    def end(_name):
        nonlocal depth
        depth -= 1

    def reject_doctype(*_args):
        raise ValueError("禁止 DTD 和 ENTITY 声明。")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = reject_doctype
    try:
        parser.Parse(text, True)
    except expat.ExpatError:
        raise ValueError("XML 语法不合法。") from None
    return expanded_bytes


def _parse_xml(raw: bytes):
    """Reject unsafe structure before the unchanged recursive B1 index runs."""
    if not raw:
        raise ValueError("XML 文件为空。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("XML 必须使用 UTF8 编码。") from None
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError("禁止 DTD 和 ENTITY 声明。")
    declaration = re.match(r"\s*<\?xml\s+[^?]*encoding\s*=\s*[\"']([^\"']+)", text, re.I)
    if declaration and declaration.group(1).lower() not in {"utf-8", "utf8"}:
        raise ValueError("XML 声明必须使用 UTF8 编码。")
    _xml_namespace_preflight(text)
    depth = 0
    nodes = 0
    root = None
    try:
        for event, node in ET.iterparse(io.StringIO(text), events=("start", "end")):
            if event == "start":
                depth += 1
                nodes += 1
                if root is None:
                    root = node
                    if root.tag != rules.q(rules.L, "Legislation"):
                        raise ValueError("根节点必须是 CLML Legislation。")
                if depth > MAX_XML_DEPTH:
                    raise ValueError("XML 深度超过安全上限；未截断或执行规则。")
                if nodes > MAX_XML_NODES:
                    raise ValueError("XML 节点数超过安全上限；未截断或执行规则。")
            else:
                depth -= 1
    except ET.ParseError:
        raise ValueError("XML 语法不合法。") from None
    if root is None:
        raise ValueError("XML 缺少根节点。")
    return root, _xml_resource_budget(root)


def _observe(root):
    metadata = list(root.iter(rules.q(rules.M, "Metadata")))
    ambiguities = []

    def unique(namespace, name):
        values = [n for container in metadata for n in container.iter(rules.q(namespace, name))]
        if len(values) != 1:
            ambiguities.append(name + ": required unique field missing or duplicated")
            return None
        return "".join(values[0].itertext()) or None

    title = unique(rules.DC, "title")
    language = unique(rules.DC, "language")
    snapshot = unique(rules.DCT, "valid")
    parents = {child: node for node in root.iter() for child in node}
    provisions = []
    seen = set()
    for node in root.iter(rules.q(rules.L, "P1")):
        identifier = node.get("IdURI")
        if identifier is not None and identifier in seen:
            ambiguities.append("duplicate P1 IdURI: " + identifier)
        seen.add(identifier)
        owner = node
        while owner is not None and owner.get("RestrictExtent") is None:
            owner = parents.get(owner)
        interval = node if any(k in node.attrib for k in ("RestrictStartDate", "RestrictEndDate")) else None
        parent = parents.get(node)
        if (interval is None and parent is not None and parent.tag == rules.q(rules.L, "P1group")
                and len([n for n in parent if n.tag == rules.q(rules.L, "P1")]) == 1):
            interval = parent
        provisions.append({
            "provision_id": identifier, "view_uri": node.get("DocumentURI"),
            "snapshot_date": snapshot,
            "extent": owner.get("RestrictExtent") if owner is not None else None,
            "start": interval.get("RestrictStartDate") if interval is not None else None,
            "end": interval.get("RestrictEndDate") if interval is not None else None,
        })
    return {"title": title, "document_id": root.get("IdURI"), "language": language,
            "provisions": provisions, "ambiguities": ambiguities}


def inspect(body):
    _assert_loaded_sources()
    records = _decode_package(body, check=False)
    budget = 0
    for record in records:
        if record["error"] is None:
            try:
                record["root"], estimate = _parse_xml(record["raw"])
                budget += estimate
            except ValueError as failure:
                record["error"] = str(failure)
    if budget > MAX_BATCH_EVIDENCE_BYTES:
        raise CitationVersionInputError("整批观察证据预算超过 48 MiB；请减少文件后重试。", 413)
    result = []
    for record in records:
        observed = None
        error = record["error"]
        if error is None:
            observed = _observe(record["root"])
        result.append({k: record[k] for k in ("id", "name", "sha256", "size_bytes")} | {
            "status": "READY" if error is None else "INVALID_INPUT",
            "error": error, "observed": observed,
        })
    response = {"schema": "CitationVersionInspectionV1", "files": result}
    validate_artifact_size(response)
    _assert_loaded_sources()
    return response


def _invalid(raw, reason):
    return {
        "method": "B1", "source_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
        "subchecks": {name: {"state": "INVALID_INPUT", "checks": []} for name in rules.NAMES},
        "axes": dict.fromkeys(rules.AXES, "INVALID_INPUT"), "target_location": None,
        "owners": {}, "date_roles": {}, "input_errors": [reason],
    }


def evaluate(source_bytes, request):
    """Preflight once, then call B1 without changing its seven checks or axes."""
    _assert_loaded_sources()
    try:
        if not isinstance(source_bytes, bytes) or len(source_bytes) > MAX_FILE_BYTES:
            raise ValueError("source must be bytes within 5 MiB")
        _parse_xml(source_bytes)
    except ValueError as failure:
        result = _invalid(source_bytes if isinstance(source_bytes, bytes) else None, str(failure))
    else:
        _json_size(request, limit=MAX_REQUEST_BYTES, indent=None)
        result = rules.evaluate(source_bytes, request, method="B1")
    validate_artifact_size(result)
    _assert_loaded_sources()
    return result


def check(body):
    _assert_loaded_sources()
    records = _decode_package(body, check=True)
    try:
        # The saved bundle adds levels around the request and retained citations.
        _json_values({"input": body, "report": {"inputs": {"citations": body["citations"]}}})
    except (ValueError, UnicodeError):
        raise CitationVersionInputError("请求嵌套将使复核包超过 128 层；整包未执行。") from None
    by_id = {r["id"]: r for r in records}
    for record in records:
        if record["error"] is None:
            try:
                _, record["evidence_budget"] = _parse_xml(record["raw"])
            except ValueError as failure:
                record["error"] = str(failure)
    budget = 32768
    for citation in body["citations"]:
        record = by_id.get(citation["file_id"])
        budget += record.get("evidence_budget", 32768) if record is not None else 32768
        # A valid request/view_uri also appears once as B1's raw evidence.
        budget += _json_size(citation["request"], limit=MAX_REQUEST_BYTES, indent=None)
    if budget > MAX_BATCH_EVIDENCE_BYTES:
        raise CitationVersionInputError("整批证据展开预算超过 48 MiB；请减少引用后重试，整包未执行。", 413)
    # Retained malformed requests may be deeply nested; bound pretty-print
    # expansion before B1 or deepcopy, not after allocating the report.
    _json_size(body["citations"], limit=MAX_REQUEST_BYTES)
    rows = []
    counts = dict.fromkeys(("total", "valid_input", "conflicts", "field_review", "known_by_review", "invalid"), 0)
    for citation in body["citations"]:
        record = by_id.get(citation["file_id"])
        if record is None:
            output = _invalid(None, "未知 file_id；请重新选择已上传的资料。")
        elif record["error"] is not None:
            output = _invalid(record["raw"], record["error"])
        else:
            output = rules.evaluate(record["raw"], citation["request"], method="B1")
        rows.append({k: citation[k] for k in ("id", "label", "file_id")} | {"output": output})
        counts["total"] += 1
        if "INVALID_INPUT" in output["axes"].values():
            counts["invalid"] += 1
        else:
            counts["valid_input"] += 1
            counts["conflicts"] += int(any(v["state"] == "MISMATCH" for v in output["subchecks"].values()))
            counts["field_review"] += int(any(v["state"] == "REVIEW" for v in output["subchecks"].values()))
            counts["known_by_review"] += int(output["axes"]["known_by"] == "REVIEW")
    report = {
        "schema": "CitationVersionReportV1",
        "checker": {"version": VERSION, "method": "B1", "source_sha256": dict(_LOADED_SOURCE_SHA256)},
        "inputs": {
            "files": [{k: r[k] for k in ("id", "name", "sha256", "size_bytes")} for r in records],
            "citations": copy.deepcopy(body["citations"]),
        },
        "rows": rows, "counts": counts,
        "count_semantics": {
            "total": "all citation rows, preserving input order",
            "valid_input": "rows without INVALID_INPUT; valid_input + invalid = total",
            "invalid": "rows with INVALID_INPUT",
            "conflicts": "valid rows with any of seven subchecks MISMATCH",
            "field_review": "valid rows with any of seven subchecks REVIEW",
            "known_by_review": "valid rows with known_by REVIEW; overlaps conflicts and field_review",
            "overlap": "conflicts, field_review and known_by_review overlap; never sum as exclusive groups",
        },
        "limitations": [
            "英国官方 CLML XML；仅核对显式版本字段，不判断引文语义或法律适用。",
            "只公开 B1；没有总 PASS。known_by 始终待复核，不能证明历史时点已知或当时可得。",
            "文件 SHA256 覆盖原始字节；不采用旧 sourceRegistry 的 sha256-lf。",
            "文档身份取自用户选择的资料；需自行确认所选资料与引用对应，内部自洽不等于独立真实性验证。",
            "七分项中的冲突、字段待复核与 known_by 待复核可同时出现；原始 B1 原因及证据路径全部保留。",
            "观察字段不自动变为用户期望；日期、地域、语言和来源绑定由请求明确提供。",
            "单份与合计原始文件上限 5 MiB，最多 10 份文件、20 条引用；XML 深度上限 128、节点数上限 50000，超限整份无效而非截断。",
            "资源边界：构树前命名空间展开预算 16 MiB（保守名称字节计数，非峰值内存上限）；路径展开预算 16 MiB，单份证据预估 4 MiB、整批 48 MiB。超限明确拒绝，未改变 B1 判断规则；报告上限 64 MiB，复核包上限 128 MiB。",
        ],
    }
    validate_artifact_size(report)
    validate_artifact_size({"schema": "CitationVersionBundleV1", "input": body, "report": report}, bundle=True)
    _assert_loaded_sources()
    return report
