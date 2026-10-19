"""离线检查 PDF 文本层，并把检查报告绑定到实际读取的源文件字节。

这些启发式规则仅检查明显乱码和可能缺失的扫描页；与文档加载器同用
pypdf，不能证明提取文字与页面视觉内容一致，也不执行 OCR 或模型调用。
"""

from collections.abc import Mapping
import hashlib
from io import BytesIO
from pathlib import Path

import pypdf

from backend.config import CHUNK_SIZE, CHUNK_OVERLAP


CHECKER_VERSION = "1.0.0"

# 正常正文不应大量包含控制字符、替代符或未解码的 CID 标记。这些阈值
# 是保守启发式，并非经过语料校准的准确率承诺：允许少量孤立异常，但
# 控制字符超过 5%、U+FFFD 或 CID 标记覆盖超过 1% 时阻止静默入库。
# 不使用汉字占比或拉丁字符占比判定，以免误伤英文和多语言文档。
CONTROL_RATIO_THRESHOLD = 0.05
REPLACEMENT_RATIO_THRESHOLD = 0.01
CID_MARKER_RATIO_THRESHOLD = 0.01

_LAYOUT_CONTROLS = frozenset("\t\n\r")
_CID_MARKER = "(cid:"
_HAN_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2EE5F),
    (0x2F800, 0x2FA1F),
    (0x30000, 0x323AF),
)


def _fingerprint_bytes(source_bytes, extension):
    return {
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "size_bytes": len(source_bytes),
        "extension": extension,
    }


def fingerprint_source(path):
    """返回文件字节的 SHA256、长度与小写扩展名；不写入源文件。"""
    source_path = Path(path)
    return _fingerprint_bytes(source_path.read_bytes(), source_path.suffix.lower())


def _report_metadata(source_fingerprint, loader):
    return {
        "source_fingerprint": source_fingerprint,
        # 只记录实际解析了本文件的后端；非 PDF 未经检查器解析，记 None，
        # 不把 pypdf 冒充成 txt/docx 的加载器。
        "loader": loader,
        "checker_version": CHECKER_VERSION,
        "chunk_parameters": {
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
        },
    }


def _resolve_pdf_object(value):
    return value.get_object() if hasattr(value, "get_object") else value


def _resources_have_image(resources, visited):
    """沿 Form 的资源递归查找 Image XObject，并终止循环引用。"""
    resources = _resolve_pdf_object(resources)
    if not isinstance(resources, Mapping) or id(resources) in visited:
        return False
    visited.add(id(resources))
    xobjects = _resolve_pdf_object(resources.get("/XObject"))
    if not isinstance(xobjects, Mapping):
        return False
    for reference in xobjects.values():
        xobject = _resolve_pdf_object(reference)
        if not isinstance(xobject, Mapping):
            continue
        if xobject.get("/Subtype") == "/Image":
            return True
        if xobject.get("/Subtype") == "/Form":
            if _resources_have_image(xobject.get("/Resources"), visited):
                return True
    return False


def _check_page(page, page_number):
    text = page.extract_text() or ""
    char_count = len(text)
    han_count = sum(
        any(start <= ord(character) <= end for start, end in _HAN_RANGES)
        for character in text
    )
    replacement_count = text.count("\ufffd")
    # \t、\n、\r 是正常排版；DEL 独立记录，不混入 C0/C1 的 Unicode 范围。
    c0_control_count = sum(
        ord(character) < 0x20 and character not in _LAYOUT_CONTROLS
        for character in text
    )
    c1_control_count = sum(0x80 <= ord(character) <= 0x9F for character in text)
    del_control_count = text.count("\x7f")
    control_count = c0_control_count + c1_control_count + del_control_count
    cid_count = text.count(_CID_MARKER)
    # CID 占比按标记本身覆盖的字符数计算，不把任意长度的数字当正文。
    # 空文本没有可计算的分母，比例保留 None；字符计数本身仍是真实的 0。
    control_ratio = control_count / char_count if char_count else None
    replacement_ratio = replacement_count / char_count if char_count else None
    cid_marker_ratio = cid_count * len(_CID_MARKER) / char_count if char_count else None
    has_image = _resources_have_image(page.get("/Resources"), set())

    # 先检查乱码，避免 U+0085 等被 str.strip() 视作空白而掩盖异常。
    if char_count and (
        control_ratio > CONTROL_RATIO_THRESHOLD
        or replacement_ratio > REPLACEMENT_RATIO_THRESHOLD
        or cid_marker_ratio > CID_MARKER_RATIO_THRESHOLD
    ):
        verdict, reasons = "FAIL", ["GARBLED_TEXT_LAYER"]
    elif not text.strip():
        if has_image:
            verdict, reasons = "REVIEW", ["SUSPECTED_SCAN_PAGE"]
        else:
            verdict, reasons = "PASS", ["BLANK_PAGE"]
    else:
        verdict, reasons = "PASS", []

    return {
        "page_number": page_number,
        "char_count": char_count,
        "han_count": han_count,
        "replacement_count": replacement_count,
        "c0_control_count": c0_control_count,
        "c1_control_count": c1_control_count,
        "del_control_count": del_control_count,
        "control_count": control_count,
        "cid_count": cid_count,
        "control_ratio": control_ratio,
        "replacement_ratio": replacement_ratio,
        "cid_marker_ratio": cid_marker_ratio,
        "has_image": has_image,
        "verdict": verdict,
        "reasons": reasons,
    }


def check_pdf(path):
    """逐页检查 PDF；任何 FAIL 优先于 REVIEW，REVIEW 不折算为 PASS。

    无法读取或提取文字时让解析异常向调用方传播，不伪造成功报告。
    指纹与解析使用同一次读取的字节，防止两次读取之间文件变化。
    """
    source_path = Path(path)
    source_bytes = source_path.read_bytes()
    report = _report_metadata(
        _fingerprint_bytes(source_bytes, source_path.suffix.lower()),
        {"name": "pypdf", "version": pypdf.__version__},
    )
    reader = pypdf.PdfReader(BytesIO(source_bytes))
    pages = [_check_page(page, number) for number, page in enumerate(reader.pages, 1)]
    verdicts = {page["verdict"] for page in pages}
    verdict = "FAIL" if "FAIL" in verdicts else "REVIEW" if "REVIEW" in verdicts else "PASS"
    report.update({"verdict": verdict, "pages": pages})
    return report


def check_document(path):
    """PDF 走逐页检查；其它格式仅记录指纹，明确标记不适用。"""
    # 与 load_and_split_doc 的报错保持一致：检查先于加载执行，不能改变缺文件时的提示。
    if not Path(path).exists():
        raise FileNotFoundError(f"文档不存在: {path}")
    if Path(path).suffix.lower() == ".pdf":
        return check_pdf(path)
    report = _report_metadata(fingerprint_source(path), None)
    report.update({"verdict": "NOT_APPLICABLE", "pages": []})
    return report
