"""门禁回放：对已归档的评测结果离线重新执行质量门禁裁决。

不调用模型、不需要密钥：读取已保存的评测结果 JSON（API 返回体，或带 `result` 字段的演示快照），
用 backend/core/gate.py 中与正式评测相同的 check_quality_gate 重新裁决。

退出码：0 = 通过（PASS）；1 = 拦截（BLOCK）；2 = 输入无法读取或缺少门禁所需字段。
可直接作为 CI 步骤：裁决不通过时该步骤失败。

用法：
    python scripts/gate_replay.py docs/competition/evidence/20261005T144504Z-full-39511424/api-response.json
    python scripts/gate_replay.py frontend/src/fixtures/demo.json --faith 0.85 --ctx 0.8
"""
from pathlib import Path
import argparse
import json
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import CTX_PREC_THRESHOLD, FAITH_THRESHOLD  # noqa: E402
from backend.core.gate import check_quality_gate  # noqa: E402

EXIT_PASS, EXIT_BLOCK, EXIT_INVALID = 0, 1, 2


def load_result(path):
    """读取归档结果；演示快照把结果放在 `result` 字段下。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data = data["result"]
    if not isinstance(data, dict) or not isinstance(data.get("summary"), list):
        raise ValueError("缺少 summary 列表，无法执行门禁")
    if not any(entry.get("faithfulness") is not None for entry in data["summary"]):
        raise ValueError("summary 中没有有效模型（faithfulness 均为空）")
    return data


def replay(result, faith_threshold=FAITH_THRESHOLD, ctx_prec_threshold=CTX_PREC_THRESHOLD):
    model_dfs = {name: pd.DataFrame(rows) for name, rows in (result.get("model_dfs") or {}).items() if isinstance(rows, list)}
    return check_quality_gate(result["summary"], model_dfs, faith_threshold, ctx_prec_threshold)


def main(argv=None):
    parser = argparse.ArgumentParser(description="对已归档的评测结果离线重新执行质量门禁")
    parser.add_argument("result", help="评测结果 JSON（API 返回体或演示快照）")
    parser.add_argument("--faith", type=float, default=FAITH_THRESHOLD, help=f"正向忠实度阈值，默认 {FAITH_THRESHOLD}")
    parser.add_argument("--ctx", type=float, default=CTX_PREC_THRESHOLD, help=f"正向上下文精度阈值，默认 {CTX_PREC_THRESHOLD}")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    try:
        result = load_result(args.result)
    except (OSError, ValueError) as error:
        print(f"无法回放：{error}", file=sys.stderr)
        return EXIT_INVALID

    gate = replay(result, args.faith, args.ctx)
    archived = result.get("gate_passed")
    if isinstance(archived, bool):
        same = "一致" if archived == gate["gate_passed"] else "不一致（阈值或门禁逻辑与归档时不同）"
        print(f"\n归档记录：{'PASS' if archived else 'FAIL'}；本次回放：{'PASS' if gate['gate_passed'] else 'FAIL'}；{same}")
    return EXIT_PASS if gate["gate_passed"] else EXIT_BLOCK


if __name__ == "__main__":
    sys.exit(main())
