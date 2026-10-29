"""veriscope-statute：把参数原样转交给随包附带的 scripts/statute_diagnostic.py。

唯一的附加参数 --example 会被替换成 --input <随包示例请求路径>；
其余参数、标准输出和退出码都由原脚本决定。
"""
from __future__ import annotations

from pathlib import Path
import runpy
import sys

_ROOT = Path(__file__).resolve().parent / "_root"
_SCRIPT = _ROOT / "scripts" / "statute_diagnostic.py"
_EXAMPLE = _ROOT / "examples" / "statute_check" / "constructed-request.json"


def main() -> None:
    args: list[str] = []
    for arg in sys.argv[1:]:
        args += ["--input", str(_EXAMPLE)] if arg == "--example" else [arg]
    sys.argv = [str(_SCRIPT), *args]
    runpy.run_path(str(_SCRIPT), run_name="__main__")
