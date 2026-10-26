"""Stage 1 (no model calls): load -> parse_check -> split for samples A and B.

Usage: python pdf_e2e_stage1.py <repo_root> <out_dir> A=<pdf> B=<pdf>
"""
import contextlib, hashlib, io, json, logging, statistics, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(repo))
samples = dict(arg.split("=", 1) for arg in sys.argv[3:])

import pypdf
from langchain_community.document_loaders import PyPDFLoader
from backend.config import CHUNK_SIZE, CHUNK_OVERLAP
from backend.services.documents import load_and_split_doc
from backend.services.parse_check import check_document, CHECKER_VERSION

HAN = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2EE5F), (0x2F800, 0x2FA1F), (0x30000, 0x323AF))
def han(t): return sum(any(a <= ord(c) <= b for a, b in HAN) for c in t)
def surrogates(t): return sum(0xD800 <= ord(c) <= 0xDFFF for c in t)
def c1(t): return sum(0x80 <= ord(c) <= 0x9F for c in t)

raw = out / "raw"; raw.mkdir(parents=True, exist_ok=True)
log_buf = io.StringIO()
handler = logging.StreamHandler(log_buf); logging.getLogger().addHandler(handler); logging.getLogger().setLevel(logging.WARNING)

results = {}
for label, pdf in samples.items():
    data = Path(pdf).read_bytes()
    r = {"sample": label, "source_name": Path(pdf).name, "source_sha256": hashlib.sha256(data).hexdigest(), "source_bytes": len(data),
         "pypdf_version": pypdf.__version__, "checker_version": CHECKER_VERSION,
         "loader": "langchain_community.document_loaders.PyPDFLoader (same class as backend/services/documents.py)",
         "chunk_size": CHUNK_SIZE, "chunk_overlap": CHUNK_OVERLAP, "run_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    err = io.StringIO()
    # 1. parse with the project's loader class
    with contextlib.redirect_stderr(err):
        try:
            docs = PyPDFLoader(pdf).load()
            text = "".join(d.page_content for d in docs)
            r["parse"] = {"exit": "ok", "pages": len(docs), "chars": len(text), "han": han(text), "surrogates": surrogates(text), "c1_controls": c1(text), "fffd": text.count("\ufffd")}
            (raw / f"{label}-extracted.txt").write_text(text, encoding="utf-8", errors="surrogatepass")
        except Exception as e:
            r["parse"] = {"exit": "error", "error": repr(e), "traceback": traceback.format_exc()}
    # 2. parse_check
    try:
        check = check_document(pdf)
        (raw / f"{label}-parse_check.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
        flagged = [{"page": p["page_number"], "verdict": p["verdict"], "reasons": p["reasons"], "control_ratio": p["control_ratio"],
                    "replacement_ratio": p["replacement_ratio"], "cid_marker_ratio": p["cid_marker_ratio"]} for p in check["pages"] if p["verdict"] != "PASS" or p["reasons"]]
        r["parse_check"] = {"verdict": check["verdict"], "flagged_pages": flagged, "page_verdicts": [p["verdict"] for p in check["pages"]]}
    except Exception as e:
        r["parse_check"] = {"verdict": None, "error": repr(e), "traceback": traceback.format_exc()}
    # 3. ingest decision mirrors backend/core/pipeline.py: FAIL raises, REVIEW continues with a warning
    v = r["parse_check"].get("verdict")
    r["ingested"] = v in ("PASS", "REVIEW", "NOT_APPLICABLE")
    r["ingest_rule"] = "pipeline.py: FAIL -> RuntimeError (blocked); REVIEW -> warning, continues; PASS -> continues"
    if r["ingested"]:
        with contextlib.redirect_stderr(err):
            try:
                chunks = load_and_split_doc(pdf)
                lens = [len(c.page_content) for c in chunks]
                r["split"] = {"exit": "ok", "chunks": len(chunks), "len_min": min(lens), "len_median": statistics.median(lens), "len_max": max(lens),
                              "first_two_head80": [c.page_content[:80] for c in chunks[:2]]}
            except Exception as e:
                r["split"] = {"exit": "error", "error": repr(e)}
    else:
        r["split"] = {"exit": "skipped", "reason": "parse_check FAIL blocks ingestion, as in pipeline.py"}
    (raw / f"{label}-stderr.log").write_text(err.getvalue(), encoding="utf-8")
    (out / f"run-{label}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    results[label] = r
(raw / "logging.log").write_text(log_buf.getvalue(), encoding="utf-8")
for label, r in results.items():
    p, c, s = r["parse"], r["parse_check"], r["split"]
    print(label, json.dumps({"pages": p.get("pages"), "chars": p.get("chars"), "han": p.get("han"), "surrogates": p.get("surrogates"), "c1": p.get("c1_controls"),
                              "verdict": c.get("verdict"), "flagged": c.get("flagged_pages"), "ingested": r["ingested"], "chunks": s.get("chunks"), "split": s.get("exit")}, ensure_ascii=False))
