"""Recognition plugin contract: schema, subprocess statuses, bindings and text map."""

import copy
import hashlib
import json
import sys
import textwrap

import pytest

from backend.api.recognition import recognize
from backend.api.service import EvaluationAPIError
from backend.recognition import runner
from backend.recognition.schema import RecognitionOutputError, validate
from backend.recognition.textmap import build_text, locate

MODEL = {"name": "det.onnx", "sha256": "a" * 64}


def ocr_result(source_sha):
    return {
        "schema": "veriscope.recognition.v1", "kind": "ocr",
        "engine": {"name": "fake-ocr", "version": "1.0", "backend": "onnxruntime-cpu", "models": [MODEL]},
        "input": {"id": "x", "sha256": source_sha, "width": 100, "height": 50, "duration_ms": None},
        "platform": {"machine": "AMD64", "python": "3.12.0", "threads": 1},
        "pages": [{"page": 1, "lines": [
            {"id": "p1-l0", "text": "金额12000元", "conf": 0.98,
             "box": [[0, 0], [90, 0], [90, 20], [0, 20]], "chars": []},
            {"id": "p1-l1", "text": "不得转让", "conf": 0.41,
             "box": [[0, 25], [60, 25], [60, 45], [0, 45]],
             "chars": [{"text": c, "conf": v, "box": [[0, 25], [10, 25], [10, 45], [0, 45]]}
                       for c, v in zip("不得转让", (0.3, 0.9, 0.95, 0.97))]},
        ]}],
        "segments": [], "timing_ms": {"load": 1.0, "infer": 2.0}, "warnings": [],
    }


def asr_result(source_sha):
    return {
        "schema": "veriscope.recognition.v1", "kind": "asr",
        "engine": {"name": "fake-asr", "version": "1.0", "backend": "onnxruntime-cpu", "models": [MODEL]},
        "input": {"id": "y", "sha256": source_sha, "width": None, "height": None, "duration_ms": 5000},
        "platform": {"machine": "riscv64", "python": "3.12.3", "threads": 8},
        "pages": [],
        "segments": [
            {"id": "s0", "start_ms": 0, "end_ms": 2000, "text": "预算一万二", "conf": None, "tokens": []},
            {"id": "s1", "start_ms": 2500, "end_ms": 4900, "text": "未经批准", "conf": None, "tokens": []},
        ],
        "timing_ms": {"load": 1.0, "infer": 2.0}, "warnings": [],
    }


def test_schema_accepts_contract_and_rejects_drift():
    good = ocr_result("b" * 64)
    assert validate(copy.deepcopy(good)) == good
    validate(asr_result("b" * 64))
    mutations = (
        lambda d: d.update(extra=1),
        lambda d: d["pages"][0]["lines"][0].update(conf=1.5),
        lambda d: d["pages"][0]["lines"][0].update(box=[[0, 0], [500, 0], [90, 20], [0, 20]]),
        lambda d: d["input"].update(duration_ms=10),
        lambda d: d["engine"]["models"][0].update(sha256="xyz"),
        lambda d: d["pages"][0]["lines"][1].update(id="p1-l0"),
        lambda d: d.update(segments=[{"id": "s"}]),
    )
    for mutate in mutations:
        bad = copy.deepcopy(good)
        mutate(bad)
        with pytest.raises(RecognitionOutputError):
            validate(bad)
    late = asr_result("b" * 64)
    late["segments"][1]["end_ms"] = 9000
    with pytest.raises(RecognitionOutputError):
        validate(late)


def _project(tmp_path, body, *, timeout=20):
    plugin_dir = tmp_path / "plugins" / "recognition"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "fake.py").write_text(textwrap.dedent(body), encoding="utf-8")
    (tmp_path / runner.CONFIG_FILE).write_text(json.dumps({"schema_version": 1, "plugins": [{
        "kind": "ocr", "python": sys.executable, "script": "plugins/recognition/fake.py",
        "engine": {"name": "fake-ocr", "version": "1.0"}, "models": [MODEL],
        "suffixes": [".png"], "timeout_s": timeout, "threads": 1}]}), encoding="utf-8")
    uploads = tmp_path / "work" / "uploads"
    uploads.mkdir(parents=True)
    source = uploads / ("0" * 32 + ".png")
    source.write_bytes(b"fake image bytes")
    return source


def _emit(mutate="pass"):
    # The plugin re-hashes its input, like a real adapter must.
    template = json.dumps(ocr_result("SHA"), ensure_ascii=False)
    return "\n".join([
        "import hashlib, json, sys",
        "path = sys.argv[sys.argv.index('--input') + 1]",
        "sha = hashlib.sha256(open(path, 'rb').read()).hexdigest()",
        "result = json.loads(" + repr(template) + ")",
        "result['input']['sha256'] = sha",
        mutate,
        "sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))",
    ])


def test_ok_run_binds_hashes_and_writes_text_and_sidecar(tmp_path):
    source = _project(tmp_path, _emit())
    response = recognize(tmp_path, {"kind": "ocr", "relative_path": "work/uploads/" + source.name})
    assert response["status"] == "ok", response
    assert response["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    text = (tmp_path / response["text_path"]).read_text(encoding="utf-8")
    assert text == "金额12000元\n不得转让"
    sidecar = json.loads((tmp_path / response["recognition_path"]).read_text(encoding="utf-8"))
    assert sidecar["text"]["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert sidecar["source"]["sha256"] == response["source_sha256"]
    assert response["min_conf"] == 0.41


@pytest.mark.parametrize("body,status", [
    ("import time; time.sleep(30)", "timeout"),
    ("import sys; sys.exit(2)", "bad_input"),
    ("raise SystemExit(3)", "plugin_error"),
    ("print('not json')", "bad_output"),
])
def test_plugin_failures_have_explicit_statuses(tmp_path, body, status):
    source = _project(tmp_path, body, timeout=2)
    assert runner.run("ocr", source, tmp_path).status == status


@pytest.mark.parametrize("mutate,status", [
    ("result['engine']['version'] = '2.0'", "binding_mismatch"),
    ("result['engine']['models'][0]['sha256'] = 'c' * 64", "binding_mismatch"),
    ("result['input']['sha256'] = 'd' * 64", "input_mismatch"),
    ("result['pages'][0]['lines'][0]['conf'] = 7", "bad_output"),
])
def test_output_that_does_not_bind_is_rejected(tmp_path, mutate, status):
    source = _project(tmp_path, _emit(mutate))
    outcome = runner.run("ocr", source, tmp_path)
    assert outcome.status == status and outcome.result is None


def test_missing_plugin_and_bad_input(tmp_path):
    source = _project(tmp_path, _emit())
    assert runner.run("asr", source, tmp_path).status == "plugin_missing"
    empty = source.with_name("1" * 32 + ".png")
    empty.write_bytes(b"")
    assert runner.run("ocr", empty, tmp_path).status == "bad_input"
    with pytest.raises(EvaluationAPIError):
        recognize(tmp_path, {"kind": "asr", "relative_path": "work/uploads/" + source.name})
    with pytest.raises(EvaluationAPIError):
        recognize(tmp_path, {"kind": "ocr", "relative_path": "../secret.png"})


def test_plugin_environment_never_carries_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
    body = "import os, sys; sys.exit(0 if 'DEEPSEEK_API_KEY' not in os.environ else 3)"
    source = _project(tmp_path, body)
    # Exit 0 with empty output is bad_output; exit 3 would mean the key leaked.
    assert runner.run("ocr", source, tmp_path).status == "bad_output"


def test_text_map_locates_quotes_with_native_confidence():
    text, spans = build_text(ocr_result("b" * 64))
    found = locate(text, spans, "不得")
    assert found["status"] == "located"
    match = found["matches"][0]
    assert match["refs"][0]["line_id"] == "p1-l1" and match["refs"][0]["page"] == 1
    assert match["min_conf"] == 0.3  # character level, not the line value
    assert locate(text, spans, "不存在")["status"] == "not_found"
    audio_text, audio_spans = build_text(asr_result("b" * 64))
    heard = locate(audio_text, audio_spans, "未经批准")["matches"][0]
    assert heard["refs"][0]["start_ms"] == 2500 and heard["conf_missing"] is True
    single = [{"start": 0, "end": 5, "conf": 0.9, "char_conf": None}]
    assert locate("ab ab", single, "ab")["status"] == "ambiguous"
