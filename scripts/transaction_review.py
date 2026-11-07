"""CPU-only transaction analysis/replay CLI; no output file without --out."""
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.transaction_review import (  # noqa: E402
    MAX_BUNDLE_BYTES, MAX_REQUEST_BYTES, TransactionReviewError,
    analyze_request, load_json_bytes, replay_bundle,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="确定性交易风险线索与证据复核；退出 0 不等于无风险，查看 JSON status。")
    parser.add_argument("operation", choices=("analyze", "replay"))
    parser.add_argument("input", type=Path, help="UTF-8 请求或完整复核包 JSON")
    parser.add_argument("--out", type=Path, help="显式输出文件；省略时输出到 stdout")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        is_bundle = args.operation == "replay"
        maximum = MAX_BUNDLE_BYTES if is_bundle else MAX_REQUEST_BYTES
        with args.input.open("rb") as source:
            raw = source.read(maximum + 1)
        payload = load_json_bytes(raw, bundle=is_bundle)
        result = replay_bundle(payload) if is_bundle else analyze_request(payload)
        # A 5000-case report stays below the 24 MiB replay-body limit only when
        # shared evidence bundles are serialized compactly, without indentation.
        text = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        if args.out is None:
            sys.stdout.write(text)
        else:
            args.out.write_text(text, encoding="utf-8", newline="\n")
        return 0
    except (TransactionReviewError, OSError, UnicodeError) as error:
        print(f"transaction-review: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
