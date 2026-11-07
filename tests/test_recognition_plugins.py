"""Local plugin command boundaries without installing recognition engines in CI."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.recognition.schema import validate

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "recognition"
ENTRIES = (
    ("rapidocr_plugin.py", ["--det-model", "missing-det", "--rec-model", "missing-rec", "--cls-model", "missing-cls"], ".png"),
    ("sherpa_asr_plugin.py", ["--model", "missing-model", "--tokens", "missing-tokens", "--vad-model", "missing-vad"], ".wav"),
)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script,models,suffix", ENTRIES)
@pytest.mark.parametrize("case", ["missing", "empty", "unsupported"])
def test_bad_input_is_exit_two_under_isolated_interpreter(tmp_path, script, models, suffix, case):
    source = tmp_path / ("input.txt" if case == "unsupported" else "input" + suffix)
    if case != "missing":
        source.write_bytes(b"not an accepted format" if case == "unsupported" else b"")
    completed = subprocess.run([sys.executable, "-I", str(PLUGIN_DIR / script),
                                "--input", str(source), "--threads", "1", *models],
                               cwd=tmp_path, capture_output=True, timeout=20)
    assert completed.returncode == 2, completed.stderr
    assert completed.stdout == b""


@pytest.mark.parametrize("script,models,suffix", ENTRIES)
def test_model_parameters_are_explicit_configuration(tmp_path, script, models, suffix):
    completed = subprocess.run([sys.executable, "-I", str(PLUGIN_DIR / script),
                                "--input", str(tmp_path / ("input" + suffix))],
                               cwd=tmp_path, capture_output=True, timeout=20)
    assert completed.returncode == 1
    assert b"required" in completed.stderr
    assert completed.stdout == b""


def test_ocr_rejects_tiff_before_decoder_or_models(tmp_path):
    source = tmp_path / "multipage.tiff"
    source.write_bytes(b"II*\0not supported")
    script, models, _ = ENTRIES[0]
    completed = subprocess.run([sys.executable, "-I", str(PLUGIN_DIR / script),
                                "--input", str(source), *models],
                               cwd=tmp_path, capture_output=True, timeout=20)
    assert completed.returncode == 2
    assert b"unsupported suffix" in completed.stderr
    assert completed.stdout == b""


def test_model_records_hash_every_explicit_file_and_use_basename(tmp_path):
    common = load_module("plugin_common_test", PLUGIN_DIR / "_common.py")
    paths = [tmp_path / name for name in ("model.onnx", "tokens.txt", "vad.onnx")]
    for path in paths:
        path.write_bytes(path.name.encode())
    assert common.model_records(paths) == [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]
    paths[-1].unlink()
    with pytest.raises(ValueError, match="missing or empty"):
        common.model_records(paths)


@pytest.mark.parametrize("rate,channels,format", [(8000, 1, "WAV"), (16000, 2, "WAV"), (16000, 1, "OGG")])
def test_asr_rejects_sampling_or_container_before_model_initialization(tmp_path, monkeypatch, rate, channels, format):
    monkeypatch.setattr(sys, "path", list(sys.path))
    module = load_module("asr_plugin_test", PLUGIN_DIR / "sherpa_asr_plugin.py")
    source = tmp_path / "input.wav"
    source.write_bytes(b"fake decoder input")

    class Audio:
        samplerate, frames = rate, 100

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, **kwargs):
            pytest.fail("Invalid audio must be rejected before decoding samples")

    Audio.channels, Audio.format = channels, format
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(SoundFile=lambda path: Audio()))
    assert module.command(module.recognize, ["--input", str(source), *ENTRIES[1][1]]) == 2


def test_payload_keeps_native_missing_confidence_and_matches_contract():
    common = load_module("plugin_payload_test", PLUGIN_DIR / "_common.py")
    segment = {"id": "s0", "start_ms": 100, "end_ms": 200, "text": "金额120元", "conf": None,
               "tokens": [{"text": "金额", "start_ms": None, "conf": None}]}
    result = common.payload("asr", "1.0", [{"name": "model.onnx", "sha256": "a" * 64}],
                            Path("sample.wav"), "b" * 64, 1, 0.0, 1.0,
                            duration_ms=1000, segments=[segment])
    assert validate(result) is result
    assert result["segments"] == [segment]
    assert json.loads(json.dumps(result, allow_nan=False))["segments"][0]["conf"] is None


@pytest.mark.parametrize("orientation,fallback", [(None, None), (6, None), (8, None), (6, "none"), (6, "error")])
def test_ocr_jpeg_dimensions_match_engine_exif_transpose(tmp_path, monkeypatch, orientation, fallback):
    from PIL import Image, ImageOps

    monkeypatch.setattr(sys, "path", list(sys.path))
    module = load_module("ocr_exif_plugin_test", PLUGIN_DIR / "rapidocr_plugin.py")
    source = tmp_path / "input.jpeg"
    image = Image.new("RGB", (80, 30), "white")
    exif = image.getexif()
    if orientation is not None:
        exif[274] = orientation
    image.save(source, exif=exif)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    expected = (30, 80) if orientation in (6, 8) and fallback is None else (80, 30)
    if fallback == "none":
        monkeypatch.setattr(ImageOps, "exif_transpose", lambda image: None)
    elif fallback == "error":
        def fail_transpose(image):
            raise RuntimeError("Invalid EXIF transpose")
        monkeypatch.setattr(ImageOps, "exif_transpose", fail_transpose)
    width, height = expected
    box = [[0, 0], [width, 0], [width, height], [0, height]]

    class Engine:
        def __init__(self, params):
            pass

        def _load_det_model(self):
            pass

        _load_cls_model = _load_rec_model = _load_det_model

        def __call__(self, *args, **kwargs):
            return SimpleNamespace(txts=["测试"], scores=[0.9], boxes=[box], word_results=[])

    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(setNumThreads=lambda count: None))
    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(RapidOCR=Engine))
    monkeypatch.setattr(module.importlib.metadata, "version", lambda name: "test-version")
    model = tmp_path / "model.onnx"
    model.write_bytes(b"No engine is loaded in this test")
    result = module.recognize(["--input", str(source), "--det-model", str(model),
                               "--rec-model", str(model), "--cls-model", str(model)])
    assert (result["input"]["width"], result["input"]["height"]) == expected
    assert result["input"]["sha256"] == source_sha == hashlib.sha256(source.read_bytes()).hexdigest()
    assert validate(result) is result
