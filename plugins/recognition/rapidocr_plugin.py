"""RapidOCR CPU plugin using three explicitly supplied local model files."""
from __future__ import annotations

import importlib.metadata
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
# -I excludes the script directory; add only this fixed product directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BadInput, command, configure, input_file, model_records, parser, payload


def recognize(argv=None):
    arguments = parser(__doc__)
    arguments.add_argument("--det-model", type=Path, required=True)
    arguments.add_argument("--cls-model", type=Path, required=True)
    arguments.add_argument("--rec-model", type=Path, required=True)
    args = arguments.parse_args(argv)
    configure(args.threads)
    source_sha = input_file(args.input, {".png", ".jpg", ".jpeg"})

    from PIL import Image, ImageOps
    try:
        with Image.open(args.input) as image:
            # Match RapidOCR's path loader, including its original-image fallback.
            try:
                corrected = ImageOps.exif_transpose(image)
            except Exception:
                corrected = None
            decoded = corrected if corrected is not None else image
            width, height = decoded.size
            decoded.load()
        if width < 1 or height < 1:
            raise BadInput("Image has no pixels")
    except (OSError, ValueError, SyntaxError) as error:
        raise BadInput("Image cannot be decoded") from error

    models = model_records([args.det_model, args.rec_model, args.cls_model])
    started = time.perf_counter()
    import cv2
    from rapidocr import RapidOCR
    cv2.setNumThreads(args.threads)
    engine = RapidOCR(params={
        "Det.model_path": str(args.det_model), "Cls.model_path": str(args.cls_model),
        "Rec.model_path": str(args.rec_model), "Global.model_root_dir": str(args.det_model.parent),
        "Global.return_word_box": True, "Global.return_single_char_box": True,
        "Global.log_level": "error", "EngineConfig.onnxruntime.intra_op_num_threads": args.threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "EngineConfig.onnxruntime.use_cuda": False,
    })
    engine._load_det_model()
    engine._load_cls_model()
    engine._load_rec_model()
    load_ms = (time.perf_counter() - started) * 1000
    version = importlib.metadata.version("rapidocr")

    started = time.perf_counter()
    result = engine(str(args.input), return_word_box=True, return_single_char_box=True)
    infer_ms = (time.perf_counter() - started) * 1000
    lines = []
    warnings = ["OCR character boxes are engine decoder-aligned approximations, not human character polygons."]
    word_results = result.word_results or ()
    for index, text in enumerate(result.txts or ()):
        chars = []
        if index < len(word_results) and word_results[index]:
            for word in word_results[index]:
                if not isinstance(word, (tuple, list)) or len(word) != 3:
                    continue
                content, confidence, box = word
                if content and box is not None:
                    chars.append({"text": str(content),
                                  "conf": float(confidence) if confidence is not None else None,
                                  "box": [[float(x), float(y)] for x, y in box]})
        if not chars:
            warnings.append(f"p1-l{index}: native character boxes/confidence unavailable; chars empty.")
        lines.append({"id": f"p1-l{index}", "text": str(text), "conf": float(result.scores[index]),
                      "box": [[float(x), float(y)] for x, y in result.boxes[index]], "chars": chars})
    return payload("ocr", version, models, args.input, source_sha, args.threads, load_ms, infer_ms,
                   width=width, height=height, pages=[{"page": 1, "lines": lines}], warnings=warnings)


if __name__ == "__main__":
    raise SystemExit(command(recognize))
